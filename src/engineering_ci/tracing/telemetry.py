"""Content-free SDK Span restoration and official OTLP/HTTP delivery."""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version

from .config import TelemetryConfig
from .projection import ACTIVITIES, PROJECTION_VERSION, identifier
from .results import prepare_result
from .store import ArchiveError


def time_ns(value):
    """Restore exact SDK microseconds without floating-point epoch conversion."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo != UTC:
        raise ValueError
    delta = parsed - datetime(1970, 1, 1, tzinfo=UTC)
    return (
        delta.days * 86400 + delta.seconds
    ) * 1_000_000_000 + delta.microseconds * 1000


def span_fields(projection):
    """Accept only the implemented projection's safe Span subset, never raw content."""
    prepare_result(projection, "0" * 64)
    result = []
    try:
        for span in projection["spans"]:
            attrs = span["attributes"]
            activity, outcome = (
                attrs["gen_ai.tool.name"],
                attrs["engineering_ci.operation.outcome"],
            )
            required = {
                "gen_ai.operation.name",
                "gen_ai.tool.name",
                "gen_ai.conversation.id",
                "engineering_ci.source.item_id",
                "engineering_ci.source.turn_id",
                "engineering_ci.archive.snapshot_id",
                "engineering_ci.archive.event_id",
                "engineering_ci.projection.version",
                "engineering_ci.operation.outcome",
            }
            if (
                set(attrs) - required - {"process.exit.code", "error.type"}
                or not required <= set(attrs)
                or activity not in ACTIVITIES.values()
                or outcome not in {"passed", "failed", "unknown"}
                or attrs["gen_ai.operation.name"] != "execute_tool"
                or attrs["engineering_ci.archive.snapshot_id"]
                != projection["snapshot_id"]
                or attrs["engineering_ci.projection.version"] != PROJECTION_VERSION
                or any(
                    identifier(attrs[key]) is None
                    for key in (
                        "gen_ai.conversation.id",
                        "engineering_ci.source.item_id",
                        "engineering_ci.source.turn_id",
                    )
                )
                or not re.fullmatch(
                    r"[0-9]{1,24}", attrs["engineering_ci.archive.event_id"]
                )
                or span["name"] != f"execute_tool {activity}"
                or span["kind"] != "SpanKind.INTERNAL"
                or span.get("parent_id") is not None
                or span.get("events")
                or span.get("links")
                or span["status"]
                != {"status_code": "ERROR" if outcome == "failed" else "UNSET"}
            ):
                raise ValueError
            if "process.exit.code" in attrs and (
                type(attrs["process.exit.code"]) is not int
                or not -(2**31) <= attrs["process.exit.code"] < 2**31
            ):
                raise ValueError
            if "error.type" in attrs and attrs["error.type"] not in {
                "process_exit_nonzero",
                "tool_failed",
            }:
                raise ValueError
            resource = span["resource"]["attributes"]
            if (
                set(resource) != {"service.name", "service.version"}
                or resource["service.name"] != "codex"
                or not re.fullmatch(
                    r"[0-9][0-9A-Za-z.+-]{0,63}", resource["service.version"]
                )
            ):
                raise ValueError
            context = span["context"]
            if any(
                not re.fullmatch(pattern, context[key])
                for key, pattern in (
                    ("trace_id", r"0x[0-9a-f]{1,32}"),
                    ("span_id", r"0x[0-9a-f]{1,16}"),
                )
            ):
                raise ValueError
            trace_id, span_id = (
                int(context["trace_id"], 16),
                int(context["span_id"], 16),
            )
            start, end = time_ns(span["start_time"]), time_ns(span["end_time"])
            if not trace_id or not span_id or not 0 <= start <= end < 2**63:
                raise ValueError
            result.append(
                {
                    "name": span["name"],
                    "trace_id": trace_id,
                    "span_id": span_id,
                    "start": start,
                    "end": end,
                    "attributes": dict(attrs),
                    "resource": resource,
                    "failed": outcome == "failed",
                }
            )
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
        raise ArchiveError(
            "invalid_projection", "Unsupported or unsafe SDK Span projection"
        ) from None
    return result


def restore_spans(fields):
    """Use SDK objects for the exporter; do not implement OTLP encoding."""
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.util.instrumentation import InstrumentationScope
    from opentelemetry.trace import (
        SpanContext,
        SpanKind,
        Status,
        StatusCode,
        TraceFlags,
    )

    return [
        ReadableSpan(
            name=x["name"],
            context=SpanContext(
                x["trace_id"], x["span_id"], False, TraceFlags(TraceFlags.SAMPLED)
            ),
            kind=SpanKind.INTERNAL,
            resource=Resource(x["resource"]),
            start_time=x["start"],
            end_time=x["end"],
            attributes=x["attributes"],
            status=Status(StatusCode.ERROR if x["failed"] else StatusCode.UNSET),
            instrumentation_scope=InstrumentationScope(
                "engineering_ci.trace", PROJECTION_VERSION
            ),
        )
        for x in fields
    ]


@dataclass(frozen=True)
class ExportAck:
    status: str
    http_status: int | None = None
    rejected: int | None = None
    attempts: int = 0
    sdk_result: str | None = None


@contextmanager
def sdk_sender(settings):
    """Let the SDK own HTTP/retries; inspect its response without leaking diagnostics."""
    import requests
    from google.protobuf.message import DecodeError
    from opentelemetry.exporter.otlp.proto.http import Compression
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceResponse,
    )

    class Session(requests.Session):
        def __init__(self):
            super().__init__()
            self.trust_env = False
            self.response, self.attempts, self.error = None, 0, None

        def request(self, *args, **kwargs):
            self.attempts += 1
            self.response = None
            # No ambient OTEL headers/netrc/proxy credentials cross this explicit target.
            kwargs["headers"] = {"Content-Type": "application/x-protobuf"}
            kwargs["allow_redirects"] = False
            kwargs["verify"] = True
            kwargs["cert"] = ()
            self.cert = None
            try:
                self.response = super().request(*args, **kwargs)
                return self.response
            except requests.RequestException as error:
                self.error = (
                    "tls_error"
                    if isinstance(error, requests.exceptions.SSLError)
                    else "network_error"
                )
                raise

    session = Session()
    loggers = [
        logging.getLogger(name)
        for name in (
            "opentelemetry.exporter.otlp.proto.http.trace_exporter",
            "opentelemetry.util.re",
        )
    ]
    previous = [logger.disabled for logger in loggers]
    for logger in loggers:
        logger.disabled = True
    exporter = None
    try:
        exporter = OTLPSpanExporter(
            endpoint=settings.endpoint,
            timeout=settings.timeout,
            session=session,
            compression=Compression.NoCompression,
            max_request_size=4 * 1024 * 1024,
        )

        def send(batch):
            session.response, session.attempts, session.error = None, 0, None
            sdk_result = exporter.export(batch).name.lower()
            response = session.response
            if response is None:
                return ExportAck(
                    session.error or "export_failed",
                    attempts=session.attempts,
                    sdk_result=sdk_result,
                )
            if response.status_code != 200:
                return ExportAck(
                    "rejected"
                    if 400 <= response.status_code < 500
                    and response.status_code not in {408, 429}
                    else "unconfirmed",
                    response.status_code,
                    attempts=session.attempts,
                    sdk_result=sdk_result,
                )
            try:
                if len(response.content) > 4 * 1024 * 1024:
                    raise ValueError
                message = ExportTraceServiceResponse.FromString(response.content)
                rejected = message.partial_success.rejected_spans
                if not 0 <= rejected <= len(batch):
                    raise ValueError
            except (DecodeError, ValueError):
                return ExportAck(
                    "response_invalid",
                    200,
                    attempts=session.attempts,
                    sdk_result=sdk_result,
                )
            return ExportAck(
                "partial" if rejected else "accepted",
                200,
                rejected,
                session.attempts,
                sdk_result,
            )

        yield send
    finally:
        if exporter is not None:
            exporter.shutdown()
        session.close()
        for logger, disabled in zip(loggers, previous, strict=True):
            logger.disabled = disabled


def deliver(projection, input_digest, settings, *, sender=None):
    """Report receiver acknowledgement separately from backend persistence or task success."""
    if not isinstance(settings, TelemetryConfig) or not settings.endpoint:
        raise ArchiveError(
            "missing_configuration",
            "OTLP endpoint is required for an explicit trace send",
        )
    fields = span_fields(projection)
    spans = restore_spans(fields)
    trace_ids = sorted({f"{item['trace_id']:032x}" for item in fields})
    receipt = {
        "schema_version": 1,
        "projection_id": projection["projection_id"],
        "snapshot_id": projection["snapshot_id"],
        "input_sha256": input_digest,
        "protocol": "http/protobuf",
        "endpoint": settings.endpoint,
        "completeness": projection["completeness"],
        "span_count": len(spans),
        "trace_count": len(trace_ids),
        "trace_ids": trace_ids,
        "acknowledged_spans": 0,
        "rejected_spans": 0,
        "unconfirmed_spans": 0,
        "remaining_spans": len(spans),
        "batches": [],
        "backend_persistence_verified": False,
        "llm_used": False,
        "dependencies": {
            "opentelemetry-exporter-otlp-proto-http": version(
                "opentelemetry-exporter-otlp-proto-http"
            )
        },
        "status": "unavailable",
    }
    if not spans:
        return receipt
    from contextlib import nullcontext

    with nullcontext(sender) if sender is not None else sdk_sender(settings) as send:
        for offset in range(0, len(spans), settings.batch_size):
            batch = spans[offset : offset + settings.batch_size]
            ack = send(batch)
            receipt["batches"].append(
                {
                    "offset": offset,
                    "spans": len(batch),
                    "status": ack.status,
                    "http_status": ack.http_status,
                    "attempts": ack.attempts,
                    "sdk_result": ack.sdk_result,
                }
            )
            receipt["status"] = ack.status
            if ack.status in {"accepted", "partial"}:
                receipt["acknowledged_spans"] += len(batch) - (ack.rejected or 0)
                receipt["rejected_spans"] += ack.rejected or 0
            elif ack.status == "rejected":
                receipt["rejected_spans"] += len(batch)
            elif ack.attempts:
                receipt["unconfirmed_spans"] += len(batch)
            if ack.attempts or ack.status in {"accepted", "partial", "rejected"}:
                receipt["remaining_spans"] -= len(batch)
            if ack.status != "accepted":
                break
    return receipt
