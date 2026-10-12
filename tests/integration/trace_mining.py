"""Actual optional Rust4PM algorithms; explicit invocation never skips dependencies."""

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


def test_actual_rust4pm_variants_dfg_and_deterministic_result():
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


def test_rust_registry_cleanup_and_boundary_frequencies():
    import r4pm

    before = r4pm.list_items()
    value = sample()
    value["operations"].append(
        {
            "turn_id": "c<&",
            "activity": "file_change",
            "event_id": "extra",
            "start_ns": 10,
            "end_ns": 20,
            "outcome": "unknown",
        }
    )
    value["turns"]["c<&"] = {"started": True, "terminal": "cancelled"}
    result = analyze(value, "0" * 64)
    assert result["start_activities"] == {"command_execution": 2, "file_change": 1}
    assert result["end_activities"] == {"command_execution": 2, "file_change": 1}
    assert result["dependencies"] == {"r4pm": "0.6.2"}
    assert r4pm.list_items() == before
