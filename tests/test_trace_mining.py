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


def test_offline_xes_exports_verified_cases_and_preserves_unknown_and_exclusions(
    tmp_path, monkeypatch, capsys
):
    from xml.etree import ElementTree as ET

    from test_trace_candidates import evidence

    from engineering_ci.tracing import store
    from engineering_ci.tracing.results import canonical

    source = evidence()
    analysis, projected = tmp_path / "analysis.json", tmp_path / "projection.json"
    analysis.write_bytes(canonical(source.analysis))
    projected.write_bytes(canonical(source.projection))
    output = tmp_path / "cases.xes"
    monkeypatch.setattr(
        store,
        "open_store",
        lambda *args: pytest.fail("Offline export must not connect to PostgreSQL"),
    )
    monkeypatch.setitem(sys.modules, "r4pm", None)
    args = [
        "export-xes",
        str(analysis),
        "--projection",
        str(projected),
        "--out",
        str(output),
        "--json",
    ]
    assert mine.main(args) == 0
    operation = json.loads(capsys.readouterr().out)
    assert operation["cases"] == 3 and operation["excluded_cases"] == 2
    assert operation["completeness"] == "unknown" and operation["xes_saved"]
    ns = {"x": "http://www.xes-standard.org/"}
    log = ET.parse(output).getroot()
    assert len(log.findall("x:trace", ns)) == 3
    assert len(log.findall("x:trace/x:event", ns)) == 9
    assert (
        log.find("x:string[@key='engineering_ci:completeness']", ns).get("value")
        == "unknown"
    )
    assert len(log.findall("x:trace/x:event/x:string[@value='failed']", ns)) == 2
    before = output.read_bytes()
    assert mine.main(args) == 2
    assert (
        json.loads(capsys.readouterr().out)["status"] == "output_exists"
        and output.read_bytes() == before
    )


def test_xes_requires_original_projection_and_matching_stored_discovery(
    tmp_path, capsys
):
    from test_trace_candidates import evidence

    from engineering_ci.tracing.results import canonical

    source = evidence()
    analysis, projected = tmp_path / "analysis.json", tmp_path / "projection.json"
    analysis.write_bytes(canonical(source.analysis))
    source.projection["operations"][0]["outcome"] = "passed"
    projected.write_bytes(canonical(source.projection))
    output = tmp_path / "cases.xes"
    assert (
        mine.main(
            [
                "export-xes",
                str(analysis),
                "--projection",
                str(projected),
                "--out",
                str(output),
                "--json",
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["status"] == "identity_conflict"
    assert not output.exists()


def test_xes_serializer_uses_real_times_and_never_copies_raw_content():
    from engineering_ci.mining.analysis import cases_xes

    selected, _ = select_cases(projection())
    selected[0]["operations"][0].update(command="SECRET", stdout="PRIVATE")
    document = cases_xes(
        selected,
        analysis={
            "analysis_id": "a" * 64,
            "snapshot_id": "b" * 64,
            "projection_id": "c" * 64,
            "input_sha256": "d" * 64,
            "completeness": "unknown",
            "token": "SECRET",
        },
    )
    assert b"SECRET" not in document and b"PRIVATE" not in document
    assert b"1970-01-01T00:00:00.000000200Z" in document
    assert b'key="engineering_ci:outcome" value="failed"' in document


@pytest.mark.parametrize("failure", [False, True])
def test_one_analysis_can_export_xes_without_repeating_discovery(
    tmp_path, monkeypatch, capsys, failure
):
    from test_trace_candidates import evidence

    from engineering_ci.tracing.results import canonical

    source, calls = evidence(), []
    projected, analysis, output = (
        tmp_path / "projection.json",
        tmp_path / "analysis.json",
        tmp_path / "cases.xes",
    )
    projected.write_bytes(canonical(source.projection))

    def analyzed(*args, **kwargs):
        calls.append(1)
        return source.analysis

    original = mine.write_new_file

    def save(path, chunks):
        if path.suffix == ".xes" and failure:
            raise OSError("SECRET filesystem diagnostic")
        return original(path, chunks)

    monkeypatch.setattr(mine, "analyze", analyzed)
    monkeypatch.setattr(mine, "write_new_file", save)
    code = mine.main(
        [str(projected), "--out", str(analysis), "--xes-out", str(output), "--json"]
    )
    result = json.loads(capsys.readouterr().out)
    assert len(calls) == 1 and analysis.exists()
    if failure:
        assert code == 2 and result["analysis_saved"] and result["xes_saved"] is False
        assert "SECRET" not in repr(result) and not output.exists()
        assert result["next_actions"][0][:3] == ["engineering-ci", "mine", "export-xes"]
    else:
        assert code == 0 and output.exists() and result["xes"]["saved"]


def test_unavailable_analysis_with_xes_request_keeps_original_unavailable_status(
    tmp_path, monkeypatch, capsys
):
    from test_trace_candidates import evidence

    from engineering_ci.mining import analysis as implementation
    from engineering_ci.tracing.results import canonical, digest

    source = evidence().projection
    source["turns"] = {}
    raw = canonical(source)
    projected, output, xes = (
        tmp_path / "projection.json",
        tmp_path / "analysis.json",
        tmp_path / "cases.xes",
    )
    projected.write_bytes(raw)
    monkeypatch.setattr(implementation, "discover", lambda cases: ({}, {}, {}, {}))
    monkeypatch.setattr(implementation, "version", lambda _: "0.6.2")
    result = implementation.analyze(source, digest(raw))
    monkeypatch.setattr(mine, "analyze", lambda *args, **kwargs: result)
    assert (
        mine.main(
            [str(projected), "--out", str(output), "--xes-out", str(xes), "--json"]
        )
        == 3
    )
    operation = json.loads(capsys.readouterr().out)
    assert operation["analysis_status"] == "unavailable" and operation["analysis_saved"]
    assert not xes.exists()
