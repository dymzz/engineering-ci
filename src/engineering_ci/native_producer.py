"""Adapt retained native observations to the public Check contract.

This module neither invokes tools nor authenticates producers. Implementation
fingerprints and execution references are facts for #63 to verify, not approval.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from . import check_contract, weaver_tool


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def execution_identity(*, run_ref=None, attempt_ref=None, parent_ref=None):
    if bool(run_ref) != bool(attempt_ref):
        raise ValueError("execution_run_attempt_pair_required")
    return {
        "execution_ref": "execution:" + uuid.uuid4().hex,
        "parent_execution_ref": parent_ref,
        "run_ref": run_ref or None,
        "attempt_ref": attempt_ref or None,
        "run_attempt_verification": "unverified" if run_ref else "not_supplied",
        "origin_execution_attributed": False,
    }


def implementation(identity, files):
    """Hash actual adapter sources, including this mapper and its validator."""
    paths = {
        "engineering_ci/native_producer.py": Path(__file__),
        "engineering_ci/check_contract.py": Path(check_contract.__file__),
        "engineering_ci/weaver_tool.py": Path(weaver_tool.__file__),
        **files,
    }
    hashes = {
        name: hashlib.sha256(Path(path).read_bytes()).hexdigest()
        for name, path in sorted(paths.items())
    }
    ref = {
        "implementation_id": identity,
        "implementation_version": "1",
        "implementation_sha256": digest(hashes),
    }
    return ref, hashes


def source_reference(plan=None, *, actual="dagger_directory"):
    """Retain target identity, without claiming #66 input-closure proof."""
    git = (plan or {}).get("git") or {}
    return check_contract.source_ref(
        str(git.get("snapshot") or "worktree"),
        declared_target_tree=git.get("execution_tree") or git.get("tree"),
        head_commit=git.get("head_commit"),
        index_tree=git.get("index_tree"),
        actual_execution_source=actual,
    )


def _attach(item, definition, results, impl):
    item.update(
        check_id=definition.check_id,
        definition_version=definition.definition_version,
        check_version=definition.definition_version,
        verification_implementation=impl,
        native_results=results,
        execution_started=bool(results),
        check_contract={
            "kind": check_contract.CHECK_CONTRACT_KIND,
            "schema_version": check_contract.CONTRACT_VERSION,
            "status": "valid" if results else "not_started",
        },
    )
    if results:
        item.update(
            native_result=results[-1],
            result_id=results[-1]["result_id"],
            result_origin=results[-1]["result_origin"],
            source_ref=results[-1]["source_ref"],
            native_satisfaction=check_contract.evaluate_check_satisfaction(
                definition, results
            ),
        )
    return item


def test_check(
    item,
    *,
    framework,
    targets,
    observation,
    source,
    context,
    impl,
    origin="dagger",
    artifact_prefix="tests",
    generated_inputs=(),
    plan_authorized=None,
    binding=None,
):
    """Describe the one framework invocation which already produced item."""
    definition = check_contract.definition_for(framework)
    if generated_inputs:
        item["generated_input_refs"] = list(generated_inputs)
    if plan_authorized is not None:
        item["plan_authorized"] = plan_authorized
    binding = binding or {}
    results = []
    if type(item.get("exit_code")) is int and item.get("status") in {
        "passed",
        "failed",
        "advisory",
    }:
        access = "python/" + framework
        rule = binding.get("rule_ref") or check_contract.rule_ref(
            "selected-" + framework,
            1,
            digest=digest({"framework": framework, "targets": list(targets)}),
        )
        source_ref = binding.get("source_ref")
        verified_closure = (
            isinstance(source_ref, dict)
            and source_ref.get("binding_status") == "byte_tree"
            and source_ref.get("scope_kind") == "full_input_closure"
            and bool(source_ref.get("content_digest"))
        )
        result = check_contract.native_result(
            definition,
            source=source_ref if isinstance(source_ref, dict) else source,
            status=item["status"],
            native_exit_code=item["exit_code"],
            result_origin=origin,
            runner_ref="execution:" + origin,
            result_instance=context["execution_ref"] + ":" + access,
            rule_ref=rule,
            baseline_ref=binding.get("baseline_ref"),
            profile_ref=binding.get("profile_ref"),
            tool=check_contract.tool_ref(
                access,
                executable=observation.get("executable"),
                version=observation.get("tool_version"),
            ),
            input_refs=binding.get(
                "input_refs", [{"kind": "test_target", "path": t} for t in targets]
            )
            + list(generated_inputs),
            artifact_refs=[
                artifact_prefix + "/" + p
                for p in ("stdout.txt", "stderr.txt", "exit-code.txt")
            ]
            if artifact_prefix
            else [],
            evidence={
                "execution": context,
                "observation": observation,
                "verification_implementation": impl,
                **(
                    {}
                    if verified_closure
                    else {"source_limit": "input_closure_not_proven"}
                ),
            },
        )
        results.append(result)
    return _attach(item, definition, results, impl)


def weaver_check(
    raw, *, source, context, impl, source_directory=None, generated_inputs=()
):
    """Map actual stage records, never synthesizing a native gate exit."""
    item = dict(raw)
    if generated_inputs:
        item["generated_input_refs"] = list(generated_inputs)
    definition = check_contract.definition_for(raw["name"])
    inputs = raw.get("inputs") or {}
    rule = None
    if inputs.get("config_sha256"):
        rule = check_contract.rule_ref(
            "weaver-config",
            1,
            digest=inputs["config_sha256"],
            baseline_required=raw.get("baseline_required"),
        )
    requirements = {r.requirement_id: r for r in definition.tool_requirements}
    results, by_requirement = [], {}
    for index, command in enumerate(raw.get("commands") or ()):
        # The runtime records its actual output paths for each invocation.
        stage = str(command.get("stdout", "")).removesuffix(".stdout")
        if stage == "live":
            stage = (
                "terminal-live-check"
                if raw["name"] == "weaver_terminal"
                else "live-check"
            )
        requirement = requirements.get(stage)
        code = command.get("exit_code")
        if requirement is None or type(code) is not int:
            continue
        access = requirement.alternatives[0].access_id
        verified_asset = raw.get("tool_asset_sha256") == weaver_tool.LINUX_SHA256
        tool = check_contract.tool_ref(
            access,
            executable=(command.get("argv") or [None])[0],
            tool_id="weaver" if verified_asset else None,
            tool_id_source="eci_pinned_asset" if verified_asset else None,
            version=raw.get("version"),
            artifact_sha256=raw.get("tool_asset_sha256"),
        )
        consumed = [
            {"kind": "check_result", "result_id": by_requirement[p]["result_id"]}
            for p in requirement.after
            if p in by_requirement
        ]
        result = check_contract.native_result(
            definition,
            source=source,
            status="passed" if code == 0 else "failed",
            native_exit_code=code,
            result_origin="dagger",
            runner_ref="execution:dagger",
            result_instance=f"{context['execution_ref']}:{index}:{access}",
            rule_ref=rule,
            tool=tool,
            phase=raw.get("phase"),
            input_refs=[
                *consumed,
                {"kind": "native_inputs", "value": inputs},
                *generated_inputs,
            ],
            artifact_refs=[command[k] for k in ("stdout", "stderr") if command.get(k)],
            evidence={
                "execution": context,
                "command": command,
                "source_directory": source_directory,
                "native_source_digest": raw.get("source_digest"),
                "native_artifacts": raw.get("native_artifacts"),
                "verification_implementation": impl,
                "environment": raw.get("environment"),
                "gate_status": raw.get("status"),
                "gate_exit_code": raw.get("gate_exit_code"),
                **(
                    {
                        "terminal_correlation_status": raw.get(
                            "terminal_correlation_status"
                        ),
                        "terminal_correlation_reason": raw.get("reason"),
                    }
                    if stage == "terminal-live-check"
                    else {}
                ),
                "source_limit": "input_closure_not_proven",
            },
        )
        results.append(result)
        by_requirement[stage] = result
    return _attach(item, definition, results, impl)
