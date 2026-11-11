"""Live in-process OTLP/gRPC receiver evidence (ECI #75 / PR #81)."""

from concurrent import futures

import grpc
import pytest
from opentelemetry.proto.collector.logs.v1 import (
    logs_service_pb2,
    logs_service_pb2_grpc,
)
from opentelemetry.proto.collector.metrics.v1 import (
    metrics_service_pb2,
    metrics_service_pb2_grpc,
)
from opentelemetry.proto.collector.trace.v1 import (
    trace_service_pb2,
    trace_service_pb2_grpc,
)

from engineering_ci import cicd_telemetry

JOB_START = 1_700_000_010_000_000_000
JOB_END = 1_700_000_090_000_000_000
EXECUTING = {
    "state": "executing",
    "start_ns": JOB_START,
    "end_ns": JOB_END,
}


def complete_facts(**overrides):
    value = {
        "workflow_name": "Engineering CI",
        "run_id": "42627302972",
        "run_attempt": 1,
        "job_id": "129194268575",
        "job_name": "python-ci / checks",
        "job_url": (
            "https://github.com/dymzz/engineering-ci/actions/"
            "runs/42627302972/job/129194268575"
        ),
        "run_conclusion": "success",
        "job_conclusion": "success",
        "head_sha": "a" * 40,
        "repository": "dymzz/engineering-ci",
        "run_started_at_ns": 1_700_000_000_000_000_000,
        "run_completed_at_ns": 1_700_000_120_000_000_000,
        "job_started_at_ns": JOB_START,
        "job_completed_at_ns": JOB_END,
        "run_status": "completed",
    }
    value.update(overrides)
    return cicd_telemetry.facts_from_mapping(value)


class _RecordingTraceService(trace_service_pb2_grpc.TraceServiceServicer):
    def __init__(self, *, reject_spans=0):
        self.requests = []
        self.reject_spans = reject_spans

    def Export(self, request, context):
        self.requests.append(request)
        response = trace_service_pb2.ExportTraceServiceResponse()
        if self.reject_spans:
            response.partial_success.rejected_spans = self.reject_spans
            response.partial_success.error_message = "test reject"
        return response


class _RecordingLogsService(logs_service_pb2_grpc.LogsServiceServicer):
    def __init__(self, *, reject_records=0):
        self.requests = []
        self.reject_records = reject_records

    def Export(self, request, context):
        self.requests.append(request)
        response = logs_service_pb2.ExportLogsServiceResponse()
        if self.reject_records:
            response.partial_success.rejected_log_records = self.reject_records
            response.partial_success.error_message = "test reject logs"
        return response


class _RecordingMetricsService(metrics_service_pb2_grpc.MetricsServiceServicer):
    def __init__(self, *, reject_points=0):
        self.requests = []
        self.reject_points = reject_points

    def Export(self, request, context):
        self.requests.append(request)
        response = metrics_service_pb2.ExportMetricsServiceResponse()
        if self.reject_points:
            response.partial_success.rejected_data_points = self.reject_points
            response.partial_success.error_message = "test reject metrics"
        return response


def _start_server(trace, logs=None, metrics=None):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    trace_service_pb2_grpc.add_TraceServiceServicer_to_server(trace, server)
    if logs is not None:
        logs_service_pb2_grpc.add_LogsServiceServicer_to_server(logs, server)
    if metrics is not None:
        metrics_service_pb2_grpc.add_MetricsServiceServicer_to_server(metrics, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    return server, port


def _string_attr(attributes, key):
    for item in attributes:
        if item.key == key and item.value.HasField("string_value"):
            return item.value.string_value
    return None


def test_live_otlp_grpc_receiver_observes_traces_logs_and_metrics():
    report = cicd_telemetry.produce(complete_facts(), phase_intervals=[EXECUTING])
    cicd_telemetry.attach_signal_candidates(report)
    cicd_telemetry.attach_otlp_json(report)
    traces = _RecordingTraceService()
    logs = _RecordingLogsService()
    metrics = _RecordingMetricsService()
    server, port = _start_server(traces, logs, metrics)
    try:
        endpoint = f"127.0.0.1:{port}"
        cicd_telemetry.export_otlp_grpc(report, endpoint, insecure=True, timeout=5.0)
        assert report["claims"]["rpc_accepted"] is True
        assert report["claims"]["otlp_logs_rpc_accepted"] is True
        assert report["claims"]["otlp_metrics_rpc_accepted"] is True
        assert report["claims"]["otlp_delivered"] is False
        assert report["claims"]["otlp_logs_delivered"] is False
        assert report["claims"]["otlp_metrics_delivered"] is False
        span_ids = []
        trace_id = None
        for resource in traces.requests[0].resource_spans:
            for scope in resource.scope_spans:
                for span in scope.spans:
                    span_ids.append(span.span_id.hex())
                    trace_id = span.trace_id.hex()
        log_span_ids = []
        log_trace_id = None
        for resource in logs.requests[0].resource_logs:
            for scope in resource.scope_logs:
                for record in scope.log_records:
                    log_span_ids.append(record.span_id.hex())
                    log_trace_id = record.trace_id.hex()
        metric_ids = []
        histogram = (
            metrics.requests[0]
            .resource_metrics[0]
            .scope_metrics[0]
            .metrics[0]
            .histogram
        )
        assert histogram.data_points
        for point in histogram.data_points:
            state = _string_attr(point.attributes, "cicd.pipeline.run.state")
            metric_ids.append(f"cicd.pipeline.run.duration|{state}")
            assert _string_attr(point.attributes, "cicd.pipeline.run.id") is None
        facts = report["facts"]
        cicd_telemetry.mark_receiver_observed(
            report,
            signal="traces",
            observed_ids=span_ids,
            rpc_accepted=True,
            run_id=facts["run_id"],
            attempt=facts["run_attempt"],
            trace_id=trace_id,
            repository=facts["repository"],
        )
        assert report["claims"]["snapshot_phase"] == "partial_receiver_confirmation"
        assert report["claims"]["transport"]["traces"]["otlp_delivered"] is True
        assert report["claims"]["otlp_delivered"] is False
        assert report["claims"]["bundle_delivery_complete"] is False
        assert report["claims"]["all_prepared_signals_delivered"] is False
        cicd_telemetry.mark_receiver_observed(
            report,
            signal="logs",
            observed_ids=log_span_ids,
            rpc_accepted=True,
            run_id=facts["run_id"],
            attempt=facts["run_attempt"],
            trace_id=log_trace_id,
            repository=facts["repository"],
        )
        cicd_telemetry.mark_receiver_observed(
            report,
            signal="metrics",
            observed_ids=metric_ids,
            rpc_accepted=True,
            run_id=facts["run_id"],
            attempt=facts["run_attempt"],
            repository=facts["repository"],
        )
        assert report["claims"]["snapshot_phase"] == "receiver_confirmed"
        assert report["claims"]["receiver_observed"] is True
        assert report["claims"]["otlp_delivered"] is True
        assert report["claims"]["otlp_logs_delivered"] is True
        assert report["claims"]["otlp_metrics_delivered"] is True
        assert report["claims"]["candidate_record_only"] is True
        assert report["claims"]["weaver_pass"] is False
        assert report["claims"]["trusted"] is False
        assert report["terminal_provenance"]["terminal_source"] == "mapping"
        assert report["terminal"]["time_precision"] == "exact_input"
        assert report["terminal"]["time_source_verified"] is False
    finally:
        server.stop(grace=0)


def test_live_otlp_grpc_unreachable_endpoint():
    report = cicd_telemetry.produce(complete_facts())
    cicd_telemetry.attach_otlp_json(report)
    with pytest.raises(cicd_telemetry.TelemetryError) as error:
        cicd_telemetry.export_otlp_grpc(
            report, "127.0.0.1:1", insecure=True, timeout=0.5
        )
    assert error.value.code in {
        "otlp_grpc_unavailable_endpoint",
        "otlp_grpc_timeout",
        "otlp_grpc_failed",
    }
    assert report["claims"]["rpc_accepted"] is False


def test_live_otlp_grpc_partial_reject():
    report = cicd_telemetry.produce(complete_facts())
    cicd_telemetry.attach_otlp_json(report)
    servicer = _RecordingTraceService(reject_spans=1)
    server, port = _start_server(servicer)
    try:
        with pytest.raises(cicd_telemetry.TelemetryError) as error:
            cicd_telemetry.export_otlp_grpc(
                report, f"127.0.0.1:{port}", insecure=True, timeout=5.0
            )
        assert error.value.code == "otlp_grpc_partial_reject"
        assert report["claims"]["rpc_accepted"] is False
        assert report["claims"]["otlp_delivered"] is False
    finally:
        server.stop(grace=0)


def test_live_logs_partial_reject_does_not_inherit_trace_ack():
    report = cicd_telemetry.produce(complete_facts())
    cicd_telemetry.attach_signal_candidates(report)
    cicd_telemetry.attach_otlp_json(report)
    traces = _RecordingTraceService()
    logs = _RecordingLogsService(reject_records=1)
    server, port = _start_server(traces, logs)
    try:
        with pytest.raises(cicd_telemetry.TelemetryError) as error:
            cicd_telemetry.export_otlp_grpc(
                report, f"127.0.0.1:{port}", insecure=True, timeout=5.0
            )
        assert error.value.code == "otlp_grpc_partial_reject"
        assert report["claims"]["rpc_accepted"] is True
        assert report["claims"]["otlp_logs_rpc_accepted"] is False
        assert report["claims"]["otlp_delivered"] is False
        assert report["claims"]["otlp_logs_delivered"] is False
    finally:
        server.stop(grace=0)


def test_job_terminal_logs_without_fake_finalizing_metric():
    report = cicd_telemetry.produce(complete_facts())
    cicd_telemetry.attach_signal_candidates(report)
    assert len(report["signals"]["logs"]) == 1
    assert report["signals"]["logs"][0]["attributes"]["eci.log.source"] == (
        "github_job_terminal"
    )
    assert report["otlp_logs_json"] is not None
    cicd_telemetry.decode_otlp_logs_request(report["otlp_logs_json"])
    assert report["signals"]["metrics"] == []
    assert report["otlp_metrics_json"] is None
    assert not any(
        item.get("state") == "finalizing" and "reason" not in item
        for item in report["signals"]["metrics_not_observable"]
    )


def test_observed_root_marks_duration_not_observable():
    report = cicd_telemetry.produce(
        complete_facts(run_completed_at_ns=None, run_status="completed"),
        end_time_mode=cicd_telemetry.END_TIME_OBSERVED_UPPER_BOUND,
        terminal_observed_at_ns=JOB_END + 5_000_000_000,
    )
    cicd_telemetry.attach_signal_candidates(report)
    assert report["otlp_metrics_json"] is None
    assert any(
        item["name"] == "cicd.pipeline.run.duration"
        and item["state"] == "finalizing"
        and item["reason"] == "phase_interval_unproven"
        for item in report["signals"]["metrics_not_observable"]
    )
