"""Source, execution and reconciliation for Lefthook jobs."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import replace
from functools import partial
from pathlib import Path

from . import (
    dagger_plan,
    execution,
    expected_checks,
    git_source,
    hooks,
    reconciliation,
    selection,
)

_SELECTION_CONFIG = "ci-selection.toml"
_PROFILE_FILES = ("ci-effective-profile.json", "ci-effective-profile.toml")
_JOB_REF = "check-index"
_PUSH_JOB_REF = "check-push"
_RUNNER_REF = "execution:host"
_INAPPLICABLE = {"not_applicable", "no_tests", "no_changes"}


def _artifact(binding, suffix, payload=None, *, exclusive=False):
    path = binding.invocation_path.with_suffix(suffix)
    if path.is_symlink() or path.resolve().parent != binding.invocation_path.parent:
        raise hooks.HookEventError("hook_artifact_outside_invocation")
    if payload is None:
        if not path.is_file():
            return None
        try:
            raw = path.read_bytes()
            content = json.loads(raw)
        except (OSError, ValueError):
            raise hooks.HookEventError("hook_artifact_invalid") from None
        if not isinstance(content, dict) or hooks.canonical_event_bytes(content) != raw:
            raise hooks.HookEventError("hook_artifact_invalid")
        return content
    git_dir = hooks._invocation_git_dir(binding.invocation_path)
    with hooks._capture_lock(git_dir):
        raw = hooks.canonical_event_bytes(payload)
        if path.exists():
            if exclusive:
                raise hooks.HookEventError("hook_job_already_activated")
            if path.read_bytes() != raw:
                raise hooks.HookEventError("hook_artifact_conflict")
        else:
            hooks._write_private_file(path, raw)
    return None


def _index_source(root, binding):
    if binding.hook != "pre-commit":
        raise hooks.HookEventError("invocation_hook_mismatch")
    event = binding.event
    # Git write-tree may lock the real Index. Keep that proof serialized while
    # the actual tools work concurrently in their own immutable checkouts.
    with hooks._capture_lock(hooks._invocation_git_dir(binding.invocation_path)):
        index = hooks.resolve_index_path(event, root=root)
        if not hooks._active_index_matches(root, index, event["index"]):
            raise hooks.HookEventError("index_changed_during_check")
    head = selection._resolve(root, "HEAD")
    if head != event.get("head_commit") or hooks._pre_commit_base_tree(
        root
    ) != event.get("base_tree"):
        raise hooks.HookEventError("pre_commit_base_changed_during_check")
    return head


def _invoke_worker(root, binding, operation, payload):
    head = _index_source(root, binding)
    result = _source_worker(
        root, binding.event["index"]["tree"], head, operation, payload
    )
    _index_source(root, binding)
    return result


def _source_worker(root, tree, head, operation, payload):
    with git_source.materialized_tree(root, tree, head=head) as (source, env):
        worker_env = dict(env)
        if "PYTHONPATH" in worker_env:
            entries = [
                str(Path(item).resolve()) if not Path(item).is_absolute() else item
                for item in worker_env["PYTHONPATH"].split(os.pathsep)
                if item and item != "."
            ]
            if entries:
                worker_env["PYTHONPATH"] = os.pathsep.join(entries)
            else:
                worker_env.pop("PYTHONPATH", None)
        result = subprocess.run(
            [sys.executable, "-P", "-m", "engineering_ci.hook_checks", operation],
            cwd=source,
            env=worker_env,
            input=json.dumps({**payload, "checkout": str(Path(root).resolve())}),
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
    if result.returncode != 0:
        diagnostic = result.stderr.strip()
        raise hooks.HookEventError(diagnostic or "hook_check_source_worker_failed")
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise hooks.HookEventError("hook_check_source_worker_output_invalid") from None


def _identity(binding):
    return {
        "invocation_ref": binding.invocation_ref,
        "event_payload_ref": binding.event_payload_ref,
        "run_ref": f"hook-run:{binding.invocation_ref}",
        "attempt_ref": f"hook-attempt:{binding.invocation_ref}",
    }


def _load_plan(binding):
    prepared = _artifact(binding, ".plan")
    if prepared is None:
        raise hooks.HookEventError("hook_plan_missing")
    if any(prepared.get(key) != value for key, value in _identity(binding).items()):
        raise hooks.HookEventError("hook_plan_invocation_mismatch")
    if "plan" not in prepared or prepared.get("plan_ref") != hooks._digest_bytes(
        hooks.canonical_event_bytes(prepared["plan"])
    ):
        raise hooks.HookEventError("hook_plan_digest_mismatch")
    return prepared


def _planned(root, binding):
    prepared = _load_plan(binding)
    head = _index_source(root, binding)
    plan = prepared["plan"]
    if plan is not None:
        if not isinstance(plan, dict):
            raise hooks.HookEventError("hook_plan_invalid")
        git = plan.get("git") or {}
        tree = binding.event["index"]["tree"]
        if (
            git.get("snapshot") != "index"
            or git.get("execution_tree") != tree
            or git.get("index_tree") != tree
            or git.get("index_file") is not None
            or git.get("head_commit") != head
            or (head is None and git.get("base_tree") != binding.event["base_tree"])
        ):
            raise hooks.HookEventError("hook_plan_source_mismatch")
    return prepared


def prepare(root, binding, *, dagger_module=None, split_checks=False):
    _index_source(root, binding)
    tree = binding.event["index"]["tree"]
    baseline = binding.event["base_tree"]
    config_names = (_SELECTION_CONFIG, *_PROFILE_FILES)
    staged = hooks._git(root, "ls-tree", "--name-only", tree, "--", *config_names)
    old = hooks._git(root, "ls-tree", "--name-only", baseline, "--", *config_names)
    if staged.returncode or old.returncode:
        raise hooks.HookEventError("hook_selection_config_tree_unavailable")
    staged_names = set(staged.stdout.splitlines())
    baseline_names = set(old.stdout.splitlines())
    staged_profiles = staged_names.intersection(_PROFILE_FILES)
    if not staged_profiles and baseline_names.intersection(_PROFILE_FILES):
        raise hooks.HookEventError("hook_effective_profile_removed")
    if staged_profiles and _SELECTION_CONFIG not in staged_names:
        raise hooks.HookEventError("hook_selection_config_missing")
    planned = (
        _invoke_worker(root, binding, "plan", {"split_checks": split_checks})
        if staged_profiles
        else {"plan": None, "split_checks": False}
    )
    plan = planned["plan"]
    module_ref = (
        dagger_plan.module_reference(dagger_module, root=root)
        if plan is not None and _requires_dagger(root, plan)
        else None
    )
    prepared = {
        **_identity(binding),
        **planned,
        "plan_ref": hooks._digest_bytes(hooks.canonical_event_bytes(plan)),
        "dagger_module": module_ref,
    }
    _artifact(binding, ".plan", prepared)
    return {"invocation_ref": binding.invocation_ref, "exit_code": 0}


def prepare_push(
    root,
    binding,
    *,
    new_ref_baseline=None,
    effective_profiles=(),
    require_plan=False,
    dagger_module=None,
):
    """Prepare distinct Plans for the ordered Ref targets of an actual push."""
    if binding.hook != "pre-push":
        raise hooks.HookEventError("invocation_hook_mismatch")
    refs = binding.event["refs"]
    configured_refs = refs if new_ref_baseline else _push_ref_baselines(root, refs)
    changes = selection.changeset_multi(
        root, configured_refs, new_ref_baseline=new_ref_baseline
    )
    result = {
        **_identity(binding),
        "references": list(changes.references),
        "status": "prepared" if changes.complete else "blocked",
        "reason": changes.error,
        "exit_code": int(not changes.complete),
    }
    if not refs:
        result.update(status="not_applicable", reason="no_ref_updates", exit_code=0)
    _artifact(binding, ".push-source", result)
    if result["exit_code"] or not (effective_profiles or require_plan):
        return result
    plans = []
    for reference in result["references"]:
        tree, head = _push_checkout(root, reference)
        plans.append(
            _source_worker(
                root,
                tree,
                head,
                "plan-push",
                {"reference": reference, "profiles": list(effective_profiles)},
            )
        )
    module_ref = (
        dagger_plan.module_reference(dagger_module, root=root)
        if any(
            plan["git"].get("ref_action") != "delete"
            and _push_needs_execution(plan)
            and _requires_dagger(root, plan)
            for plan in plans
        )
        else None
    )
    _artifact(
        binding,
        ".plan",
        {
            **_identity(binding),
            "plan": plans,
            "plan_ref": hooks._digest_bytes(hooks.canonical_event_bytes(plans)),
            "dagger_module": module_ref,
        },
    )
    return result


def _push_ref_baselines(root, refs):
    result = []
    for ref in refs:
        if ref["ref_action"] == "create":
            head = selection._resolve(root, ref["after"])
            data = (
                selection._git_bytes(root, "show", f"{head}:{_SELECTION_CONFIG}")
                if head
                else None
            )
            if data is not None and data.returncode == 0:
                baseline = selection.resolve_new_ref_baseline(
                    selection._parse_config_bytes(data.stdout),
                    remote_ref=ref["remote_ref"],
                    local_ref=ref["local_ref"],
                )
                ref = {**ref, "new_ref_baseline": baseline}
        result.append(ref)
    return result


def _push_checkout(root, ref):
    if ref["ref_action"] == "delete":
        # Deletion has no execution target tree; only the deleted ref's verified
        # local `before` commit supplies approved delete policy. Absent old
        # objects fail closed instead of reading unverified worktree/HEAD policy.
        head = ref["before"]
        tree = selection._tree_of(root, head) if head else None
        error_code = "hook_push_delete_policy_unavailable"
    else:
        head, tree = ref["after"], ref["tree"]
        error_code = "hook_push_source_unavailable"
    if not head or not tree or selection._tree_of(root, head) != tree:
        raise hooks.HookEventError(error_code)
    return tree, head


def _push_plans(root, binding):
    prepared = _load_plan(binding)
    source = _artifact(binding, ".push-source")
    if source is None or any(
        source.get(key) != value for key, value in _identity(binding).items()
    ):
        raise hooks.HookEventError("hook_push_source_invocation_mismatch")
    plans, refs = prepared["plan"], source.get("references")
    if not isinstance(plans, list) or not isinstance(refs, list) or source["exit_code"]:
        raise hooks.HookEventError("hook_push_plan_source_invalid")
    args = binding.event["refs"]
    if len(plans) != len(refs) or len(refs) != len(args):
        raise hooks.HookEventError("hook_push_plan_ref_count_mismatch")
    # The immutable resolved create baseline replaces the mutable ref name when
    # rechecking Source. Every other OID still comes from the original Git stdin.
    current = selection.changeset_multi(
        root,
        [
            {
                **raw,
                "new_ref_baseline": ref["before"]
                if ref["ref_action"] == "create"
                else None,
            }
            for raw, ref in zip(args, refs, strict=True)
        ],
    )
    if list(current.references) != refs:
        raise hooks.HookEventError("hook_push_source_mismatch")
    for plan, ref in zip(plans, refs, strict=True):
        if not isinstance(plan, dict):
            raise hooks.HookEventError("hook_plan_invalid")
        git = plan.get("git") or {}
        if (
            git.get("snapshot") != "push"
            or git.get("references") != [ref]
            or git.get("head_commit") != ref["after"]
            or git.get("base_commit") != ref["before"]
            or git.get("tree") != ref["tree"]
            or git.get("execution_tree") != ref["tree"]
            or git.get("ref_action") != ref["ref_action"]
            or git.get("changed_paths") != list(ref["paths"])
            or git.get("deleted_paths") != list(ref["deleted_paths"])
        ):
            raise hooks.HookEventError("hook_push_plan_source_mismatch")
    return prepared, refs


def _independent_index_jobs(plan):
    chosen = plan["selection"]
    if chosen.get("prerequisites") or (plan.get("dependency_contract") or {}).get(
        "edges"
    ):
        return {}
    checks = dict.fromkeys(item["name"] for item in execution._selected_checks(plan))
    framework = chosen.get("framework", "unittest")
    if chosen["tests"]:
        checks[framework] = None
    jobs = {}
    for name in checks:
        if name == framework and chosen["tests"]:
            job = "check-tests"
        elif name in execution._WORKFLOW_SCRIPT_CHECKS:
            # Workflow checks share Actionlint's inline ShellCheck observation.
            job = "check-workflows"
        elif name in execution._HYGIENE_CHECKS or name == "source":
            job = f"check-{name.replace('_', '-')}"
        else:
            return {}
        jobs.setdefault(job, []).append(name)
    return jobs


def _index_jobs(prepared):
    if prepared.get("split_checks") is True:
        return _independent_index_jobs(prepared["plan"])
    return {_JOB_REF: None}


def skip_index_job(binding, job_ref):
    return job_ref not in _index_jobs(_load_plan(binding))


def _index_execution_suffix(job_ref):
    return ".execution" if job_ref == _JOB_REF else f".execution-{job_ref}"


def _index_activation_suffix(job_ref):
    return ".activation" if job_ref == _JOB_REF else f".activation-{job_ref}"


def run_checks(root, binding, *, job_ref=_JOB_REF):
    prepared = _planned(root, binding)
    jobs = _index_jobs(prepared)
    if job_ref not in jobs:
        raise hooks.HookEventError("hook_index_job_not_applicable")
    _artifact(
        binding, _index_activation_suffix(job_ref), _identity(binding), exclusive=True
    )
    suffix = _index_execution_suffix(job_ref)
    if prepared["plan"] is None:
        report = hooks.check_pre_commit(root, invocation_ref=binding.invocation_ref)
    else:
        report = _invoke_worker(
            root,
            binding,
            "execute",
            {
                **prepared,
                "dagger_bundle": str(_dagger_bundle(binding, suffix)),
                "job_ref": job_ref,
                "check_names": jobs[job_ref],
            },
        )
    _record_execution(binding, prepared, report, suffix=suffix)
    return report


def _record_execution(binding, prepared, report, *, suffix=".execution"):
    _artifact(
        binding,
        suffix,
        {
            **_identity(binding),
            "report": report,
            "plan_ref": prepared["plan_ref"],
            "dagger_module": prepared.get("dagger_module"),
            "report_digest": hooks._digest_bytes(hooks.canonical_event_bytes(report)),
        },
    )


def _dagger_bundle(binding, suffix=".execution"):
    path = binding.invocation_path.with_suffix(suffix.replace(".execution", ".dagger"))
    if (
        path.is_symlink()
        or path.is_junction()
        or path.resolve().parent != binding.invocation_path.parent
    ):
        raise hooks.HookEventError("hook_artifact_outside_invocation")
    return path


def _requires_dagger(root, plan):
    project = False
    if plan["selection"].get("tests"):
        result = hooks._git(
            root,
            "ls-tree",
            "--name-only",
            plan["git"]["execution_tree"],
            "--",
            "pyproject.toml",
        )
        if result.returncode:
            raise hooks.HookEventError("hook_dagger_project_tree_unavailable")
        project = result.stdout.strip() == "pyproject.toml"
    return dagger_plan.required(plan, python_project=project)


def _observe(
    plan, recorded, expected, report, job_ref, execution_suffix, *, native_dagger
):
    identity = report.get("execution_identity") or {}
    if (
        identity.get("run_ref") != recorded["run_ref"]
        or identity.get("attempt_ref") != recorded["attempt_ref"]
        or (report.get("plan") or {}).get("git") != plan["git"]
    ):
        return None, None
    if native_dagger:
        if not identity.get("execution_ref") or not dagger_plan.source_bound(
            plan, report
        ):
            return None, None
    elif (
        identity.get("execution_ref")
        != f"execution:{recorded['invocation_ref']}:{execution_suffix}"
    ):
        return None, None
    _results, _rejections, report_ref = reconciliation.verify_execution_report(report)
    attributed = {
        "job_ref": job_ref,
        "run_ref": recorded["run_ref"],
        "attempt_ref": recorded["attempt_ref"],
        "runner_ref": "execution:dagger" if native_dagger else _RUNNER_REF,
        "report_ref": report_ref,
    }
    scope = {
        "invocation_ref": recorded["invocation_ref"],
        "event_ref": expected["event_ref"],
        "scope_id": expected["scope_id"],
    }
    issuer = {
        "name": "engineering-ci.local-hook-job",
        "version": "1",
        "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    qualification = {
        **issuer,
        "sha256": hashlib.sha256(
            hooks.canonical_event_bytes({**attributed, **scope})
        ).hexdigest(),
    }
    route = {
        **attributed,
        **scope,
        "attempt_eligibility": "eligible",
        "qualification_ref": qualification,
    }
    observation = {
        **attributed,
        "report": report,
        "job_receipt": {
            **attributed,
            **scope,
            "kind": reconciliation.JOB_RECEIPT_KIND,
            "schema_version": reconciliation.JOB_RECEIPT_SCHEMA_VERSION,
            "issuer_ref": issuer,
            "verification_status": "upstream_verified",
            "expected_set_ref": expected["expected_set_ref"],
        },
    }
    return route, observation


def _load_execution(
    binding,
    prepared,
    *,
    suffix=".execution",
    activation=".activation",
    native_plan=None,
):
    recorded = _artifact(binding, suffix)
    activated = _artifact(binding, activation)
    if recorded is not None and activated != _identity(binding):
        raise hooks.HookEventError("hook_report_activation_missing")
    if recorded is not None and recorded.get("plan_ref") != prepared["plan_ref"]:
        raise hooks.HookEventError("hook_report_plan_mismatch")
    if recorded is not None and recorded.get("dagger_module") != prepared.get(
        "dagger_module"
    ):
        raise hooks.HookEventError("hook_report_dagger_module_mismatch")
    if recorded is not None and any(
        recorded.get(key) != value for key, value in _identity(binding).items()
    ):
        raise hooks.HookEventError("hook_report_invocation_mismatch")
    report = recorded.get("report") if recorded is not None else None
    if recorded is not None and (
        not isinstance(report, dict)
        or recorded.get("report_digest")
        != hooks._digest_bytes(hooks.canonical_event_bytes(report))
    ):
        raise hooks.HookEventError("hook_report_digest_mismatch")
    if (
        report is not None
        and native_plan is not None
        and dagger_plan.load_result(
            _dagger_bundle(binding, suffix),
            native_plan,
            run_ref=recorded["run_ref"],
            attempt_ref=recorded["attempt_ref"],
        )
        != report
    ):
        raise hooks.HookEventError("hook_dagger_report_mismatch")
    return recorded, report


def _reconcile_plan(
    binding,
    plan,
    evidence,
    resolve,
):
    expected = plan["expected_checks"]
    routing, observations = [], []
    for job_ref, names, route, observation in evidence:
        if observation is not None:
            observations.append(observation)
        routing.extend(
            {
                "expected_check_ref": check["expected_check_ref"],
                "mapping_status": "routed",
                "job_refs": [job_ref],
                "job_instances": [route] if route is not None else [],
            }
            for check in expected["checks"]
            if names is None or check["name"] in names
        )
    receipt = reconciliation.reconcile_expected_results(
        expected,
        routing=routing,
        resolutions=resolve() if observations else [],
        observations=observations,
        invocation_ref=binding.invocation_ref,
    )
    reconciliation.validate_receipt(receipt)
    return receipt


def _index_reports(binding, prepared, *, native_dagger):
    return {
        job_ref: _load_execution(
            binding,
            prepared,
            suffix=_index_execution_suffix(job_ref),
            activation=_index_activation_suffix(job_ref),
            native_plan=prepared["plan"] if native_dagger else None,
        )
        for job_ref in _index_jobs(prepared)
    }


def _original_exit(reports):
    return next(
        (report["exit_code"] for report in reports if report and report["exit_code"]), 0
    )


def reconcile(root, binding):
    prepared = _planned(root, binding)
    plan = prepared["plan"]
    native_dagger = plan is not None and _requires_dagger(root, plan)
    reports = _index_reports(binding, prepared, native_dagger=native_dagger)
    if plan is None:
        _, report = reports[_JOB_REF]
        if report is None:
            raise hooks.HookEventError("hook_report_missing")
        outcome = {**_identity(binding), "exit_code": report["exit_code"]}
        _artifact(binding, ".receipt", outcome)
        return outcome
    evidence = []
    for job_ref, names in _index_jobs(prepared).items():
        recorded, report = reports[job_ref]
        route = observation = None
        if report is not None:
            route, observation = _observe(
                plan,
                recorded,
                plan["expected_checks"],
                report,
                job_ref,
                job_ref,
                native_dagger=native_dagger,
            )
        evidence.append((job_ref, names, route, observation))
    receipt = _reconcile_plan(
        binding,
        plan,
        evidence,
        lambda: _invoke_worker(
            root, binding, "resolve", {**prepared, "native_dagger": native_dagger}
        ),
    )
    _artifact(binding, ".receipt", receipt)
    return {
        "invocation_ref": binding.invocation_ref,
        "receipt": receipt,
        "exit_code": _original_exit(report for _, report in reports.values())
        or reconciliation.hook_exit_code([receipt]),
    }


def consume_index_result(root, binding):
    """Require a final result even if Lefthook suppressed the reconciliation Job."""
    prepared = _planned(root, binding)
    receipt = _artifact(binding, ".receipt")
    if receipt is None:
        raise hooks.HookEventError("hook_final_result_missing")
    plan = prepared["plan"]
    native_dagger = plan is not None and _requires_dagger(root, plan)
    reports = _index_reports(binding, prepared, native_dagger=native_dagger)
    if plan is None:
        _, report = reports[_JOB_REF]
        if report is None or receipt != {
            **_identity(binding),
            "exit_code": report["exit_code"],
        }:
            raise hooks.HookEventError("hook_final_result_mismatch")
        return receipt
    _check_receipt_scope(binding, plan, receipt)
    if native_dagger:
        _, report = reports[_JOB_REF]
        if report is None or not dagger_plan.source_bound(plan, report):
            raise hooks.HookEventError("hook_dagger_source_unverified")
    if any(report is None for _, report in reports.values()):
        raise hooks.HookEventError("hook_report_missing")
    _check_receipt_reports(
        receipt, {job: report for job, (_, report) in reports.items()}
    )
    return {
        "exit_code": _original_exit(report for _, report in reports.values())
        or reconciliation.hook_exit_code([receipt])
    }


def _check_receipt_scope(binding, plan, receipt):
    reconciliation.validate_receipt(receipt)
    expected = plan["expected_checks"]
    if (
        receipt.get("expected_invocation_ref") != binding.invocation_ref
        or receipt.get("scope_id") != expected["scope_id"]
        or receipt.get("expected_set_ref") != expected["expected_set_ref"]
    ):
        raise hooks.HookEventError("hook_final_result_mismatch")


def _check_receipt_reports(receipt, reports):
    references = {
        job: reconciliation.execution_report_ref(report)
        for job, report in reports.items()
        if report is not None
    }
    if any(
        result["report_ref"] != references.get(result["job_ref"])
        for entry in receipt["entries"]
        for result in entry["observed_results"]
    ):
        raise hooks.HookEventError("hook_final_report_mismatch")


def _run_push_plan(root, plan, source, prepared, job_ref, operation):
    tree, head = _push_checkout(root, source)
    return _source_worker(
        root,
        tree,
        head,
        operation,
        {**prepared, "plan": plan, "job_ref": job_ref},
    )


def _push_execution_suffix(index):
    return f"{_PUSH_JOB_REF}:{index}"


def _push_needs_execution(plan):
    expected = plan["expected_checks"]
    return expected["status"] != "complete" or any(
        item["applicability"]["status"] not in _INAPPLICABLE
        for item in expected["checks"]
    )


def run_push_checks(root, binding):
    prepared, refs = _push_plans(root, binding)
    _artifact(binding, ".activation", _identity(binding), exclusive=True)
    reports = []
    for index, (plan, source) in enumerate(zip(prepared["plan"], refs, strict=True)):
        report = None
        if source["ref_action"] != "delete" and _push_needs_execution(plan):
            artifact_suffix = f".execution-{index}"
            report = _run_push_plan(
                root,
                plan,
                source,
                {
                    **prepared,
                    "dagger_bundle": str(_dagger_bundle(binding, artifact_suffix)),
                },
                _push_execution_suffix(index),
                "execute",
            )
            _record_execution(binding, prepared, report, suffix=artifact_suffix)
        reports.append(report)
    return {
        "references": [
            {"remote_ref": ref["remote_ref"], "report": report}
            for ref, report in zip(refs, reports, strict=True)
        ],
        "exit_code": next((r["exit_code"] for r in reports if r and r["exit_code"]), 0),
    }


def reconcile_push(root, binding):
    prepared, refs = _push_plans(root, binding)
    reports = []
    receipts = []
    for index, (plan, source) in enumerate(zip(prepared["plan"], refs, strict=True)):
        native_dagger = source["ref_action"] != "delete" and _requires_dagger(
            root, plan
        )
        recorded, report = _load_execution(
            binding,
            prepared,
            suffix=f".execution-{index}",
            native_plan=plan if native_dagger else None,
        )
        suffix = _push_execution_suffix(index)
        route = observation = None
        if report is not None:
            route, observation = _observe(
                plan,
                recorded,
                plan["expected_checks"],
                report,
                _PUSH_JOB_REF,
                suffix,
                native_dagger=native_dagger,
            )
        receipt = _reconcile_plan(
            binding,
            plan,
            [(_PUSH_JOB_REF, None, route, observation)],
            partial(
                _run_push_plan,
                root,
                plan,
                source,
                {**prepared, "native_dagger": native_dagger},
                suffix,
                "resolve",
            ),
        )
        reports.append(report)
        receipts.append(receipt)
    _artifact(
        binding,
        ".receipt",
        {**_identity(binding), "plan_ref": prepared["plan_ref"], "receipts": receipts},
    )
    return {
        "references": [
            {"remote_ref": ref["remote_ref"], "receipt": receipt}
            for ref, receipt in zip(refs, receipts, strict=True)
        ],
        "exit_code": next(
            (r["exit_code"] for r in reports if r and r["exit_code"]),
            reconciliation.hook_exit_code(receipts) if refs else 0,
        ),
    }


def consume_push_result(root, binding):
    """Require final reconciliation for every Ref in the original push order."""
    prepared, refs = _push_plans(root, binding)
    result = _artifact(binding, ".receipt")
    if result is None:
        raise hooks.HookEventError("hook_final_result_missing")
    receipts = result.get("receipts")
    if (
        any(result.get(key) != value for key, value in _identity(binding).items())
        or result.get("plan_ref") != prepared["plan_ref"]
        or not isinstance(receipts, list)
        or len(receipts) != len(refs)
    ):
        raise hooks.HookEventError("hook_final_result_mismatch")
    native_exit = 0
    for index, (plan, source, receipt) in enumerate(
        zip(prepared["plan"], refs, receipts, strict=True)
    ):
        _check_receipt_scope(binding, plan, receipt)
        native_plan = (
            plan
            if source["ref_action"] != "delete"
            and _push_needs_execution(plan)
            and _requires_dagger(root, plan)
            else None
        )
        _, report = _load_execution(
            binding, prepared, suffix=f".execution-{index}", native_plan=native_plan
        )
        _check_receipt_reports(receipt, {_PUSH_JOB_REF: report})
        if native_plan is not None and (
            report is None or not dagger_plan.source_bound(plan, report)
        ):
            raise hooks.HookEventError("hook_dagger_source_unverified")
        native_exit = native_exit or (report or {}).get("exit_code", 0)
    return {
        "exit_code": native_exit
        or (reconciliation.hook_exit_code(receipts) if refs else 0)
    }


def print_result(outcome, invocation_ref):
    print(invocation_ref)
    check = outcome.get("check")
    if check is not None:
        sys.stdout.write(check.get("stdout") or "")
        sys.stderr.write(check.get("stderr") or "")
    for check in outcome.get("checks") or ():
        status = check.get("status")
        if status in {"failed", "not_executed", "advisory"}:
            print(
                f"{check['name']}: {check.get('reason') or status}",
                file=sys.stderr,
            )
            for finding in check.get("findings") or ():
                print(finding, file=sys.stderr)
            sys.stdout.write(check.get("stdout") or "")
            sys.stderr.write(check.get("stderr") or "")
    if outcome.get("reason") is not None:
        print(outcome["reason"], file=sys.stderr)
    for reference in outcome.get("references") or ():
        if reference.get("error"):
            print(f"{reference['remote_ref']}: {reference['error']}", file=sys.stderr)
        if reference.get("report") is not None:
            print_result(reference["report"], reference["remote_ref"])
        if reference.get("receipt") is not None:
            print(f"{reference['remote_ref']}: {reference['receipt']['status']}")
    receipt = outcome.get("receipt")
    if receipt is not None:
        print(f"reconciliation: {receipt['status']}")


def _matching_push_profile(root, changes, candidates):
    profiles = []
    for candidate in map(Path, candidates):
        if candidate.is_absolute() or ".." in candidate.parts:
            raise hooks.HookEventError("hook_push_profile_outside_source")
        path = (root / candidate).resolve()
        if not path.is_relative_to(root):
            raise hooks.HookEventError("hook_push_profile_outside_source")
        if not path.is_file():
            continue
        try:
            expected_checks.load_effective_profile(
                path, git=expected_checks._git_from_changeset(changes)
            )
        except expected_checks.ExpectedError as error:
            if str(error) == "effective_profile_event_mismatch":
                continue
            raise
        profiles.append(path)
    if len(profiles) != 1:
        raise hooks.HookEventError("hook_push_effective_profile_required")
    return profiles[0]


def _make_plan(operation, payload, root):
    config_path = Path(_SELECTION_CONFIG)
    config = selection.load_config(root / config_path)
    if operation == "plan-push":
        ref = payload["reference"]
        changes = replace(selection.ref_changeset(root, ref), references=(ref,))
        profile = _matching_push_profile(
            root, changes, payload["profiles"] or config.push.effective_profiles
        )
    else:
        profiles = [root / name for name in _PROFILE_FILES if (root / name).is_file()]
        if len(profiles) != 1:
            raise hooks.HookEventError("hook_effective_profile_required")
        profile = profiles[0]
        changes = selection.changeset(root, base="HEAD", mode="index")
    chosen = selection.select(changes, config, root=root)
    tach_validation = (
        selection._tach_check(root, config, chosen.changeset)
        if chosen.tests and not chosen.full_tests
        else None
    )
    return selection.plan(
        chosen,
        config=config,
        config_path=config_path,
        root=root,
        tach_validation=tach_validation,
        effective_profile_path=profile,
    )


def _work(operation, payload, root):
    if operation == "plan-push":
        return _make_plan(operation, payload, root)
    if operation == "plan":
        plan = _make_plan(operation, payload, root)
        selected = execution._selected_checks(plan)
        split_checks = (
            payload.get("split_checks")
            and not dagger_plan.required(
                plan, python_project=(root / "pyproject.toml").is_file()
            )
            and bool(_independent_index_jobs(plan))
            and not execution.omitted_hygiene_checks(plan, selected, root=root)
            and not execution.omitted_workflow_script_checks(plan, selected, root=root)
        )
        return {"plan": plan, "split_checks": bool(split_checks)}
    plan = payload["plan"]
    if operation == "execute":
        if dagger_plan.required(
            plan, python_project=(root / "pyproject.toml").is_file()
        ):
            if payload.get("check_names") is not None:
                raise hooks.HookEventError("hook_dagger_split_unsupported")
            return dagger_plan.execute(
                plan,
                source=root,
                checkout=Path(payload["checkout"]),
                destination=Path(payload["dagger_bundle"]),
                module_ref=payload.get("dagger_module"),
                run_ref=payload["run_ref"],
                attempt_ref=payload["attempt_ref"],
            )
        return execution.execute(
            plan,
            root=root,
            run_ref=payload["run_ref"],
            attempt_ref=payload["attempt_ref"],
            run_instance=f"{payload['invocation_ref']}:{payload.get('job_ref', _JOB_REF)}",
            check_names=payload.get("check_names"),
        )
    if operation != "resolve":
        raise hooks.HookEventError("hook_source_worker_operation_invalid")
    return [
        execution.evaluate_source_resolution(
            plan,
            check,
            root=root,
            execution_context="dagger_export"
            if payload.get("native_dagger")
            else "host",
        )
        for check in plan["expected_checks"]["checks"]
        if check["applicability"]["status"] not in _INAPPLICABLE
    ]


if __name__ == "__main__":
    try:
        value = _work(sys.argv[1], json.load(sys.stdin), Path.cwd())
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2) from None
    print(json.dumps(value, ensure_ascii=True))
