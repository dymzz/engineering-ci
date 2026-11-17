"""#66 Hook Event Coordinator: Invocation → Collect → Final Reconciliation.

This module owns one Hook Invocation's outer control path. It:

- loads and verifies a #102 ``invocation_ref → event_payload_ref → Event``;
- binds Expected Event/Ref/Baseline identity to that Invocation Event;
- allocates this Invocation's ``run_ref`` / ``attempt_ref``;
- collects Execution Reports only when they attribute to this Job/Attempt;
- issues Job Receipts only for collected Reports;
- calls #104 ``reconcile_expected_results()`` per Expected scope after
  dispatch returns (including filtered/failed Jobs);
- maps Receipt statuses to a Git Hook exit code that requires a closed
  Expected enumeration (``receipt.status=matched``).

It does not install Hook files, re-derive Expected projections, admit
trusted evidence (#63), or decide Required/Release (#61). Lefthook Job
scheduling remains outside this module; callers supply activated Job facts.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from engineering_ci import hooks, reconciliation

DISPATCH_KIND = "engineering-ci-hook-dispatch"
DISPATCH_VERSION = 1
COORDINATOR_ISSUER_NAME = "engineering-ci.hook.coordinator"
COORDINATOR_ISSUER_VERSION = "1"
DEFAULT_RUNNER_REF = "execution:host"
DEFAULT_SCHEDULER_REF = {"name": "lefthook", "version": "2.2.2"}
DEFAULT_CONFIG_REF = {"name": "hook-job", "version": "1"}
DEFAULT_QUALIFICATION_NAME = "engineering-ci.hook.attempt-qualification"
_CLOSED_CONSUMER_COMPLETENESS = frozenset(
    {"expected_domain_complete", "domain_complete_empty"}
)

HOOK_EXIT_OK = 0
HOOK_EXIT_RECONCILED_FAILURE = 1
HOOK_EXIT_COORDINATOR_ERROR = 2


class HookCoordinatorError(ValueError):
    """The outer Hook control path cannot complete without loss."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _digest(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _hex_digest(value):
    return _digest(value).removeprefix("sha256:")


def coordinator_issuer_ref():
    """Stable issuer identity for Job Receipts produced by this Coordinator."""
    return {
        "name": COORDINATOR_ISSUER_NAME,
        "version": COORDINATOR_ISSUER_VERSION,
        "sha256": _hex_digest(
            {
                "name": COORDINATOR_ISSUER_NAME,
                "version": COORDINATOR_ISSUER_VERSION,
                "kind": DISPATCH_KIND,
                "schema_version": DISPATCH_VERSION,
            }
        ),
    }


def attempt_qualification_ref(*, run_ref, attempt_ref, invocation_ref):
    """Evidence pin for a Scheduler-activated Attempt on this Invocation."""
    payload = {
        "kind": "engineering-ci-hook-attempt-qualification",
        "schema_version": 1,
        "run_ref": run_ref,
        "attempt_ref": attempt_ref,
        "invocation_ref": invocation_ref,
    }
    return {
        "name": DEFAULT_QUALIFICATION_NAME,
        "version": "1",
        "sha256": _hex_digest(payload),
    }


@dataclass(frozen=True)
class InvocationBinding:
    """Verified #102 Invocation plus its content-addressed Event."""

    invocation_ref: str
    event_payload_ref: str
    event: dict
    hook: str
    invocation_path: Path
    event_path: Path


@dataclass
class RunBinding:
    """One Invocation's Run/Attempt identity and activated Job map."""

    invocation_ref: str
    run_ref: str
    attempt_ref: str
    runner_ref: str = DEFAULT_RUNNER_REF
    jobs: dict = field(default_factory=dict)
    collected_reports: dict = field(default_factory=dict)

    def record_job(
        self,
        job_ref,
        *,
        eligibility="eligible",
        mapping_status="routed",
        report_ref=None,
        reason=None,
    ):
        if not isinstance(job_ref, str) or not job_ref:
            raise HookCoordinatorError("job_ref_invalid")
        if eligibility not in reconciliation.ATTEMPT_ELIGIBILITY_STATUSES:
            raise HookCoordinatorError("attempt_eligibility_invalid")
        if mapping_status not in reconciliation.ROUTING_STATUSES:
            raise HookCoordinatorError("mapping_status_invalid")
        entry = {
            "job_ref": job_ref,
            "run_ref": self.run_ref,
            "attempt_ref": self.attempt_ref,
            "runner_ref": self.runner_ref,
            "attempt_eligibility": eligibility,
            "mapping_status": mapping_status,
            "invocation_ref": self.invocation_ref,
        }
        if eligibility != "unknown":
            entry["qualification_ref"] = attempt_qualification_ref(
                run_ref=self.run_ref,
                attempt_ref=self.attempt_ref,
                invocation_ref=self.invocation_ref,
            )
        if report_ref is not None:
            entry["report_ref"] = copy.deepcopy(report_ref)
        if reason is not None:
            entry["reason"] = reason
        self.jobs[job_ref] = entry
        return entry


def load_invocation_binding(path):
    """Load one Invocation file and verify its Event content/reference linkage.

    Returns the Invocation identity together with the verified Event. Same
    ``event_payload_ref`` content may appear under many ``invocation_ref``
    values; callers must never treat the payload digest as an execution id.
    """
    path = Path(path)
    raw = path.read_bytes()
    try:
        invocation = json.loads(raw)
    except ValueError:
        raise HookCoordinatorError("invocation_payload_invalid") from None
    if (
        not isinstance(invocation, dict)
        or hooks.canonical_event_bytes(invocation) != raw
    ):
        raise HookCoordinatorError("invocation_payload_invalid")
    if (
        invocation.get("kind") != hooks.INVOCATION_KIND
        or invocation.get("schema_version") != hooks.INVOCATION_SCHEMA_VERSION
    ):
        raise HookCoordinatorError("invocation_payload_invalid")
    expected_ref = f"invocation:{path.stem}"
    if invocation.get("invocation_ref") != expected_ref:
        raise HookCoordinatorError("invocation_ref_mismatch")
    try:
        event = hooks.load_invocation(path)
    except hooks.HookEventError as error:
        raise HookCoordinatorError(error.code) from error
    event_payload_ref = invocation.get("event_payload_ref")
    if event_payload_ref != hooks.event_payload_ref(event):
        raise HookCoordinatorError("invocation_event_mismatch")
    git_dir = hooks._invocation_git_dir(path)
    event_path = hooks._controlled_event_path(git_dir, invocation.get("event_file"))
    return InvocationBinding(
        invocation_ref=expected_ref,
        event_payload_ref=event_payload_ref,
        event=event,
        hook=invocation.get("hook"),
        invocation_path=path.resolve(),
        event_path=event_path,
    )


def open_attempt(
    binding, *, run_id=None, attempt_id=None, runner_ref=DEFAULT_RUNNER_REF
):
    """Allocate this Invocation's Run/Attempt identity."""
    if not isinstance(binding, InvocationBinding):
        raise HookCoordinatorError("invocation_binding_invalid")
    run_token = run_id if run_id is not None else uuid.uuid4().hex
    attempt_token = attempt_id if attempt_id is not None else "1"
    if not isinstance(run_token, str) or not run_token:
        raise HookCoordinatorError("run_ref_invalid")
    if not isinstance(attempt_token, str) or not attempt_token:
        raise HookCoordinatorError("attempt_ref_invalid")
    if not isinstance(runner_ref, str) or not runner_ref:
        raise HookCoordinatorError("runner_ref_invalid")
    return RunBinding(
        invocation_ref=binding.invocation_ref,
        run_ref=f"run/{run_token}",
        attempt_ref=f"attempt/{attempt_token}",
        runner_ref=runner_ref,
    )


def _invocation_baselines(event):
    """Return comparable (before, after, local_ref, remote_ref) tuples."""
    if not isinstance(event, dict):
        raise HookCoordinatorError("invocation_event_invalid")
    hook = event.get("hook")
    if hook == "pre-push":
        refs = event.get("refs")
        if not isinstance(refs, list) or not refs:
            raise HookCoordinatorError("invocation_refs_missing")
        baselines = []
        for ref in refs:
            if not isinstance(ref, dict):
                raise HookCoordinatorError("invocation_refs_invalid")
            baselines.append(
                (
                    ref.get("before"),
                    ref.get("after"),
                    ref.get("local_ref"),
                    ref.get("remote_ref"),
                )
            )
        return hook, baselines
    if hook == "pre-commit":
        index = event.get("index")
        if not isinstance(index, dict) or not index.get("tree"):
            raise HookCoordinatorError("invocation_index_missing")
        return hook, [(None, index.get("tree"), None, None)]
    raise HookCoordinatorError("invocation_hook_unsupported")


def _event_ref_baselines(event_ref):
    """Extract Expected Event baseline pairs from one scope event_ref."""
    if not isinstance(event_ref, dict):
        raise HookCoordinatorError("expected_event_ref_invalid")
    references = event_ref.get("references") or []
    if references:
        if not isinstance(references, list):
            raise HookCoordinatorError("expected_event_ref_invalid")
        baselines = []
        for ref in references:
            if not isinstance(ref, dict):
                raise HookCoordinatorError("expected_event_ref_invalid")
            baselines.append(
                (
                    ref.get("before") or event_ref.get("base_commit"),
                    ref.get("after") or event_ref.get("head_commit"),
                    ref.get("local_ref"),
                    ref.get("remote_ref") or ref.get("source_ref"),
                )
            )
        return baselines
    return [
        (
            event_ref.get("base_commit"),
            event_ref.get("head_commit"),
            event_ref.get("head_ref"),
            event_ref.get("base_ref"),
        )
    ]


def _baseline_matches(expected_baseline, invocation_baselines, *, hook):
    """Match on OID/Tree identity; ref names are optional corroboration."""
    before, after, local_ref, remote_ref = expected_baseline
    for inv_before, inv_after, inv_local, inv_remote in invocation_baselines:
        if hook == "pre-commit":
            if after and after == inv_after:
                return True
            continue
        if before != inv_before or after != inv_after:
            continue
        named = {value for value in (local_ref, remote_ref) if value}
        invoked = {value for value in (inv_local, inv_remote) if value}
        if named and invoked and named.isdisjoint(invoked):
            continue
        return True
    return False


def verify_expected_matches_invocation(binding, expected):
    """Refuse Expected projections that do not bind this Invocation Event.

    Compares Expected ``event_ref`` / per-Ref baselines with the verified Hook
    Event refs (pre-push old→new) or Index Tree (pre-commit). Does not rebuild
    Selection or Expected production.
    """
    if not isinstance(binding, InvocationBinding):
        raise HookCoordinatorError("invocation_binding_invalid")
    hook, invocation_baselines = _invocation_baselines(binding.event)
    for _scope_id, scope in _iter_scopes(expected):
        event_ref = scope.get("event_ref")
        if event_ref is None and scope is not expected:
            raise HookCoordinatorError("expected_event_ref_missing")
        if event_ref is None:
            event_ref = expected.get("event_ref")
        if not isinstance(event_ref, dict):
            raise HookCoordinatorError("expected_event_ref_missing")
        for baseline in _event_ref_baselines(event_ref):
            if not _baseline_matches(baseline, invocation_baselines, hook=hook):
                raise HookCoordinatorError("expected_event_mismatch")
        if hook == "pre-push" and scope.get("ref"):
            ref_meta = scope["ref"]
            if isinstance(ref_meta, dict):
                target = (
                    ref_meta.get("remote_ref")
                    or ref_meta.get("local_ref")
                    or ref_meta.get("source_ref")
                )
                before = ref_meta.get("before")
                after = ref_meta.get("after")
                if (
                    before is not None
                    and after is not None
                    and not _baseline_matches(
                        (before, after, ref_meta.get("local_ref"), target),
                        invocation_baselines,
                        hook=hook,
                    )
                ):
                    raise HookCoordinatorError("expected_ref_mismatch")
    return True


def report_ref_for(report):
    """Compute the content identity of one Execution Report."""
    if not isinstance(report, dict):
        raise HookCoordinatorError("execution_report_invalid")
    if report.get("kind") != reconciliation.EXECUTION_REPORT_KIND:
        raise HookCoordinatorError("execution_report_invalid")
    schema_version = report.get("schema_version")
    if type(schema_version) is not int or schema_version < 1:
        raise HookCoordinatorError("execution_report_invalid")
    digest = _digest(report).removeprefix("sha256:")
    return {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": schema_version,
        "sha256": digest,
    }


def verify_execution_report(report, report_ref=None):
    """Verify Report digest and extract Native Results via #104 rules."""
    if report_ref is None:
        report_ref = report_ref_for(report)
    else:
        expected = report_ref_for(report)
        if report_ref != expected:
            raise HookCoordinatorError("execution_report_digest_mismatch")
    try:
        results, rejections = reconciliation._execution_report(report, report_ref)
    except reconciliation.ReconciliationError as error:
        raise HookCoordinatorError(error.code) from error
    return results, rejections, report_ref


def _report_execution_attribution(report):
    return {
        "invocation_ref": report.get("invocation_ref"),
        "run_ref": report.get("run_ref"),
        "attempt_ref": report.get("attempt_ref"),
        "job_ref": report.get("job_ref"),
    }


def attribute_report(run_binding, job_ref, report):
    """Stamp Report bytes with this Invocation/Job/Attempt execution identity."""
    if not isinstance(run_binding, RunBinding):
        raise HookCoordinatorError("run_binding_invalid")
    if job_ref not in run_binding.jobs:
        raise HookCoordinatorError("job_not_activated")
    if not isinstance(report, dict):
        raise HookCoordinatorError("execution_report_invalid")
    stamped = copy.deepcopy(report)
    stamped["invocation_ref"] = run_binding.invocation_ref
    stamped["run_ref"] = run_binding.run_ref
    stamped["attempt_ref"] = run_binding.attempt_ref
    stamped["job_ref"] = job_ref
    return stamped


def collect_job_report(run_binding, job_ref, report):
    """Register a Report as produced by this Job/Attempt on this Invocation.

    Content digest integrity is necessary but not sufficient: the Report must
    attribute to this ``invocation_ref`` / ``run_ref`` / ``attempt_ref`` /
    ``job_ref``. A historical Report from another Attempt cannot be collected
    here without failing attribution, so it cannot receive a new
    ``upstream_verified`` Receipt.
    """
    if not isinstance(run_binding, RunBinding):
        raise HookCoordinatorError("run_binding_invalid")
    if job_ref not in run_binding.jobs:
        raise HookCoordinatorError("job_not_activated")
    job = run_binding.jobs[job_ref]
    if job.get("attempt_eligibility") != "eligible":
        raise HookCoordinatorError("attempt_not_eligible")
    attribution = _report_execution_attribution(report)
    expected = {
        "invocation_ref": run_binding.invocation_ref,
        "run_ref": run_binding.run_ref,
        "attempt_ref": run_binding.attempt_ref,
        "job_ref": job_ref,
    }
    if any(attribution.get(key) is None for key in expected):
        raise HookCoordinatorError("execution_report_attribution_missing")
    if attribution != expected:
        raise HookCoordinatorError("execution_report_attribution_mismatch")
    results, rejections, report_ref = verify_execution_report(report)
    run_binding.collected_reports[job_ref] = copy.deepcopy(report_ref)
    job["report_ref"] = copy.deepcopy(report_ref)
    return results, rejections, report_ref


def issue_job_receipt(
    run_binding,
    job_ref,
    report_ref,
    *,
    event_ref,
    scope_id,
    expected_set_ref,
    report=None,
):
    """Issue a Job Receipt only for a Report collected on this Attempt."""
    if not isinstance(run_binding, RunBinding):
        raise HookCoordinatorError("run_binding_invalid")
    if job_ref not in run_binding.jobs:
        raise HookCoordinatorError("job_not_activated")
    job = run_binding.jobs[job_ref]
    if job.get("attempt_eligibility") != "eligible":
        raise HookCoordinatorError("attempt_not_eligible")
    if report is None:
        raise HookCoordinatorError("execution_report_required")
    collected = run_binding.collected_reports.get(job_ref)
    if collected is None:
        raise HookCoordinatorError("execution_report_not_collected")
    if collected != report_ref:
        raise HookCoordinatorError("execution_report_not_collected")
    _results, _rejections, verified_ref = verify_execution_report(report, report_ref)
    if verified_ref != report_ref:
        raise HookCoordinatorError("execution_report_digest_mismatch")
    attribution = _report_execution_attribution(report)
    expected_attr = {
        "invocation_ref": run_binding.invocation_ref,
        "run_ref": run_binding.run_ref,
        "attempt_ref": run_binding.attempt_ref,
        "job_ref": job_ref,
    }
    if attribution != expected_attr:
        raise HookCoordinatorError("execution_report_attribution_mismatch")
    if not isinstance(event_ref, dict):
        raise HookCoordinatorError("event_ref_invalid")
    if not isinstance(scope_id, str) or not scope_id.startswith("sha256:"):
        raise HookCoordinatorError("scope_id_invalid")
    if not isinstance(expected_set_ref, str) or not expected_set_ref.startswith(
        "sha256:"
    ):
        raise HookCoordinatorError("expected_set_ref_invalid")
    receipt = {
        "kind": reconciliation.JOB_RECEIPT_KIND,
        "schema_version": reconciliation.JOB_RECEIPT_SCHEMA_VERSION,
        "job_ref": job_ref,
        "run_ref": run_binding.run_ref,
        "attempt_ref": run_binding.attempt_ref,
        "runner_ref": run_binding.runner_ref,
        "report_ref": copy.deepcopy(report_ref),
        "issuer_ref": coordinator_issuer_ref(),
        "verification_status": "upstream_verified",
        "invocation_ref": run_binding.invocation_ref,
        "event_ref": copy.deepcopy(event_ref),
        "scope_id": scope_id,
        "expected_set_ref": expected_set_ref,
    }
    try:
        reconciliation._job_receipt(
            receipt,
            job_ref=job_ref,
            run_ref=run_binding.run_ref,
            attempt_ref=run_binding.attempt_ref,
            runner_ref=run_binding.runner_ref,
            report_ref=report_ref,
        )
    except reconciliation.ReconciliationError as error:
        raise HookCoordinatorError(error.code) from error
    job["report_ref"] = copy.deepcopy(report_ref)
    return receipt


def build_observation(
    run_binding,
    job_ref,
    report,
    *,
    job_receipt=None,
    native_results=None,
):
    """Build one #104 Observation from a verified Report and optional Receipt."""
    results, _rejections, report_ref = verify_execution_report(report)
    if native_results is None:
        native_results = results
    observation = {
        "job_ref": job_ref,
        "run_ref": run_binding.run_ref,
        "attempt_ref": run_binding.attempt_ref,
        "runner_ref": run_binding.runner_ref,
        "report": report,
        "report_ref": copy.deepcopy(report_ref),
        "native_results": list(native_results),
    }
    if job_receipt is not None:
        observation["job_receipt"] = copy.deepcopy(job_receipt)
    return observation


def build_routing(
    expected_check_ref,
    run_binding,
    job_refs,
    *,
    mapping_status="routed",
    reason=None,
    scheduler_ref=None,
    effective_config_ref=None,
):
    """Build one #104 Routing entry from activated Jobs on this Run."""
    if not isinstance(expected_check_ref, str) or not expected_check_ref:
        raise HookCoordinatorError("expected_check_ref_invalid")
    if mapping_status not in reconciliation.ROUTING_STATUSES:
        raise HookCoordinatorError("mapping_status_invalid")
    instances = []
    if mapping_status == "routed":
        for job_ref in job_refs:
            if job_ref not in run_binding.jobs:
                raise HookCoordinatorError("job_not_activated")
            job = run_binding.jobs[job_ref]
            instance = {
                "job_ref": job["job_ref"],
                "run_ref": job["run_ref"],
                "attempt_ref": job["attempt_ref"],
                "runner_ref": job["runner_ref"],
                "attempt_eligibility": job["attempt_eligibility"],
                "invocation_ref": run_binding.invocation_ref,
            }
            if job.get("qualification_ref") is not None:
                instance["qualification_ref"] = copy.deepcopy(job["qualification_ref"])
            if job.get("report_ref") is not None:
                instance["report_ref"] = copy.deepcopy(job["report_ref"])
            instances.append(instance)
    return {
        "expected_check_ref": expected_check_ref,
        "job_refs": list(job_refs),
        "job_instances": instances,
        "mapping_status": mapping_status,
        "scheduler_ref": copy.deepcopy(scheduler_ref or DEFAULT_SCHEDULER_REF),
        "effective_config_ref": copy.deepcopy(
            effective_config_ref or DEFAULT_CONFIG_REF
        ),
        "reason": reason,
    }


def _iter_scopes(expected):
    """Yield ``(ref_scope_id_or_None, scope)`` for Final Reconciliation."""
    if not isinstance(expected, dict):
        raise HookCoordinatorError("expected_invalid")
    if "per_ref" in expected:
        per_ref = expected.get("per_ref")
        if not isinstance(per_ref, list):
            raise HookCoordinatorError("expected_ref_scopes_invalid")
        if not per_ref:
            raise HookCoordinatorError("expected_ref_scopes_empty")
        for item in per_ref:
            if not isinstance(item, dict) or not item.get("scope_id"):
                raise HookCoordinatorError("expected_ref_scope_invalid")
            yield item["scope_id"], item
        return
    yield None, expected


_BLOCKING_ENTRY_STATUSES = frozenset({"missing", "unknown", "unrouted", "insufficient"})


def hook_exit_code(receipts):
    """Map Final Reconciliation Receipts to a Git Hook process exit code.

    Exit 0 requires every scope Receipt ``status=matched`` (Expected
    enumeration closed and every eligible entry matched/not_applicable) and
    ``all_observations_reconciled``. A structurally matched but ``partial``
    Expected is not enough to continue the Hook; #61/#63 remain unevaluated.
    """
    if not isinstance(receipts, list) or not receipts:
        return HOOK_EXIT_RECONCILED_FAILURE
    for receipt in receipts:
        if not isinstance(receipt, dict):
            return HOOK_EXIT_RECONCILED_FAILURE
        if receipt.get("all_observations_reconciled") is not True:
            return HOOK_EXIT_RECONCILED_FAILURE
        if receipt.get("status") != "matched":
            return HOOK_EXIT_RECONCILED_FAILURE
        completeness = receipt.get("expected_completeness")
        if (
            completeness is not None
            and completeness not in _CLOSED_CONSUMER_COMPLETENESS
            and receipt.get("expected_status") != "complete"
        ):
            return HOOK_EXIT_RECONCILED_FAILURE
        for entry in receipt.get("entries") or ():
            status = entry.get("binding_status")
            if status in _BLOCKING_ENTRY_STATUSES:
                return HOOK_EXIT_RECONCILED_FAILURE
            if status not in {"matched", "not_applicable"}:
                return HOOK_EXIT_RECONCILED_FAILURE
    return HOOK_EXIT_OK


def finalize_invocation(
    expected,
    *,
    binding,
    routing=(),
    resolutions=(),
    observations=(),
    routing_by_scope=None,
    resolutions_by_scope=None,
    observations_by_scope=None,
):
    """Run Final Reconciliation for one Invocation after Dispatch returns.

    Always executes, including when Jobs failed or were filtered. Requires the
    Expected Event/Ref baselines to match ``binding.event``. Multi-ref Expected
    containers are reconciled one ``ref_scope_id`` at a time; a single Scope
    Expected is called with ``ref_scope_id=None``.
    """
    if not isinstance(binding, InvocationBinding):
        raise HookCoordinatorError("invocation_binding_invalid")
    verify_expected_matches_invocation(binding, expected)
    invocation_ref = binding.invocation_ref
    routing_by_scope = routing_by_scope or {}
    resolutions_by_scope = resolutions_by_scope or {}
    observations_by_scope = observations_by_scope or {}
    scopes = list(_iter_scopes(expected))
    multi_ref = any(scope_id is not None for scope_id, _scope in scopes)
    if (
        multi_ref
        and not (routing_by_scope or resolutions_by_scope or observations_by_scope)
        and (routing or resolutions or observations)
    ):
        raise HookCoordinatorError("scoped_inputs_required")
    receipts = []
    for ref_scope_id, _scope in scopes:
        key = ref_scope_id
        if multi_ref:
            if key not in routing_by_scope and routing:
                raise HookCoordinatorError("scoped_routing_missing")
            scope_routing = routing_by_scope.get(key, ())
            scope_resolutions = resolutions_by_scope.get(key, ())
            scope_observations = observations_by_scope.get(key, ())
        else:
            scope_routing = (
                routing_by_scope.get(key, routing) if routing_by_scope else routing
            )
            scope_resolutions = (
                resolutions_by_scope.get(key, resolutions)
                if resolutions_by_scope
                else resolutions
            )
            scope_observations = (
                observations_by_scope.get(key, observations)
                if observations_by_scope
                else observations
            )
        try:
            receipt = reconciliation.reconcile_expected_results(
                expected,
                ref_scope_id=ref_scope_id,
                routing=list(scope_routing),
                resolutions=list(scope_resolutions),
                observations=list(scope_observations),
                invocation_ref=invocation_ref,
            )
            reconciliation.validate_receipt(receipt)
        except reconciliation.ReconciliationError as error:
            raise HookCoordinatorError(error.code) from error
        receipts.append(receipt)
    exit_code = hook_exit_code(receipts)
    return {
        "kind": DISPATCH_KIND,
        "schema_version": DISPATCH_VERSION,
        "invocation_ref": invocation_ref,
        "receipts": receipts,
        "exit_code": exit_code,
        "trusted": False,
        "required": False,
    }


def _load_json(path, code):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HookCoordinatorError(code) from None


def _is_scope_map(value):
    return (
        isinstance(value, dict)
        and value
        and all(isinstance(key, str) and key.startswith("sha256:") for key in value)
    )


def _load_flat_or_scoped(paths, code):
    flat = []
    by_scope = {}
    for path in paths:
        value = _load_json(path, code)
        if _is_scope_map(value):
            for scope_id, items in value.items():
                if isinstance(items, list):
                    by_scope.setdefault(scope_id, []).extend(items)
                else:
                    by_scope.setdefault(scope_id, []).append(items)
            continue
        if isinstance(value, list):
            flat.extend(value)
        else:
            flat.append(value)
    return flat, by_scope


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Hook Event Coordinator: verify Invocation and Final Reconciliation"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify = subparsers.add_parser(
        "verify-invocation",
        help="Load and verify one #102 Invocation + Event binding",
    )
    verify.add_argument("--invocation-path", required=True)
    verify.add_argument("--json", action="store_true")

    finalize = subparsers.add_parser(
        "finalize",
        help="Final Reconciliation for one Invocation after Dispatch returns",
    )
    finalize.add_argument("--invocation-path", required=True)
    finalize.add_argument("--expected", required=True, help="Expected projection JSON")
    finalize.add_argument(
        "--routing",
        action="append",
        default=[],
        help="Routing JSON (flat list, or scope_id→list map for multi-ref)",
    )
    finalize.add_argument(
        "--resolutions",
        action="append",
        default=[],
        help="Resolutions JSON (flat list, or scope_id→list map for multi-ref)",
    )
    finalize.add_argument(
        "--observations",
        action="append",
        default=[],
        help="Observations JSON (flat list, or scope_id→list map for multi-ref)",
    )
    finalize.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "verify-invocation":
            binding = load_invocation_binding(args.invocation_path)
            payload = {
                "invocation_ref": binding.invocation_ref,
                "event_payload_ref": binding.event_payload_ref,
                "hook": binding.hook,
                "event_path": str(binding.event_path),
                "invocation_path": str(binding.invocation_path),
                "event": binding.event,
            }
            if args.json:
                print(json.dumps(payload, sort_keys=True))
            else:
                print(binding.invocation_ref)
            return HOOK_EXIT_OK

        binding = load_invocation_binding(args.invocation_path)
        expected = _load_json(args.expected, "expected_payload_invalid")
        routing, routing_by_scope = _load_flat_or_scoped(
            args.routing, "routing_payload_invalid"
        )
        resolutions, resolutions_by_scope = _load_flat_or_scoped(
            args.resolutions, "resolutions_payload_invalid"
        )
        observations, observations_by_scope = _load_flat_or_scoped(
            args.observations, "observations_payload_invalid"
        )
        result = finalize_invocation(
            expected,
            binding=binding,
            routing=routing,
            resolutions=resolutions,
            observations=observations,
            routing_by_scope=routing_by_scope or None,
            resolutions_by_scope=resolutions_by_scope or None,
            observations_by_scope=observations_by_scope or None,
        )
    except HookCoordinatorError as error:
        print(error.code, file=sys.stderr)
        return HOOK_EXIT_COORDINATOR_ERROR

    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(result["exit_code"])
    return result["exit_code"]
