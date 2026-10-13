"""Read-only, bounded views of existing evidence; no model context or tool execution."""

from __future__ import annotations

import base64
import json
from collections import Counter
from dataclasses import dataclass

from ..mining.candidates import (
    REVIEW_REQUIREMENTS,
    candidate_source,
    checked_cases,
    observations,
    pattern_identity,
    witness,
)
from .catalog import page_limit
from .projection import ACTIVITIES
from .results import canonical, digest, result_id
from .store import ArchiveError

LEVELS = ("summary", "pattern", "candidate", "evidence", "raw")
KINDS = ("adjacent_activity_repeat", "repeated_variant", "observed_successor_branch")
OUTCOMES = ("passed", "failed", "unknown")


def encode(value):
    """Measure the exact compact UTF-8 JSON returned by the CLI, including newline."""
    return (
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def page_bounds(query):
    """Share pagination bounds across explicit evidence sources, not their semantics."""
    page_limit(query.limit)
    if type(query.max_bytes) is not int or not 1024 <= query.max_bytes <= 16777216:
        raise ArchiveError(
            "invalid_query", "Output budget must be 1024–16777216 UTF-8 bytes"
        )
    if query.after is not None and (
        not isinstance(query.after, str) or len(query.after) > 2048
    ):
        raise ArchiveError("invalid_query", "Invalid continuation token")


@dataclass(frozen=True)
class Query:
    level: str
    limit: int = 20
    max_bytes: int = 16384
    after: str | None = None
    candidate: str | None = None
    kind: str | None = None
    activity: str | None = None
    outcome: str | None = None
    min_cases: int = 1
    allow_raw: bool = False

    def validate(self):
        """Reject invalid bounds and unused selectors before opening data sources."""
        page_bounds(self)
        if self.level not in LEVELS:
            raise ArchiveError(
                "invalid_query", "Output budget must be 1024–16777216 UTF-8 bytes"
            )
        if (
            self.kind not in (None, *KINDS)
            or self.activity not in (None, *ACTIVITIES.values())
            or self.outcome not in (None, *OUTCOMES)
        ):
            raise ArchiveError("invalid_query", "Unsupported pattern or outcome filter")
        if type(self.min_cases) is not int or self.min_cases < 1:
            raise ArchiveError("invalid_query", "min_cases must be a positive integer")
        if self.candidate is not None:
            result_id(self.candidate)
        if (self.level in {"candidate", "evidence"}) != (self.candidate is not None):
            raise ArchiveError(
                "invalid_query",
                "candidate/evidence require one candidate ID; other levels do not accept it",
            )
        if type(self.allow_raw) is not bool or self.allow_raw and self.level != "raw":
            raise ArchiveError(
                "invalid_query", "Raw acknowledgement is only valid for raw queries"
            )
        if self.level == "raw" and not self.allow_raw:
            raise ArchiveError(
                "authorization_required",
                "Raw query requires an explicit snapshot and --allow-raw",
            )
        if self.level in {"candidate", "evidence", "raw"} and (
            self.kind
            or self.activity
            or self.min_cases != 1
            or self.level != "evidence"
            and self.outcome
        ):
            raise ArchiveError(
                "invalid_query",
                "These filters do not apply to the selected query level",
            )
        return self

    def identity(self, scope):
        """Bind continuation to immutable input, level and filters, but not page size."""
        return digest(
            canonical(
                {
                    "version": 1,
                    "scope": scope,
                    "level": self.level,
                    "candidate": self.candidate,
                    "kind": self.kind,
                    "activity": self.activity,
                    "outcome": self.outcome,
                    "min_cases": self.min_cases,
                }
            )
        )


def token(identity, key):
    return base64.urlsafe_b64encode(canonical({"query": identity, "key": key})).decode(
        "ascii"
    )


def cursor(query, identity):
    """A token is a scope-bound bookmark, never an authorization credential."""
    if query.after is None:
        return None
    try:
        value = json.loads(base64.b64decode(query.after, altchars=b"-_", validate=True))
        if (
            not isinstance(value, dict)
            or set(value) != {"query", "key"}
            or value["query"] != identity
        ):
            raise ValueError
        if not isinstance(value["key"], (str, int)) or isinstance(value["key"], bool):
            raise TypeError
        return value["key"]
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ArchiveError(
            "invalid_query",
            "Continuation token does not match this input, level or filters",
        ) from None


def page(
    query,
    scope,
    entries,
    *,
    key,
    metadata=None,
    more=False,
    remaining=None,
    extra_bytes=lambda _: 0,
):
    """Admit whole items and a usable bookmark; never cut JSON or a raw record."""
    identity = query.identity(scope)
    value = {
        "schema_version": 1,
        "operation": "trace.query",
        "level": query.level,
        "status": "returned",
        "exit_code": 0,
        "scope": scope,
        "items": [],
        "page": {
            "limit": query.limit,
            "next_after": None,
            "remaining_items": remaining,
        },
        "budget": {
            "max_bytes": query.max_bytes,
            "unit": "utf8_bytes_including_newline",
            "limited": False,
        },
        **(metadata or {}),
    }
    if len(encode(value)) > query.max_bytes:
        return budget_error(query)
    selected = entries[: query.limit]
    for i, item in enumerate(selected):
        value["items"].append(item)
        has_more = more or i + 1 < len(entries)
        value["page"]["next_after"] = token(identity, key(item)) if has_more else None
        value["page"]["remaining_items"] = (
            remaining - len(value["items"]) if remaining is not None else None
        )
        required = len(encode(value)) + sum(
            extra_bytes(entry) for entry in value["items"]
        )
        if required > query.max_bytes:
            value["items"].pop()
            if not value["items"]:
                return budget_error(query, required_bytes=required, reference=key(item))
            value["page"]["next_after"] = token(identity, key(value["items"][-1]))
            value["page"]["remaining_items"] = (
                remaining - len(value["items"]) if remaining is not None else None
            )
            value["budget"]["limited"] = True
            break
    # Adding the limited flag cannot enlarge JSON (true is shorter than false).
    return value


def budget_error(query, *, required_bytes=None, reference=None):
    """An unreturnable first item fails explicitly instead of looping over empty pages."""
    return {
        "schema_version": 1,
        "operation": "trace.query",
        "level": query.level,
        "status": "budget_exceeded",
        "exit_code": 3,
        "items": [],
        "max_bytes": query.max_bytes,
        "reason": "Increase --max-bytes; an entire item and its envelope must fit",
        "required_bytes_upper_bound": required_bytes,
        "blocked_reference": reference,
    }


def after_items(query, scope, items, key):
    bookmark = cursor(query, query.identity(scope))
    if bookmark is None:
        return items
    for index, item in enumerate(items):
        if key(item) == bookmark:
            return items[index + 1 :]
    raise ArchiveError("invalid_query", "Continuation item is outside this query")


def analysis_query(evidence, query):
    """Corroborate existing discovery and expose one decision-sized view without mining."""
    query.validate()
    if query.level == "raw":
        raise ArchiveError("invalid_query", "Raw queries use an archived snapshot")
    source = candidate_source(evidence)
    if evidence.projection is None or not evidence.association_verified:
        raise ArchiveError(
            "unavailable", "Query requires the original verified projection association"
        )
    selected, excluded = checked_cases(evidence)
    scope = {
        name: source[name]
        for name in (
            "analysis_id",
            "analysis_document_digest",
            "projection_id",
            "projection_document_digest",
            "snapshot_id",
        )
    }
    records = []
    for kind, activities, members, targets in observations(selected):
        identifier, pattern = pattern_identity(source, kind, activities, targets)
        case_count = len({case["turn_id"] for case, _ in members})
        if (
            query.kind
            and kind != query.kind
            or query.activity
            and query.activity not in activities
            or case_count < query.min_cases
        ):
            continue
        outcomes = Counter(
            item["outcome"] for _, operations in members for item in operations
        )
        if (
            query.level in {"summary", "pattern"}
            and query.outcome
            and not outcomes[query.outcome]
        ):
            continue
        records.append(
            (
                {
                    "candidate_id": identifier,
                    "kind": kind,
                    "pattern": pattern,
                    "occurrences": len(members),
                    "support": {
                        "cases": case_count,
                        "selected_cases": len(selected),
                        "ratio": case_count / len(selected),
                    },
                    "occurrence_member_outcomes": dict(sorted(outcomes.items())),
                },
                members,
            )
        )
    metadata = {
        "analysis_status": evidence.analysis["status"],
        "completeness": evidence.analysis["completeness"],
        "allow_incomplete": evidence.analysis["allow_incomplete"],
        "case_scope": evidence.analysis["case_scope"],
        "origin": evidence.origin,
        "llm_used": False,
        "optimization_verified": False,
        "verification": "analysis_and_projection_correlated; no_execution_approval",
    }

    def key(item):
        return item["candidate_id"]

    if query.level in {"summary", "pattern"}:
        items = [record for record, _ in records]
        if query.level == "summary":
            items = [
                {
                    name: item[name]
                    for name in ("candidate_id", "kind", "occurrences", "support")
                }
                for item in items
            ]
            metadata["statistics"] = {
                "selected_cases": len(selected),
                "excluded_cases": len(excluded),
                "projected_operations": len(evidence.projection["operations"]),
                "selected_outcomes": dict(
                    sorted(
                        Counter(
                            item["outcome"]
                            for case in selected
                            for item in case["operations"]
                        ).items()
                    )
                ),
                "matching_candidates": len(items),
            }
        items = after_items(query, scope, items, key)
    else:
        record, members = next(
            (
                (record, members)
                for record, members in records
                if record["candidate_id"] == query.candidate
            ),
            (None, None),
        )
        if record is None:
            raise ArchiveError(
                "candidate_unavailable",
                "Candidate is not present in this verified analysis",
            )
        if query.level == "candidate":
            items = [
                {
                    **record,
                    "parameters": {"status": "unavailable_in_activity_projection"},
                    "dependencies": {
                        "status": "unavailable; observed_order_is_not_causality"
                    },
                    "tool_coverage": {"status": "unmapped", "handler": None},
                    "analysis_tools": source["dependencies"],
                    "validation": {
                        "review": "not_reviewed",
                        "quality_gate": "not_evaluated",
                        "optimization_verified": False,
                    },
                    "required_evidence": list(REVIEW_REQUIREMENTS),
                }
            ]
            items = after_items(query, scope, items, key)
        else:
            items = [
                {"witness_index": i, **witness(case, operations)}
                for i, (case, operations) in enumerate(members)
                if not query.outcome
                or any(item["outcome"] == query.outcome for item in operations)
            ]
            metadata["candidate_id"] = query.candidate
            metadata["verification"] = (
                "projected_member_references; raw_payload_not_read"
            )

            def key(item):
                return item["witness_index"]

            items = after_items(query, scope, items, key)
    return page(query, scope, items, key=key, metadata=metadata, remaining=len(items))


def summary_query(catalog, query, *, snapshot=None):
    """List safe registered analysis summaries; counts describe this page, not all history."""
    query.validate()
    if (
        query.level != "summary"
        or query.kind
        or query.activity
        or query.outcome
        or query.min_cases != 1
    ):
        raise ArchiveError(
            "invalid_query",
            "Unscoped summary accepts only --snapshot, pagination and budget",
        )
    if snapshot is not None:
        result_id(snapshot)
    scope = {"kind": "registered_analysis_metadata", "snapshot_id": snapshot}
    after = cursor(query, query.identity(scope))
    if after is not None:
        result_id(after)
    result = catalog.results(
        snapshot=snapshot, kind="analysis", limit=query.limit, after=after
    )
    return page(
        query,
        scope,
        [summary_item(item) for item in result["items"]],
        key=lambda item: item["result_id"],
        more=result["next_after"] is not None,
        metadata={
            "verification": "registered_metadata; documents_and_raw_payload_not_read"
        },
    )


def summary_item(item):
    """Only expose supported counts and references, even if stored JSON has extra content."""
    value = {
        name: result_id(item[name])
        for name in (
            "result_id",
            "snapshot_id",
            "parent_result_id",
            "input_digest",
            "document_digest",
        )
    }
    summary = item["summary"]
    fields = {
        "status": ("passed", "unavailable"),
        "engine": ("rust4pm", "pm4py"),
        "completeness": ("complete", "incomplete", "unknown"),
    }
    stats = {}
    for name, allowed in fields.items():
        if summary.get(name) not in allowed:
            raise ArchiveError(
                "archive_corrupt", "Stored analysis summary is unsupported"
            )
        stats[name] = summary[name]
    for name in ("selected_cases", "excluded_cases", "variants", "dfg_edges"):
        count = summary.get(name)
        if type(count) is not int or count < 0:
            raise ArchiveError(
                "archive_corrupt", "Stored analysis count is unavailable"
            )
        stats[name] = count
    if item["origin"] not in ("file", "computed"):
        raise ArchiveError("archive_corrupt", "Stored analysis origin is unsupported")
    return {**value, "origin": item["origin"], "statistics": stats}


def raw_query(catalog, query, snapshot):
    """Read explicit private members only after budget admission; never infer full integrity."""
    query.validate()
    result_id(snapshot)
    scope = {"snapshot_id": snapshot}
    after = cursor(query, query.identity(scope))
    if after is not None and (type(after) is not int or after < 0):
        raise ArchiveError("invalid_query", "Raw cursor must contain a member position")
    result = catalog.raw_members(snapshot, limit=query.limit, after=after)
    metadata = {
        "completeness": result["snapshot"]["completeness"],
        "verification": "selected_member_digests; full_snapshot_not_verified",
        "encoding": "base64_original_bytes",
        "private_content": True,
    }
    # Base64 length is exact without reading or allocating the unadmitted payloads.
    entries = [{**item, "payload_base64": ""} for item in result["items"]]
    value = page(
        query,
        scope,
        entries,
        key=lambda item: item["position"],
        metadata=metadata,
        more=result["has_more"],
        remaining=result["snapshot"]["records"]
        - (after + 1 if after is not None else 0),
        extra_bytes=lambda item: 4 * ((item["bytes"] + 2) // 3),
    )
    if value["status"] == "returned":
        for item in value["items"]:
            item["payload_base64"] = catalog.raw_payload(snapshot, item)
    return value
