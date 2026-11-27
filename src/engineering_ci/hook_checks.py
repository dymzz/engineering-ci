"""Source, execution and reconciliation for Lefthook jobs."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager, suppress
from dataclasses import replace
from functools import partial
from pathlib import Path

from . import (
    check_contract,
    dagger_plan,
    execution,
    expected_checks,
    git_source,
    hooks,
    plan_contract,
    reconciliation,
    selection,
)

_SELECTION_CONFIG = "ci-selection.toml"
_PROFILE_FILES = ("ci-effective-profile.json", "ci-effective-profile.toml")
_JOB_REF = "check-index"
_PUSH_JOB_REF = "check-push"
_RUNNER_REF = "execution:host"
_INAPPLICABLE = {"not_applicable", "no_tests", "no_changes"}


_HOOK_ARTIFACT_LIMIT = 16 * 1024 * 1024


def _hook_artifact_path(binding, suffix):
    path = binding.invocation_path.with_suffix(suffix)
    if (
        path.is_symlink()
        or path.is_junction()
        or path.resolve().parent != binding.invocation_path.parent
    ):
        raise hooks.HookEventError("hook_artifact_outside_invocation")
    return path


def _decode_hook_artifact(path, *, limit):
    if not path.exists():
        return None
    if not path.is_file():
        raise hooks.HookEventError("hook_artifact_invalid")
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise hooks.HookEventError("hook_artifact_oversized")
        content = json.loads(raw)
        if not isinstance(content, dict) or hooks.canonical_event_bytes(content) != raw:
            raise hooks.HookEventError("hook_artifact_invalid")
    except (OSError, ValueError, RecursionError) as exc:
        if isinstance(exc, hooks.HookEventError):
            raise
        raise hooks.HookEventError("hook_artifact_invalid") from None
    return content


def _artifact(binding, suffix, payload=None, *, exclusive=False):
    path = _hook_artifact_path(binding, suffix)
    if payload is None:
        return _decode_hook_artifact(path, limit=_HOOK_ARTIFACT_LIMIT)
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


@contextmanager
def _stage_timing(
    binding,
    stage,
    *,
    job_ref=None,
    suffix=None,
    started_mono=None,
    started_ns=None,
    exclusive_activation=False,
):
    """Measure monotonic and UTC Hook stage timings into an invocation sidecar."""
    mono_start = time.perf_counter() if started_mono is None else started_mono
    ns_start = time.time_ns() if started_ns is None else started_ns
    details = {"status": "ok", "extra": {}}
    try:
        yield details
    except BaseException as exc:
        details["status"] = (
            "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        )
        details["extra"]["error"] = type(exc).__name__
        raise
    else:
        exit_code = details["extra"].get("exit_code")
        if exit_code is not None:
            details["status"] = "passed" if exit_code == 0 else "failed"
    finally:
        completed_ns = time.time_ns()
        elapsed = time.perf_counter() - mono_start
        with suppress(OSError, hooks.HookEventError):
            hooks._record_stage_timing(
                binding.invocation_path,
                invocation_ref=binding.invocation_ref,
                event_payload_ref=binding.event_payload_ref,
                hook=binding.hook,
                stage=stage,
                started_ns=ns_start,
                completed_ns=completed_ns,
                elapsed_seconds=elapsed,
                status=details["status"],
                job_ref=job_ref,
                suffix=suffix,
                extra=details["extra"] or None,
                exclusive_activation=exclusive_activation,
            )


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
    _validate_plan_record(binding, prepared)
    return prepared


def _validate_plan_record(binding, prepared):
    if any(prepared.get(key) != value for key, value in _identity(binding).items()):
        raise hooks.HookEventError("hook_plan_invocation_mismatch")
    if "plan" not in prepared or prepared.get("plan_ref") != hooks._digest_bytes(
        hooks.canonical_event_bytes(prepared["plan"])
    ):
        raise hooks.HookEventError("hook_plan_digest_mismatch")
    if "split_checks" in prepared and type(prepared["split_checks"]) is not bool:
        raise hooks.HookEventError("hook_plan_invalid")


def _planned(root, binding):
    prepared = _load_plan(binding)
    head = _index_source(root, binding)
    _validate_index_plan_source(binding, prepared["plan"], head)
    return prepared


def _validate_index_plan_source(binding, plan, head):
    if plan is not None:
        if not isinstance(plan, dict) or not isinstance(plan.get("git"), dict):
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


def prepare(root, binding, *, dagger_module=None, split_checks=False):
    with _stage_timing(binding, "prepare", job_ref="prepare") as timing:
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
        timing["extra"].update(plan_ref=prepared["plan_ref"], exit_code=0)
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
    with _stage_timing(binding, "prepare", job_ref="prepare-push") as timing:
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
            timing["extra"]["exit_code"] = result["exit_code"]
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
        plan_ref = hooks._digest_bytes(hooks.canonical_event_bytes(plans))
        _artifact(
            binding,
            ".plan",
            {
                **_identity(binding),
                "plan": plans,
                "plan_ref": plan_ref,
                "dagger_module": module_ref,
            },
        )
        timing["extra"].update(plan_ref=plan_ref, exit_code=result["exit_code"])
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
    refs = _validate_push_plan_source(binding, prepared, source)
    args = binding.event["refs"]
    # The immutable resolved create baseline replaces the mutable ref name when rechecking Git.
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
    return prepared, refs


def _validate_push_plan_source(binding, prepared, source):
    if source is None or any(
        source.get(key) != value for key, value in _identity(binding).items()
    ):
        raise hooks.HookEventError("hook_push_source_invocation_mismatch")
    plans, refs = prepared["plan"], source.get("references")
    if (
        not isinstance(plans, list)
        or not isinstance(refs, list)
        or type(source.get("exit_code")) is not int
        or source["exit_code"]
    ):
        raise hooks.HookEventError("hook_push_plan_source_invalid")
    args = binding.event["refs"]
    if len(plans) != len(refs) or len(refs) != len(args):
        raise hooks.HookEventError("hook_push_plan_ref_count_mismatch")
    for plan, ref, raw in zip(plans, refs, args, strict=True):
        if not isinstance(plan, dict) or not isinstance(plan.get("git"), dict):
            raise hooks.HookEventError("hook_plan_invalid")
        if (
            not isinstance(ref, dict)
            or any(
                key not in ref
                for key in (
                    "tree",
                    "before",
                    "after",
                    "paths",
                    "deleted_paths",
                    "ref_action",
                )
            )
            or any(ref.get(key) != raw.get(key) for key in ("local_ref", "remote_ref"))
        ):
            raise hooks.HookEventError("hook_push_source_mismatch")
        # Capture classifies the raw OID pair. Source resolution can then
        # identify a non-fast-forward update using the actual commit graph.
        if ref["ref_action"] != raw["ref_action"] and not (
            raw["ref_action"] == "update"
            and ref["ref_action"] == "force_update"
            and ref.get("force_push") is True
        ):
            raise hooks.HookEventError("hook_push_source_mismatch")
        if (ref.get("raw_before"), ref.get("raw_after")) != (
            raw.get("before"),
            raw.get("after"),
        ):
            raise hooks.HookEventError("hook_push_source_mismatch")
        git = plan.get("git") or {}
        if (
            git.get("snapshot") != "push"
            or git.get("references") != [ref]
            or git.get("head_commit") != ref["after"]
            or git.get("base_commit") != ref["before"]
            or git.get("tree") != ref["tree"]
            or git.get("execution_tree") != ref["tree"]
            or git.get("ref_action") != ref["ref_action"]
            or not isinstance(ref["paths"], list)
            or not isinstance(ref["deleted_paths"], list)
            or git.get("changed_paths") != ref["paths"]
            or git.get("deleted_paths") != ref["deleted_paths"]
        ):
            raise hooks.HookEventError("hook_push_plan_source_mismatch")
    return refs


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
    entry_mono = time.perf_counter()
    entry_ns = time.time_ns()
    prepared = _planned(root, binding)
    jobs = _index_jobs(prepared)
    if job_ref not in jobs:
        raise hooks.HookEventError("hook_index_job_not_applicable")
    _artifact(
        binding,
        _index_activation_suffix(job_ref),
        _identity(binding),
        exclusive=True,
    )
    timing_suffix = ".timing-run" if job_ref == _JOB_REF else f".timing-run-{job_ref}"
    with _stage_timing(
        binding,
        "run",
        job_ref=job_ref,
        suffix=timing_suffix,
        started_mono=entry_mono,
        started_ns=entry_ns,
        exclusive_activation=True,
    ) as timing:
        suffix = _index_execution_suffix(job_ref)
        if prepared["plan"] is None:
            report = hooks.check_pre_commit(root, invocation_ref=binding.invocation_ref)
        else:
            head = _index_source(root, binding)
            report = _source_worker(
                root,
                binding.event["index"]["tree"],
                head,
                "execute",
                {
                    **prepared,
                    "dagger_bundle": str(_dagger_bundle(binding, suffix)),
                    "job_ref": job_ref,
                    "check_names": jobs[job_ref],
                },
            )
        timing["extra"].update(
            plan_ref=prepared["plan_ref"], exit_code=report["exit_code"]
        )
        try:
            _index_source(root, binding)
        except BaseException as exc:
            timing["extra"]["source_rejection"] = getattr(
                exc, "code", type(exc).__name__
            )
            _record_unsealed_observation(binding, prepared, report, suffix)
            raise
        try:
            _record_execution(binding, prepared, report, suffix=suffix)
        except BaseException:
            timing["extra"]["report_status"] = "seal_failed"
            _record_unsealed_observation(binding, prepared, report, suffix)
            raise
        return report


def _record_unsealed_observation(binding, prepared, report, suffix):
    # Only `.execution*` is consumed by live reconciliation. The separate raw
    # observation preserves returned facts if Source qualification or sealing fails.
    with suppress(OSError, hooks.HookEventError):
        _record_execution(
            binding,
            prepared,
            report,
            suffix=suffix.replace(".execution", ".observation", 1),
        )


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
    if not _report_matches_role(
        plan, recorded, report, execution_suffix, native_dagger=native_dagger
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


def _report_matches_role(plan, recorded, report, execution_suffix, *, native_dagger):
    identity = report.get("execution_identity")
    projection = report.get("plan")
    if (
        not isinstance(identity, dict)
        or not isinstance(projection, dict)
        or identity.get("run_ref") != recorded["run_ref"]
        or identity.get("attempt_ref") != recorded["attempt_ref"]
        or projection.get("git") != plan["git"]
    ):
        return False
    if native_dagger:
        ref = identity.get("execution_ref")
        return (
            isinstance(ref, str)
            and bool(ref)
            and dagger_plan.source_bound(plan, report)
        )
    return (
        identity.get("execution_ref")
        == f"execution:{recorded['invocation_ref']}:{execution_suffix}"
    )


def _load_execution(
    binding,
    prepared,
    *,
    suffix=".execution",
    activation=".activation",
    native_plan=None,
):
    recorded = _artifact(binding, suffix)
    if (
        recorded is not None
        and _artifact(binding, suffix.replace(".execution", ".source-rejection", 1))
        is not None
    ):
        raise hooks.HookEventError("hook_source_rejected_after_execution")
    activated = _artifact(binding, activation)
    report = _validate_execution_record(binding, prepared, recorded, activated)
    if report is not None and native_plan is not None:
        _validate_recorded_dagger_bundle(binding, suffix, native_plan, recorded, report)
    return recorded, report


def _validate_execution_record(binding, prepared, recorded, activated):
    if recorded is None:
        return None
    if activated != _identity(binding):
        raise hooks.HookEventError("hook_report_activation_missing")
    if recorded.get("plan_ref") != prepared["plan_ref"]:
        raise hooks.HookEventError("hook_report_plan_mismatch")
    if "dagger_module" not in recorded or recorded["dagger_module"] != prepared.get(
        "dagger_module"
    ):
        raise hooks.HookEventError("hook_report_dagger_module_mismatch")
    if any(recorded.get(key) != value for key, value in _identity(binding).items()):
        raise hooks.HookEventError("hook_report_invocation_mismatch")
    report = recorded.get("report")
    if not isinstance(report, dict) or recorded.get(
        "report_digest"
    ) != hooks._digest_bytes(hooks.canonical_event_bytes(report)):
        raise hooks.HookEventError("hook_report_digest_mismatch")
    return report


def _validate_recorded_dagger_bundle(
    binding, suffix, native_plan, recorded, report, *, max_bytes=None
):
    if (
        dagger_plan.load_result(
            _dagger_bundle(binding, suffix),
            native_plan,
            run_ref=recorded["run_ref"],
            attempt_ref=recorded["attempt_ref"],
            max_bytes=max_bytes,
        )
        != report
    ):
        raise hooks.HookEventError("hook_dagger_report_mismatch")


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
        eligible_statuses=check_contract.LOCAL_SETTLEMENT_WITNESS_STATUSES,
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
    with _stage_timing(binding, "reconcile", job_ref="reconcile") as timing:
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
            timing["extra"].update(
                plan_ref=prepared["plan_ref"], exit_code=outcome["exit_code"]
            )
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
        exit_code = _original_exit(
            report for _, report in reports.values()
        ) or reconciliation.hook_exit_code([receipt])
        timing["extra"].update(plan_ref=prepared["plan_ref"], exit_code=exit_code)
        return {
            "invocation_ref": binding.invocation_ref,
            "receipt": receipt,
            "exit_code": exit_code,
        }


def consume_index_result(root, binding):
    """Require a final result even if Lefthook suppressed the reconciliation Job."""
    with _stage_timing(binding, "consume", job_ref="consume") as timing:
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
            timing["extra"].update(
                plan_ref=prepared["plan_ref"], exit_code=receipt["exit_code"]
            )
            return receipt
        if native_dagger:
            _, report = reports[_JOB_REF]
            if report is None or not dagger_plan.source_bound(plan, report):
                raise hooks.HookEventError("hook_dagger_source_unverified")
        if any(report is None for _, report in reports.values()):
            raise hooks.HookEventError("hook_report_missing")
        verify_index_receipt_binding(
            binding,
            plan,
            receipt,
            {job: report for job, (_, report) in reports.items()},
        )
        exit_code = _original_exit(
            report for _, report in reports.values()
        ) or reconciliation.hook_exit_code([receipt])
        timing["extra"].update(plan_ref=prepared["plan_ref"], exit_code=exit_code)
        return {"exit_code": exit_code}


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


def verify_index_receipt_binding(binding, plan, receipt, reports_by_job):
    """Pure read-only verification of pre-commit Receipt against Plan and Job Reports."""
    if not isinstance(plan, dict):
        raise hooks.HookEventError("hook_plan_invalid")
    _check_receipt_scope(binding, plan, receipt)
    if any(report is None for report in reports_by_job.values()):
        raise hooks.HookEventError("hook_report_missing")
    _check_receipt_reports(receipt, reports_by_job)
    references = {
        job: reconciliation.execution_report_ref(report)
        for job, report in reports_by_job.items()
        if report is not None
    }
    for entry in receipt.get("entries") or ():
        for seq_key in ("job_instances", "rejected_observations"):
            for item in entry.get(seq_key) or ():
                if (
                    isinstance(item, dict)
                    and "report_ref" in item
                    and item["report_ref"] != references.get(item.get("job_ref"))
                ):
                    raise hooks.HookEventError("hook_final_report_mismatch")


def verify_push_receipt_binding(
    binding, plans, receipts, reports_by_index, *, refs=None
):
    """Pure read-only verification of Plan[i] -> Ref[i] -> Receipt[i] -> Execution[i]."""
    if not isinstance(plans, list) or not isinstance(receipts, list):
        raise hooks.HookEventError("hook_final_result_mismatch")
    if len(receipts) != len(plans):
        raise hooks.HookEventError("hook_final_result_mismatch")
    if refs is not None and len(refs) != len(plans):
        raise hooks.HookEventError("hook_final_result_mismatch")
    for index, (plan, receipt) in enumerate(zip(plans, receipts, strict=True)):
        if not isinstance(plan, dict):
            raise hooks.HookEventError("hook_plan_invalid")
        _check_receipt_scope(binding, plan, receipt)
        report = reports_by_index.get(index)
        ref_action = (
            refs[index].get("ref_action")
            if refs is not None and index < len(refs) and isinstance(refs[index], dict)
            else (plan.get("git") or {}).get("ref_action")
        )
        needs_exec = ref_action != "delete" and _push_needs_execution(plan)
        if not needs_exec:
            if report is not None:
                raise hooks.HookEventError("hook_final_report_mismatch")
        elif report is None:
            raise hooks.HookEventError("hook_report_missing")
        _check_receipt_reports(receipt, {_PUSH_JOB_REF: report})
        expected_ref = (
            reconciliation.execution_report_ref(report) if report is not None else None
        )
        for entry in receipt.get("entries") or ():
            for seq_key in ("job_instances", "rejected_observations"):
                for item in entry.get(seq_key) or ():
                    if (
                        isinstance(item, dict)
                        and "report_ref" in item
                        and (
                            item.get("job_ref") != _PUSH_JOB_REF
                            or expected_ref is None
                            or item["report_ref"] != expected_ref
                        )
                    ):
                        raise hooks.HookEventError("hook_final_report_mismatch")
    for extra_index in reports_by_index:
        if extra_index < 0 or extra_index >= len(plans):
            raise hooks.HookEventError("hook_final_report_mismatch")


def _report_ref_canonical(report_ref):
    if isinstance(report_ref, dict):
        try:
            normalized = reconciliation._report_ref(report_ref, "invalid_report_ref")
            return json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        except (reconciliation.ReconciliationError, TypeError, ValueError):
            return json.dumps(report_ref, sort_keys=True, separators=(",", ":"))
    if isinstance(report_ref, str) and report_ref.strip():
        return report_ref.strip()
    return None


def _scan_receipt_report_refs(obj, out_refs):
    direct_rref = _report_ref_canonical(obj.get("report_ref"))
    if direct_rref:
        out_refs.append(direct_rref)
    for seq_key in ("entries", "results", "references"):
        seq = obj.get(seq_key)
        if not isinstance(seq, list):
            continue
        for item in seq:
            if not isinstance(item, dict):
                continue
            rr = _report_ref_canonical(item.get("report_ref"))
            if rr:
                out_refs.append(rr)
            for sub_key in ("evidence", "job_instances", "rejected_observations"):
                sub = item.get(sub_key)
                if not isinstance(sub, list):
                    continue
                for sub_item in sub:
                    if isinstance(sub_item, dict):
                        srr = _report_ref_canonical(sub_item.get("report_ref"))
                        if srr:
                            out_refs.append(srr)
    sub_receipts = obj.get("receipts")
    if isinstance(sub_receipts, list):
        for sub_rec in sub_receipts:
            if isinstance(sub_rec, dict):
                _scan_receipt_report_refs(sub_rec, out_refs)


def inspect_invocation_offline(binding, *, max_bytes=16 * 1024 * 1024):
    """Pure read-only owner inspection of Hook Plan, Activation, Executions, Receipt, and Timings.

    Separates offline artifact/identity/binding verification from live Git
    Source checks (``_index_source`` / ``_planned``). Never spawns subprocesses.
    """
    invocations_dir = binding.invocation_path.parent
    stem = binding.invocation_path.stem
    event = binding.event
    identity = _identity(binding)
    expected_run = identity["run_ref"]
    expected_att = identity["attempt_ref"]
    conflicts = []
    consumed_files = []

    def _load_sidecar(sidecar_path):
        consumed_files.append(str(sidecar_path))
        try:
            suffix = sidecar_path.name.removeprefix(stem)
            return _decode_hook_artifact(
                _hook_artifact_path(binding, suffix), limit=max_bytes
            )
        except hooks.HookEventError as exc:
            conflicts.append(
                {
                    "reason": "hook_sidecar_invalid",
                    "artifact": sidecar_path.name,
                    "detail": exc.code,
                }
            )
            return None

    plan_ref = None
    plan_data_obj = None
    plan_payload_obj = None
    has_plan_key = False
    plan_path = invocations_dir / f"{stem}.plan"
    if plan_path.exists() or plan_path.is_symlink():
        candidate = _load_sidecar(plan_path)
        if candidate is not None:
            try:
                _validate_plan_record(binding, candidate)
                plan = candidate["plan"]
                if binding.hook == "pre-commit":
                    _validate_index_plan_source(binding, plan, event.get("head_commit"))
                    if plan is not None:
                        plan_contract.validate(plan)
                elif binding.hook == "pre-push":
                    source_path = invocations_dir / f"{stem}.push-source"
                    push_source = (
                        _load_sidecar(source_path) if source_path.exists() else None
                    )
                    _validate_push_plan_source(binding, candidate, push_source)
                    for ref_plan in plan:
                        plan_contract.validate(ref_plan)
            except (
                hooks.HookEventError,
                plan_contract.PlanError,
                TypeError,
                KeyError,
                AttributeError,
            ) as exc:
                conflicts.append(
                    {
                        "reason": getattr(exc, "code", "hook_plan_invalid"),
                        "artifact": plan_path.name,
                    }
                )
            else:
                plan_data_obj = candidate
                plan_payload_obj = plan
                plan_ref = candidate["plan_ref"]
                has_plan_key = True
    plan_scope = (
        "push_multi_ref_aggregate"
        if isinstance(plan_payload_obj, list)
        else ("single_plan" if isinstance(plan_payload_obj, dict) else "none")
    )

    push_refs = (
        push_source["references"] if has_plan_key and binding.hook == "pre-push" else []
    )

    expected_jobs_map = None
    expected_exec_to_remote = {}
    expected_unit_artifacts = []
    if binding.hook == "pre-commit" and isinstance(plan_data_obj, dict):
        expected_jobs_map = _index_jobs(plan_data_obj)
        for job_key in sorted(expected_jobs_map):
            expected_unit_artifacts.append(f"{stem}{_index_execution_suffix(job_key)}")
    elif binding.hook == "pre-push" and isinstance(plan_payload_obj, list):
        for idx, p_item in enumerate(plan_payload_obj):
            ref_item = push_refs[idx] if idx < len(push_refs) else None
            rref = None
            raction = None
            if isinstance(ref_item, dict):
                raw_rr = ref_item.get("remote_ref")
                if isinstance(raw_rr, str) and raw_rr.strip():
                    rref = raw_rr.strip()
                raction = ref_item.get("ref_action")
            if rref is None and isinstance(p_item, dict):
                pg = p_item.get("git")
                if isinstance(pg, dict) and isinstance(pg.get("remote_ref"), str):
                    rref = pg["remote_ref"].strip() or None
                if raction is None and isinstance(pg, dict):
                    raction = pg.get("ref_action")
            needs_exec = (
                raction != "delete"
                and isinstance(p_item, dict)
                and isinstance(p_item.get("expected_checks"), dict)
                and _push_needs_execution(p_item)
            )
            art_name = f"{stem}.execution-{idx}"
            if rref is not None:
                expected_exec_to_remote[art_name] = rref
            if needs_exec:
                expected_unit_artifacts.append(art_name)

    executions = []
    raw_reports_by_artifact = {}
    seen_exec_paths = set()
    activation_cache = {}
    activation_owners = {}
    is_split_commit = bool(expected_jobs_map and set(expected_jobs_map) != {_JOB_REF})

    def _expected_activation_name(exec_name):
        prefix = f"{stem}.execution-"
        if exec_name.startswith(prefix):
            suffix_part = exec_name[len(prefix) :]
            if suffix_part.startswith("check-"):
                return f"{stem}.activation-{suffix_part}"
        return f"{stem}.activation"

    def _verify_activation(exec_name):
        act_name = _expected_activation_name(exec_name)
        if is_split_commit and act_name == f"{stem}.activation":
            conflicts.append(
                {
                    "reason": "hook_shared_activation_conflict",
                    "artifact": exec_name,
                    "expected_activation": act_name,
                }
            )
            return False
        if (
            exec_name.startswith(f"{stem}.execution-check-")
            and (invocations_dir / f"{stem}.activation").exists()
        ):
            conflicts.append(
                {
                    "reason": "hook_shared_activation_conflict",
                    "artifact": f"{stem}.activation",
                    "exec_artifact": exec_name,
                }
            )
        prev_owner = activation_owners.get(act_name)
        if prev_owner is not None and prev_owner != exec_name and is_split_commit:
            conflicts.append(
                {
                    "reason": "hook_shared_activation_conflict",
                    "artifact": act_name,
                    "affected_artifacts": sorted({prev_owner, exec_name}),
                }
            )
            return False
        activation_owners.setdefault(act_name, exec_name)
        if act_name in activation_cache:
            return activation_cache[act_name]
        act_path = invocations_dir / act_name
        if not act_path.exists():
            conflicts.append(
                {
                    "reason": "hook_execution_activation_missing",
                    "artifact": exec_name,
                    "expected_activation": act_name,
                }
            )
            activation_cache[act_name] = False
            return False
        act_data = _load_sidecar(act_path)
        if act_data is None:
            activation_cache[act_name] = False
            return False
        ok = act_data == identity
        if not ok:
            conflicts.append(
                {
                    "reason": "hook_activation_identity_mismatch",
                    "artifact": act_name,
                    "exec_artifact": exec_name,
                    "expected_run_ref": expected_run,
                    "expected_attempt_ref": expected_att,
                }
            )
        activation_cache[act_name] = ok
        return ok

    for pattern in (
        f"{stem}.execution",
        f"{stem}.execution-[0-9]*",
        f"{stem}.execution-check-*",
    ):
        for exec_path in sorted(invocations_dir.glob(pattern)):
            resolved_exec = exec_path.resolve()
            if resolved_exec in seen_exec_paths:
                continue
            seen_exec_paths.add(resolved_exec)
            exec_data = _load_sidecar(exec_path)
            if exec_data is None:
                continue
            act_ok = _verify_activation(exec_path.name)
            rej_path = invocations_dir / exec_path.name.replace(
                ".execution", ".source-rejection", 1
            )
            source_rejection_reason = None
            if rej_path.exists():
                rej_data = _load_sidecar(rej_path)
                if isinstance(rej_data, dict):
                    raw_rej = rej_data.get("reason")
                    source_rejection_reason = (
                        raw_rej
                        if isinstance(raw_rej, str) and raw_rej
                        else "source_rejected"
                    )
                    conflicts.append(
                        {
                            "reason": "hook_source_rejected_after_execution",
                            "artifact": exec_path.name,
                            "detail": source_rejection_reason,
                        }
                    )
            if (
                exec_path.name in expected_unit_artifacts
                and plan_data_obj is not None
                and act_ok
            ):
                artifact_suffix = exec_path.name.removeprefix(stem)
                try:
                    report = _validate_execution_record(
                        binding, plan_data_obj, exec_data, identity
                    )
                    if binding.hook == "pre-commit":
                        role = next(
                            job
                            for job in expected_jobs_map
                            if _index_execution_suffix(job) == artifact_suffix
                        )
                        role_plan = plan_payload_obj
                    else:
                        index = int(artifact_suffix.removeprefix(".execution-"))
                        role = _push_execution_suffix(index)
                        role_plan = plan_payload_obj[index]
                    if role_plan is not None:
                        native = (
                            report.get("execution_context") == "dagger_export"
                            or dagger_plan.required(role_plan, python_project=False)
                            or (
                                binding.hook == "pre-commit"
                                and plan_data_obj.get("dagger_module") is not None
                            )
                        )
                        if (
                            native
                            or binding.invocation_path.with_suffix(
                                artifact_suffix.replace(".execution", ".dagger", 1)
                            ).exists()
                        ):
                            bundle = _dagger_bundle(binding, artifact_suffix)
                            consumed_files.extend(
                                str(bundle / name)
                                for name in (
                                    "plan.json",
                                    "report.json",
                                    "exit-code.txt",
                                )
                            )
                            _validate_recorded_dagger_bundle(
                                binding,
                                artifact_suffix,
                                role_plan,
                                exec_data,
                                report,
                                max_bytes=max_bytes,
                            )
                            native = True
                        if not _report_matches_role(
                            role_plan, exec_data, report, role, native_dagger=native
                        ):
                            raise hooks.HookEventError("hook_execution_role_mismatch")
                        if (
                            binding.hook == "pre-commit"
                            and expected_jobs_map[role] is not None
                        ):
                            checks = report.get("checks")
                            if not isinstance(checks, list) or {
                                check.get("name")
                                for check in checks
                                if isinstance(check, dict)
                            } != set(expected_jobs_map[role]):
                                raise hooks.HookEventError(
                                    "hook_execution_job_checks_mismatch"
                                )
                except (hooks.HookEventError, ValueError, TypeError, KeyError) as exc:
                    conflicts.append(
                        {
                            "reason": getattr(
                                exc, "code", "hook_execution_record_invalid"
                            ),
                            "artifact": exec_path.name,
                        }
                    )
                    binding_status = "invalid"
                else:
                    binding_status = (
                        "verified" if role_plan is not None else "fallback_exit_only"
                    )
            else:
                binding_status = "unbound"
            if source_rejection_reason is not None:
                binding_status = "source_rejected"
            raw_epr = exec_data.get("plan_ref")
            exec_plan_ref = (
                raw_epr.strip()
                if isinstance(raw_epr, str) and raw_epr.strip()
                else None
            )
            if plan_ref is not None and exec_plan_ref != plan_ref:
                conflicts.append(
                    {
                        "reason": "hook_execution_plan_ref_mismatch",
                        "artifact": exec_path.name,
                        "expected": plan_ref,
                        "observed": exec_plan_ref,
                    }
                )
            report = exec_data.get("report")
            report_ref_val = None
            occurred = False
            rejections = {}
            report_digest_verified = False
            run_ref = None
            attempt_ref = None
            execution_ref = None
            bundle_dir = None
            git_commit = None
            source_tree_id = None
            remote_ref = expected_exec_to_remote.get(exec_path.name)
            if isinstance(report, dict):
                raw_reports_by_artifact[exec_path.name] = report
                actual_digest = hooks._digest_bytes(hooks.canonical_event_bytes(report))
                raw_dd = exec_data.get("report_digest")
                declared_digest = (
                    raw_dd.strip()
                    if isinstance(raw_dd, str) and raw_dd.strip()
                    else None
                )
                if declared_digest == actual_digest:
                    report_digest_verified = True
                else:
                    conflicts.append(
                        {
                            "reason": "hook_execution_report_digest_mismatch",
                            "artifact": exec_path.name,
                            "expected": declared_digest,
                            "observed": actual_digest,
                        }
                    )
                if report.get("kind") == execution.REPORT_KIND:
                    try:
                        results, rejections, verified_ref = (
                            reconciliation.verify_execution_report(report)
                        )
                        report_ref_val = _report_ref_canonical(verified_ref)
                        occurred = (
                            reconciliation.observed_execution_ref(
                                report, results, rejections
                            )
                            is not None
                        )
                    except (
                        reconciliation.ReconciliationError,
                        TypeError,
                        ValueError,
                    ) as exc:
                        report_digest_verified = False
                        conflicts.append(
                            {
                                "reason": "hook_execution_report_invalid",
                                "artifact": exec_path.name,
                                "error": str(exc),
                            }
                        )
                ident = report.get("execution_identity")
                if isinstance(ident, dict):
                    for ikey, dst in (
                        ("run_ref", "run_ref"),
                        ("attempt_ref", "attempt_ref"),
                        ("execution_ref", "execution_ref"),
                    ):
                        iv = ident.get(ikey)
                        norm_iv = (
                            iv.strip() if isinstance(iv, str) and iv.strip() else None
                        )
                        if dst == "run_ref":
                            run_ref = norm_iv
                        elif dst == "attempt_ref":
                            attempt_ref = norm_iv
                        else:
                            execution_ref = norm_iv
                    if run_ref is not None and run_ref != expected_run:
                        conflicts.append(
                            {
                                "reason": "hook_execution_run_ref_mismatch",
                                "artifact": exec_path.name,
                                "expected": expected_run,
                                "observed": run_ref,
                            }
                        )
                    if attempt_ref is not None and attempt_ref != expected_att:
                        conflicts.append(
                            {
                                "reason": "hook_execution_attempt_ref_mismatch",
                                "artifact": exec_path.name,
                                "expected": expected_att,
                                "observed": attempt_ref,
                            }
                        )
                report_plan = report.get("plan")
                plan_git = (
                    report_plan.get("git") if isinstance(report_plan, dict) else None
                )
                if isinstance(plan_git, dict):
                    hc = plan_git.get("head_commit")
                    git_commit = (
                        hc.strip() if isinstance(hc, str) and hc.strip() else None
                    )
                    st = plan_git.get("source_tree_id")
                    source_tree_id = (
                        st.strip() if isinstance(st, str) and st.strip() else None
                    )
                    if remote_ref is None:
                        rr = plan_git.get("remote_ref")
                        remote_ref = (
                            rr.strip() if isinstance(rr, str) and rr.strip() else None
                        )
                raw_dagger_dir = report.get("dagger_bundle")
                if isinstance(raw_dagger_dir, str) and raw_dagger_dir.strip():
                    with suppress(OSError):
                        bundle_dir = Path(raw_dagger_dir.strip()).resolve().as_posix()
            else:
                conflicts.append(
                    {
                        "reason": "hook_execution_report_missing",
                        "artifact": exec_path.name,
                    }
                )
            executions.append(
                {
                    "artifact": exec_path.name,
                    "scope": exec_path.suffix.lstrip("."),
                    "plan_ref": exec_plan_ref,
                    "plan_scope": plan_scope,
                    "remote_ref": remote_ref,
                    "run_ref": run_ref,
                    "attempt_ref": attempt_ref,
                    "execution_ref": execution_ref,
                    "report_ref": report_ref_val,
                    "report_digest_verified": bool(report_digest_verified and act_ok),
                    "source_rejection": source_rejection_reason,
                    "execution_binding_status": binding_status,
                    "execution_occurrence_status": "observed"
                    if occurred
                    else "unproven",
                    "report_contract_rejections": rejections,
                    "bundle_directory": bundle_dir,
                    "git_commit": git_commit,
                    "source_tree_id": source_tree_id,
                    "exit_code": report.get("exit_code")
                    if isinstance(report, dict)
                    else None,
                }
            )

    raw_observations = []
    for raw_path in sorted(invocations_dir.glob(f"{stem}.observation*")):
        observed = _load_sidecar(raw_path)
        if observed is None or plan_data_obj is None:
            continue
        try:
            _validate_execution_record(binding, plan_data_obj, observed, identity)
        except hooks.HookEventError as exc:
            conflicts.append({"reason": exc.code, "artifact": raw_path.name})
            continue
        raw_observations.append(
            {
                "artifact": raw_path.name,
                "status": "unsealed_observation",
                "exit_code": observed["report"].get("exit_code"),
                "report": observed["report"],
            }
        )

    loaded_arts = {
        ex["artifact"]
        for ex in executions
        if ex["execution_binding_status"] in ("verified", "fallback_exit_only")
    }
    missing_expected_executions = [
        art for art in expected_unit_artifacts if art not in loaded_arts
    ]
    receipt_path = invocations_dir / f"{stem}.receipt"
    receipt_summary = None
    if receipt_path.exists():
        receipt_data = _load_sidecar(receipt_path)
        if receipt_data is not None:
            raw_rpr = receipt_data.get("plan_ref")
            receipt_plan_ref = (
                raw_rpr.strip()
                if isinstance(raw_rpr, str) and raw_rpr.strip()
                else None
            )
            if (
                receipt_plan_ref is not None
                and plan_ref is not None
                and receipt_plan_ref != plan_ref
            ):
                conflicts.append(
                    {
                        "reason": "hook_receipt_plan_ref_mismatch",
                        "expected": plan_ref,
                        "observed": receipt_plan_ref,
                    }
                )
            receipt_report_refs = []
            validation_status = "valid"
            native_receipt_status = None

            def _scan_inv_refs(obj):
                exp_inv = obj.get("expected_invocation_ref")
                if (
                    isinstance(exp_inv, str)
                    and exp_inv.strip() != binding.invocation_ref
                ):
                    conflicts.append(
                        {
                            "reason": "hook_receipt_invocation_ref_mismatch",
                            "expected": binding.invocation_ref,
                            "observed": exp_inv.strip(),
                        }
                    )
                for sr in obj.get("receipts") or ():
                    if isinstance(sr, dict):
                        _scan_inv_refs(sr)

            def _validate_rec(rec_obj):
                try:
                    reconciliation.validate_receipt(rec_obj)
                    return True
                except reconciliation.ReconciliationError as exc:
                    conflicts.append(
                        {"reason": "hook_receipt_validation_failed", "error": str(exc)}
                    )
                    return False

            is_fallback_exit_only = (
                plan_payload_obj is None
                and has_plan_key
                and "kind" not in receipt_data
                and "receipts" not in receipt_data
                and "status" not in receipt_data
                and "exit_code" in receipt_data
            )
            if is_fallback_exit_only:
                validation_status = "fallback"
                native_receipt_status = "fallback_exit_only"
                if (
                    receipt_data.get("run_ref") != expected_run
                    or receipt_data.get("attempt_ref") != expected_att
                ):
                    conflicts.append(
                        {
                            "reason": "hook_receipt_fallback_identity_mismatch",
                            "expected_run_ref": expected_run,
                            "expected_attempt_ref": expected_att,
                        }
                    )
                if not executions:
                    conflicts.append({"reason": "hook_receipt_executions_missing"})
                elif receipt_data.get("exit_code") != executions[0].get("exit_code"):
                    conflicts.append(
                        {
                            "reason": "hook_receipt_fallback_exit_mismatch",
                            "expected": executions[0].get("exit_code"),
                            "observed": receipt_data.get("exit_code"),
                        }
                    )
            elif "receipts" in receipt_data:
                sub_receipts = receipt_data.get("receipts")
                if not isinstance(sub_receipts, list):
                    validation_status = "invalid"
                    conflicts.append({"reason": "hook_receipt_wrapper_invalid"})
                else:
                    if isinstance(plan_payload_obj, list) and len(sub_receipts) != len(
                        plan_payload_obj
                    ):
                        validation_status = "invalid"
                        conflicts.append(
                            {
                                "reason": "hook_receipt_ref_count_mismatch",
                                "expected": len(plan_payload_obj),
                                "observed": len(sub_receipts),
                            }
                        )
                    sub_statuses = []
                    for sub_rec in sub_receipts:
                        if not _validate_rec(sub_rec):
                            validation_status = "invalid"
                        if isinstance(sub_rec, dict) and isinstance(
                            sub_rec.get("status"), str
                        ):
                            sub_statuses.append(sub_rec["status"])
                    if not sub_receipts and binding.hook == "pre-push":
                        native_receipt_status = (
                            "matched" if not push_refs else "partial"
                        )
                    elif sub_statuses and all(s == "matched" for s in sub_statuses):
                        native_receipt_status = "matched"
                    elif sub_statuses and len(set(sub_statuses)) == 1:
                        native_receipt_status = sub_statuses[0]
                    else:
                        native_receipt_status = "partial"
            else:
                if not _validate_rec(receipt_data):
                    validation_status = "invalid"
                native_receipt_status = (
                    str(receipt_data.get("status"))
                    if receipt_data.get("status") is not None
                    else None
                )

            _scan_inv_refs(receipt_data)
            _scan_receipt_report_refs(receipt_data, receipt_report_refs)
            valid_exec_report_refs = {
                ex["report_ref"]
                for ex in executions
                if ex.get("report_ref") and ex.get("report_digest_verified")
            }
            job_to_exec_art = {}
            for ex in executions:
                jkey = (
                    _JOB_REF
                    if ex["scope"] == "execution"
                    else ex["scope"].removeprefix("execution-")
                )
                job_to_exec_art[jkey] = ex["artifact"]

            ref_owner_artifacts = {}

            def _collect_owners(obj, *, push_idx=None):
                top_job = (
                    obj.get("job_ref").strip()
                    if isinstance(obj.get("job_ref"), str) and obj["job_ref"].strip()
                    else None
                )
                top_rr = _report_ref_canonical(obj.get("report_ref"))
                if top_rr:
                    if push_idx is not None:
                        ref_owner_artifacts.setdefault(top_rr, []).append(
                            f"{stem}.execution-{push_idx}"
                        )
                    elif top_job and top_job in job_to_exec_art:
                        ref_owner_artifacts.setdefault(top_rr, []).append(
                            job_to_exec_art[top_job]
                        )
                for seq_key in ("entries", "results", "references"):
                    for item in obj.get(seq_key) or ():
                        if not isinstance(item, dict):
                            continue
                        raw_ijob = item.get("job_ref")
                        ijob = (
                            raw_ijob.strip()
                            if isinstance(raw_ijob, str) and raw_ijob.strip()
                            else top_job
                        )
                        irr = _report_ref_canonical(item.get("report_ref"))
                        if irr:
                            if push_idx is not None:
                                ref_owner_artifacts.setdefault(irr, []).append(
                                    f"{stem}.execution-{push_idx}"
                                )
                            elif ijob and ijob in job_to_exec_art:
                                ref_owner_artifacts.setdefault(irr, []).append(
                                    job_to_exec_art[ijob]
                                )
                        for sub_key in (
                            "evidence",
                            "job_instances",
                            "rejected_observations",
                            "observed_results",
                        ):
                            for sub_item in item.get(sub_key) or ():
                                if not isinstance(sub_item, dict):
                                    continue
                                raw_sj = sub_item.get("job_ref")
                                sjob = (
                                    raw_sj.strip()
                                    if isinstance(raw_sj, str) and raw_sj.strip()
                                    else ijob
                                )
                                srr = _report_ref_canonical(sub_item.get("report_ref"))
                                if srr:
                                    if push_idx is not None:
                                        ref_owner_artifacts.setdefault(srr, []).append(
                                            f"{stem}.execution-{push_idx}"
                                        )
                                    elif sjob and sjob in job_to_exec_art:
                                        ref_owner_artifacts.setdefault(srr, []).append(
                                            job_to_exec_art[sjob]
                                        )
                for idx_r, sub_rec in enumerate(obj.get("receipts") or ()):
                    if isinstance(sub_rec, dict):
                        _collect_owners(sub_rec, push_idx=idx_r)

            _collect_owners(receipt_data)
            missing_exec_for_receipt = False
            for rr in receipt_report_refs:
                if rr not in valid_exec_report_refs:
                    missing_exec_for_receipt = True
                    owners = sorted(set(ref_owner_artifacts.get(rr, ())))
                    conflict_rec = {
                        "reason": "hook_receipt_report_ref_not_in_executions",
                        "observed": rr,
                    }
                    if owners:
                        conflict_rec["affected_artifacts"] = owners
                    conflicts.append(conflict_rec)

            if not is_fallback_exit_only and validation_status == "valid":
                if plan_data_obj is None or not has_plan_key:
                    conflicts.append({"reason": "hook_receipt_plan_missing"})
                elif "receipts" in receipt_data:
                    sub_receipts = receipt_data.get("receipts") or []
                    reports_by_index = {}
                    exec_prefix = f"{stem}.execution-"
                    for ex in executions:
                        art = ex["artifact"]
                        if art.startswith(exec_prefix):
                            suffix_idx = art[len(exec_prefix) :]
                            if suffix_idx.isdigit():
                                rep_obj = raw_reports_by_artifact.get(art)
                                if rep_obj is not None:
                                    reports_by_index[int(suffix_idx)] = rep_obj
                            else:
                                conflicts.append(
                                    {
                                        "reason": "hook_execution_unexpected_for_plan",
                                        "artifact": art,
                                    }
                                )
                        else:
                            conflicts.append(
                                {
                                    "reason": "hook_execution_unexpected_for_plan",
                                    "artifact": art,
                                }
                            )
                    try:
                        verify_push_receipt_binding(
                            binding,
                            plan_payload_obj,
                            sub_receipts,
                            reports_by_index,
                            refs=push_refs or None,
                        )
                    except hooks.HookEventError as exc:
                        if exc.code == "hook_report_missing":
                            missing_exec_for_receipt = True
                            conflicts.append(
                                {
                                    "reason": "hook_receipt_executions_missing",
                                    "error": exc.code,
                                }
                            )
                        else:
                            conflicts.append(
                                {
                                    "reason": "hook_receipt_scope_or_report_mismatch",
                                    "error": exc.code,
                                }
                            )
                else:
                    expected_jobs = set(expected_jobs_map or _index_jobs(plan_data_obj))
                    reports_by_job = {job: None for job in expected_jobs}
                    for ex in executions:
                        job_key = (
                            _JOB_REF
                            if ex["scope"] == "execution"
                            else ex["scope"].removeprefix("execution-")
                        )
                        if job_key not in expected_jobs:
                            conflicts.append(
                                {
                                    "reason": "hook_execution_unexpected_for_plan",
                                    "artifact": ex["artifact"],
                                    "job_ref": job_key,
                                }
                            )
                        reports_by_job[job_key] = raw_reports_by_artifact.get(
                            ex["artifact"]
                        )
                    try:
                        verify_index_receipt_binding(
                            binding, plan_payload_obj, receipt_data, reports_by_job
                        )
                    except hooks.HookEventError as exc:
                        if exc.code == "hook_report_missing":
                            missing_exec_for_receipt = True
                            conflicts.append(
                                {
                                    "reason": "hook_receipt_executions_missing",
                                    "error": exc.code,
                                }
                            )
                        else:
                            conflict_item = {
                                "reason": "hook_receipt_scope_or_report_mismatch",
                                "error": exc.code,
                            }
                            if exc.code == "hook_final_report_mismatch":
                                failing_arts = []
                                for j_name, j_rep in reports_by_job.items():
                                    j_art = job_to_exec_art.get(j_name)
                                    if not j_art:
                                        continue
                                    j_ex = next(
                                        (
                                            e
                                            for e in executions
                                            if e["artifact"] == j_art
                                        ),
                                        None,
                                    )
                                    if j_ex and not j_ex.get("report_digest_verified"):
                                        failing_arts.append(j_art)
                                    elif isinstance(j_rep, dict):
                                        try:
                                            j_rref = _report_ref_canonical(
                                                reconciliation.execution_report_ref(
                                                    j_rep
                                                )
                                            )
                                        except (
                                            reconciliation.ReconciliationError,
                                            TypeError,
                                            ValueError,
                                        ):
                                            j_rref = None
                                        if j_rref and j_rref not in receipt_report_refs:
                                            failing_arts.append(j_art)
                                if failing_arts:
                                    conflict_item["affected_artifacts"] = sorted(
                                        set(failing_arts)
                                    )
                            conflicts.append(conflict_item)

            has_integrity_conflict = any(
                c.get("reason", "").startswith(
                    (
                        "hook_execution_",
                        "hook_activation_",
                        "hook_shared_activation_",
                        "hook_receipt_",
                        "hook_plan_",
                        "hook_sidecar_",
                        "hook_source_",
                    )
                )
                for c in conflicts
            )
            if missing_exec_for_receipt:
                verifiable_evidence_status = "missing_executions"
            elif validation_status == "invalid" or has_integrity_conflict:
                verifiable_evidence_status = "mismatch_or_invalid"
            elif native_receipt_status in ("matched", "fallback_exit_only"):
                verifiable_evidence_status = "verified"
            else:
                verifiable_evidence_status = "partial_or_unverified"

            receipt_summary = {
                "status": native_receipt_status,
                "native_status": native_receipt_status,
                "validation_status": validation_status,
                "verifiable_evidence_status": verifiable_evidence_status,
                "receipt_ref": receipt_data.get("receipt_ref"),
                "plan_ref": receipt_plan_ref,
                "report_refs": sorted(set(receipt_report_refs)),
                "exit_code": receipt_data.get("exit_code"),
            }
    if missing_expected_executions and executions:
        conflicts.append(
            {
                "reason": "hook_expected_execution_missing",
                "missing_artifacts": missing_expected_executions,
                "affected_artifacts": missing_expected_executions,
            }
        )

    stage_timings = []
    for sidecar in sorted(invocations_dir.glob(f"{stem}.timing-*")):
        stage_data = _load_sidecar(sidecar)
        if stage_data is None:
            continue
        if (
            any(
                stage_data.get(key) != identity[key]
                for key in ("invocation_ref", "event_payload_ref")
            )
            or stage_data.get("hook") != binding.hook
        ):
            conflicts.append(
                {"reason": "hook_timing_identity_mismatch", "artifact": sidecar.name}
            )
            continue
        raw_spr = stage_data.get("plan_ref")
        stage_plan_ref = (
            raw_spr.strip() if isinstance(raw_spr, str) and raw_spr.strip() else None
        )
        if (
            stage_plan_ref is not None
            and plan_ref is not None
            and stage_plan_ref != plan_ref
        ):
            conflicts.append(
                {
                    "reason": "hook_timing_plan_ref_mismatch",
                    "artifact": sidecar.name,
                    "expected": plan_ref,
                    "observed": stage_plan_ref,
                }
            )
        stage_timings.append(
            {
                "artifact": sidecar.name,
                "hook": stage_data.get("hook") or stage_data.get("hook_name"),
                "stage": stage_data.get("stage"),
                "job_ref": stage_data.get("job_ref"),
                "status": stage_data.get("status"),
                "elapsed_seconds": stage_data.get("elapsed_seconds"),
                "started_at_ns": stage_data.get("started_at_ns"),
                "completed_at_ns": stage_data.get("completed_at_ns"),
                "attempt_count": stage_data.get("attempt_count"),
                "duplicate_attempts": stage_data.get("duplicate_attempts"),
                "primary_record_policy": stage_data.get("primary_record_policy"),
                "per_ref": stage_data.get("per_ref"),
                "exit_code": stage_data.get("exit_code"),
                "source_rejection": stage_data.get("source_rejection"),
                "report_status": stage_data.get("report_status"),
            }
        )

    git_commits = []
    if binding.hook == "pre-commit":
        hc = event.get("head_commit")
        if isinstance(hc, str) and hc.strip():
            git_commits.append(hc.strip())
    elif binding.hook == "pre-push":
        for ref_item in event.get("refs") or ():
            if isinstance(ref_item, dict):
                after = ref_item.get("after")
                if (
                    isinstance(after, str)
                    and after.strip()
                    and after.strip() not in git_commits
                ):
                    git_commits.append(after.strip())

    source_tree_ids = []
    if isinstance(plan_payload_obj, dict):
        st = (plan_payload_obj.get("git") or {}).get("source_tree_id")
        if isinstance(st, str) and st.strip():
            source_tree_ids.append(st.strip())
    elif isinstance(plan_payload_obj, list):
        for p_item in plan_payload_obj:
            if isinstance(p_item, dict):
                st = (p_item.get("git") or {}).get("source_tree_id")
                if (
                    isinstance(st, str)
                    and st.strip()
                    and st.strip() not in source_tree_ids
                ):
                    source_tree_ids.append(st.strip())

    return {
        "plan_ref": plan_ref,
        "plan_scope": plan_scope,
        "expected_unit_count": len(expected_unit_artifacts) if has_plan_key else None,
        "observed_unit_count": len(executions),
        "raw_observations": raw_observations,
        "expected_execution_artifacts": expected_unit_artifacts,
        "missing_expected_executions": missing_expected_executions,
        "git_commits": git_commits,
        "source_tree_ids": source_tree_ids,
        "push_references": push_refs,
        "executions": executions,
        "receipt": receipt_summary,
        "stage_timings": stage_timings,
        "internal_conflicts": conflicts,
        "consumed_files": consumed_files,
    }


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
    entry_mono = time.perf_counter()
    entry_ns = time.time_ns()
    prepared, refs = _push_plans(root, binding)
    _artifact(binding, ".activation", _identity(binding), exclusive=True)
    with _stage_timing(
        binding,
        "run",
        job_ref=_PUSH_JOB_REF,
        suffix=".timing-run",
        started_mono=entry_mono,
        started_ns=entry_ns,
        exclusive_activation=True,
    ) as timing:
        reports = []
        per_ref = []
        timing["extra"].update(plan_ref=prepared["plan_ref"], per_ref=per_ref)
        for index, (plan, source) in enumerate(
            zip(prepared["plan"], refs, strict=True)
        ):
            ref_started = time.perf_counter()
            report = None
            errored = False
            try:
                if source["ref_action"] != "delete" and _push_needs_execution(plan):
                    artifact_suffix = f".execution-{index}"
                    report = _run_push_plan(
                        root,
                        plan,
                        source,
                        {
                            **prepared,
                            "dagger_bundle": str(
                                _dagger_bundle(binding, artifact_suffix)
                            ),
                        },
                        _push_execution_suffix(index),
                        "execute",
                    )
                    try:
                        _record_execution(
                            binding, prepared, report, suffix=artifact_suffix
                        )
                    except BaseException:
                        _record_unsealed_observation(
                            binding, prepared, report, artifact_suffix
                        )
                        raise
                reports.append(report)
            except BaseException:
                errored = True
                raise
            finally:
                per_ref.append(
                    {
                        "remote_ref": source["remote_ref"],
                        "ref_action": source["ref_action"],
                        "elapsed_seconds": time.perf_counter() - ref_started,
                        "exit_code": report.get("exit_code")
                        if report is not None
                        else (None if errored else 0),
                        "skipped_execution": False if errored else (report is None),
                        **({"status": "errored"} if errored else {}),
                        **(
                            {"report_status": "seal_failed"}
                            if errored and report is not None
                            else {}
                        ),
                    }
                )
        exit_code = next((r["exit_code"] for r in reports if r and r["exit_code"]), 0)
        timing["extra"].update(
            plan_ref=prepared["plan_ref"],
            exit_code=exit_code,
            per_ref=per_ref,
        )
        return {
            "references": [
                {"remote_ref": ref["remote_ref"], "report": report}
                for ref, report in zip(refs, reports, strict=True)
            ],
            "exit_code": exit_code,
        }


def reconcile_push(root, binding):
    with _stage_timing(binding, "reconcile", job_ref="reconcile-push") as timing:
        prepared, refs = _push_plans(root, binding)
        reports = []
        receipts = []
        for index, (plan, source) in enumerate(
            zip(prepared["plan"], refs, strict=True)
        ):
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
            {
                **_identity(binding),
                "plan_ref": prepared["plan_ref"],
                "receipts": receipts,
            },
        )
        exit_code = next(
            (r["exit_code"] for r in reports if r and r["exit_code"]),
            reconciliation.hook_exit_code(receipts) if refs else 0,
        )
        timing["extra"].update(plan_ref=prepared["plan_ref"], exit_code=exit_code)
        return {
            "references": [
                {"remote_ref": ref["remote_ref"], "receipt": receipt}
                for ref, receipt in zip(refs, receipts, strict=True)
            ],
            "exit_code": exit_code,
        }


def consume_push_result(root, binding):
    """Require final reconciliation for every Ref in the original push order."""
    with _stage_timing(binding, "consume", job_ref="consume-push") as timing:
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
        reports_by_index = {}
        for index, (plan, source) in enumerate(
            zip(prepared["plan"], refs, strict=True)
        ):
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
            reports_by_index[index] = report
            if native_plan is not None and (
                report is None or not dagger_plan.source_bound(plan, report)
            ):
                raise hooks.HookEventError("hook_dagger_source_unverified")
            native_exit = native_exit or (report or {}).get("exit_code", 0)
        verify_push_receipt_binding(
            binding,
            prepared["plan"],
            receipts,
            reports_by_index,
            refs=refs,
        )
        exit_code = native_exit or (
            reconciliation.hook_exit_code(receipts) if refs else 0
        )
        timing["extra"].update(plan_ref=prepared["plan_ref"], exit_code=exit_code)
        return {"exit_code": exit_code}


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
