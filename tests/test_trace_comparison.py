"""Explicit comparison scope, unavailable measurements and CLI compatibility."""

import json
from dataclasses import replace

import pytest
from test_trace_results import analysis, projection

from engineering_ci.cli import mine
from engineering_ci.mining.comparison import AnalysisEvidence, compare
from engineering_ci.tracing.results import canonical
from engineering_ci.tracing.store import ArchiveError


def evidence(*, legacy=False):
    parent = projection()
    parent["operations"] = [{"event_id": "1", "start_ns": 10, "end_ns": 20}]
    value = analysis(parent, legacy=legacy)
    value.update(
        status="passed",
        selected_cases=[{"turn_id": "t1", "boundary": "completed", "event_ids": ["1"]}],
        activity_counts={"command_execution": 1},
        outcome_counts={"failed": 1},
        variants=[{"activities": ["command_execution"], "cases": 1}],
        dfg=[],
        start_activities={"command_execution": 1},
        end_activities={"command_execution": 1},
    )
    return AnalysisEvidence(value, parent, "file", True)


def test_same_input_cross_backend_comparison_is_not_an_optimization_claim():
    result = compare(evidence(legacy=True), evidence())
    assert result["status"] == "compared" and result["results_equivalent"] is True
    assert result["baseline"]["dependencies"] != result["candidate"]["dependencies"]
    assert (
        not result["optimization_verified"]
        and result["quality_gate"] == "not_evaluated"
    )
    assert result["baseline"]["metrics"]["tool_service_ns"] == 10
    assert result["baseline"]["metrics"]["workflow_elapsed_ns"] is None
    assert result == compare(evidence(legacy=True), evidence())


def test_statistics_difference_does_not_become_a_fake_ci_failure_or_pass():
    other = evidence()
    other.analysis["outcome_counts"] = {"passed": 1}
    result = compare(evidence(), other)
    assert result["status"] == "compared" and result["results_equivalent"] is False
    assert (
        not result["equal_fields"]["outcome_counts"]
        and not result["optimization_verified"]
    )


def test_unverified_files_are_not_comparable_even_if_declared_ids_match():
    left = evidence()
    result = compare(replace(left, projection=None, association_verified=False), left)
    assert (
        result["status"] == "not_comparable"
        and "input_association_unverified" in result["reasons"]
    )
    assert result["results_equivalent"] is None


def test_changed_case_membership_is_reported_without_guessing_correspondence():
    other = evidence()
    other.analysis["selected_cases"][0]["event_ids"] = ["other"]
    result = compare(evidence(), other)
    assert (
        result["status"] == "not_comparable"
        and "different_case_membership" in result["reasons"]
    )


def test_matching_declared_id_cannot_hide_changed_projection_content():
    candidate = evidence()
    candidate.projection["operations"][0]["end_ns"] = 99
    result = compare(evidence(), candidate)
    assert result["status"] == "not_comparable"
    assert "different_projection_content" in result["reasons"]


def test_unavailable_analysis_never_passes_comparison():
    parent = projection()
    unavailable = AnalysisEvidence(analysis(parent), parent, "file", True)
    result = compare(unavailable, unavailable)
    assert (
        result["status"] == "not_comparable"
        and "analysis_unavailable" in result["reasons"]
    )


def test_missing_measurements_remain_unknown_and_cannot_establish_equivalence():
    other = evidence()
    other.analysis.pop("outcome_counts")
    result = compare(evidence(), other)
    assert (
        result["status"] == "not_comparable"
        and result["candidate"]["metrics"]["outcome_counts"] is None
    )


@pytest.mark.parametrize("bad", [True, -1, "SECRET"])
def test_invalid_counts_are_safe_structured_errors(bad):
    other = evidence()
    other.analysis["outcome_counts"] = {"passed": bad}
    with pytest.raises(ArchiveError) as error:
        compare(evidence(), other)
    assert "SECRET" not in str(error.value)


def test_file_comparison_does_not_import_or_rerun_backend(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(mine, "analyze", lambda *a, **kw: pytest.fail("rerun mining"))
    left, right = evidence(legacy=True), evidence()
    parent, baseline, candidate = [
        tmp_path / x for x in ("projection.json", "baseline.json", "candidate.json")
    ]
    parent.write_bytes(canonical(left.projection))
    baseline.write_bytes(canonical(left.analysis))
    candidate.write_bytes(canonical(right.analysis))
    output = tmp_path / "comparison.json"
    assert (
        mine.main(
            [
                "compare",
                str(baseline),
                str(candidate),
                "--baseline-projection",
                str(parent),
                "--candidate-projection",
                str(parent),
                "--out",
                str(output),
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["results_equivalent"] is True
    assert json.loads(output.read_bytes())["optimization_verified"] is False


def test_missing_input_association_still_saves_non_success_comparison(tmp_path, capsys):
    source = tmp_path / "analysis.json"
    source.write_bytes(canonical(evidence().analysis))
    assert (
        mine.main(
            [
                "compare",
                str(source),
                str(source),
                "--out",
                str(tmp_path / "output.json"),
                "--json",
            ]
        )
        == 3
    )
    assert json.loads(capsys.readouterr().out)["status"] == "not_comparable"


def test_analysis_must_match_original_projection_bytes(tmp_path, capsys):
    value = evidence()
    source, parent = tmp_path / "analysis.json", tmp_path / "projection.json"
    source.write_bytes(canonical(value.analysis))
    parent.write_bytes(canonical(value.projection) + b"\n")
    assert (
        mine.main(
            [
                "compare",
                str(source),
                str(source),
                "--baseline-projection",
                str(parent),
                "--candidate-projection",
                str(parent),
                "--out",
                str(tmp_path / "no-output.json"),
                "--json",
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["status"] == "identity_conflict"
