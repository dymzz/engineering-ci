"""Candidate provenance, incomplete evidence and review-only failure semantics."""

import json
import sys
from dataclasses import replace

import pytest
from test_trace_results import analysis, projection

from engineering_ci.cli import mine
from engineering_ci.mining.analysis import analysis_identity
from engineering_ci.mining.candidates import candidates
from engineering_ci.mining.comparison import AnalysisEvidence
from engineering_ci.tracing.results import canonical, digest
from engineering_ci.tracing.store import ArchiveError


def evidence(*, legacy=False):
    """Three admitted turns and two excluded turns with explicit expected discovery."""
    parent = projection()
    parent["turns"] = {
        turn: {
            "started": True,
            "terminal": "cancelled" if turn == "t3" else "completed",
        }
        for turn in ("t1", "t2", "t3", "open", "overlap")
    }
    parent["turns"]["open"]["terminal"] = None
    for turn, last in (
        ("t1", "file_change"),
        ("t2", "mcp_tool_call"),
        ("t3", "file_change"),
    ):
        for i, activity in enumerate(("command_execution", "command_execution", last)):
            parent["operations"].append(
                {
                    "turn_id": turn,
                    "event_id": str(len(parent["operations"]) + 1),
                    "activity": activity,
                    "start_ns": i * 100,
                    "end_ns": i * 100 + 50,
                    "outcome": ("unknown" if turn == "t3" else "failed")
                    if i == 0
                    else "passed",
                }
            )
    parent["operations"] += [
        {
            "turn_id": "open",
            "event_id": "10",
            "activity": "command_execution",
            "start_ns": 0,
            "end_ns": 50,
            "outcome": "failed",
        },
        *[
            {
                "turn_id": "overlap",
                "event_id": str(i),
                "activity": "command_execution",
                "start_ns": 0,
                "end_ns": 50,
                "outcome": "unknown",
            }
            for i in (11, 12)
        ],
    ]
    value = analysis(parent, legacy=legacy)
    value.update(
        status="passed",
        selected_cases=[
            {
                "turn_id": turn,
                "boundary": parent["turns"][turn]["terminal"],
                "event_ids": [str(i) for i in range(start, start + 3)],
            }
            for turn, start in (("t1", 1), ("t2", 4), ("t3", 7))
        ],
        excluded_cases=[
            {
                "turn_id": "open",
                "reason": "open_or_missing_turn_boundary",
                "operations": 1,
            },
            {
                "turn_id": "overlap",
                "reason": "overlap_or_timestamp_tie",
                "operations": 2,
            },
        ],
        variants=[
            {
                "activities": ["command_execution", "command_execution", "file_change"],
                "cases": 2,
            },
            {
                "activities": [
                    "command_execution",
                    "command_execution",
                    "mcp_tool_call",
                ],
                "cases": 1,
            },
        ],
        dfg=[
            {"from": "command_execution", "to": "command_execution", "count": 3},
            {"from": "command_execution", "to": "file_change", "count": 2},
            {"from": "command_execution", "to": "mcp_tool_call", "count": 1},
        ],
        start_activities={"command_execution": 3},
        end_activities={"file_change": 2, "mcp_tool_call": 1},
        activity_counts={"command_execution": 9, "file_change": 2, "mcp_tool_call": 1},
        outcome_counts={"failed": 3, "passed": 6, "unknown": 3},
    )
    return AnalysisEvidence(value, parent, "file", True)


def refreshed(value):
    """Keep test input identity correct when changing the evidence payload."""
    value.analysis["input_sha256"] = digest(canonical(value.projection))
    value.analysis["analysis_id"] = digest(
        canonical(analysis_identity(value.analysis)).rstrip(b"\n")
    )
    return value


def test_candidates_are_referenced_observations_not_approved_optimizations():
    result = candidates(evidence())
    assert result == candidates(evidence())
    assert result["status"] == "reported" and result["total_candidates"] == 3
    repeat, variant, branch = result["candidates"]
    assert [x["kind"] for x in result["candidates"]] == [
        "adjacent_activity_repeat",
        "repeated_variant",
        "observed_successor_branch",
    ]
    assert repeat["occurrences"] == 3 and repeat["case_count"] == 3
    assert variant["occurrences"] == 2 and variant["case_count"] == 2
    assert branch["pattern"]["successor_counts"] == {
        "command_execution": 3,
        "file_change": 2,
        "mcp_tool_call": 1,
    }
    assert set(branch["successor_examples"]) == set(
        branch["pattern"]["successor_counts"]
    )
    assert all(
        x["status"] == "needs_review" and not x["executable"] and x["handler"] is None
        for x in result["candidates"]
    )
    assert (
        not result["optimization_verified"]
        and result["quality_gate"] == "not_evaluated"
        and not result["llm_used"]
    )
    assert all(x["required_evidence"] for x in result["candidates"])
    assert result["source"]["snapshot_id"] == evidence().projection["snapshot_id"]


def test_failures_unknown_and_excluded_cases_remain_visible():
    result = candidates(evidence())
    assert result["completeness"] == "unknown" and result["excluded_cases"] == 2
    assert result["coverage"] == {
        "projected_operations": 12,
        "selected_operations": 9,
        "excluded_operations": 3,
        "selected_outcome_counts": {"failed": 2, "passed": 6, "unknown": 1},
        "all_outcome_counts": {"failed": 3, "passed": 6, "unknown": 3},
        "exclusion_counts": {
            "open_or_missing_turn_boundary": 1,
            "overlap_or_timestamp_tie": 1,
        },
    }
    assert (
        result["candidates"][0]["witnesses"][0]["operations"][0]["outcome"] == "failed"
    )
    assert result["candidates"][0]["witnesses"][2]["boundary"] == "cancelled"


def test_output_limits_preserve_full_counts_and_candidate_identity():
    full = candidates(evidence())
    small = candidates(evidence(), limit=1, evidence_limit=1)
    assert small["total_candidates"] == 3 and small["omitted_candidates"] == 2
    assert small["coverage"] == full["coverage"]
    assert small["candidates"][0]["omitted_witnesses"] == 2
    assert (
        small["candidates"][0]["candidate_id"] == full["candidates"][0]["candidate_id"]
    )
    assert small["report_id"] != full["report_id"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": True},
        {"limit": 0},
        {"limit": 101},
        {"evidence_limit": 0},
        {"evidence_limit": 21},
    ],
)
def test_invalid_limits_fail_without_running_anything(kwargs):
    with pytest.raises(ArchiveError, match="limit"):
        candidates(evidence(), **kwargs)


def test_legacy_analysis_is_compatible_but_retains_its_identity():
    value = candidates(evidence(legacy=True))
    assert value["status"] == "reported" and "pm4py" in value["source"]["dependencies"]
    assert value["report_id"] != candidates(evidence())["report_id"]


def test_missing_association_reports_unavailable_instead_of_a_false_pass():
    value = evidence()
    result = candidates(replace(value, projection=None, association_verified=False))
    assert result["status"] == "unavailable" and not result["candidates"]
    assert (
        result["reasons"] == ["input_association_unverified"]
        and result["coverage"] is None
    )


@pytest.mark.parametrize(
    "field",
    [
        "dfg",
        "variants",
        "selected_cases",
        "excluded_cases",
        "activity_counts",
        "outcome_counts",
    ],
)
def test_changed_model_or_measurements_are_not_used_for_review(field):
    value = evidence()
    if isinstance(value.analysis[field], list):
        value.analysis[field] = []
    else:
        value.analysis[field] = {}
    with pytest.raises(ArchiveError, match="differ"):
        candidates(value)


def test_unique_safe_source_references_are_required():
    value = evidence()
    value.projection["operations"][1]["event_id"] = value.projection["operations"][0][
        "event_id"
    ]
    with pytest.raises(ArchiveError, match="unique"):
        candidates(value)
    value = evidence()
    value.projection["operations"][0]["turn_id"] = "SECRET/password"
    with pytest.raises(ArchiveError) as error:
        candidates(value)
    assert "SECRET" not in str(error.value)


def test_unknown_content_is_not_copied_to_candidate_report():
    value = evidence()
    value.projection["operations"][0].update(command="SECRET", stdout="PRIVATE")
    result = candidates(refreshed(value))
    assert "SECRET" not in json.dumps(result) and "PRIVATE" not in json.dumps(result)


def test_cli_generates_report_without_optional_backend_or_database(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setitem(sys.modules, "r4pm", None)
    monkeypatch.setattr(mine, "analyze", lambda *a, **kw: pytest.fail("rerun backend"))
    value = evidence()
    parent, analysis_path, output = [
        tmp_path / name for name in ("projection.json", "analysis.json", "report.json")
    ]
    parent.write_bytes(canonical(value.projection))
    analysis_path.write_bytes(canonical(value.analysis))
    assert (
        mine.main(
            [
                "candidates",
                str(analysis_path),
                "--projection",
                str(parent),
                "--out",
                str(output),
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["review_status"] == "not_reviewed"
    assert (
        json.loads(output.read_bytes())["candidates"][0]["witnesses"][0]["operations"][
            0
        ]["event_id"]
        == "1"
    )
    assert (
        mine.main(
            [
                "candidates",
                str(analysis_path),
                "--projection",
                str(parent),
                "--out",
                str(output),
                "--json",
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["status"] == "output_exists"


def test_cli_missing_projection_saves_unavailable_report(tmp_path, capsys):
    path, out = tmp_path / "analysis.json", tmp_path / "report.json"
    path.write_bytes(canonical(evidence().analysis))
    assert mine.main(["candidates", str(path), "--out", str(out), "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["status"] == "unavailable"
    assert json.loads(out.read_bytes())["reasons"] == ["input_association_unverified"]


@pytest.mark.parametrize("raw", [b"not json", b"[]", b"{}"])
def test_cli_corrupt_or_wrong_document_is_safe(tmp_path, capsys, raw):
    path = tmp_path / "wrong.json"
    path.write_bytes(raw)
    assert (
        mine.main(
            ["candidates", str(path), "--out", str(tmp_path / "report.json"), "--json"]
        )
        == 2
    )
    assert "Traceback" not in capsys.readouterr().out


def test_cli_refuses_changed_projection_bytes(tmp_path, capsys):
    value = evidence()
    path, parent = tmp_path / "analysis.json", tmp_path / "projection.json"
    path.write_bytes(canonical(value.analysis))
    parent.write_bytes(canonical(value.projection) + b"\n")
    assert (
        mine.main(
            [
                "candidates",
                str(path),
                "--projection",
                str(parent),
                "--out",
                str(tmp_path / "report.json"),
                "--json",
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["status"] == "identity_conflict"


def single_case(activities):
    """Small explicit models for empty reports and bounded long-variant references."""
    parent = projection()
    parent["turns"] = {"one": {"started": True, "terminal": "completed"}}
    parent["operations"] = [
        {
            "turn_id": "one",
            "event_id": str(i),
            "activity": activity,
            "start_ns": i * 100,
            "end_ns": i * 100 + 50,
            "outcome": "passed",
        }
        for i, activity in enumerate(activities)
    ]
    value = analysis(parent)
    if not activities:
        value.update(
            start_activities={},
            end_activities={},
            activity_counts={},
            outcome_counts={},
        )
    else:
        assert all(activity == "command_execution" for activity in activities)
        value.update(
            status="passed",
            selected_cases=[
                {
                    "turn_id": "one",
                    "boundary": "completed",
                    "event_ids": [str(i) for i in range(len(activities))],
                }
            ],
            variants=[{"activities": activities, "cases": 1}],
            dfg=[
                {
                    "from": "command_execution",
                    "to": "command_execution",
                    "count": len(activities) - 1,
                }
            ]
            if len(activities) > 1
            else [],
            start_activities={"command_execution": 1},
            end_activities={"command_execution": 1},
            activity_counts={"command_execution": len(activities)},
            outcome_counts={"passed": len(activities)},
        )
    return AnalysisEvidence(value, parent, "file", True)


def test_no_observations_is_unavailable_but_no_candidate_is_valid_empty_report():
    unavailable = candidates(single_case([]))
    assert unavailable["status"] == "unavailable" and unavailable["reasons"] == [
        "analysis_unavailable"
    ]
    empty = candidates(single_case(["command_execution"]))
    assert empty["status"] == "reported" and empty["total_candidates"] == 0
    assert not empty["optimization_verified"]


def test_long_repeated_variant_keeps_counts_and_explicit_reference_omissions():
    value = single_case(["command_execution"] * 12)
    value.projection["turns"]["two"] = {"started": True, "terminal": "completed"}
    value.projection["operations"] += [
        {
            **operation,
            "turn_id": "two",
            "event_id": str(int(operation["event_id"]) + 12),
        }
        for operation in value.projection["operations"][:]
    ]
    value.analysis["selected_cases"].append(
        {
            "turn_id": "two",
            "boundary": "completed",
            "event_ids": [str(i) for i in range(12, 24)],
        }
    )
    value.analysis["variants"][0]["cases"] = 2
    value.analysis["dfg"][0]["count"] = 22
    value.analysis.update(
        start_activities={"command_execution": 2},
        end_activities={"command_execution": 2},
        activity_counts={"command_execution": 24},
        outcome_counts={"passed": 24},
    )
    result = candidates(refreshed(value), evidence_limit=1)
    repeat, variant = result["candidates"]
    assert (
        repeat["occurrences"] == 22
    )  # Adjacent pairs overlap; not a count of deletable calls.
    assert (
        variant["pattern"]["activity_count"] == 12
        and variant["pattern"]["omitted_activities"] == 4
    )
    assert (
        variant["witnesses"][0]["omitted_operations"] == 4
        and variant["omitted_witnesses"] == 1
    )


def test_incomplete_policy_is_explicit_and_part_of_report_identity():
    value = evidence()
    value.analysis["completeness"] = value.projection["completeness"] = "incomplete"
    with pytest.raises(ArchiveError) as error:
        candidates(value)
    assert error.value.status == "incomplete_input"
    value.analysis["allow_incomplete"] = True
    result = candidates(refreshed(value))
    assert result["completeness"] == "incomplete" and result["allow_incomplete"] is True
    assert result["report_id"] != candidates(evidence())["report_id"]


def test_write_failure_keeps_provenance_and_a_usable_new_output_command(
    tmp_path, monkeypatch, capsys
):
    from engineering_ci.cli import mine_candidates

    value = evidence()
    path, parent = tmp_path / "analysis.json", tmp_path / "projection.json"
    path.write_bytes(canonical(value.analysis))
    parent.write_bytes(canonical(value.projection))
    writer = mine_candidates.write_new_file

    def denied(*args):
        raise PermissionError("SECRET")

    monkeypatch.setattr(mine_candidates, "write_new_file", denied)
    assert (
        mine.main(
            [
                "candidates",
                str(path),
                "--projection",
                str(parent),
                "--limit",
                "2",
                "--evidence-limit",
                "1",
                "--out",
                str(tmp_path / "out.json"),
                "--json",
            ]
        )
        == 2
    )
    failed = json.loads(capsys.readouterr().out)
    assert (
        failed["report_saved"] is False
        and failed["analysis_id"] == value.analysis["analysis_id"]
    )
    assert "SECRET" not in json.dumps(failed)
    retry = failed["next_actions"][0]
    assert retry[retry.index("--out") + 1] != str(tmp_path / "out.json")
    monkeypatch.setattr(mine_candidates, "write_new_file", writer)
    assert mine.main(retry[2:]) == 0
    assert json.loads(capsys.readouterr().out)["report_id"] == failed["report_id"]


@pytest.mark.parametrize("existing", [True, False])
def test_existing_output_and_invalid_limits_precede_database_access(
    tmp_path, monkeypatch, capsys, existing
):
    from engineering_ci.tracing import store

    monkeypatch.setattr(
        store, "open_store", lambda *args: pytest.fail("unnecessary database access")
    )
    out = tmp_path / "out.json"
    if existing:
        out.write_bytes(b"original")
    args = ["candidates", "--analysis-result", "b" * 64, "--out", str(out), "--json"]
    if not existing:
        args += ["--limit", "0"]
    assert mine.main(args) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == ("output_exists" if existing else "invalid_query")
    if existing:
        assert out.read_bytes() == b"original"
        assert result["next_actions"][0][4] == "b" * 64


def test_registered_parent_reference_is_checked_independently():
    from engineering_ci.cli.mine_inputs import registered_evidence

    value = evidence()
    parent = value.projection

    class Catalog:
        def result(self, identifier):
            if identifier == value.analysis["analysis_id"]:
                return {
                    "kind": "analysis",
                    "result_id": identifier,
                    "parent_result_id": parent["projection_id"],
                    "snapshot_id": parent["snapshot_id"],
                    "input_digest": value.analysis["input_sha256"],
                    "origin": "file",
                }
            return {"kind": "analysis", "document_digest": digest(canonical(parent))}

        def document(self, identifier):
            return canonical(
                value.analysis
                if identifier == value.analysis["analysis_id"]
                else parent
            )

    with pytest.raises(ArchiveError) as error:
        registered_evidence(Catalog(), value.analysis["analysis_id"])
    assert error.value.status == "archive_corrupt"
