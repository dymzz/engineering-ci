"""Real optional Rust4PM imports/discovery over registered-operation fixtures."""

import json

from test_checks import checkout as check_checkout
from test_workflow_event_log import host
from test_workflow_observations import prepared

from engineering_ci.cli import main as cli
from engineering_ci.mining.workflow import analyze
from engineering_ci.workflow_adapters.event_log import event_log, export_xes
from engineering_ci.workflow_adapters.evidence import execution_evidence

checkout = check_checkout


def test_real_rust4pm_keeps_attempt_variants_and_never_infers_cross_step_edges(
    checkout,
):
    import r4pm
    from r4pm import bindings

    runtime, directory, _, calls = prepared(checkout)
    runtime.resume(directory)
    runtime.resume(directory)
    log = event_log(execution_evidence(directory, runtime.catalog, checkout))
    before = r4pm.list_items()
    result = analyze(log)
    assert result["dependencies"] == {"r4pm": "0.6.2"}
    assert len(result["variants"]) == 5
    assert all(
        item["activities"][0] == item["activities"][1] for item in result["variants"]
    )
    assert all(edge["from"] == edge["to"] for edge in result["dfg"])
    assert result["check_execution_counts"] == {
        "actual_executions": 5,
        "reused_evidence": 5,
        "execution_unknown": 0,
    }
    assert len(calls) == 5 and r4pm.list_items() == before
    path = checkout / ".test-tools/cases.xes"
    path.write_bytes(export_xes(log))
    identifiers = []
    try:
        imported = r4pm.import_item("EventLog", str(path))
        identifiers.append(imported)
        projection = bindings.log_to_activity_projection(imported)
        identifiers.append(projection)
        assert sorted(
            (item["activities"], item["count"])
            for item in bindings.get_variants(projection)
        ) == sorted((item["activities"], item["cases"]) for item in result["variants"])
    finally:
        for identifier in reversed(identifiers):
            r4pm.remove_item(identifier)
    assert r4pm.list_items() == before


def test_real_analysis_cli_returns_json_but_original_failures_remain_failures(
    checkout, monkeypatch, capsys
):
    runtime, directory, _, calls = prepared(checkout, fail=True)
    state = runtime.resume(directory)
    capsys.readouterr()
    host(checkout, runtime, monkeypatch)
    output = checkout / ".test-tools/analysis.json"
    xes = checkout / ".test-tools/cases.xes"
    assert (
        cli(
            [
                "mine",
                "workflow",
                str(directory),
                "--out",
                str(output),
                "--xes-out",
                str(xes),
                "--json",
            ]
        )
        == 0
    )
    result = json.loads(output.read_text())
    assert result["kind"] == "workflow-analysis" and result["status"] == "passed"
    assert result["recorded_result"]["exit_code"] == state["exit_code"] == 7
    assert result["recorded_result"]["status"] == "failed"
    assert result["outcome_counts"] == {"failed": 5}
    assert len(calls) == 5 and xes.exists()
    assert json.loads(capsys.readouterr().out)["xes_saved"]
