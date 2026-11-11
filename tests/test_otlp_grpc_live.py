"""Live in-process OTLP/gRPC TraceService receiver evidence (ECI #75 / PR #81)."""

from concurrent import futures

import grpc
import pytest
from opentelemetry.proto.collector.trace.v1 import (
    trace_service_pb2,
    trace_service_pb2_grpc,
)

from engineering_ci import cicd_telemetry

JOB_END = 1_700_000_090_000_000_000


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
        "job_started_at_ns": 1_700_000_010_000_000_000,
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


def _start_server(servicer):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    trace_service_pb2_grpc.add_TraceServiceServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    return server, port


def test_live_otlp_grpc_receiver_observes_exported_spans():
    report = cicd_telemetry.produce(complete_facts())
    cicd_telemetry.attach_signal_candidates(report)
    cicd_telemetry.attach_otlp_json(report)
    servicer = _RecordingTraceService()
    server, port = _start_server(servicer)
    try:
        endpoint = f"127.0.0.1:{port}"
        cicd_telemetry.export_otlp_grpc(report, endpoint, insecure=True, timeout=5.0)
        assert report["claims"]["rpc_accepted"] is True
        assert len(servicer.requests) == 1
        observed = []
        for resource in servicer.requests[0].resource_spans:
            for scope in resource.scope_spans:
                for span in scope.spans:
                    observed.append(span.span_id.hex())
        assert len(observed) == 2
        cicd_telemetry.mark_receiver_observed(report, observed_span_ids=observed)
        assert report["claims"]["receiver_observed"] is True
        assert report["claims"]["otlp_delivered"] is True
        assert report["claims"]["weaver_pass"] is False
        assert report["claims"]["trusted"] is False
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
    finally:
        server.stop(grace=0)


def test_job_terminal_logs_and_exact_duration_metric():
    report = cicd_telemetry.produce(complete_facts())
    cicd_telemetry.attach_signal_candidates(report)
    assert len(report["signals"]["logs"]) == 1
    assert report["signals"]["logs"][0]["attributes"]["eci.log.source"] == (
        "github_job_terminal"
    )
    assert report["otlp_logs_json"] is not None
    assert any(
        item["name"] == "cicd.pipeline.run.duration"
        for item in report["signals"]["metrics"]
    )
    assert report["otlp_metrics_json"] is not None


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
        and item["reason"] == "exact_run_duration_unproven"
        for item in report["signals"]["metrics_not_observable"]
    )
