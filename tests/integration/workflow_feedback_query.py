"""Explicit real PostgreSQL feedback retrieval, paging and corruption acceptance."""

import copy

import pytest
import trace_store
from test_workflow_feedback_query import recorded

from engineering_ci.cli import main as cli
from engineering_ci.tracing.feedback_query import FeedbackQuery, query_feedback
from engineering_ci.tracing.retrieval import encode
from engineering_ci.tracing.store import ArchiveError, migration_digest
from engineering_ci.workflow_core.documents import digest, read

archive = trace_store.archive


def test_real_readonly_result_and_failure_facts_without_write_or_migration(
    archive, monkeypatch
):
    failed = recorded("actual-pg-failure", status="failed", completeness="unknown")
    archive.register_feedback(failed)
    before = archive.schema_version()

    def prohibited(*args, **kwargs):
        pytest.fail(
            "Read-only retrieval must not execute a migration or insert feedback"
        )

    monkeypatch.setattr(archive, "initialize", prohibited)
    monkeypatch.setattr(archive, "register_feedback", prohibited)
    result = query_feedback(archive, FeedbackQuery(feedback_id=failed["feedback_id"]))
    item = result["items"][0]
    assert result["exit_code"] == 0 and item["recorded_result"]["status"] == "failed"
    assert (
        item["recorded_result"]["exit_code"] == 1
        and item["observation_completeness"] == "unknown"
    )
    assert item["references"]["execution_evidence"] == failed["evidence"]
    assert archive.schema_version() == before == 3
    with archive.transaction(readonly=True) as cursor:
        cursor.execute(archive.query("SELECT count(*) FROM {schema}.workflow_feedback"))
        assert cursor.fetchone()[0] == 1


def test_real_filters_and_paging_preserve_same_execution_snapshots(archive):
    first = recorded("same-execution")
    resumed = copy.deepcopy(first)
    resumed["metrics"].update(
        observed_check_attempts=10, reused_checks=10, adapter_batches=2
    )
    resumed["evidence"]["state_sha256"] = digest({"resume": "state"})
    resumed["evidence"]["observations_sha256"] = digest({"resume": "observations"})
    resumed["feedback_id"] = digest(
        {key: item for key, item in resumed.items() if key != "feedback_id"}
    )
    for item in (first, resumed, recorded("other", status="failed")):
        archive.register_feedback(item)
    filters = {
        "approval_id": first["capability"]["approval_id"],
        "execution_id": first["evidence"]["execution_id"],
        "name": "pr-check-reuse",
        "version": "1.0.0",
        "result_status": "passed",
        "completeness": "complete",
        "limit": 1,
    }
    page1 = query_feedback(archive, FeedbackQuery(**filters))
    page2 = query_feedback(
        archive, FeedbackQuery(**filters, after=page1["page"]["next_after"])
    )
    rows = page1["items"] + page2["items"]
    assert {item["feedback_id"] for item in rows} == {
        first["feedback_id"],
        resumed["feedback_id"],
    }
    assert sorted(item["metrics"]["observed_check_attempts"] for item in rows) == [
        5,
        10,
    ]
    assert page2["page"]["next_after"] is None
    assert page1["record_scope"] == "archived_feedback_snapshots"
    with pytest.raises(ArchiveError, match="does not match"):
        query_feedback(
            archive,
            FeedbackQuery(approval_id="f" * 64, after=page1["page"]["next_after"]),
        )


def test_real_sparse_filter_progress_and_byte_budget(archive):
    values = sorted(
        [recorded(str(n)) for n in range(106)], key=lambda value: value["feedback_id"]
    )
    for value in values:
        archive.register_feedback(value)
    target = values[-1]["evidence"]["execution_id"]
    first = query_feedback(archive, FeedbackQuery(execution_id=target, max_bytes=4096))
    assert not first["items"] and first["page"]["next_after"]
    assert (
        first["source_page"]["scanned_candidates"] == 100 and len(encode(first)) <= 4096
    )
    second = query_feedback(
        archive,
        FeedbackQuery(
            execution_id=target, after=first["page"]["next_after"], max_bytes=4096
        ),
    )
    assert second["items"][0]["feedback_id"] == values[-1]["feedback_id"]
    assert second["page"]["next_after"] is None
    small = query_feedback(
        archive, FeedbackQuery(feedback_id=values[-1]["feedback_id"], max_bytes=1024)
    )
    assert (
        small["status"] == "budget_exceeded"
        and small["exit_code"] == 3
        and len(encode(small)) <= 1024
    )


def test_real_corrupted_feedback_refuses_even_if_other_filters_would_not_match(archive):
    value = recorded()
    archive.register_feedback(value)
    with archive.transaction() as cursor:
        cursor.execute(
            archive.query(
                "UPDATE {schema}.workflow_feedback SET document = %s WHERE feedback_id = %s"
            ),
            (b"PRIVATE malformed content", value["feedback_id"]),
        )
    with pytest.raises(ArchiveError) as error:
        query_feedback(archive, FeedbackQuery(name="unmatched-name"))
    assert error.value.status == "feedback_corrupt" and "PRIVATE" not in str(
        error.value
    )


def test_real_schema2_query_refuses_without_implicit_upgrade(archive):
    with archive.transaction() as cursor:
        cursor.execute(archive.query("DROP TABLE {schema}.workflow_feedback"))
        cursor.execute(
            archive.query(
                "UPDATE {schema}.archive_meta SET schema_version = 2, migration_digest = %s"
            ),
            (migration_digest(2),),
        )
    with pytest.raises(ArchiveError) as error:
        query_feedback(archive, FeedbackQuery())
    assert error.value.status == "migration_required" and archive.schema_version() == 2


def test_real_cli_uses_existing_config_and_bounded_json(
    archive, tmp_path, monkeypatch, capsys
):
    from engineering_ci.cli import workflow

    monkeypatch.setattr(workflow, "ROOT", tmp_path)
    value = recorded(status="failed")
    archive.register_feedback(value)
    config = tmp_path / "trace.toml"
    config.write_text(
        f'schema_version = 1\n[archive]\nschema = "{archive.config.schema}"\n',
        encoding="utf-8",
    )
    output = tmp_path / ".test-tools/query.json"
    assert (
        cli(
            [
                "workflow",
                "feedback-query",
                "--feedback-id",
                value["feedback_id"],
                "--config",
                str(config),
                "--max-bytes",
                "4096",
                "--json",
                str(output),
            ]
        )
        == 0
    )
    result = read(output)
    assert result["items"][0]["recorded_result"]["exit_code"] == 1
    assert len(capsys.readouterr().out.encode()) <= 4096
    assert not list(tmp_path.glob("**/state.json"))
