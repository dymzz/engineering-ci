"""Read-only summaries of explicitly selected snapshots, never arbitrary historical totals."""

from collections import Counter, defaultdict
from dataclasses import dataclass

from ..workflow_core.documents import digest
from ..workflow_core.schemas import HASH, validate
from .feedback_query import checked_document
from .retrieval import Query, budget_error, encode, page_bounds
from .store import ArchiveError

SUMMARY_VERSION = "workflow-feedback-summary/1"
MAX_SNAPSHOTS = 32


@dataclass(frozen=True)
class FeedbackSummary:
    feedback_ids: tuple[str, ...]
    max_bytes: int = 16384

    @property
    def identities(self):
        return sorted(set(self.feedback_ids))

    def validate(self):
        """Bound the explicit input set before connecting; repeated IDs are read once."""
        page_bounds(Query("evidence", max_bytes=self.max_bytes))
        if (
            not isinstance(self.feedback_ids, (tuple, list))
            or not 1 <= len(self.feedback_ids) <= MAX_SNAPSHOTS
        ):
            raise ArchiveError(
                "invalid_query", "Summary requires 1–32 explicit feedback IDs"
            )
        for identity in self.feedback_ids:
            validate(identity, HASH, label="summary feedback ID")
        return self


def execution_summary(values):
    """Union stable attempt identities and retain failures, ambiguous snapshots and legacy gaps."""
    values = sorted(values, key=lambda item: item["feedback_id"])
    indexed, legacy, attempts, conflict_keys = [], [], {}, set()
    bindings = {
        digest(
            {
                key: value[key]
                for key in (
                    "capability",
                    "generation_id",
                    "definition_id",
                    "catalog_revision",
                )
            }
        )
        for value in values
    }
    steps = set()
    for value in values:
        snapshot = value.get("attempt_snapshot")
        if snapshot is None:
            legacy.append(value["feedback_id"])
            continue
        indexed.append(value)
        steps.add(tuple(sorted(snapshot["required_step_ids"])))
        for row in snapshot["attempts"]:
            key = (row["step_id"], row["attempt"])
            if key in attempts and attempts[key] != row:
                conflict_keys.add(key)
            else:
                attempts[key] = row
    conflict = len(bindings) != 1 or len(steps) > 1 or bool(conflict_keys)
    manifests = {
        value["feedback_id"]: {
            (row["step_id"], row["attempt"])
            for row in value["attempt_snapshot"]["attempts"]
        }
        for value in indexed
    }
    snapshots = []
    for value in values:
        identity = value["feedback_id"]
        cover = next(
            (
                other
                for other in sorted(manifests)
                if identity in manifests
                and (
                    manifests[identity] < manifests[other]
                    or manifests[identity] == manifests[other]
                    and other < identity
                )
            ),
            None,
        )
        snapshots.append(
            {
                "feedback_id": identity,
                **{
                    key: value[key]
                    for key in (
                        "capability",
                        "generation_id",
                        "definition_id",
                        "catalog_revision",
                    )
                },
                "recorded_result": value["recorded_result"],
                "observation_completeness": value["observation_completeness"],
                "coverage": "legacy_unverifiable"
                if identity in legacy
                else "conflicting"
                if conflict
                else "indexed",
                "covered_by": cover
                if identity not in legacy and not conflict
                else None,
                "execution_evidence": value["evidence"],
            }
        )
    metrics = None
    if indexed and not conflict:
        rows = list(attempts.values())
        metrics = {
            "scope": "verified_selected_attempt_indices",
            "unique_observed_attempts": len(rows),
            "known_check_submissions": sum(
                row["check_submitted"] is True for row in rows
            ),
            "unknown_submission_measurements": sum(
                row["check_submitted"] is None for row in rows
            ),
            "known_reused_checks": sum(row["evidence_reused"] is True for row in rows),
            "unknown_reuse_measurements": sum(
                row["evidence_reused"] is None for row in rows
            ),
            "known_adapter_batches": len(
                {
                    (row["session_id"], row["batch_id"])
                    for row in rows
                    if row["batch_id"] is not None
                }
            ),
            "unknown_batch_measurements": sum(row["batch_id"] is None for row in rows),
            "attempt_outcomes": dict(
                sorted(
                    Counter(
                        row["result_status"] or "not_observed" for row in rows
                    ).items()
                )
            ),
            "duplicate_attempt_references_removed": sum(
                len(items) for items in manifests.values()
            )
            - len(rows),
        }
    return {
        "execution_id": values[0]["evidence"]["execution_id"],
        "capability": values[0]["capability"] if len(bindings) == 1 else None,
        "snapshot_coverage": "conflicting"
        if conflict
        else "legacy_unverifiable"
        if not indexed
        else "partial"
        if legacy
        or any(value["observation_completeness"] != "complete" for value in indexed)
        else "verified_selected_indices",
        "snapshots": snapshots,
        "legacy_feedback_ids": legacy,
        "conflicting_attempts": [
            {"step_id": step, "attempt": number}
            for step, number in sorted(conflict_keys)
        ],
        "binding_conflict": len(bindings) != 1 or len(steps) > 1,
        "metrics": metrics,
    }


def summarize(values, scope):
    """Summarize only this fixed selection; no latest-state inference or global success rate."""
    executions = defaultdict(list)
    for value in values:
        executions[value["evidence"]["execution_id"]].append(value)
    result = {
        "schema_version": 1,
        "operation": "workflow.feedback-summary",
        "summary_version": SUMMARY_VERSION,
        "summary_id": digest(
            {
                "scope": scope,
                "feedback_ids": sorted(value["feedback_id"] for value in values),
            }
        ),
        "status": "summarized",
        "exit_code": 0,
        "scope": scope,
        "collection_coverage": "unknown",
        "current_input_validity": "not_checked",
        "approval_state_now": "not_checked",
        "execution_evidence_validation": "not_checked",
        "optimization_evaluated": False,
        "llm_used": False,
        "selected_snapshots": len(values),
        "selected_executions": len(executions),
        "executions": [
            execution_summary(executions[key]) for key in sorted(executions)
        ],
    }
    result["has_coverage_gaps"] = any(
        item["snapshot_coverage"] != "verified_selected_indices"
        for item in result["executions"]
    )
    return result


def query_summary(store, request):
    """Read one explicit selection in a single repeatable read; reject missing or corrupt members."""
    values, scope = read_selection(store, request)
    return summary_result(values, scope, request.max_bytes)


def read_selection(store, request):
    """Share verified input retrieval with review; never use saved totals as source facts."""
    request.validate()
    with store.transaction(readonly=True) as cursor:
        store._owned(cursor, required_version=3)
        cursor.execute(
            "SELECT current_database(), inet_server_addr()::text, inet_server_port(), current_user"
        )
        scope = {
            "database_sha256": digest(list(cursor.fetchone())),
            "schema": store.config.schema,
            "feedback_ids": request.identities,
            "record_scope": "explicit_feedback_snapshot_selection",
            "summary_version": SUMMARY_VERSION,
        }
        cursor.execute(
            store.query(
                "SELECT feedback_id, approval_id, document_digest, document, archived_at FROM {schema}.workflow_feedback WHERE feedback_id = ANY(%s::text[]) ORDER BY feedback_id"
            ),
            (request.identities,),
        )
        rows = cursor.fetchall()
        if {row[0] for row in rows} != set(request.identities):
            raise ArchiveError(
                "feedback_unavailable",
                "An explicitly selected feedback snapshot is missing; no partial total was produced",
            )
        values = [checked_document(row) for row in rows]
    return values, scope


def summary_result(values, scope, max_bytes):
    """Reconstruct the complete original summary, including its requested output budget."""
    result = summarize(values, scope)
    result["budget"] = {
        "max_bytes": max_bytes,
        "unit": "utf8_bytes_including_newline",
    }
    size = len(encode(result))
    if size > max_bytes:
        failure = budget_error(
            Query("evidence", max_bytes=max_bytes),
            required_bytes=size,
            reference=result["summary_id"],
        )
        failure["operation"] = "workflow.feedback-summary"
        return failure
    return result
