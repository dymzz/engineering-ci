"""Tests for Dagger native OTel capture, parent-graph correlation, and A.1 verification."""

from __future__ import annotations

import copy
import gzip
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from google.protobuf import json_format
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from engineering_ci import dagger_telemetry as dt
from engineering_ci.cicd_telemetry import otlp_wire_to_protobuf_json


def _kv(key: str, value: object) -> dict:
    if isinstance(value, bool):
        val = {"boolValue": value}
    elif isinstance(value, int):
        val = {"intValue": str(value)}
    else:
        val = {"stringValue": str(value)}
    return {"key": key, "value": val}


def _span(
    *,
    trace_id: str,
    span_id: str,
    parent_span_id: str = "",
    name: str,
    attributes: dict[str, object] | None = None,
    status_code: int = 1,
) -> dict:
    return {
        "traceId": trace_id,
        "spanId": span_id,
        "parentSpanId": parent_span_id,
        "name": name,
        "kind": 1,
        "startTimeUnixNano": "1000",
        "endTimeUnixNano": "2000",
        "attributes": [_kv(k, v) for k, v in (attributes or {}).items()],
        "status": {"code": status_code},
    }


def _make_check_capture(
    *,
    trace_id: str = "1" * 32,
    target_name: str = "lint",
    camel_fn: str = "lint",
    module_name: str = "engineering-ci",
    session_id: str = "sess-001",
    engine_instance_id: str | None = "eng-001",
    external_parent_id: str = "",
    include_check_sync_hop: bool = True,
    cache_outcome: str = "executed",
    cache_result_id: str = "res-001",
    cache_contract: str = "contract-v1",
    dag_digest: str = "sha256:abc123",
    core_status_code: int = 1,
    isolated_capture: bool = True,
) -> dict:
    resource_attrs = [
        _kv("service.name", "dagger-engine"),
        _kv(dt.DAGGER_SESSION_ID_ATTR, session_id),
    ]
    if engine_instance_id is not None:
        resource_attrs.append(
            _kv(dt.DAGGER_ENGINE_INSTANCE_ID_ATTR, engine_instance_id)
        )

    sel_id = "1000000000000001"
    sync_id = "1000000000000002"
    dagql_id = "1000000000000003"
    core_id = "1000000000000004"

    parallel_spans = [
        _span(
            trace_id=trace_id,
            span_id=sel_id,
            parent_span_id=external_parent_id,
            name=f"dag://?check={target_name}",
        )
    ]
    dagql_spans = []
    dagql_parent = sel_id
    if include_check_sync_hop:
        dagql_spans.append(
            _span(
                trace_id=trace_id,
                span_id=sync_id,
                parent_span_id=sel_id,
                name="Check.sync",
            )
        )
        dagql_parent = sync_id

    dagql_spans.append(
        _span(
            trace_id=trace_id,
            span_id=dagql_id,
            parent_span_id=dagql_parent,
            name=f"{module_name}:EngineeringCi.{camel_fn}",
            attributes={dt.DAGGER_DAG_DIGEST_ATTR: dag_digest},
        )
    )
    core_spans = [
        _span(
            trace_id=trace_id,
            span_id=core_id,
            parent_span_id=dagql_id,
            name=f"EngineeringCi.{camel_fn}",
            attributes={
                dt.DAGGER_CACHE_CONTRACT_ATTR: cache_contract,
                dt.DAGGER_CACHE_OUTCOME_ATTR: cache_outcome,
                dt.DAGGER_CACHE_RESULT_ID_ATTR: cache_result_id,
                dt.DAGGER_CACHE_HIT_ROUTE_ATTR: "none"
                if cache_outcome == "executed"
                else "content",
            },
            status_code=core_status_code,
        )
    ]

    return {
        "isolated_capture": isolated_capture,
        "trace_batches": [
            {
                "batch_index": 0,
                "payload": {
                    "resourceSpans": [
                        {
                            "resource": {"attributes": resource_attrs},
                            "scopeSpans": [
                                {
                                    "scope": {"name": "dagger.io/util/parallel"},
                                    "spans": parallel_spans,
                                },
                                {
                                    "scope": {"name": "dagger.io/dagql"},
                                    "spans": dagql_spans,
                                },
                                {
                                    "scope": {"name": "dagger.io/core"},
                                    "spans": core_spans,
                                },
                            ],
                        }
                    ]
                },
            }
        ],
    }


def _make_artifact_capture(
    *,
    trace_id: str = "2" * 32,
    camel_fn: str = "repositoryRuffTools",
    module_name: str = "engineering-ci",
    session_id: str = "sess-art-001",
    engine_instance_id: str = "eng-001",
    external_parent_id: str = "",
    dag_digest: str = "sha256:art999",
    cache_outcome: str = "hit",
    isolated_capture: bool = True,
) -> dict:
    resource_attrs = [
        _kv("service.name", "dagger-engine"),
        _kv(dt.DAGGER_SESSION_ID_ATTR, session_id),
        _kv(dt.DAGGER_ENGINE_INSTANCE_ID_ATTR, engine_instance_id),
    ]
    dagql_id = "2000000000000001"
    core_id = "2000000000000002"
    export_id = "2000000000000003"
    return {
        "isolated_capture": isolated_capture,
        "trace_batches": [
            {
                "batch_index": 0,
                "payload": {
                    "resourceSpans": [
                        {
                            "resource": {"attributes": resource_attrs},
                            "scopeSpans": [
                                {
                                    "scope": {"name": "dagger.io/dagql"},
                                    "spans": [
                                        _span(
                                            trace_id=trace_id,
                                            span_id=dagql_id,
                                            parent_span_id=external_parent_id,
                                            name=f"{module_name}:EngineeringCi.{camel_fn}",
                                            attributes={
                                                dt.DAGGER_DAG_DIGEST_ATTR: dag_digest
                                            },
                                        )
                                    ],
                                },
                                {
                                    "scope": {"name": "dagger.io/core"},
                                    "spans": [
                                        _span(
                                            trace_id=trace_id,
                                            span_id=core_id,
                                            parent_span_id=dagql_id,
                                            name=f"EngineeringCi.{camel_fn}",
                                            attributes={
                                                dt.DAGGER_CACHE_CONTRACT_ATTR: "contract-art",
                                                dt.DAGGER_CACHE_OUTCOME_ATTR: cache_outcome,
                                                dt.DAGGER_CACHE_RESULT_ID_ATTR: "res-art-1",
                                                dt.DAGGER_CACHE_HIT_ROUTE_ATTR: "content",
                                            },
                                        ),
                                        _span(
                                            trace_id=trace_id,
                                            span_id=export_id,
                                            parent_span_id=dagql_id,
                                            name="Artifact.value",
                                        ),
                                    ],
                                },
                            ],
                        }
                    ]
                },
            }
        ],
    }


def _rewrite_span_ids(batch: dict, prefix: str) -> dict:
    cloned = copy.deepcopy(batch)
    for res in cloned["payload"]["resourceSpans"]:
        for scope in res["scopeSpans"]:
            for sp in scope["spans"]:
                sp["spanId"] = prefix + sp["spanId"][len(prefix) :]
                parent = sp.get("parentSpanId") or ""
                if parent.startswith("10"):
                    sp["parentSpanId"] = prefix + parent[len(prefix) :]
    return cloned


class CorrelationTests(unittest.TestCase):
    def test_bound_check_with_branching_and_facts_separated(self) -> None:
        capture = _make_check_capture(
            include_check_sync_hop=True,
            cache_outcome="executed",
        )
        receipt = dt.correlate_capture(
            capture,
            entry_kind="check",
            target_name="lint",
            exporter_configured="yes",
            check_exit_code=0,
            declared_target_source_ref="refs/heads/main@abc",
            required_observability=True,
        )
        self.assertEqual(receipt["correlation"]["status"], "bound")
        self.assertEqual(receipt["correlation"]["matched_session_id"], "sess-001")
        self.assertEqual(
            receipt["correlation"]["matched_engine_instance_id"], "eng-001"
        )
        self.assertEqual(receipt["claims"]["sent"], "sent")
        self.assertEqual(receipt["claims"]["sent_basis"], "receiver_observation")
        self.assertTrue(receipt["claims"]["receiver_observed"])
        self.assertEqual(receipt["claims"]["trace_invocation_binding"], "bound")
        self.assertNotIn("source_associated", receipt["claims"])
        self.assertEqual(receipt["claims"]["native_check_result"]["status"], "passed")
        self.assertEqual(receipt["source_binding"]["source_binding_status"], "unknown")
        self.assertEqual(
            receipt["source_binding"]["observed_dag_digests"], ["sha256:abc123"]
        )
        self.assertEqual(receipt["cache_observation"]["status"], "observed_execution")
        self.assertEqual(receipt["cache_observation"]["node_level"], "function_only")
        self.assertIsNone(receipt["cache_observation"]["new_tool_execution"])
        self.assertEqual(receipt["origin_execution"]["status"], "unknown")
        self.assertTrue(receipt["observability_gate"]["satisfied"])

    def test_failed_check_preserves_failure_even_when_trace_binds(self) -> None:
        capture = _make_check_capture(core_status_code=2, cache_outcome="executed")
        receipt = dt.correlate_capture(
            capture,
            entry_kind="check",
            target_name="lint",
            exporter_configured="yes",
            check_exit_code=1,
            required_observability=True,
        )
        self.assertEqual(receipt["correlation"]["status"], "bound")
        self.assertEqual(receipt["claims"]["native_check_result"]["status"], "failed")
        self.assertEqual(receipt["claims"]["native_check_result"]["exit_code"], 1)
        self.assertTrue(receipt["observability_gate"]["satisfied"])

    def test_rejects_missing_engine_instance_id_and_cross_session(self) -> None:
        missing_engine = _make_check_capture(engine_instance_id=None)
        res = dt.correlate_capture(
            missing_engine,
            entry_kind="check",
            target_name="lint",
            exporter_configured="yes",
            required_observability=True,
        )
        self.assertEqual(res["correlation"]["status"], "unbound")
        self.assertEqual(res["correlation"]["reason"], "missing_engine_instance_id")
        self.assertFalse(res["observability_gate"]["satisfied"])

        cross = dt._mutate_cross_session_core(_make_check_capture(), "lint")
        res_cross = dt.correlate_capture(
            cross, entry_kind="check", target_name="lint", isolated_capture=True
        )
        self.assertEqual(res_cross["correlation"]["status"], "unbound")
        self.assertEqual(res_cross["correlation"]["reason"], "cross_session_chain")

    def test_concurrent_same_name_checks_ambiguous_vs_traceparent_disambiguated(
        self,
    ) -> None:
        t1 = "a" * 32
        t2 = "b" * 32
        c1 = _make_check_capture(
            trace_id=t1,
            session_id="sess-one",
            engine_instance_id="eng-one",
            external_parent_id="aaaa000000000001",
        )
        c2 = _make_check_capture(
            trace_id=t2,
            session_id="sess-two",
            engine_instance_id="eng-one",
            external_parent_id="bbbb000000000002",
        )
        c2["trace_batches"][0] = _rewrite_span_ids(c2["trace_batches"][0], "99")
        combined = {
            "isolated_capture": True,
            "trace_batches": [c1["trace_batches"][0], c2["trace_batches"][0]],
        }

        amb = dt.correlate_capture(combined, entry_kind="check", target_name="lint")
        self.assertEqual(amb["correlation"]["status"], "ambiguous")

        bound_tp = dt.correlate_capture(
            combined,
            entry_kind="check",
            target_name="lint",
            traceparent=f"00-{t2}-bbbb000000000002-01",
        )
        self.assertEqual(bound_tp["correlation"]["status"], "bound")
        self.assertEqual(bound_tp["correlation"]["matched_session_id"], "sess-two")

        # Matching trace_id WITHOUT matching propagated parent_span_id must NOT bind.
        bad_parent = dt.correlate_capture(
            combined,
            entry_kind="check",
            target_name="lint",
            traceparent=f"00-{t2}-cccccccccccccccc-01",
        )
        self.assertEqual(bad_parent["correlation"]["status"], "unbound")
        self.assertEqual(
            bad_parent["correlation"]["reason"], "traceparent_parent_not_in_lineage"
        )

    def test_artifact_function_entry_and_no_check_impersonation(self) -> None:
        art = _make_artifact_capture()
        receipt = dt.correlate_capture(
            art,
            entry_kind="artifact_function",
            target_name="repository-ruff-tools",
            exporter_configured="yes",
            required_observability=True,
        )
        self.assertEqual(receipt["correlation"]["status"], "bound")
        self.assertEqual(receipt["target"]["camel_function"], "repositoryRuffTools")
        self.assertEqual(receipt["cache_observation"]["status"], "observed_hit")
        self.assertIs(receipt["cache_observation"]["new_tool_execution"], False)
        self.assertEqual(receipt["origin_execution"]["status"], "unknown")
        self.assertTrue(receipt["observability_gate"]["satisfied"])

        # Linked prior origin only succeeds with matching contract/result_id/scope.
        linked = dt.correlate_capture(
            art,
            entry_kind="artifact_function",
            target_name="repository-ruff-tools",
            prior_executions=[
                {
                    "trace_id": "9" * 32,
                    "span_id": "8" * 16,
                    "session_id": "sess-prev",
                    "cache_result_id": "res-art-1",
                    "contract": "contract-art",
                    "node_level": "function_only",
                }
            ],
        )
        self.assertEqual(
            linked["origin_execution"]["status"], "existing_execution_linked"
        )

        # Check capture cannot impersonate artifact function entry.
        check_cap = _make_check_capture(target_name="lint", camel_fn="lint")
        impersonate = dt.correlate_capture(
            check_cap,
            entry_kind="artifact_function",
            target_name="lint",
            isolated_capture=True,
        )
        self.assertEqual(impersonate["correlation"]["status"], "unbound")
        self.assertEqual(
            impersonate["correlation"]["reason"], "unexpected_check_selector_ancestor"
        )

    def test_verify_a1_suite_passes_on_valid_trio_and_rejects_fake_bound(self) -> None:
        pass_cap = _make_check_capture(core_status_code=1)
        pass_rec = dt.correlate_capture(
            pass_cap,
            entry_kind="check",
            target_name="lint",
            exporter_configured="yes",
            check_exit_code=0,
            required_observability=True,
        )
        fail_cap = _make_check_capture(trace_id="3" * 32, core_status_code=2)
        fail_rec = dt.correlate_capture(
            fail_cap,
            entry_kind="check",
            target_name="lint",
            exporter_configured="yes",
            check_exit_code=1,
            required_observability=True,
        )
        art_cap = _make_artifact_capture()
        art_rec = dt.correlate_capture(
            art_cap,
            entry_kind="artifact_function",
            target_name="repository-ruff-tools",
            exporter_configured="yes",
            required_observability=True,
        )
        report = dt.verify_a1_suite(
            pass_capture=pass_cap,
            pass_receipt=pass_rec,
            fail_capture=fail_cap,
            fail_receipt=fail_rec,
            artifact_capture=art_cap,
            artifact_receipt=art_rec,
        )
        self.assertTrue(report["passed"], report["failures"])

        fake_pass = copy.deepcopy(pass_rec)
        fake_pass["source_binding"]["source_binding_status"] = "verified"
        bad_report = dt.verify_a1_suite(
            pass_capture=pass_cap,
            pass_receipt=fake_pass,
            fail_capture=fail_cap,
            fail_receipt=fail_rec,
            artifact_capture=art_cap,
            artifact_receipt=art_rec,
        )
        self.assertFalse(bad_report["passed"])


class OtlpReceiverTests(unittest.TestCase):
    def test_concurrent_protobuf_json_gzip_metrics_and_atomic_write(self) -> None:
        cap = _make_check_capture()
        wire_payload = cap["trace_batches"][0]["payload"]
        pb_dict = otlp_wire_to_protobuf_json(copy.deepcopy(wire_payload))
        proto_req = json_format.ParseDict(pb_dict, ExportTraceServiceRequest())
        proto_bytes = proto_req.SerializeToString()

        metrics_req = ExportMetricsServiceRequest()
        metrics_req.resource_metrics.add()
        metrics_bytes = metrics_req.SerializeToString()

        server = dt.OtlpHttpCaptureServer(host="127.0.0.1", port=0)
        server.start()
        errors: list[Exception] = []
        try:
            base = f"http://{server.host}:{server.port}"

            def post(path: str, data: bytes, headers: dict[str, str]) -> None:
                try:
                    req = urllib.request.Request(
                        f"{base}{path}", data=data, headers=headers, method="POST"
                    )
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        self.assertEqual(resp.status, 200)
                except (
                    OSError,
                    urllib.error.URLError,
                    AssertionError,
                ) as exc:  # pragma: no cover
                    errors.append(exc)

            threads = [
                threading.Thread(
                    target=post,
                    args=(
                        "/v1/traces",
                        proto_bytes,
                        {"Content-Type": "application/x-protobuf"},
                    ),
                )
                for _ in range(4)
            ]
            threads.append(
                threading.Thread(
                    target=post,
                    args=(
                        "/v1/traces",
                        gzip.compress(json.dumps(wire_payload).encode("utf-8")),
                        {
                            "Content-Type": "application/json",
                            "Content-Encoding": "gzip",
                        },
                    ),
                )
            )
            threads.append(
                threading.Thread(
                    target=post,
                    args=(
                        "/v1/metrics",
                        metrics_bytes,
                        {"Content-Type": "application/x-protobuf"},
                    ),
                )
            )
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
            self.assertFalse(errors, errors)

            # Invalid OTLP payload should return HTTP 400 cleanly.
            bad_req = urllib.request.Request(
                f"{base}/v1/traces",
                data=b"not-valid-json",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(bad_req, timeout=5)
            self.assertEqual(ctx.exception.code, 400)

            with tempfile.TemporaryDirectory() as tmpdir:
                out_file = Path(tmpdir) / "capture.json"
                snapshot = server.stop(output_path=out_file)
                self.assertEqual(len(snapshot["trace_batches"]), 5)
                self.assertEqual(len(snapshot["metric_batches"]), 1)
                loaded = json.loads(out_file.read_text(encoding="utf-8"))
                self.assertEqual(len(loaded["trace_batches"]), 5)
                # All spans should parse cleanly after atomic seal.
                parsed = dt.parse_capture(out_file)
                self.assertEqual(len(parsed.spans), 20)
        finally:
            if server._thread is not None:
                server.stop()


if __name__ == "__main__":
    unittest.main()
