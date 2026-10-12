"""Actual official OTLP exporter against a local protocol fixture; no silent skips."""

import json
import threading
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from trace_projection import records

from engineering_ci.tracing.config import TelemetryConfig
from engineering_ci.tracing.projection import standardize
from engineering_ci.tracing.telemetry import deliver


@contextmanager
def receiver(status=200, *, rejected=0, body=None):
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceResponse,
    )

    received = []
    reply = ExportTraceServiceResponse()
    if rejected:
        reply.partial_success.rejected_spans = rejected
        reply.partial_success.error_message = "SECRET_SERVER_DIAGNOSTIC"

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((payload, dict(self.headers)))
            if status is None:
                self.close_connection = True
                return
            self.send_response(status)
            self.send_header("Content-Type", "application/x-protobuf")
            if status == 307:
                self.send_header("Location", "/credential-trap")
            self.end_headers()
            self.wfile.write(body if body is not None else reply.SerializeToString())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            TelemetryConfig(
                endpoint=f"http://127.0.0.1:{server.server_port}/v1/traces", timeout=1
            ),
            received,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_actual_sdk_protobuf_preserves_identity_time_and_failure_without_credentials(
    monkeypatch,
):
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_HEADERS", "Authorization=SECRET_AMBIENT_TOKEN"
    )
    value = standardize(records(), "a" * 64)
    with receiver() as (settings, received):
        result = deliver(value, "0" * 64, settings)
    assert result["status"] == "accepted" and result["acknowledged_spans"] == 1
    assert (
        not result["backend_persistence_verified"]
        and result["batches"][0]["attempts"] == 1
    )
    assert not any(key.lower() == "authorization" for key in received[0][1])
    raw = received[0][0]
    assert b"SECRET" not in raw and b"PRIVATE" not in raw
    request = ExportTraceServiceRequest.FromString(raw)
    span = request.resource_spans[0].scope_spans[0].spans[0]
    assert int.from_bytes(span.trace_id, "big") == int(
        value["spans"][0]["context"]["trace_id"], 16
    )
    assert (
        span.start_time_unix_nano == 1_000_000_000
        and span.end_time_unix_nano == 2_000_000_000
    )
    assert span.status.code == 2


def test_actual_partial_rejection_is_not_sdk_full_success(capsys):
    value = standardize(records(), "a" * 64)
    with receiver(rejected=1) as (settings, received):
        result = deliver(value, "0" * 64, settings)
    assert len(received) == 1 and result["status"] == "partial"
    assert result["acknowledged_spans"] == 0 and result["rejected_spans"] == 1
    assert result["batches"][0]["sdk_result"] == "success"
    assert "SECRET" not in json.dumps(result) + capsys.readouterr().err


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (401, None, "rejected"),
        (403, None, "rejected"),
        (422, None, "rejected"),
        (307, None, "unconfirmed"),
        (200, b"invalid protobuf", "response_invalid"),
    ],
)
def test_actual_response_classification_and_no_redirect(status, body, expected):
    value = standardize(records(), "a" * 64)
    with receiver(status, body=body) as (settings, received):
        result = deliver(value, "0" * 64, settings)
    assert result["status"] == expected and result["acknowledged_spans"] == 0
    assert len(received) == 1


def test_actual_response_loss_keeps_unconfirmed_and_records_sdk_retries():
    value = standardize(records(), "a" * 64)
    with receiver(None) as (settings, received):
        result = deliver(value, "0" * 64, settings)
    assert result["status"] == "network_error" and result["unconfirmed_spans"] == 1
    assert result["acknowledged_spans"] == 0 and result["batches"][0][
        "attempts"
    ] == len(received)
    assert len(received) >= 1 and not result["backend_persistence_verified"]


def test_actual_tls_error_is_classified_without_leaking_request_content():
    value = standardize(records(), "a" * 64)
    with receiver() as (settings, received):
        result = deliver(
            value,
            "0" * 64,
            replace(settings, endpoint=settings.endpoint.replace("http:", "https:")),
        )
    assert result["status"] == "tls_error" and result["acknowledged_spans"] == 0
    assert not received and "SECRET" not in json.dumps(result)
