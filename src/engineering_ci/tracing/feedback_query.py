"""Bounded, verified reads of archived capability feedback; no execution or migration."""

import hashlib
import json
from dataclasses import dataclass

from ..workflow_core.capabilities import FEEDBACK, REFERENCE, VERSION
from ..workflow_core.documents import WorkflowError, canonical, digest
from ..workflow_core.schemas import HASH, ID, validate
from .catalog import timestamp
from .retrieval import Query, cursor, page, page_bounds, token
from .store import ArchiveError

QUERY_VERSION = "workflow-feedback-query/1"
SCAN_LIMIT = 100
SELECTORS = {
    "feedback_id": HASH,
    "approval_id": HASH,
    "execution_id": ID,
    "name": REFERENCE["properties"]["name"],
    "version": VERSION,
    "result_status": FEEDBACK["properties"]["recorded_result"]["properties"]["status"],
    "completeness": FEEDBACK["properties"]["observation_completeness"],
}


@dataclass(frozen=True)
class FeedbackQuery:
    feedback_id: str | None = None
    approval_id: str | None = None
    execution_id: str | None = None
    name: str | None = None
    version: str | None = None
    result_status: str | None = None
    completeness: str | None = None
    limit: int = 20
    max_bytes: int = 16384
    after: str | None = None

    @property
    def filters(self):
        return {key: getattr(self, key) for key in SELECTORS}

    @property
    def paging(self):
        return Query(
            "evidence", limit=self.limit, max_bytes=self.max_bytes, after=self.after
        )

    def validate(self):
        """Reject invalid requests before connection setup, using the original field schemas."""
        page_bounds(self.paging)
        for key, value in self.filters.items():
            if value is not None:
                validate(value, SELECTORS[key], label="feedback query " + key)
        if self.feedback_id is not None and self.after is not None:
            raise WorkflowError("Single-feedback lookup does not accept --after")
        if self.after is not None:
            # Validate cursor syntax before opening PostgreSQL; scope is checked after connecting.
            try:
                import base64

                value = json.loads(
                    base64.b64decode(self.after, altchars=b"-_", validate=True)
                )
                validate(
                    value,
                    {
                        "type": "object",
                        "properties": {"query": HASH, "key": HASH},
                        "required": ["query", "key"],
                        "additionalProperties": False,
                    },
                    label="feedback continuation",
                )
            except (ValueError, TypeError, UnicodeError, RecursionError):
                raise ArchiveError(
                    "invalid_query", "Invalid feedback continuation token"
                ) from None
        return self


def checked_row(row):
    """Verify column identities, actual bytes and original feedback schema before exposing facts."""
    identity, approval, checksum, payload, archived_at = row
    try:
        raw = bytes(payload)
        if len(raw) > 2 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != checksum:
            raise ValueError
        value = json.loads(raw)
        validate(value, FEEDBACK, label="archived capability feedback")
        if (
            canonical(value) != raw
            or value["feedback_id"] != identity
            or value["capability"]["approval_id"] != approval
            or digest(
                {key: item for key, item in value.items() if key != "feedback_id"}
            )
            != identity
        ):
            raise ValueError
        item = {
            "feedback_id": identity,
            "capability": value["capability"],
            "execution_id": value["evidence"]["execution_id"],
            "definition_id": value["definition_id"],
            "generation_id": value["generation_id"],
            "catalog_revision_at_call": value["catalog_revision"],
            "archived_at": timestamp(archived_at),
            "recorded_result": value["recorded_result"],
            "observation_completeness": value["observation_completeness"],
            "metrics": value["metrics"],
            "references": {
                "document_sha256": checksum,
                "execution_evidence": value["evidence"],
            },
            "current_input_validity": "not_checked",
            "approval_state_now": "not_checked",
            "execution_evidence_validation": "not_checked",
            "task_outcome_scope": value["task_outcome_scope"],
            "optimization_evaluated": False,
            "llm_used": False,
        }
        return item
    except (
        WorkflowError,
        ValueError,
        TypeError,
        KeyError,
        UnicodeError,
        RecursionError,
    ):
        raise ArchiveError(
            "feedback_corrupt", "Archived feedback content or identity is invalid"
        ) from None


def matches(item, request):
    """Apply document filters only after integrity validation, without parsing arbitrary SQL JSON."""
    actual = {
        "execution_id": item["execution_id"],
        "name": item["capability"]["name"],
        "version": item["capability"]["version"],
        "result_status": item["recorded_result"]["status"],
        "completeness": item["observation_completeness"],
    }
    return all(getattr(request, key) in (None, value) for key, value in actual.items())


def query_feedback(store, request):
    """Read at most one source page; empty sparse matches may still have a continuation."""
    request.validate()
    with store.transaction(readonly=True) as sql_cursor:
        store._owned(sql_cursor, required_version=3)
        sql_cursor.execute(
            "SELECT current_database(), inet_server_addr()::text, inet_server_port(), current_user"
        )
        scope = {
            "query_version": QUERY_VERSION,
            "database_sha256": digest(list(sql_cursor.fetchone())),
            "schema": store.config.schema,
            "filters": request.filters,
            "record_kind": "workflow-capability-feedback",
        }
        bookmark = cursor(request.paging, request.paging.identity(scope))
        if bookmark is not None:
            validate(bookmark, HASH, label="feedback bookmark")
            sql_cursor.execute(
                store.query(
                    "SELECT feedback_id FROM {schema}.workflow_feedback WHERE feedback_id = %s AND (%s::text IS NULL OR approval_id = %s)"
                ),
                (bookmark, request.approval_id, request.approval_id),
            )
            if sql_cursor.fetchone() is None:
                raise ArchiveError(
                    "invalid_query",
                    "Feedback continuation is outside this source or approval filter",
                )
        sql_cursor.execute(
            store.query(
                "SELECT feedback_id, approval_id, document_digest, document, archived_at FROM {schema}.workflow_feedback WHERE (%s::text IS NULL OR feedback_id = %s) AND (%s::text IS NULL OR approval_id = %s) AND (%s::text IS NULL OR feedback_id > %s) ORDER BY feedback_id LIMIT %s"
            ),
            (
                request.feedback_id,
                request.feedback_id,
                request.approval_id,
                request.approval_id,
                bookmark,
                bookmark,
                SCAN_LIMIT + 1,
            ),
        )
        rows = sql_cursor.fetchall()
        entries, scanned, last = [], 0, None
        for row in rows[:SCAN_LIMIT]:
            item = checked_row(row)
            scanned += 1
            last = item["feedback_id"]
            if matches(item, request):
                entries.append(item)
                if len(entries) == request.limit:
                    break
        more = scanned < len(rows)
    if request.feedback_id and not entries:
        raise ArchiveError(
            "feedback_unavailable",
            "No matching archived feedback snapshot is available",
        )
    next_after = token(request.paging.identity(scope), last) if more else None
    result = page(
        request.paging,
        scope,
        entries,
        key=lambda item: item["feedback_id"],
        more=more,
        metadata={
            "operation": "workflow.feedback-query",
            "record_scope": "archived_feedback_snapshots",
            "collection_coverage": "unknown",
            "current_input_validity": "not_checked",
            "pagination": "live keyset by feedback ID; restart to include concurrent earlier inserts",
            "execution_order": "not_inferred_from_archive_order",
            "source_page": {
                "scanned_candidates": scanned,
                "scan_limit": SCAN_LIMIT,
                "matching_snapshots": len(entries),
            },
            "page": {
                "limit": request.limit,
                "next_after": next_after,
                "remaining_items": None,
            },
        },
    )
    if result["exit_code"] == 0 and not result["budget"]["limited"]:
        # Both bookmarks contain fixed-size IDs; replacement cannot enlarge the encoded page.
        result["page"]["next_after"] = next_after
    elif result["exit_code"] != 0:
        result["operation"] = "workflow.feedback-query"
    return result
