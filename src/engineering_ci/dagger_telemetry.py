"""Dagger native OpenTelemetry capture, parent-graph correlation, and A.1 verification."""

from __future__ import annotations

import argparse
import base64
import copy
import datetime
import gzip
import http.server
import json
import os
import re
import signal
import sys
import tempfile
import threading
import time
import zlib
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONTRACT_VERSION = 1
PINNED_ENGINE_VERSION = "v1.0.0-beta.16"
NATIVE_TRACE_RECEIPT_KIND = "engineering-ci-dagger-native-trace-receipt"
A1_VERIFICATION_KIND = "engineering-ci-dagger-a1-verification"

DAGGER_SESSION_ID_ATTR = "dagger.io/session.id"
DAGGER_ENGINE_INSTANCE_ID_ATTR = "dagger.io/engine.instance.id"
SERVICE_NAME_ATTR = "service.name"

DAGGER_CACHE_CONTRACT_ATTR = "dagger.io/cache.contract"
DAGGER_CACHE_OUTCOME_ATTR = "dagger.io/cache.outcome"
DAGGER_CACHE_RESULT_ID_ATTR = "dagger.io/cache.result.id"
DAGGER_CACHE_HIT_ROUTE_ATTR = "dagger.io/cache.hit.route"
DAGGER_DAG_DIGEST_ATTR = "dagger.io/dag.digest"
DAGGER_CONTENT_DIGEST_ATTR = "dagger.io/content_preferred_digest"
DAGGER_CHECK_NAME_ATTR = "dagger.io/check.name"
DAGGER_CHECK_PASSED_ATTR = "dagger.io/check.passed"

CORE_SCOPE_NAME = "dagger.io/core"
DAGQL_SCOPE_NAME = "dagger.io/dagql"
CHECK_SELECTOR_PREFIX = "dag://?check="

TRACEPARENT_RE = re.compile(
    r"^(?P<version>[0-9a-f]{2})-(?P<trace_id>[0-9a-f]{32})-(?P<parent_span_id>[0-9a-f]{16})-(?P<flags>[0-9a-f]{2})$"
)

BETA16_CAPABILITIES: dict[str, Any] = {
    "engine_version": PINNED_ENGINE_VERSION,
    "workload_export": {
        "engine_config": {"telemetry": {"workloadExport": True}},
        "required_env": [
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
            "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        ],
        "notes": (
            "NewWorkloadExport requires both traces and metrics OTLP endpoints "
            "or Engine startup fails."
        ),
    },
    "traceparent_propagation": "supported",
    "flush_semantics": {
        "engine_shutdown_drain_seconds": 30,
        "sdk_processor": "BatchSpanProcessor(max_queue=512, interval=60s); shutdown flush required",
    },
    "observed_spans": {
        "check_selector_prefix": CHECK_SELECTOR_PREFIX,
        "module_dagql_scope": DAGQL_SCOPE_NAME,
        "core_scope": CORE_SCOPE_NAME,
    },
    "check_attributes": {
        DAGGER_CHECK_NAME_ATTR: "partial",
        DAGGER_CHECK_PASSED_ATTR: "partial",
        "correlation_strategy": "parent_graph_traversal",
    },
    "cache_attributes": [
        DAGGER_CACHE_CONTRACT_ATTR,
        DAGGER_CACHE_OUTCOME_ATTR,
        DAGGER_CACHE_RESULT_ID_ATTR,
        DAGGER_CACHE_HIT_ROUTE_ATTR,
    ],
    "digest_attributes": [DAGGER_DAG_DIGEST_ATTR, DAGGER_CONTENT_DIGEST_ATTR],
}


class DaggerTelemetryError(ValueError):
    """Raised on malformed OTLP payloads, traceparents, or verification errors."""


@dataclass(frozen=True)
class SpanRecord:
    """Normalized OTLP span retaining per-resource attributes independently."""

    trace_id: str
    span_id: str
    parent_span_id: str
    name: str
    scope_name: str
    scope_version: str
    kind: str
    status_code: str
    status_message: str
    start_time_unix_nano: int
    end_time_unix_nano: int
    resource_attributes: dict[str, Any]
    attributes: dict[str, Any]
    session_id: str | None
    engine_instance_id: str | None
    service_name: str | None


@dataclass
class CaptureDataset:
    """Parsed OTLP spans plus transport/receiver observation metadata."""

    spans: list[SpanRecord]
    trace_batch_count: int = 0
    metric_batch_count: int = 0
    isolated_capture: bool | None = None
    receiver_observed: bool | None = None
    exporter_configured: str | None = None


@dataclass
class CandidateChain:
    """Validated candidate span chain discovered via real parent linkage."""

    anchor: SpanRecord
    dagql_span: SpanRecord
    core_span: SpanRecord
    lineage_to_anchor: list[SpanRecord] = field(default_factory=list)
    subtree_spans: list[SpanRecord] = field(default_factory=list)


def get_beta16_capabilities() -> dict[str, Any]:
    """Return pinned capability metadata for Dagger v1.0.0-beta.16."""
    return copy.deepcopy(BETA16_CAPABILITIES)


def kebab_to_camel(name: str) -> str:
    """Convert kebab-case CLI target name to Dagger GraphQL camelCase name."""
    parts = [part for part in name.split("-") if part]
    if not parts:
        return ""
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def parse_traceparent(value: str) -> tuple[str, str]:
    """Parse W3C traceparent and return (trace_id, parent_span_id)."""
    normalized = value.strip().lower()
    match = TRACEPARENT_RE.fullmatch(normalized)
    if not match:
        raise DaggerTelemetryError("invalid_traceparent")
    trace_id = match.group("trace_id")
    parent_span_id = match.group("parent_span_id")
    if trace_id == "0" * 32 or parent_span_id == "0" * 16:
        raise DaggerTelemetryError("invalid_traceparent")
    return trace_id, parent_span_id


def _normalize_id(value: Any, byte_len: int, *, allow_empty: bool = False) -> str:
    if value is None or value == "":
        if allow_empty:
            return ""
        raise DaggerTelemetryError("missing_otel_id")
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        if len(raw) != byte_len:
            raise DaggerTelemetryError("invalid_otel_id_length")
        return raw.hex()
    if not isinstance(value, str):
        raise DaggerTelemetryError("invalid_otel_id_type")
    text = value.strip()
    if not text and allow_empty:
        return ""
    expected_hex_len = byte_len * 2
    if len(text) == expected_hex_len and re.fullmatch(r"[0-9a-fA-F]+", text):
        return text.lower()
    try:
        decoded = base64.b64decode(text, validate=True)
    except Exception as error:
        raise DaggerTelemetryError("invalid_otel_id") from error
    if len(decoded) != byte_len:
        raise DaggerTelemetryError("invalid_otel_id_length")
    return decoded.hex()


def _decode_any_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for key in ("stringValue", "string_value"):
        if key in value:
            return value[key]
    for key in ("boolValue", "bool_value"):
        if key in value:
            return bool(value[key])
    for key in ("intValue", "int_value"):
        if key in value:
            try:
                return int(value[key])
            except (TypeError, ValueError):
                return value[key]
    for key in ("doubleValue", "double_value"):
        if key in value:
            return float(value[key])
    for key in ("bytesValue", "bytes_value"):
        if key in value:
            return value[key]
    for key in ("arrayValue", "array_value"):
        if key in value and isinstance(value[key], dict):
            return [_decode_any_value(item) for item in value[key].get("values") or []]
    for key in ("kvlistValue", "kvlist_value"):
        if key in value and isinstance(value[key], dict):
            return _decode_attributes(value[key].get("values") or [])
    return None


def _decode_attributes(items: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if not isinstance(items, list):
        return result
    for entry in items:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        if isinstance(key, str):
            result[key] = _decode_any_value(entry.get("value"))
    return result


def _normalize_status_code(status_obj: Any) -> tuple[str, str]:
    if not isinstance(status_obj, dict):
        return "UNSET", ""
    message = str(status_obj.get("message") or "")
    raw_code = status_obj.get("code")
    if isinstance(raw_code, int):
        mapping = {0: "UNSET", 1: "OK", 2: "ERROR"}
        return mapping.get(raw_code, "UNSET"), message
    if isinstance(raw_code, str):
        upper = raw_code.upper().removeprefix("STATUS_CODE_")
        if upper in ("UNSET", "OK", "ERROR"):
            return upper, message
        if upper.isdigit():
            return _normalize_status_code({"code": int(upper), "message": message})
    return "UNSET", message


def _nonempty_str(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _extract_spans_from_export_dict(payload: dict[str, Any]) -> list[SpanRecord]:
    spans: list[SpanRecord] = []
    resource_spans = payload.get("resourceSpans")
    if resource_spans is None:
        resource_spans = payload.get("resource_spans")
    for resource_entry in resource_spans or []:
        if not isinstance(resource_entry, dict):
            continue
        resource = resource_entry.get("resource") or {}
        resource_attrs = _decode_attributes(resource.get("attributes") or [])
        session_id = _nonempty_str(resource_attrs.get(DAGGER_SESSION_ID_ATTR))
        engine_instance_id = _nonempty_str(
            resource_attrs.get(DAGGER_ENGINE_INSTANCE_ID_ATTR)
        )
        service_name = _nonempty_str(resource_attrs.get(SERVICE_NAME_ATTR))
        scope_spans = resource_entry.get("scopeSpans")
        if scope_spans is None:
            scope_spans = resource_entry.get("scope_spans")
        for scope_entry in scope_spans or []:
            if not isinstance(scope_entry, dict):
                continue
            scope = scope_entry.get("scope") or {}
            scope_name = str(scope.get("name") or "")
            scope_version = str(scope.get("version") or "")
            for span in scope_entry.get("spans") or []:
                if not isinstance(span, dict):
                    continue
                trace_id = _normalize_id(span.get("traceId", span.get("trace_id")), 16)
                span_id = _normalize_id(span.get("spanId", span.get("span_id")), 8)
                parent_raw = span.get("parentSpanId", span.get("parent_span_id"))
                parent_span_id = _normalize_id(parent_raw, 8, allow_empty=True)
                span_attrs = _decode_attributes(span.get("attributes") or [])
                status_code, status_message = _normalize_status_code(span.get("status"))
                start_ns = _safe_int(
                    span.get("startTimeUnixNano", span.get("start_time_unix_nano"))
                )
                end_ns = _safe_int(
                    span.get("endTimeUnixNano", span.get("end_time_unix_nano"))
                )
                spans.append(
                    SpanRecord(
                        trace_id=trace_id,
                        span_id=span_id,
                        parent_span_id=parent_span_id,
                        name=str(span.get("name") or ""),
                        scope_name=scope_name,
                        scope_version=scope_version,
                        kind=str(span.get("kind") or ""),
                        status_code=status_code,
                        status_message=status_message,
                        start_time_unix_nano=start_ns,
                        end_time_unix_nano=end_ns,
                        resource_attributes=dict(resource_attrs),
                        attributes=span_attrs,
                        session_id=session_id,
                        engine_instance_id=engine_instance_id,
                        service_name=service_name,
                    )
                )
    return spans


def protobuf_trace_bytes_to_wire_dict(raw_bytes: bytes) -> dict[str, Any]:
    """Decode binary OTLP ExportTraceServiceRequest into canonical hex-ID wire JSON."""
    from google.protobuf import json_format
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    request = ExportTraceServiceRequest()
    try:
        request.ParseFromString(raw_bytes)
    except Exception as error:
        raise DaggerTelemetryError("otlp_trace_protobuf_invalid") from error
    payload = json_format.MessageToDict(request, preserving_proto_field_name=False)
    return normalize_wire_ids(payload)


def protobuf_metrics_bytes_to_wire_dict(raw_bytes: bytes) -> dict[str, Any]:
    """Validate and decode binary OTLP ExportMetricsServiceRequest."""
    from google.protobuf import json_format
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
        ExportMetricsServiceRequest,
    )

    request = ExportMetricsServiceRequest()
    try:
        request.ParseFromString(raw_bytes)
    except Exception as error:
        raise DaggerTelemetryError("otlp_metrics_protobuf_invalid") from error
    return json_format.MessageToDict(request, preserving_proto_field_name=False)


def normalize_wire_ids(payload: dict[str, Any]) -> dict[str, Any]:
    """Ensure traceId/spanId/parentSpanId fields are lowercase hex strings."""
    cloned = copy.deepcopy(payload)
    for resource in cloned.get("resourceSpans") or []:
        if not isinstance(resource, dict):
            continue
        for scope in resource.get("scopeSpans") or []:
            if not isinstance(scope, dict):
                continue
            for span in scope.get("spans") or []:
                if not isinstance(span, dict):
                    continue
                if span.get("traceId"):
                    span["traceId"] = _normalize_id(span["traceId"], 16)
                if span.get("spanId"):
                    span["spanId"] = _normalize_id(span["spanId"], 8)
                if span.get("parentSpanId"):
                    span["parentSpanId"] = _normalize_id(
                        span["parentSpanId"], 8, allow_empty=True
                    )
    return cloned


def parse_capture(source: dict[str, Any] | list[Any] | str | Path) -> CaptureDataset:
    """Load OTLP spans from dict, batch wrapper, JSON/JSONL string, or file path."""
    if isinstance(source, Path):
        text = source.read_text(encoding="utf-8")
        return parse_capture(text)
    if isinstance(source, str):
        stripped = source.strip()
        if not stripped:
            return CaptureDataset(spans=[], trace_batch_count=0)
        if "\n" in stripped and not stripped.startswith(("{", "[")):
            datasets = [
                parse_capture(json.loads(line))
                for line in stripped.splitlines()
                if line.strip()
            ]
            return _merge_datasets(datasets)
        try:
            loaded = json.loads(stripped)
        except json.JSONDecodeError:
            datasets = [
                parse_capture(json.loads(line))
                for line in stripped.splitlines()
                if line.strip()
            ]
            return _merge_datasets(datasets)
        return parse_capture(loaded)
    if isinstance(source, list):
        return _merge_datasets([parse_capture(item) for item in source])
    if not isinstance(source, dict):
        raise DaggerTelemetryError("unsupported_capture_payload")

    if "trace_batches" in source or "batches" in source:
        raw_batches = source.get("trace_batches")
        if raw_batches is None:
            raw_batches = source.get("batches") or []
        spans: list[SpanRecord] = []
        for batch in raw_batches:
            if isinstance(batch, dict) and "payload" in batch:
                spans.extend(_extract_spans_from_export_dict(batch["payload"]))
            elif isinstance(batch, dict):
                spans.extend(_extract_spans_from_export_dict(batch))
        metric_batches = source.get("metric_batches") or []
        return CaptureDataset(
            spans=spans,
            trace_batch_count=len(raw_batches),
            metric_batch_count=len(metric_batches),
            isolated_capture=source.get("isolated_capture"),
            receiver_observed=source.get("receiver_observed"),
            exporter_configured=source.get("exporter_configured"),
        )

    spans = _extract_spans_from_export_dict(source)
    return CaptureDataset(
        spans=spans,
        trace_batch_count=1 if spans else 0,
        isolated_capture=source.get("isolated_capture"),
        receiver_observed=source.get("receiver_observed"),
        exporter_configured=source.get("exporter_configured"),
    )


def _merge_datasets(datasets: list[CaptureDataset]) -> CaptureDataset:
    merged_spans: list[SpanRecord] = []
    trace_batches = 0
    metric_batches = 0
    isolated: bool | None = None
    receiver_obs: bool | None = None
    exporter_cfg: str | None = None
    for ds in datasets:
        merged_spans.extend(ds.spans)
        trace_batches += ds.trace_batch_count
        metric_batches += ds.metric_batch_count
        if ds.isolated_capture is not None:
            isolated = ds.isolated_capture
        if ds.receiver_observed is not None:
            receiver_obs = ds.receiver_observed
        if ds.exporter_configured is not None:
            exporter_cfg = ds.exporter_configured
    return CaptureDataset(
        spans=merged_spans,
        trace_batch_count=trace_batches,
        metric_batch_count=metric_batches,
        isolated_capture=isolated,
        receiver_observed=receiver_obs,
        exporter_configured=exporter_cfg,
    )


def _index_spans(
    spans: list[SpanRecord],
) -> tuple[
    dict[tuple[str, str], SpanRecord],
    dict[tuple[str, str], list[SpanRecord]],
    set[str],
]:
    by_key: dict[tuple[str, str], SpanRecord] = {}
    children_by_key: dict[tuple[str, str], list[SpanRecord]] = {}
    duplicate_traces: set[str] = set()
    for span in spans:
        key = (span.trace_id, span.span_id)
        if key in by_key:
            duplicate_traces.add(span.trace_id)
        else:
            by_key[key] = span
        children_by_key.setdefault((span.trace_id, span.parent_span_id), []).append(
            span
        )
    return by_key, children_by_key, duplicate_traces


def _is_dagger_span(span: SpanRecord) -> bool:
    return (
        span.scope_name.startswith("dagger.io/")
        or bool(span.session_id)
        or bool(span.engine_instance_id)
        or span.name.startswith(CHECK_SELECTOR_PREFIX)
    )


def _validate_resource_identity(
    spans_to_check: list[SpanRecord],
) -> tuple[str | None, str | None, str | None]:
    sessions: set[str] = set()
    engines: set[str] = set()
    for span in spans_to_check:
        if not span.session_id:
            return None, None, "missing_session_id"
        if not span.engine_instance_id:
            return None, None, "missing_engine_instance_id"
        sessions.add(span.session_id)
        engines.add(span.engine_instance_id)
    if len(sessions) != 1:
        return None, None, "cross_session_chain"
    if len(engines) != 1:
        return None, None, "cross_engine_chain"
    return next(iter(sessions)), next(iter(engines)), None


def _collect_lineage_to_root(
    span: SpanRecord,
    by_key: dict[tuple[str, str], SpanRecord],
) -> tuple[list[SpanRecord], bool]:
    """Walk upward from span; return (ancestors_from_root_to_span, broken_internal_gap)."""
    lineage: list[SpanRecord] = []
    seen: set[str] = set()
    current: SpanRecord | None = span
    while current is not None:
        if current.span_id in seen:
            return lineage, True
        seen.add(current.span_id)
        lineage.append(current)
        if not current.parent_span_id:
            break
        parent = by_key.get((current.trace_id, current.parent_span_id))
        if parent is None:
            # Missing parent is only a broken internal graph if this span claims an
            # internal parent inside the same Engine trace rather than an external root.
            # When tracing from an anchor down to Core, path gaps fail separately;
            # upward missing parent on the anchor is valid iff traceparent matches it.
            break
        current = parent
    lineage.reverse()
    return lineage, False


def _collect_subtree(
    root: SpanRecord,
    children_by_key: dict[tuple[str, str], list[SpanRecord]],
) -> list[SpanRecord]:
    result: list[SpanRecord] = []
    queue: deque[SpanRecord] = deque([root])
    seen: set[str] = set()
    while queue:
        node = queue.popleft()
        if node.span_id in seen:
            continue
        seen.add(node.span_id)
        result.append(node)
        for child in children_by_key.get((node.trace_id, node.span_id), []):
            queue.append(child)
    return result


def _find_path(
    start: SpanRecord,
    target_span_id: str,
    children_by_key: dict[tuple[str, str], list[SpanRecord]],
) -> list[SpanRecord] | None:
    queue: deque[list[SpanRecord]] = deque([[start]])
    seen: set[str] = {start.span_id}
    while queue:
        path = queue.popleft()
        tail = path[-1]
        if tail.span_id == target_span_id:
            return path
        for child in children_by_key.get((tail.trace_id, tail.span_id), []):
            if child.span_id in seen:
                continue
            seen.add(child.span_id)
            queue.append([*path, child])
    return None


def _matches_module_dagql(
    span: SpanRecord, module_name: str, object_name: str, camel_fn: str
) -> bool:
    expected = f"{module_name}:{object_name}.{camel_fn}"
    if span.name != expected:
        return False
    return not span.scope_name or span.scope_name == DAGQL_SCOPE_NAME


def _matches_core(span: SpanRecord, object_name: str, camel_fn: str) -> bool:
    expected = f"{object_name}.{camel_fn}"
    if span.name != expected:
        return False
    return not span.scope_name or span.scope_name == CORE_SCOPE_NAME


def _find_core_peer(
    dagql_node: SpanRecord,
    dagql_subtree: list[SpanRecord],
    by_key: dict[tuple[str, str], SpanRecord],
    *,
    object_name: str,
    camel_fn: str,
) -> tuple[SpanRecord | None, str | None]:
    """Locate the Core span paired with a Module DagQL span (parent in beta.16, or child)."""
    parent = (
        by_key.get((dagql_node.trace_id, dagql_node.parent_span_id))
        if dagql_node.parent_span_id
        else None
    )
    if parent is not None and _matches_core(parent, object_name, camel_fn):
        return parent, None
    core_children = [
        child
        for child in dagql_subtree
        if child.span_id != dagql_node.span_id
        and _matches_core(child, object_name, camel_fn)
    ]
    if not core_children:
        return None, "missing_core_child_span"
    if len(core_children) > 1:
        return None, "multiple_core_children_under_dagql"
    return core_children[0], None


def _discover_check_candidates(
    dataset: CaptureDataset,
    by_key: dict[tuple[str, str], SpanRecord],
    children_by_key: dict[tuple[str, str], list[SpanRecord]],
    duplicate_traces: set[str],
    *,
    target_name: str,
    module_name: str,
    object_name: str,
    camel_fn: str,
) -> tuple[list[CandidateChain], list[str]]:
    selector_name = f"{CHECK_SELECTOR_PREFIX}{target_name}"
    selectors = [span for span in dataset.spans if span.name == selector_name]
    candidates: list[CandidateChain] = []
    rejected: list[str] = []

    if not selectors:
        has_module_or_core = any(
            _matches_module_dagql(span, module_name, object_name, camel_fn)
            or _matches_core(span, object_name, camel_fn)
            for span in dataset.spans
        )
        rejected.append(
            "missing_check_selector"
            if has_module_or_core
            else "no_matching_check_spans"
        )
        return candidates, rejected

    for selector in selectors:
        if selector.trace_id in duplicate_traces:
            rejected.append("duplicate_span_id")
            continue
        subtree = _collect_subtree(selector, children_by_key)
        dagql_nodes = [
            node
            for node in subtree
            if _matches_module_dagql(node, module_name, object_name, camel_fn)
        ]
        if not dagql_nodes:
            rejected.append("broken_parent_chain_or_wrong_module")
            continue
        if len(dagql_nodes) > 1:
            rejected.append("multiple_dagql_descendants_under_selector")
            continue
        dagql_node = dagql_nodes[0]
        dagql_subtree = _collect_subtree(dagql_node, children_by_key)
        core_node, core_err = _find_core_peer(
            dagql_node,
            dagql_subtree,
            by_key,
            object_name=object_name,
            camel_fn=camel_fn,
        )
        if core_err or core_node is None:
            rejected.append(core_err or "missing_core_child_span")
            continue
        leaf_for_path = (
            dagql_node if core_node.span_id == dagql_node.parent_span_id else core_node
        )
        path = _find_path(selector, leaf_for_path.span_id, children_by_key)
        if not path:
            rejected.append("broken_parent_chain")
            continue
        _, _, identity_error = _validate_resource_identity(path)
        if identity_error:
            rejected.append(identity_error)
            continue
        lineage, cycle = _collect_lineage_to_root(selector, by_key)
        if cycle:
            rejected.append("parent_cycle_detected")
            continue
        candidates.append(
            CandidateChain(
                anchor=selector,
                dagql_span=dagql_node,
                core_span=core_node,
                lineage_to_anchor=lineage,
                subtree_spans=dagql_subtree,
            )
        )
    return candidates, rejected


def _discover_artifact_candidates(
    dataset: CaptureDataset,
    by_key: dict[tuple[str, str], SpanRecord],
    children_by_key: dict[tuple[str, str], list[SpanRecord]],
    duplicate_traces: set[str],
    *,
    module_name: str,
    object_name: str,
    camel_fn: str,
) -> tuple[list[CandidateChain], list[str]]:
    dagql_nodes = [
        span
        for span in dataset.spans
        if _matches_module_dagql(span, module_name, object_name, camel_fn)
    ]
    candidates: list[CandidateChain] = []
    rejected: list[str] = []

    if not dagql_nodes:
        rejected.append("no_matching_artifact_function_spans")
        return candidates, rejected

    for dagql_node in dagql_nodes:
        if dagql_node.trace_id in duplicate_traces:
            rejected.append("duplicate_span_id")
            continue
        lineage, cycle = _collect_lineage_to_root(dagql_node, by_key)
        if cycle:
            rejected.append("parent_cycle_detected")
            continue
        if any(node.name.startswith(CHECK_SELECTOR_PREFIX) for node in lineage):
            rejected.append("unexpected_check_selector_ancestor")
            continue
        dagql_subtree = _collect_subtree(dagql_node, children_by_key)
        core_node, core_err = _find_core_peer(
            dagql_node,
            dagql_subtree,
            by_key,
            object_name=object_name,
            camel_fn=camel_fn,
        )
        if core_err or core_node is None:
            rejected.append(core_err or "missing_core_child_span")
            continue
        path_to_core = (
            [core_node, dagql_node]
            if core_node.span_id == dagql_node.parent_span_id
            else _find_path(dagql_node, core_node.span_id, children_by_key)
        )
        if not path_to_core:
            rejected.append("broken_parent_chain")
            continue
        dagger_lineage = [node for node in lineage if _is_dagger_span(node)]
        _, _, identity_error = _validate_resource_identity(
            [*dagger_lineage, *path_to_core]
        )
        if identity_error:
            rejected.append(identity_error)
            continue
        candidates.append(
            CandidateChain(
                anchor=dagql_node,
                dagql_span=dagql_node,
                core_span=core_node,
                lineage_to_anchor=lineage,
                subtree_spans=dagql_subtree,
            )
        )
    return candidates, rejected


def _matches_traceparent_lineage(
    candidate: CandidateChain,
    trace_id: str,
    parent_span_id: str,
) -> tuple[bool, str | None]:
    if candidate.anchor.trace_id != trace_id:
        return False, "trace_id_mismatch"
    for node in candidate.lineage_to_anchor:
        if node.span_id == parent_span_id or node.parent_span_id == parent_span_id:
            return True, None
    return False, "traceparent_parent_not_in_lineage"


def _apply_correlation_proof(
    candidates: list[CandidateChain],
    rejected: list[str],
    *,
    traceparent: str | None,
    expected_session_id: str | None,
    isolated_capture: bool,
) -> tuple[list[CandidateChain], list[str], str]:
    filtered = list(candidates)
    reasons = list(rejected)

    if expected_session_id:
        next_filtered: list[CandidateChain] = []
        for item in filtered:
            if item.anchor.session_id == expected_session_id:
                next_filtered.append(item)
            else:
                reasons.append("session_id_mismatch")
        filtered = next_filtered

    if traceparent:
        tp_trace_id, tp_parent_span_id = parse_traceparent(traceparent)
        next_filtered = []
        for item in filtered:
            matched, fail_reason = _matches_traceparent_lineage(
                item, tp_trace_id, tp_parent_span_id
            )
            if matched:
                next_filtered.append(item)
            elif fail_reason:
                reasons.append(fail_reason)
        filtered = next_filtered
        proof_mode = "matched_traceparent"
    elif expected_session_id:
        proof_mode = "matched_expected_session_id"
    elif isolated_capture:
        proof_mode = "matched_isolated_capture"
    else:
        if filtered:
            reasons.append("unisolated_capture_without_traceparent")
        filtered = []
        proof_mode = "unisolated_capture_without_traceparent"

    return filtered, _unique_preserve_order(reasons), proof_mode


def _unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in values:
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _collect_digests(spans: list[SpanRecord]) -> list[str]:
    digests: list[str] = []
    for span in spans:
        for attr in (DAGGER_DAG_DIGEST_ATTR, DAGGER_CONTENT_DIGEST_ATTR):
            val = span.attributes.get(attr)
            if isinstance(val, str) and val.strip():
                digests.append(val.strip())
    return _unique_preserve_order(digests)


def _is_exec_span(span: SpanRecord) -> bool:
    return span.scope_name == CORE_SCOPE_NAME and span.name in (
        "Container.withExec",
        "Exec",
    )


def _build_cache_and_origin(
    candidate: CandidateChain | None,
    *,
    prior_executions: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if candidate is None:
        return (
            {
                "status": "not_applicable",
                "node_level": "none",
                "outcomes": [],
                "contract": None,
                "cache_result_id": None,
                "cache_hit_route": None,
                "new_tool_execution": None,
                "exec_spans_observed": 0,
            },
            {"status": "not_applicable", "matched_origin": None},
        )

    subtree = candidate.subtree_spans
    exec_spans = [span for span in subtree if _is_exec_span(span)]
    node_level = "function_and_exec" if exec_spans else "function_only"

    cache_spans = [candidate.core_span, *exec_spans]
    outcomes = _unique_preserve_order(
        [
            str(span.attributes[DAGGER_CACHE_OUTCOME_ATTR]).strip().lower()
            for span in cache_spans
            if isinstance(span.attributes.get(DAGGER_CACHE_OUTCOME_ATTR), str)
            and span.attributes[DAGGER_CACHE_OUTCOME_ATTR].strip()
        ]
    )
    contract = _nonempty_str(
        candidate.core_span.attributes.get(DAGGER_CACHE_CONTRACT_ATTR)
    )
    cache_result_id = _nonempty_str(
        candidate.core_span.attributes.get(DAGGER_CACHE_RESULT_ID_ATTR)
    )
    cache_hit_route = _nonempty_str(
        candidate.core_span.attributes.get(DAGGER_CACHE_HIT_ROUTE_ATTR)
    )

    hit_values = {"hit", "joined"}
    if not outcomes:
        cache_status = "unknown"
        new_tool_execution: bool | None = None
    elif all(item in hit_values for item in outcomes):
        cache_status = "observed_hit"
        new_tool_execution = False
    elif all(item == "executed" for item in outcomes):
        cache_status = "observed_execution"
        # Function-level cache.outcome="executed" never proves an uncached inner tool run.
        new_tool_execution = None
    else:
        cache_status = "mixed"
        new_tool_execution = None

    origin_execution: dict[str, Any] = {"status": "unknown", "matched_origin": None}
    if (
        cache_status == "observed_hit"
        and cache_result_id
        and contract
        and prior_executions
    ):
        for prior in prior_executions:
            if (
                prior.get("cache_result_id") == cache_result_id
                and prior.get("contract") == contract
                and prior.get("node_level", node_level) == node_level
            ):
                origin_execution = {
                    "status": "existing_execution_linked",
                    "matched_origin": {
                        "trace_id": prior.get("trace_id"),
                        "span_id": prior.get("span_id"),
                        "session_id": prior.get("session_id"),
                        "cache_result_id": cache_result_id,
                    },
                }
                break

    cache_observation = {
        "status": cache_status,
        "node_level": node_level,
        "outcomes": outcomes,
        "contract": contract,
        "cache_result_id": cache_result_id,
        "cache_hit_route": cache_hit_route,
        "new_tool_execution": new_tool_execution,
        "exec_spans_observed": len(exec_spans),
    }
    return cache_observation, origin_execution


def _build_native_check_result(
    candidate: CandidateChain | None,
    *,
    check_exit_code: int | None,
) -> dict[str, Any]:
    if check_exit_code is None:
        status = "unknown"
    elif check_exit_code == 0:
        status = "passed"
    else:
        status = "failed"

    check_name_attr = None
    check_passed_attr = None
    span_status = None
    if candidate is not None:
        span_status = candidate.core_span.status_code
        for span in (candidate.anchor, candidate.dagql_span, candidate.core_span):
            if check_name_attr is None and DAGGER_CHECK_NAME_ATTR in span.attributes:
                check_name_attr = span.attributes[DAGGER_CHECK_NAME_ATTR]
            if (
                check_passed_attr is None
                and DAGGER_CHECK_PASSED_ATTR in span.attributes
            ):
                check_passed_attr = span.attributes[DAGGER_CHECK_PASSED_ATTR]
    return {
        "status": status,
        "exit_code": check_exit_code,
        "span_status": span_status,
        "check_attributes": {
            DAGGER_CHECK_NAME_ATTR: check_name_attr,
            DAGGER_CHECK_PASSED_ATTR: check_passed_attr,
        },
    }


def correlate_capture(
    capture: CaptureDataset | dict[str, Any] | list[Any] | str | Path,
    *,
    entry_kind: str,
    target_name: str,
    module_name: str = "engineering-ci",
    object_name: str = "EngineeringCi",
    traceparent: str | None = None,
    expected_session_id: str | None = None,
    isolated_capture: bool | None = None,
    exporter_configured: str | None = None,
    check_exit_code: int | None = None,
    declared_target_source_ref: str | None = None,
    required_observability: bool = False,
    prior_executions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Correlate OTLP capture spans with an Engineering CI Dagger invocation."""
    if entry_kind not in ("check", "artifact_function"):
        raise DaggerTelemetryError(f"unsupported_entry_kind:{entry_kind}")
    dataset = capture if isinstance(capture, CaptureDataset) else parse_capture(capture)
    camel_fn = kebab_to_camel(target_name)
    effective_isolated = (
        bool(isolated_capture)
        if isolated_capture is not None
        else bool(dataset.isolated_capture)
    )
    effective_exporter = exporter_configured or dataset.exporter_configured or "unknown"
    if effective_exporter not in ("yes", "no", "unknown"):
        raise DaggerTelemetryError(f"invalid_exporter_configured:{effective_exporter}")

    receiver_observed = (
        bool(dataset.receiver_observed)
        if dataset.receiver_observed is not None
        else (dataset.trace_batch_count > 0 or len(dataset.spans) > 0)
    )
    if receiver_observed:
        sent_status = "sent"
        sent_basis = "receiver_observation"
    elif effective_exporter == "no":
        sent_status = "not_sent"
        sent_basis = "not_sent_reported"
    else:
        sent_status = "unknown"
        sent_basis = "unverified"

    by_key, children_by_key, duplicate_traces = _index_spans(dataset.spans)

    if traceparent:
        try:
            parse_traceparent(traceparent)
        except DaggerTelemetryError as error:
            return _assemble_receipt(
                entry_kind=entry_kind,
                target_name=target_name,
                module_name=module_name,
                object_name=object_name,
                camel_fn=camel_fn,
                binding_status="unbound",
                binding_reason=str(error),
                rejected_reasons=[str(error)],
                candidate=None,
                candidate_count=0,
                isolated_capture=effective_isolated,
                traceparent_supplied=True,
                exporter_configured=effective_exporter,
                sent_status=sent_status,
                sent_basis=sent_basis,
                receiver_observed=receiver_observed,
                check_exit_code=check_exit_code,
                declared_target_source_ref=declared_target_source_ref,
                required_observability=required_observability,
                prior_executions=prior_executions,
            )

    if entry_kind == "check":
        candidates, rejected = _discover_check_candidates(
            dataset,
            by_key,
            children_by_key,
            duplicate_traces,
            target_name=target_name,
            module_name=module_name,
            object_name=object_name,
            camel_fn=camel_fn,
        )
    else:
        candidates, rejected = _discover_artifact_candidates(
            dataset,
            by_key,
            children_by_key,
            duplicate_traces,
            module_name=module_name,
            object_name=object_name,
            camel_fn=camel_fn,
        )

    matched, all_reasons, proof_mode = _apply_correlation_proof(
        candidates,
        rejected,
        traceparent=traceparent,
        expected_session_id=expected_session_id,
        isolated_capture=effective_isolated,
    )

    if len(matched) == 1:
        binding_status = "bound"
        binding_reason = proof_mode
        chosen: CandidateChain | None = matched[0]
    elif len(matched) > 1:
        binding_status = "ambiguous"
        binding_reason = "multiple_matching_candidates"
        chosen = None
    elif "duplicate_span_id" in all_reasons:
        binding_status = "ambiguous"
        binding_reason = "duplicate_span_id"
        chosen = None
    else:
        binding_status = "unbound"
        if "traceparent_parent_not_in_lineage" in all_reasons:
            binding_reason = "traceparent_parent_not_in_lineage"
        else:
            binding_reason = all_reasons[0] if all_reasons else "no_matching_candidates"
        chosen = None

    return _assemble_receipt(
        entry_kind=entry_kind,
        target_name=target_name,
        module_name=module_name,
        object_name=object_name,
        camel_fn=camel_fn,
        binding_status=binding_status,
        binding_reason=binding_reason,
        rejected_reasons=all_reasons,
        candidate=chosen,
        candidate_count=len(matched),
        isolated_capture=effective_isolated,
        traceparent_supplied=bool(traceparent),
        exporter_configured=effective_exporter,
        sent_status=sent_status,
        sent_basis=sent_basis,
        receiver_observed=receiver_observed,
        check_exit_code=check_exit_code,
        declared_target_source_ref=declared_target_source_ref,
        required_observability=required_observability,
        prior_executions=prior_executions,
    )


def _assemble_receipt(
    *,
    entry_kind: str,
    target_name: str,
    module_name: str,
    object_name: str,
    camel_fn: str,
    binding_status: str,
    binding_reason: str,
    rejected_reasons: list[str],
    candidate: CandidateChain | None,
    candidate_count: int,
    isolated_capture: bool,
    traceparent_supplied: bool,
    exporter_configured: str,
    sent_status: str,
    sent_basis: str,
    receiver_observed: bool,
    check_exit_code: int | None,
    declared_target_source_ref: str | None,
    required_observability: bool,
    prior_executions: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    cache_observation, origin_execution = _build_cache_and_origin(
        candidate, prior_executions=prior_executions
    )
    native_check_result = _build_native_check_result(
        candidate, check_exit_code=check_exit_code
    )
    observed_digests = (
        _collect_digests(
            [
                candidate.anchor,
                candidate.dagql_span,
                candidate.core_span,
                *candidate.lineage_to_anchor,
                *candidate.subtree_spans,
            ]
        )
        if candidate is not None
        else []
    )
    gate_failures: list[str] = []
    if required_observability:
        if exporter_configured != "yes":
            gate_failures.append(f"exporter_configured={exporter_configured}")
        if sent_status != "sent":
            gate_failures.append(f"sent={sent_status}")
        if not receiver_observed:
            gate_failures.append("receiver_not_observed")
        if binding_status != "bound":
            gate_failures.append(f"trace_binding={binding_status}:{binding_reason}")

    return {
        "schema_version": CONTRACT_VERSION,
        "kind": NATIVE_TRACE_RECEIPT_KIND,
        "engine_version": PINNED_ENGINE_VERSION,
        "target": {
            "entry_kind": entry_kind,
            "target_name": target_name,
            "module_name": module_name,
            "object_name": object_name,
            "camel_function": camel_fn,
        },
        "correlation": {
            "status": binding_status,
            "reason": binding_reason,
            "isolated_capture": isolated_capture,
            "traceparent_supplied": traceparent_supplied,
            "candidate_count": candidate_count,
            "matched_trace_id": candidate.anchor.trace_id if candidate else None,
            "matched_session_id": candidate.anchor.session_id if candidate else None,
            "matched_engine_instance_id": (
                candidate.anchor.engine_instance_id if candidate else None
            ),
            "anchor_span_id": candidate.anchor.span_id if candidate else None,
            "dagql_span_id": candidate.dagql_span.span_id if candidate else None,
            "core_span_id": candidate.core_span.span_id if candidate else None,
            "lineage_span_ids": (
                [item.span_id for item in candidate.lineage_to_anchor]
                if candidate
                else []
            ),
            "rejected_reasons": rejected_reasons,
        },
        "claims": {
            "exporter_configured": exporter_configured,
            "sent": sent_status,
            "sent_basis": sent_basis,
            "receiver_observed": receiver_observed,
            "trace_invocation_binding": binding_status,
            "native_check_result": native_check_result,
        },
        "source_binding": {
            "source_binding_status": "unknown",
            "declared_target_source_ref": declared_target_source_ref,
            "observed_dag_digests": observed_digests,
            "note": (
                "Git source identity remains unverified until #66 materialization "
                "receipt exists; trace correlation alone binds an invocation only."
            ),
        },
        "cache_observation": cache_observation,
        "origin_execution": origin_execution,
        "observability_gate": {
            "required_observability": required_observability,
            "satisfied": not gate_failures,
            "failures": gate_failures,
        },
    }


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


class _OtlpCaptureState:
    """Thread-safe serialized capture accumulator for OTLP HTTP requests."""

    def __init__(self, *, isolated_capture: bool = True) -> None:
        self._lock = threading.Lock()
        self._trace_batches: list[dict[str, Any]] = []
        self._metric_batches: list[dict[str, Any]] = []
        self._isolated_capture = isolated_capture

    def record_traces(self, payload: dict[str, Any], *, content_type: str) -> None:
        with self._lock:
            self._trace_batches.append(
                {
                    "batch_index": len(self._trace_batches),
                    "received_at": datetime.datetime.now(datetime.UTC).isoformat(),
                    "content_type": content_type,
                    "payload": payload,
                }
            )

    def record_metrics(self, payload: dict[str, Any], *, content_type: str) -> None:
        resource_metrics = payload.get("resourceMetrics") or payload.get(
            "resource_metrics"
        )
        with self._lock:
            self._metric_batches.append(
                {
                    "batch_index": len(self._metric_batches),
                    "received_at": datetime.datetime.now(datetime.UTC).isoformat(),
                    "content_type": content_type,
                    "resource_metrics_count": len(resource_metrics or []),
                }
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": CONTRACT_VERSION,
                "engine_version": PINNED_ENGINE_VERSION,
                "isolated_capture": self._isolated_capture,
                "receiver_observed": len(self._trace_batches) > 0,
                "trace_batches": copy.deepcopy(self._trace_batches),
                "metric_batches": copy.deepcopy(self._metric_batches),
            }


def _decode_http_body(headers: Any, body: bytes) -> bytes:
    encoding = str(headers.get("Content-Encoding") or "").strip().lower()
    if encoding == "gzip":
        return gzip.decompress(body)
    return body


def _make_handler(state: _OtlpCaptureState) -> type[http.server.BaseHTTPRequestHandler]:
    from engineering_ci.cicd_telemetry import (
        TelemetryError,
        decode_otlp_export_request,
        decode_otlp_metrics_request,
    )

    class OtlpHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_json(self, status: int, body: dict[str, Any]) -> None:
            raw = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            if self.path.rstrip("/") == "/healthz":
                snap = state.snapshot()
                self._send_json(
                    200,
                    {
                        "status": "ok",
                        "trace_batches": len(snap["trace_batches"]),
                        "metric_batches": len(snap["metric_batches"]),
                    },
                )
                return
            self._send_json(404, {"error": "not_found"})

        def do_POST(self) -> None:
            content_length = int(self.headers.get("Content-Length") or "0")
            raw_body = self.rfile.read(content_length)
            try:
                body = _decode_http_body(self.headers, raw_body)
            except (OSError, zlib.error, EOFError):
                self._send_json(400, {"error": "invalid_gzip_body"})
                return

            content_type = (
                str(self.headers.get("Content-Type") or "application/x-protobuf")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
            route = self.path.rstrip("/")
            if route == "/v1/traces":
                try:
                    if content_type == "application/json":
                        decoded_json = json.loads(body.decode("utf-8"))
                        decode_otlp_export_request(decoded_json)
                        wire_dict = normalize_wire_ids(decoded_json)
                    else:
                        wire_dict = protobuf_trace_bytes_to_wire_dict(body)
                except (
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                    DaggerTelemetryError,
                    TelemetryError,
                ):
                    self._send_json(400, {"error": "invalid_otlp_traces_payload"})
                    return
                state.record_traces(wire_dict, content_type=content_type)
                self._send_json(200, {})
                return

            if route == "/v1/metrics":
                try:
                    if content_type == "application/json":
                        decoded_json = json.loads(body.decode("utf-8"))
                        decode_otlp_metrics_request(decoded_json)
                        metric_dict = decoded_json
                    else:
                        metric_dict = protobuf_metrics_bytes_to_wire_dict(body)
                except (
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                    DaggerTelemetryError,
                    TelemetryError,
                ):
                    self._send_json(400, {"error": "invalid_otlp_metrics_payload"})
                    return
                state.record_metrics(metric_dict, content_type=content_type)
                self._send_json(200, {})
                return

            self._send_json(404, {"error": "unknown_otlp_route"})

    return OtlpHandler


class OtlpHttpCaptureServer:
    """Bounded localhost OTLP/HTTP receiver with serialized locking & atomic dump."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        isolated_capture: bool = True,
    ) -> None:
        self.state = _OtlpCaptureState(isolated_capture=isolated_capture)
        self._server = http.server.ThreadingHTTPServer(
            (host, port), _make_handler(self.state)
        )
        self._server.daemon_threads = True
        self.host = str(self._server.server_address[0])
        self.port = int(self._server.server_address[1])
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start serving HTTP requests on a background thread."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.1},
            daemon=True,
        )
        self._thread.start()

    def stop(self, output_path: Path | None = None) -> dict[str, Any]:
        """Shut down HTTP server cleanly and optionally flush capture atomically."""
        if self._thread is not None:
            self._server.shutdown()
            self._thread.join(timeout=5.0)
            self._thread = None
        self._server.server_close()
        snapshot = self.state.snapshot()
        if output_path is not None:
            _atomic_write_json(output_path, snapshot)
        return snapshot


def _load_json_dict(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _mutate_strip_engine_id(raw: dict[str, Any]) -> dict[str, Any]:
    mutated = copy.deepcopy(raw)
    for batch in mutated.get("trace_batches") or []:
        for resource in batch.get("payload", {}).get("resourceSpans") or []:
            attrs = resource.get("resource", {}).get("attributes") or []
            resource["resource"]["attributes"] = [
                attr
                for attr in attrs
                if attr.get("key") != DAGGER_ENGINE_INSTANCE_ID_ATTR
            ]
    return mutated


def _mutate_break_check_parent(raw: dict[str, Any], target_name: str) -> dict[str, Any]:
    mutated = copy.deepcopy(raw)
    selector_name = f"{CHECK_SELECTOR_PREFIX}{target_name}"
    for batch in mutated.get("trace_batches") or []:
        for resource in batch.get("payload", {}).get("resourceSpans") or []:
            for scope in resource.get("scopeSpans") or []:
                for span in scope.get("spans") or []:
                    if span.get("name") == selector_name:
                        span["spanId"] = "ffffffffffffffff"
    return mutated


def _mutate_duplicate_check_chain(raw: dict[str, Any]) -> dict[str, Any]:
    mutated = copy.deepcopy(raw)
    batches = copy.deepcopy(mutated.get("trace_batches") or [])
    for batch in batches:
        for resource in batch.get("payload", {}).get("resourceSpans") or []:
            for scope in resource.get("scopeSpans") or []:
                for span in scope.get("spans") or []:
                    if span.get("traceId"):
                        span["traceId"] = (
                            "f" if not span["traceId"].startswith("f") else "e"
                        ) + span["traceId"][1:]
                    if span.get("spanId"):
                        span["spanId"] = (
                            "a" if not span["spanId"].startswith("a") else "b"
                        ) + span["spanId"][1:]
                    if span.get("parentSpanId"):
                        span["parentSpanId"] = (
                            "a" if not span["parentSpanId"].startswith("a") else "b"
                        ) + span["parentSpanId"][1:]
    mutated.setdefault("trace_batches", []).extend(batches)
    return mutated


def _mutate_wrong_module_prefix(raw: dict[str, Any], camel_fn: str) -> dict[str, Any]:
    mutated = copy.deepcopy(raw)
    expected = f"engineering-ci:EngineeringCi.{camel_fn}"
    for batch in mutated.get("trace_batches") or []:
        for resource in batch.get("payload", {}).get("resourceSpans") or []:
            for scope in resource.get("scopeSpans") or []:
                for span in scope.get("spans") or []:
                    if span.get("name") == expected:
                        span["name"] = f"other-module:EngineeringCi.{camel_fn}"
    return mutated


def _mutate_cross_session_core(raw: dict[str, Any], camel_fn: str) -> dict[str, Any]:
    mutated = copy.deepcopy(raw)
    expected_core = f"EngineeringCi.{camel_fn}"
    new_batches: list[dict[str, Any]] = []
    for batch in mutated.get("trace_batches") or []:
        cloned_batch = copy.deepcopy(batch)
        split_resources: list[dict[str, Any]] = []
        for resource in cloned_batch.get("payload", {}).get("resourceSpans") or []:
            normal_scopes: list[dict[str, Any]] = []
            core_spans_found: list[tuple[dict[str, Any], dict[str, Any]]] = []
            for scope in resource.get("scopeSpans") or []:
                kept_spans = []
                for span in scope.get("spans") or []:
                    if span.get("name") == expected_core:
                        core_spans_found.append((scope.get("scope") or {}, span))
                    else:
                        kept_spans.append(span)
                if kept_spans:
                    normal_scope = copy.deepcopy(scope)
                    normal_scope["spans"] = kept_spans
                    normal_scopes.append(normal_scope)
            if normal_scopes:
                res_copy = copy.deepcopy(resource)
                res_copy["scopeSpans"] = normal_scopes
                split_resources.append(res_copy)
            if core_spans_found:
                res_alt = copy.deepcopy(resource)
                attrs = res_alt.setdefault("resource", {}).setdefault("attributes", [])
                updated = False
                for attr in attrs:
                    if attr.get("key") == DAGGER_SESSION_ID_ATTR:
                        attr["value"] = {"stringValue": "mutated-other-session"}
                        updated = True
                if not updated:
                    attrs.append(
                        {
                            "key": DAGGER_SESSION_ID_ATTR,
                            "value": {"stringValue": "mutated-other-session"},
                        }
                    )
                res_alt["scopeSpans"] = [
                    {"scope": scope_meta, "spans": [span]}
                    for scope_meta, span in core_spans_found
                ]
                split_resources.append(res_alt)
        cloned_batch["payload"]["resourceSpans"] = split_resources
        new_batches.append(cloned_batch)
    mutated["trace_batches"] = new_batches
    return mutated


def _assert_receipt_positive(
    receipt: dict[str, Any],
    *,
    expected_entry_kind: str,
    expected_target: str,
    expected_check_status: str,
) -> list[str]:
    failures: list[str] = []
    target = receipt.get("target", {})
    corr = receipt.get("correlation", {})
    claims = receipt.get("claims", {})
    source_binding = receipt.get("source_binding", {})
    cache_obs = receipt.get("cache_observation", {})
    origin = receipt.get("origin_execution", {})
    gate = receipt.get("observability_gate", {})

    if target.get("entry_kind") != expected_entry_kind:
        failures.append(
            f"{expected_target}:wrong_entry_kind:{target.get('entry_kind')}"
        )
    if target.get("target_name") != expected_target:
        failures.append(
            f"{expected_target}:wrong_target_name:{target.get('target_name')}"
        )
    if corr.get("status") != "bound":
        failures.append(
            f"{expected_target}:not_bound:{corr.get('status')}:{corr.get('reason')}"
        )
    if not corr.get("matched_session_id"):
        failures.append(f"{expected_target}:missing_matched_session_id")
    if not corr.get("matched_engine_instance_id"):
        failures.append(f"{expected_target}:missing_matched_engine_instance_id")
    if (
        claims.get("sent") != "sent"
        or claims.get("sent_basis") != "receiver_observation"
    ):
        failures.append(f"{expected_target}:invalid_sent_claim:{claims}")
    if claims.get("receiver_observed") is not True:
        failures.append(f"{expected_target}:receiver_not_observed")
    if claims.get("trace_invocation_binding") != "bound":
        failures.append(f"{expected_target}:trace_invocation_binding_not_bound")
    if claims.get("native_check_result", {}).get("status") != expected_check_status:
        failures.append(
            f"{expected_target}:unexpected_check_status:"
            f"{claims.get('native_check_result', {}).get('status')}!={expected_check_status}"
        )
    if source_binding.get("source_binding_status") != "unknown":
        failures.append(
            f"{expected_target}:source_binding_must_stay_unknown:"
            f"{source_binding.get('source_binding_status')}"
        )
    if (
        cache_obs.get("node_level") == "function_only"
        and cache_obs.get("status") == "observed_execution"
    ):
        if cache_obs.get("new_tool_execution") is not None:
            failures.append(
                f"{expected_target}:function_only_executed_claimed_new_exec"
            )
        if origin.get("status") != "unknown":
            failures.append(
                f"{expected_target}:function_only_executed_overclaimed_origin:{origin}"
            )
    if gate.get("required_observability") and gate.get("satisfied") is not True:
        failures.append(f"{expected_target}:observability_gate_unsatisfied:{gate}")
    return failures


def verify_a1_suite(
    *,
    pass_capture: dict[str, Any],
    pass_receipt: dict[str, Any],
    fail_capture: dict[str, Any],
    fail_receipt: dict[str, Any],
    artifact_capture: dict[str, Any],
    artifact_receipt: dict[str, Any],
    pass_target: str = "lint",
    fail_target: str = "lint",
    artifact_target: str = "repository-ruff-tools",
) -> dict[str, Any]:
    """Run strict A.1 positive and negative verification checks on real OTLP captures."""
    failures: list[str] = []
    failures.extend(
        _assert_receipt_positive(
            pass_receipt,
            expected_entry_kind="check",
            expected_target=pass_target,
            expected_check_status="passed",
        )
    )
    failures.extend(
        _assert_receipt_positive(
            fail_receipt,
            expected_entry_kind="check",
            expected_target=fail_target,
            expected_check_status="failed",
        )
    )
    failures.extend(
        _assert_receipt_positive(
            artifact_receipt,
            expected_entry_kind="artifact_function",
            expected_target=artifact_target,
            expected_check_status="unknown",
        )
    )

    neg_results: dict[str, str] = {}
    missing_engine = correlate_capture(
        _mutate_strip_engine_id(pass_capture),
        entry_kind="check",
        target_name=pass_target,
        isolated_capture=True,
    )
    neg_results["strip_engine_instance_id"] = missing_engine["correlation"]["status"]
    if missing_engine["correlation"]["status"] == "bound":
        failures.append("neg_strip_engine_instance_id_bound")

    broken_parent = correlate_capture(
        _mutate_break_check_parent(pass_capture, pass_target),
        entry_kind="check",
        target_name=pass_target,
        isolated_capture=True,
    )
    neg_results["break_parent_chain"] = broken_parent["correlation"]["status"]
    if broken_parent["correlation"]["status"] == "bound":
        failures.append("neg_break_parent_chain_bound")

    dup_chain = correlate_capture(
        _mutate_duplicate_check_chain(pass_capture),
        entry_kind="check",
        target_name=pass_target,
        isolated_capture=True,
    )
    neg_results["duplicate_check_chain"] = dup_chain["correlation"]["status"]
    if dup_chain["correlation"]["status"] != "ambiguous":
        failures.append(
            f"neg_duplicate_check_chain_expected_ambiguous:{dup_chain['correlation']['status']}"
        )

    unisolated = correlate_capture(
        pass_capture,
        entry_kind="check",
        target_name=pass_target,
        isolated_capture=False,
    )
    neg_results["unisolated_without_traceparent"] = unisolated["correlation"]["status"]
    if unisolated["correlation"]["status"] != "unbound":
        failures.append("neg_unisolated_without_traceparent_bound")

    wrong_mod = correlate_capture(
        _mutate_wrong_module_prefix(pass_capture, kebab_to_camel(pass_target)),
        entry_kind="check",
        target_name=pass_target,
        isolated_capture=True,
    )
    neg_results["wrong_module_prefix"] = wrong_mod["correlation"]["status"]
    if wrong_mod["correlation"]["status"] == "bound":
        failures.append("neg_wrong_module_prefix_bound")

    cross_session = correlate_capture(
        _mutate_cross_session_core(pass_capture, kebab_to_camel(pass_target)),
        entry_kind="check",
        target_name=pass_target,
        isolated_capture=True,
    )
    neg_results["cross_session_split"] = cross_session["correlation"]["status"]
    if cross_session["correlation"]["status"] == "bound":
        failures.append("neg_cross_session_split_bound")

    # Verify fail capture contains negative check exit code while still binding trace.
    wrong_fn_artifact = correlate_capture(
        pass_capture,
        entry_kind="artifact_function",
        target_name=pass_target,
        isolated_capture=True,
    )
    neg_results["check_cannot_impersonate_artifact"] = wrong_fn_artifact["correlation"][
        "status"
    ]
    if wrong_fn_artifact["correlation"]["status"] == "bound":
        failures.append("neg_check_impersonated_artifact")

    # Verify genuine fail capture isn't empty and artifact capture refuses Check entry.
    artifact_as_check = correlate_capture(
        artifact_capture,
        entry_kind="check",
        target_name=artifact_target,
        isolated_capture=True,
    )
    neg_results["artifact_cannot_impersonate_check"] = artifact_as_check["correlation"][
        "status"
    ]
    if artifact_as_check["correlation"]["status"] == "bound":
        failures.append("neg_artifact_impersonated_check")

    if not parse_capture(fail_capture).spans:
        failures.append("fail_capture_empty")

    report = {
        "schema_version": CONTRACT_VERSION,
        "kind": A1_VERIFICATION_KIND,
        "engine_version": PINNED_ENGINE_VERSION,
        "passed": not failures,
        "failures": failures,
        "negative_controls": neg_results,
        "checked_targets": {
            "pass": pass_target,
            "fail": fail_target,
            "artifact_function": artifact_target,
        },
    }
    return report


def _run_receive(args: argparse.Namespace) -> int:
    server = OtlpHttpCaptureServer(
        host=args.host,
        port=args.port,
        isolated_capture=not args.shared_capture,
    )
    server.start()
    stop_event = threading.Event()

    def _request_stop(*_unused: Any) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _request_stop)

    if args.ready_file:
        _atomic_write_json(
            Path(args.ready_file),
            {
                "status": "ready",
                "host": server.host,
                "port": server.port,
                "pid": os.getpid(),
            },
        )

    deadline = (
        time.monotonic() + args.duration_seconds
        if args.duration_seconds and args.duration_seconds > 0
        else None
    )
    stop_file = Path(args.stop_file) if args.stop_file else None
    try:
        while not stop_event.is_set():
            if stop_file is not None and stop_file.exists():
                break
            if deadline is not None and time.monotonic() >= deadline:
                break
            stop_event.wait(timeout=0.1)
    finally:
        snapshot = server.stop(output_path=Path(args.output))

    print(
        json.dumps(
            {
                "status": "stopped",
                "output": str(args.output),
                "trace_batches": len(snapshot["trace_batches"]),
                "metric_batches": len(snapshot["metric_batches"]),
            }
        )
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="engineering-ci dagger-telemetry",
        description="Dagger native OTLP capture, correlation, and A.1 verification.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    sub.add_parser("capabilities", help="Print pinned v1.0.0-beta.16 capabilities.")

    receive = sub.add_parser("receive", help="Run serialized OTLP/HTTP capture server.")
    receive.add_argument("--host", default="127.0.0.1")
    receive.add_argument("--port", type=int, default=4318)
    receive.add_argument("--output", required=True, type=Path)
    receive.add_argument("--ready-file", type=Path)
    receive.add_argument("--stop-file", type=Path)
    receive.add_argument("--duration-seconds", type=float)
    receive.add_argument("--shared-capture", action="store_true")

    corr = sub.add_parser("correlate", help="Correlate OTLP capture with invocation.")
    corr.add_argument("--capture", required=True, type=Path)
    corr.add_argument(
        "--entry-kind", required=True, choices=("check", "artifact_function")
    )
    corr.add_argument("--target", required=True)
    corr.add_argument("--module-name", default="engineering-ci")
    corr.add_argument("--object-name", default="EngineeringCi")
    corr.add_argument("--traceparent")
    corr.add_argument("--expected-session-id")
    corr.add_argument("--isolated", action="store_true")
    corr.add_argument(
        "--exporter-configured", default="unknown", choices=("yes", "no", "unknown")
    )
    corr.add_argument("--check-exit-code", type=int)
    corr.add_argument("--source-ref")
    corr.add_argument("--required-observability", action="store_true")
    corr.add_argument("--output", type=Path)

    verify = sub.add_parser(
        "verify-a1", help="Verify real PASS/FAIL/Artifact captures & negative controls."
    )
    verify.add_argument("--pass-capture", required=True, type=Path)
    verify.add_argument("--pass-receipt", required=True, type=Path)
    verify.add_argument("--fail-capture", required=True, type=Path)
    verify.add_argument("--fail-receipt", required=True, type=Path)
    verify.add_argument("--artifact-capture", required=True, type=Path)
    verify.add_argument("--artifact-receipt", required=True, type=Path)
    verify.add_argument("--pass-target", default="lint")
    verify.add_argument("--fail-target", default="lint")
    verify.add_argument("--artifact-target", default="repository-ruff-tools")
    verify.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for ``engineering-ci dagger-telemetry``."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.subcommand == "capabilities":
        print(json.dumps(get_beta16_capabilities(), indent=2, sort_keys=True))
        return 0
    if args.subcommand == "receive":
        return _run_receive(args)
    if args.subcommand == "correlate":
        receipt = correlate_capture(
            args.capture,
            entry_kind=args.entry_kind,
            target_name=args.target,
            module_name=args.module_name,
            object_name=args.object_name,
            traceparent=args.traceparent,
            expected_session_id=args.expected_session_id,
            isolated_capture=True if args.isolated else None,
            exporter_configured=args.exporter_configured,
            check_exit_code=args.check_exit_code,
            declared_target_source_ref=args.source_ref,
            required_observability=args.required_observability,
        )
        if args.output:
            _atomic_write_json(args.output, receipt)
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return 0 if receipt["observability_gate"]["satisfied"] else 1
    if args.subcommand == "verify-a1":
        report = verify_a1_suite(
            pass_capture=_load_json_dict(args.pass_capture),
            pass_receipt=_load_json_dict(args.pass_receipt),
            fail_capture=_load_json_dict(args.fail_capture),
            fail_receipt=_load_json_dict(args.fail_receipt),
            artifact_capture=_load_json_dict(args.artifact_capture),
            artifact_receipt=_load_json_dict(args.artifact_receipt),
            pass_target=args.pass_target,
            fail_target=args.fail_target,
            artifact_target=args.artifact_target,
        )
        if args.output:
            _atomic_write_json(args.output, report)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["passed"] else 1
    parser.error(f"unknown subcommand: {args.subcommand}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
