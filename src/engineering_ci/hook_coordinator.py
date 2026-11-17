"""#66 Hook Event Coordinator: Invocation → Collect → Final Reconciliation.

This module owns one Hook Invocation's outer control path. It:

- loads and verifies a #102 ``invocation_ref → event_payload_ref → Event``;
- binds Expected Event/Ref/Baseline identity to that Invocation Event;
- allocates this Invocation's ``run_ref`` / ``attempt_ref``;
- mints a Dispatch ``production_ref`` per eligible Job for the Runner;
- dispatches Jobs through #52 ``execution.execute()`` and seals delivery;
- collects only delivered original Reports (no field-only ``upstream_verified``);
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
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from engineering_ci import execution, hooks, reconciliation

DISPATCH_KIND = "engineering-ci-hook-dispatch"
DISPATCH_VERSION = 1
COORDINATOR_ISSUER_NAME = "engineering-ci.hook.coordinator"
COORDINATOR_ISSUER_VERSION = "1"
DEFAULT_RUNNER_REF = "execution:host"
DEFAULT_SCHEDULER_REF = {"name": "lefthook", "version": "2.2.2"}
DEFAULT_CONFIG_REF = {"name": "hook-job", "version": "1"}
DEFAULT_QUALIFICATION_NAME = "engineering-ci.hook.attempt-qualification"
_CLOSED_SINGLE_COMPLETENESS = frozenset(
    {"expected_domain_complete", "domain_complete_empty"}
)
_CLOSED_PER_REF_COMPLETENESS = frozenset(
    {"per_ref", "expected_domain_complete", "domain_complete_empty"}
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
    binding: InvocationBinding
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
        if eligibility == "eligible":
            # Minted at Dispatch activation; Collect accepts only Reports that
            # embed this production_ref via build_execution_report().
            entry["production_ref"] = _digest(
                {
                    "kind": "engineering-ci-hook-job-production",
                    "invocation_ref": self.invocation_ref,
                    "run_ref": self.run_ref,
                    "attempt_ref": self.attempt_ref,
                    "job_ref": job_ref,
                    "nonce": uuid.uuid4().hex,
                }
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
        binding=binding,
        runner_ref=runner_ref,
    )


_ZERO_OID_40 = "0" * 40


def _transport_oid(value, *, delete_after=False):
    """Normalize a Git transport OID; analyzed ``None`` delete after → zero OID."""
    if value is None or value == "":
        if delete_after:
            return _ZERO_OID_40
        return None
    return value


def _invocation_push_refs(event):
    """Ordered raw Git transport identities from a verified pre-push Event.

    Uses stdin ``before``/``after`` OIDs only — never Selection analysis baselines.
    """
    refs = event.get("refs")
    if not isinstance(refs, list) or not refs:
        raise HookCoordinatorError("invocation_refs_missing")
    identities = []
    for ref in refs:
        if not isinstance(ref, dict):
            raise HookCoordinatorError("invocation_refs_invalid")
        identity = (
            ref.get("local_ref"),
            ref.get("remote_ref"),
            ref.get("before"),
            ref.get("after"),
            ref.get("ref_action"),
        )
        if any(value is None or value == "" for value in identity[:4]):
            raise HookCoordinatorError("invocation_refs_invalid")
        identities.append(identity)
    return identities


def _scope_transport_identity(scope, expected):
    """Raw push transport identity for one Expected scope.

    Matches ``(local_ref, remote_ref, raw_before, raw_after[, ref_action])``.
    Never treats producer ``event_ref.head_ref`` / ``base_ref`` as local/remote
    names (those hold the remote label and raw-before OID). Analyzed Selection
    ``before``/``after`` baselines are not used for Invocation coverage.
    """
    event_ref = scope.get("event_ref")
    if event_ref is None and scope is expected:
        event_ref = expected.get("event_ref")
    if not isinstance(event_ref, dict):
        raise HookCoordinatorError("expected_event_ref_missing")

    local_ref = scope.get("local_ref") or event_ref.get("local_ref")
    remote_ref = scope.get("remote_ref")
    if isinstance(remote_ref, dict):
        remote_ref = None
    source_label = scope.get("source_ref")
    if remote_ref is None and isinstance(source_label, str) and source_label:
        remote_ref = source_label
    if remote_ref is None:
        remote_ref = event_ref.get("remote_ref")
    action = scope.get("ref_action") or event_ref.get("ref_action")

    references = event_ref.get("references") or []
    ref_item = None
    if isinstance(references, list) and references:
        if not isinstance(references[0], dict):
            raise HookCoordinatorError("expected_event_ref_invalid")
        ref_item = references[0]

    raw_before = event_ref.get("raw_before")
    raw_after = event_ref.get("raw_after")
    if ref_item is not None:
        local_ref = local_ref or ref_item.get("local_ref")
        remote_candidate = ref_item.get("remote_ref")
        if remote_candidate is None and isinstance(ref_item.get("source_ref"), str):
            remote_candidate = ref_item.get("source_ref")
        remote_ref = remote_ref or remote_candidate
        action = action or ref_item.get("ref_action")
        if raw_before is None:
            raw_before = ref_item.get("raw_before")
        if raw_after is None:
            raw_after = ref_item.get("raw_after")
        # Producer delete: analyzed after may be None while raw_after is zero.
        if raw_after is None and (
            action == "delete" or ref_item.get("ref_action") == "delete"
        ):
            raw_after = _transport_oid(ref_item.get("after"), delete_after=True)
        # Only when raw_* were never recorded (non-producer fixtures): accept
        # reference before/after as transport OIDs, not Selection baselines.
        if raw_before is None and "raw_before" not in (ref_item or {}):
            raw_before = ref_item.get("before")
        if raw_after is None and "raw_after" not in (ref_item or {}):
            raw_after = _transport_oid(
                ref_item.get("after"),
                delete_after=(action == "delete"),
            )
    else:
        # Single-scope Expected must declare local/remote explicitly. Do not
        # read head_ref/base_ref as names (producer: head_ref=remote label,
        # base_ref=raw_before OID).
        if raw_before is None:
            raw_before = event_ref.get("base_commit")
        if raw_after is None:
            raw_after = _transport_oid(
                event_ref.get("head_commit"),
                delete_after=(action == "delete"),
            )

    raw_before = _transport_oid(raw_before)
    raw_after = _transport_oid(raw_after, delete_after=(action == "delete"))
    if any(
        value is None or value == ""
        for value in (local_ref, remote_ref, raw_before, raw_after)
    ):
        raise HookCoordinatorError("expected_ref_identity_incomplete")
    return (local_ref, remote_ref, raw_before, raw_after, action)


def _scope_index_tree(scope, expected):
    """Index Tree OID from Expected Source — never the HEAD Commit OID."""
    source = scope.get("source_ref")
    if not isinstance(source, dict):
        source = scope.get("source")
    if not isinstance(source, dict) and scope is expected:
        source = expected.get("source_ref")
    if not isinstance(source, dict):
        raise HookCoordinatorError("expected_index_tree_missing")
    tree = source.get("index_tree")
    if tree:
        return tree
    if source.get("snapshot") == "index":
        tree = source.get("declared_target_tree")
        if tree:
            return tree
    raise HookCoordinatorError("expected_index_tree_missing")


def _invocation_git_dir(binding):
    """Physical Git Directory that owns this verified Invocation store."""
    if not isinstance(binding, InvocationBinding):
        raise HookCoordinatorError("invocation_binding_invalid")
    return Path(hooks._invocation_git_dir(binding.invocation_path)).resolve()


def _absolute_git_dir(root):
    """Resolve ``rev-parse --absolute-git-dir`` for an execution Worktree root."""
    result = hooks._git(root, "rev-parse", "--absolute-git-dir")
    if result.returncode != 0:
        raise HookCoordinatorError("execution_git_dir_unavailable")
    value = (result.stdout or "").strip()
    if not value:
        raise HookCoordinatorError("execution_git_dir_unavailable")
    return Path(value).resolve()


def _require_execution_root_matches_invocation(binding, root):
    """Refuse roots whose absolute Git Directory is not this Invocation store."""
    invocation_git_dir = _invocation_git_dir(binding)
    root_git_dir = _absolute_git_dir(root)
    if root_git_dir != invocation_git_dir:
        raise HookCoordinatorError("execution_repository_mismatch")
    return invocation_git_dir


def _tree_of_commit(git_dir, commit):
    """Resolve ``commit^{tree}`` via the Invocation Git Directory (no worktree guess)."""
    if not isinstance(commit, str) or not commit or commit == _ZERO_OID_40:
        raise HookCoordinatorError("push_target_tree_unavailable")
    result = subprocess.run(
        ["git", "--git-dir", str(git_dir), "rev-parse", f"{commit}^{{tree}}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise HookCoordinatorError("push_target_tree_unavailable")
    tree = (result.stdout or "").strip()
    if not tree:
        raise HookCoordinatorError("push_target_tree_unavailable")
    return tree


def _scope_push_source(scope, expected):
    """Expected Source dict for a push scope (never the remote-ref label string)."""
    source = scope.get("source")
    if not isinstance(source, dict):
        source = scope.get("source_ref")
    if not isinstance(source, dict) and scope is expected:
        source = expected.get("source")
    if not isinstance(source, dict) and scope is expected:
        source = expected.get("source_ref")
    if not isinstance(source, dict):
        raise HookCoordinatorError("expected_push_source_missing")
    return source


def _verify_push_source_matches_target(git_dir, source, raw_after, *, action=None):
    """Require Expected Source to name the Push target Commit/Tree — not Worktree."""
    if action == "delete" or raw_after == _ZERO_OID_40:
        return
    snapshot = source.get("snapshot")
    if snapshot not in {"push", "commit"}:
        raise HookCoordinatorError("expected_source_not_push_target")
    target_tree = _tree_of_commit(git_dir, raw_after)
    declared = source.get("declared_target_tree")
    if not declared or declared != target_tree:
        raise HookCoordinatorError("expected_source_tree_mismatch")
    head = source.get("head_commit")
    if head is not None and head != raw_after:
        raise HookCoordinatorError("expected_source_head_mismatch")


def verify_expected_matches_invocation(binding, expected):
    """Refuse Expected projections that do not bind this Invocation Event.

    Pre-push: raw transport ``(local_ref, remote_ref, raw_before, raw_after)``
    membership both ways when multi-ref, and Expected Source must declare the
    Push target Commit/Tree (Worktree Sources are refused). Selection analysis
    baselines are not compared here. Pre-commit: Expected Source Index Tree
    versus #102 ``index.tree`` (Commit OID is never compared to Tree OID).
    """
    if not isinstance(binding, InvocationBinding):
        raise HookCoordinatorError("invocation_binding_invalid")
    event = binding.event
    hook = event.get("hook")
    scopes = list(_iter_scopes(expected))
    if hook == "pre-commit":
        index = event.get("index")
        if not isinstance(index, dict) or not index.get("tree"):
            raise HookCoordinatorError("invocation_index_missing")
        captured_tree = index["tree"]
        for _scope_id, scope in scopes:
            if _scope_index_tree(scope, expected) != captured_tree:
                raise HookCoordinatorError("expected_event_mismatch")
        return True
    if hook != "pre-push":
        raise HookCoordinatorError("invocation_hook_unsupported")
    invocation_refs = _invocation_push_refs(event)
    git_dir = _invocation_git_dir(binding)
    covered = set()
    for _scope_id, scope in scopes:
        identity = _scope_transport_identity(scope, expected)
        local_ref, remote_ref, raw_before, raw_after, action = identity
        matched = False
        matched_action = action
        for index, inv in enumerate(invocation_refs):
            inv_local, inv_remote, inv_before, inv_after, inv_action = inv
            if (
                local_ref == inv_local
                and remote_ref == inv_remote
                and raw_before == inv_before
                and raw_after == inv_after
            ):
                if (
                    action is not None
                    and inv_action is not None
                    and action != inv_action
                ):
                    raise HookCoordinatorError("expected_ref_action_mismatch")
                covered.add(index)
                matched = True
                matched_action = action if action is not None else inv_action
                break
        if not matched:
            raise HookCoordinatorError("expected_event_mismatch")
        source = _scope_push_source(scope, expected)
        _verify_push_source_matches_target(
            git_dir, source, raw_after, action=matched_action
        )
    multi_expected = any(scope_id is not None for scope_id, _scope in scopes)
    multi_invocation = len(invocation_refs) > 1
    if (multi_expected or multi_invocation) and covered != set(
        range(len(invocation_refs))
    ):
        raise HookCoordinatorError("invocation_ref_uncovered")
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
        "production_ref": report.get("production_ref"),
    }


def attempt_result_instance(run_binding, job_ref):
    """``execution.execute`` ``run_instance`` prefix for this Job/Attempt."""
    return f"{run_binding.run_ref}/{run_binding.attempt_ref}/{job_ref}"


def _require_runner_produced_report(report):
    """Refuse Coordinator-synthesized Native Result wrappers.

    ``upstream_verified`` Collect requires the original Runner/#52 Report shape
    so ``plan_authorized`` / contract / prerequisite rejection semantics remain.
    """
    checks = report.get("checks")
    if not isinstance(checks, list) or not checks:
        raise HookCoordinatorError("execution_report_not_runner_produced")
    saw_results = False
    for check in checks:
        if not isinstance(check, dict):
            raise HookCoordinatorError("execution_report_not_runner_produced")
        has_results = (
            check.get("native_results") is not None
            or check.get("native_result") is not None
        )
        if not has_results:
            continue
        saw_results = True
        if check.get("plan_authorized") is not True:
            raise HookCoordinatorError("execution_report_not_runner_produced")
        contract = check.get("check_contract")
        if not isinstance(contract, dict) or contract.get("status") != "valid":
            raise HookCoordinatorError("execution_report_not_runner_produced")
    if not saw_results:
        raise HookCoordinatorError("execution_report_not_runner_produced")


def _require_attempt_bound_natives(run_binding, job_ref, results):
    """Native Results must be minted under this Attempt's execute run_instance."""
    expected_prefix = attempt_result_instance(run_binding, job_ref)
    if not results:
        raise HookCoordinatorError("native_results_required")
    for result in results:
        if not isinstance(result, dict):
            raise HookCoordinatorError("native_result_attempt_mismatch")
        instance = result.get("result_instance")
        if not isinstance(instance, str) or not instance.startswith(expected_prefix):
            raise HookCoordinatorError("native_result_attempt_mismatch")
        if result.get("runner_ref") != run_binding.runner_ref:
            raise HookCoordinatorError("native_result_runner_mismatch")


def _seal_delivered_report(run_binding, job_ref, report):
    """Attach Dispatch attribution without rewriting Check/Native bodies."""
    sealed = copy.deepcopy(report)
    sealed["invocation_ref"] = run_binding.invocation_ref
    sealed["run_ref"] = run_binding.run_ref
    sealed["attempt_ref"] = run_binding.attempt_ref
    sealed["job_ref"] = job_ref
    sealed["production_ref"] = run_binding.jobs[job_ref]["production_ref"]
    return sealed


def _resolve_invocation_index_path(binding, root, git_dir):
    """Absolute Index path recorded on the verified pre-commit Event."""
    index = binding.event.get("index")
    if not isinstance(index, dict):
        raise HookCoordinatorError("invocation_index_missing")
    file_ref = index.get("file")
    if not isinstance(file_ref, str) or not file_ref:
        raise HookCoordinatorError("invocation_index_file_missing")
    candidate = Path(file_ref)
    if candidate.is_absolute():
        resolved = candidate.resolve()
        if resolved.is_file():
            return resolved
        raise HookCoordinatorError("invocation_index_file_missing")
    for base in (Path(root).resolve(), Path(git_dir).resolve()):
        joined = (base / file_ref).resolve()
        if joined.is_file():
            return joined
    raise HookCoordinatorError("invocation_index_file_missing")


def _require_plan_matches_push_invocation(binding, plan, git_dir):
    """Refuse Worktree Plans and Plans whose head/tree is not a Push after OID."""
    if not isinstance(plan, dict):
        raise HookCoordinatorError("execution_plan_invalid")
    git = plan.get("git")
    if not isinstance(git, dict):
        raise HookCoordinatorError("execution_source_not_push_target")
    if git.get("snapshot") not in {"push", "commit"}:
        raise HookCoordinatorError("execution_source_not_push_target")
    head = git.get("head_commit")
    afters = {
        after
        for _local, _remote, _before, after, _action in _invocation_push_refs(
            binding.event
        )
        if after != _ZERO_OID_40
    }
    if head not in afters:
        raise HookCoordinatorError("execution_head_not_invocation_target")
    target_tree = _tree_of_commit(git_dir, head)
    declared = git.get("execution_tree") or git.get("tree")
    if declared is not None and declared != target_tree:
        raise HookCoordinatorError("execution_tree_not_invocation_target")


def _require_plan_matches_precommit_invocation(binding, plan, root, git_dir):
    """Refuse Plans that are not the captured Index Source for this Invocation."""
    index = binding.event.get("index")
    if not isinstance(index, dict) or not index.get("tree"):
        raise HookCoordinatorError("invocation_index_missing")
    captured_tree = index["tree"]
    captured_index = _resolve_invocation_index_path(binding, root, git_dir)
    if not isinstance(plan, dict):
        raise HookCoordinatorError("execution_plan_invalid")
    git = plan.get("git")
    if not isinstance(git, dict):
        raise HookCoordinatorError("execution_source_not_index_target")
    if git.get("snapshot") != "index":
        raise HookCoordinatorError("execution_source_not_index_target")
    declared_trees = [
        git.get(key) for key in ("index_tree", "execution_tree") if git.get(key)
    ]
    if not declared_trees:
        raise HookCoordinatorError("execution_index_tree_missing")
    if any(tree != captured_tree for tree in declared_trees):
        raise HookCoordinatorError("execution_index_tree_mismatch")
    plan_index = git.get("index_file")
    if plan_index is None:
        default_index = (Path(git_dir) / "index").resolve()
        if captured_index != default_index:
            raise HookCoordinatorError("execution_index_file_mismatch")
    elif Path(plan_index).resolve() != captured_index:
        raise HookCoordinatorError("execution_index_file_mismatch")


def _require_plan_matches_invocation(binding, plan, root, git_dir):
    """Dispatch Hook-specific Plan ↔ Invocation Source ownership checks."""
    if not isinstance(binding, InvocationBinding):
        raise HookCoordinatorError("invocation_binding_invalid")
    if binding.hook == "pre-push":
        _require_plan_matches_push_invocation(binding, plan, git_dir)
        return
    if binding.hook == "pre-commit":
        _require_plan_matches_precommit_invocation(binding, plan, root, git_dir)
        return
    raise HookCoordinatorError("invocation_hook_unsupported")


def execute_activated_job(run_binding, job_ref, *, plan, root, **execute_kwargs):
    """Run one eligible Job through #52 ``execution.execute`` and seal delivery.

    The returned Report keeps execute's Check/Native bodies intact and records
    ``delivered_report_ref`` so Collect can require this production boundary.
    Source checks always use the Invocation Binding sealed into ``RunBinding``
    at ``open_attempt``: execution ``root`` must share that Git Directory;
    pre-push Plans must target the Push Commit/Tree; pre-commit Plans must
    target the captured Index Tree/file (Worktree Plans refused).
    """
    if not isinstance(run_binding, RunBinding):
        raise HookCoordinatorError("run_binding_invalid")
    if job_ref not in run_binding.jobs:
        raise HookCoordinatorError("job_not_activated")
    job = run_binding.jobs[job_ref]
    if job.get("attempt_eligibility") != "eligible":
        raise HookCoordinatorError("attempt_not_eligible")
    production_ref = job.get("production_ref")
    if not isinstance(production_ref, str) or not production_ref.startswith("sha256:"):
        raise HookCoordinatorError("job_production_ref_missing")
    if job.get("delivered_report_ref") is not None:
        raise HookCoordinatorError("job_already_executed")
    binding = run_binding.binding
    if not isinstance(binding, InvocationBinding):
        raise HookCoordinatorError("invocation_binding_invalid")
    if binding.invocation_ref != run_binding.invocation_ref:
        raise HookCoordinatorError("execution_binding_mismatch")
    git_dir = _require_execution_root_matches_invocation(binding, root)
    _require_plan_matches_invocation(binding, plan, root, git_dir)
    run_instance = attempt_result_instance(run_binding, job_ref)
    execute_kwargs = dict(execute_kwargs)
    execute_kwargs.setdefault("execution_context", "host")
    if execute_kwargs.get("run_instance") not in (None, run_instance):
        raise HookCoordinatorError("run_instance_mismatch")
    report = execution.execute(
        plan,
        root=root,
        run_instance=run_instance,
        **{
            key: value for key, value in execute_kwargs.items() if key != "run_instance"
        },
    )
    if (
        not isinstance(report, dict)
        or report.get("kind") != reconciliation.EXECUTION_REPORT_KIND
    ):
        raise HookCoordinatorError("execution_report_invalid")
    sealed = _seal_delivered_report(run_binding, job_ref, report)
    report_ref = report_ref_for(sealed)
    job["delivered_report_ref"] = copy.deepcopy(report_ref)
    job["report_ref"] = copy.deepcopy(report_ref)
    return sealed


def collect_job_report(run_binding, job_ref, report):
    """Collect a Report delivered by ``execute_activated_job`` for this Job.

    Field-identical hand-built Reports without a recorded delivery are refused.
    """
    if not isinstance(run_binding, RunBinding):
        raise HookCoordinatorError("run_binding_invalid")
    if job_ref not in run_binding.jobs:
        raise HookCoordinatorError("job_not_activated")
    job = run_binding.jobs[job_ref]
    if job.get("attempt_eligibility") != "eligible":
        raise HookCoordinatorError("attempt_not_eligible")
    production_ref = job.get("production_ref")
    delivered = job.get("delivered_report_ref")
    if not isinstance(production_ref, str) or not production_ref.startswith("sha256:"):
        raise HookCoordinatorError("job_production_ref_missing")
    if delivered is None:
        raise HookCoordinatorError("job_execution_delivery_missing")
    if job.get("issued_report_ref") is not None:
        raise HookCoordinatorError("job_report_already_collected")
    if not isinstance(report, dict):
        raise HookCoordinatorError("execution_report_invalid")
    attribution = _report_execution_attribution(report)
    expected = {
        "invocation_ref": run_binding.invocation_ref,
        "run_ref": run_binding.run_ref,
        "attempt_ref": run_binding.attempt_ref,
        "job_ref": job_ref,
        "production_ref": production_ref,
    }
    if any(attribution.get(key) is None for key in expected):
        raise HookCoordinatorError("execution_report_attribution_missing")
    if attribution != expected:
        raise HookCoordinatorError("execution_report_attribution_mismatch")
    _require_runner_produced_report(report)
    results, rejections, report_ref = verify_execution_report(report)
    if report_ref != delivered:
        raise HookCoordinatorError("execution_report_delivery_mismatch")
    _require_attempt_bound_natives(run_binding, job_ref, results)
    run_binding.collected_reports[job_ref] = copy.deepcopy(report_ref)
    job["issued_report_ref"] = copy.deepcopy(report_ref)
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
    if job.get("issued_report_ref") != report_ref:
        raise HookCoordinatorError("execution_report_production_mismatch")
    _require_runner_produced_report(report)
    results, _rejections, verified_ref = verify_execution_report(report, report_ref)
    if verified_ref != report_ref:
        raise HookCoordinatorError("execution_report_digest_mismatch")
    _require_attempt_bound_natives(run_binding, job_ref, results)
    attribution = _report_execution_attribution(report)
    expected_attr = {
        "invocation_ref": run_binding.invocation_ref,
        "run_ref": run_binding.run_ref,
        "attempt_ref": run_binding.attempt_ref,
        "job_ref": job_ref,
        "production_ref": job.get("production_ref"),
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
        # Independent of receipt.status: complete+not_claimed must not exit 0.
        per_ref_scope = receipt.get("ref") is not None
        allowed = (
            _CLOSED_PER_REF_COMPLETENESS
            if per_ref_scope
            else _CLOSED_SINGLE_COMPLETENESS
        )
        if completeness not in allowed:
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
