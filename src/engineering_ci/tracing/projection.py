"""Versioned, content-free views of authoritative rollout execution records."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from importlib.metadata import version

from .sources.codex_rollout import TESTED_RUNTIME, decode_record
from .store import ArchiveError

PROJECTION_VERSION = "codex-rollout-items/1"
ACTIVITIES = {
    "CommandExecution": "command_execution",
    "FileChange": "file_change",
    "McpToolCall": "mcp_tool_call",
}


def identifier(value):
    """Accept bounded opaque protocol IDs; never export arbitrary payload strings."""
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value)
        else None
    )


@dataclass(frozen=True)
class Operation:
    item_id: str
    turn_id: str
    activity: str
    start_ns: int | None
    end_ns: int | None
    outcome: str
    exit_code: int | None
    event_id: str


def operations(records):
    """Select native completed items, not text outputs or duplicate wrapper calls."""
    selected, boundaries, seen, diagnostics = (
        [],
        {},
        {},
        {
            "invalid_records": 0,
            "invalid_operations": 0,
            "duplicate_items": 0,
            "unmapped_records": 0,
        },
    )
    runtime, source_session = None, None
    for record in records:
        value = None if record.fragment else decode_record(record.raw)
        if value is None:
            diagnostics["invalid_records"] += 1
            continue
        payload = value.get("payload")
        if not isinstance(payload, dict):
            diagnostics["unmapped_records"] += 1
            continue
        if value.get("type") == "session_meta":
            runtime = payload.get("cli_version")
            source_session = identifier(payload.get("id"))
            continue
        kind = payload.get("type")
        if not isinstance(kind, str):
            diagnostics["unmapped_records"] += 1
            continue
        if value.get("type") != "event_msg":
            diagnostics["unmapped_records"] += 1
            continue
        turn = identifier(payload.get("turn_id"))
        if kind in {"task_started", "task_complete", "turn_aborted"} and turn:
            boundary = boundaries.setdefault(turn, {"started": False, "terminal": None})
            if kind == "task_started":
                boundary["started"] = True
            else:
                boundary["terminal"] = (
                    "completed" if kind == "task_complete" else "cancelled"
                )
            continue
        item = payload.get("item")
        if (
            kind != "item_completed"
            or not isinstance(item, dict)
            or not isinstance(item.get("type"), str)
            or item.get("type") not in ACTIVITIES
        ):
            diagnostics["unmapped_records"] += 1
            continue
        native_id = identifier(item.get("id"))
        if not turn or not native_id:
            diagnostics["invalid_operations"] += 1
            continue
        key = (turn, native_id)
        if key in seen:
            if seen[key] != record.digest:
                raise ArchiveError(
                    "projection_conflict",
                    "Native completed item identity has conflicting records",
                )
            diagnostics["duplicate_items"] += 1
            continue
        seen[key] = record.digest
        start, end = payload.get("started_at_ms"), payload.get("completed_at_ms")
        valid_time = (
            type(start) is int
            and type(end) is int
            and 0 <= start <= end < 2**63 // 1_000_000
        )
        code = item.get("exit_code")
        code = code if type(code) is int and -(2**31) <= code < 2**31 else None
        status = item.get("status")
        outcome = (
            "failed"
            if status == "failed" or code is not None and code != 0
            else "passed"
            if status == "completed" or code == 0
            else "unknown"
        )
        selected.append(
            Operation(
                native_id,
                turn,
                ACTIVITIES[item["type"]],
                start * 1_000_000 if valid_time else None,
                end * 1_000_000 if valid_time else None,
                outcome,
                code,
                record.event_id,
            )
        )
    if runtime != TESTED_RUNTIME:
        raise ArchiveError(
            "unsupported_projection",
            "Raw archive is supported, but this runtime dialect has not been verified for projection",
        )
    return {
        "operations": selected,
        "turns": boundaries,
        "diagnostics": diagnostics,
        "runtime_version": runtime,
        "session_id": source_session,
    }


def standardize(records, snapshot_id):
    """Use actual SDK Span objects without exporters, clock fabrication or raw content."""
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.util.instrumentation import InstrumentationScope
    from opentelemetry.semconv._incubating.attributes import gen_ai_attributes as genai
    from opentelemetry.trace import (
        SpanContext,
        SpanKind,
        Status,
        StatusCode,
        TraceFlags,
    )

    result = operations(records)
    dependencies = {
        name: version(name)
        for name in ("opentelemetry-sdk", "opentelemetry-semantic-conventions")
    }
    identity = {
        "snapshot_id": snapshot_id,
        "projection_version": PROJECTION_VERSION,
        "mapping": ACTIVITIES,
        "dependencies": dependencies,
    }
    projection_id = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    spans, no_time = [], 0
    resource = Resource(
        {"service.name": "codex", "service.version": result["runtime_version"]}
    )
    for operation in result["operations"]:
        if operation.start_ns is None:
            no_time += 1
            continue
        trace_id = (
            int.from_bytes(
                hashlib.sha256(
                    f"{result['session_id']}:{operation.turn_id}".encode()
                ).digest()[:16],
                "big",
            )
            or 1
        )
        span_id = (
            int.from_bytes(
                hashlib.sha256(
                    f"{operation.turn_id}:{operation.item_id}".encode()
                ).digest()[:8],
                "big",
            )
            or 1
        )
        attrs = {
            genai.GEN_AI_OPERATION_NAME: "execute_tool",
            genai.GEN_AI_TOOL_NAME: operation.activity,
            genai.GEN_AI_CONVERSATION_ID: result["session_id"],
            "engineering_ci.source.item_id": operation.item_id,
            "engineering_ci.source.turn_id": operation.turn_id,
            "engineering_ci.archive.snapshot_id": snapshot_id,
            "engineering_ci.archive.event_id": operation.event_id,
            "engineering_ci.projection.version": PROJECTION_VERSION,
            "engineering_ci.operation.outcome": operation.outcome,
        }
        if operation.exit_code is not None:
            attrs["process.exit.code"] = operation.exit_code
        if operation.outcome == "failed":
            attrs["error.type"] = (
                "process_exit_nonzero"
                if operation.exit_code not in {None, 0}
                else "tool_failed"
            )
        span = ReadableSpan(
            name=f"execute_tool {operation.activity}",
            context=SpanContext(
                trace_id, span_id, False, TraceFlags(TraceFlags.SAMPLED)
            ),
            kind=SpanKind.INTERNAL,
            resource=resource,
            start_time=operation.start_ns,
            end_time=operation.end_ns,
            attributes=attrs,
            status=Status(
                StatusCode.ERROR if operation.outcome == "failed" else StatusCode.UNSET
            ),
            instrumentation_scope=InstrumentationScope(
                "engineering_ci.trace", PROJECTION_VERSION
            ),
        )
        spans.append(json.loads(span.to_json()))
    return {
        "schema_version": 1,
        "projection_id": projection_id,
        **identity,
        "format": "opentelemetry-sdk-span-json",
        "semantics": "generated gen_ai attributes from locked semantic-conventions package",
        "completeness": "incomplete"
        if result["diagnostics"]["invalid_records"]
        else "unknown",
        "spans": spans,
        "operations": [asdict(item) for item in result["operations"]],
        "turns": result["turns"],
        "diagnostics": {
            **result["diagnostics"],
            "operations_without_valid_time": no_time,
        },
        "time_semantics": "native item_completed started_at_ms/completed_at_ms",
        "id_semantics": "derived OTel IDs; native item_id is not assumed to be a tool call ID",
        "cost": None,
        "llm_used": False,
    }
