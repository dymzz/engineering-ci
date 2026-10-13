"""Explicit archived selections, cumulative attempt coverage and preserved negative facts."""

import copy
import json
from contextlib import contextmanager

import pytest
from test_workflow_feedback_query import MemoryArchive, recorded, row

from engineering_ci.cli import main as cli
from engineering_ci.cli import workflow
from engineering_ci.tracing.feedback_query import checked_document, checked_row
from engineering_ci.tracing.feedback_summary import FeedbackSummary, query_summary
from engineering_ci.tracing.retrieval import encode
from engineering_ci.tracing.store import ArchiveError
from engineering_ci.workflow_core.documents import digest, read
from engineering_ci.workflow_core.feedback_snapshot import measured


def seal(value):
    value["feedback_id"] = digest(
        {key: item for key, item in value.items() if key != "feedback_id"}
    )
    return value


def indexed(count, *, seed="execution", completeness="complete", status="passed"):
    """Valid protocol fixtures; these do not claim real check execution."""
    value = recorded(seed, completeness=completeness, status=status)
    value["schema_version"] = 2
    attempts = []
    for number in range(count):
        step, attempt = "check-" + str(number % 5), number // 5 + 1
        key = {"seed": seed, "step": step, "attempt": attempt}
        attempts.append(
            {
                "step_id": step,
                "attempt": attempt,
                "tool_id": "checks.pytest",
                "contract_sha256": digest({"contract": step}),
                "session_id": digest({"session": seed, "round": attempt})[:32],
                "batch_id": digest({"batch": seed, "round": attempt})[:32],
                "input_sha256": digest({"input": key}),
                "result_sha256": digest({"result": key}),
                "source_row_sha256": digest({"row": key}),
                "observation_status": "complete",
                "check_status": "verified",
                "check_submitted": False,
                "evidence_reused": True,
                "result_status": "passed",
                "exit_code": 0,
            }
        )
    value["attempt_snapshot"] = {
        "protocol": "workflow-feedback-attempts/1",
        "required_step_ids": ["check-" + str(n) for n in range(5)],
        "attempts": attempts,
    }
    value["metrics"] = measured(value["attempt_snapshot"], completeness == "complete")
    value["evidence"]["state_sha256"] = digest(
        {"state": seed, "count": count, "status": status}
    )
    value["evidence"]["observations_sha256"] = digest(
        {"observations": seed, "count": count}
    )
    value["evidence"]["evidence_sha256"] = digest(attempts)
    return seal(value)


class SummaryArchive(MemoryArchive):
    def execute(self, statement, parameters=None):
        if "ANY(" in statement:
            assert statement.startswith("SELECT") and parameters is not None
            self.statements.append((statement, parameters))
            self.answer = [item for item in self.rows if item[0] in parameters[0]]
        else:
            super().execute(statement, parameters)


def summary(*values, max_bytes=16384):
    store = SummaryArchive(values)
    result = query_summary(
        store,
        FeedbackSummary(tuple(value["feedback_id"] for value in values), max_bytes),
    )
    assert store.transactions == [True]
    return result


def test_cumulative_five_and_ten_are_ten_not_fifteen_and_no_check_execution():
    first, resumed = indexed(5), indexed(10)
    result = summary(first, resumed)
    group = result["executions"][0]
    metrics = group["metrics"]
    assert result["status"] == "summarized" and result["exit_code"] == 0
    assert (
        result["selected_executions"] == 1
        and result["collection_coverage"] == "unknown"
    )
    assert metrics["unique_observed_attempts"] == metrics["known_reused_checks"] == 10
    assert metrics["duplicate_attempt_references_removed"] == 5
    assert (
        metrics["known_check_submissions"] == 0
        and metrics["known_adapter_batches"] == 2
    )
    assert group["snapshot_coverage"] == "verified_selected_indices"
    old = next(
        item
        for item in group["snapshots"]
        if item["feedback_id"] == first["feedback_id"]
    )
    assert old["covered_by"] == resumed["feedback_id"]
    assert (
        result["current_input_validity"]
        == result["execution_evidence_validation"]
        == "not_checked"
    )


def test_repeated_inputs_deduplicate_and_order_does_not_change_summary_identity():
    first, resumed = indexed(5), indexed(10)
    store = SummaryArchive([first, resumed])
    left = query_summary(
        store,
        FeedbackSummary(
            (first["feedback_id"], resumed["feedback_id"], first["feedback_id"])
        ),
    )
    right = query_summary(
        store, FeedbackSummary((resumed["feedback_id"], first["feedback_id"]))
    )
    assert left == right and left["selected_snapshots"] == 2
    assert store.statements[-1][1] == (
        sorted([first["feedback_id"], resumed["feedback_id"]]),
    )


def test_distinct_execution_ids_never_deduplicate_their_same_step_attempt():
    result = summary(indexed(5), indexed(5, seed="another"))
    assert result["selected_executions"] == 2
    assert all(
        item["metrics"]["unique_observed_attempts"] == 5
        for item in result["executions"]
    )


def test_equal_indices_have_one_representative_and_keep_both_failure_and_pass():
    first, other = indexed(5, status="failed"), indexed(5)
    group = summary(first, other)["executions"][0]
    small, large = sorted(group["snapshots"], key=lambda value: value["feedback_id"])
    assert small["covered_by"] is None and large["covered_by"] == small["feedback_id"]
    assert group["metrics"]["unique_observed_attempts"] == 5
    assert {item["recorded_result"]["status"] for item in group["snapshots"]} == {
        "passed",
        "failed",
    }


def test_partial_overlap_deduplicates_only_the_matching_attempt():
    first, other = indexed(5), indexed(5)
    first["attempt_snapshot"]["attempts"] = first["attempt_snapshot"]["attempts"][:3]
    other["attempt_snapshot"]["attempts"] = other["attempt_snapshot"]["attempts"][2:]
    for value in (first, other):
        value["metrics"] = measured(value["attempt_snapshot"], True)
        value["evidence"]["evidence_sha256"] = digest(value["attempt_snapshot"])
        seal(value)
    group = summary(first, other)["executions"][0]
    assert group["metrics"]["unique_observed_attempts"] == 5
    assert group["metrics"]["duplicate_attempt_references_removed"] == 1
    assert all(item["covered_by"] is None for item in group["snapshots"])


def test_legacy_counters_cannot_prove_coverage_or_be_added_to_v2():
    old, new = recorded(), indexed(10)
    old["evidence"]["execution_id"] = new["evidence"]["execution_id"]
    for key in ("capability", "generation_id", "definition_id", "catalog_revision"):
        old[key] = new[key]
    seal(old)
    only = summary(old)["executions"][0]
    assert (
        only["metrics"] is None and only["snapshot_coverage"] == "legacy_unverifiable"
    )
    mixed = summary(old, new)["executions"][0]
    assert mixed["metrics"]["unique_observed_attempts"] == 10
    assert mixed["snapshot_coverage"] == "partial" and mixed["legacy_feedback_ids"] == [
        old["feedback_id"]
    ]


@pytest.mark.parametrize(
    "mutation",
    [
        "source_row_sha256",
        "check_submitted",
        "approval",
        "definition",
        "required_steps",
    ],
)
def test_same_execution_with_changed_evidence_or_binding_is_not_summed(mutation):
    first, resumed = indexed(5), indexed(10)
    if mutation == "approval":
        resumed["capability"]["approval_id"] = "f" * 64
    elif mutation == "definition":
        resumed["definition_id"] = "f" * 64
    elif mutation == "required_steps":
        resumed["attempt_snapshot"]["required_step_ids"].append("other")
    else:
        resumed["attempt_snapshot"]["attempts"][0][mutation] = (
            "f" * 64 if mutation.endswith("sha256") else True
        )
    resumed["metrics"] = measured(resumed["attempt_snapshot"], True)
    seal(resumed)
    group = summary(first, resumed)["executions"][0]
    assert group["snapshot_coverage"] == "conflicting" and group["metrics"] is None
    assert all(item["covered_by"] is None for item in group["snapshots"])


def test_failed_snapshot_survives_a_later_pass_and_incomplete_measurements_stay_visible():
    first, resumed = indexed(5, status="failed"), indexed(10, completeness="incomplete")
    group = summary(first, resumed)["executions"][0]
    failed = next(
        item
        for item in group["snapshots"]
        if item["feedback_id"] == first["feedback_id"]
    )
    assert (
        failed["recorded_result"]["status"] == "failed"
        and failed["recorded_result"]["exit_code"] == 1
    )
    assert group["snapshot_coverage"] == "partial"
    unknown = indexed(5, completeness="incomplete")
    unknown["attempt_snapshot"]["attempts"][0].update(
        check_status="unavailable",
        check_submitted=None,
        evidence_reused=None,
        batch_id=None,
    )
    unknown["metrics"] = measured(unknown["attempt_snapshot"], False)
    seal(unknown)
    metrics = summary(unknown)["executions"][0]["metrics"]
    assert (
        metrics["known_reused_checks"] == 4
        and metrics["unknown_reuse_measurements"] == 1
    )
    assert (
        metrics["unknown_submission_measurements"]
        == metrics["unknown_batch_measurements"]
        == 1
    )


@pytest.mark.parametrize(
    "mutation", ["count", "duplicate", "unknown_step", "gap", "version", "secret"]
)
def test_invalid_attempt_manifest_or_metrics_fails_integrity(mutation):
    value = indexed(5)
    if mutation == "count":
        value["metrics"]["reused_checks"] = 50
    elif mutation == "duplicate":
        value["attempt_snapshot"]["attempts"].append(
            copy.deepcopy(value["attempt_snapshot"]["attempts"][0])
        )
    elif mutation == "unknown_step":
        value["attempt_snapshot"]["attempts"][0]["step_id"] = "unknown"
    elif mutation == "gap":
        value["attempt_snapshot"]["attempts"][0]["attempt"] = 10**18
    elif mutation == "version":
        value["schema_version"] = 1
    else:
        value["attempt_snapshot"]["attempts"][0]["token"] = "PRIVATE"
    seal(value)
    with pytest.raises(ArchiveError) as error:
        checked_document(row(value))
    assert error.value.status == "feedback_corrupt" and "PRIVATE" not in str(
        error.value
    )


def test_query_does_not_return_full_attempt_manifest():
    value = indexed(10)
    item = checked_row(row(value))
    assert item["attempt_snapshot"]["attempts"] == 10
    assert item["attempt_snapshot"]["sha256"] == digest(value["attempt_snapshot"])


def test_missing_explicit_snapshot_and_corrupt_archive_are_nonzero():
    value = indexed(5)
    with pytest.raises(ArchiveError, match="missing"):
        query_summary(
            SummaryArchive([value]), FeedbackSummary((value["feedback_id"], "f" * 64))
        )
    store = SummaryArchive([value])
    damaged = list(store.rows[0])
    damaged[3] = b"PRIVATE corrupt"
    store.rows[0] = tuple(damaged)
    with pytest.raises(ArchiveError) as error:
        query_summary(store, FeedbackSummary((value["feedback_id"],)))
    assert error.value.status == "feedback_corrupt"


@pytest.mark.parametrize(
    "ids,budget",
    [((), 16384), (("bad",), 16384), (("f" * 64,) * 33, 16384), (("f" * 64,), 1000)],
)
def test_invalid_summary_selection_never_connects(ids, budget):
    store = SummaryArchive([])
    with pytest.raises((ArchiveError, ValueError)):
        query_summary(store, FeedbackSummary(ids, budget))
    assert not store.transactions


def test_budget_rejects_entire_summary_instead_of_returning_a_false_partial_total():
    result = summary(indexed(5), indexed(10), max_bytes=1024)
    assert result["status"] == "budget_exceeded" and result["exit_code"] == 3
    assert (
        result["operation"] == "workflow.feedback-summary"
        and len(encode(result)) <= 1024
    )
    assert "executions" not in result


def test_cli_does_not_construct_runtime_or_overwrite_original_query_receipt(
    tmp_path, monkeypatch, capsys
):
    from engineering_ci.tracing import store as module

    monkeypatch.setattr(workflow, "ROOT", tmp_path)

    def prohibited(*args, **kwargs):
        pytest.fail("Summary must not construct the execution layer")

    monkeypatch.setattr(workflow, "Runtime", prohibited)
    monkeypatch.setattr(workflow, "catalog", prohibited)
    value = indexed(5, status="failed")

    @contextmanager
    def connection(config):
        yield SummaryArchive([value])

    monkeypatch.setattr(module, "open_store", connection)
    output = tmp_path / ".test-tools/summary.json"
    arguments = [
        "workflow",
        "feedback-summary",
        "--feedback-id",
        value["feedback_id"],
        "--json",
        str(output),
    ]
    assert cli(arguments) == cli(arguments) == 0
    assert (
        read(output)["executions"][0]["snapshots"][0]["recorded_result"]["exit_code"]
        == 1
    )
    assert "PRIVATE" not in capsys.readouterr().out
    original = read(output)
    original["operation"] = "workflow.feedback-query"
    output.write_text(json.dumps(original))
    assert (
        cli(arguments) == 3 and read(output)["operation"] == "workflow.feedback-query"
    )
