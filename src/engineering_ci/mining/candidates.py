"""Deterministic review hypotheses grounded in existing observed analysis."""

from collections import Counter, defaultdict
from itertools import pairwise

from ..tracing.projection import identifier
from ..tracing.results import canonical, digest, prepare_result
from ..tracing.store import ArchiveError
from .analysis import select_cases

REPORT_VERSION = "observed-workflow-candidates/1"
REFERENCE_LIMIT = 8
REVIEW_REQUIREMENTS = (
    "exact_action_or_check_identity",
    "input_digest_and_tool_environment",
    "failure_recovery_and_negative_test_context",
    "handler_inputs_outputs_permissions_and_side_effects",
    "original_quality_gates_and_candidate_execution_evidence",
)


def checked_cases(evidence):
    """Corroborate stored discovery against its input without calling a mining engine."""
    analysis, projection = evidence.analysis, evidence.projection
    prepare_result(projection, "0" * 64)
    if any(
        analysis[key] != projection[key]
        for key in ("snapshot_id", "projection_id", "completeness")
    ):
        raise ArchiveError("identity_conflict", "Analysis and projection scope differ")
    operations = projection["operations"]
    if any(
        not isinstance(item, dict)
        or identifier(item.get("turn_id")) is None
        or not isinstance(item.get("event_id"), str)
        or not item["event_id"].isascii()
        or not item["event_id"].isdigit()
        or not 1 <= len(item["event_id"]) <= 24
        for item in operations
    ) or len({item["event_id"] for item in operations}) != len(operations):
        raise ArchiveError(
            "invalid_evidence",
            "Projected event references must be unique safe source offsets",
        )
    selected, excluded = select_cases(
        projection, allow_incomplete=analysis["allow_incomplete"]
    )
    variants, edges, starts, ends = Counter(), Counter(), Counter(), Counter()
    for case in selected:
        activities = tuple(item["activity"] for item in case["operations"])
        variants[activities] += 1
        edges.update(pairwise(activities))
        starts[activities[0]] += 1
        ends[activities[-1]] += 1
    expected = {
        "selected_cases": [
            {
                "turn_id": case["turn_id"],
                "boundary": case["boundary"],
                "event_ids": [item["event_id"] for item in case["operations"]],
            }
            for case in selected
        ],
        "excluded_cases": excluded,
        "variants": [
            {"activities": list(key), "cases": count}
            for key, count in sorted(variants.items())
        ],
        "dfg": [
            {"from": left, "to": right, "count": count}
            for (left, right), count in sorted(edges.items())
        ],
        "start_activities": dict(sorted(starts.items())),
        "end_activities": dict(sorted(ends.items())),
        "activity_counts": dict(
            sorted(Counter(item["activity"] for item in operations).items())
        ),
        "outcome_counts": dict(
            sorted(Counter(item["outcome"] for item in operations).items())
        ),
        "status": "passed" if selected else "unavailable",
    }
    if any(
        canonical(analysis.get(key)) != canonical(value)
        for key, value in expected.items()
    ):
        raise ArchiveError(
            "invalid_evidence",
            "Analysis model or measurements differ from its actual projection",
        )
    return selected, excluded


def witness(case, operations):
    """Export bounded source references and outcomes, never tool arguments or output."""
    return {
        "turn_id": case["turn_id"],
        "boundary": case["boundary"],
        "operation_count": len(operations),
        "operations": [
            {
                key: item[key]
                for key in ("event_id", "activity", "outcome", "start_ns", "end_ns")
            }
            for item in operations[:REFERENCE_LIMIT]
        ],
        "omitted_operations": max(0, len(operations) - REFERENCE_LIMIT),
    }


def observations(selected):
    """Group observed repeats and successors; no inferred dependency or removable work."""
    repeats, variants, successors = (
        defaultdict(list),
        defaultdict(list),
        defaultdict(list),
    )
    for case in selected:
        operations = case["operations"]
        variants[tuple(item["activity"] for item in operations)].append(
            (case, operations)
        )
        for left, right in pairwise(operations):
            successors[left["activity"]].append((case, [left, right]))
            if left["activity"] == right["activity"]:
                repeats[left["activity"]].append((case, [left, right]))
    for activity, members in sorted(repeats.items()):
        yield "adjacent_activity_repeat", (activity, activity), members, None
    for activities, members in sorted(variants.items()):
        if len(members) >= 2:
            yield "repeated_variant", activities, members, None
    for activity, members in sorted(successors.items()):
        targets = Counter(items[1]["activity"] for _, items in members)
        if len(targets) >= 2:
            yield (
                "observed_successor_branch",
                (activity,),
                members,
                dict(sorted(targets.items())),
            )


def validate_limits(limit, evidence_limit):
    """Reject unsupported output bounds before reading files or opening a database."""
    if (
        type(limit) is not int
        or not 1 <= limit <= 100
        or type(evidence_limit) is not int
        or not 1 <= evidence_limit <= 20
    ):
        raise ArchiveError(
            "invalid_query", "Candidate limit must be 1–100 and evidence limit 1–20"
        )


def candidates(evidence, *, limit=20, evidence_limit=3):
    """Build review-only evidence; operation success never means optimization approval."""
    validate_limits(limit, evidence_limit)
    analysis, projection = evidence.analysis, evidence.projection
    if (
        not isinstance(analysis, dict)
        or type(evidence.association_verified) is not bool
    ):
        raise ArchiveError(
            "invalid_evidence", "Expected a supported analysis evidence reference"
        )
    prepare_result(analysis, analysis.get("input_sha256"), origin=evidence.origin)
    source = {
        "analysis_id": analysis["analysis_id"],
        "projection_id": analysis["projection_id"],
        "snapshot_id": analysis["snapshot_id"],
        "origin": evidence.origin,
        "analysis_document_digest": digest(canonical(analysis)),
        "analysis_input_sha256": analysis["input_sha256"],
        "projection_document_digest": digest(canonical(projection))
        if projection is not None
        else None,
        "association_verified": evidence.association_verified,
        "dependencies": analysis["dependencies"],
    }
    identity = {
        "candidate_report_version": REPORT_VERSION,
        "source": source,
        "parameters": {
            "limit": limit,
            "evidence_limit": evidence_limit,
            "reference_limit": REFERENCE_LIMIT,
        },
    }
    report = {
        "schema_version": 1,
        **identity,
        "report_id": digest(canonical(identity).rstrip(b"\n")),
        "status": "unavailable",
        "reasons": [],
        "completeness": analysis["completeness"],
        "case_scope": analysis["case_scope"],
        "allow_incomplete": analysis["allow_incomplete"],
        "selected_cases": len(analysis["selected_cases"]),
        "excluded_cases": len(analysis["excluded_cases"]),
        "coverage": None,
        "total_candidates": 0,
        "omitted_candidates": 0,
        "candidates": [],
        "review_status": "not_reviewed",
        "quality_gate": "not_evaluated",
        "optimization_verified": False,
        "llm_used": False,
        "limitations": [
            "Coarse activity repeats are not repeated checks or proof of removable work",
            "Successors describe observed order, not causal dependencies or executable branches",
            "No Handler, quality approval, cost saving or cross-run optimization is inferred",
        ],
    }
    if projection is None or not evidence.association_verified:
        report["reasons"] = ["input_association_unverified"]
        return report
    selected, excluded = checked_cases(evidence)
    report["coverage"] = {
        "projected_operations": len(projection["operations"]),
        "selected_operations": sum(len(case["operations"]) for case in selected),
        "excluded_operations": sum(case["operations"] for case in excluded),
        "selected_outcome_counts": dict(
            sorted(
                Counter(
                    item["outcome"] for case in selected for item in case["operations"]
                ).items()
            )
        ),
        "all_outcome_counts": analysis["outcome_counts"],
        "exclusion_counts": dict(
            sorted(Counter(case["reason"] for case in excluded).items())
        ),
    }
    if not selected:
        report["reasons"] = ["analysis_unavailable"]
        return report
    proposed = []
    for kind, activities, members, targets in observations(selected):
        pattern = {
            "activities": list(activities[:REFERENCE_LIMIT]),
            "activity_count": len(activities),
            "omitted_activities": max(0, len(activities) - REFERENCE_LIMIT),
            "activity_sequence_digest": digest(canonical(list(activities))),
            "successor_counts": targets,
        }
        candidate_id = digest(
            canonical(
                {
                    "version": REPORT_VERSION,
                    "source": source,
                    "kind": kind,
                    "pattern": pattern,
                }
            ).rstrip(b"\n")
        )
        proposed.append(
            {
                "candidate_id": candidate_id,
                "kind": kind,
                "pattern": pattern,
                "occurrences": len(members),
                "case_count": len({case["turn_id"] for case, _ in members}),
                "witnesses": [
                    witness(case, items) for case, items in members[:evidence_limit]
                ],
                "omitted_witnesses": max(0, len(members) - evidence_limit),
                "successor_examples": {
                    target: next(
                        witness(case, items)
                        for case, items in members
                        if items[1]["activity"] == target
                    )
                    for target in targets
                }
                if targets is not None
                else None,
                "status": "needs_review",
                "executable": False,
                "handler": None,
                "proposed_investigation": {
                    "adjacent_activity_repeat": "Determine whether adjacent activities are distinct work, recovery or eligible same-input reuse",
                    "repeated_variant": "Inspect whether repeated observed sequences have a common explicit reusable operation contract",
                    "observed_successor_branch": "Identify the actual inputs and conditions behind different observed successors",
                }[kind],
                "required_evidence": [
                    {"name": name, "status": "not_available_in_activity_projection"}
                    for name in REVIEW_REQUIREMENTS
                ],
            }
        )
    report.update(
        status="reported",
        total_candidates=len(proposed),
        omitted_candidates=max(0, len(proposed) - limit),
        candidates=proposed[:limit],
    )
    return report
