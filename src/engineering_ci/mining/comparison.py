"""Compare existing observed results; never infer optimization or CI eligibility."""

from dataclasses import dataclass

from ..tracing.projection import ACTIVITIES
from ..tracing.results import canonical, digest, prepare_result
from ..tracing.store import ArchiveError

COMPARISON_VERSION = "observed-result-comparison/1"


@dataclass(frozen=True)
class AnalysisEvidence:
    analysis: dict
    projection: dict | None
    origin: str
    association_verified: bool


def admitted_cases(value):
    """Keep case membership and boundaries explicit instead of guessing dataset identity."""
    try:
        cases = {
            item["turn_id"]: (item["boundary"], tuple(item["event_ids"]))
            for item in value["selected_cases"]
        }
        if len(cases) != len(value["selected_cases"]) or any(
            not isinstance(turn, str)
            or boundary not in {"completed", "cancelled"}
            or not ids
            or any(not isinstance(x, str) for x in ids)
            for turn, (boundary, ids) in cases.items()
        ):
            raise ValueError
        return cases
    except (KeyError, TypeError, ValueError):
        raise ArchiveError(
            "invalid_result", "Invalid analysis case membership"
        ) from None


def counts(value, name, allowed):
    """Missing measurements remain unavailable, not zero."""
    result = value.get(name)
    if result is None:
        return None
    if (
        not isinstance(result, dict)
        or set(result) - set(allowed)
        or any(type(x) is not int or x < 0 for x in result.values())
    ):
        raise ArchiveError("invalid_result", "Invalid analysis count measurement")
    return dict(sorted(result.items()))


def measures(evidence):
    value, projection = evidence.analysis, evidence.projection
    service = None
    if projection is not None:
        selected = {
            event for _, events in admitted_cases(value).values() for event in events
        }
        members = [x for x in projection["operations"] if x["event_id"] in selected]
        if len(members) == len(selected) and all(
            type(x.get("start_ns")) is int
            and type(x.get("end_ns")) is int
            and 0 <= x["start_ns"] <= x["end_ns"]
            for x in members
        ):
            service = sum(x["end_ns"] - x["start_ns"] for x in members)
    return {
        "selected_cases": len(value["selected_cases"]),
        "excluded_cases": len(value["excluded_cases"]),
        "activity_counts": counts(value, "activity_counts", ACTIVITIES.values()),
        "outcome_counts": counts(
            value, "outcome_counts", ("passed", "failed", "unknown")
        ),
        "tool_service_ns": service,
        "workflow_elapsed_ns": None,
        "analysis_runtime_ns": None,
        "cost": None,
    }


def compare(baseline, candidate):
    """Measure same-input algorithm equivalence; cross-run improvement needs separate evidence."""
    for evidence in (baseline, candidate):
        prepare_result(
            evidence.analysis, evidence.analysis.get("input_sha256"), origin="file"
        )
    left, right = baseline.analysis, candidate.analysis
    reasons = []
    for name in (
        "snapshot_id",
        "projection_id",
        "case_scope",
        "allow_incomplete",
        "completeness",
    ):
        if left[name] != right[name]:
            reasons.append(f"different_{name}")
    if not baseline.association_verified or not candidate.association_verified:
        reasons.append("input_association_unverified")
    if admitted_cases(left) != admitted_cases(right):
        reasons.append("different_case_membership")
    if (
        left["status"] != "passed"
        or right["status"] != "passed"
        or not left["selected_cases"]
        or not right["selected_cases"]
    ):
        reasons.append("analysis_unavailable")
    if baseline.projection is not None and candidate.projection is not None:
        if canonical(baseline.projection) != canonical(candidate.projection):
            reasons.append("different_projection_content")
        for name in ("projection_version", "mapping"):
            if baseline.projection[name] != candidate.projection[name]:
                reasons.append(f"different_{name}")
    metrics = [measures(baseline), measures(candidate)]
    if any(
        x["activity_counts"] is None or x["outcome_counts"] is None for x in metrics
    ):
        reasons.append("missing_count_measurements")
    identity = {
        "comparison_version": COMPARISON_VERSION,
        "baseline_digest": digest(canonical(left)),
        "candidate_digest": digest(canonical(right)),
        "baseline_projection_digest": digest(canonical(baseline.projection))
        if baseline.projection is not None
        else None,
        "candidate_projection_digest": digest(canonical(candidate.projection))
        if candidate.projection is not None
        else None,
        "baseline_association_verified": baseline.association_verified,
        "candidate_association_verified": candidate.association_verified,
        "scope": "same_fixed_input_algorithm_results",
    }
    comparable = not reasons
    equality = {
        key: left.get(key) == right.get(key)
        for key in (
            "variants",
            "dfg",
            "start_activities",
            "end_activities",
            "selected_cases",
            "excluded_cases",
            "activity_counts",
            "outcome_counts",
        )
    }
    return {
        "schema_version": 1,
        "comparison_id": digest(canonical(identity).rstrip(b"\n")),
        **identity,
        "status": "compared" if comparable else "not_comparable",
        "reasons": reasons,
        "baseline": {
            "analysis_id": left["analysis_id"],
            "snapshot_id": left["snapshot_id"],
            "projection_id": left["projection_id"],
            "origin": baseline.origin,
            "dependencies": left["dependencies"],
            "metrics": metrics[0],
        },
        "candidate": {
            "analysis_id": right["analysis_id"],
            "snapshot_id": right["snapshot_id"],
            "projection_id": right["projection_id"],
            "origin": candidate.origin,
            "dependencies": right["dependencies"],
            "metrics": metrics[1],
        },
        "equal_fields": equality,
        "results_equivalent": all(equality.values()) if comparable else None,
        "quality_gate": "not_evaluated",
        "optimization_verified": False,
        "llm_used": False,
        "limitations": [
            "Comparison describes the same fixed observed input, not a cross-run performance benchmark",
            "Tool service time is a sum of selected operations, not workflow wall time",
            "No CI quality, cost saving or removable work is inferred; original task outcomes remain independent",
        ],
    }
