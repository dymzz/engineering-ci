"""Case admission and CLI semantics without requiring the optional mining stack."""

import json
import sys
from types import SimpleNamespace

import pytest

from engineering_ci.cli import mine
from engineering_ci.mining.analysis import discover, select_cases
from engineering_ci.tracing.projection import PROJECTION_VERSION
from engineering_ci.tracing.store import ArchiveError


def projection(members=None, *, completeness="unknown", terminal="completed"):
    operations = (
        members
        if members is not None
        else [
            {
                "activity": "command_execution",
                "turn_id": "turn-1",
                "event_id": "10",
                "start_ns": 100,
                "end_ns": 200,
                "outcome": "failed",
            },
            {
                "activity": "file_change",
                "turn_id": "turn-1",
                "event_id": "20",
                "start_ns": 300,
                "end_ns": 400,
                "outcome": "passed",
            },
        ]
    )
    return {
        "projection_version": PROJECTION_VERSION,
        "completeness": completeness,
        "operations": operations,
        "turns": {"turn-1": {"started": True, "terminal": terminal}},
    }


def test_failure_and_unknown_completeness_are_included_and_labelled():
    selected, excluded = select_cases(projection())
    assert len(selected) == 1 and not excluded
    assert selected[0]["operations"][0]["outcome"] == "failed"


@pytest.mark.parametrize("start,end", [(150, 250), (100, 200), (300, 200), (None, 400)])
def test_parallel_tied_or_bad_clock_is_not_invented_order(start, end):
    value = projection()
    value["operations"][1].update(start_ns=start, end_ns=end)
    selected, excluded = select_cases(value)
    assert not selected and excluded[0]["reason"] in {
        "overlap_or_timestamp_tie",
        "missing_or_invalid_time",
    }


def test_cancelled_case_remains_an_observed_failure_path():
    selected, _ = select_cases(projection(terminal="cancelled"))
    assert selected[0]["boundary"] == "cancelled"


def test_open_case_is_explained_not_marked_passed():
    selected, excluded = select_cases(projection(terminal=None))
    assert not selected and excluded[0]["reason"] == "open_or_missing_turn_boundary"


def test_incomplete_requires_explicit_analysis_policy():
    value = projection(completeness="incomplete")
    with pytest.raises(ArchiveError) as error:
        select_cases(value)
    assert error.value.status == "incomplete_input"
    assert select_cases(value, allow_incomplete=True)[0]


@pytest.mark.parametrize("value", [{}, [], {"projection_version": "future"}])
def test_invalid_projection_is_explicit(value):
    with pytest.raises(ArchiveError):
        select_cases(value)


def test_mine_cli_preserves_unavailable_and_saved_result(tmp_path, capsys, monkeypatch):
    path, output = tmp_path / "projection.json", tmp_path / "analysis.json"
    path.write_text(json.dumps(projection()))
    monkeypatch.setattr(
        mine,
        "analyze",
        lambda *a, **kw: {
            "status": "unavailable",
            "analysis_id": "abc",
            "selected_cases": [],
            "excluded_cases": [],
            "variants": [],
        },
    )
    assert mine.main([str(path), "--out", str(output), "--json"]) == 3
    assert json.loads(output.read_text())["status"] == "unavailable"
    assert json.loads(capsys.readouterr().out)["exit_code"] == 3


def test_mine_missing_optional_package_is_not_success(tmp_path, capsys, monkeypatch):
    path = tmp_path / "projection.json"
    path.write_text(json.dumps(projection()))

    def missing(*a, **kw):
        raise ImportError("SECRET")

    monkeypatch.setattr(mine, "analyze", missing)
    assert mine.main([str(path), "--out", str(tmp_path / "result.json"), "--json"]) == 2
    value = capsys.readouterr().out
    assert "SECRET" not in value and json.loads(value)["status"] == "missing_tool"


def test_rust_failure_is_safe_and_releases_all_registry_objects(monkeypatch):
    removed = []

    def failed(_):
        raise ValueError("SECRET")

    fake = SimpleNamespace(
        import_item=lambda *a: "log",
        remove_item=removed.append,
        bindings=SimpleNamespace(
            log_to_activity_projection=lambda _: "projection", get_variants=failed
        ),
    )
    monkeypatch.setitem(sys.modules, "r4pm", fake)
    with pytest.raises(ArchiveError) as error:
        discover(select_cases(projection())[0])
    assert error.value.status == "analysis_failed" and "SECRET" not in str(error.value)
    assert removed == ["projection", "log"]
