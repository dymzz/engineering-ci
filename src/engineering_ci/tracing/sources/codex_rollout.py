"""Read a fixed prefix of a Codex rollout without rewriting or executing records."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import UUID

EMPTY_DIGEST = hashlib.sha256(b"").hexdigest()
TESTED_RUNTIME = "0.162.0-alpha.2"
KNOWN_TYPES = {
    "session_meta",
    "event_msg",
    "response_item",
    "world_state",
    "turn_context",
    "token_usage_record",
    "compacted",
}


class SourceError(ValueError):
    """Report source failures without quoting file contents."""

    def __init__(self, status, reason):
        self.status = status
        super().__init__(reason)


@dataclass(frozen=True)
class Cursor:
    offset: int = 0
    digest: str = EMPTY_DIGEST

    def __post_init__(self):
        if (
            type(self.offset) is not int
            or self.offset < 0
            or not isinstance(self.digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", self.digest)
        ):
            raise SourceError("invalid_cursor", "Invalid source cursor")


@dataclass(frozen=True)
class SourceIdentity:
    source: str
    session_id: str
    header_digest: str
    runtime_version: str | None
    format: str = "codex-rollout"


@dataclass(frozen=True)
class Record:
    offset: int
    raw: bytes = field(repr=False)
    parse_status: str
    kind: str
    timestamp: str | None
    fragment: bool = False

    @property
    def digest(self):
        return hashlib.sha256(self.raw).hexdigest()

    @property
    def event_id(self):
        return (
            f"tail:{self.offset}:{self.digest}" if self.fragment else str(self.offset)
        )


@dataclass(frozen=True)
class Batch:
    previous: Cursor
    cursor: Cursor
    records: tuple[Record, ...]
    observed_bytes: int
    final: bool
    capture_digest: str | None = None


def decode_record(raw):
    """Parse only for derived metadata; the original bytes remain authoritative."""
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def record_at(offset, raw, *, fragment=False):
    value = None if fragment else decode_record(raw)
    status = (
        "truncated" if fragment else "parsed" if value is not None else "invalid_json"
    )
    kind = value.get("type") if value else None
    kind = kind if isinstance(kind, str) and kind in KNOWN_TYPES else "unknown"
    timestamp = None
    candidate = value.get("timestamp") if value else None
    if isinstance(candidate, str) and len(candidate) <= 64:
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.utcoffset() is not None:
                timestamp = candidate
        except ValueError:
            pass
    return Record(offset, raw, status, kind, timestamp, fragment)


class RolloutSource:
    """Freeze file length, identify its stream, and verify prior bytes on resume."""

    def __init__(self, path, *, max_record_bytes=4 * 1024 * 1024, batch_size=100):
        self.path = Path(path)
        if type(max_record_bytes) is not int or max_record_bytes <= 0:
            raise SourceError("invalid_configuration", "Invalid record size limit")
        if type(batch_size) is not int or batch_size <= 0:
            raise SourceError("invalid_configuration", "Invalid batch size")
        self.max_record_bytes = max_record_bytes
        self.batch_size = batch_size
        try:
            with self.path.open("rb") as stream:
                self.observed_bytes = stream.seek(0, 2)
                stream.seek(0)
                header = stream.readline(min(self.observed_bytes, max_record_bytes + 1))
        except OSError:
            raise SourceError(
                "source_unavailable", "Cannot read the selected source file"
            ) from None
        if len(header) > max_record_bytes:
            raise SourceError(
                "payload_limit", "Source header exceeds the record size limit"
            )
        value = decode_record(header)
        payload = value.get("payload") if value else None
        if (
            value is None
            or not header.endswith(b"\n")
            or value.get("type") != "session_meta"
        ):
            raise SourceError(
                "unsupported_source", "Expected a complete Codex rollout session header"
            )
        try:
            session_id = str(UUID(payload["id"]))
        except (KeyError, TypeError, ValueError, AttributeError):
            raise SourceError(
                "unsupported_source", "Source session identity is unavailable"
            ) from None
        version = payload.get("cli_version")
        version = (
            version
            if isinstance(version, str)
            and re.fullmatch(r"[0-9][0-9A-Za-z.+-]{0,63}", version)
            else None
        )
        header_digest = hashlib.sha256(header).hexdigest()
        self.identity = SourceIdentity(
            f"urn:engineering-ci:codex-rollout:{session_id}:{header_digest}",
            session_id,
            header_digest,
            version,
        )

    def batches(self, cursor=None):
        """Yield bounded atomic batches; an unfinished tail never advances the cursor."""
        try:
            with self.path.open("rb") as stream:
                yield from self._read(stream, Cursor() if cursor is None else cursor)
        except OSError:
            raise SourceError(
                "source_unavailable", "Cannot read the selected source file"
            ) from None

    def _read(self, stream, cursor):
        if cursor.offset > self.observed_bytes:
            raise SourceError(
                "source_changed", "Source is shorter than its saved cursor"
            )
        header = stream.readline(self.max_record_bytes + 1)
        if hashlib.sha256(header).hexdigest() != self.identity.header_digest:
            raise SourceError("source_changed", "Source header changed during capture")
        stream.seek(0)
        hasher = hashlib.sha256()
        remaining = cursor.offset
        while remaining:
            chunk = stream.read(min(remaining, 1024 * 1024))
            if not chunk:
                raise SourceError(
                    "source_changed", "Source was truncated during capture"
                )
            hasher.update(chunk)
            remaining -= len(chunk)
        if hasher.hexdigest() != cursor.digest:
            raise SourceError(
                "source_changed", "Previously archived source bytes have changed"
            )
        if cursor.offset:
            stream.seek(cursor.offset - 1)
            if stream.read(1) != b"\n":
                raise SourceError(
                    "invalid_cursor", "Cursor is not at a record boundary"
                )
        stream.seek(cursor.offset)
        previous, offset = cursor, cursor.offset
        records, byte_count = [], 0
        while offset < self.observed_bytes:
            raw = stream.readline(
                min(self.observed_bytes - offset, self.max_record_bytes + 1)
            )
            if not raw:
                raise SourceError(
                    "source_changed", "Source was truncated during capture"
                )
            if len(raw) > self.max_record_bytes:
                raise SourceError(
                    "payload_limit",
                    "Source record exceeds the size limit; no bytes discarded",
                )
            fragment = not raw.endswith(b"\n")
            records.append(record_at(offset, raw, fragment=fragment))
            hasher.update(raw)
            if fragment:
                yield Batch(
                    previous,
                    Cursor(offset, cursor.digest),
                    tuple(records),
                    self.observed_bytes,
                    True,
                    hasher.hexdigest(),
                )
                return
            offset += len(raw)
            cursor = Cursor(offset, hasher.hexdigest())
            byte_count += len(raw)
            if len(records) >= self.batch_size or byte_count >= 8 * 1024 * 1024:
                final = offset == self.observed_bytes
                yield Batch(
                    previous,
                    cursor,
                    tuple(records),
                    self.observed_bytes,
                    final,
                    hasher.hexdigest() if final else None,
                )
                if final:
                    return
                previous, records, byte_count = cursor, [], 0
        if records or previous.offset == self.observed_bytes:
            yield Batch(
                previous,
                cursor,
                tuple(records),
                self.observed_bytes,
                True,
                hasher.hexdigest(),
            )


def inspect_source(source):
    """Inspect only head/tail windows; trust other native records without scanning them."""
    window = 32768
    counts, invalid, fragments, sampled = {}, 0, 0, 0
    seen = set()
    try:
        with source.path.open("rb") as stream:
            for start in (0, max(0, source.observed_bytes - window)):
                stream.seek(start)
                raw = stream.read(min(window, source.observed_bytes - start))
                if start:
                    stream.seek(start - 1)
                    if stream.read(1) != b"\n":
                        boundary = raw.find(b"\n")
                        if boundary < 0:
                            continue
                        start += boundary + 1
                        raw = raw[boundary + 1 :]
                offset = start
                for line in raw.splitlines(keepends=True):
                    end = offset + len(line)
                    is_tail = end == source.observed_bytes
                    if (line.endswith(b"\n") or is_tail) and offset not in seen:
                        seen.add(offset)
                        record = record_at(
                            offset, line, fragment=is_tail and not line.endswith(b"\n")
                        )
                        counts[record.kind] = counts.get(record.kind, 0) + 1
                        invalid += record.parse_status == "invalid_json"
                        sampled += 1
                    offset = end
            if source.observed_bytes:
                stream.seek(source.observed_bytes - 1)
                fragments = int(stream.read(1) != b"\n")
    except OSError:
        raise SourceError(
            "source_unavailable", "Cannot inspect source boundaries"
        ) from None
    return {
        "source": source.identity.source,
        "source_format": source.identity.format,
        "session_id": source.identity.session_id,
        "runtime_version": source.identity.runtime_version,
        "tested_runtime": source.identity.runtime_version == TESTED_RUNTIME,
        "observed_bytes": source.observed_bytes,
        "inspection": "head-tail-sample",
        "window_bytes": window,
        "sampled_records": sampled,
        "sampled_record_types": counts,
        "sampled_invalid_records": invalid,
        "tail_fragments": fragments,
        "completeness": "incomplete" if invalid or fragments else "unknown",
        "completeness_reason": "Malformed or truncated record observed"
        if invalid or fragments
        else "Native records are trusted; protocol does not prove full stream completeness",
        "raw_content_displayed": False,
    }
