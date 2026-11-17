"""#66 Hook Event Coordinator: Invocation → Collect → Final Reconciliation.

This module owns one Hook Invocation's outer control path. It:

- loads and verifies a #102 ``invocation_ref → event_payload_ref → Event``;
- allocates this Invocation's ``run_ref`` / ``attempt_ref``;
- verifies Execution Reports and issues Job Receipts only after Report
  digest + Result membership checks succeed;
- calls #104 ``reconcile_expected_results()`` per Expected scope after
  dispatch returns (including filtered/failed Jobs);
- maps Receipt statuses to a Git Hook exit code.

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

# Hook exit: 0 only when every scope Receipt is matched and fully reconciled.
# Non-zero covers missing/unknown/unrouted/partial, plus Coordinator failures.
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


def open_attempt(binding, *, run_id=None, attempt_id=None, runner_ref=DEFAULT_RUNNER_REF):
    """Allocate this Invocation's Run/Attempt identity.

    Each call creates a fresh Attempt for the given Invocation. Replaying a
    previous Attempt's Job Receipt against a later Invocation is refused by
    #104 attribution checks when ``invocation_ref`` is bound.
    """
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
    """Verify Report digest and extract Native Results via #104 rules.

    Returns ``(native_results, rejections, report_ref)``. A missing or
    malformed Report never yields ``upstream_verified``.
    """
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
    """Issue a Job Receipt only after Report identity is verified.

    ``verification_status=upstream_verified`` requires a successful
    ``verify_execution_report`` for the same ``report_ref``. Job exit=0 or a
    self-reported success string never authorizes this status.
    """
    if not isinstance(run_binding, RunBinding):
        raise HookCoordinatorError("run_binding_invalid")
    if job_ref not in run_binding.jobs:
        raise HookCoordinatorError("job_not_activated")
    job = run_binding.jobs[job_ref]
    if job.get("attempt_eligibility") != "eligible":
        raise HookCoordinatorError("attempt_not_eligible")
    if report is None:
        raise HookCoordinatorError("execution_report_required")
    _results, _rejections, verified_ref = verify_execution_report(report, report_ref)
    if verified_ref != report_ref:
        raise HookCoordinatorError("execution_report_digest_mismatch")
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
    # Re-validate through #104's private shape guard so Collect never drifts.
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
    per_ref = expected.get("per_ref")
    if isinstance(per_ref, list) and per_ref:
        for item in per_ref:
            if not isinstance(item, dict) or not item.get("scope_id"):
                raise HookCoordinatorError("expected_ref_scope_invalid")
            yield item["scope_id"], item
        return
    yield None, expected


_BLOCKING_ENTRY_STATUSES = frozenset(
    {"missing", "unknown", "unrouted", "insufficient"}
)
_BLOCKING_RECEIPT_STATUSES = frozenset({"unrouted", "unknown"})


def hook_exit_code(receipts):
    """Map Final Reconciliation Receipts to a Git Hook process exit code.

    A Receipt-level ``partial`` status from an incomplete Expected projection is
    not itself blocking when every eligible entry is ``matched`` /
    ``not_applicable`` and all observations reconciled. Missing, unknown,
    unrouted or insufficient bindings always fail the Hook. Trusted/Required
    remain unevaluated here (#63/#61).
    """
    if not isinstance(receipts, list) or not receipts:
        return HOOK_EXIT_RECONCILED_FAILURE
    for receipt in receipts:
        if not isinstance(receipt, dict):
            return HOOK_EXIT_RECONCILED_FAILURE
        if receipt.get("all_observations_reconciled") is not True:
            return HOOK_EXIT_RECONCILED_FAILURE
        if receipt.get("status") in _BLOCKING_RECEIPT_STATUSES:
            return HOOK_EXIT_RECONCILED_FAILURE
        entries = receipt.get("entries") or ()
        if not entries and receipt.get("status") != "matched":
            return HOOK_EXIT_RECONCILED_FAILURE
        for entry in entries:
            status = entry.get("binding_status")
            if status in _BLOCKING_ENTRY_STATUSES:
                return HOOK_EXIT_RECONCILED_FAILURE
            if status not in {"matched", "not_applicable"}:
                return HOOK_EXIT_RECONCILED_FAILURE
    return HOOK_EXIT_OK


def finalize_invocation(
    expected,
    *,
    invocation_ref,
    routing=(),
    resolutions=(),
    observations=(),
    routing_by_scope=None,
    resolutions_by_scope=None,
    observations_by_scope=None,
):
    """Run Final Reconciliation for one Invocation after Dispatch returns.

    Always executes, including when Jobs failed or were filtered. Multi-ref
    Expected containers are reconciled one ``ref_scope_id`` at a time; a single
    Scope Expected is called with ``ref_scope_id=None``.
    """
    if not isinstance(invocation_ref, str) or not invocation_ref:
        raise HookCoordinatorError("invocation_ref_invalid")
    routing_by_scope = routing_by_scope or {}
    resolutions_by_scope = resolutions_by_scope or {}
    observations_by_scope = observations_by_scope or {}
    receipts = []
    for ref_scope_id, _scope in _iter_scopes(expected):
        key = ref_scope_id
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
        help="Routing JSON file (repeatable); used for every scope unless scoped files are used",
    )
    finalize.add_argument(
        "--resolutions",
        action="append",
        default=[],
        help="Resolutions JSON file (array); repeatable",
    )
    finalize.add_argument(
        "--observations",
        action="append",
        default=[],
        help="Observations JSON file (array); repeatable",
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
        routing = []
        for path in args.routing:
            value = _load_json(path, "routing_payload_invalid")
            if isinstance(value, list):
                routing.extend(value)
            else:
                routing.append(value)
        resolutions = []
        for path in args.resolutions:
            value = _load_json(path, "resolutions_payload_invalid")
            if not isinstance(value, list):
                raise HookCoordinatorError("resolutions_payload_invalid")
            resolutions.extend(value)
        observations = []
        for path in args.observations:
            value = _load_json(path, "observations_payload_invalid")
            if not isinstance(value, list):
                raise HookCoordinatorError("observations_payload_invalid")
            observations.extend(value)
        result = finalize_invocation(
            expected,
            invocation_ref=binding.invocation_ref,
            routing=routing,
            resolutions=resolutions,
            observations=observations,
        )
    except HookCoordinatorError as error:
        print(error.code, file=sys.stderr)
        return HOOK_EXIT_COORDINATOR_ERROR

    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(result["exit_code"])
    return result["exit_code"]
