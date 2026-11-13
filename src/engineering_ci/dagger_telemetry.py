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
# The cache entry a call resolved to. It names one entry of one cache for the
# cache's lifetime, so two invocations that share it share one origin.
ATTR_CACHE_RESULT_ID = "dagger.io/cache.result.id"
# The content identity the engine resolved for a call. It is the native,
# verifiable source binding of what the call consumed.
ATTR_DAG_DIGEST = "dagger.io/dag.digest"
ATTR_CONTENT_PREFERRED_DIGEST = "dagger.io/dag.content_preferred_digest"

# The cache outcome the engine recorded for a call.
CACHE_OUTCOME_EXECUTED = "executed"
CACHE_OUTCOME_HIT = "hit"
CACHE_OUTCOME_JOINED = "joined"
CACHE_OUTCOME_UNCACHED = "uncached"

# Origin execution states (ECI #87 review: a missing new tool execution is
# not the same as an unusable origin reference).
ORIGIN_CURRENT_EXECUTION = "current_execution"
ORIGIN_EXISTING_EXECUTION = "existing_execution"
ORIGIN_UNKNOWN = "unknown"

# What a bound trace proves.
PROVES_INVOCATION = "invocation_call_and_cache_outcome"
PROVES_ORIGIN_EXECUTION = "origin_tool_execution"

# Cache observation vocabulary (design/execution-graph/evidence-and-identity.md
# D-06). The value always comes with the range it was observed on.
CACHE_SCOPE_OBSERVED_HIT = "observed_hit"
CACHE_SCOPE_OBSERVED_EXECUTION = "observed_execution"
CACHE_SCOPE_MIXED = "mixed"
CACHE_SCOPE_UNKNOWN = "unknown"
CACHE_SCOPE_NOT_APPLICABLE = "not_applicable"

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
BASIS_CHECK_SELECTOR = "engine_check_selector"
BASIS_FUNCTION_CALL = "engine_function_call"
BASIS_CHECK_IN_TRACE = "engine_check_in_propagated_trace"
BASIS_NONE = "none"

# The native keys that can carry a Check identity in the pinned version. The
# observed run identified the Check through the Span names; the attribute
# exists in the pinned sources but did not appear in that run.
KEY_CHECK_ATTRIBUTE = "check_attribute"
KEY_CHECK_SELECTOR = "check_selector"
KEY_FUNCTION_CALL = "function_call"
KEY_TRACEPARENT = "traceparent"
# The engine names the Check it selected as a dagql selector.
CHECK_SELECTOR_PREFIX = "dag://?check="

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
REASON_PARENT_CHAIN = "native_parent_chain_not_observed"
REASON_INVOCATION_AMBIGUOUS = "native_invocation_ambiguous"
REASON_CALLER_SCOPE = "caller_capture_scope_not_proven"
REASON_SPAN_CONFLICT = "native_span_identity_conflict"
REASON_NATIVE_RESULT_MISMATCH = "native_result_invocation_mismatch"


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
                "processor with a one minute export timeout."
            ),
            source="engine/telemetry/workload_export.go::WorkloadExport",
        ),
        Capability(
            key="engine_export_flush",
            status="available",
            detail=(
                "The Engine drains the workload export itself: the session "
                "providers finish first, then the export is shut down with a "
                "30 second flush budget. An incomplete shutdown is only a "
                "warning."
            ),
            source="cmd/engine/main.go:engine/telemetry/workload_export.go",
        ),
        Capability(
            key="sdk_export_flush",
            status="available",
            detail=(
                "The module entrypoint force-flushes and shuts down the SDK "
                "tracer provider when the module process ends."
            ),
            source=(
                ".dagger/sdk/src/dagger/mod/cli.py::app"
                ":.dagger/sdk/src/dagger/telemetry.py::shutdown"
            ),
        ),
        Capability(
            key="engine_check_attribute",
            status="partial",
            detail=(
                "The pinned sources set dagger.io/check.name and "
                "dagger.io/check.passed on the Check span, but the observed "
                "v1.0.0-beta.16 run emitted neither. The Check identity was "
                "carried by the Span names instead."
            ),
            source=(
                "dagger/otel-go attrs.go::CheckNameAttr:core/checks.go"
                ":unresolved:engine_check_attribute_observed"
            ),
        ),
        Capability(
            key="engine_function_call_name",
            status="available",
            detail=(
                "The engine names a module function call in camel case "
                "(engineering-ci:EngineeringCi.repositoryRuffTools) while the "
                "CLI spells the same entry in kebab case "
                "(repository-ruff-tools); the Check selector keeps the raw "
                "Check name (dag://?check=lint)."
            ),
            source="observed:v1.0.0-beta.16-native-capture",
        ),
        Capability(
            key="engine_check_identity_by_span_name",
            status="available",
            detail=(
                "The observed run identified the Check through the Span "
                "names: the CLI selector dag://?check=<name>, the module "
                "function call <module>:<object>.<function> and the call's "
                "core span <object>.<function>."
            ),
            source="observed:v1.0.0-beta.16-native-capture",
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
        resource_attrs = _attribute_map(resource.get("resource") or {})
        resources.append(resource_attrs)
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
                        "session_id": resource_attrs.get(ATTR_SESSION_ID, ""),
                        "engine_instance_id": resource_attrs.get(
                            ATTR_ENGINE_INSTANCE_ID, ""
                        ),
                        "service_name": resource_attrs.get(ATTR_SERVICE_NAME, ""),
                        "check_name": attributes.get(ATTR_CHECK_NAME, ""),
                        "check_passed": attributes.get(ATTR_CHECK_PASSED),
                        "cache_outcome": attributes.get(ATTR_CACHE_OUTCOME, ""),
                        "cache_contract": attributes.get(ATTR_CACHE_CONTRACT, ""),
                        "cache_result_id": attributes.get(ATTR_CACHE_RESULT_ID, ""),
                        "dag_digest": attributes.get(ATTR_DAG_DIGEST, ""),
                        "content_preferred_digest": attributes.get(
                            ATTR_CONTENT_PREFERRED_DIGEST, ""
                        ),
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


def _normalized(value):
    """Normalize a declared name to the engine's field-name form.

    The CLI calls a function `repository-ruff-tools`; the engine names the
    call `repositoryRuffTools`. Comparing without separators and case covers
    the kebab, snake and camel forms of the same entry.
    """
    return "".join(char for char in (value or "").lower() if char.isalnum())


def _function_span(span, declared_name):
    """Match this repository's actual Dagger module, not any .<name> suffix."""
    name = span.get("name") or ""
    prefixes = ("EngineeringCi.", "engineering-ci:EngineeringCi.")
    return any(
        name.startswith(prefix)
        and _normalized(name[len(prefix) :]) == _normalized(declared_name)
        for prefix in prefixes
    )


def _check_keys(capture, check_name, function_name):
    """Find identity candidates only. Matching a name is NOT a binding."""
    declared = check_name or function_name
    keys = {}
    attribute = [s for s in capture.spans if s.get("check_name") == declared]
    selector = [
        s for s in capture.spans
        if check_name and s.get("name") == CHECK_SELECTOR_PREFIX + declared
    ]
    function = [s for s in capture.spans if _function_span(s, declared)]
    if attribute:
        keys[KEY_CHECK_ATTRIBUTE] = attribute
    if selector:
        keys[KEY_CHECK_SELECTOR] = selector
    if function:
        keys[KEY_FUNCTION_CALL] = function
    return keys


def _native_scope(span):
    """The Resource identity carried by this *particular* Span."""
    return span.get("session_id"), span.get("engine_instance_id")


def _ancestry(span, by_span, stop=None):
    """Return a native parent chain up to stop; do not invent missing links.

    An unknown parent beyond the requested stop is acceptable: exported traces
    can be partial. A gap *before* stop, cycle, or resource switch is not.
    """
    chain, visited = [], set()
    node = span
    while node:
        key = node["trace_id"], node["span_id"]
        if key in visited or not all(_native_scope(node)):
            return None
        visited.add(key)
        chain.append(node)
        if stop is not None and key == (stop["trace_id"], stop["span_id"]):
            return list(reversed(chain))
        parent_id = node.get("parent_span_id")
        if not parent_id:
            break
        parent = by_span.get((node["trace_id"], parent_id))
        if parent is None:
            break
        if _native_scope(node) != _native_scope(parent):
            return None
        node = parent
    return None if stop is not None else list(reversed(chain))


def _propagated_path(chain, parsed):
    if parsed is None:
        return False
    trace_id, parent_id = parsed
    return any(
        span["trace_id"] == trace_id
        and (span["span_id"] == parent_id or span["parent_span_id"] == parent_id)
        for span in chain
    )


def _cache_observation_scope(bound_spans):
    """Report the cache observation with the range it was observed on.

    The D-06 vocabulary never rises above its range: a hit on one call is
    never reported as the status of a whole Check, and a mixed range is
    reported as mixed.
    """
    if not bound_spans:
        return {
            "status": CACHE_SCOPE_NOT_APPLICABLE,
            "observed": [],
            "scope": [],
            "reason": "no_bound_call_span",
        }
    scope = sorted({span.get("name") or "" for span in bound_spans})
    outcomes = {
        span.get("cache_outcome") for span in bound_spans if span.get("cache_outcome")
    }
    if not outcomes:
        return {
            "status": CACHE_SCOPE_UNKNOWN,
            "observed": [],
            "scope": scope,
            "reason": "cache_outcome_not_observed_on_the_bound_call",
        }
    observed = sorted(outcomes)
    hit = CACHE_OUTCOME_HIT in outcomes
    ran = bool(outcomes & {CACHE_OUTCOME_EXECUTED, CACHE_OUTCOME_UNCACHED})
    if hit and ran:
        status = CACHE_SCOPE_MIXED
    elif hit:
        status = CACHE_SCOPE_OBSERVED_HIT
    elif ran:
        status = CACHE_SCOPE_OBSERVED_EXECUTION
    else:
        status = CACHE_SCOPE_UNKNOWN
    return {
        "status": status,
        "observed": observed,
        "scope": scope,
        "reason": "cache_outcome_on_the_bound_call_only",
    }


def _source_binding(bound_spans, source_binding_ref, capture):
    """Record caller-declared target and observed Dagger node digests.

    Neither a Git Tree string nor a node XXH3 digest verifies that the tool
    consumed the target's exact file/configuration bytes. #66 owns the actual
    Source producer; this A.1 candidate therefore reports it as unknown.
    """
    digests = sorted(
        {span.get("dag_digest") for span in bound_spans if span.get("dag_digest")}
    )
    content_digests = sorted(
        {
            span.get("content_preferred_digest")
            for span in bound_spans
            if span.get("content_preferred_digest")
        }
    )
    return {
        "source_binding_ref": source_binding_ref,
        "declared_target_source_ref": source_binding_ref,
        "source_binding_status": "unknown",
        "source_binding_reason": "execution_source_not_verified_by_66",
        "observed_dag_digests": digests,
        "observed_content_preferred_digests": content_digests,
        "session_ids": list(capture.session_ids) if capture else [],
        "engine_instance_ids": list(capture.engine_instance_ids) if capture else [],
    }


def _origin_execution(bound_spans):
    """Observe Function cache facts without upgrading them to Tool Origin.

    This pinned native capture does not identify Ruff's process by a verified
    exec-to-result link. Function cache execution is not proof that the tool
    itself ran in the same invocation.
    """
    outcomes = sorted(
        {s["cache_outcome"] for s in bound_spans if s.get("cache_outcome")}
    )
    ids = sorted(
        {s["cache_result_id"] for s in bound_spans if s.get("cache_result_id")}
    )
    return {
        "ref": None,
        "status": ORIGIN_UNKNOWN,
        "reason": "function_cache_is_not_tool_origin" if outcomes else
                  "cache_outcome_not_observed",
        "cache_outcome": outcomes[0] if len(outcomes) == 1 else None,
        "cache_result_id": ids[0] if len(ids) == 1 else None,
        "new_tool_execution": None,
    }


def resolve_origin_execution(receipts):
    """Do not join unrelated origins on an unscoped cache.result.id.

    Version-scoped Engine/cache-node identity is required for B's later
    origin handoff; candidates in A/A.1 remain unknown.
    """
    return [dict(receipt.get("origin_execution") or {}) for receipt in receipts]


def _bind_capture(
    capture, parsed, check_name, function_name, *,
    invocation_ref=None, capture_isolated=False,
):
    """Bind only native ancestry in the actual caller's capture scope.

    A unique module/selector chain is necessary but not sufficient: without
    propagated context the caller must also attest a dedicated receiver
    capture for this invocation. This is a scoped A.1 correlation, NOT a
    verified Git Source identity or a cross-Run execution identity.
    """
    if parsed is None and not (check_name or function_name):
        return BINDING_UNBOUND, REASON_NO_CORRELATION_KEY, BASIS_NONE, [], {}

    by_span = {}
    for span in capture.spans:
        key = (span["trace_id"], span["span_id"])
        if not all(key) or key in by_span:
            return BINDING_AMBIGUOUS, REASON_SPAN_CONFLICT, BASIS_NONE, [], {}
        by_span[key] = span

    if not check_name and not function_name:
        # A real propagated parent binds the direct native descendants,
        # not arbitrary other spans sharing the trace id.
        propagated = [
            s for s in capture.spans
            if s["trace_id"] == parsed[0]
            and (s["span_id"] == parsed[1] or s["parent_span_id"] == parsed[1])
        ]
        if len(propagated) == 1:
            return BINDING_BOUND, None, BASIS_TRACEPARENT, propagated, {
                KEY_TRACEPARENT: propagated,
            }
        if len(propagated) > 1:
            return BINDING_AMBIGUOUS, REASON_INVOCATION_AMBIGUOUS, BASIS_NONE, [], {}
        if any(s["trace_id"] == parsed[0] for s in capture.spans):
            return BINDING_AMBIGUOUS, REASON_TRACE_ID_ONLY, BASIS_NONE, [], {}
        return BINDING_UNBOUND, REASON_NO_CORRELATED_SPAN, BASIS_NONE, [], {}

    keys = _check_keys(capture, check_name, function_name)
    if not keys:
        return BINDING_UNBOUND, REASON_CHECK_NAME_NOT_OBSERVED, BASIS_NONE, [], {}

    # A single-call receiver with two native selectors for the same Check
    # is not uniquely attributable without an actual propagated parent.
    if parsed is None and (
        (check_name and len(keys.get(KEY_CHECK_SELECTOR, ())) > 1)
        or (function_name and len([
            s for s in keys.get(KEY_FUNCTION_CALL, ())
            if s["name"].startswith("engineering-ci:EngineeringCi.")
        ]) > 1)
    ):
        return (
            BINDING_AMBIGUOUS, REASON_INVOCATION_AMBIGUOUS,
            BASIS_NONE, [], keys,
        )

    candidates = []
    if check_name:
        selectors = keys.get(KEY_CHECK_SELECTOR, ())
        functions = [
            s for s in keys.get(KEY_FUNCTION_CALL, ())
            if s["name"].startswith("engineering-ci:EngineeringCi.")
        ]
        cores = [
            s for s in keys.get(KEY_FUNCTION_CALL, ())
            if s["name"].startswith("EngineeringCi.")
        ]
        for selector in selectors:
            if not all(_native_scope(selector)):
                continue
            for module in functions:
                path = _ancestry(module, by_span, stop=selector)
                if path is None or not any(
                    (s["trace_id"], s["span_id"])
                    in {(c["trace_id"], c["span_id"]) for c in cores}
                    for s in path
                ):
                    continue
                candidates.append(path)
        # A genuinely propagated check attribute can bind without a selector,
        # provided it uniquely identifies the call and parent context.
        if not candidates and parsed is not None:
            for span in keys.get(KEY_CHECK_ATTRIBUTE, ()):
                if all(_native_scope(span)) and _propagated_path([span], parsed):
                    candidates.append([span])
    else:
        modules = [
            s for s in keys.get(KEY_FUNCTION_CALL, ())
            if s["name"].startswith("engineering-ci:EngineeringCi.")
        ]
        cores = [
            s for s in keys.get(KEY_FUNCTION_CALL, ())
            if s["name"].startswith("EngineeringCi.")
        ]
        for module in modules:
            if not all(_native_scope(module)):
                continue
            for core in cores:
                path = _ancestry(module, by_span, stop=core)
                if path is not None:
                    candidates.append(path)

    if not candidates:
        if parsed and any(
            s["trace_id"] != parsed[0] for group in keys.values() for s in group
        ) and not any(
            s["trace_id"] == parsed[0] for group in keys.values() for s in group
        ):
            return BINDING_AMBIGUOUS, REASON_CHECK_NAME_OUTSIDE_TRACE, BASIS_NONE, [], keys
        if parsed is not None and any(
            s["trace_id"] == parsed[0]
            for group in keys.values() for s in group
        ):
            return BINDING_AMBIGUOUS, REASON_TRACE_ID_ONLY, BASIS_NONE, [], keys
        return BINDING_UNBOUND, (
            REASON_SESSION_NOT_OBSERVED if not any(
                all(_native_scope(s)) for group in keys.values() for s in group
            ) else REASON_PARENT_CHAIN
        ), BASIS_NONE, [], keys

    # One selector/Function call cannot be chosen merely because a Capture
    # contains its name. Confirm a propagated parent or a caller-scoped
    # isolated receiver and a real invocation record.
    if parsed is not None:
        candidates = [path for path in candidates if _propagated_path(path, parsed)]
        if not candidates:
            return BINDING_AMBIGUOUS, REASON_TRACE_ID_ONLY, BASIS_NONE, [], keys
        basis = BASIS_TRACEPARENT
    else:
        if not (capture_isolated and invocation_ref):
            return BINDING_UNBOUND, REASON_CALLER_SCOPE, BASIS_NONE, [], keys
        basis = BASIS_CHECK_SELECTOR if check_name else BASIS_FUNCTION_CALL

    # Multiple distinct eligible Function paths cannot be reduced by name.
    unique = {
        (path[0]["trace_id"], path[0]["span_id"], path[-1]["span_id"]): path
        for path in candidates
    }
    if len(unique) != 1:
        return BINDING_AMBIGUOUS, REASON_INVOCATION_AMBIGUOUS, BASIS_NONE, [], keys
    selected = next(iter(unique.values()))
    # Distinct Session/Engine identities in the selected chain are rejected by
    # _ancestry; do not promote capture-wide session collections.
    return BINDING_BOUND, None, basis, selected, keys


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
    function_name: str | None = None,
    capture_ref: str | None = None,
    capture_isolated: bool = False,
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
    binding_keys: dict[str, list[dict]] = {}
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
        binding_status, reason, basis, bound_spans, binding_keys = _bind_capture(
            capture, parsed, check_name, function_name,
            invocation_ref=invocation_ref,
            capture_isolated=capture_isolated,
        )

    source_associated = binding_status == BINDING_BOUND
    if required_observability and not source_associated and reason is None:
        # The current Required set asks for observability attributes. The
        # coverage stays partial; a native result alone does not close it.
        reason = REASON_REQUIRED_OBSERVABILITY_UNMET

    origin_execution = _origin_execution(bound_spans)
    proves = [PROVES_INVOCATION] if binding_status == BINDING_BOUND else []

    claims = {
        CLAIM_EXPORTER_CONFIGURED: bool(
            sdk_scope.configured or engine_scope.configured
        ),
        CLAIM_SENT: sent,
        CLAIM_RECEIVER_OBSERVED: receiver_observed,
        CLAIM_SOURCE_ASSOCIATED: source_associated,
        CLAIM_NATIVE_CHECK_RESULT: native_result is not None,
    }

    if binding_status == BINDING_BOUND:
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
            "binding_status": binding_status,
            "binding_reason": reason,
            "correlation_basis": basis,
            "binding_proves": proves,
            "binding_keys": sorted(binding_keys),
            "correlation_key": traceparent,
            "declared_check_name": check_name,
            "declared_function_name": function_name,
            "session_ids": sorted({s["session_id"] for s in bound_spans}),
            "engine_instance_ids": sorted(
                {s["engine_instance_id"] for s in bound_spans}
            ),
            "capture_isolated": bool(capture_isolated),
            "verified_parent_chain": [
                {
                    "trace_id": s["trace_id"],
                    "span_id": s["span_id"],
                    "parent_span_id": s["parent_span_id"],
                    "name": s["name"],
                    "session_id": s["session_id"],
                    "engine_instance_id": s["engine_instance_id"],
                }
                for s in bound_spans
            ],
        },
        "origin_execution": origin_execution,
        "cache_observation_scope": _cache_observation_scope(bound_spans),
        "invocation": {
            "actual_invocation_ref": invocation_ref,
            "source_binding_ref": source_binding_ref,
            "check_or_function_ref": check_ref,
        },
        # Trace association is independent of authoritative Git Source matching.
        "source_binding": _source_binding(bound_spans, source_binding_ref, capture),
        "native_check_result": native_result,
        "native_result_or_bundle_ref": native_result_ref,
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
        "--capture-isolated",
        action="store_true",
        help="Caller confirms this Receiver was dedicated to the one invocation",
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
        "--function-name",
        help=(
            "Module function the invocation called (for example "
            "execute-plan); confirmed when a captured span carries the "
            "engine's function call name"
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
        function_name=args.function_name,
        capture_ref=args.capture_ref,
        capture_isolated=args.capture_isolated,
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
        print(f"binding_status: {receipt['binding']['binding_status']}")
        print(f"correlation_basis: {receipt['binding']['correlation_basis']}")
        print(
            f"cache_observation_scope: {receipt['cache_observation_scope']['status']}"
        )
        print(f"origin_execution: {receipt['origin_execution']['status']}")
        print(f"artifact: {args.output}")
    if receipt["status"] in {STATUS_PRODUCED, STATUS_PARTIAL}:
        return 0
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
