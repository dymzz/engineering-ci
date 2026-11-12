"""Dagger native OTel export/receive path: pinned capabilities and observations.

ECI #87 section A (Execution Graph E slice).

Producer decision:
- Records what the PINNED Dagger version natively offers for OpenTelemetry
  trace export, and what an actual receiver captured for one invocation.
- Never produces spans. It does not instrument Dagger Functions, does not add
  an exporter, a collector or a second telemetry store, and does not prove
  OTLP delivery, Check attribution or trusted admission.
- Exporter configured, sent, receiver observed, source associated and the
  native Check result are five separate facts. None of them implies another,
  and a command exit code is not a send receipt.
- A missing or unassociated trace lowers trace and cache attribution only. An
  independently proven native Check result is kept as it is.

Pinned native capabilities (v1.0.0-beta.16):
- Engine workload export: the engine config file /etc/dagger/engine.json with
  telemetry.workloadExport, plus the standard OTLP environment variables in
  the engine environment.
- Engine span attributes carry the Check identity (dagger.io/check.name,
  dagger.io/check.passed) and the cache outcome
  (dagger.io/cache.contract, dagger.io/cache.outcome).
- The generated SDK exports module-side client spans over OTLP/HTTP and
  propagates a W3C traceparent into the engine's GraphQL requests.

Correlation (ECI #87 A.1):
- The native correlation key is the propagated traceparent plus the engine's
  own Check attribute. A shared trace id is not a correlation: a capture
  binds only when a captured span carries the propagated parent span id.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ._paths import ROOT

SCHEMA_VERSION = 1
REPORT_KIND = "engineering-ci-dagger-telemetry"
EVIDENCE_CLASS = "candidate_record"
# Evidence label, not an import path. Mirrors the #75 producer convention.
OTLP_SCOPE_NAME = "eci_core.dagger_telemetry"

# Native environment contract of the pinned SDK (.dagger/sdk/src/dagger/
# telemetry.py). These names are read by Dagger itself; ECI never installs a
# span processor or an exporter of its own.
ENV_OTLP_ENDPOINT = "OTEL_EXPORTER_OTLP_ENDPOINT"
ENV_OTLP_TRACES_ENDPOINT = "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"
ENV_OTLP_PROTOCOL = "OTEL_EXPORTER_OTLP_PROTOCOL"
ENV_OTLP_TRACES_PROTOCOL = "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL"
ENV_SDK_DISABLED = "OTEL_SDK_DISABLED"
ENV_TRACES_LIVE = "OTEL_EXPORTER_OTLP_TRACES_LIVE"
ENV_SERVICE_NAME = "OTEL_SERVICE_NAME"
ENV_TRACEPARENT = "TRACEPARENT"

# Native environment contract of the engine workload export at v1.0.0-beta.16
# (engine/telemetry/workload_export.go). The standard OTLP variables decide
# the endpoint and the protocol; the engine config file turns the export on.
ENGINE_CONFIG_PATH = "/etc/dagger/engine.json"
ENGINE_WORKLOAD_EXPORT_KEY = "workloadExport"
ENGINE_PROTOCOL_GRPC = "grpc"
ENGINE_PROTOCOL_HTTP = "http/protobuf"

# The pinned SDK depends on opentelemetry-exporter-otlp-proto-http, so the
# module-side transport is OTLP/HTTP with protobuf encoding.
# OTEL_EXPORTER_OTLP_PROTOCOL has no effect on the pinned SDK: the exporter
# class is fixed by the dependency, not selected by that variable.
PINNED_PROTOCOL = "http/protobuf"
# The pinned SDK initialises traces exporters only; metrics and logs are
# skipped on purpose (.dagger/sdk/src/dagger/telemetry.py::_initialize).
PINNED_SIGNALS = ("traces",)
PINNED_SERVICE_NAME = "dagger-python-sdk"
# Native SDK default endpoint when no OTLP endpoint variable is set.
SDK_DEFAULT_OTLP_ENDPOINT = "http://localhost:4318"

# The engine workload export carries spans and workload readings; it never
# exports logs.
ENGINE_SIGNALS = ("traces", "metrics")

# Engine span attributes at v1.0.0-beta.16 (dagger/otel-go attrs.go,
# engine/telemetryattrs/attrs.go). They are read from the captured spans.
ATTR_CHECK_NAME = "dagger.io/check.name"
ATTR_CHECK_PASSED = "dagger.io/check.passed"
ATTR_CACHE_CONTRACT = "dagger.io/cache.contract"
ATTR_CACHE_OUTCOME = "dagger.io/cache.outcome"
ATTR_CACHE_HIT_ROUTE = "dagger.io/cache.hit.route"
ATTR_MODULE_FUNCTION_NAME = "dagger.io/module.function.name"
ATTR_CACHED = "dagger.io/dag.cached"
ATTR_SESSION_ID = "dagger.io/session.id"
ATTR_ENGINE_INSTANCE_ID = "dagger.io/engine.instance.id"
ATTR_SERVICE_NAME = "service.name"

# The five separate facts of ECI #87 A.2. None of them covers another.
CLAIM_EXPORTER_CONFIGURED = "exporter_configured"
CLAIM_SENT = "sent"
CLAIM_RECEIVER_OBSERVED = "receiver_observed"
CLAIM_SOURCE_ASSOCIATED = "source_associated"
CLAIM_NATIVE_CHECK_RESULT = "native_check_result"

SENT_SENT = "sent"
SENT_NOT_SENT = "not_sent"
SENT_UNKNOWN = "unknown"

BINDING_BOUND = "bound"
BINDING_UNBOUND = "unbound"
BINDING_AMBIGUOUS = "ambiguous"

STATUS_PRODUCED = "produced"
STATUS_PARTIAL = "partial"
STATUS_INCOMPLETE = "incomplete"

# Correlation bases a real native propagation can prove.
BASIS_TRACEPARENT = "w3c_traceparent_propagation"
BASIS_CHECK_ATTRIBUTE = "engine_check_attribute"
BASIS_CHECK_ATTRIBUTE_IN_TRACE = "engine_check_attribute_in_propagated_trace"
BASIS_NONE = "none"

# Stable reasons.
REASON_NO_CAPTURE = "no_capture"
REASON_CAPTURE_EMPTY = "capture_empty"
REASON_NO_CORRELATION_KEY = "no_correlation_key"
REASON_NO_CORRELATED_SPAN = "no_correlated_span"
REASON_TRACE_ID_ONLY = "trace_id_match_without_propagated_parent"
REASON_CHECK_NAME_NOT_OBSERVED = "check_name_not_observed"
REASON_SESSION_NOT_OBSERVED = "engine_session_not_observed"
REASON_CHECK_NAME_OUTSIDE_TRACE = "check_name_outside_propagated_trace"
REASON_REQUIRED_OBSERVABILITY_UNMET = "required_observability_unmet"
REASON_EXPORTER_ERROR = "exporter_reported_failure"


class DaggerTelemetryError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Capability:
    """One pinned-version capability fact with its evidence status."""

    key: str
    status: str
    detail: str
    source: str


@dataclass(frozen=True)
class ExporterScope:
    """One native exporter scope: who exports, where, with which signal."""

    scope: str
    configured: bool
    endpoint: str | None
    endpoint_kind: str
    protocol: str
    signals: tuple[str, ...]
    service_name: str
    source: str
    detail: dict = field(default_factory=dict)

    def to_json(self):
        payload = asdict(self)
        payload["signals"] = list(self.signals)
        return payload


@dataclass(frozen=True)
class Capture:
    """Spans actually captured by a receiver, as read from an OTLP/JSON file."""

    path: str
    sha256: str
    span_count: int
    trace_ids: tuple[str, ...]
    span_ids: tuple[str, ...]
    span_names: tuple[str, ...]
    parent_span_ids: tuple[str, ...]
    scopes: tuple[str, ...]
    check_names: tuple[str, ...]
    cache_outcomes: tuple[str, ...]
    session_ids: tuple[str, ...]
    engine_instance_ids: tuple[str, ...]
    service_names: tuple[str, ...]
    spans: tuple[dict, ...] = field(default=(), repr=False)


def pinned_engine_version(root: Path | None = None) -> str | None:
    """Read the engine version the repository actually pins."""
    root = ROOT if root is None else root
    manifest = root / "dagger-module.toml"
    try:
        text = manifest.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "engineVersion":
            return value.strip().strip('"').strip("'")
    return None


def pinned_sdk_version(root: Path | None = None) -> str | None:
    """Read the SDK version marker the pinned SDK actually carries.

    The generated SDK in this repository carries no version marker, so this
    returns None instead of a guessed version.
    """
    root = ROOT if root is None else root
    marker = root / ".dagger" / "sdk" / "src" / "dagger" / "client" / "gen.py"
    try:
        text = marker.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("version:"):
            value = stripped.split(":", 1)[1].strip().strip('"').strip("'")
            return value or None
    return None


def capability_inventory() -> tuple[Capability, ...]:
    """Native capabilities of the pinned Dagger version.

    Every row is bound to a pinned source. A capability the pinned sources do
    not prove is recorded as unknown instead of being inferred from newer
    Dagger documentation.
    """
    return (
        Capability(
            key="engine_version_pinned",
            status="available",
            detail=(
                "dagger-module.toml pins engineVersion; CI derives the CLI "
                "version from it and starts registry.dagger.io/engine with it."
            ),
            source=(
                "dagger-module.toml:.github/workflows/ci.yml"
                ":.github/workflows/reusable-python-ci.yml"
            ),
        ),
        Capability(
            key="sdk_version_marker",
            status="not_available",
            detail=(
                "The generated SDK carries no version marker; its version is "
                "the engine that generated it."
            ),
            source=".dagger/sdk/src/dagger/client/gen.py",
        ),
        Capability(
            key="engine_workload_export",
            status="available",
            detail=(
                "The engine exports its own spans when its config file sets "
                "telemetry.workloadExport and the standard OTLP environment "
                "names an endpoint. Every signal the export carries needs an "
                "endpoint of its own, and a missing one is fatal at engine "
                "startup rather than a disabled export."
            ),
            source=(
                "engine/config/config.go::TelemetryConfig"
                ":engine/telemetry/workload_export.go::NewWorkloadExport"
                ":cmd/engine/main.go"
            ),
        ),
        Capability(
            key="engine_export_protocol",
            status="available",
            detail=(
                "The workload export speaks OTLP over grpc or http/protobuf, "
                "selected by OTEL_EXPORTER_OTLP_TRACES_PROTOCOL or "
                "OTEL_EXPORTER_OTLP_PROTOCOL."
            ),
            source="engine/telemetry/workload_export.go::workloadExportProtocol",
        ),
        Capability(
            key="engine_export_signals",
            status="available",
            detail=(
                "The workload export carries spans and workload readings. It "
                "never exports logs."
            ),
            source="engine/telemetry/workload_export.go",
        ),
        Capability(
            key="engine_export_durability",
            status="partial",
            detail=(
                "Receiver acceptance is not an engine-side durability "
                "guarantee; the export uses bounded queues and a batch "
                "processor."
            ),
            source="engine/telemetry/workload_export.go::WorkloadExport",
        ),
        Capability(
            key="engine_check_attribute",
            status="available",
            detail=(
                "Engine spans carry the Check identity: dagger.io/check.name "
                "and dagger.io/check.passed."
            ),
            source="dagger/otel-go attrs.go::CheckNameAttr",
        ),
        Capability(
            key="engine_cache_observation",
            status="available",
            detail=(
                "Engine spans carry the cache contract and outcome: "
                "dagger.io/cache.contract, dagger.io/cache.outcome "
                "(hit/executed/joined/uncached) and dagger.io/cache.hit.route."
            ),
            source="engine/telemetryattrs/attrs.go",
        ),
        Capability(
            key="sdk_module_telemetry",
            status="available",
            detail=(
                "The pinned generated SDK configures OpenTelemetry from OTEL_* "
                "environment variables and exports traces only."
            ),
            source=".dagger/sdk/src/dagger/telemetry.py",
        ),
        Capability(
            key="sdk_trace_exporter_transport",
            status="available",
            detail=(
                "The pinned SDK depends on opentelemetry-exporter-otlp-proto-http, "
                "so native trace export is OTLP/HTTP with protobuf encoding."
            ),
            source=".dagger/sdk/pyproject.toml",
        ),
        Capability(
            key="sdk_metric_and_log_export",
            status="not_available",
            detail=(
                "The pinned SDK initialises traces exporters only; metrics and "
                "logs are skipped on purpose."
            ),
            source=".dagger/sdk/src/dagger/telemetry.py::_initialize",
        ),
        Capability(
            key="sdk_context_propagation",
            status="available",
            detail=(
                "The pinned SDK reads TRACEPARENT, attaches that context and "
                "injects traceparent into its GraphQL requests."
            ),
            source=(
                ".dagger/sdk/src/dagger/telemetry.py::_DaggerPropagationConfigurator"
                ":.dagger/sdk/src/dagger/client/_session.py::TelemetryTransport"
            ),
        ),
        Capability(
            key="sdk_live_span_export",
            status="available",
            detail=(
                "OTEL_EXPORTER_OTLP_TRACES_LIVE switches the pinned SDK to a span "
                "processor that exports on start instead of on end."
            ),
            source=".dagger/sdk/src/dagger/telemetry.py::LiveSpanProcessor",
        ),
    )


def _endpoint_and_kind(environ, explicit=None):
    if explicit:
        return explicit, "explicit"
    value = environ.get(ENV_OTLP_TRACES_ENDPOINT) or environ.get(ENV_OTLP_ENDPOINT)
    if value:
        return value, "explicit"
    if any(key.startswith("OTEL_") for key in environ):
        return SDK_DEFAULT_OTLP_ENDPOINT, "sdk_default"
    return None, "none"


def read_sdk_scope(environ=None) -> ExporterScope:
    """Read the module-side SDK exporter configuration from the environment."""
    environ = os.environ if environ is None else environ
    disabled = environ.get(ENV_SDK_DISABLED, "").strip().lower() == "true"
    configured = any(key.startswith("OTEL_") for key in environ)
    endpoint, endpoint_kind = _endpoint_and_kind(environ)
    return ExporterScope(
        scope="sdk_module_client",
        configured=bool(configured and not disabled),
        endpoint=endpoint,
        endpoint_kind=endpoint_kind,
        protocol=PINNED_PROTOCOL,
        signals=PINNED_SIGNALS,
        service_name=environ.get(ENV_SERVICE_NAME) or PINNED_SERVICE_NAME,
        source="process_environment",
        detail={
            "sdk_disabled": disabled,
            "live_export": ENV_TRACES_LIVE in environ,
            "protocol_variable_ignored": ENV_OTLP_PROTOCOL in environ,
            "traceparent": environ.get(ENV_TRACEPARENT),
        },
    )


def read_engine_config(path) -> dict | None:
    """Read the engine config file that enables the native workload export."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(payload, dict):
        return None
    telemetry = payload.get("telemetry")
    if not isinstance(telemetry, dict):
        return None
    return telemetry


def read_engine_scope(
    engine_config=None, endpoint=None, protocol=None, environ=None
) -> ExporterScope:
    """Read the engine workload export configuration.

    The engine needs its config file to enable telemetry.workloadExport and
    an OTLP endpoint in its environment. Neither is a fact about the host
    process unless the caller supplies the config that was applied.
    """
    environ = os.environ if environ is None else environ
    telemetry = read_engine_config(engine_config) if engine_config else None
    workload_export = bool((telemetry or {}).get(ENGINE_WORKLOAD_EXPORT_KEY))
    resolved_endpoint, endpoint_kind = _endpoint_and_kind(environ, endpoint)
    resolved_protocol = (
        protocol
        or environ.get(ENV_OTLP_TRACES_PROTOCOL)
        or environ.get(ENV_OTLP_PROTOCOL)
        or ENGINE_PROTOCOL_HTTP
    )
    return ExporterScope(
        scope="engine_workload",
        configured=bool(workload_export and resolved_endpoint),
        endpoint=resolved_endpoint,
        endpoint_kind=endpoint_kind,
        protocol=resolved_protocol,
        signals=ENGINE_SIGNALS,
        service_name="dagger-engine",
        source="engine_config" if engine_config else "unknown",
        detail={
            "config_path": str(engine_config) if engine_config else ENGINE_CONFIG_PATH,
            "config_observed": engine_config is not None,
            "workload_export_enabled": workload_export,
            "endpoint_required": True,
            "logs_exported": False,
        },
    )


def parse_traceparent(value: str | None) -> tuple[str, str] | None:
    """Parse a W3C traceparent into (trace_id, parent_span_id)."""
    if not value:
        return None
    parts = value.strip().split("-")
    if len(parts) < 4:
        return None
    version, trace_id, span_id = parts[0], parts[1], parts[2]
    if version == "00" and (trace_id == "0" * 32 or span_id == "0" * 16):
        return None
    if len(trace_id) != 32 or len(span_id) != 16:
        return None
    try:
        int(trace_id, 16)
        int(span_id, 16)
    except ValueError:
        return None
    return trace_id, span_id


def _attribute_map(span):
    values = {}
    for attribute in span.get("attributes", []) or []:
        key = attribute.get("key")
        value = attribute.get("value") or {}
        if not key:
            continue
        if "stringValue" in value:
            values[key] = value["stringValue"]
        elif "boolValue" in value:
            values[key] = value["boolValue"]
        elif "intValue" in value:
            values[key] = value["intValue"]
    return values


def _capture_spans(payload):
    spans = []
    resources = []
    for resource in payload.get("resourceSpans", []) or []:
        resources.append(_attribute_map(resource.get("resource") or {}))
        for scope in resource.get("scopeSpans", []) or []:
            scope_name = (scope.get("scope") or {}).get("name") or ""
            for span in scope.get("spans", []) or []:
                attributes = _attribute_map(span)
                spans.append(
                    {
                        "trace_id": span.get("traceId") or "",
                        "span_id": span.get("spanId") or "",
                        "parent_span_id": span.get("parentSpanId") or "",
                        "name": span.get("name") or "",
                        "scope": scope_name,
                        "check_name": attributes.get(ATTR_CHECK_NAME, ""),
                        "check_passed": attributes.get(ATTR_CHECK_PASSED),
                        "cache_outcome": attributes.get(ATTR_CACHE_OUTCOME, ""),
                        "cache_contract": attributes.get(ATTR_CACHE_CONTRACT, ""),
                        "module_function_name": attributes.get(
                            ATTR_MODULE_FUNCTION_NAME, ""
                        ),
                        "cached": attributes.get(ATTR_CACHED),
                    }
                )
    return spans, resources


def read_capture(path) -> Capture:
    """Read an OTLP/JSON capture produced by an actual receiver."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise DaggerTelemetryError("capture_unreadable") from error
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as error:
        raise DaggerTelemetryError("capture_invalid") from error
    if not isinstance(payload, dict):
        raise DaggerTelemetryError("capture_invalid")
    spans, resources = _capture_spans(payload)
    return Capture(
        path=str(path),
        sha256=hashlib.sha256(raw).hexdigest(),
        span_count=len(spans),
        trace_ids=tuple(
            sorted({span["trace_id"] for span in spans if span["trace_id"]})
        ),
        span_ids=tuple(span["span_id"] for span in spans if span["span_id"]),
        span_names=tuple(span["name"] for span in spans if span["name"]),
        parent_span_ids=tuple(
            span["parent_span_id"] for span in spans if span["parent_span_id"]
        ),
        scopes=tuple(sorted({span["scope"] for span in spans if span["scope"]})),
        check_names=tuple(
            sorted({span["check_name"] for span in spans if span["check_name"]})
        ),
        cache_outcomes=tuple(
            sorted({span["cache_outcome"] for span in spans if span["cache_outcome"]})
        ),
        session_ids=tuple(
            sorted(
                {
                    resource[ATTR_SESSION_ID]
                    for resource in resources
                    if resource.get(ATTR_SESSION_ID)
                }
            )
        ),
        engine_instance_ids=tuple(
            sorted(
                {
                    resource[ATTR_ENGINE_INSTANCE_ID]
                    for resource in resources
                    if resource.get(ATTR_ENGINE_INSTANCE_ID)
                }
            )
        ),
        service_names=tuple(
            sorted(
                {
                    resource[ATTR_SERVICE_NAME]
                    for resource in resources
                    if resource.get(ATTR_SERVICE_NAME)
                }
            )
        ),
        spans=tuple(spans),
    )


def _digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_native_result(path):
    """Read an independently produced native Check result."""
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as error:
        raise DaggerTelemetryError("native_result_unreadable") from error
    if not isinstance(payload, dict):
        raise DaggerTelemetryError("native_result_invalid")
    return payload, {
        "path": str(path),
        "sha256": _digest_file(path),
        "source": "native_result",
    }


def read_exporter_error(path):
    """Read an exporter-side failure record, the only proof of a failed send."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise DaggerTelemetryError("exporter_error_unreadable") from error
    if not raw.strip():
        raise DaggerTelemetryError("exporter_error_empty")
    return {
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "source": "exporter_error",
    }


def _bind_capture(capture, parsed, check_name):
    """Bind a capture to one invocation.

    Returns (status, reason, basis, bound_spans). The rules are deliberately
    strict: a shared trace id is never a correlation on its own, and a
    declared Check name is only accepted when the engine's own Check
    attribute confirms it.
    """
    if parsed is None and not check_name:
        return BINDING_UNBOUND, REASON_NO_CORRELATION_KEY, BASIS_NONE, []

    propagated = []
    if parsed is not None:
        trace_id, parent_span_id = parsed
        propagated = [
            span
            for span in capture.spans
            if span["trace_id"] == trace_id
            and (
                span["parent_span_id"] == parent_span_id
                or span["span_id"] == parent_span_id
            )
        ]

    named = []
    if check_name:
        named = [span for span in capture.spans if span["check_name"] == check_name]

    if check_name and not named:
        # The engine did not report the declared Check in this capture.
        return BINDING_UNBOUND, REASON_CHECK_NAME_NOT_OBSERVED, BASIS_NONE, []

    if check_name and not capture.session_ids:
        # The Check attribute alone is not a same-source reference: the
        # capture carries no engine session to bind it to.
        return BINDING_UNBOUND, REASON_SESSION_NOT_OBSERVED, BASIS_NONE, []

    if check_name and parsed is not None:
        trace_id, parent_span_id = parsed
        in_trace = [span for span in named if span["trace_id"] == trace_id]
        if not in_trace:
            return (
                BINDING_AMBIGUOUS,
                REASON_CHECK_NAME_OUTSIDE_TRACE,
                BASIS_NONE,
                [],
            )
        if any(
            span["parent_span_id"] == parent_span_id
            or span["span_id"] == parent_span_id
            for span in in_trace
        ):
            return BINDING_BOUND, None, BASIS_TRACEPARENT, in_trace
        return BINDING_BOUND, None, BASIS_CHECK_ATTRIBUTE_IN_TRACE, in_trace

    if check_name:
        return BINDING_BOUND, None, BASIS_CHECK_ATTRIBUTE, named

    if propagated:
        return BINDING_BOUND, None, BASIS_TRACEPARENT, propagated
    if parsed is not None and any(
        span["trace_id"] == parsed[0] for span in capture.spans
    ):
        return BINDING_AMBIGUOUS, REASON_TRACE_ID_ONLY, BASIS_NONE, []
    return BINDING_UNBOUND, REASON_NO_CORRELATED_SPAN, BASIS_NONE, []


def build_receipt(
    *,
    sdk_scope: ExporterScope | None = None,
    engine_scope: ExporterScope | None = None,
    capture: Capture | None = None,
    exporter_error=None,
    native_result=None,
    native_result_ref=None,
    correlation=None,
    invocation_ref: str | None = None,
    source_binding_ref: str | None = None,
    check_ref: str | None = None,
    check_name: str | None = None,
    capture_ref: str | None = None,
    required_observability: bool = False,
    root: Path | None = None,
):
    """Bind one invocation, its native result and what the receiver captured.

    Refusal rules:
    - No capture means the exporter may have sent anything. `sent` stays
      unknown and `receiver_observed` stays false.
    - A shared trace id is not a correlation. A capture binds on the
      propagated parent span id, or on the engine's own Check attribute.
    - A native Check result is kept whatever the trace state is.
    - When the current Required set demands observability attributes, an
      unobserved or unbound trace keeps the coverage partial instead of
      reporting a complete observation.
    """
    sdk_scope = sdk_scope if sdk_scope is not None else read_sdk_scope({})
    engine_scope = engine_scope if engine_scope is not None else read_engine_scope()
    traceparent = correlation
    parsed = parse_traceparent(traceparent)
    basis = BASIS_NONE
    bound_spans: list[dict] = []
    reason: str | None = None
    binding_status = BINDING_UNBOUND
    sent = SENT_UNKNOWN
    sent_basis: str | None = None

    if exporter_error is not None:
        sent = SENT_NOT_SENT
        sent_basis = REASON_EXPORTER_ERROR
        reason = REASON_EXPORTER_ERROR
    if capture is None:
        receiver_observed = False
        if sent != SENT_NOT_SENT:
            reason = REASON_NO_CAPTURE
    elif capture.span_count == 0:
        receiver_observed = False
        if sent != SENT_NOT_SENT:
            reason = REASON_CAPTURE_EMPTY
    else:
        receiver_observed = True
        if sent != SENT_NOT_SENT:
            sent = SENT_SENT
            sent_basis = "receiver_observation"
        binding_status, reason, basis, bound_spans = _bind_capture(
            capture, parsed, check_name
        )

    source_associated = binding_status == BINDING_BOUND
    if required_observability and not source_associated and reason is None:
        # The current Required set asks for observability attributes. The
        # coverage stays partial; a native result alone does not close it.
        reason = REASON_REQUIRED_OBSERVABILITY_UNMET

    claims = {
        CLAIM_EXPORTER_CONFIGURED: bool(
            sdk_scope.configured or engine_scope.configured
        ),
        CLAIM_SENT: sent,
        CLAIM_RECEIVER_OBSERVED: receiver_observed,
        CLAIM_SOURCE_ASSOCIATED: source_associated,
        CLAIM_NATIVE_CHECK_RESULT: native_result is not None,
    }

    if binding_status == BINDING_BOUND and not required_observability:
        status = STATUS_PRODUCED
    elif capture is not None or native_result is not None or exporter_error is not None:
        status = STATUS_PARTIAL
    else:
        status = STATUS_INCOMPLETE

    root = ROOT if root is None else root
    return {
        "kind": REPORT_KIND,
        "schema_version": SCHEMA_VERSION,
        "evidence_class": EVIDENCE_CLASS,
        "status": status,
        "reason": reason,
        "producer": {
            "name": OTLP_SCOPE_NAME,
            "decision": (
                "Record the pinned Dagger native OTel export/receive path for "
                "one invocation. No spans are produced here."
            ),
        },
        "source": "dagger_native_otel",
        "pinned": {
            "engine_version": pinned_engine_version(root),
            "sdk_version": pinned_sdk_version(root),
            "sdk_source": ".dagger/sdk/src/dagger/telemetry.py",
            "sdk_dependency": "dagger-io (local path .dagger/sdk, editable)",
        },
        "capabilities": [asdict(capability) for capability in capability_inventory()],
        "exporter": {
            "sdk": sdk_scope.to_json(),
            "engine": engine_scope.to_json(),
        },
        "claims": claims,
        "sent_basis": sent_basis,
        "binding": {
            "status": binding_status,
            "reason": reason,
            "basis": basis,
            "correlation_key": traceparent,
            "declared_check_name": check_name,
            "session_ids": list(capture.session_ids) if capture else [],
            "engine_instance_ids": (
                list(capture.engine_instance_ids) if capture else []
            ),
        },
        "invocation": {
            "actual_invocation_ref": invocation_ref,
            "source_binding_ref": source_binding_ref,
            "check_or_function_ref": check_ref,
        },
        "native_check_result": native_result,
        "native_result_ref": native_result_ref,
        "raw_trace_ref": (
            {
                "path": capture.path,
                "sha256": capture.sha256,
                "receiver": capture_ref,
            }
            if capture is not None
            else None
        ),
        "exporter_error_ref": exporter_error,
        "observed_trace_span_ids": [span["span_id"] for span in bound_spans],
        "required_observability": {
            "declared": bool(required_observability),
            "satisfied": bool(source_associated),
        },
        "coverage": {
            "signal_scope": list(PINNED_SIGNALS),
            "spans_observed": capture.span_count if capture else 0,
            "spans_bound": len(bound_spans),
            "trace_ids": list(capture.trace_ids) if capture else [],
            "span_names": sorted({span["name"] for span in bound_spans}),
            "scopes": list(capture.scopes) if capture else [],
            "check_names": list(capture.check_names) if capture else [],
            "cache_outcomes": list(capture.cache_outcomes) if capture else [],
            "session_ids": list(capture.session_ids) if capture else [],
            "engine_instance_ids": list(capture.engine_instance_ids) if capture else [],
            "service_names": list(capture.service_names) if capture else [],
            "unobserved_reason": None if receiver_observed else reason,
        },
    }


def write_receipt(receipt, path):
    """Write the receipt artifact. Binary write keeps LF so digests match."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
    )
    return path


def _print(value):
    sys.stdout.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="engineering-ci dagger-telemetry",
        description=(
            "Record the pinned Dagger native OTel export/receive path for one "
            "invocation: exporter configuration, what a receiver actually "
            "captured, and the independent native Check result. Does not "
            "produce spans or prove delivery (ECI #87 A)."
        ),
    )
    parser.add_argument(
        "--capture",
        type=Path,
        help="OTLP/JSON file with the spans an actual receiver captured",
    )
    parser.add_argument(
        "--capture-ref",
        help="Label of the receiver configuration that produced the capture",
    )
    parser.add_argument(
        "--native-result",
        type=Path,
        help="JSON file with the independent native Check result",
    )
    parser.add_argument(
        "--exporter-error",
        type=Path,
        help="Exporter-side failure record; the only proof that a send failed",
    )
    parser.add_argument(
        "--traceparent",
        help=(
            "W3C traceparent propagated into the invocation; the native "
            "correlation key. Defaults to the TRACEPARENT environment variable"
        ),
    )
    parser.add_argument(
        "--engine-config",
        type=Path,
        help="Engine config file that was applied, with telemetry.workloadExport",
    )
    parser.add_argument(
        "--engine-endpoint",
        help="OTLP endpoint given to the engine environment",
    )
    parser.add_argument(
        "--engine-protocol",
        choices=[ENGINE_PROTOCOL_GRPC, ENGINE_PROTOCOL_HTTP],
        help="OTLP protocol given to the engine environment",
    )
    parser.add_argument(
        "--check-name",
        help=(
            "Dagger Check name the invocation ran; confirmed only when a "
            "captured span carries the engine's own Check attribute"
        ),
    )
    parser.add_argument(
        "--invocation-ref", help="Reference of the actual invocation that ran"
    )
    parser.add_argument(
        "--source-binding-ref", help="Reference of the bound source snapshot"
    )
    parser.add_argument(
        "--check-ref", help="Reference of the observed Check or Function"
    )
    parser.add_argument(
        "--required-observability",
        action="store_true",
        help=(
            "The current Required set demands observability attributes; an "
            "unobserved or unbound trace keeps the coverage partial"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dagger-telemetry.json"),
        help="Artifact path for the observation receipt",
    )
    parser.add_argument("--json", action="store_true", help="Print the receipt")
    args = parser.parse_args(argv)

    sdk_scope = read_sdk_scope()
    engine_scope = read_engine_scope(
        engine_config=args.engine_config,
        endpoint=args.engine_endpoint,
        protocol=args.engine_protocol,
    )
    try:
        capture = read_capture(args.capture) if args.capture else None
        exporter_error = (
            read_exporter_error(args.exporter_error) if args.exporter_error else None
        )
        native_result = None
        native_result_ref = None
        if args.native_result:
            native_result, native_result_ref = read_native_result(args.native_result)
    except DaggerTelemetryError as error:
        parser.exit(2, f"dagger-telemetry rejected: {error.code}\n")

    correlation = args.traceparent or sdk_scope.detail.get("traceparent")
    receipt = build_receipt(
        sdk_scope=sdk_scope,
        engine_scope=engine_scope,
        capture=capture,
        exporter_error=exporter_error,
        native_result=native_result,
        native_result_ref=native_result_ref,
        correlation=correlation,
        invocation_ref=args.invocation_ref,
        source_binding_ref=args.source_binding_ref,
        check_ref=args.check_ref,
        check_name=args.check_name,
        capture_ref=args.capture_ref,
        required_observability=args.required_observability,
    )
    try:
        write_receipt(receipt, args.output)
    except OSError:
        parser.exit(2, "dagger-telemetry rejected: artifact_flush_failed\n")

    if args.json:
        _print(receipt)
    else:
        print(f"status: {receipt['status']}")
        print(f"evidence_class: {receipt['evidence_class']}")
        print(f"reason: {receipt['reason'] or 'ok'}")
        print(f"exporter_configured: {receipt['claims'][CLAIM_EXPORTER_CONFIGURED]}")
        print(f"sent: {receipt['claims'][CLAIM_SENT]}")
        print(f"receiver_observed: {receipt['claims'][CLAIM_RECEIVER_OBSERVED]}")
        print(f"source_associated: {receipt['claims'][CLAIM_SOURCE_ASSOCIATED]}")
        print(f"native_check_result: {receipt['claims'][CLAIM_NATIVE_CHECK_RESULT]}")
        print(f"binding_status: {receipt['binding']['status']}")
        print(f"artifact: {args.output}")
    if receipt["status"] in {STATUS_PRODUCED, STATUS_PARTIAL}:
        return 0
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
