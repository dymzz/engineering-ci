"""Dagger native OTel export/receive observations (ECI #87 section A)."""

import json
from pathlib import Path

import pytest

from engineering_ci import dagger_telemetry

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
PARENT_SPAN_ID = "00f067aa0ba902b7"
OTHER_SPAN_ID = "1111111111111111"
TRACEPARENT = f"00-{TRACE_ID}-{PARENT_SPAN_ID}-01"


def capture_document(*spans):
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": []},
                "scopeSpans": [
                    {
                        "scope": {"name": "dagger.io/engine"},
                        "spans": list(spans),
                    }
                ],
            }
        ]
    }


def span(
    trace_id=TRACE_ID,
    span_id="2222222222222222",
    parent_span_id=PARENT_SPAN_ID,
    name="dagger.io/engine",
    check_name=None,
    cache_outcome=None,
):
    attributes = []
    if check_name is not None:
        attributes.append(
            {"key": "dagger.io/check.name", "value": {"stringValue": check_name}}
        )
    if cache_outcome is not None:
        attributes.append(
            {
                "key": "dagger.io/cache.outcome",
                "value": {"stringValue": cache_outcome},
            }
        )
    return {
        "traceId": trace_id,
        "spanId": span_id,
        "parentSpanId": parent_span_id,
        "name": name,
        "attributes": attributes,
    }


def sdk_scope(**overrides):
    environ = {
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318",
        "TRACEPARENT": TRACEPARENT,
    }
    environ.update(overrides)
    return dagger_telemetry.read_sdk_scope(environ)


def engine_scope(**overrides):
    return dagger_telemetry.read_engine_scope(environ={}, **overrides)


def receipt(**overrides):
    values = {
        "sdk_scope": sdk_scope(),
        "engine_scope": engine_scope(),
        "correlation": TRACEPARENT,
    }
    values.update(overrides)
    return dagger_telemetry.build_receipt(**values)


def write_capture(tmp_path, document, name="capture.json"):
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_sdk_scope_reads_the_native_environment():
    scope = sdk_scope()
    assert scope.scope == "sdk_module_client"
    assert scope.configured is True
    assert scope.endpoint == "http://127.0.0.1:4318"
    assert scope.endpoint_kind == "explicit"
    assert scope.protocol == "http/protobuf"
    assert scope.signals == ("traces",)
    assert scope.service_name == "dagger-python-sdk"
    assert scope.detail["traceparent"] == TRACEPARENT


def test_sdk_scope_is_disabled_by_the_sdk_switch():
    scope = sdk_scope(OTEL_SDK_DISABLED="true")
    assert scope.configured is False
    assert scope.detail["sdk_disabled"] is True


def test_sdk_scope_without_any_otel_variable_is_unconfigured():
    scope = dagger_telemetry.read_sdk_scope({})
    assert scope.configured is False
    assert scope.endpoint is None
    assert scope.endpoint_kind == "none"


def test_sdk_protocol_variable_is_recorded_as_ignored():
    scope = sdk_scope(OTEL_EXPORTER_OTLP_PROTOCOL="grpc")
    assert scope.protocol == "http/protobuf"
    assert scope.detail["protocol_variable_ignored"] is True


def test_engine_scope_needs_the_workload_export_switch(tmp_path):
    config = tmp_path / "engine.json"
    config.write_text(json.dumps({"telemetry": {"workloadExport": True}}), "utf-8")
    scope = dagger_telemetry.read_engine_scope(
        engine_config=config,
        endpoint="http://172.17.0.1:4318/v1/traces",
        environ={},
    )
    assert scope.scope == "engine_workload"
    assert scope.configured is True
    assert scope.protocol == "http/protobuf"
    assert scope.signals == ("traces", "metrics")
    assert scope.detail["workload_export_enabled"] is True
    assert scope.detail["logs_exported"] is False


def test_engine_scope_without_the_config_is_not_configured():
    scope = engine_scope(endpoint="http://172.17.0.1:4318/v1/traces")
    assert scope.configured is False
    assert scope.detail["config_observed"] is False


def test_engine_scope_without_an_endpoint_is_not_configured(tmp_path):
    config = tmp_path / "engine.json"
    config.write_text(json.dumps({"telemetry": {"workloadExport": True}}), "utf-8")
    scope = dagger_telemetry.read_engine_scope(engine_config=config, environ={})
    assert scope.configured is False


def test_receipt_without_capture_keeps_the_trace_unknown():
    record = receipt()
    assert record["status"] == "incomplete"
    assert record["reason"] == "no_capture"
    assert record["claims"]["exporter_configured"] is True
    assert record["claims"]["sent"] == "unknown"
    assert record["claims"]["receiver_observed"] is False
    assert record["claims"]["source_associated"] is False
    assert record["claims"]["native_check_result"] is False
    assert record["raw_trace_ref"] is None


def test_native_result_is_kept_when_the_trace_is_missing():
    record = receipt(
        native_result={"exit_code": 1}, native_result_ref={"path": "r.json"}
    )
    assert record["status"] == "partial"
    assert record["claims"]["native_check_result"] is True
    assert record["claims"]["receiver_observed"] is False
    assert record["native_check_result"] == {"exit_code": 1}


def test_capture_binds_through_the_propagated_traceparent(tmp_path):
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(span()))
    )
    record = receipt(capture=capture)
    assert record["status"] == "produced"
    assert record["binding"]["status"] == "bound"
    assert record["binding"]["basis"] == "w3c_traceparent_propagation"
    assert record["claims"]["sent"] == "sent"
    assert record["claims"]["receiver_observed"] is True
    assert record["claims"]["source_associated"] is True
    assert record["observed_trace_span_ids"] == ["2222222222222222"]
    assert record["raw_trace_ref"]["sha256"] == capture.sha256


def test_declared_check_name_binds_on_the_propagated_parent(tmp_path):
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(span(check_name="lint")))
    )
    record = receipt(capture=capture, check_name="lint")
    assert record["binding"]["status"] == "bound"
    assert record["binding"]["basis"] == "w3c_traceparent_propagation"
    assert record["coverage"]["check_names"] == ["lint"]


def test_check_name_in_the_same_trace_binds_on_the_engine_attribute(tmp_path):
    capture = dagger_telemetry.read_capture(
        write_capture(
            tmp_path,
            capture_document(span(parent_span_id=OTHER_SPAN_ID, check_name="lint")),
        )
    )
    record = receipt(capture=capture, check_name="lint")
    assert record["binding"]["status"] == "bound"
    assert record["binding"]["basis"] == "engine_check_attribute_in_propagated_trace"


def test_check_name_without_a_correlation_key_binds_on_the_attribute(tmp_path):
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(span(check_name="lint")))
    )
    record = receipt(capture=capture, check_name="lint", correlation=None)
    assert record["binding"]["status"] == "bound"
    assert record["binding"]["basis"] == "engine_check_attribute"


def test_declared_check_name_needs_the_engine_attribute(tmp_path):
    other = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(span(check_name="tests")))
    )
    record = receipt(capture=other, check_name="lint")
    assert record["binding"]["status"] == "unbound"
    assert record["reason"] == "check_name_not_observed"
    assert record["claims"]["source_associated"] is False


def test_check_name_outside_the_propagated_trace_is_ambiguous(tmp_path):
    capture = dagger_telemetry.read_capture(
        write_capture(
            tmp_path,
            capture_document(span(trace_id="a" * 32, check_name="lint")),
        )
    )
    record = receipt(capture=capture, check_name="lint")
    assert record["binding"]["status"] == "ambiguous"
    assert record["reason"] == "check_name_outside_propagated_trace"
    assert record["claims"]["source_associated"] is False


def test_cache_outcome_is_recorded_as_observed_scope(tmp_path):
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(span(cache_outcome="hit")))
    )
    record = receipt(capture=capture)
    assert record["coverage"]["cache_outcomes"] == ["hit"]


def test_shared_trace_id_without_the_propagated_parent_stays_ambiguous(tmp_path):
    capture = dagger_telemetry.read_capture(
        write_capture(
            tmp_path,
            capture_document(span(parent_span_id=OTHER_SPAN_ID)),
        )
    )
    record = receipt(capture=capture)
    assert record["binding"]["status"] == "ambiguous"
    assert record["reason"] == "trace_id_match_without_propagated_parent"
    assert record["claims"]["source_associated"] is False
    assert record["observed_trace_span_ids"] == []


def test_capture_of_another_trace_stays_unbound(tmp_path):
    capture = dagger_telemetry.read_capture(
        write_capture(
            tmp_path,
            capture_document(span(trace_id="a" * 32)),
        )
    )
    record = receipt(capture=capture)
    assert record["binding"]["status"] == "unbound"
    assert record["reason"] == "no_correlated_span"
    assert record["claims"]["receiver_observed"] is True


def test_capture_without_correlation_key_is_observed_but_unbound(tmp_path):
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(span()))
    )
    record = receipt(capture=capture, correlation=None)
    assert record["binding"]["status"] == "unbound"
    assert record["reason"] == "no_correlation_key"
    assert record["claims"]["receiver_observed"] is True
    assert record["claims"]["source_associated"] is False


def test_empty_capture_is_not_a_receiver_observation(tmp_path):
    capture = dagger_telemetry.read_capture(write_capture(tmp_path, capture_document()))
    record = receipt(capture=capture)
    assert record["claims"]["receiver_observed"] is False
    assert record["claims"]["sent"] == "unknown"
    assert record["reason"] == "capture_empty"


def test_exporter_error_is_the_only_proof_of_a_failed_send(tmp_path):
    error = tmp_path / "exporter.err"
    error.write_text("export failed: connection refused\n", encoding="utf-8")
    record = receipt(exporter_error=dagger_telemetry.read_exporter_error(error))
    assert record["claims"]["sent"] == "not_sent"
    assert record["sent_basis"] == "exporter_reported_failure"
    assert record["reason"] == "exporter_reported_failure"


def test_required_observability_keeps_the_coverage_partial(tmp_path):
    capture = dagger_telemetry.read_capture(
        write_capture(
            tmp_path,
            capture_document(span(trace_id="a" * 32)),
        )
    )
    record = receipt(capture=capture, required_observability=True)
    assert record["status"] == "partial"
    assert record["required_observability"] == {
        "declared": True,
        "satisfied": False,
    }


def test_capability_inventory_is_bound_to_pinned_sources():
    inventory = {
        capability.key: capability
        for capability in dagger_telemetry.capability_inventory()
    }
    assert inventory["engine_workload_export"].status == "available"
    assert inventory["engine_check_attribute"].status == "available"
    assert inventory["engine_cache_observation"].status == "available"
    assert inventory["sdk_context_propagation"].status == "available"
    assert inventory["sdk_metric_and_log_export"].status == "not_available"
    assert inventory["sdk_version_marker"].status == "not_available"


def test_pinned_versions_come_from_the_repository():
    assert dagger_telemetry.pinned_engine_version() == "v1.0.0-beta.16"
    # The generated SDK carries no version marker; it is never guessed.
    assert dagger_telemetry.pinned_sdk_version() != ""
    sdk_source = (
        Path(dagger_telemetry.ROOT)
        / ".dagger"
        / "sdk"
        / "src"
        / "dagger"
        / "telemetry.py"
    )
    assert sdk_source.is_file()


def test_parse_traceparent_rejects_invalid_values():
    assert dagger_telemetry.parse_traceparent(None) is None
    assert dagger_telemetry.parse_traceparent("not-a-traceparent") is None
    assert (
        dagger_telemetry.parse_traceparent(f"00-{'0' * 32}-{PARENT_SPAN_ID}-01") is None
    )
    assert dagger_telemetry.parse_traceparent(TRACEPARENT) == (TRACE_ID, PARENT_SPAN_ID)


def test_cli_writes_the_receipt_artifact(tmp_path):
    capture_path = write_capture(tmp_path, capture_document(span(check_name="lint")))
    native_path = tmp_path / "native.json"
    native_path.write_text(json.dumps({"exit_code": 0}), encoding="utf-8")
    output = tmp_path / "out.json"
    code = dagger_telemetry.main(
        [
            "--capture",
            str(capture_path),
            "--native-result",
            str(native_path),
            "--traceparent",
            TRACEPARENT,
            "--check-name",
            "lint",
            "--invocation-ref",
            "dagger check --engineering-ci --check=lint",
            "--check-ref",
            "lint",
            "--output",
            str(output),
            "--json",
        ]
    )
    assert code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["kind"] == "engineering-ci-dagger-telemetry"
    assert payload["evidence_class"] == "candidate_record"
    assert payload["status"] == "produced"
    assert payload["claims"]["source_associated"] is True
    assert payload["invocation"]["actual_invocation_ref"] == (
        "dagger check --engineering-ci --check=lint"
    )
    assert payload["invocation"]["check_or_function_ref"] == "lint"


def test_cli_returns_three_without_usable_evidence(tmp_path):
    output = tmp_path / "out.json"
    code = dagger_telemetry.main(["--output", str(output)])
    assert code == 3
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "incomplete"


def test_cli_rejects_an_invalid_capture(tmp_path):
    capture_path = tmp_path / "capture.json"
    capture_path.write_text("not json", encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        dagger_telemetry.main(
            ["--capture", str(capture_path), "--output", str(tmp_path / "out.json")]
        )
    assert error.value.code == 2


def test_receipt_file_bytes_use_line_feeds(tmp_path):
    output = tmp_path / "out.json"
    dagger_telemetry.main(["--output", str(output)])
    raw = Path(output).read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")
