"""Deterministic human review evidence; no approval, execution or capability mutation."""

from collections import defaultdict

from ..tracing.feedback_summary import SUMMARY_VERSION, FeedbackSummary
from ..tracing.retrieval import Query, budget_error, encode, page_bounds
from ..workflow_core.capabilities import APPROVAL, CALL, CapabilityStore
from ..workflow_core.documents import WorkflowError, digest, read
from ..workflow_core.feedback_snapshot import from_view
from ..workflow_core.schemas import validate
from .candidates import TargetPolicyChanged
from .capabilities import inside, verified_comparison
from .comparison import IDENTITY_PARTS, gate, metrics
from .evidence import execution_evidence

REVIEW_VERSION = "workflow-feedback-review/1"


class ProtectedReviewOutput(WorkflowError):
    """Prevent even an error receipt from being saved over original trial evidence."""


def review_request(source):
    """Reject non-successful or unsupported summary documents before any database read."""
    try:
        if any(
            source.get(key) != value
            for key, value in {
                "schema_version": 1,
                "operation": "workflow.feedback-summary",
                "summary_version": SUMMARY_VERSION,
                "status": "summarized",
                "exit_code": 0,
            }.items()
        ):
            raise ValueError
        return FeedbackSummary(
            tuple(source["scope"]["feedback_ids"]), source["budget"]["max_bytes"]
        ).validate()
    except (KeyError, TypeError, ValueError):
        raise WorkflowError(
            "Review requires a successful supported feedback summary"
        ) from None


def snapshot_evidence(value, view):
    """Verify exact snapshot facts or retained v2 attempt rows; do not invent older state files."""
    # Frozen call identity is already checked by execution_evidence; store matching is checked by caller.
    reference = view["metadata"]["capability_call"]
    if (
        reference is None
        or reference.get("status") != "bound"
        or reference["capability"] != value["capability"]
        or reference["catalog_revision"] != value["catalog_revision"]
        or any(
            value[key] != view["metadata"][key]
            for key in ("definition_id", "generation_id")
        )
    ):
        return "binding_mismatch"
    if value["evidence"] == view["scope"]:
        if (
            value["recorded_result"] != view["metadata"]["recorded_result"]
            or value["observation_completeness"]
            != view["metadata"]["observation_completeness"]
            or value["metrics"] != {key: metrics(view)[key] for key in value["metrics"]}
        ):
            return "snapshot_facts_mismatch"
        if value.get("attempt_snapshot") is not None and value[
            "attempt_snapshot"
        ] != from_view(view):
            return "attempt_index_mismatch"
        return "verified_exact_snapshot"
    if value.get("attempt_snapshot") is None:
        return "historical_snapshot_unavailable"
    rows = {
        (row["step_id"], row["attempt"]): row for row in from_view(view)["attempts"]
    }
    selected = value["attempt_snapshot"]["attempts"]
    if not selected:
        return "historical_snapshot_unavailable"
    if any(rows.get((row["step_id"], row["attempt"])) != row for row in selected):
        return "attempt_evidence_changed_or_missing"
    return "verified_retained_attempt_rows"


def call_differences(value, view, trial):
    """Compare recorded call identities with the approved candidate, never current workstation inputs."""
    candidate = {row["step_id"]: row for row in trial["checks"]}
    snapshot = value.get("attempt_snapshot")
    selected = (
        {(row["step_id"], row["attempt"]) for row in snapshot["attempts"]}
        if snapshot
        else None
    )
    differences = []
    for row in view["items"]:
        if selected is not None and (row["step_id"], row["attempt"]) not in selected:
            continue
        old = candidate.get(row["step_id"])
        if (
            old is None
            or not row["registered_check"]
            or row["check"]["status"] != "verified"
        ):
            differences.append(
                {
                    "step_id": row["step_id"],
                    "attempt": row["attempt"],
                    "reason": "original_check_unavailable",
                }
            )
            continue
        observed = {
            **row,
            "check": {
                **row["check"],
                "recorded_gate": view["recorded_gates"].get(row["key"]),
            },
        }
        if not gate(observed):
            differences.append(
                {
                    "step_id": row["step_id"],
                    "attempt": row["attempt"],
                    "reason": "original_quality_gate_not_passed",
                }
            )
        # The approved comparison binds the candidate result; its identity is read from that source below.
        source_identity = trial["candidate_identities"].get(row["step_id"], {})
        identity = source_identity.get("parts", {})
        current = row["check"].get("identity_parts", {})
        if (
            not IDENTITY_PARTS <= identity.keys()
            or not IDENTITY_PARTS <= current.keys()
            or not row["check"].get("identity_sha256")
        ):
            differences.append(
                {
                    "step_id": row["step_id"],
                    "attempt": row["attempt"],
                    "reason": "original_check_identity_unavailable",
                }
            )
            continue
        changed = [
            key
            for key in IDENTITY_PARTS | {"executor"}
            if identity.get(key) != current.get(key)
        ]
        if (
            not changed
            and source_identity.get("sha256") != row["check"]["identity_sha256"]
        ):
            changed.append("full_identity")
        if row["tool_id"] != old["tool_id"]:
            changed.append("tool")
        if changed:
            differences.append(
                {
                    "step_id": row["step_id"],
                    "attempt": row["attempt"],
                    "reason": "recorded_context_changed",
                    "parts": sorted(changed),
                }
            )
    return differences


def review_feedback(
    source,
    values,
    store_path,
    paths,
    catalog,
    root,
    *,
    max_bytes=16384,
    destination=None,
):
    """Associate verified archive input with explicit local approvals and receipts under existing locks."""
    page_bounds(Query("evidence", max_bytes=max_bytes))
    if len(paths) > 32:
        raise WorkflowError("Review accepts at most 32 explicit execution directories")
    store = CapabilityStore(inside(store_path, root))
    if not (store.directory / "catalog.json").is_file():
        raise WorkflowError(
            "Review requires an existing capability catalog; no store was created"
        )
    views, source_issues = {}, []
    protected = [store.directory, *(inside(path, root) for path in paths)]
    for path in paths:
        directory = inside(path, root)
        try:
            binding = read(directory / "capability.json")
            validate(binding, CALL, label="review frozen call")
            if inside(binding["store"], root) != store.directory:
                raise WorkflowError("Frozen call names another capability store")
            view = execution_evidence(directory, catalog, root)
            identity = view["scope"]["execution_id"]
            if identity in views and views[identity]["scope"] != view["scope"]:
                raise WorkflowError("Repeated execution input changed during review")
            views[identity] = view
        except (WorkflowError, OSError):
            source_issues.append(
                {
                    "path": directory.relative_to(root).as_posix(),
                    "status": "unavailable",
                }
            )
    families = defaultdict(list)
    for value in values:
        families[
            tuple(
                value["capability"][key] for key in ("name", "version", "approval_id")
            )
        ].append(value)
    items = []
    with store.locked():
        index = store.index()
        for reference, feedbacks in sorted(families.items()):
            name, version, approval_id = reference
            item = {
                "capability": dict(
                    zip(("name", "version", "approval_id"), reference, strict=True)
                ),
                "recommendation": "investigate",
                "reasons": [],
                "feedback": [],
                "trial": {"status": "unavailable"},
                "enabled_at_read": index["active"].get(name) == approval_id,
            }
            try:
                original = read(store.location(name, version) / "approval.json")
                validate(original, APPROVAL, label="review approval input")
                protected.extend(
                    inside(original[key]["path"], root)
                    for key in ("baseline", "candidate")
                )
                approval, directory = store.load(name, version, catalog)
                if approval["approval_id"] != approval_id:
                    raise WorkflowError("Selected feedback names another approval")
                trial = verified_comparison(approval, directory, catalog, root)
                candidate = execution_evidence(
                    inside(approval["candidate"]["path"], root), catalog, root
                )
                if candidate["scope"] != approval["candidate"]["scope"]:
                    raise WorkflowError("Approved candidate changed during review")
                trial["candidate_identities"] = {
                    row["step_id"]: {
                        "parts": row["check"].get("identity_parts", {}),
                        "sha256": row["check"].get("identity_sha256"),
                    }
                    for row in candidate["items"]
                }
                item["trial"] = {
                    "status": "verified",
                    "approval_id": approval_id,
                    "comparison_id": approval["comparison_id"],
                    "comparison_sha256": approval["comparison_sha256"],
                    "verified_scope": approval["verified_scope"],
                    "baseline": approval["baseline"]["scope"],
                    "candidate": approval["candidate"]["scope"],
                }
            except TargetPolicyChanged as error:
                trial = None
                item["trial"] = {
                    "status": "policy_changed",
                    "comparison_id": approval["comparison_id"],
                    "comparison_sha256": approval["comparison_sha256"],
                    "recorded_policy_sha256": error.recorded_policy,
                    "current_policy_sha256": error.current_policy,
                }
                item["reasons"].append("target_policy_changed")
                item["follow_up"] = "review_new_definition_and_controlled_trial"
            except (WorkflowError, OSError):
                trial = None
                item["reasons"].append("approval_or_original_comparison_unavailable")
            mismatch, uncertain = False, trial is None
            for value in sorted(feedbacks, key=lambda entry: entry["feedback_id"]):
                view = views.get(value["evidence"]["execution_id"])
                validation = (
                    snapshot_evidence(value, view)
                    if view
                    else "execution_not_supplied_or_unavailable"
                )
                differences = (
                    call_differences(value, view, trial)
                    if view and trial and validation.startswith("verified_")
                    else []
                )
                item["feedback"].append(
                    {
                        "feedback_id": value["feedback_id"],
                        "execution_id": value["evidence"]["execution_id"],
                        "execution_scope_at_read": view["scope"] if view else None,
                        "recorded_result": value["recorded_result"],
                        "execution_evidence_validation": validation,
                        "historical_state_validation": "verified_at_read"
                        if validation == "verified_exact_snapshot"
                        else "not_available",
                        "differences": differences,
                    }
                )
                if (
                    not validation.startswith("verified_")
                    or value["observation_completeness"] != "complete"
                ):
                    uncertain = True
                    item["reasons"].append("selected_execution_evidence_not_complete")
                if value["recorded_result"] != {
                    "phase": "FINISHED",
                    "status": "passed",
                    "exit_code": 0,
                }:
                    uncertain = True
                    item["reasons"].append("original_non_success_retained")
                if any(
                    change["reason"]
                    in {
                        "original_check_unavailable",
                        "original_quality_gate_not_passed",
                        "original_check_identity_unavailable",
                    }
                    for change in differences
                ):
                    uncertain = True
                    item["reasons"].append("original_check_measurement_unavailable")
                if any(
                    change["reason"] == "recorded_context_changed"
                    for change in differences
                ):
                    mismatch = True
                    item["reasons"].append("call_and_approved_trial_context_differ")
            if any(
                group["snapshot_coverage"] == "conflicting"
                and any(
                    row["feedback_id"] in {v["feedback_id"] for v in feedbacks}
                    for row in group["snapshots"]
                )
                for group in source["executions"]
            ):
                uncertain = True
                item["reasons"].append("archived_attempt_or_binding_conflict")
            item["recommendation"] = (
                "investigate"
                if uncertain
                else "new_controlled_trial"
                if mismatch
                else "retain_evidence"
            )
            item["reasons"] = sorted(set(item["reasons"]))
            items.append(item)
    if destination and any(destination.is_relative_to(path) for path in protected):
        raise ProtectedReviewOutput(
            "Review JSON must stay outside original capability and execution evidence"
        )
    result = {
        "schema_version": 1,
        "operation": "workflow.feedback-review",
        "review_version": REVIEW_VERSION,
        "summary_id": source["summary_id"],
        "summary_sha256": digest(source),
        "catalog_sha256": digest(index),
        "status": "reviewed",
        "exit_code": 0,
        "items": items,
        "source_issues": source_issues,
        "collection_coverage": "unknown",
        "current_input_validity": "not_checked",
        "decision_required": True,
        "capability_mutated": False,
        "checks_executed": False,
        "optimization_evaluated": False,
        "llm_used": False,
    }
    result["review_id"] = digest(result)
    result["budget"] = {"max_bytes": max_bytes, "unit": "utf8_bytes_including_newline"}
    size = len(encode(result))
    if size > max_bytes:
        failure = budget_error(
            Query("evidence", max_bytes=max_bytes),
            required_bytes=size,
            reference=result["review_id"],
        )
        failure["operation"] = "workflow.feedback-review"
        return failure
    return result
