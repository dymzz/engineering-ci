"""Bounded archive queries and explicit projection/analysis evidence registration."""

from __future__ import annotations

from datetime import UTC

from .results import canonical, digest, result_id
from .store import ArchiveError


def page_limit(value):
    """Keep metadata lists bounded without scanning payloads."""
    if type(value) is not int or not 1 <= value <= 100:
        raise ArchiveError(
            "invalid_query", "Query limit must be an integer between 1 and 100"
        )
    return value


def timestamp(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class TraceCatalog:
    """Query evidence and link derived results; no execution or migration is implicit."""

    def __init__(self, store):
        self.store = store

    def sources(self, *, limit=20, after=None):
        limit = page_limit(limit)
        with self.store.transaction(readonly=True) as cursor:
            self.store._owned(cursor)
            cursor.execute(
                self.store.query(
                    "SELECT source, session_id, format, runtime_version, cursor_offset, cursor_digest FROM {schema}.sources WHERE (%s::text IS NULL OR source > %s) ORDER BY source LIMIT %s"
                ),
                (after, after, limit + 1),
            )
            rows = cursor.fetchall()
        items = [self._source(row) for row in rows[:limit]]
        return {
            "items": items,
            "limit": limit,
            "next_after": items[-1]["source"] if len(rows) > limit else None,
        }

    @staticmethod
    def _source(row):
        return {
            "source": row[0],
            "session_id": row[1],
            "format": row[2],
            "runtime_version": row[3],
            "cursor": {"offset": row[4], "digest": row[5]},
            "recovery_validation": "file_prefix_not_checked",
        }

    def source(self, source):
        with self.store.transaction(readonly=True) as cursor:
            self.store._owned(cursor)
            return self._read_source(cursor, source)

    def _read_source(self, cursor, source):
        cursor.execute(
            self.store.query(
                "SELECT source, session_id, format, runtime_version, cursor_offset, cursor_digest FROM {schema}.sources WHERE source = %s"
            ),
            (source,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ArchiveError("source_unavailable", "Source has no committed archive")
        return self._source(row)

    @staticmethod
    def _snapshot(row):
        return {
            "snapshot_id": row[0],
            "source": row[1],
            "observed_bytes": row[2],
            "capture_digest": row[3],
            "completeness": row[4],
            "registered_at": timestamp(row[5]),
            "records": row[6],
        }

    def snapshot(self, snapshot):
        result_id(snapshot)
        with self.store.transaction(readonly=True) as cursor:
            self.store._owned(cursor)
            return self._read_snapshot(cursor, snapshot)

    def _read_snapshot(self, cursor, snapshot):
        cursor.execute(
            self.store.query(
                "SELECT s.snapshot_id, s.source, s.observed_bytes, s.capture_digest, s.completeness, s.created_at, (SELECT count(*) FROM {schema}.snapshot_members m WHERE m.snapshot_id = s.snapshot_id) FROM {schema}.snapshots s WHERE s.snapshot_id = %s"
            ),
            (snapshot,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ArchiveError("snapshot_unavailable", "Snapshot does not exist")
        return self._snapshot(row)

    def snapshots(self, *, source=None, limit=20, after=None):
        limit = page_limit(limit)
        with self.store.transaction(readonly=True) as cursor:
            self.store._owned(cursor)
            if after:
                result_id(after)
                cursor.execute(
                    self.store.query(
                        "SELECT created_at FROM {schema}.snapshots WHERE snapshot_id = %s AND (%s::text IS NULL OR source = %s)"
                    ),
                    (after, source, source),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ArchiveError(
                        "invalid_query", "Snapshot page cursor is outside this query"
                    )
                before = row[0]
            else:
                before = None
            cursor.execute(
                self.store.query(
                    "SELECT s.snapshot_id, s.source, s.observed_bytes, s.capture_digest, s.completeness, s.created_at, (SELECT count(*) FROM {schema}.snapshot_members m WHERE m.snapshot_id = s.snapshot_id) FROM {schema}.snapshots s WHERE (%s::text IS NULL OR s.source = %s) AND (%s::timestamptz IS NULL OR (s.created_at, s.snapshot_id) < (%s::timestamptz, %s::text)) ORDER BY s.created_at DESC, s.snapshot_id DESC LIMIT %s"
                ),
                (source, source, before, before, after, limit + 1),
            )
            rows = cursor.fetchall()
        items = [self._snapshot(row) for row in rows[:limit]]
        return {
            "items": items,
            "limit": limit,
            "next_after": items[-1]["snapshot_id"] if len(rows) > limit else None,
        }

    @staticmethod
    def _result(row):
        return {
            "result_id": row[0],
            "kind": row[1],
            "snapshot_id": row[2],
            "parent_result_id": row[3],
            "input_digest": row[4],
            "document_digest": row[5],
            "summary": row[6],
            "origin": row[7],
            "registered_at": timestamp(row[8]),
            "verification": "registered_identity_and_metadata; document_not_read",
        }

    def result(self, identifier):
        result_id(identifier)
        with self.store.transaction(readonly=True) as cursor:
            self.store._owned(cursor, required_version=2)
            return self._read_result(cursor, identifier)

    def _read_result(self, cursor, identifier):
        cursor.execute(
            self.store.query(
                "SELECT result_id, kind, snapshot_id, parent_result_id, input_digest, document_digest, summary, origin, created_at FROM {schema}.derived_results WHERE result_id = %s"
            ),
            (identifier,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ArchiveError("result_unavailable", "Derived result is not registered")
        return self._result(row)

    def results(self, *, snapshot=None, kind=None, limit=20, after=None):
        limit = page_limit(limit)
        if snapshot:
            result_id(snapshot)
        if kind not in {None, "projection", "analysis"}:
            raise ArchiveError("invalid_query", "Unknown derived result kind")
        with self.store.transaction(readonly=True) as cursor:
            self.store._owned(cursor, required_version=2)
            if after:
                result_id(after)
                cursor.execute(
                    self.store.query(
                        "SELECT created_at FROM {schema}.derived_results WHERE result_id = %s AND (%s::text IS NULL OR snapshot_id = %s) AND (%s::text IS NULL OR kind = %s)"
                    ),
                    (after, snapshot, snapshot, kind, kind),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ArchiveError(
                        "invalid_query", "Result page cursor is outside this query"
                    )
                before = row[0]
            else:
                before = None
            cursor.execute(
                self.store.query(
                    "SELECT result_id, kind, snapshot_id, parent_result_id, input_digest, document_digest, summary, origin, created_at FROM {schema}.derived_results WHERE (%s::text IS NULL OR snapshot_id = %s) AND (%s::text IS NULL OR kind = %s) AND (%s::timestamptz IS NULL OR (created_at, result_id) < (%s::timestamptz, %s::text)) ORDER BY created_at DESC, result_id DESC LIMIT %s"
                ),
                (snapshot, snapshot, kind, kind, before, before, after, limit + 1),
            )
            rows = cursor.fetchall()
        items = [self._result(row) for row in rows[:limit]]
        return {
            "items": items,
            "limit": limit,
            "next_after": items[-1]["result_id"] if len(rows) > limit else None,
        }

    def document(self, identifier):
        """Read one explicitly selected derived JSON and verify its byte digest."""
        result_id(identifier)
        with self.store.transaction(readonly=True) as cursor:
            self.store._owned(cursor, required_version=2)
            cursor.execute(
                self.store.query(
                    "SELECT document, document_digest FROM {schema}.derived_results WHERE result_id = %s"
                ),
                (identifier,),
            )
            row = cursor.fetchone()
        if row is None:
            raise ArchiveError("result_unavailable", "Derived result is not registered")
        raw = bytes(row[0])
        if digest(raw) != row[1]:
            raise ArchiveError(
                "archive_corrupt", "Derived result document digest does not match"
            )
        return raw

    def lineage(self, identifier):
        """Resolve actual stored foreign-key links; never infer parents from filenames."""
        result_id(identifier)
        with self.store.transaction(readonly=True) as cursor:
            self.store._owned(cursor, required_version=2)
            result = self._read_result(cursor, identifier)
            snapshot = self._read_snapshot(cursor, result["snapshot_id"])
            source = self._read_source(cursor, snapshot["source"])
            parent = (
                self._read_result(cursor, result["parent_result_id"])
                if result["parent_result_id"]
                else None
            )
        return {
            "source": source,
            "snapshot": snapshot,
            "projection": parent or result,
            "analysis": result if parent else None,
        }

    def save(self, record, *, projection_input=None):
        """Persist one result and its checked input association without rerunning tools."""
        from psycopg.types.json import Jsonb

        with self.store.transaction() as cursor:
            self.store._owned(cursor, required_version=2)
            cursor.execute(
                self.store.query(
                    "SELECT capture_digest, completeness FROM {schema}.snapshots WHERE snapshot_id = %s"
                ),
                (record.snapshot_id,),
            )
            snapshot = cursor.fetchone()
            if snapshot is None:
                raise ArchiveError(
                    "snapshot_unavailable", "Result refers to an unavailable snapshot"
                )
            if record.summary["completeness"] != snapshot[1]:
                raise ArchiveError(
                    "identity_conflict",
                    "Result completeness differs from its archived snapshot",
                )
            if record.kind == "projection":
                if record.input_digest != snapshot[0]:
                    raise ArchiveError(
                        "identity_conflict",
                        "Projection input differs from its archived snapshot",
                    )
            else:
                cursor.execute(
                    self.store.query(
                        "SELECT kind, snapshot_id, document_digest FROM {schema}.derived_results WHERE result_id = %s"
                    ),
                    (record.parent_result_id,),
                )
                parent = cursor.fetchone()
                if parent is None:
                    raise ArchiveError(
                        "result_unavailable",
                        "Register the input projection before its analysis",
                    )
                if (
                    parent[:2] != ("projection", record.snapshot_id)
                    or projection_input is None
                    or parent[2] != digest(canonical(projection_input))
                ):
                    raise ArchiveError(
                        "identity_conflict",
                        "Analysis input differs from the registered projection",
                    )
            cursor.execute(
                self.store.query(
                    "INSERT INTO {schema}.derived_results (result_id, kind, snapshot_id, parent_result_id, input_digest, document_digest, document, summary, origin) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING"
                ),
                (
                    record.result_id,
                    record.kind,
                    record.snapshot_id,
                    record.parent_result_id,
                    record.input_digest,
                    record.document_digest,
                    record.document,
                    Jsonb(record.summary),
                    record.origin,
                ),
            )
            cursor.execute(
                self.store.query(
                    "SELECT kind, snapshot_id, parent_result_id, input_digest, document_digest, document, summary FROM {schema}.derived_results WHERE result_id = %s"
                ),
                (record.result_id,),
            )
            row = cursor.fetchone()
            if row is None or (*row[:5], bytes(row[5]), row[6]) != (
                record.kind,
                record.snapshot_id,
                record.parent_result_id,
                record.input_digest,
                record.document_digest,
                record.document,
                record.summary,
            ):
                raise ArchiveError(
                    "identity_conflict",
                    "Result identity is already registered with different content",
                )
        return {
            "result_id": record.result_id,
            "kind": record.kind,
            "snapshot_id": record.snapshot_id,
            "parent_result_id": record.parent_result_id,
            "association": "registered",
        }
