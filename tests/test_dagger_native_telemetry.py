"""Tests for Dagger native OTel capture, parent-graph correlation, and A.1 verification."""

from __future__ import annotations

import copy
import gzip
import hashlib
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

    scope_meta = (
        {
            "receiver_observed": True,
            "capture_scope": {
                "scope_kind": "controlled_isolated_receiver",
                "evidence_level": "controlled_single_invocation",
                "source": "ephemeral_otlp_http_receiver",
                "single_invocation_window": True,
            },
        }
        if isolated_capture
        else {}
    )
    return {
        "isolated_capture": isolated_capture,
        **scope_meta,
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
    include_export_file_span: bool = True,
    export_filename: str = "tool-versions.json",
    export_host_path: str | None = None,
) -> dict:
    resource_attrs = [
        _kv("service.name", "dagger-engine"),
        _kv(dt.DAGGER_SESSION_ID_ATTR, session_id),
        _kv(dt.DAGGER_ENGINE_INSTANCE_ID_ATTR, engine_instance_id),
    ]
    dagql_id = "2000000000000001"
    core_id = "2000000000000002"
    export_id = "2000000000000003"
    export_file_id = "2000000000000004"
    core_spans = [
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
            name="File.export",
        ),
    ]
    if include_export_file_span:
        export_span_name = (
            f"export file {export_filename} to host {export_host_path}"
            if export_host_path
            else f"export file {export_filename}"
        )
        core_spans.append(
            _span(
                trace_id=trace_id,
                span_id=export_file_id,
                parent_span_id=export_id,
                name=export_span_name,
            )
        )
    scope_meta = (
        {
            "receiver_observed": True,
            "capture_scope": {
                "scope_kind": "controlled_isolated_receiver",
                "evidence_level": "controlled_single_invocation",
                "source": "ephemeral_otlp_http_receiver",
                "single_invocation_window": True,
            },
        }
        if isolated_capture
        else {}
    )
    return {
        "isolated_capture": isolated_capture,
        **scope_meta,
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
                                    "spans": core_spans,
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
            "receiver_observed": True,
            "capture_scope": c1["capture_scope"],
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

    def test_unisolated_expected_session_id_does_not_prove_invocation(self) -> None:
        capture = _make_check_capture(
            trace_id="4" * 32,
            session_id="sess-shared",
            engine_instance_id="eng-shared",
            external_parent_id="aaaa111111111111",
            isolated_capture=False,
        )
        # Expected session_id filters candidates, NOT an invocation-level proof.
        rejected = dt.correlate_capture(
            capture,
            entry_kind="check",
            target_name="lint",
            expected_session_id="sess-shared",
            isolated_capture=False,
        )
        self.assertEqual(rejected["correlation"]["status"], "unbound")
        self.assertEqual(
            rejected["correlation"]["reason"],
            "unisolated_capture_without_traceparent",
        )

        # Same unisolated data with propagated traceparent binds cleanly.
        bound = dt.correlate_capture(
            capture,
            entry_kind="check",
            target_name="lint",
            expected_session_id="sess-shared",
            traceparent=f"00-{'4' * 32}-aaaa111111111111-01",
            isolated_capture=False,
        )
        self.assertEqual(bound["correlation"]["status"], "bound")
        self.assertEqual(bound["correlation"]["reason"], "matched_traceparent")

    def test_same_trace_export_span_attribution_does_not_cross_functions(self) -> None:
        trace_id = "5" * 32
        query_id = "5000000000000001"
        core_a = "5000000000000002"
        dagql_a = "5000000000000003"
        core_b = "5000000000000004"
        dagql_b = "5000000000000005"
        export_b = "5000000000000006"
        resume_b = "5000000000000007"
        capture = {
            "isolated_capture": True,
            "receiver_observed": True,
            "capture_scope": {
                "scope_kind": "controlled_isolated_receiver",
                "evidence_level": "controlled_single_invocation",
                "source": "ephemeral_otlp_http_receiver",
                "single_invocation_window": True,
            },
            "trace_batches": [
                {
                    "batch_index": 0,
                    "payload": {
                        "resourceSpans": [
                            {
                                "resource": {
                                    "attributes": [
                                        _kv("service.name", "dagger-engine"),
                                        _kv(dt.DAGGER_SESSION_ID_ATTR, "sess-multi"),
                                        _kv(dt.DAGGER_ENGINE_INSTANCE_ID_ATTR, "eng-1"),
                                    ]
                                },
                                "scopeSpans": [
                                    {
                                        "scope": {"name": dt.DAGQL_SCOPE_NAME},
                                        "spans": [
                                            _span(
                                                trace_id=trace_id,
                                                span_id=dagql_a,
                                                parent_span_id=core_a,
                                                name="engineering-ci:EngineeringCi.repositoryRuffTools",
                                            ),
                                            _span(
                                                trace_id=trace_id,
                                                span_id=dagql_b,
                                                parent_span_id=core_b,
                                                name="engineering-ci:EngineeringCi.repositoryTestArtifacts",
                                            ),
                                        ],
                                    },
                                    {
                                        "scope": {"name": dt.CORE_SCOPE_NAME},
                                        "spans": [
                                            _span(
                                                trace_id=trace_id,
                                                span_id=query_id,
                                                parent_span_id="",
                                                name="POST /query",
                                            ),
                                            _span(
                                                trace_id=trace_id,
                                                span_id=core_a,
                                                parent_span_id=query_id,
                                                name="EngineeringCi.repositoryRuffTools",
                                            ),
                                            _span(
                                                trace_id=trace_id,
                                                span_id=core_b,
                                                parent_span_id=query_id,
                                                name="EngineeringCi.repositoryTestArtifacts",
                                            ),
                                            _span(
                                                trace_id=trace_id,
                                                span_id=export_b,
                                                parent_span_id=query_id,
                                                name="File.export",
                                            ),
                                            {
                                                **_span(
                                                    trace_id=trace_id,
                                                    span_id=resume_b,
                                                    parent_span_id=export_b,
                                                    name="resume file",
                                                ),
                                                "links": [
                                                    {
                                                        "traceId": trace_id,
                                                        "spanId": query_id,
                                                        "attributes": [
                                                            _kv(
                                                                "dagger.io/link.purpose",
                                                                "cause",
                                                            )
                                                        ],
                                                    },
                                                    {
                                                        "traceId": trace_id,
                                                        "spanId": core_b,
                                                        "attributes": [
                                                            _kv(
                                                                "dagger.io/link.purpose",
                                                                "cause",
                                                            )
                                                        ],
                                                    },
                                                ],
                                            },
                                        ],
                                    },
                                ],
                            }
                        ]
                    },
                }
            ],
        }

        rec_a = dt.correlate_capture(
            capture,
            entry_kind="artifact_function",
            target_name="repository-ruff-tools",
            artifact_exit_code=0,
            artifact_sha256="deadbeef",
        )
        self.assertEqual(rec_a["correlation"]["status"], "bound")
        art_a = rec_a["claims"]["native_artifact_result"]
        self.assertTrue(art_a["export_span_observed"])
        self.assertEqual(art_a["export_attribution_status"], "unknown")
        self.assertEqual(
            art_a["export_attribution_basis"], "unattributed_export_in_trace"
        )
        self.assertIsNone(art_a["attributed_export_span_id"])

        rec_b = dt.correlate_capture(
            capture,
            entry_kind="artifact_function",
            target_name="repository-test-artifacts",
            artifact_exit_code=0,
            artifact_sha256="deadbeef",
        )
        self.assertEqual(rec_b["correlation"]["status"], "bound")
        art_b = rec_b["claims"]["native_artifact_result"]
        self.assertTrue(art_b["export_span_observed"])
        self.assertEqual(art_b["export_attribution_status"], "attributed")
        self.assertEqual(art_b["export_attribution_basis"], "span_link")
        self.assertEqual(art_b["attributed_export_span_id"], export_b)

    def test_beta16_capabilities_distinguishes_traceparent_layers(self) -> None:
        caps = dt.get_beta16_capabilities()
        tp = caps["traceparent_propagation"]
        self.assertEqual(tp["correlator_parsing_and_matching"], "supported")
        self.assertEqual(tp["engine_end_to_end_parent_propagation"], "unverified")

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

        # Unverified self-asserted prior_executions must NOT auto-link origin in A.1.
        unverified_prior = dt.correlate_capture(
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
        self.assertEqual(unverified_prior["origin_execution"]["status"], "unknown")

        # Missing dagql/core scope must not bind (P2-1).
        unscoped_dagql = dt.correlate_capture(
            dt._mutate_strip_scope(art, dt.DAGQL_SCOPE_NAME),
            entry_kind="artifact_function",
            target_name="repository-ruff-tools",
            isolated_capture=True,
        )
        self.assertEqual(unscoped_dagql["correlation"]["status"], "unbound")
        self.assertEqual(
            unscoped_dagql["correlation"]["reason"], "missing_or_wrong_dagql_scope"
        )

        unscoped_core = dt.correlate_capture(
            dt._mutate_strip_scope(art, dt.CORE_SCOPE_NAME),
            entry_kind="artifact_function",
            target_name="repository-ruff-tools",
            isolated_capture=True,
        )
        self.assertEqual(unscoped_core["correlation"]["status"], "unbound")
        self.assertEqual(
            unscoped_core["correlation"]["reason"], "missing_or_wrong_core_scope"
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

    def test_verify_a1_suite_reconciles_identity_and_native_sources(self) -> None:
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
        with tempfile.TemporaryDirectory() as tmpdir:
            art_file = Path(tmpdir) / "tool-versions.json"
            art_file.write_text('{"ruff": "0.16.10"}\n', encoding="utf-8")
            art_sha = dt.sha256_file(art_file)
            art_cap = _make_artifact_capture(export_host_path=art_file.as_posix())
            art_rec = dt.correlate_capture(
                art_cap,
                entry_kind="artifact_function",
                target_name="repository-ruff-tools",
                exporter_configured="yes",
                artifact_exit_code=0,
                artifact_path=art_file,
                artifact_sha256=art_sha,
                required_observability=True,
            )
            fail_log_text = (
                "F401 [*] `os` imported but unused\n"
                " --> src/engineering_ci/_ci_negative_lint_tmp.py:1:8\n"
            )

            report = dt.verify_a1_suite(
                pass_capture=pass_cap,
                pass_receipt=pass_rec,
                fail_capture=fail_cap,
                fail_receipt=fail_rec,
                artifact_capture=art_cap,
                artifact_receipt=art_rec,
                pass_exit_code=0,
                fail_exit_code=1,
                fail_log=fail_log_text,
                fail_diagnostic_pattern=r"F401.*_ci_negative_lint_tmp\.py",
                artifact_exit_code=0,
                artifact_path=art_file,
                artifact_expected_sha256=art_sha,
            )
            self.assertTrue(report["passed"], report["failures"])
            self.assertEqual(
                report["negative_controls"][
                    "unisolated_expected_session_without_traceparent"
                ],
                "unbound",
            )
            self.assertEqual(
                report["negative_controls"]["unattributed_export_in_trace"],
                "unknown",
            )
            self.assertEqual(
                report["negative_controls"]["non_cause_export_link"],
                "unknown",
            )
            self.assertEqual(
                report["negative_controls"]["missing_native_artifact_identity"],
                "False",
            )
            self.assertEqual(
                report["negative_controls"]["non_isolated_override_rejected"],
                "unbound",
            )

            base_kwargs = {
                "pass_capture": pass_cap,
                "pass_receipt": pass_rec,
                "fail_capture": fail_cap,
                "fail_receipt": fail_rec,
                "artifact_capture": art_cap,
                "artifact_receipt": art_rec,
                "pass_exit_code": 0,
                "fail_exit_code": 1,
                "fail_log": fail_log_text,
                "fail_diagnostic_pattern": r"F401.*_ci_negative_lint_tmp\.py",
                "artifact_exit_code": 0,
                "artifact_path": art_file,
                "artifact_expected_sha256": art_sha,
            }

            # P1-1: forged trace/span IDs on receipt must fail even if status="bound".
            forged_id = copy.deepcopy(pass_rec)
            forged_id["correlation"]["matched_trace_id"] = "f" * 32
            self.assertFalse(
                dt.verify_a1_suite(**{**base_kwargs, "pass_receipt": forged_id})[
                    "passed"
                ]
            )

            forged_span = copy.deepcopy(pass_rec)
            forged_span["correlation"]["core_span_id"] = "f" * 16
            self.assertFalse(
                dt.verify_a1_suite(**{**base_kwargs, "pass_receipt": forged_span})[
                    "passed"
                ]
            )

            # P1-1: cross-capture swap (fail receipt submitted for pass capture) must fail.
            self.assertFalse(
                dt.verify_a1_suite(**{**base_kwargs, "pass_receipt": fail_rec})[
                    "passed"
                ]
            )

            # P1-2: missing F401 diagnostic or zero fail exit must fail.
            self.assertFalse(
                dt.verify_a1_suite(
                    **{**base_kwargs, "fail_log": "unexpected environment error"}
                )["passed"]
            )
            self.assertFalse(
                dt.verify_a1_suite(**{**base_kwargs, "fail_exit_code": 0})["passed"]
            )

            # P1-2: artifact SHA-256 mismatch must fail.
            self.assertFalse(
                dt.verify_a1_suite(
                    **{**base_kwargs, "artifact_expected_sha256": "0" * 64}
                )["passed"]
            )

            # P1-3: forged unverified origin execution linkage must fail.
            forged_origin = copy.deepcopy(art_rec)
            forged_origin["origin_execution"] = {
                "status": "existing_execution_linked",
                "matched_origin": {"cache_result_id": "res-art-1"},
            }
            self.assertFalse(
                dt.verify_a1_suite(
                    **{**base_kwargs, "artifact_receipt": forged_origin}
                )["passed"]
            )

            # Source binding cannot be forged to verified.
            fake_pass = copy.deepcopy(pass_rec)
            fake_pass["source_binding"]["source_binding_status"] = "verified"
            self.assertFalse(
                dt.verify_a1_suite(**{**base_kwargs, "pass_receipt": fake_pass})[
                    "passed"
                ]
            )

            # Export span present in trace without function attribution must fail verify_a1_suite.
            unattr_art_cap = dt._mutate_unattribute_export(art_cap)
            unattr_art_rec = dt.correlate_capture(
                unattr_art_cap,
                entry_kind="artifact_function",
                target_name="repository-ruff-tools",
                exporter_configured="yes",
                artifact_exit_code=0,
                artifact_path=art_file,
                artifact_sha256=art_sha,
                required_observability=True,
            )
            self.assertFalse(
                dt.verify_a1_suite(
                    **{
                        **base_kwargs,
                        "artifact_capture": unattr_art_cap,
                        "artifact_receipt": unattr_art_rec,
                    }
                )["passed"]
            )


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
                self.assertEqual(
                    snapshot["capture_format"], "decoded_normalized_otlp_json"
                )
                self.assertEqual(len(snapshot["trace_batches"]), 5)
                self.assertEqual(len(snapshot["metric_batches"]), 1)
                expected_proto_sha = hashlib.sha256(proto_bytes).hexdigest()
                proto_batches = [
                    b
                    for b in snapshot["trace_batches"]
                    if b["content_type"] == "application/x-protobuf"
                ]
                self.assertEqual(len(proto_batches), 4)
                for batch in proto_batches:
                    self.assertEqual(
                        batch["payload_representation"],
                        "decoded_normalized_otlp_json",
                    )
                    self.assertEqual(batch["content_encoding"], "identity")
                    self.assertEqual(batch["raw_payload_sha256"], expected_proto_sha)
                    self.assertEqual(batch["raw_payload_bytes"], len(proto_bytes))
                gzip_batches = [
                    b
                    for b in snapshot["trace_batches"]
                    if b["content_encoding"] == "gzip"
                ]
                self.assertEqual(len(gzip_batches), 1)
                loaded = json.loads(out_file.read_text(encoding="utf-8"))
                self.assertEqual(len(loaded["trace_batches"]), 5)
                self.assertEqual(
                    loaded["capture_scope"]["scope_kind"],
                    "controlled_isolated_receiver",
                )
                # All spans should parse cleanly after atomic seal.
                parsed = dt.parse_capture(out_file)
                self.assertEqual(len(parsed.spans), 20)
        finally:
            if server._thread is not None:
                server.stop()

    def test_non_cause_link_does_not_prove_export_causality_or_attribution(
        self,
    ) -> None:
        trace_id = "6" * 32
        query_id = "6000000000000001"
        core_id = "6000000000000002"
        dagql_id = "6000000000000003"
        export_id = "6000000000000004"
        resume_id = "6000000000000005"
        capture = {
            "isolated_capture": True,
            "receiver_observed": True,
            "capture_scope": {
                "scope_kind": "controlled_isolated_receiver",
                "evidence_level": "controlled_single_invocation",
                "source": "ephemeral_otlp_http_receiver",
                "single_invocation_window": True,
            },
            "trace_batches": [
                {
                    "batch_index": 0,
                    "payload": {
                        "resourceSpans": [
                            {
                                "resource": {
                                    "attributes": [
                                        _kv("service.name", "dagger-engine"),
                                        _kv(dt.DAGGER_SESSION_ID_ATTR, "sess-noncause"),
                                        _kv(
                                            dt.DAGGER_ENGINE_INSTANCE_ID_ATTR, "eng-nc"
                                        ),
                                    ]
                                },
                                "scopeSpans": [
                                    {
                                        "scope": {"name": dt.DAGQL_SCOPE_NAME},
                                        "spans": [
                                            _span(
                                                trace_id=trace_id,
                                                span_id=dagql_id,
                                                parent_span_id=core_id,
                                                name="engineering-ci:EngineeringCi.repositoryRuffTools",
                                            )
                                        ],
                                    },
                                    {
                                        "scope": {"name": dt.CORE_SCOPE_NAME},
                                        "spans": [
                                            _span(
                                                trace_id=trace_id,
                                                span_id=query_id,
                                                parent_span_id="",
                                                name="POST /query",
                                            ),
                                            _span(
                                                trace_id=trace_id,
                                                span_id=core_id,
                                                parent_span_id=query_id,
                                                name="EngineeringCi.repositoryRuffTools",
                                            ),
                                            _span(
                                                trace_id=trace_id,
                                                span_id=export_id,
                                                parent_span_id=query_id,
                                                name="File.export",
                                            ),
                                            {
                                                **_span(
                                                    trace_id=trace_id,
                                                    span_id=resume_id,
                                                    parent_span_id=export_id,
                                                    name="resume file",
                                                ),
                                                "links": [
                                                    {
                                                        "traceId": trace_id,
                                                        "spanId": core_id,
                                                        "attributes": [
                                                            _kv(
                                                                "dagger.io/link.purpose",
                                                                "related",
                                                            )
                                                        ],
                                                    }
                                                ],
                                            },
                                            _span(
                                                trace_id=trace_id,
                                                span_id="6000000000000006",
                                                parent_span_id=export_id,
                                                name="export file tool-versions.json to host /tmp/tool-versions.json",
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
        receipt = dt.correlate_capture(
            capture,
            entry_kind="artifact_function",
            target_name="repository-ruff-tools",
        )
        art = receipt["claims"]["native_artifact_result"]
        self.assertTrue(art["export_span_observed"])
        self.assertEqual(art["export_causality_status"], "observed_unlinked")
        self.assertEqual(art["export_attribution_status"], "unknown")
        self.assertIsNone(art["attributed_export_span_id"])
        self.assertFalse(art["artifact_binding_verified"])

    def test_multi_function_cause_links_on_single_export_mark_ambiguous(self) -> None:
        trace_id = "7" * 32
        query_id = "7000000000000001"
        core_a = "7000000000000002"
        dagql_a = "7000000000000003"
        core_b = "7000000000000004"
        dagql_b = "7000000000000005"
        export_id = "7000000000000006"
        resume_id = "7000000000000007"
        capture = {
            "isolated_capture": True,
            "receiver_observed": True,
            "capture_scope": {
                "scope_kind": "controlled_isolated_receiver",
                "evidence_level": "controlled_single_invocation",
                "source": "ephemeral_otlp_http_receiver",
                "single_invocation_window": True,
            },
            "trace_batches": [
                {
                    "batch_index": 0,
                    "payload": {
                        "resourceSpans": [
                            {
                                "resource": {
                                    "attributes": [
                                        _kv("service.name", "dagger-engine"),
                                        _kv(dt.DAGGER_SESSION_ID_ATTR, "sess-ambig"),
                                        _kv(
                                            dt.DAGGER_ENGINE_INSTANCE_ID_ATTR,
                                            "eng-ambig",
                                        ),
                                    ]
                                },
                                "scopeSpans": [
                                    {
                                        "scope": {"name": dt.DAGQL_SCOPE_NAME},
                                        "spans": [
                                            _span(
                                                trace_id=trace_id,
                                                span_id=dagql_a,
                                                parent_span_id=core_a,
                                                name="engineering-ci:EngineeringCi.repositoryRuffTools",
                                            ),
                                            _span(
                                                trace_id=trace_id,
                                                span_id=dagql_b,
                                                parent_span_id=core_b,
                                                name="engineering-ci:EngineeringCi.repositoryTestArtifacts",
                                            ),
                                        ],
                                    },
                                    {
                                        "scope": {"name": dt.CORE_SCOPE_NAME},
                                        "spans": [
                                            _span(
                                                trace_id=trace_id,
                                                span_id=query_id,
                                                parent_span_id="",
                                                name="POST /query",
                                            ),
                                            _span(
                                                trace_id=trace_id,
                                                span_id=core_a,
                                                parent_span_id=query_id,
                                                name="EngineeringCi.repositoryRuffTools",
                                            ),
                                            _span(
                                                trace_id=trace_id,
                                                span_id=core_b,
                                                parent_span_id=query_id,
                                                name="EngineeringCi.repositoryTestArtifacts",
                                            ),
                                            _span(
                                                trace_id=trace_id,
                                                span_id=export_id,
                                                parent_span_id=query_id,
                                                name="File.export",
                                            ),
                                            {
                                                **_span(
                                                    trace_id=trace_id,
                                                    span_id=resume_id,
                                                    parent_span_id=export_id,
                                                    name="resume file",
                                                ),
                                                "links": [
                                                    {
                                                        "traceId": trace_id,
                                                        "spanId": core_a,
                                                        "attributes": [
                                                            _kv(
                                                                "dagger.io/link.purpose",
                                                                "cause",
                                                            )
                                                        ],
                                                    },
                                                    {
                                                        "traceId": trace_id,
                                                        "spanId": core_b,
                                                        "attributes": [
                                                            _kv(
                                                                "dagger.io/link.purpose",
                                                                "cause",
                                                            )
                                                        ],
                                                    },
                                                ],
                                            },
                                        ],
                                    },
                                ],
                            }
                        ]
                    },
                }
            ],
        }
        receipt = dt.correlate_capture(
            capture,
            entry_kind="artifact_function",
            target_name="repository-ruff-tools",
        )
        art = receipt["claims"]["native_artifact_result"]
        self.assertTrue(art["export_span_observed"])
        self.assertEqual(art["export_causality_status"], "causally_linked")
        self.assertEqual(art["export_attribution_status"], "ambiguous")
        self.assertEqual(art["export_attribution_basis"], "multiple_cause_targets")
        self.assertFalse(art["artifact_binding_verified"])

    def test_missing_local_file_with_self_reported_sha256_cannot_claim_exported(
        self,
    ) -> None:
        capture = _make_artifact_capture()
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_file = Path(tmpdir) / "tool-versions.json"
            receipt = dt.correlate_capture(
                capture,
                entry_kind="artifact_function",
                target_name="repository-ruff-tools",
                artifact_exit_code=0,
                artifact_path=missing_file,
                artifact_sha256="a" * 64,
            )
        art = receipt["claims"]["native_artifact_result"]
        self.assertEqual(art["export_attribution_status"], "attributed")
        self.assertEqual(art["status"], "unknown")
        self.assertEqual(art["status_reason"], "missing_local_artifact_file")
        self.assertFalse(art["local_file_verified"])
        self.assertIsNone(art["artifact_sha256"])
        self.assertEqual(art["declared_artifact_sha256"], "a" * 64)
        self.assertFalse(art["artifact_binding_verified"])

    def test_generic_file_export_without_concrete_target_keeps_binding_unverified(
        self,
    ) -> None:
        capture = _make_artifact_capture(include_export_file_span=False)
        with tempfile.TemporaryDirectory() as tmpdir:
            art_file = Path(tmpdir) / "tool-versions.json"
            art_file.write_text('{"ruff":"0.15.8"}\n', encoding="utf-8")
            art_sha = dt.sha256_file(art_file)
            receipt = dt.correlate_capture(
                capture,
                entry_kind="artifact_function",
                target_name="repository-ruff-tools",
                artifact_exit_code=0,
                artifact_path=art_file,
                artifact_sha256=art_sha,
            )
        art = receipt["claims"]["native_artifact_result"]
        self.assertEqual(art["export_attribution_status"], "attributed")
        self.assertTrue(art["local_file_verified"])
        self.assertEqual(art["status"], "exported")
        self.assertIs(art["export_target_matched"], False)
        self.assertFalse(art["artifact_binding_verified"])

    def test_caller_isolated_true_cannot_override_non_isolated_or_bare_capture(
        self,
    ) -> None:
        for base_flag, expected_level in (
            (False, "unisolated"),
            (None, "caller_declared_only_rejected"),
        ):
            cap = _make_check_capture(isolated_capture=False)
            cap["isolated_capture"] = base_flag
            receipt = dt.correlate_capture(
                cap,
                entry_kind="check",
                target_name="lint",
                isolated_capture=True,
                expected_session_id="sess-lint-001",
            )
            self.assertEqual(receipt["correlation"]["status"], "unbound")
            self.assertIs(
                receipt["correlation"]["isolation_evidence"]["override_rejected"],
                True,
            )
            self.assertEqual(
                receipt["correlation"]["isolation_evidence"]["evidence_level"],
                expected_level,
            )

        bare_true = _make_check_capture(isolated_capture=True)
        bare_true.pop("receiver_observed", None)
        bare_true.pop("capture_scope", None)
        bare_receipt = dt.correlate_capture(
            bare_true,
            entry_kind="check",
            target_name="lint",
            isolated_capture=True,
        )
        self.assertEqual(bare_receipt["correlation"]["status"], "unbound")
        self.assertIs(
            bare_receipt["correlation"]["isolation_evidence"]["override_rejected"],
            True,
        )
        self.assertEqual(
            bare_receipt["correlation"]["isolation_evidence"]["evidence_level"],
            "capture_declared_only_unverified",
        )

    def test_select_a1_capture_scope_covers_cli_package_and_marks_offline_honestly(
        self,
    ) -> None:
        cli_decision = dt.select_a1_capture_scope(
            ["src/engineering_ci/cli/__init__.py"]
        )
        self.assertTrue(cli_decision["run_live_capture"])
        self.assertEqual(
            cli_decision["matched_paths"], ["src/engineering_ci/cli/__init__.py"]
        )

        doc_decision = dt.select_a1_capture_scope(["README.md"])
        self.assertFalse(doc_decision["run_live_capture"])
        self.assertEqual(doc_decision["evidence_level"], "offline_contract_only")

        with tempfile.TemporaryDirectory() as tmpdir:
            changed_list = Path(tmpdir) / "changed.txt"
            changed_list.write_text(
                "src/engineering_ci/cli/release.py\n", encoding="utf-8"
            )
            gh_out = Path(tmpdir) / "github_output.txt"
            exit_code = dt.main(
                [
                    "a1-scope",
                    "--changed-files-file",
                    str(changed_list),
                    "--github-output",
                    str(gh_out),
                ]
            )
            self.assertEqual(exit_code, 0)
            gh_text = gh_out.read_text(encoding="utf-8")
            self.assertIn("run_live_capture=true", gh_text)

    def test_explicit_mismatched_host_path_preserves_attribution_without_binding(
        self,
    ) -> None:
        capture = _make_artifact_capture(
            export_host_path="/other/dir/tool-versions.json"
        )
        receipt = dt.correlate_capture(
            capture,
            entry_kind="artifact_function",
            target_name="repository-ruff-tools",
            artifact_exit_code=0,
            artifact_path=Path("/expected/dir/tool-versions.json"),
        )
        art = receipt["claims"]["native_artifact_result"]
        self.assertEqual(art["export_attribution_status"], "attributed")
        self.assertIs(art["export_target_matched"], False)
        self.assertEqual(art["export_target_match_basis"], "export_path_mismatch")
        self.assertFalse(art["artifact_binding_verified"])


if __name__ == "__main__":
    unittest.main()
