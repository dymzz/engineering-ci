"""Explicit real host PostgreSQL feedback and schema migration acceptance."""

import hashlib

import pytest
import trace_store
from test_checks import checkout as check_checkout
from test_workflow_capabilities import enable, reviewed

from engineering_ci.tracing.store import ArchiveError, import_source, migration_digest
from engineering_ci.workflow_adapters.capabilities import create_call
from engineering_ci.workflow_adapters.feedback import prepare
from engineering_ci.workflow_core.documents import WorkflowError, canonical, digest

checkout = check_checkout
archive = trace_store.archive
rollout = trace_store.rollout


def actual_feedback(checkout):
    runtime, store, _, _, _, _, _ = reviewed(checkout)
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
    return prepare(directory, runtime.catalog, checkout)[0]


def test_actual_feedback_is_idempotent_read_back_and_not_a_projection(
    archive, checkout
):
    value = actual_feedback(checkout)
    first = archive.register_feedback(value)
    assert archive.register_feedback(value) == first
    with archive.transaction(readonly=True) as cursor:
        cursor.execute(
            archive.query(
                "SELECT document_digest, document FROM {schema}.workflow_feedback WHERE feedback_id = %s"
            ),
            (value["feedback_id"],),
        )
        checksum, payload = cursor.fetchone()
        assert (
            bytes(payload) == canonical(value)
            and checksum == hashlib.sha256(bytes(payload)).hexdigest()
        )
        cursor.execute(archive.query("SELECT count(*) FROM {schema}.workflow_feedback"))
        assert cursor.fetchone()[0] == 1
        cursor.execute(archive.query("SELECT count(*) FROM {schema}.derived_results"))
        assert cursor.fetchone()[0] == 0


def test_actual_v2_requires_explicit_migration_preserving_raw_archive(
    archive, checkout, tmp_path
):
    value = actual_feedback(checkout)
    source = rollout(tmp_path / "source.jsonl")
    captured = import_source(archive, source)
    original = b"".join(item.raw for item in archive.replay(captured["snapshot_id"]))
    with archive.transaction() as cursor:
        cursor.execute(archive.query("DROP TABLE {schema}.workflow_feedback"))
        cursor.execute(
            archive.query(
                "UPDATE {schema}.archive_meta SET schema_version = 2, migration_digest = %s"
            ),
            (migration_digest(2),),
        )
    with pytest.raises(ArchiveError) as error:
        archive.register_feedback(value)
    assert error.value.status == "migration_required" and archive.schema_version() == 2
    assert not archive.initialize() and archive.schema_version() == 3
    assert not archive.initialize()
    archive.register_feedback(value)
    assert (
        b"".join(item.raw for item in archive.replay(captured["snapshot_id"]))
        == original
    )


def test_actual_feedback_conflict_does_not_fabricate_registered(archive, checkout):
    value = actual_feedback(checkout)
    archive.register_feedback(value)
    with archive.transaction() as cursor:
        cursor.execute(
            archive.query(
                "UPDATE {schema}.workflow_feedback SET document = %s WHERE feedback_id = %s"
            ),
            (b"corrupt", value["feedback_id"]),
        )
    with pytest.raises(ArchiveError) as error:
        archive.register_feedback(value)
    assert error.value.status == "feedback_conflict"


def test_sensitive_or_forged_feedback_is_rejected_before_database_write(
    archive, checkout
):
    value = actual_feedback(checkout)
    value["token"] = "PRIVATE"
    with pytest.raises(WorkflowError):
        archive.register_feedback(value)
    del value["token"]
    value["feedback_id"] = "f" * 64
    with pytest.raises(ArchiveError) as error:
        archive.register_feedback(value)
    assert error.value.status == "invalid_feedback"
    value["feedback_id"] = digest(
        {key: item for key, item in value.items() if key != "feedback_id"}
    )
    archive.register_feedback(value)
