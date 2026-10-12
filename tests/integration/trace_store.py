"""Explicit host PostgreSQL acceptance: run this file, never silently skip it."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from engineering_ci.tracing.config import TraceConfig
from engineering_ci.tracing.sources.codex_rollout import (
    Cursor,
    RolloutSource,
    record_at,
)
from engineering_ci.tracing.store import ArchiveError, import_source, open_store


@pytest.fixture
def archive():
    """Create and remove only a unique, component-owned test namespace."""
    schema = "engineering_ci_trace_test_" + uuid4().hex[:16]
    config = TraceConfig(schema=schema, batch_size=2)
    with open_store(config) as store:
        assert store.initialize()
        try:
            yield store
        finally:
            assert config.schema == schema and schema.startswith(
                "engineering_ci_trace_test_"
            )
            with store.transaction() as cursor:
                store._owned(cursor)
                cursor.execute(store.query("DROP SCHEMA {schema} CASCADE"))


def rollout(
    path, content=b' {"type":"event_msg","payload":{"type":"task_complete"}}\r\n'
):
    header = (
        json.dumps(
            {
                "type": "session_meta",
                "timestamp": "2026-10-09T04:38:50.516Z",
                "payload": {"id": str(uuid4()), "cli_version": "0.162.0-alpha.2"},
            }
        ).encode()
        + b"\n"
    )
    path.write_bytes(header + content)
    return RolloutSource(path, batch_size=2)


def test_real_migration_repeat_and_public_access(archive):
    assert not archive.initialize()
    with archive.transaction(readonly=True) as cursor:
        cursor.execute(
            "SELECT nspacl::text FROM pg_namespace WHERE nspname = %s",
            (archive.config.schema,),
        )
        assert "=U/" not in (cursor.fetchone()[0] or "")


def test_real_roundtrip_repeat_and_cloud_event_bytes(archive, tmp_path):
    value = rollout(tmp_path / "source.jsonl")
    result = import_source(archive, value)
    assert (
        b"".join(r.raw for r in archive.replay(result["snapshot_id"]))
        == value.path.read_bytes()
    )
    assert result["completeness"] == "unknown"
    again = import_source(archive, RolloutSource(value.path))
    assert again["snapshot_id"] == result["snapshot_id"]
    with archive.transaction(readonly=True) as cursor:
        cursor.execute(
            archive.query(
                "SELECT count(*), bool_and(attributes->>'payloadhash' = payload_digest), bool_and(NOT attributes ? 'data_base64') FROM {schema}.events"
            )
        )
        assert cursor.fetchone() == (2, True, True)


def test_real_rollback_preserves_bytes_and_cursor_together(archive, tmp_path):
    value = rollout(tmp_path / "source.jsonl")
    batch = next(value.batches())
    with (
        pytest.raises(RuntimeError, match="rollback"),
        archive.connection.transaction(),
    ):
        archive.append(value.identity, batch)
        raise RuntimeError("rollback")
    assert archive.cursor(value.identity) == Cursor()
    with archive.transaction(readonly=True) as cursor:
        cursor.execute(archive.query("SELECT count(*) FROM {schema}.events"))
        assert cursor.fetchone() == (0,)


def test_committed_write_with_simulated_lost_ack_resumes_without_duplicates(
    archive, tmp_path
):
    value = rollout(tmp_path / "source.jsonl")
    batch = next(value.batches())
    archive.append(value.identity, batch)  # Real PostgreSQL commit.
    with pytest.raises(ConnectionError):
        raise ConnectionError("Injected client acknowledgement loss after commit")
    with open_store(archive.config) as resumed:
        assert resumed.cursor(value.identity) == batch.cursor
        assert resumed.append(value.identity, batch) == batch.cursor
        result = import_source(resumed, value)
        assert (
            b"".join(r.raw for r in resumed.replay(result["snapshot_id"]))
            == value.path.read_bytes()
        )


def test_real_identity_conflict_does_not_overwrite(archive, tmp_path):
    value = rollout(tmp_path / "source.jsonl")
    batch = next(value.batches())
    archive.append(value.identity, batch)
    changed = replace(
        batch,
        records=(
            record_at(
                batch.records[0].offset,
                batch.records[0].raw.replace(b"session_meta", b"session_erro"),
            ),
            *batch.records[1:],
        ),
    )
    with pytest.raises(ArchiveError) as error:
        archive.append(value.identity, changed)
    assert error.value.status == "identity_conflict"
    assert archive.cursor(value.identity) == batch.cursor


def test_real_tail_and_late_completion_keep_old_snapshot_immutable(archive, tmp_path):
    value = rollout(tmp_path / "source.jsonl", b'{"type":"event_msg"')
    old_bytes = value.path.read_bytes()
    old = import_source(archive, value)
    assert old["completeness"] == "incomplete"
    with value.path.open("ab") as stream:
        stream.write(b"}\n")
    new = import_source(archive, RolloutSource(value.path))
    assert new["snapshot_id"] != old["snapshot_id"] and new["completeness"] == "unknown"
    assert b"".join(r.raw for r in archive.replay(old["snapshot_id"])) == old_bytes
    assert (
        b"".join(r.raw for r in archive.replay(new["snapshot_id"]))
        == value.path.read_bytes()
    )


def test_real_corruption_refuses_replay_and_repeat_snapshot(archive, tmp_path):
    value = rollout(tmp_path / "source.jsonl")
    result = import_source(archive, value)
    with archive.transaction() as cursor:
        cursor.execute(
            archive.query(
                "UPDATE {schema}.events SET payload = %s WHERE source = %s AND record_offset = 0"
            ),
            (b"corrupted", value.identity.source),
        )
    with pytest.raises(ArchiveError) as error:
        list(archive.replay(result["snapshot_id"]))
    assert error.value.status == "archive_corrupt"
    with pytest.raises(ArchiveError):
        import_source(archive, value)


def test_real_stale_cursor_and_digest_conflict(archive, tmp_path):
    value = rollout(tmp_path / "source.jsonl", b"{}\n{}\n{}\n")
    first = next(value.batches())
    archive.append(value.identity, first)
    larger = next(RolloutSource(value.path, batch_size=10).batches())
    with pytest.raises(ArchiveError) as error:
        archive.append(value.identity, larger)
    assert error.value.status == "stale_cursor"
    following = next(value.batches(first.cursor))
    wrong = replace(following, previous=Cursor(first.cursor.offset, "0" * 64))
    with pytest.raises(ArchiveError) as error:
        archive.append(value.identity, wrong)
    assert error.value.status == "identity_conflict"


def test_real_foreign_marker_is_refused_without_schema_modification(archive):
    from engineering_ci.tracing.store import COMPONENT

    with archive.transaction() as cursor:
        cursor.execute(
            archive.query(
                "UPDATE {schema}.archive_meta SET component = 'foreign-component'"
            )
        )
    try:
        with pytest.raises(ArchiveError) as error:
            archive.initialize()
        assert error.value.status == "schema_conflict"
    finally:
        with archive.transaction() as cursor:
            cursor.execute(
                archive.query("UPDATE {schema}.archive_meta SET component = %s"),
                (COMPONENT,),
            )


def test_real_invalid_batch_cannot_advance_past_unarchived_bytes(archive, tmp_path):
    value = rollout(tmp_path / "source.jsonl")
    batch = next(value.batches())
    with pytest.raises(ArchiveError) as error:
        archive.append(
            value.identity,
            replace(batch, cursor=Cursor(batch.cursor.offset + 1, batch.cursor.digest)),
        )
    assert error.value.status == "invalid_batch"
    assert archive.cursor(value.identity) == Cursor()


CHILD = """
import json, sys, time
from pathlib import Path
from engineering_ci.tracing.config import TraceConfig
from engineering_ci.tracing.store import open_store, import_source, ArchiveError
from engineering_ci.tracing.sources.codex_rollout import RolloutSource
schema, path, marker, mode = sys.argv[1:]
with open_store(TraceConfig(schema=schema, batch_size=2)) as store:
    source = RolloutSource(path, batch_size=2)
    if mode == 'pending':
        with store.connection.transaction():
            store.append(source.identity, next(source.batches()))
            Path(marker).write_text('pending')
            time.sleep(30)
    elif mode == 'committed':
        store.append(source.identity, next(source.batches()))
        Path(marker).write_text('committed')
        time.sleep(30)
    else:
        try:
            result = import_source(store, source)
            Path(marker).write_text(json.dumps({'status':'passed','snapshot_id':result['snapshot_id']}))
        except ArchiveError as error:
            Path(marker).write_text(json.dumps({'status':error.status}))
"""


def child(archive, path, marker, mode):
    # Credentials are inherited in the process environment, never command arguments.
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            CHILD,
            archive.config.schema,
            str(path),
            str(marker),
            mode,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
        },
    )


def await_marker(process, marker):
    deadline = time.monotonic() + 15
    while (
        not marker.exists() and time.monotonic() < deadline and process.poll() is None
    ):
        time.sleep(0.05)
    assert marker.exists(), "Archive test child did not reach its checkpoint"


@pytest.mark.parametrize("mode", ["pending", "committed"])
def test_real_force_kill_before_and_after_commit(archive, tmp_path, mode):
    value = rollout(tmp_path / "source.jsonl", b"{}\n{}\n{}\n")
    marker = tmp_path / "checkpoint"
    process = child(archive, value.path, marker, mode)
    try:
        await_marker(process, marker)
    finally:
        process.kill()
        process.wait(timeout=10)
    saved = archive.cursor(value.identity)
    assert (saved.offset == 0) == (mode == "pending")
    result = import_source(archive, value)
    assert (
        hashlib.sha256(
            b"".join(r.raw for r in archive.replay(result["snapshot_id"]))
        ).hexdigest()
        == hashlib.sha256(value.path.read_bytes()).hexdigest()
    )


def test_real_two_process_importers_never_duplicate_or_skip(archive, tmp_path):
    value = rollout(tmp_path / "source.jsonl", b"{}\n" * 40)
    markers = [tmp_path / f"result-{i}.json" for i in range(2)]
    processes = [child(archive, value.path, marker, "import") for marker in markers]
    try:
        for process, marker in zip(processes, markers, strict=True):
            await_marker(process, marker)
            assert process.wait(timeout=15) == 0
            assert json.loads(marker.read_text())["status"] in {
                "passed",
                "stale_cursor",
            }
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
    result = import_source(archive, value)
    assert result["records"] == 41
    assert (
        b"".join(r.raw for r in archive.replay(result["snapshot_id"]))
        == value.path.read_bytes()
    )
