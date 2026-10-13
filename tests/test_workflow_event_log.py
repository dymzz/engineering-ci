"""Verified check facts, parallel-safe cases and offline export failure semantics."""

import copy
import json
from xml.etree import ElementTree as ET

import pytest
from test_checks import checkout as check_checkout
from test_workflow_observations import prepared

from engineering_ci.cli import main as cli
from engineering_ci.cli import mine_workflow
from engineering_ci.workflow_adapters.event_log import event_log, export_xes
from engineering_ci.workflow_adapters.evidence import check_details, execution_evidence
from engineering_ci.workflow_core.documents import WorkflowError, read, write
from engineering_ci.workflow_core.observations import history

checkout = check_checkout
NS = {"x": "http://www.xes-standard.org/"}


def parsed(data):
    root = ET.fromstring(data)
    return root, root.findall("x:trace", NS), root.findall("x:trace/x:event", NS)


def facts(node):
    return {item.get("key"): item.get("value") for item in node if item.get("key")}


def test_only_allowlisted_typed_facts_are_exposed_without_commands_or_secrets():
    entry = {
        "identity": {
            "command": ["echo", "PRIVATE"],
            "root": "PRIVATE",
            "inputs": {"secret": "PRIVATE"},
            "parameters": {
                "coverage": False,
                "junit": "reports/core.xml",
                "token": "PRIVATE",
            },
            "tools": {
                "python": {"version": "3.14.3", "executable": "PRIVATE"},
                "uv": {"version": "0.12.23"},
                "git": {"version": "PRIVATE"},
                "token": {"version": "PRIVATE"},
            },
            "environment": {
                "platform": "Linux",
                "machine": "x86_64",
                "TOKEN": "PRIVATE",
            },
            "executor": {"backend": "dagger", "cli": "PRIVATE"},
        },
        "counts": {
            "tests": 2,
            "failures": 1,
            "errors": -1,
            "skipped": True,
            "secret": "PRIVATE",
        },
    }
    result = check_details(entry)
    assert result["parameters"] == {"coverage": False, "junit": "reports/core.xml"}
    assert result["tool_versions"] == {"python": "3.14.3", "uv": "0.12.23"}
    assert result["environment"] == {
        "platform": "Linux",
        "machine": "x86_64",
        "backend": "dagger",
    }
    assert result["counts"] == {"tests": 2, "failures": 1}
    assert "PRIVATE" not in json.dumps(result)
    entry["identity"]["parameters"]["junit"] = "../PRIVATE.xml"
    assert "junit" not in check_details(entry)["parameters"]


def test_actual_registered_tools_and_batch_survive_xes_without_serializing_parallel_steps(
    checkout,
):
    runtime, directory, _, calls = prepared(checkout)
    runtime.resume(directory)
    view = execution_evidence(directory, runtime.catalog, checkout)
    batches = {row["batch"]["id"] for row in view["items"]}
    assert len(batches) == 1 and all(row["batch"]["size"] == 5 for row in view["items"])
    log = event_log(view)
    assert len(log["cases"]) == 5 and not log["excluded_cases"]
    root, cases, events = parsed(export_xes(log))
    assert len(cases) == len(events) == 5 and len(calls) == 5
    assert all(len(case.findall("x:event", NS)) == 1 for case in cases)
    attrs = [facts(event) for event in events]
    assert {item["concept:name"] for item in attrs} == {
        row["tool_id"] for row in view["items"]
    }
    assert all(item["engineering_ci:actual_check_executed"] == "true" for item in attrs)
    assert all(item["engineering_ci:tool_version:python"] == "3.14.3" for item in attrs)
    assert all(
        item["engineering_ci:time_scope"] == "adapter_invocation" for item in attrs
    )
    assert all(
        "time:timestamp" in item
        and "engineering_ci:individual_check_duration_ns" not in item
        for item in attrs
    )
    assert facts(root)["engineering_ci:completeness"] == "complete"


def test_resume_appends_attempts_and_reuse_without_rerunning_checks(checkout):
    runtime, directory, _, calls = prepared(checkout)
    runtime.resume(directory)
    before = event_log(execution_evidence(directory, runtime.catalog, checkout))
    runtime.resume(directory)
    after = event_log(execution_evidence(directory, runtime.catalog, checkout))
    assert before["event_log_id"] != after["event_log_id"]
    assert len(calls) == 5 and len(after["cases"]) == 5
    for case in after["cases"]:
        attrs = [item["attributes"] for item in case["operations"]]
        assert [item["engineering_ci:attempt"] for item in attrs] == [1, 2]
        assert attrs[1]["engineering_ci:actual_check_executed"] is False
        assert attrs[1]["engineering_ci:evidence_reused"] is True
        assert (
            attrs[0]["engineering_ci:batch_id"] != attrs[1]["engineering_ci:batch_id"]
        )


def test_legacy_observations_have_unknown_batch_without_guessing(checkout):
    runtime, directory, _, _ = prepared(checkout)
    runtime.resume(directory)
    for path in (directory / "observations").glob("*/*.json"):
        value = read(path)
        value.pop("batch_id", None)
        value.pop("batch_size", None)
        write(path, value, replace=True)
    view = execution_evidence(directory, runtime.catalog, checkout)
    assert all(row["batch"] is None for row in view["items"])
    _, _, events = parsed(export_xes(event_log(view)))
    assert all("engineering_ci:batch_id" not in facts(item) for item in events)


def test_corrupt_resource_excludes_case_and_changes_event_log_identity(checkout):
    runtime, directory, _, _ = prepared(checkout)
    runtime.resume(directory)
    view = execution_evidence(directory, runtime.catalog, checkout)
    before = event_log(view)
    pytest_row = next(item for item in view["items"] if item["step_id"] == "pytest")
    resource = pytest_row["check"]["resources"][0]
    (checkout / resource["path"]).write_bytes(b"broken")
    after = event_log(execution_evidence(directory, runtime.catalog, checkout))
    assert before["event_log_id"] != after["event_log_id"]
    assert len(after["cases"]) == 4
    assert after["excluded_cases"][0]["reason"] == "original_check_evidence_unavailable"
    assert runtime.status(directory)["status"] == "passed"


def test_missing_observations_require_explicit_admission_not_fabricated_end(checkout):
    runtime, directory, _, _ = prepared(checkout)
    state = runtime.resume(directory)
    returned = next(
        event for event in history(directory, state)[0] if event["event"] == "returned"
    )
    path = (
        directory
        / "observations"
        / returned["session_id"]
        / f"{returned['sequence']:08d}.json"
    )
    path.unlink()
    view = execution_evidence(directory, runtime.catalog, checkout)
    with pytest.raises(WorkflowError, match="--allow-incomplete"):
        event_log(view)
    log = event_log(view, allow_incomplete=True)
    assert log["completeness"] == "incomplete" and len(log["cases"]) == 4
    assert log["excluded_cases"][0]["reason"] == "missing_attempt_boundary"


def test_return_cannot_claim_another_batch(checkout):
    runtime, directory, _, _ = prepared(checkout)
    state = runtime.resume(directory)
    event = next(
        item for item in history(directory, state)[0] if item["event"] == "returned"
    )
    path = (
        directory
        / "observations"
        / event["session_id"]
        / f"{event['sequence']:08d}.json"
    )
    event["batch_id"] = "f" * 32
    write(path, event, replace=True)
    with pytest.raises(WorkflowError, match="different adapter batch"):
        execution_evidence(directory, runtime.catalog, checkout)


def test_changed_parameters_and_actual_versions_are_distinct_input_identities(
    checkout, monkeypatch
):
    from engineering_ci import checks

    runtime, directory, _, _ = prepared(checkout)
    runtime.resume(directory)
    before = execution_evidence(directory, runtime.catalog, checkout)
    monkeypatch.setattr(
        checks, "preflight", lambda *args: {"python": {"version": "3.14.4"}}
    )
    runtime.resume(directory)
    after = execution_evidence(directory, runtime.catalog, checkout)
    new = [row for row in after["items"] if row["attempt"] == 2]
    assert all(row["check"]["actual_check_executed"] for row in new)
    for row in new:
        previous = next(
            item for item in before["items"] if item["step_id"] == row["step_id"]
        )
        assert (
            row["check"]["identity_parts"]["tools"]
            != previous["check"]["identity_parts"]["tools"]
        )
        assert row["check"]["details"]["tool_versions"]["python"] == "3.14.4"
    left = check_details({"identity": {"parameters": {"coverage": False}}})
    right = check_details({"identity": {"parameters": {"coverage": True}}})
    assert left["parameters"] != right["parameters"]


def host(checkout, runtime, monkeypatch):
    monkeypatch.setattr(mine_workflow, "ROOT", checkout)
    monkeypatch.setattr(mine_workflow, "catalog", lambda root: runtime.catalog)


def test_export_cli_has_no_database_sdk_or_discovery_dependency(
    checkout, monkeypatch, capsys
):
    runtime, directory, _, calls = prepared(checkout, fail=True)
    state = runtime.resume(directory)
    capsys.readouterr()
    host(checkout, runtime, monkeypatch)
    from engineering_ci.mining import analysis
    from engineering_ci.tracing import store

    def unexpected(*args, **kwargs):
        pytest.fail("Export must not run discovery, open PostgreSQL or invoke tools")

    monkeypatch.setattr(analysis, "discover", unexpected)
    monkeypatch.setattr(store, "open_store", unexpected)
    output = checkout / ".test-tools/cases.xes"
    assert (
        cli(
            [
                "mine",
                "export-xes",
                "--execution",
                str(directory),
                "--out",
                str(output),
                "--json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["recorded_result"]["exit_code"] == state["exit_code"] == 7
    assert len(calls) == 5 and output.exists()
    assert (
        result["case_scope"] == "workflow-step" and not result["optimization_verified"]
    )
    assert (
        cli(
            [
                "mine",
                "export-xes",
                "--execution",
                str(directory),
                "--out",
                str(output),
                "--json",
            ]
        )
        == 2
    )


def test_export_identity_pin_and_source_overwrite_are_rejected(
    checkout, monkeypatch, capsys
):
    runtime, directory, _, _ = prepared(checkout)
    runtime.resume(directory)
    capsys.readouterr()
    host(checkout, runtime, monkeypatch)
    output = checkout / ".test-tools/cases.xes"
    args = [
        "mine",
        "export-xes",
        "--execution",
        str(directory),
        "--out",
        str(output),
        "--json",
    ]
    assert cli([*args, "--event-log-id", "f" * 64]) == 2
    assert "evidence changed" in capsys.readouterr().out and not output.exists()
    assert (
        cli(
            [
                "mine",
                "export-xes",
                "--execution",
                str(directory),
                "--out",
                str(directory / "new.xes"),
                "--json",
            ]
        )
        == 2
    )
    assert not (directory / "new.xes").exists()


def test_analysis_xes_failure_keeps_report_and_identity_bound_recovery(
    checkout, monkeypatch, capsys
):
    from engineering_ci.mining import workflow as mining

    runtime, directory, _, _ = prepared(checkout)
    runtime.resume(directory)
    capsys.readouterr()
    host(checkout, runtime, monkeypatch)
    monkeypatch.setattr(
        mining,
        "analyze",
        lambda log: {"status": "passed", "analysis_id": "a" * 64, "engine": "rust4pm"},
    )
    monkeypatch.setattr(
        mine_workflow,
        "export_xes",
        lambda log: (_ for _ in ()).throw(OSError("PRIVATE")),
    )
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
        == 2
    )
    result = json.loads(capsys.readouterr().out)
    assert (
        result["analysis_saved"]
        and result["analysis_status"] == "passed"
        and output.exists()
    )
    assert (
        not result["xes_saved"]
        and not xes.exists()
        and "PRIVATE" not in json.dumps(result)
    )
    assert "--event-log-id" in result["next_actions"][0]


def test_no_cases_missing_engine_and_invalid_time_are_not_success(
    checkout, monkeypatch, capsys
):
    from engineering_ci.mining import workflow as mining

    runtime, directory, _, _ = prepared(checkout)
    runtime.resume(directory)
    capsys.readouterr()
    host(checkout, runtime, monkeypatch)
    view = execution_evidence(directory, runtime.catalog, checkout)
    damaged = copy.deepcopy(view)
    damaged["items"][0]["started_ns"] = damaged["items"][0]["ended_ns"] + 1
    assert len(event_log(damaged)["cases"]) == 4

    def missing(log):
        raise ImportError("PRIVATE")

    monkeypatch.setattr(mining, "analyze", missing)
    output = checkout / ".test-tools/no-analysis.json"
    assert (
        cli(["mine", "workflow", str(directory), "--out", str(output), "--json"]) == 2
    )
    assert (
        json.loads(capsys.readouterr().out)["status"] == "missing_tool"
        and not output.exists()
    )
    empty = event_log({**view, "items": []})
    assert len(empty["excluded_cases"]) == 5
    with pytest.raises(WorkflowError, match="No complete"):
        export_xes(empty)


def test_unavailable_analysis_is_saved_with_nonzero_status(
    checkout, monkeypatch, capsys
):
    from engineering_ci.mining import workflow as mining

    runtime, directory, _, calls = prepared(checkout)
    host(checkout, runtime, monkeypatch)
    monkeypatch.setattr(
        mining,
        "analyze",
        lambda log: {
            "status": "unavailable",
            "analysis_id": "a" * 64,
            "engine": "rust4pm",
        },
    )
    output = checkout / ".test-tools/unavailable.json"
    assert (
        cli(["mine", "workflow", str(directory), "--out", str(output), "--json"]) == 3
    )
    result = json.loads(capsys.readouterr().out)
    assert (
        result["analysis_saved"]
        and result["cases"] == 0
        and result["excluded_cases"] == 5
    )
    assert result["recorded_result"]["status"] == "not_run" and not calls
