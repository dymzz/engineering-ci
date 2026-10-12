"""Actual optional PM4Py algorithms; explicit invocation never skips dependencies."""

from engineering_ci.mining.analysis import analyze
from engineering_ci.tracing.projection import PROJECTION_VERSION


def sample():
    operations, turns = [], {}
    for turn in ("a", "b"):
        turns[turn] = {"started": True, "terminal": "completed"}
        for i, activity in enumerate(
            ("command_execution", "file_change", "command_execution")
        ):
            operations.append(
                {
                    "turn_id": turn,
                    "activity": activity,
                    "event_id": str(i),
                    "start_ns": i * 1_000_000_000,
                    "end_ns": i * 1_000_000_000 + 500_000_000,
                    "outcome": "failed" if i == 0 else "passed",
                }
            )
    return {
        "projection_version": PROJECTION_VERSION,
        "projection_id": "projection-id",
        "snapshot_id": "snapshot-id",
        "completeness": "unknown",
        "operations": operations,
        "turns": turns,
    }


def test_actual_pm4py_variants_dfg_and_deterministic_result():
    value = sample()
    result = analyze(value, "0" * 64)
    assert result == analyze(value, "0" * 64)
    assert result["variants"] == [
        {
            "activities": ["command_execution", "file_change", "command_execution"],
            "cases": 2,
        }
    ]
    assert result["dfg"] == [
        {"from": "command_execution", "to": "file_change", "count": 2},
        {"from": "file_change", "to": "command_execution", "count": 2},
    ]
    assert result["outcome_counts"] == {"failed": 2, "passed": 4}
    assert result["llm_used"] is False and result["completeness"] == "unknown"


def test_no_valid_cases_are_unavailable_and_no_algorithm_success_fabricated():
    value = sample()
    value["turns"] = {}
    result = analyze(value, "0" * 64)
    assert (
        result["status"] == "unavailable"
        and not result["variants"]
        and not result["dfg"]
    )
    assert len(result["excluded_cases"]) == 2


def test_analysis_identity_changes_with_input_and_policy():
    assert (
        analyze(sample(), "0" * 64)["analysis_id"]
        != analyze(sample(), "1" * 64)["analysis_id"]
    )
    assert (
        analyze(sample(), "0" * 64)["analysis_id"]
        != analyze(sample(), "0" * 64, allow_incomplete=True)["analysis_id"]
    )
