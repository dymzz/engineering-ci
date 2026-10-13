"""Attempt history, explicit candidate targets and bounded real evidence joins."""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_checks import checkout as check_checkout
from test_checks import fake_process
from test_generated_workflows import Arithmetic, execution, tools
from test_generated_workflows import recipe as arithmetic_recipe
from test_trace_candidates import evidence as trace_evidence
from test_workflow_adapter import native

from engineering_ci.cli import main as cli
from engineering_ci.mining.candidates import candidates
from engineering_ci.pipeline import PIPELINE
from engineering_ci.tracing.store import ArchiveError
from engineering_ci.workflow_adapters.candidates import (
    profile_target,
    selected_source,
    verify_target,
)
from engineering_ci.workflow_adapters.checks import catalog, recipe
from engineering_ci.workflow_adapters.evidence import evidence
from engineering_ci.workflow_core import Runtime, generate, load_generation
from engineering_ci.workflow_core.documents import WorkflowError, digest, read, write
from engineering_ci.workflow_core.observations import history

checkout = check_checkout


def prepared(checkout, *, fail=False, source=False):
    calls = []
    registered = catalog(
        checkout,
        runner=native(
            checkout,
            fake_process(7 if fail else 0, lambda args, kw: calls.append(args)),
        ),
    )
    selected = recipe(["pr"], executor="native")
    provenance = None
    if source:
        observation = trace_evidence()
        provenance = selected_source(
            observation, candidates(observation)["candidates"][0]["candidate_id"]
        )
    path = generate(
        selected,
        registered,
        checkout / ".test-tools/generated",
        source=provenance,
        target=profile_target(selected) if source else None,
    )
    runtime = Runtime(registered)
    directory = runtime.create(path, checkout / ".test-tools/runs")
    return runtime, directory, path, calls


def test_complete_attempt_history_is_safe_and_tied_to_original_failure(tmp_path):
    runtime, directory, adapter = execution(tmp_path, Arithmetic(fail=True))
    state = runtime.resume(directory)
    events, complete, _ = history(directory, state)
    assert complete == "complete" and state["exit_code"] == 7
    assert [event["event"] for event in events] == [
        "session_started",
        "admitted",
        "invoking",
        "returned",
        "skipped",
        "session_finished",
    ]
    received = next(event for event in events if event["event"] == "returned")
    assert received["result"]["status"] == "failed"
    assert received["result_sha256"] == digest(state["steps"]["first"]["result"])
    assert "inputs" not in received and "outputs" not in received["result"]
    assert len(adapter.calls) == 1
    before = sorted(
        (path, path.read_bytes())
        for path in (directory / "observations").glob("*/*.json")
    )
    runtime.status(directory)
    assert before == sorted(
        (path, path.read_bytes())
        for path in (directory / "observations").glob("*/*.json")
    )


def test_observation_write_failure_does_not_change_original_exit(tmp_path, monkeypatch):
    from engineering_ci.workflow_core import observations

    def unavailable(*args, **kwargs):
        raise OSError("Token SECRET cannot write")

    monkeypatch.setattr(observations, "write", unavailable)
    runtime, directory, _ = execution(tmp_path, Arithmetic(fail=True))
    state = runtime.resume(directory)
    assert state["exit_code"] == 7 and runtime.observation_errors > 0
    assert "SECRET" not in json.dumps(state)
    assert history(directory, state)[1] == "incomplete"


def test_native_actual_execution_and_reuse_are_different_facts(checkout):
    runtime, directory, _, calls = prepared(checkout, source=True)
    first = runtime.resume(directory)
    original = evidence(directory, runtime.catalog, checkout)
    assert len(calls) == 5 and all(
        item["check"]["actual_check_executed"] for item in original["items"]
    )
    assert original["source"] and original["target_policy"] == "matched"
    assert not original["approved"] and not original["optimization_verified"]
    assert original["historical_check_identity"] == "unavailable"
    new_path = generate(
        recipe(["pr"], executor="native"),
        runtime.catalog,
        checkout / ".test-tools/generated",
    )
    second_dir = runtime.create(
        new_path, checkout / ".test-tools/runs", reuse_from=directory
    )
    runtime.resume(second_dir)
    reused = evidence(second_dir, runtime.catalog, checkout)
    assert len(calls) == 5
    assert all(
        item["check"]["actual_check_executed"] is False for item in reused["items"]
    )
    assert all(item["check"]["evidence_reused"] for item in reused["items"])
    assert all(
        item["reuse_source"]["execution_id"] == first["execution_id"]
        for item in reused["items"]
    )
    assert all(item["check"]["identity_sha256"] for item in reused["items"])
    assert all(item["check"]["dagger_cache_hit"] is None for item in reused["items"])


def test_each_attempt_survives_failure_reuse_and_changed_input(checkout):
    runtime, directory, _, calls = prepared(checkout, fail=True)
    assert runtime.resume(directory)["exit_code"] == 7
    assert runtime.resume(directory)["exit_code"] == 7 and len(calls) == 5
    (checkout / "tests/test_added.py").write_text("def test_added(): pass\n")
    assert runtime.resume(directory)["exit_code"] == 7 and len(calls) == 6
    result = evidence(directory, runtime.catalog, checkout, limit=100, budget=65536)
    assert (
        len(result["items"]) == 15 and result["observation_completeness"] == "complete"
    )
    pytest_rows = [row for row in result["items"] if row["tool_id"] == "python.pytest"]
    assert [row["check"]["actual_check_executed"] for row in pytest_rows] == [
        True,
        False,
        True,
    ]
    assert (
        pytest_rows[0]["check"]["identity_sha256"]
        != pytest_rows[2]["check"]["identity_sha256"]
    )
    assert all(row["result"]["status"] == "failed" for row in pytest_rows)
    assert result["current_input_validity"] == "not_checked"


def test_corrupt_old_resource_is_unavailable_not_current_pass(checkout):
    runtime, directory, _, _ = prepared(checkout)
    state = runtime.resume(directory)
    report = state["steps"]["pytest"]["result"]["outputs"]["report"]
    (checkout / report["path"]).write_text("{broken")
    result = evidence(directory, runtime.catalog, checkout)
    assert all(row["check"]["status"] == "unavailable" for row in result["items"])
    assert result["recorded_result"]["status"] == "passed"
    assert result["current_input_validity"] == "not_checked"


def test_candidate_mapping_and_policy_cannot_be_inferred_or_changed(checkout):
    runtime, _, path, _ = prepared(checkout, source=True)
    workflow, provenance, _ = load_generation(path, runtime.catalog)
    changed = copy.deepcopy(PIPELINE)
    changed["arguments"]["pytest"].append("-x")
    with pytest.raises(WorkflowError, match="policy changed"):
        verify_target(workflow, provenance, pipeline=changed)
    for bad in (recipe(["tests"]), recipe(["pr"])):
        if len(bad["steps"]) == 5:
            bad["steps"][0]["required"] = False
        with pytest.raises(WorkflowError, match="complete registered"):
            profile_target(bad)
    changed = copy.deepcopy(provenance)
    changed["target"]["mapping"][0]["tool_id"] = "python.pytest"
    with pytest.raises(WorkflowError, match="mapping differs"):
        generate(
            recipe(["pr"], executor="native"),
            runtime.catalog,
            checkout / ".test-tools/generated",
            source=provenance["source"],
            target=changed["target"],
        )


def test_pagination_budget_and_stale_cursor_use_same_input_scope(checkout):
    runtime, directory, _, _ = prepared(checkout)
    runtime.resume(directory)
    first = evidence(directory, runtime.catalog, checkout, limit=2, budget=8192)
    second = evidence(
        directory,
        runtime.catalog,
        checkout,
        limit=2,
        budget=8192,
        after=first["page"]["next_after"],
    )
    assert not {x["key"] for x in first["items"]} & {x["key"] for x in second["items"]}
    assert (
        len(
            json.dumps(
                first, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        )
        + 1
        <= 8192
    )
    assert (
        evidence(directory, runtime.catalog, checkout, budget=1024)["status"]
        == "budget_exceeded"
    )
    runtime.resume(directory)
    with pytest.raises(ArchiveError, match="Continuation"):
        evidence(
            directory, runtime.catalog, checkout, after=first["page"]["next_after"]
        )


@pytest.mark.parametrize("effect", ["check", "write"])
def test_process_death_preserves_unfinished_attempt_and_unknown_write(tmp_path, effect):
    registered, adapter = tools(effect=effect)
    path = generate(arithmetic_recipe(), registered, tmp_path / "generated")
    directory = Runtime(registered).create(path, tmp_path / "runs")
    script = """
import os, sys
from engineering_ci.workflow_core import Catalog, Runtime, ToolSpec
from engineering_ci.workflow_core.schemas import object_schema
class Exit:
    def invoke(self, calls, context): os._exit(91)
schema=object_schema({"value":{"type":"integer"}})
c=Catalog()
for name in ("math.increment","math.add-ten"):
    c.register(ToolSpec(name,schema,schema,effect=sys.argv[2],recovery="manual" if sys.argv[2]=="write" else "restartable"),Exit())
Runtime(c).resume(sys.argv[1])
"""
    process = subprocess.run(
        [sys.executable, "-c", script, str(directory), effect],
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
        check=False,
    )
    assert process.returncode == 91
    runtime = Runtime(registered)
    assert history(directory, runtime.status(directory))[1] == "incomplete"
    result = runtime.resume(directory)
    assert result["phase"] == ("UNKNOWN" if effect == "write" else "FINISHED")
    assert len(adapter.calls) == (0 if effect == "write" else 2)
    events, completeness, _ = history(directory, result)
    assert completeness == "incomplete"
    assert any(item["event"] == "invoking" and item["attempt"] == 1 for item in events)


def test_cli_exposes_exact_continuation_and_execution_evidence(
    checkout, monkeypatch, capsys
):
    from engineering_ci.cli import workflow

    runtime, directory, _, _ = prepared(checkout)
    monkeypatch.setattr(workflow, "ROOT", checkout)
    monkeypatch.setattr(workflow, "catalog", lambda root: runtime.catalog)
    assert cli(["workflow", "resume", str(directory)]) == 0
    output = capsys.readouterr().out
    assert '"write_errors": 0' in output
    assert (
        cli(
            [
                "workflow",
                "evidence",
                str(directory),
                "--limit",
                "1",
                "--max-bytes",
                "8192",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["operation"] == "workflow.evidence" and len(report["items"]) == 1
    assert len(output.encode("utf-8")) <= 8192 and report["page"]["next_after"]
    assert (
        cli(
            [
                "workflow",
                "status",
                str(directory),
                "--json",
                str(directory / "observations/override.json"),
            ]
        )
        == 3
    )
    assert "outside the execution" in capsys.readouterr().out


def test_observation_identity_or_missing_record_is_not_silently_accepted(tmp_path):
    runtime, directory, _ = execution(tmp_path)
    state = runtime.resume(directory)
    path = next((directory / "observations").glob("*/00000002.json"))
    value = read(path)
    value["execution_id"] = "0" * 32
    write(path, value, replace=True)
    with pytest.raises(WorkflowError, match="identity"):
        history(directory, state)
    path.unlink()
    assert history(directory, state)[1] == "incomplete"


@pytest.mark.parametrize(
    "body,expected",
    [("assert 1 == 1", 0), ("assert 1 == 2", 1), ("raise RuntimeError('failed')", 1)],
)
def test_actual_pytest_exit_remains_authoritative_with_observation_report(
    tmp_path, body, expected
):
    from xml.etree import ElementTree

    from engineering_ci.workflow_core import Catalog, ToolResult, ToolSpec
    from engineering_ci.workflow_core.schemas import object_schema

    test = tmp_path / "test_actual.py"
    test.write_text("def test_actual():\n    " + body + "\n")
    ini = tmp_path / "pytest.ini"
    ini.write_text("[pytest]\n")

    class PytestTool:
        def invoke(self, calls, context):
            xml = context.run_dir / "junit.xml"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-c",
                    str(ini),
                    str(test),
                    f"--junitxml={xml}",
                ],
                check=False,
                capture_output=True,
                env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
            )
            suite = ElementTree.parse(xml).getroot().find("testsuite")
            return {
                calls[0].step_id: ToolResult(
                    "passed" if result.returncode == 0 else "failed",
                    result.returncode,
                    outputs={
                        "tests": int(suite.get("tests")),
                        "failures": int(suite.get("failures")),
                    },
                    executed=True,
                    complete=True,
                    current=True,
                    report_generated=True,
                    reason="SECRET_DIAGNOSTIC",
                )
            }

    registered = Catalog()
    registered.register(
        ToolSpec(
            "test.pytest",
            object_schema({}),
            object_schema(
                {"tests": {"type": "integer"}, "failures": {"type": "integer"}}
            ),
        ),
        PytestTool(),
    )
    path = generate(
        {
            "schema_version": 1,
            "kind": "workflow-recipe",
            "steps": [{"id": "tests", "tool": "test.pytest", "inputs": {}}],
        },
        registered,
        tmp_path / "generated",
    )
    runtime = Runtime(registered)
    directory = runtime.create(path, tmp_path / "runs")
    state = runtime.resume(directory)
    assert state["exit_code"] == expected
    events, completeness, _ = history(directory, state)
    assert completeness == "complete" and "SECRET" not in json.dumps(events)
    result = next(event["result"] for event in events if event["event"] == "returned")
    assert result["report_generated"] and result["exit_code"] == expected


def test_legacy_generation_and_checkpoint_remain_readable_without_observations(
    tmp_path,
):
    registered, _ = tools()
    path = generate(arithmetic_recipe(), registered, tmp_path / "generated")
    runtime = Runtime(registered)
    directory = runtime.create(path, tmp_path / "runs")
    assert "target" not in load_generation(path, registered)[1]
    state = read(directory / "state.json")
    state.pop("observation_protocol")
    write(directory / "state.json", state, replace=True)
    assert history(directory, runtime.status(directory))[1] == "unknown"
    runtime.resume(directory)
    assert history(directory, runtime.status(directory))[1] == "unknown"


def test_entire_missing_session_cannot_be_hidden_by_later_success(tmp_path):
    import shutil

    runtime, directory, _ = execution(tmp_path)
    first = runtime.resume(directory)
    sessions = list((directory / "observations").iterdir())
    assert len(sessions) == 1
    shutil.rmtree(sessions[0])
    result = runtime.resume(directory)
    assert result["exit_code"] == first["exit_code"] == 0
    assert history(directory, result)[1] == "incomplete"


def test_stale_target_is_rejected_by_cli_before_creating_or_invoking_run(
    checkout, monkeypatch, capsys
):
    from engineering_ci.cli import workflow
    from engineering_ci.workflow_adapters import candidates as bindings

    runtime, _, path, calls = prepared(checkout, source=True)
    changed = copy.deepcopy(PIPELINE)
    changed["toolchain"]["ruff"] = "0.16.11"
    monkeypatch.setattr(workflow, "ROOT", checkout)
    monkeypatch.setattr(workflow, "catalog", lambda root: runtime.catalog)
    original = bindings.verify_target
    monkeypatch.setattr(
        bindings,
        "verify_target",
        lambda value, provenance: original(value, provenance, pipeline=changed),
    )
    assert cli(["workflow", "validate", str(path)]) == 3
    assert "policy changed" in capsys.readouterr().out
    destination = checkout / ".test-tools/new-runs"
    assert cli(["workflow", "run", str(path), "--out", str(destination)]) == 3
    assert not calls and not destination.exists()


def test_evidence_does_not_mix_a_running_checkpoints_results(checkout):
    from filelock import FileLock

    runtime, directory, _, _ = prepared(checkout)
    runtime.resume(directory)
    with (
        FileLock(directory / "execution.lock"),
        pytest.raises(WorkflowError, match="busy"),
    ):
        evidence(directory, runtime.catalog, checkout)


def test_invalid_query_and_missing_sdk_have_nonzero_actionable_cli_results(
    checkout, monkeypatch, capsys
):
    from engineering_ci.cli import workflow
    from engineering_ci.tracing import workflow_spans

    runtime, directory, _, _ = prepared(checkout)
    runtime.resume(directory)
    monkeypatch.setattr(workflow, "ROOT", checkout)
    monkeypatch.setattr(workflow, "catalog", lambda root: runtime.catalog)
    assert cli(["workflow", "evidence", str(directory), "--limit", "0"]) == 3
    assert "limit" in capsys.readouterr().out

    def missing(*args):
        raise ImportError("SECRET install diagnostics")

    monkeypatch.setattr(workflow_spans, "standardize", missing)
    assert (
        cli(
            [
                "workflow",
                "evidence",
                str(directory),
                "--otel-json",
                str(checkout / ".test-tools/spans.json"),
            ]
        )
        == 3
    )
    assert "install the locked trace extra" in capsys.readouterr().out
    assert runtime.status(directory)["exit_code"] == 0
