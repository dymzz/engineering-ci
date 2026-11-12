"""Native Plan result collection; Dagger owns graphs and cache."""

import asyncio
import json

import dagger
from dagger import dag

from .plan_result import PlanExecution
from .plan_result import result as make_result


async def _tests(module, source, targets, framework, python_version):
    """Use the existing pytest computation, or native unittest, exactly once."""
    if framework == "pytest":
        execution = await module._run_pytest(
            source, python_version, targets[0], _targets=targets
        )
        code = execution.exit_code
        stdout = execution.output
        stderr = await execution.container.stderr()
        xml = execution.xml
        files = module._pytest_bundle(execution)
    else:
        container = module._uv_container(source, python_version).with_exec(
            ["uv", "run", "python", "-m", "unittest", *targets],
            expect=dagger.ReturnType.ANY,
        )
        code = await container.exit_code()
        stdout = await container.stdout()
        stderr = await container.stderr()
        xml = None
        files = dag.directory().with_new_file("exit-code.txt", str(code) + "\n")
    files = files.with_new_file("stdout.txt", stdout).with_new_file(
        "stderr.txt", stderr
    )
    payload = {
        "tests": {"status": "passed" if code == 0 else "failed", "exit_code": code},
        "stdout": stdout,
        "stderr": stderr,
    }
    if xml is not None:
        payload["junit"] = xml
    return payload, files


async def _weaver(module, source, value, directory, item, config_path, tree_state):
    """Read one original /out Directory; do not rescan for Report or Enforce."""
    from engineering_ci import selection

    kind = item["name"].removeprefix("weaver_")
    metadata = {}
    if kind == "terminal":
        gate = selection.validate_post_completion_binding(value, directory)
        for key in (
            "input_closure_verified",
            "plan_closure_digest",
            "actual_closure_digest",
        ):
            metadata[key] = gate.get(key)
        if gate.get("source_tree_verified") != "not_requested":
            metadata["source_tree_verified"] = gate["source_tree_verified"]
            metadata["tree_verified"] = gate["source_tree_verified"] == "verified"
        if gate.get("source_digest"):
            metadata["source_digest"] = gate["source_digest"]
        if not gate["ok"]:
            return (
                {
                    "name": item["name"],
                    "status": "not_executed",
                    "reason": gate.get("reason") or "post_completion_binding_invalid",
                    "gate_exit_code": 1,
                    "trusted": False,
                    "authenticated": False,
                    "selected": item.get("status"),
                    **metadata,
                },
                None,
                metadata,
            )
        config_path = value["post_completion"]["weaver_config"]["path"]
    files = await module.weaver_artifacts(source, kind, config_path)
    native = json.loads(await files.file("report.json").contents())
    native["selected"] = item.get("status")
    native["source_tree_verified"] = metadata.get("source_tree_verified", tree_state)
    if kind == "terminal":
        native.update(phase=selection.POST_COMPLETION_PHASE, **metadata)
    return native, files, metadata


async def _native_results(
    module,
    source,
    value,
    directory,
    selected,
    report,
    artifacts,
    python_version,
    weaver_config,
):
    """Collect fixed independent native branches; Engine owns their DAG."""
    from engineering_ci import execution

    targets = value["selection"]["tests"]
    branches = []
    requirement = execution.test_requirement_failure(value)
    if requirement:
        report["tests"] = {
            "status": "not_executed",
            "exit_code": None,
            "reason": requirement,
        }
        report.update(status="blocked", exit_code=5, reason=requirement)
    elif targets:
        branches.append(
            (
                "tests",
                _tests(module, source, targets, report["framework"], python_version),
            )
        )
    branches.extend(
        (
            item["name"],
            _weaver(
                module,
                source,
                value,
                directory,
                item,
                weaver_config,
                report["source_tree_verified"],
            ),
        )
        for item in selected
        if item["name"] in {"weaver_registry", "weaver_live", "weaver_terminal"}
    )
    # These awaits resolve actual Container/Directory computations, not an ECI
    # queue or topological scheduler. A failed branch cannot cancel its peers.
    outputs = await asyncio.gather(
        *(task for _, task in branches), return_exceptions=True
    )
    for (name, _), output in zip(branches, outputs, strict=True):
        if isinstance(output, BaseException):
            if not isinstance(output, Exception):
                raise output
            native = {
                "name": name,
                "status": "not_executed",
                "exit_code": None,
                "reason": "native_execution_error",
                "error_type": type(output).__name__,
                "diagnostic": str(output),
            }
            if name == "tests":
                report["tests"] = native
            else:
                report["checks"].append(native)
            if report["status"] == "passed":
                report.update(
                    status="blocked", exit_code=5, reason="native_execution_error"
                )
            continue
        if name == "tests":
            payload, files = output
            report.update(payload)
            artifacts = artifacts.with_directory("tests", files)
            if payload["tests"]["exit_code"] != 0:
                report.update(
                    status="failed",
                    exit_code=payload["tests"]["exit_code"],
                    reason="tests_failed",
                )
        else:
            native, files, metadata = output
            report.update(metadata)
            report["checks"].append(native)
            if files is not None:
                artifacts = artifacts.with_directory("checks/" + name, files)
            if native["status"] != "passed" and report["status"] == "passed":
                report.update(
                    status="blocked"
                    if native["status"] == "not_executed"
                    else "failed",
                    exit_code=native.get("gate_exit_code") or 1,
                    reason=native.get("reason") or "checks_failed",
                )
    report["result_sharing"] = {
        "scope": "this_result_bundle",
        "native_branches": [name for name, _ in branches],
        "origin_execution_attributed": False,
    }
    return artifacts


async def execute(module, plan, source, python_version, weaver_config) -> PlanExecution:
    """Execute plan targets and preserve one complete native result bundle."""
    artifacts = dag.directory()
    import copy
    import tempfile

    from engineering_ci import execution, plan_contract, selection

    raw = await plan.contents()
    artifacts = artifacts.with_new_file("plan.json", raw)
    try:
        value = json.loads(raw)
    except ValueError:
        return make_result(
            execution.plan_rejection(execution.ExecutionError("plan_invalid")),
            artifacts,
        )
    try:
        execution.validate_plan(value)
    except execution.ExecutionError as error:
        return make_result(execution.plan_rejection(error), artifacts)
    declaration_consistent = (
        True if value["schema_version"] == plan_contract.PLAN_VERSION else None
    )
    chosen = value.get("selection") or {}
    targets = chosen.get("tests") or []
    framework = chosen.get("framework", "unittest")
    selected_checks = [
        item
        for item in value.get("check_scope", chosen.get("check_scope", []))
        if item.get("status") in {"selected", "unknown"}
    ]
    if ("check_scope" not in value and "check_scope" not in chosen) and chosen.get(
        "checks"
    ):
        selected_checks = [
            {"name": name, "status": "unknown"} for name in chosen["checks"]
        ]
    prerequisites = list(chosen.get("prerequisites") or [])
    if value.get("git", {}).get("snapshot") == "multi":
        return make_result(
            {
                "status": "not_executed",
                "reason": "multi_ref_requires_per_ref_execution",
                "plan_declaration_consistent": declaration_consistent,
            },
            artifacts,
        )
    if not targets and not selected_checks and not prerequisites:
        return make_result(
            {
                "status": "not_executed",
                "reason": "no_selected_tests",
                "plan_declaration_consistent": declaration_consistent,
            },
            artifacts,
        )
    git = value.get("git") or {}
    source_tree_id = git.get("source_tree_id")
    execution_tree = git.get("execution_tree")
    tree_verified = False
    source_tree_state = "not_requested"
    source_digest = None
    input_closure_verified = "not_requested"
    with tempfile.TemporaryDirectory() as directory:
        await source.export(directory)
        exported_content_id = selection.directory_content_id(directory)
        if source_tree_id and execution_tree:
            source_digest = selection.directory_content_id(directory)
            tree_verified = source_digest == source_tree_id
            source_tree_state = "verified" if tree_verified else "failed"
            if not tree_verified:
                return make_result(
                    {
                        "kind": execution.REPORT_KIND,
                        "schema_version": execution.SCHEMA_VERSION,
                        "plan_declaration_consistent": declaration_consistent,
                        "source_binding_verified": "unknown",
                        "status": "not_executed",
                        "reason": "source_tree_mismatch",
                        "exit_code": 4,
                        "tree_verified": False,
                        "source_tree_verified": source_tree_state,
                        "input_closure_verified": input_closure_verified,
                        "trusted": False,
                        "plan_execution_tree": execution_tree,
                        "source_tree_id": source_tree_id,
                        "source_digest": source_digest,
                    },
                    artifacts,
                )
        report = {
            "kind": execution.REPORT_KIND,
            "schema_version": execution.SCHEMA_VERSION,
            "dependency_contract": value.get("dependency_contract"),
            "plan_declaration_consistent": declaration_consistent,
            # Preserved metadata/export identity is not a verified #66
            # Source/Prerequisite receipt or actual-config binding.
            "source_binding_verified": "unknown",
            "plan": {"generator": value.get("generator"), "git": git},
            "execution_context": "dagger_export",
            "status": "passed",
            "exit_code": 0,
            "targets": targets,
            "framework": framework,
            # tree_verified is whole-tree Source-byte equivalence only when
            # that check ran. Post-completion uses input_closure_verified.
            # Neither field is #63 Check-evidence admission.
            "tree_verified": tree_verified,
            "source_tree_verified": source_tree_state,
            "input_closure_verified": input_closure_verified,
            "trusted": False,
            "plan_execution_tree": execution_tree,
            "source_tree_id": source_tree_id,
            "source_digest": source_digest,
            "tests": {"status": "not_executed", "exit_code": 3},
            "checks": [],
            "prerequisites": [],
            "source_handoff": {
                "original_git": git,
                "exported_content_id": exported_content_id,
                "host_context": "dagger_export",
                "original_git_snapshot_preserved": True,
            },
        }
        artifacts = await _native_results(
            module,
            source,
            value,
            directory,
            selected_checks,
            report,
            artifacts,
            python_version,
            weaver_config,
        )
        if selected_checks or prerequisites:
            check_plan = copy.deepcopy(value)
            check_plan.setdefault("selection", {})
            check_plan["selection"] = dict(check_plan["selection"])
            check_plan["selection"]["tests"] = []
            check_plan["git"] = dict(check_plan.get("git") or {})
            # This is only the Host adapter's filesystem view. Preserve the
            # original Git identity in source_handoff, and do not claim an
            # exported Directory has the original ODB or verified full Tree.
            check_plan["git"]["execution_tree"] = None
            check_plan["git"]["index_tree"] = None
            check_plan["git"]["content_binding"] = None
            check_plan["git"]["snapshot"] = "worktree"
            weaver_checks = {"weaver_registry", "weaver_live", "weaver_terminal"}
            check_plan["check_scope"] = [
                item
                for item in selected_checks
                if item.get("name") not in weaver_checks
            ]
            check_plan["selection"]["checks"] = [
                name
                for name in check_plan["selection"].get("checks", [])
                if name not in weaver_checks
            ]
            host = (
                execution.execute(
                    check_plan, root=directory, execution_context="dagger_export"
                )
                if check_plan["check_scope"] or prerequisites
                else {
                    "status": "passed",
                    "exit_code": 0,
                    "checks": [],
                    "prerequisites": [],
                }
            )
            report["checks"].extend(host.get("checks") or [])
            report["prerequisites"] = host.get("prerequisites") or []
            after_host = selection.directory_content_id(directory)
            report["source_handoff"]["after_host_content_id"] = after_host
            if after_host != exported_content_id:
                report.update(
                    status="not_executed",
                    reason="exported_source_changed",
                    exit_code=4,
                    tree_verified=False,
                    source_tree_verified="failed_after_host_action",
                )
                report["source_handoff"]["generated_outputs_delivered"] = False
                report["source_handoff"]["native_results_source"] = (
                    "immutable_input_before_host_action"
                )
                return make_result(report, artifacts)
            if report["status"] == "passed":
                report["status"] = host.get("status", "blocked")
                report["reason"] = host.get("reason")
                report["exit_code"] = host.get("exit_code", 5)
    return make_result(report, artifacts)
