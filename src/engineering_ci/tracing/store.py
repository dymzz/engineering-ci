"""PostgreSQL byte archive with atomic source cursors and immutable snapshots."""

from __future__ import annotations

import hashlib
import importlib
import os
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from .config import DATABASE_ENV, TraceConfig
from .envelope import attributes
from .preflight import failure
from .sources.codex_rollout import Cursor, record_at

COMPONENT = "engineering-ci.trace"
SCHEMA_VERSION = 1
MIGRATION = Path(__file__).with_name("migrations") / "001.sql"


def migration_digest():
    """Use normalized SQL text so checkout line endings do not change ownership."""
    return hashlib.sha256(MIGRATION.read_text(encoding="utf-8").encode()).hexdigest()


class ArchiveError(RuntimeError):
    """Keep driver diagnostics and connection parameters outside public results."""

    def __init__(self, status, reason, sqlstate=None):
        self.status, self.sqlstate = status, sqlstate
        super().__init__(reason)


@contextmanager
def open_store(config, *, environ=None):
    """Connect lazily, close reliably, and translate only actual driver failures."""
    if not isinstance(config, TraceConfig):
        raise ArchiveError("invalid_configuration", "Invalid tracing configuration")
    env = os.environ if environ is None else environ
    conninfo = env.get(DATABASE_ENV)
    if not isinstance(conninfo, str) or not conninfo.strip():
        raise ArchiveError("missing_configuration", f"{DATABASE_ENV} is not configured")
    try:
        driver = importlib.import_module("psycopg")
    except (ImportError, OSError):
        raise ArchiveError(
            "missing_tool", "Install the trace extra to enable Psycopg 3"
        ) from None
    try:
        driver.conninfo.conninfo_to_dict(conninfo)
    except driver.ProgrammingError:
        raise ArchiveError(
            "invalid_configuration", "Invalid PostgreSQL conninfo"
        ) from None
    try:
        with driver.connect(
            conninfo,
            connect_timeout=config.connect_timeout,
            application_name="engineering-ci-trace",
            autocommit=True,
        ) as connection:
            yield ArchiveStore(connection, config)
    except (driver.Error, OSError) as error:
        result = failure(error, config.schema)
        raise ArchiveError(
            result.status,
            "PostgreSQL archive operation failed; resume by querying the same source",
            result.sqlstate,
        ) from None


class ArchiveStore:
    """Store only archival evidence; never advance an execution FSM."""

    def __init__(self, connection, config):
        from psycopg import sql

        self.connection, self.config = connection, config
        self.schema = sql.Identifier(config.schema)

    def query(self, statement):
        from psycopg import sql

        return sql.SQL(statement).format(schema=self.schema)

    @contextmanager
    def transaction(self, *, readonly=False):
        """Bound statements and locks locally, including consistent snapshot reads."""
        with self.connection.transaction(), self.connection.cursor() as cursor:
            if readonly:
                cursor.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                )
            cursor.execute(
                "SELECT set_config('statement_timeout', %s, true), set_config('lock_timeout', %s, true)",
                (
                    f"{self.config.statement_timeout * 1000}ms",
                    f"{self.config.lock_timeout * 1000}ms",
                ),
            )
            yield cursor

    def _owned(self, cursor):
        """Reject uninitialized, foreign or unsupported namespaces without modifying them."""
        cursor.execute(
            "SELECT to_regclass(%s)", (f"{self.config.schema}.archive_meta",)
        )
        if cursor.fetchone()[0] is None:
            raise ArchiveError(
                "archive_uninitialized",
                "Archive schema is not initialized by this component; run trace init explicitly",
            )
        cursor.execute(
            self.query(
                "SELECT component, schema_version, migration_digest FROM {schema}.archive_meta WHERE singleton"
            )
        )
        row = cursor.fetchone()
        expected = (COMPONENT, SCHEMA_VERSION, migration_digest())
        if row != expected:
            raise ArchiveError(
                "schema_conflict",
                "Archive schema ownership or migration version does not match",
            )
        cursor.execute(
            "SELECT to_regclass(%s), to_regclass(%s), to_regclass(%s), to_regclass(%s)",
            tuple(
                f"{self.config.schema}.{name}"
                for name in ("sources", "events", "snapshots", "snapshot_members")
            ),
        )
        if any(item is None for item in cursor.fetchone()):
            raise ArchiveError(
                "archive_corrupt", "Archive schema has missing component tables"
            )

    def initialize(self):
        """Create the dedicated namespace once; never adopt arbitrary existing objects."""
        with self.transaction() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(current_database() || %s, 0))",
                (":" + self.config.schema,),
            )
            cursor.execute("SELECT to_regnamespace(%s)", (self.config.schema,))
            if cursor.fetchone()[0] is not None:
                self._owned(cursor)
                return False
            cursor.execute(self.query("CREATE SCHEMA {schema}"))
            cursor.execute(self.query("REVOKE ALL ON SCHEMA {schema} FROM PUBLIC"))
            cursor.execute(self.query(MIGRATION.read_text(encoding="utf-8")))
            cursor.execute(
                self.query(
                    "INSERT INTO {schema}.archive_meta VALUES (true, %s, %s, %s)"
                ),
                (COMPONENT, SCHEMA_VERSION, migration_digest()),
            )
        return True

    def cursor(self, identity):
        """Read the committed recovery point, never a speculative sequence watermark."""
        with self.transaction(readonly=True) as cursor:
            self._owned(cursor)
            cursor.execute(
                self.query(
                    "SELECT cursor_offset, cursor_digest FROM {schema}.sources WHERE source = %s"
                ),
                (identity.source,),
            )
            row = cursor.fetchone()
            return Cursor(*row) if row else Cursor()

    def append(self, identity, batch):
        """Commit bytes and cursor together; same identity with different content is an error."""
        from psycopg.types.json import Jsonb

        expected = batch.previous.offset
        saw_fragment = False
        for record in batch.records:
            if (
                record.offset != expected
                or saw_fragment
                or not record.raw
                or type(record.fragment) is not bool
            ):
                raise ArchiveError(
                    "invalid_batch",
                    "Archive batch has a gap, overlap or invalid fragment",
                )
            saw_fragment = record.fragment
            expected += len(record.raw)
        complete_end = (
            expected - len(batch.records[-1].raw) if saw_fragment else expected
        )
        if (
            batch.cursor.offset != complete_end
            or expected > batch.observed_bytes
            or batch.final
            and expected != batch.observed_bytes
            or saw_fragment
            and not batch.final
        ):
            raise ArchiveError(
                "invalid_batch", "Archive batch does not match its capture cursor"
            )

        with self.transaction() as cursor:
            self._owned(cursor)
            cursor.execute(
                self.query(
                    "INSERT INTO {schema}.sources (source, session_id, header_digest, runtime_version, format, cursor_digest) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING"
                ),
                (
                    identity.source,
                    identity.session_id,
                    identity.header_digest,
                    identity.runtime_version,
                    identity.format,
                    Cursor().digest,
                ),
            )
            cursor.execute(
                self.query(
                    "SELECT cursor_offset, cursor_digest, session_id, header_digest, runtime_version, format FROM {schema}.sources WHERE source = %s FOR UPDATE"
                ),
                (identity.source,),
            )
            row = cursor.fetchone()
            saved = Cursor(*row[:2])
            if tuple(row[2:]) != (
                identity.session_id,
                identity.header_digest,
                identity.runtime_version,
                identity.format,
            ):
                raise ArchiveError(
                    "identity_conflict",
                    "Source metadata conflicts with the archived identity",
                )
            if (
                saved.offset == batch.previous.offset
                and saved.digest != batch.previous.digest
                or saved.offset == batch.cursor.offset
                and saved.digest != batch.cursor.digest
            ):
                raise ArchiveError(
                    "identity_conflict",
                    "Source cursor digest conflicts with the archive",
                )
            duplicate = saved != batch.previous
            if duplicate and saved.offset < batch.cursor.offset:
                raise ArchiveError(
                    "stale_cursor",
                    "Another importer advanced this source; resume from its committed cursor",
                )
            for record in batch.records:
                attrs = attributes(identity, record)
                if not duplicate:
                    cursor.execute(
                        self.query(
                            "INSERT INTO {schema}.events (source, event_id, record_offset, payload, payload_digest, attributes, parse_status, fragment) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING"
                        ),
                        (
                            identity.source,
                            record.event_id,
                            record.offset,
                            record.raw,
                            record.digest,
                            Jsonb(attrs),
                            record.parse_status,
                            record.fragment,
                        ),
                    )
                cursor.execute(
                    self.query(
                        "SELECT payload, payload_digest, attributes, record_offset, parse_status, fragment FROM {schema}.events WHERE source = %s AND event_id = %s"
                    ),
                    (identity.source, record.event_id),
                )
                existing = cursor.fetchone()
                if existing is None or (bytes(existing[0]), *existing[1:]) != (
                    record.raw,
                    record.digest,
                    attrs,
                    record.offset,
                    record.parse_status,
                    record.fragment,
                ):
                    raise ArchiveError(
                        "identity_conflict",
                        "Archived event identity has different bytes or metadata",
                    )
            if duplicate:
                return saved
            cursor.execute(
                self.query(
                    "UPDATE {schema}.sources SET cursor_offset = %s, cursor_digest = %s WHERE source = %s"
                ),
                (batch.cursor.offset, batch.cursor.digest, identity.source),
            )
        return batch.cursor

    def snapshot(self, identity, batch):
        """Verify a capture and persist its exact ordered member set in one transaction."""
        if not batch.final or not batch.capture_digest:
            raise ArchiveError(
                "invalid_snapshot", "Snapshot requires a complete capture boundary"
            )
        snapshot_id = hashlib.sha256(
            f"{identity.source}:{batch.observed_bytes}:{batch.capture_digest}".encode()
        ).hexdigest()
        fragment_id = next((r.event_id for r in batch.records if r.fragment), None)
        with self.transaction() as cursor:
            self._owned(cursor)
            cursor.execute(
                self.query(
                    "SELECT source FROM {schema}.sources WHERE source = %s FOR SHARE"
                ),
                (identity.source,),
            )
            if cursor.fetchone() is None:
                raise ArchiveError(
                    "invalid_snapshot", "Source has no committed archive"
                )
            # A server-side cursor keeps large captures out of process memory.
            with self.connection.cursor(name="trace_snapshot_members") as reader:
                reader.execute(
                    self.query(
                        "SELECT event_id, record_offset, payload, payload_digest, parse_status FROM {schema}.events WHERE source = %s AND ((NOT fragment AND record_offset < %s) OR event_id = %s) ORDER BY record_offset"
                    ),
                    (identity.source, batch.cursor.offset, fragment_id),
                )
                hasher, offset, members, incomplete = hashlib.sha256(), 0, [], False
                for event_id, start, raw, digest, parse_status in reader:
                    raw = bytes(raw)
                    if start != offset or hashlib.sha256(raw).hexdigest() != digest:
                        raise ArchiveError(
                            "archive_corrupt",
                            "Archived bytes are missing, changed or out of order",
                        )
                    hasher.update(raw)
                    offset += len(raw)
                    members.append(event_id)
                    incomplete |= parse_status != "parsed"
            if (
                offset != batch.observed_bytes
                or hasher.hexdigest() != batch.capture_digest
            ):
                raise ArchiveError(
                    "archive_corrupt",
                    "Archived capture does not match the observed source digest",
                )
            completeness = "incomplete" if incomplete else "unknown"
            cursor.execute(
                self.query(
                    "INSERT INTO {schema}.snapshots (snapshot_id, source, observed_bytes, capture_digest, completeness) VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING snapshot_id"
                ),
                (
                    snapshot_id,
                    identity.source,
                    offset,
                    batch.capture_digest,
                    completeness,
                ),
            )
            if cursor.fetchone():
                cursor.executemany(
                    self.query(
                        "INSERT INTO {schema}.snapshot_members VALUES (%s, %s, %s, %s)"
                    ),
                    [
                        (snapshot_id, i, identity.source, event_id)
                        for i, event_id in enumerate(members)
                    ],
                )
            else:
                cursor.execute(
                    self.query(
                        "SELECT source, observed_bytes, capture_digest, completeness FROM {schema}.snapshots WHERE snapshot_id = %s"
                    ),
                    (snapshot_id,),
                )
                if cursor.fetchone() != (
                    identity.source,
                    offset,
                    batch.capture_digest,
                    completeness,
                ):
                    raise ArchiveError(
                        "identity_conflict",
                        "Snapshot metadata conflicts with its identity",
                    )
                cursor.execute(
                    self.query(
                        "SELECT event_id FROM {schema}.snapshot_members WHERE snapshot_id = %s ORDER BY position"
                    ),
                    (snapshot_id,),
                )
                if [row[0] for row in cursor] != members:
                    raise ArchiveError(
                        "archive_corrupt",
                        "Snapshot member set conflicts with the original capture",
                    )
        return {
            "snapshot_id": snapshot_id,
            "source": identity.source,
            "observed_bytes": offset,
            "capture_digest": batch.capture_digest,
            "records": len(members),
            "completeness": completeness,
        }

    def replay(self, snapshot_id):
        """Yield verified raw members; this operation never reexecutes archived tools."""
        with self.transaction(readonly=True) as cursor:
            self._owned(cursor)
            cursor.execute(
                self.query(
                    "SELECT observed_bytes, capture_digest FROM {schema}.snapshots WHERE snapshot_id = %s"
                ),
                (snapshot_id,),
            )
            expected = cursor.fetchone()
            if expected is None:
                raise ArchiveError("snapshot_unavailable", "Snapshot does not exist")
            offset, hasher = 0, hashlib.sha256()
            with self.connection.cursor(name="trace_replay_members") as reader:
                reader.execute(
                    self.query(
                        "SELECT e.record_offset, e.payload, e.payload_digest, e.fragment FROM {schema}.snapshot_members m JOIN {schema}.events e ON e.source = m.source AND e.event_id = m.event_id WHERE m.snapshot_id = %s ORDER BY m.position"
                    ),
                    (snapshot_id,),
                )
                for start, raw, digest, fragment in reader:
                    raw = bytes(raw)
                    if start != offset or hashlib.sha256(raw).hexdigest() != digest:
                        raise ArchiveError(
                            "archive_corrupt",
                            "Snapshot member bytes have changed or are missing",
                        )
                    hasher.update(raw)
                    offset += len(raw)
                    yield record_at(start, raw, fragment=fragment)
            if (offset, hasher.hexdigest()) != expected:
                raise ArchiveError(
                    "archive_corrupt", "Snapshot byte count or digest does not match"
                )


def import_source(store, source):
    """Resume a single explicit file and return only confirmed archive evidence."""
    cursor = store.cursor(source.identity)
    batches = 0
    for batch in source.batches(cursor):
        store.append(source.identity, batch)
        batches += 1
        if batch.final:
            snapshot = store.snapshot(source.identity, batch)
    return {**snapshot, "cursor": asdict(batch.cursor), "batches": batches}
