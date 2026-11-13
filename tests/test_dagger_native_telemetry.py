"""Dagger native OTel export/receive observations (ECI #87 section A)."""

import json
from pathlib import Path

import pytest

from engineering_ci import dagger_telemetry

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
PARENT_SPAN_ID = "00f067aa0ba902b7"
OTHER_SPAN_ID = "1111111111111111"
TRACEPARENT = f"00-{TRACE_ID}-{PARENT_SPAN_ID}-01"


SESSION_ID = "session-0001"
ENGINE_INSTANCE_ID = "engine-0001"


def capture_document(*spans, session_id: str | None = SESSION_ID):
    resource_attributes = []
    if session_id is not None:
        resource_attributes.append(
            {"key": "dagger.io/session.id", "value": {"stringValue": session_id}}
        )
        resource_attributes.append(
            {
                "key": "dagger.io/engine.instance.id",
                "value": {"stringValue": ENGINE_INSTANCE_ID},
            }
        )
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": resource_attributes},
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
    cache_result_id=None,
    digest=None,
):
    attributes = []
    if digest is not None:
        attributes.append(
            {"key": "dagger.io/dag.digest", "value": {"stringValue": digest}}
        )
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
    if cache_result_id is not None:
        attributes.append(
            {
                "key": "dagger.io/cache.result.id",
                "value": {"stringValue": cache_result_id},
            }
        )
    return {
        "traceId": trace_id,
        "spanId": span_id,
        "parentSpanId": parent_span_id,
        "name": name,
        "attributes": attributes,
    }


def check_graph(seed=0x700, *, check_name="lint", cache_outcome=None,
                cache_result_id=None, digest=None):
    """A real selector -> Artifact.value -> core -> module native ancestry."""
    selector, artifact, core, module = (f"{seed + i:016x}" for i in range(4))
    return (
        span(span_id=selector, name=f"dag://?check={check_name}"),
        span(span_id=artifact, parent_span_id=selector, name="Artifact.value"),
        span(span_id=core, parent_span_id=artifact,
             name=f"EngineeringCi.{check_name}", cache_outcome=cache_outcome,
             cache_result_id=cache_result_id, digest=digest),
        span(span_id=module, parent_span_id=core,
             name=f"engineering-ci:EngineeringCi.{check_name}"),
    )


def function_graph(seed=0xa00, *, function="repositoryRuffTools", cache_outcome=None):
    core, module = (f"{seed + i:016x}" for i in range(2))
    return (
        span(span_id=core, name=f"EngineeringCi.{function}",
             cache_outcome=cache_outcome),
        span(span_id=module, parent_span_id=core,
             name=f"engineering-ci:EngineeringCi.{function}"),
    )


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
        "invocation_ref": "scoped-test-invocation",
        "capture_isolated": True,
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
    assert record["binding"]["binding_status"] == "bound"
    assert record["binding"]["correlation_basis"] == "w3c_traceparent_propagation"
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
    assert record["binding"]["binding_status"] == "bound"
    assert record["binding"]["correlation_basis"] == "w3c_traceparent_propagation"
    assert record["coverage"]["check_names"] == ["lint"]


def test_check_name_in_the_same_trace_binds_on_the_engine_attribute(tmp_path):
    """Same Trace without actual propagation is NOT sufficient."""
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path,
                      capture_document(span(parent_span_id=OTHER_SPAN_ID,
                                            check_name="lint")))
    )
    record = receipt(capture=capture, check_name="lint")
    assert record["binding"]["binding_status"] == "ambiguous"
    assert record["reason"] == "trace_id_match_without_propagated_parent"

def test_check_selector_span_name_binds_the_check(tmp_path):
    """Selector alone cannot prove the tool invocation."""
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(
            span(name="dag://?check=lint", span_id="3333333333333333")))
    )
    record = receipt(capture=capture, check_name="lint", correlation=None)
    assert record["binding"]["binding_status"] == "unbound"
    assert record["reason"] == "native_parent_chain_not_observed"

def test_module_function_call_span_name_binds_the_check(tmp_path):
    """Function name without selector lineage is not a native Check bind."""
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(
            span(name="engineering-ci:EngineeringCi.lint",
                 span_id="4444444444444444")))
    )
    record = receipt(capture=capture, check_name="lint", correlation=None)
    assert record["binding"]["binding_status"] == "unbound"
    assert record["reason"] == "native_parent_chain_not_observed"

def test_check_name_without_a_correlation_key_binds_on_the_attribute(tmp_path):
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(span(check_name="lint")))
    )
    record = receipt(capture=capture, check_name="lint", correlation=None)
    assert record["binding"]["binding_status"] == "unbound"
    assert record["reason"] == "native_parent_chain_not_observed"

def test_declared_check_name_needs_the_engine_attribute(tmp_path):
    other = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(span(check_name="tests")))
    )
    record = receipt(capture=other, check_name="lint")
    assert record["binding"]["binding_status"] == "unbound"
    assert record["reason"] == "check_name_not_observed"
    assert record["claims"]["source_associated"] is False


def test_check_name_without_an_engine_session_is_not_a_same_source_binding(tmp_path):
    capture = dagger_telemetry.read_capture(
        write_capture(
            tmp_path,
            capture_document(span(check_name="lint"), session_id=None),
        )
    )
    record = receipt(capture=capture, check_name="lint")
    assert record["binding"]["binding_status"] == "unbound"
    assert record["reason"] == "engine_session_not_observed"
    assert record["claims"]["source_associated"] is False


def test_bound_check_records_the_engine_session_reference(tmp_path):
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(*check_graph()))
    )
    record = receipt(capture=capture, check_name="lint", correlation=None)
    assert record["binding"]["binding_status"] == "bound"
    assert record["binding"]["session_ids"] == [SESSION_ID]
    assert record["binding"]["engine_instance_ids"] == [ENGINE_INSTANCE_ID]
    assert len(record["binding"]["verified_parent_chain"]) == 4

def test_check_name_outside_the_propagated_trace_is_ambiguous(tmp_path):
    capture = dagger_telemetry.read_capture(
        write_capture(
            tmp_path,
            capture_document(span(trace_id="a" * 32, check_name="lint")),
        )
    )
    record = receipt(capture=capture, check_name="lint")
    assert record["binding"]["binding_status"] == "ambiguous"
    assert record["reason"] == "check_name_outside_propagated_trace"
    assert record["claims"]["source_associated"] is False


def test_cache_outcome_is_recorded_as_observed_scope(tmp_path):
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(span(cache_outcome="hit")))
    )
    record = receipt(capture=capture)
    assert record["coverage"]["cache_outcomes"] == ["hit"]


def test_executed_call_proves_the_origin_tool_execution(tmp_path):
    """Function executed is a cache observation, not Tool Origin proof."""
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(
            *check_graph(cache_outcome="executed", cache_result_id="7921")))
    )
    record = receipt(capture=capture, check_name="lint", correlation=None)
    assert record["binding"]["binding_status"] == "bound"
    assert record["origin_execution"]["status"] == "unknown"
    assert record["origin_execution"]["new_tool_execution"] is None
    assert record["origin_execution"]["ref"] is None
    assert record["origin_execution"]["cache_result_id"] == "7921"
    assert "origin_tool_execution" not in record["binding"]["binding_proves"]
    assert record["cache_observation_scope"]["status"] == "observed_execution"

def test_cache_hit_ran_no_tool_and_leaves_the_origin_unknown(tmp_path):
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(
            *check_graph(cache_outcome="hit", cache_result_id="7921")))
    )
    record = receipt(capture=capture, check_name="lint", correlation=None)
    origin = record["origin_execution"]
    assert origin["status"] == "unknown"
    assert origin["new_tool_execution"] is None
    assert origin["ref"] is None
    assert origin["reason"] == "function_cache_is_not_tool_origin"
    assert origin["cache_result_id"] == "7921"
    assert record["cache_observation_scope"]["status"] == "observed_hit"

def test_cache_reuse_points_at_the_existing_origin_when_it_is_proven(tmp_path):
    """Unscoped cache.result.id must not join separate tool origins."""
    first = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(
            *check_graph(cache_outcome="executed", cache_result_id="7921")),
            "first.json")
    )
    second = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(
            *check_graph(seed=0x800, cache_outcome="hit",
                         cache_result_id="7921")), "second.json")
    )
    records = [receipt(capture=first, check_name="lint", correlation=None),
               receipt(capture=second, check_name="lint", correlation=None)]
    origins = dagger_telemetry.resolve_origin_execution(records)
    assert [origin["status"] for origin in origins] == ["unknown", "unknown"]
    assert all(origin["ref"] is None for origin in origins)
    assert all(origin["new_tool_execution"] is None for origin in origins)

def test_cache_reuse_without_the_prior_execution_stays_unknown(tmp_path):
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(
            *check_graph(cache_outcome="hit", cache_result_id="7921")))
    )
    record = receipt(capture=capture, check_name="lint", correlation=None)
    origins = dagger_telemetry.resolve_origin_execution([record])
    assert origins[0]["status"] == "unknown"
    assert origins[0]["ref"] is None
    assert origins[0]["new_tool_execution"] is None

def test_shared_trace_id_without_the_propagated_parent_stays_ambiguous(tmp_path):
    capture = dagger_telemetry.read_capture(
        write_capture(
            tmp_path,
            capture_document(span(parent_span_id=OTHER_SPAN_ID)),
        )
    )
    record = receipt(capture=capture)
    assert record["binding"]["binding_status"] == "ambiguous"
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
    assert record["binding"]["binding_status"] == "unbound"
    assert record["reason"] == "no_correlated_span"
    assert record["claims"]["receiver_observed"] is True


def test_capture_without_correlation_key_is_observed_but_unbound(tmp_path):
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(span()))
    )
    record = receipt(capture=capture, correlation=None)
    assert record["binding"]["binding_status"] == "unbound"
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
    assert inventory["engine_check_attribute"].status == "partial"
    assert inventory["engine_check_identity_by_span_name"].status == "available"
    assert inventory["engine_cache_observation"].status == "available"
    assert inventory["engine_export_flush"].status == "available"
    assert inventory["sdk_export_flush"].status == "available"
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


# The directed acceptance of the Issue #87 review.


def test_two_invocations_of_the_same_check_name_do_not_merge(tmp_path):
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(
            *check_graph(seed=0x700), *check_graph(seed=0x800)))
    )
    record = receipt(capture=capture, check_name="lint", correlation=None)
    assert record["binding"]["binding_status"] == "ambiguous"
    assert record["reason"] == "native_invocation_ambiguous"
    assert record["observed_trace_span_ids"] == []

def test_one_check_with_several_spans_binds_all_of_them(tmp_path):
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(*check_graph()))
    )
    record = receipt(capture=capture, check_name="lint", correlation=None)
    assert record["binding"]["binding_status"] == "bound"
    assert len(record["observed_trace_span_ids"]) == 4
    assert [x["name"] for x in record["binding"]["verified_parent_chain"]] == [
        "dag://?check=lint", "Artifact.value", "EngineeringCi.lint",
        "engineering-ci:EngineeringCi.lint",
    ]

def test_interleaved_spans_of_concurrent_checks_do_not_cross_bind(tmp_path):
    lint = check_graph(seed=0x700)
    tests = check_graph(seed=0x800, check_name="tests")
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(
            lint[0], tests[0], tests[1], lint[1], tests[2], lint[2],
            tests[3], lint[3]))
    )
    record = receipt(capture=capture, check_name="lint", correlation=None)
    assert record["binding"]["binding_status"] == "bound"
    assert record["observed_trace_span_ids"] == [s["spanId"] for s in lint]
    assert "EngineeringCi.tests" not in record["coverage"]["span_names"]

def test_a_host_check_without_dagger_spans_keeps_its_native_result():
    record = receipt(
        native_result={"check": "host hygiene", "exit_code": 0},
        native_result_ref={"path": "host.json", "sha256": "0" * 64},
    )
    assert record["binding"]["binding_status"] == "unbound"
    assert record["binding"]["binding_reason"] == "no_capture"
    assert record["claims"]["source_associated"] is False
    assert record["claims"]["native_check_result"] is True
    assert record["native_check_result"] == {"check": "host hygiene", "exit_code": 0}
    assert record["status"] == "partial"


def test_a_failing_native_result_still_binds_to_its_trace(tmp_path):
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(
            *check_graph(cache_outcome="executed", cache_result_id="7921")))
    )
    record = receipt(
        capture=capture, check_name="lint", correlation=None,
        native_result={"check": "lint", "exit_code": 1},
        native_result_ref={"path": "native.json", "sha256": "0" * 64},
    )
    assert record["binding"]["binding_status"] == "bound"
    assert record["claims"]["native_check_result"] is True
    assert record["native_check_result"]["exit_code"] == 1
    assert record["status"] == "produced"

def test_standalone_function_entry_point_binds_by_its_call_name(tmp_path):
    """Function entry has no selector; it still needs real parent ancestry."""
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(
            *function_graph(cache_outcome="executed")))
    )
    record = receipt(capture=capture, function_name="repository-ruff-tools",
                     correlation=None)
    assert record["binding"]["binding_status"] == "bound"
    assert record["binding"]["correlation_basis"] == "engine_function_call"
    assert record["binding"]["declared_function_name"] == "repository-ruff-tools"
    assert len(record["observed_trace_span_ids"]) == 2

def test_a_different_function_in_the_same_capture_does_not_bind(tmp_path):
    actual = function_graph()
    unrelated = function_graph(seed=0xb00, function="executePlan")
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(*actual, *unrelated))
    )
    record = receipt(capture=capture, function_name="repository-ruff-tools",
                     correlation=None)
    assert record["binding"]["binding_status"] == "bound"
    assert record["observed_trace_span_ids"] == [s["spanId"] for s in actual]

def test_check_binding_requires_caller_capture_scope(tmp_path):
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(*check_graph()))
    )
    record = receipt(capture=capture, check_name="lint", correlation=None,
                     capture_isolated=False)
    assert record["binding"]["binding_status"] == "unbound"
    assert record["reason"] == "caller_capture_scope_not_proven"


def test_check_binding_rejects_missing_parent_and_wrong_module(tmp_path):
    wrong = list(check_graph())
    wrong[-1] = dict(wrong[-1])
    wrong[-1]["parentSpanId"] = OTHER_SPAN_ID
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(*wrong), "broken.json")
    )
    assert receipt(capture=capture, check_name="lint", correlation=None)[
        "binding"
    ]["binding_status"] == "unbound"

    wrong[-1] = span(span_id=f"{0x703:016x}", parent_span_id=f"{0x702:016x}",
                     name="unrelated:EngineeringCi.lint")
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(*wrong), "module.json")
    )
    assert receipt(capture=capture, check_name="lint", correlation=None)[
        "binding"
    ]["binding_status"] == "unbound"


def test_check_binding_rejects_cross_resource_session(tmp_path):
    first = capture_document(*check_graph()[:-1], session_id="first")
    second = capture_document(check_graph()[-1], session_id="second")
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path, {"resourceSpans": (
            first["resourceSpans"] + second["resourceSpans"]
        )})
    )
    record = receipt(capture=capture, check_name="lint", correlation=None)
    assert record["binding"]["binding_status"] == "unbound"


def test_check_binding_rejects_duplicate_native_span_ids(tmp_path):
    spans = check_graph()
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(*spans, spans[-1]))
    )
    record = receipt(capture=capture, check_name="lint", correlation=None)
    assert record["binding"]["binding_status"] == "ambiguous"
    assert record["reason"] == "native_span_identity_conflict"


def test_required_observability_produced_when_native_binding_satisfied(tmp_path):
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(*check_graph()))
    )
    record = receipt(capture=capture, check_name="lint", correlation=None,
                     required_observability=True)
    assert record["required_observability"] == {
        "declared": True, "satisfied": True,
    }
    assert record["status"] == "produced"


def test_real_capture_has_strict_native_check_and_artifact_paths(tmp_path):
    """Real engine JSON can be supplied as a focused local/CI artifact fixture."""
    # This is checked again against the actual receiver output in the A.1 CI gate.
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(
            *check_graph(), *function_graph(seed=0xb00)), "native.json")
    )
    record = receipt(capture=capture, check_name="lint", correlation=None)
    assert record["binding"]["binding_status"] == "bound"
    assert len(record["binding"]["verified_parent_chain"]) == 4


def test_source_binding_records_the_engine_content_identity(tmp_path):
    capture = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(
            *check_graph(cache_outcome="executed", digest="xxh3:f40b13ae16dbfbf3")))
    )
    record = receipt(capture=capture, check_name="lint", correlation=None,
                     source_binding_ref="git-tree:abc123")
    binding = record["source_binding"]
    assert binding["source_binding_ref"] == "git-tree:abc123"
    assert binding["observed_dag_digests"] == ["xxh3:f40b13ae16dbfbf3"]
    assert binding["session_ids"] == [SESSION_ID]
    assert binding["engine_instance_ids"] == [ENGINE_INSTANCE_ID]

def test_cache_observation_scope_reports_its_range(tmp_path):
    hit = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(
            *check_graph(cache_outcome="hit")), "hit.json")
    )
    record = receipt(capture=hit, check_name="lint", correlation=None)
    assert record["cache_observation_scope"]["status"] == "observed_hit"
    assert record["cache_observation_scope"]["scope"] == ["EngineeringCi.lint"]

    # The two cache outcomes exist at different nodes of one parent chain.
    mixed_spans = list(check_graph(cache_outcome="hit"))
    mixed_spans[-1] = dict(mixed_spans[-1])
    mixed_spans[-1]["attributes"] = [
        {"key": "dagger.io/cache.outcome", "value": {"stringValue": "executed"}}
    ]
    mixed = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(*mixed_spans), "mixed.json")
    )
    record = receipt(capture=mixed, check_name="lint", correlation=None)
    assert record["cache_observation_scope"]["status"] == "mixed"

    none = dagger_telemetry.read_capture(
        write_capture(tmp_path, capture_document(*check_graph()), "none.json")
    )
    record = receipt(capture=none, check_name="lint", correlation=None)
    assert record["cache_observation_scope"]["status"] == "unknown"
    assert receipt()["cache_observation_scope"]["status"] == "not_applicable"

