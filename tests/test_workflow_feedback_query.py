"""Read-only feedback pages, integrity, sparse filters, budgets and safe CLI errors."""

import copy
import hashlib
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from engineering_ci.cli import main as cli
from engineering_ci.cli import workflow
from engineering_ci.tracing.feedback_query import (
    FeedbackQuery,
    checked_row,
    query_feedback,
)
from engineering_ci.tracing.retrieval import encode
from engineering_ci.tracing.store import ArchiveError
from engineering_ci.workflow_core.documents import (
    WorkflowError,
    canonical,
    digest,
    read,
)


def recorded(
    seed="first",
    *,
    approval=None,
    execution=None,
    status="passed",
    completeness="complete",
    name="pr-check-reuse",
):
    """Construct valid archived protocol fixtures, without implying real tool execution."""
    value = {
        "schema_version": 1,
        "kind": "workflow-capability-feedback",
        "capability": {
            "name": name,
            "version": "1.0.0",
            "approval_id": approval or digest({"approval": seed}),
        },
        "definition_id": digest({"definition": seed}),
        "generation_id": "a" * 32,
        "catalog_revision": 2,
        "evidence": {
            "execution_id": execution or digest({"execution": seed})[:32],
            **{
                key: digest({key: seed})
                for key in (
                    "state_sha256",
                    "observations_sha256",
                    "provenance_sha256",
                    "evidence_sha256",
                )
            },
        },
        "recorded_result": {
            "phase": "FINISHED",
            "status": status,
            "exit_code": 1 if status == "failed" else 0,
        },
        "observation_completeness": completeness,
        "metrics": {
            "required_checks": 5,
            "observed_check_attempts": 5,
            "check_submissions": 0,
            "reused_checks": 5,
            "adapter_batches": 1,
        },
        "current_input_validity": "not_checked",
        "task_outcome_scope": "original-registered-checks",
        "optimization_evaluated": False,
        "llm_used": False,
    }
    value["feedback_id"] = digest(value)
    return value


def row(value):
    raw = canonical(value)
    return (
        value["feedback_id"],
        value["capability"]["approval_id"],
        hashlib.sha256(raw).hexdigest(),
        raw,
        datetime(2026, 10, 14, tzinfo=UTC),
    )


class MemoryArchive:
    """Model SQL source paging; tests assert bound parameters and read-only transactions."""

    def __init__(self, values, *, schema="feedback_test", database="database-a"):
        self.rows = sorted([row(value) for value in values])
        self.config = SimpleNamespace(schema=schema)
        self.database = database
        self.statements, self.transactions = [], []
        self.answer = []

    @contextmanager
    def transaction(self, *, readonly=False):
        self.transactions.append(readonly)
        assert readonly is True
        yield self

    def _owned(self, cursor, *, required_version):
        assert cursor is self and required_version == 3

    def query(self, statement):
        return statement

    def execute(self, statement, parameters=None):
        assert statement.startswith("SELECT")
        self.statements.append((statement, parameters))
        if "current_database()" in statement:
            self.answer = [(self.database, "127.0.0.1", 5432, "archive_reader")]
        elif "SELECT feedback_id FROM" in statement:
            key, approval, _ = parameters
            self.answer = [
                (item[0],)
                for item in self.rows
                if item[0] == key and approval in (None, item[1])
            ]
        else:
            identity, _, approval, _, after, _, limit = parameters
            assert limit == 101
            self.answer = [
                item
                for item in self.rows
                if identity in (None, item[0])
                and approval in (None, item[1])
                and (after is None or item[0] > after)
            ][:limit]

    def fetchone(self):
        return self.answer[0] if self.answer else None

    def fetchall(self):
        return self.answer


def test_failed_record_stays_failed_while_query_succeeds_read_only():
    value = recorded(status="failed", completeness="incomplete")
    store = MemoryArchive([value])
    result = query_feedback(store, FeedbackQuery(feedback_id=value["feedback_id"]))
    assert result["exit_code"] == 0 and result["status"] == "returned"
    item = result["items"][0]
    assert (
        item["recorded_result"] == value["recorded_result"]
        and item["recorded_result"]["exit_code"] == 1
    )
    assert item["current_input_validity"] == item["approval_state_now"] == "not_checked"
    assert item["execution_evidence_validation"] == "not_checked"
    assert (
        result["collection_coverage"] == "unknown"
        and result["record_scope"] == "archived_feedback_snapshots"
    )
    assert store.transactions == [True] and result["page"]["next_after"] is None


def test_filters_query_parameters_and_matching_schema_are_original_facts():
    failed = recorded("failed", status="failed", name="original-checks")
    passed = recorded("passed", approval=failed["capability"]["approval_id"])
    request = FeedbackQuery(
        approval_id=failed["capability"]["approval_id"],
        execution_id=failed["evidence"]["execution_id"],
        name="original-checks",
        version="1.0.0",
        result_status="failed",
        completeness="complete",
    )
    store = MemoryArchive([failed, passed, recorded("other-approval")])
    result = query_feedback(store, request)
    assert [item["feedback_id"] for item in result["items"]] == [failed["feedback_id"]]
    assert result["source_page"]["scanned_candidates"] == 2
    sql, parameters = store.statements[-1]
    assert (
        "approval_id = %s" in sql and failed["capability"]["approval_id"] in parameters
    )
    assert "original-checks" not in sql


def test_keyset_paging_has_no_duplicate_and_retains_two_snapshots_of_one_execution():
    values = [recorded(str(n), execution="1" * 32) for n in range(4)]
    store = MemoryArchive(values)
    seen, after = [], None
    for _ in range(4):
        result = query_feedback(store, FeedbackQuery(limit=1, after=after))
        seen.extend(item["feedback_id"] for item in result["items"])
        after = result["page"]["next_after"]
    assert len(set(seen)) == 4 and after is None
    assert seen == sorted(value["feedback_id"] for value in values)
    assert all(value["evidence"]["execution_id"] == "1" * 32 for value in values)


def test_sparse_filters_empty_page_has_a_progressing_source_bookmark():
    values = sorted(
        [recorded(str(n)) for n in range(106)], key=lambda value: value["feedback_id"]
    )
    wanted = values[-1]
    store = MemoryArchive(values)
    request = FeedbackQuery(execution_id=wanted["evidence"]["execution_id"])
    first = query_feedback(store, request)
    assert first["items"] == [] and first["page"]["next_after"]
    assert first["source_page"]["scanned_candidates"] == 100
    second = query_feedback(
        store,
        FeedbackQuery(
            execution_id=request.execution_id, after=first["page"]["next_after"]
        ),
    )
    assert [item["feedback_id"] for item in second["items"]] == [wanted["feedback_id"]]
    assert (
        second["page"]["next_after"] is None
        and second["source_page"]["scanned_candidates"] == 6
    )


@pytest.mark.parametrize(
    "change",
    [{"name": "other-name"}, {"approval_id": "f" * 64}, {"result_status": "failed"}],
)
def test_cursor_rejects_changed_filters(change):
    store = MemoryArchive([recorded(str(n)) for n in range(2)])
    first = query_feedback(store, FeedbackQuery(limit=1))
    with pytest.raises(ArchiveError, match="does not match"):
        query_feedback(
            store, FeedbackQuery(after=first["page"]["next_after"], **change)
        )


@pytest.mark.parametrize(
    "attribute,value", [("schema", "another_schema"), ("database", "another-database")]
)
def test_cursor_rejects_changed_database_namespace(attribute, value):
    store = MemoryArchive([recorded(str(n)) for n in range(2)])
    first = query_feedback(store, FeedbackQuery(limit=1))
    if attribute == "schema":
        store.config.schema = value
    else:
        store.database = value
    with pytest.raises(ArchiveError, match="does not match"):
        query_feedback(store, FeedbackQuery(after=first["page"]["next_after"]))


def test_deleted_bookmark_is_unavailable_not_a_false_complete_page():
    store = MemoryArchive([recorded(str(n)) for n in range(2)])
    first = query_feedback(store, FeedbackQuery(limit=1))
    store.rows.pop(0)
    with pytest.raises(ArchiveError, match="outside"):
        query_feedback(store, FeedbackQuery(after=first["page"]["next_after"]))


@pytest.mark.parametrize(
    "arguments",
    [
        {"feedback_id": "not-a-hash"},
        {"approval_id": "' OR true"},
        {"execution_id": "1" * 64},
        {"name": "../outside"},
        {"version": "v0.6.5"},
        {"result_status": "success"},
        {"completeness": "passed"},
        {"limit": 0},
        {"limit": 101},
        {"max_bytes": 1023},
        {"after": "invalid"},
        {"after": ""},
        {"feedback_id": "f" * 64, "after": ""},
    ],
)
def test_invalid_request_is_rejected_before_any_store_operation(arguments):
    store = MemoryArchive([])
    with pytest.raises((ArchiveError, WorkflowError)):
        query_feedback(store, FeedbackQuery(**arguments))
    assert store.transactions == [] and store.statements == []


@pytest.mark.parametrize("budget", [1024, 2048, 4096, 16384])
def test_budget_measures_entire_encoded_json_and_continuation_never_skips_unreturned_rows(
    budget,
):
    store = MemoryArchive([recorded(str(n)) for n in range(5)])
    result = query_feedback(store, FeedbackQuery(max_bytes=budget))
    assert len(encode(result)) <= budget
    if result["exit_code"] == 0 and result["page"]["next_after"]:
        first = {item["feedback_id"] for item in result["items"]}
        next_page = query_feedback(
            store, FeedbackQuery(max_bytes=16384, after=result["page"]["next_after"])
        )
        assert first.isdisjoint(item["feedback_id"] for item in next_page["items"])
        assert len(first) + len(next_page["items"]) == 5


@pytest.mark.parametrize(
    "mutation",
    [
        "payload",
        "checksum",
        "feedback_id",
        "approval_id",
        "sensitive_field",
        "noncanonical",
    ],
)
def test_corrupt_archival_bytes_and_unknown_sensitive_fields_fail_without_echo(
    mutation,
):
    original = recorded()
    bad = list(row(original))
    if mutation == "payload":
        bad[3] = b"PRIVATE: not JSON"
    elif mutation == "checksum":
        bad[2] = "f" * 64
    elif mutation == "feedback_id":
        bad[0] = "f" * 64
    elif mutation == "approval_id":
        bad[1] = "f" * 64
    else:
        value = copy.deepcopy(original)
        if mutation == "sensitive_field":
            value["token"] = "PRIVATE"
            bad[3] = canonical(value)
        else:
            bad[3] = json.dumps(value, indent=2).encode()
        bad[2] = hashlib.sha256(bad[3]).hexdigest()
    with pytest.raises(ArchiveError) as error:
        checked_row(tuple(bad))
    assert error.value.status == "feedback_corrupt" and "PRIVATE" not in str(
        error.value
    )


def test_no_results_and_single_missing_lookup_are_distinct():
    result = query_feedback(MemoryArchive([]), FeedbackQuery())
    assert (
        result["items"] == []
        and result["page"]["next_after"] is None
        and result["exit_code"] == 0
    )
    with pytest.raises(ArchiveError) as error:
        query_feedback(MemoryArchive([]), FeedbackQuery(feedback_id="f" * 64))
    assert error.value.status == "feedback_unavailable"


def test_cli_never_constructs_runtime_or_catalog_and_saves_only_query_receipts(
    tmp_path, monkeypatch, capsys
):
    from engineering_ci.tracing import store as store_module

    monkeypatch.setattr(workflow, "ROOT", tmp_path)

    def prohibited(*args, **kwargs):
        raise AssertionError(
            "Execution layer must not be constructed for an archive query"
        )

    monkeypatch.setattr(workflow, "catalog", prohibited)
    monkeypatch.setattr(workflow, "Runtime", prohibited)
    fake = MemoryArchive([recorded(status="failed")])

    @contextmanager
    def connection(config):
        yield fake

    monkeypatch.setattr(store_module, "open_store", connection)
    output = tmp_path / ".test-tools/query.json"
    assert cli(["workflow", "feedback-query", "--json", str(output)]) == 0
    value = read(output)
    assert value["items"][0]["recorded_result"]["exit_code"] == 1
    assert len(capsys.readouterr().out.encode()) <= 16384
    assert cli(["workflow", "feedback-query", "--json", str(output)]) == 0
    original = tmp_path / ".test-tools/original-feedback.json"
    original.write_bytes(canonical(recorded()))
    before = original.read_bytes()
    assert cli(["workflow", "feedback-query", "--json", str(original)]) == 3
    assert original.read_bytes() == before
    assert (
        cli(["workflow", "feedback-query", "--json", str(tmp_path / "catalog.json")])
        == 3
    )


def test_cli_invalid_flags_do_not_connect_and_private_driver_failure_is_safe(
    tmp_path, monkeypatch, capsys
):
    from engineering_ci.tracing import store as store_module

    monkeypatch.setattr(workflow, "ROOT", tmp_path)
    calls = []

    @contextmanager
    def failure(config):
        calls.append(config)
        raise RuntimeError("postgres://user:PRIVATE@host")
        yield  # pragma: no cover -- preserve the context manager protocol

    monkeypatch.setattr(store_module, "open_store", failure)
    assert cli(["workflow", "feedback-query", "--execution-id", "invalid"]) == 3
    assert not calls
    assert cli(["workflow", "feedback-query", "--after", ""]) == 3
    assert not calls
    output = tmp_path / ".test-tools/query.json"
    assert (
        cli(["workflow", "feedback-query", "--limit", "0", "--json", str(output)]) == 3
    )
    assert not calls and read(output)["exit_code"] == 3
    assert cli(["workflow", "feedback-query", "--json", str(output)]) == 3
    assert len(calls) == 1 and "PRIVATE" not in capsys.readouterr().out
    assert read(output)["exit_code"] == 3


@pytest.mark.parametrize(
    "status,sqlstate", [("permission_denied", "42501"), ("migration_required", None)]
)
def test_cli_archive_access_errors_are_distinct_from_empty_data(
    tmp_path, monkeypatch, capsys, status, sqlstate
):
    from engineering_ci.tracing import store as store_module

    monkeypatch.setattr(workflow, "ROOT", tmp_path)

    @contextmanager
    def denied(config):
        raise ArchiveError(
            status, "Archive access unavailable; inspect trace status", sqlstate
        )
        yield  # pragma: no cover

    monkeypatch.setattr(store_module, "open_store", denied)
    assert cli(["workflow", "feedback-query"]) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == status and result["sqlstate"] == sqlstate
    assert "items" not in result


def test_query_json_write_failure_reports_nonzero_and_does_not_change_archive(
    tmp_path, monkeypatch, capsys
):
    from engineering_ci.cli import workflow_feedback_query as query_cli
    from engineering_ci.tracing import store as store_module

    monkeypatch.setattr(workflow, "ROOT", tmp_path)
    fake = MemoryArchive([recorded()])
    before = copy.deepcopy(fake.rows)

    @contextmanager
    def connection(config):
        yield fake

    def unavailable(*args, **kwargs):
        raise OSError("PRIVATE failure")

    monkeypatch.setattr(store_module, "open_store", connection)
    monkeypatch.setattr(query_cli, "write", unavailable)
    assert (
        cli(
            [
                "workflow",
                "feedback-query",
                "--json",
                str(tmp_path / ".test-tools/query.json"),
            ]
        )
        == 3
    )
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "output_unavailable" and fake.rows == before
    assert "PRIVATE" not in result["reason"]
