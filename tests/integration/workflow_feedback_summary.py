"""Explicit real PostgreSQL summary acceptance; production feedback is never modified."""

import copy
import json

import pytest
import trace_store
from test_checks import checkout as check_checkout
from test_workflow_capabilities import enable, reviewed
from test_workflow_feedback_summary import indexed, seal

from engineering_ci.cli import main as cli
from engineering_ci.tracing.feedback_summary import FeedbackSummary, query_summary
from engineering_ci.tracing.retrieval import encode
from engineering_ci.tracing.store import ArchiveError
from engineering_ci.workflow_adapters.capabilities import create_call
from engineering_ci.workflow_adapters.feedback import prepare
from engineering_ci.workflow_core.documents import digest, read

archive = trace_store.archive
checkout = check_checkout


def test_real_runtime_snapshots_archive_and_deduplicate_without_new_check_submissions(
    archive,
    checkout,
    monkeypatch,
):
    # The original five tools are explicit process substitutes; Runtime and PostgreSQL are real.
    runtime, store, _, _, _, _, calls = reviewed(checkout)
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
    first = prepare(directory, runtime.catalog, checkout)[0]
    runtime.resume(directory)
    resumed = prepare(directory, runtime.catalog, checkout)[0]
    before = len(calls)
    assert first["schema_version"] == resumed["schema_version"] == 2
    assert first["metrics"]["observed_check_attempts"] == 5
    assert resumed["metrics"]["observed_check_attempts"] == 10
    for value in (first, resumed):
        archive.register_feedback(value)
    monkeypatch.setattr(
        runtime, "resume", lambda *args: pytest.fail("Summary cannot run tools")
    )
    monkeypatch.setattr(
        archive, "initialize", lambda: pytest.fail("Summary cannot migrate")
    )
    result = query_summary(
        archive, FeedbackSummary((first["feedback_id"], resumed["feedback_id"]))
    )
    group = result["executions"][0]
    assert (
        result["exit_code"] == 0
        and group["snapshot_coverage"] == "verified_selected_indices"
    )
    assert (
        group["metrics"]["unique_observed_attempts"]
        == group["metrics"]["known_reused_checks"]
        == 10
    )
    assert group["metrics"]["known_check_submissions"] == 0
    assert (
        group["metrics"]["duplicate_attempt_references_removed"] == 5
        and len(calls) == before
    )
    with archive.transaction(readonly=True) as cursor:
        cursor.execute(archive.query("SELECT count(*) FROM {schema}.workflow_feedback"))
        assert cursor.fetchone()[0] == 2


def test_real_selected_failure_partial_and_v1_survive(archive):
    failed, resumed = (
        indexed(5, status="failed"),
        indexed(10, completeness="incomplete"),
    )
    legacy = copy.deepcopy(failed)
    legacy["schema_version"] = 1
    del legacy["attempt_snapshot"]
    seal(legacy)
    for item in (failed, resumed, legacy):
        archive.register_feedback(item)
    result = query_summary(
        archive,
        FeedbackSummary(
            tuple(item["feedback_id"] for item in (failed, resumed, legacy))
        ),
    )
    group = result["executions"][0]
    assert group["snapshot_coverage"] == "partial" and result["has_coverage_gaps"]
    assert group["metrics"]["unique_observed_attempts"] == 10
    assert (
        sum(item["recorded_result"]["exit_code"] == 1 for item in group["snapshots"])
        == 2
    )


def test_real_missing_and_corrupt_feedback_are_not_partial_success(archive):
    value = indexed(5)
    archive.register_feedback(value)
    with pytest.raises(ArchiveError) as error:
        query_summary(archive, FeedbackSummary((value["feedback_id"], "f" * 64)))
    assert error.value.status == "feedback_unavailable"
    with archive.transaction() as cursor:
        cursor.execute(
            archive.query(
                "UPDATE {schema}.workflow_feedback SET document = %s WHERE feedback_id = %s"
            ),
            (b"PRIVATE broken", value["feedback_id"]),
        )
    with pytest.raises(ArchiveError) as error:
        query_summary(archive, FeedbackSummary((value["feedback_id"],)))
    assert error.value.status == "feedback_corrupt" and "PRIVATE" not in str(
        error.value
    )


def test_real_cli_uses_explicit_ids_and_budget_in_existing_config(
    archive, tmp_path, monkeypatch, capsys
):
    from engineering_ci.cli import workflow

    value = indexed(5, status="failed")
    archive.register_feedback(value)
    monkeypatch.setattr(workflow, "ROOT", tmp_path)
    config = tmp_path / "tracing.toml"
    config.write_text(
        f'schema_version = 1\n[archive]\nschema = "{archive.config.schema}"\n'
    )
    output = tmp_path / ".test-tools/summary.json"
    args = [
        "workflow",
        "feedback-summary",
        "--config",
        str(config),
        "--feedback-id",
        value["feedback_id"],
        "--max-bytes",
        "4096",
        "--json",
        str(output),
    ]
    assert cli(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(encode(result)) <= 4096 and read(output) == result
    assert result["executions"][0]["snapshots"][0]["recorded_result"]["exit_code"] == 1
    assert not (tmp_path / "state.json").exists()
    args[args.index("4096")] = "1024"
    assert cli(args) == 3 and read(output)["status"] == "budget_exceeded"


def test_real_conflicting_attempt_is_reported_without_a_total(archive):
    first, later = indexed(5), indexed(10)
    later["attempt_snapshot"]["attempts"][0]["source_row_sha256"] = digest(
        {"changed": "evidence"}
    )
    seal(later)
    for item in (first, later):
        archive.register_feedback(item)
    group = query_summary(
        archive, FeedbackSummary((first["feedback_id"], later["feedback_id"]))
    )["executions"][0]
    assert group["snapshot_coverage"] == "conflicting" and group["metrics"] is None
