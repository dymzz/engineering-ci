"""Controlled check reuse: identity, quality, provenance and read-only reporting."""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from filelock import FileLock
from test_checks import checkout as check_checkout
from test_workflow_observations import prepared

from engineering_ci.cli import main as cli
from engineering_ci.workflow_adapters.comparison import compare
from engineering_ci.workflow_adapters.evidence import execution_evidence, execution_pair
from engineering_ci.workflow_core.documents import WorkflowError

checkout = check_checkout


def trial(checkout, *, fail=False, reuse=True):
    runtime, baseline, definition, calls = prepared(checkout, fail=fail, source=True)
    runtime.resume(baseline)
    candidate = runtime.create(
        definition,
        checkout / ".test-tools/runs",
        reuse_from=baseline if reuse else None,
    )
    runtime.resume(candidate)
    return runtime, baseline, candidate, calls


def views(runtime, baseline, candidate, checkout):
    return execution_pair(baseline, candidate, runtime.catalog, checkout)


def test_matching_trial_verifies_only_submission_reuse_and_preserves_quality(checkout):
    runtime, baseline, candidate, calls = trial(checkout)
    left, right = views(runtime, baseline, candidate, checkout)
    result = compare(left, right)
    assert len(calls) == 5 and result["exit_code"] == 0
    assert (
        result["optimization_verified"]
        and result["verified_scope"] == "check-submission-reuse"
    )
    assert result["check_submissions_avoided"] == 5
    assert result["baseline"]["metrics"]["check_submissions"] == 5
    assert result["candidate"]["metrics"]["check_submissions"] == 0
    assert result["candidate"]["metrics"]["reused_checks"] == 5
    assert (
        result["baseline"]["metrics"]["adapter_batches"]
        == result["candidate"]["metrics"]["adapter_batches"]
        == 1
    )
    assert result["recorded_quality_gate"] == "passed" and len(result["checks"]) == 5
    assert result["current_input_validity"] == "not_checked" and not result["approved"]
    assert all(
        result["candidate"]["metrics"][key] is None
        for key in ("individual_check_duration_ns", "dagger_cache_hit", "cost")
    )
    assert result == compare(left, right)
    for directory in (baseline, candidate):
        assert runtime.status(directory)["status"] == "passed"


def test_equal_fresh_executions_do_not_claim_any_benefit(checkout):
    runtime, baseline, candidate, calls = trial(checkout, reuse=False)
    result = compare(*views(runtime, baseline, candidate, checkout))
    assert len(calls) == 10 and result["exit_code"] == 0
    assert result["status"] == "compared" and result["check_submissions_avoided"] == 0
    assert result["verified_scope"] is None and not result["optimization_verified"]


def test_changed_comparison_rules_have_a_distinct_report_identity(
    checkout, monkeypatch
):
    from engineering_ci.workflow_adapters import comparison

    runtime, baseline, candidate, _ = trial(checkout)
    inputs = views(runtime, baseline, candidate, checkout)
    original = compare(*inputs)
    monkeypatch.setattr(comparison, "COMPARISON_VERSION", "pr-check-submission-reuse/1")
    assert compare(*inputs)["comparison_id"] != original["comparison_id"]


def test_same_execution_or_optimized_baseline_is_not_a_control(checkout):
    runtime, _, candidate, _ = trial(checkout)
    result = compare(*views(runtime, candidate, candidate, checkout))
    assert result["exit_code"] == 3 and result["status"] == "not_comparable"
    reasons = {item["reason"] for item in result["reasons"]}
    assert {
        "same_execution_is_not_an_independent_trial",
        "baseline_is_not_a_fresh_check_sample",
    } <= reasons


@pytest.mark.parametrize(
    "dimension", ["inputs", "tools", "environment", "parameters", "executor"]
)
def test_one_changed_identity_keeps_other_pairs_but_refuses_global_claim(
    checkout, dimension
):
    runtime, baseline, candidate, _ = trial(checkout)
    left, right = views(runtime, baseline, candidate, checkout)
    row = right["items"][0]
    row["check"]["identity_parts"][dimension] = "f" * 64
    result = compare(left, right)
    assert result["status"] == "not_comparable" and result["exit_code"] == 3
    assert (
        result["check_submissions_avoided"] is None
        and not result["optimization_verified"]
    )
    assert any(item["reason"] == "different_" + dimension for item in result["reasons"])
    assert sum(pair["status"] == "compared" for pair in result["checks"]) == 4


def test_actual_source_change_reruns_related_checks_without_becoming_comparable(
    checkout,
):
    runtime, baseline, definition, calls = prepared(checkout, source=True)
    runtime.resume(baseline)
    (checkout / "tests/changed.py").write_text("# changed input\n")
    candidate = runtime.create(
        definition, checkout / ".test-tools/runs", reuse_from=baseline
    )
    runtime.resume(candidate)
    result = compare(*views(runtime, baseline, candidate, checkout))
    assert len(calls) == 6 and result["status"] == "not_comparable"
    assert any(
        item.get("step_id") == "pytest" and item["reason"] == "different_inputs"
        for item in result["reasons"]
    )
    assert not result["optimization_verified"]


def test_failure_report_and_reused_failure_never_satisfy_quality_gate(checkout):
    runtime, baseline, candidate, calls = trial(checkout, fail=True)
    result = compare(*views(runtime, baseline, candidate, checkout))
    assert len(calls) == 5 and result["status"] == "compared"
    assert result["exit_code"] == 3 and result["recorded_quality_gate"] == "not_passed"
    assert not result["optimization_verified"]
    assert (
        result["baseline"]["recorded_result"]["exit_code"]
        == result["candidate"]["recorded_result"]["exit_code"]
        == 7
    )


def test_reuse_from_another_frozen_parent_cannot_claim_this_baseline(checkout):
    runtime, baseline, candidate, _ = trial(checkout)
    left, right = views(runtime, baseline, candidate, checkout)
    right["metadata"]["reuse_source"]["state_sha256"] = "f" * 64
    result = compare(left, right)
    assert result["status"] == "not_comparable"
    assert all(
        "reuse_is_not_bound_to_this_baseline" in pair["reasons"]
        for pair in result["checks"]
    )


def test_resource_order_does_not_change_identity_but_membership_and_hash_do(checkout):
    runtime, baseline, candidate, _ = trial(checkout)
    left, right = views(runtime, baseline, candidate, checkout)
    for row in right["items"]:
        row["check"]["resources"].reverse()
    assert compare(left, right)["optimization_verified"]
    row = next(item for item in right["items"] if item["step_id"] == "pytest")
    original = copy.deepcopy(row["check"]["resources"])
    row["check"]["resources"][0]["sha256"] = "f" * 64
    assert not compare(left, right)["optimization_verified"]
    row["check"]["resources"] = original + original
    result = compare(left, right)
    assert any(
        item["reason"] == "reused_resources_differ_from_baseline"
        for item in result["reasons"]
    )


def test_resume_attempts_remain_visible_and_are_not_compressed_into_one_sample(
    checkout,
):
    runtime, baseline, candidate, calls = trial(checkout)
    runtime.resume(candidate)
    result = compare(*views(runtime, baseline, candidate, checkout))
    assert result["status"] == "not_comparable" and len(calls) == 5
    assert result["candidate"]["metrics"]["observed_check_attempts"] == 10
    assert result["candidate"]["metrics"]["reused_checks"] == 10
    assert any(
        item["reason"] == "multiple_attempts_require_a_new_controlled_execution"
        for item in result["reasons"]
    )


@pytest.mark.parametrize(
    "damage", ["resource", "observation", "measurement", "identity", "gate"]
)
def test_missing_or_corrupt_facts_never_become_a_verified_optimization(
    checkout, damage
):
    runtime, baseline, candidate, _ = trial(checkout)
    left, right = views(runtime, baseline, candidate, checkout)
    row = right["items"][0]
    if damage == "resource":
        row["check"]["status"] = "unavailable"
    elif damage == "observation":
        right["metadata"]["observation_completeness"] = "incomplete"
    elif damage == "measurement":
        row["check"]["actual_check_executed"] = None
    elif damage == "identity":
        row["check"].pop("identity_parts")
    else:
        row["check"]["recorded_gate"]["input_stable"] = False
    result = compare(left, right)
    assert result["exit_code"] == 3 and not result["optimization_verified"]
    if damage == "observation":
        assert result["candidate"]["metrics"]["check_submissions"] is None


def test_removed_required_step_or_changed_provenance_is_rejected(checkout):
    runtime, baseline, candidate, _ = trial(checkout)
    left, right = views(runtime, baseline, candidate, checkout)
    missing = copy.deepcopy(right)
    missing["workflow"]["steps"].pop()
    assert compare(left, missing)["status"] == "not_comparable"
    right["metadata"]["source"]["candidate_id"] = "f" * 64
    result = compare(left, right)
    assert any(
        item["reason"] == "different_candidate_source" for item in result["reasons"]
    )


def test_pair_locks_source_executions_and_does_not_change_public_evidence_identity(
    checkout,
):
    runtime, baseline, candidate, _ = trial(checkout)
    before = execution_evidence(baseline, runtime.catalog, checkout)
    left, _ = views(runtime, baseline, candidate, checkout)
    assert left["scope"] == before["scope"]
    assert "recorded_gate" not in before["items"][0]["check"]
    assert left["items"][0]["check"]["recorded_gate"]
    with (
        FileLock(candidate / "execution.lock"),
        pytest.raises(WorkflowError, match="busy"),
    ):
        views(runtime, baseline, candidate, checkout)
    assert (
        execution_evidence(baseline, runtime.catalog, checkout)["scope"]
        == before["scope"]
    )


def test_compare_cli_reads_existing_inputs_without_rerunning_or_rewriting_them(
    checkout, monkeypatch, capsys
):
    from engineering_ci.cli import workflow
    from engineering_ci.tracing import store

    runtime, baseline, candidate, calls = trial(checkout)
    capsys.readouterr()
    monkeypatch.setattr(workflow, "ROOT", checkout)
    monkeypatch.setattr(workflow, "catalog", lambda root: runtime.catalog)
    monkeypatch.setattr(
        store, "open_store", lambda *args: pytest.fail("unexpected PostgreSQL access")
    )
    frozen = {
        str(path): path.read_bytes()
        for directory in (baseline, candidate)
        for path in directory.rglob("*")
        if path.is_file() and path.name != "execution.lock"
    }
    output, action = (
        checkout / ".test-tools/comparison.json",
        checkout / ".test-tools/compare-action.json",
    )
    args = [
        "workflow",
        "compare",
        str(baseline),
        str(candidate),
        "--out",
        str(output),
        "--json",
        str(action),
    ]
    assert cli(args) == 0
    assert json.loads(action.read_text())["report_generated"]
    assert json.loads(output.read_text())["optimization_verified"] and len(calls) == 5
    assert all(Path(name).read_bytes() == content for name, content in frozen.items())
    assert cli(args) == 3
    assert "choose a new file" in capsys.readouterr().out
    assert json.loads(output.read_text())["optimization_verified"]


def test_cli_preserves_non_comparable_report_and_protects_sources(
    checkout, monkeypatch, capsys
):
    from engineering_ci.cli import workflow

    runtime, baseline, candidate, _ = trial(checkout)
    capsys.readouterr()
    monkeypatch.setattr(workflow, "ROOT", checkout)
    monkeypatch.setattr(workflow, "catalog", lambda root: runtime.catalog)
    output = checkout / ".test-tools/not-comparable.json"
    assert (
        cli(
            [
                "workflow",
                "compare",
                str(candidate),
                str(candidate),
                "--out",
                str(output),
            ]
        )
        == 3
    )
    assert (
        output.exists() and json.loads(output.read_text())["status"] == "not_comparable"
    )
    assert (
        cli(
            [
                "workflow",
                "compare",
                str(baseline),
                str(candidate),
                "--out",
                str(baseline / "report.json"),
            ]
        )
        == 3
    )
    assert not (baseline / "report.json").exists()
    same = checkout / ".test-tools/same.json"
    assert (
        cli(
            [
                "workflow",
                "compare",
                str(baseline),
                str(candidate),
                "--out",
                str(same),
                "--json",
                str(same),
            ]
        )
        == 3
    )
    assert not same.exists()


def test_actual_damaged_receipt_is_not_replaced_by_its_old_pass_marker(checkout):
    runtime, baseline, candidate, calls = trial(checkout)
    old = execution_evidence(baseline, runtime.catalog, checkout)
    pytest_row = next(row for row in old["items"] if row["step_id"] == "pytest")
    (checkout / pytest_row["check"]["resources"][0]["path"]).write_bytes(b"broken")
    result = compare(*views(runtime, baseline, candidate, checkout))
    assert (
        result["status"] == "not_comparable"
        and result["recorded_quality_gate"] == "not_passed"
    )
    assert not result["optimization_verified"] and len(calls) == 5
    assert (
        runtime.status(baseline)["status"]
        == runtime.status(candidate)["status"]
        == "passed"
    )


def test_report_write_failure_returns_source_context_without_faking_success(
    checkout, monkeypatch, capsys
):
    from engineering_ci.cli import workflow

    runtime, baseline, candidate, _ = trial(checkout)
    capsys.readouterr()
    monkeypatch.setattr(workflow, "ROOT", checkout)
    monkeypatch.setattr(workflow, "catalog", lambda root: runtime.catalog)

    def unavailable(*args, **kwargs):
        raise OSError("PRIVATE write diagnostics")

    monkeypatch.setattr(workflow, "write", unavailable)
    output = checkout / ".test-tools/failed-report.json"
    assert (
        cli(
            ["workflow", "compare", str(baseline), str(candidate), "--out", str(output)]
        )
        == 3
    )
    result = json.loads(capsys.readouterr().out)
    assert not output.exists() and not result["report_generated"]
    assert "PRIVATE" not in json.dumps(result)
    assert result["baseline"] == str(baseline) and result["candidate"] == str(candidate)
    assert result["next_actions"][0] == [
        "engineering-ci",
        "workflow",
        "status",
        str(baseline),
    ]


@pytest.mark.parametrize("body,expected", [("assert 1 == 1", 0), ("assert 1 == 2", 1)])
def test_real_pytest_outcome_survives_reuse_and_comparison(checkout, body, expected):
    from test_workflow_adapter import native

    from engineering_ci.workflow_adapters.checks import catalog, recipe
    from engineering_ci.workflow_core import Runtime, generate

    fixture = checkout / "tests/test_fixture.py"
    fixture.write_text("def test_observed():\n    " + body + "\n")
    pytest_calls = []

    def process(args, **kwargs):
        if "pytest" not in args:
            return subprocess.CompletedProcess(args, 0)
        pytest_calls.append(args)
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "--junitxml=" + str(checkout / "core-junit.xml"),
                str(fixture),
            ],
            capture_output=True,
            check=False,
            env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        )

    tools = catalog(checkout, runner=native(checkout, process))
    definition = generate(
        recipe(["pr"], executor="native"), tools, checkout / ".test-tools/generated"
    )
    runtime = Runtime(tools)
    baseline = runtime.create(definition, checkout / ".test-tools/runs")
    first = runtime.resume(baseline)
    candidate = runtime.create(
        definition, checkout / ".test-tools/runs", reuse_from=baseline
    )
    second = runtime.resume(candidate)
    result = compare(*views(runtime, baseline, candidate, checkout))
    assert first["exit_code"] == second["exit_code"] == expected
    assert len(pytest_calls) == 1
    assert result["status"] == "compared"
    assert result["exit_code"] == (0 if expected == 0 else 3)
    assert result["optimization_verified"] is (expected == 0)
    assert result["candidate"]["recorded_result"]["exit_code"] == expected


def test_generate_candidate_id_is_not_treated_as_a_comparison_execution_path(
    checkout, monkeypatch, capsys
):
    from test_trace_candidates import evidence as trace_evidence

    from engineering_ci.cli import mine_inputs, workflow
    from engineering_ci.mining.candidates import candidates

    runtime, _, _, _ = prepared(checkout)
    original = trace_evidence()
    selected = candidates(original)["candidates"][0]["candidate_id"]
    monkeypatch.setattr(workflow, "ROOT", checkout)
    monkeypatch.setattr(workflow, "catalog", lambda root: runtime.catalog)
    monkeypatch.setattr(mine_inputs, "file_evidence", lambda *args: original)
    output = checkout / ".test-tools/candidate-generation.json"
    assert (
        cli(
            [
                "workflow",
                "generate",
                "--profile",
                "pr",
                "--candidate",
                selected,
                "--analysis",
                str(checkout / ".test-tools/analysis.json"),
                "--projection",
                str(checkout / ".test-tools/projection.json"),
                "--out",
                str(checkout / ".test-tools/generated"),
                "--json",
                str(output),
            ]
        )
        == 0
    )
    result = json.loads(output.read_text())
    assert result["status"] == "generated" and Path(result["output"]).is_file()
