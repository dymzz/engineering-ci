"""Real PostgreSQL → summary → original local receipts; no production capability mutation."""

import pytest
import test_workflow_feedback_review as review_fixtures
import trace_store

from engineering_ci.cli import main as cli
from engineering_ci.tracing.feedback_summary import FeedbackSummary, query_summary
from engineering_ci.workflow_core.documents import read, write

archive = trace_store.archive
case = review_fixtures.case
checkout = review_fixtures.checkout


def actual_cli(case, archive, monkeypatch):
    from engineering_ci.cli import workflow
    from engineering_ci.workflow_adapters import checks

    monkeypatch.setattr(workflow, "ROOT", case["root"])
    monkeypatch.setattr(checks, "catalog", lambda root: case["runtime"].catalog)
    value = case["values"][0]
    archive.register_feedback(value)
    summary = query_summary(archive, FeedbackSummary((value["feedback_id"],)))
    path = case["root"] / ".test-tools/summary.json"
    write(path, summary)
    config = case["root"] / "tracing.toml"
    config.write_text(
        f'schema_version = 1\n[archive]\nschema = "{archive.config.schema}"\n'
    )
    output = case["root"] / ".test-tools/review.json"
    return (
        [
            "workflow",
            "feedback-review",
            "--summary",
            str(path),
            "--store",
            str(case["store"].directory),
            "--execution",
            str(case["execution"]),
            "--config",
            str(config),
            "--json",
            str(output),
        ],
        path,
        output,
    )


def test_actual_postgres_summary_review_preserves_original_files_and_no_execution(
    case, archive, monkeypatch
):
    from engineering_ci.workflow_core import Runtime

    args, _, output = actual_cli(case, archive, monkeypatch)
    paths = [case["store"].directory / "catalog.json", case["execution"] / "state.json"]
    before = [path.read_bytes() for path in paths]
    calls = len(case["calls"])

    def prohibited(*args, **kwargs):
        pytest.fail("Review cannot execute or initialize")

    monkeypatch.setattr(Runtime, "resume", prohibited)
    monkeypatch.setattr(Runtime, "create", prohibited)
    monkeypatch.setattr(type(archive), "initialize", prohibited)
    assert cli(args) == 0
    value = read(output)
    assert value["items"][0]["recommendation"] == "retain_evidence"
    assert (
        value["items"][0]["feedback"][0]["execution_evidence_validation"]
        == "verified_exact_snapshot"
    )
    assert before == [path.read_bytes() for path in paths] and calls == len(
        case["calls"]
    )
    with archive.transaction(readonly=True) as cursor:
        cursor.execute(archive.query("SELECT count(*) FROM {schema}.workflow_feedback"))
        assert cursor.fetchone()[0] == 1


def test_actual_postgres_modified_summary_is_not_trusted(case, archive, monkeypatch):
    args, summary, output = actual_cli(case, archive, monkeypatch)
    value = read(summary)
    value["executions"][0]["metrics"]["unique_observed_attempts"] = 9999
    write(summary, value, replace=True)
    assert cli(args) == 3 and read(output)["status"] == "summary_changed_or_unavailable"


def test_actual_postgres_archive_corruption_never_becomes_review_evidence(
    case, archive, monkeypatch
):
    args, _, output = actual_cli(case, archive, monkeypatch)
    with archive.transaction() as cursor:
        cursor.execute(
            archive.query(
                "UPDATE {schema}.workflow_feedback SET document = %s WHERE feedback_id = %s"
            ),
            (b"PRIVATE damaged", case["values"][0]["feedback_id"]),
        )
    assert cli(args) == 3 and read(output)["status"] == "feedback_corrupt"


def test_actual_original_comparison_missing_and_disabled_version_are_not_auto_enabled(
    case, archive, monkeypatch
):
    args, _, output = actual_cli(case, archive, monkeypatch)
    case["store"].deactivate("pr-check-reuse")
    assert cli(args) == 0
    assert read(output)["items"][0]["enabled_at_read"] is False
    comparison = case["store"].location("pr-check-reuse", "1.0.0") / "comparison.json"
    comparison.unlink()
    assert (
        cli(args) == 0 and read(output)["items"][0]["recommendation"] == "investigate"
    )
    assert not case["store"].index()["active"]
