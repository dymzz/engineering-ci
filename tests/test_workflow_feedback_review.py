"""Review original controlled evidence without running tools or changing approvals."""

import os
import subprocess
import sys
from contextlib import contextmanager

import pytest
from test_checks import checkout as check_checkout
from test_workflow_capabilities import enable, reviewed
from test_workflow_feedback_summary import SummaryArchive

from engineering_ci.cli import main as cli
from engineering_ci.cli import workflow
from engineering_ci.tracing.feedback_summary import FeedbackSummary, query_summary
from engineering_ci.workflow_adapters.capabilities import create_call
from engineering_ci.workflow_adapters.feedback import prepare
from engineering_ci.workflow_adapters.feedback_review import (
    review_feedback,
    review_request,
)
from engineering_ci.workflow_core.documents import WorkflowError, digest, read, write

checkout = check_checkout


@pytest.fixture
def case(checkout):
    # Original domain tools are explicit substitutes; Runtime, files and evidence validators are real.
    runtime, store, _, baseline, candidate, _, calls = reviewed(checkout)
    enable(runtime, store, checkout)
    directory, _ = create_call(
        store,
        "pr-check-reuse",
        None,
        checkout / ".test-tools/calls",
        runtime.catalog,
        checkout,
    )
    runtime.resume(directory)
    value = prepare(directory, runtime.catalog, checkout)[0]
    return {
        "root": checkout,
        "runtime": runtime,
        "store": store,
        "execution": directory,
        "baseline": baseline,
        "candidate": candidate,
        "values": [value],
        "calls": calls,
    }


def source(case):
    values = case["values"]
    return query_summary(
        SummaryArchive(values),
        FeedbackSummary(tuple(value["feedback_id"] for value in values)),
    )


def review(case, *, paths=None, max_bytes=16384):
    return review_feedback(
        source(case),
        case["values"],
        case["store"].directory,
        [case["execution"]] if paths is None else paths,
        case["runtime"].catalog,
        case["root"],
        max_bytes=max_bytes,
    )


def test_readonly_historical_basis_and_repeat_identity_without_new_tool_calls(
    case, monkeypatch
):
    from engineering_ci.workflow_core import Runtime
    from engineering_ci.workflow_core.capabilities import CapabilityStore

    before_calls = len(case["calls"])
    paths = [
        case["store"].directory / "catalog.json",
        *(
            folder / "state.json"
            for folder in (case["baseline"], case["candidate"], case["execution"])
        ),
    ]
    before = [path.read_bytes() for path in paths]

    def prohibited(*args, **kwargs):
        pytest.fail("Review cannot execute or mutate capabilities")

    monkeypatch.setattr(Runtime, "resume", prohibited)
    monkeypatch.setattr(Runtime, "create", prohibited)
    for method in ("activate", "deactivate", "publish"):
        monkeypatch.setattr(CapabilityStore, method, prohibited)
    result = review(case)
    assert (
        result == review(case)
        and result["status"] == "reviewed"
        and result["exit_code"] == 0
    )
    item = result["items"][0]
    assert (
        item["recommendation"] == "retain_evidence"
        and item["trial"]["status"] == "verified"
    )
    assert (
        item["feedback"][0]["execution_evidence_validation"]
        == "verified_exact_snapshot"
    )
    assert (
        result["decision_required"]
        and not result["capability_mutated"]
        and not result["checks_executed"]
    )
    assert result["current_input_validity"] == "not_checked"
    assert len(case["calls"]) == before_calls and before == [
        path.read_bytes() for path in paths
    ]


def test_deactivated_capability_can_be_reviewed_but_is_not_enabled(case):
    case["store"].deactivate("pr-check-reuse")
    item = review(case)["items"][0]
    assert (
        item["recommendation"] == "retain_evidence" and item["enabled_at_read"] is False
    )
    assert not case["store"].index()["active"]


def test_old_v2_attempts_verify_against_retained_rows_not_an_invented_old_state(case):
    case["runtime"].resume(case["execution"])
    result = review(case)
    observed = result["items"][0]["feedback"][0]
    assert observed["execution_evidence_validation"] == "verified_retained_attempt_rows"
    assert observed["historical_state_validation"] == "not_available"
    assert result["items"][0]["recommendation"] == "retain_evidence"


def test_v1_exact_scope_can_be_verified_but_unavailable_old_scope_is_not_guessed(case):
    legacy = case["values"][0]
    legacy["schema_version"] = 1
    del legacy["attempt_snapshot"]
    legacy["feedback_id"] = digest(
        {k: v for k, v in legacy.items() if k != "feedback_id"}
    )
    assert review(case)["items"][0]["recommendation"] == "retain_evidence"
    case["runtime"].resume(case["execution"])
    item = review(case)["items"][0]
    assert item["recommendation"] == "investigate"
    assert (
        item["feedback"][0]["execution_evidence_validation"]
        == "historical_snapshot_unavailable"
    )


def test_changed_recorded_input_requires_a_new_trial_without_review_execution(case):
    (case["root"] / "tests/new_input.py").write_text("# source input changed\n")
    case["runtime"].resume(case["execution"])
    case["values"] = [
        prepare(case["execution"], case["runtime"].catalog, case["root"])[0]
    ]
    before = len(case["calls"])
    item = review(case)["items"][0]
    assert item["recommendation"] == "new_controlled_trial"
    assert any(
        "inputs" in difference.get("parts", [])
        for difference in item["feedback"][0]["differences"]
    )
    assert len(case["calls"]) == before


def test_missing_execution_and_changed_original_comparison_are_investigation(case):
    assert review(case, paths=[])["items"][0]["recommendation"] == "investigate"
    comparison = case["store"].location("pr-check-reuse", "1.0.0") / "comparison.json"
    value = read(comparison)
    value["check_submissions_avoided"] = 10000
    write(comparison, value, replace=True)
    item = review(case)["items"][0]
    assert (
        item["recommendation"] == "investigate"
        and item["trial"]["status"] == "unavailable"
    )


def test_real_pytest_failure_stays_failed_and_review_only_generates_diagnosis(case):
    from test_workflow_adapter import native

    fixture = case["root"] / "tests/test_review_failure.py"
    fixture.write_text("def test_failure():\n    assert 1 == 2\n")
    actual_calls = []

    def process(args, **kwargs):
        if "pytest" not in args:
            return subprocess.CompletedProcess(args, 0)
        actual_calls.append(args)
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                str(fixture),
                "--junitxml=core-junit.xml",
            ],
            cwd=kwargs["cwd"],
            env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
            check=False,
        )

    case["runtime"].catalog.resolve("python.pytest")[1].runner = native(
        case["root"], process
    )
    state = case["runtime"].resume(case["execution"])
    assert state["exit_code"] == 1 and len(actual_calls) == 1
    case["values"] = [
        prepare(case["execution"], case["runtime"].catalog, case["root"])[0]
    ]
    result = review(case)
    item = result["items"][0]
    assert result["exit_code"] == 0 and item["recommendation"] == "investigate"
    assert item["feedback"][0]["recorded_result"]["exit_code"] == 1
    assert "original_non_success_retained" in item["reasons"] and len(actual_calls) == 1
    assert case["runtime"].status(case["execution"])["exit_code"] == 1


def test_no_store_created_and_whole_output_budget_is_enforced(case):
    absent = case["root"] / ".test-tools/no-store"
    with pytest.raises(WorkflowError, match="existing"):
        review_feedback(
            source(case),
            case["values"],
            absent,
            [],
            case["runtime"].catalog,
            case["root"],
        )
    assert not absent.exists()
    result = review(case, max_bytes=1024)
    assert result["status"] == "budget_exceeded" and result["exit_code"] == 3
    assert (
        result["operation"] == "workflow.feedback-review"
        and "items" in result
        and result["items"] == []
    )


@pytest.mark.parametrize(
    "change",
    [
        {"status": "budget_exceeded"},
        {"operation": "workflow.feedback-query"},
        {"summary_version": "future/9"},
    ],
)
def test_invalid_source_summary_is_rejected_before_database(case, change):
    value = source(case)
    value.update(change)
    with pytest.raises(WorkflowError):
        review_request(value)


def cli_case(case, monkeypatch):
    from engineering_ci.tracing import store as module
    from engineering_ci.workflow_adapters import checks

    monkeypatch.setattr(workflow, "ROOT", case["root"])
    monkeypatch.setattr(checks, "catalog", lambda root: case["runtime"].catalog)

    @contextmanager
    def connection(config):
        yield SummaryArchive(case["values"])

    monkeypatch.setattr(module, "open_store", connection)
    summary = case["root"] / ".test-tools/summary.json"
    write(summary, source(case))
    output = case["root"] / ".test-tools/review.json"
    args = [
        "workflow",
        "feedback-review",
        "--summary",
        str(summary),
        "--store",
        str(case["store"].directory),
        "--execution",
        str(case["execution"]),
        "--json",
        str(output),
    ]
    return args, summary, output


def test_cli_revalidates_summary_not_its_untrusted_counts_and_never_echoes_payload(
    case, monkeypatch, capsys
):
    args, path, output = cli_case(case, monkeypatch)
    assert (
        cli(args) == 0
        and read(output)["items"][0]["recommendation"] == "retain_evidence"
    )
    value = read(path)
    value["executions"][0]["metrics"]["known_reused_checks"] = 5000
    value["secret"] = "PRIVATE"
    write(path, value, replace=True)
    assert cli(args) == 3 and read(output)["status"] == "summary_changed_or_unavailable"
    assert "PRIVATE" not in capsys.readouterr().out


def test_cli_canonical_identity_does_not_accept_false_as_integer_zero(
    case, monkeypatch
):
    args, path, output = cli_case(case, monkeypatch)
    value = read(path)
    value["exit_code"] = False
    write(path, value, replace=True)
    assert cli(args) == 3 and read(output)["status"] == "summary_changed_or_unavailable"


@pytest.mark.parametrize(
    "target", ["source", "store", "execution", "baseline", "candidate"]
)
def test_cli_never_writes_even_error_receipts_inside_original_evidence(
    case, monkeypatch, target
):
    args, summary, _ = cli_case(case, monkeypatch)
    output = (
        summary
        if target == "source"
        else (case["store"].directory if target == "store" else case[target])
        / "review-output.json"
    )
    before = output.read_bytes() if output.exists() else None
    args[-1] = str(output)
    assert cli(args) == 3
    assert (output.read_bytes() if output.exists() else None) == before


def test_cli_handles_private_archive_failure_without_invoking_review(
    case, monkeypatch, capsys
):
    from engineering_ci.tracing import store as module

    args, _, output = cli_case(case, monkeypatch)

    @contextmanager
    def failed(config):
        raise RuntimeError("postgres://private:PRIVATE@host")
        yield  # pragma: no cover

    monkeypatch.setattr(module, "open_store", failed)
    assert cli(args) == 3 and "PRIVATE" not in capsys.readouterr().out
    assert read(output)["exit_code"] == 3


def test_source_summary_changed_during_review_is_not_a_current_review(
    case, monkeypatch
):
    from engineering_ci.workflow_adapters import feedback_review

    args, path, output = cli_case(case, monkeypatch)
    original = feedback_review.review_feedback

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        source = read(path)
        source["has_coverage_gaps"] = not source["has_coverage_gaps"]
        write(path, source, replace=True)
        return result

    monkeypatch.setattr(feedback_review, "review_feedback", changed)
    assert cli(args) == 3 and read(output)["status"] == "summary_changed_during_review"


def test_policy_drift_is_distinct_from_missing_files_and_does_not_admit_old_basis(
    case, monkeypatch
):
    from engineering_ci.workflow_adapters import feedback_review
    from engineering_ci.workflow_adapters.candidates import TargetPolicyChanged

    def stale(*args, **kwargs):
        raise TargetPolicyChanged(
            {"policy_sha256": "a" * 64}, {"policy_sha256": "b" * 64}
        )

    monkeypatch.setattr(feedback_review, "verified_comparison", stale)
    item = review(case)["items"][0]
    assert (
        item["recommendation"] == "investigate"
        and item["trial"]["status"] == "policy_changed"
    )
    assert item["reasons"] == ["target_policy_changed"]
    assert item["follow_up"] == "review_new_definition_and_controlled_trial"
    assert (
        item["trial"]["recorded_policy_sha256"]
        != item["trial"]["current_policy_sha256"]
    )
