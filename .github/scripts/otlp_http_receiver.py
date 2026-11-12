"""Minimal OTLP/HTTP receiver for CI-only Dagger native trace evidence.

It records the spans an OTLP exporter actually pushed and writes them once, at
the end of the run, as an OTLP/JSON ExportTraceServiceRequest body. It is a
verification helper, not a telemetry store: nothing is retained between runs.

Usage:
    python .github/scripts/otlp_http_receiver.py --port 4318 --output capture.json
"""

from __future__ import annotations

import argparse
import base64
import http.server
import json
import signal
import threading
from pathlib import Path

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)

RECEIVED: list[ExportTraceServiceRequest] = []
LOCK = threading.Lock()


def any_value(value) -> dict:
    kind = value.WhichOneof("value")
    if kind == "string_value":
        return {"stringValue": value.string_value}
    if kind == "bool_value":
        return {"boolValue": value.bool_value}
    if kind == "int_value":
        return {"intValue": str(value.int_value)}
    if kind == "double_value":
        return {"doubleValue": value.double_value}
    if kind == "bytes_value":
        return {"bytesValue": base64.b64encode(value.bytes_value).decode()}
    return {}


def attributes(items) -> list[dict]:
    return [{"key": item.key, "value": any_value(item.value)} for item in items]


def span_json(span) -> dict:
    record = {
        "traceId": span.trace_id.hex(),
        "spanId": span.span_id.hex(),
        "name": span.name,
        "kind": span.kind,
        "startTimeUnixNano": str(span.start_time_unix_nano),
        "endTimeUnixNano": str(span.end_time_unix_nano),
        "attributes": attributes(span.attributes),
        "status": {"code": span.status.code},
    }
    if span.parent_span_id:
        record["parentSpanId"] = span.parent_span_id.hex()
    if span.links:
        record["links"] = [
            {
                "traceId": link.trace_id.hex(),
                "spanId": link.span_id.hex(),
                "attributes": attributes(link.attributes),
            }
            for link in span.links
        ]
    return record


def capture_json() -> dict:
    resource_spans = []
    with LOCK:
        requests = list(RECEIVED)
    for request in requests:
        for resource in request.resource_spans:
            resource_spans.append(
                {
                    "resource": {
                        "attributes": attributes(resource.resource.attributes)
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": scope.scope.name},
                            "spans": [span_json(span) for span in scope.spans],
                        }
                        for scope in resource.scope_spans
                    ],
                }
            )
    return {"resourceSpans": resource_spans}


class ReceiverServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, output: Path):
        super().__init__(address, handler)
        self.output = output


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        request = ExportTraceServiceRequest()
        request.ParseFromString(body)
        with LOCK:
            RECEIVED.append(request)
        # Rewrite the capture after every push, so the recorded spans survive
        # an abrupt end of the process.
        write_output(self.server.output)
        payload = ExportTraceServiceResponse().SerializeToString()
        self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        print(f"{self.address_string()} {format % args}", flush=True)


def write_output(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Binary write keeps LF so the recorded digest matches the file bytes.
    path.write_bytes((json.dumps(capture_json(), indent=2) + "\n").encode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=4318)
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help=(
            "Local address to bind. CI passes the Docker bridge gateway so "
            "the Engine container can reach it; loopback stays the default."
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    server = ReceiverServer((args.host, args.port), Handler, args.output)

    def stop(signum, frame):
        # shutdown() blocks until serve_forever() returns, so it cannot run
        # on the thread that is serving.
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"otlp receiver listening on {args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    finally:
        write_output(args.output)
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
