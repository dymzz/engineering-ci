"""Delegate discovery of registered step attempts to the existing Rust4PM engine."""

from collections import Counter
from importlib.metadata import version

from ..workflow_core.documents import digest
from .analysis import discover


def analyze(log):
    """Analysis success describes data processing, independently of the original tool result."""
    variants, edges, starts, ends = discover(log["cases"])
    dependencies = {"r4pm": version("r4pm")}
    identity = {
        "event_log_id": log["event_log_id"],
        "analysis_version": "workflow-step-variants/1",
        "engine": "rust4pm",
        "dependencies": dependencies,
    }
    members = [item for case in log["cases"] for item in case["operations"]]
    return {
        "schema_version": 1,
        "kind": "workflow-analysis",
        "analysis_id": digest(identity),
        **identity,
        "status": "passed" if members else "unavailable",
        "reason": None if members else "No complete observed step cases are available",
        "source": {
            key: log[key]
            for key in (
                "execution_id",
                "state_sha256",
                "observations_sha256",
                "evidence_sha256",
            )
        },
        "candidate_source": log["source"],
        "target": log["target"],
        "case_scope": log["case_scope"],
        "completeness": log["completeness"],
        "recorded_result": log["recorded_result"],
        "current_input_validity": "not_checked",
        "selected_cases": [
            {
                "case_id": case["case_id"],
                "event_ids": [item["event_id"] for item in case["operations"]],
            }
            for case in log["cases"]
        ],
        "excluded_cases": log["excluded_cases"],
        "workflow_dependencies": log["dependencies"],
        "activity_counts": dict(
            sorted(Counter(item["activity"] for item in members).items())
        ),
        "outcome_counts": dict(
            sorted(
                Counter(
                    item["attributes"]["engineering_ci:outcome"] for item in members
                ).items()
            )
        ),
        "check_execution_counts": {
            "actual_executions": sum(
                item["attributes"]["engineering_ci:actual_check_executed"] is True
                for item in members
            ),
            "reused_evidence": sum(
                item["attributes"]["engineering_ci:evidence_reused"] is True
                for item in members
            ),
            "execution_unknown": sum(
                item["attributes"]["engineering_ci:actual_check_executed"] is None
                for item in members
            ),
        },
        "variants": [
            {"activities": list(key), "cases": int(count)}
            for key, count in sorted(variants.items())
        ],
        "dfg": [
            {"from": left, "to": right, "count": int(count)}
            for (left, right), count in sorted(edges.items())
        ],
        "start_activities": dict(starts),
        "end_activities": dict(ends),
        "time_scope": log["time_scope"],
        "individual_check_duration_ns": None,
        "dagger_cache_hit": None,
        "cost": None,
        "optimization_verified": False,
        "llm_used": False,
        "limitations": [
            "Cases contain attempts of one step; independent steps have no inferred total order",
            "Batch wrapper timings are not individual check durations",
            "Analysis does not approve a candidate or replace original quality gates",
        ],
    }
