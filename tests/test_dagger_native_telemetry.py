"""Tests for Dagger native OTel capture, parent-graph correlation, and A.1 verification."""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
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
                report["negative_controls"]["filename_only_export_verified"],
                "False",
            )
            self.assertEqual(
                report["negative_controls"]["non_isolated_override_rejected"],
                "unbound",
            )
            self.assertEqual(
                report["negative_controls"]["legacy_missing_capture_scope"],
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

    def test_filename_only_export_in_other_dir_stays_limited_clue_without_exact_binding(
        self,
    ) -> None:
        capture = _make_artifact_capture(export_filename="same.json")
        with tempfile.TemporaryDirectory() as tmpdir:
            local_file = Path(tmpdir) / "same.json"
            local_file.write_text('{"ok": true}\n', encoding="utf-8")
            sha = dt.sha256_file(local_file)
            receipt = dt.correlate_capture(
                capture,
                entry_kind="artifact_function",
                target_name="repository-ruff-tools",
                artifact_exit_code=0,
                artifact_path=local_file,
                artifact_sha256=sha,
            )
        art = receipt["claims"]["native_artifact_result"]
        self.assertTrue(art["export_span_observed"])
        self.assertEqual(art["export_attribution_status"], "attributed")
        self.assertTrue(art["local_file_verified"])
        self.assertEqual(art["status"], "exported")
        self.assertIs(art["export_target_matched"], False)
        self.assertEqual(art["export_target_match_basis"], "filename_matched")
        self.assertFalse(art["artifact_binding_verified"])

    def test_legacy_isolated_and_receiver_observed_without_capture_scope_stays_unbound(
        self,
    ) -> None:
        capture = _make_check_capture(isolated_capture=True)
        capture.pop("capture_scope", None)
        self.assertIs(capture["isolated_capture"], True)
        self.assertIs(capture["receiver_observed"], True)

        receipt = dt.correlate_capture(
            capture,
            entry_kind="check",
            target_name="lint",
            isolated_capture=True,
        )
        self.assertEqual(receipt["correlation"]["status"], "unbound")
        self.assertEqual(
            receipt["correlation"]["reason"],
            "legacy_isolation_missing_capture_scope_without_traceparent",
        )
        self.assertEqual(
            receipt["correlation"]["isolation_evidence"]["evidence_level"],
            "declared_and_receiver_observed_without_scope",
        )
        self.assertIs(
            receipt["correlation"]["isolation_evidence"]["override_rejected"],
            True,
        )

    def test_cross_module_cause_links_on_single_export_mark_ambiguous(self) -> None:
        trace_id = "8" * 32
        query_id = "8000000000000001"
        core_eci = "8000000000000002"
        dagql_eci = "8000000000000003"
        core_other = "8000000000000004"
        dagql_other = "8000000000000005"
        export_id = "8000000000000006"
        resume_id = "8000000000000007"
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
                                        _kv(dt.DAGGER_SESSION_ID_ATTR, "sess-cross"),
                                        _kv(
                                            dt.DAGGER_ENGINE_INSTANCE_ID_ATTR,
                                            "eng-cross",
                                        ),
                                    ]
                                },
                                "scopeSpans": [
                                    {
                                        "scope": {"name": dt.DAGQL_SCOPE_NAME},
                                        "spans": [
                                            _span(
                                                trace_id=trace_id,
                                                span_id=dagql_eci,
                                                parent_span_id=core_eci,
                                                name="engineering-ci:EngineeringCi.repositoryRuffTools",
                                            ),
                                            _span(
                                                trace_id=trace_id,
                                                span_id=dagql_other,
                                                parent_span_id=core_other,
                                                name="other-module:OtherMod.buildArtifact",
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
                                                span_id=core_eci,
                                                parent_span_id=query_id,
                                                name="EngineeringCi.repositoryRuffTools",
                                            ),
                                            _span(
                                                trace_id=trace_id,
                                                span_id=core_other,
                                                parent_span_id=query_id,
                                                name="OtherMod.buildArtifact",
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
                                                        "spanId": core_eci,
                                                        "attributes": [
                                                            _kv(
                                                                "dagger.io/link.purpose",
                                                                "cause",
                                                            )
                                                        ],
                                                    },
                                                    {
                                                        "traceId": trace_id,
                                                        "spanId": core_other,
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


class A1LiveScopeGitPathEdgeTests(unittest.TestCase):
    def test_hidden_dot_paths_and_dot_slash_prefixes_keep_leading_dots(self) -> None:
        for raw_path in (
            ".dagger/src/engineering_ci_module/main.py",
            "./.dagger/src/engineering_ci_module/main.py",
            ".github/workflows/ci.yml",
            "./.github/workflows/ci.yml",
        ):
            with self.subTest(raw_path=raw_path):
                decision = dt.select_a1_capture_scope([raw_path])
                self.assertTrue(decision["run_live_capture"], decision)
                self.assertTrue(decision["matched_paths"][0].startswith("."), decision)

        non_sensitive_hidden = dt.select_a1_capture_scope(
            ["./.github/workflows/other.yml"]
        )
        self.assertFalse(non_sensitive_hidden["run_live_capture"])
        self.assertEqual(
            non_sensitive_hidden["inspected_changed_paths"],
            [".github/workflows/other.yml"],
        )

    def test_unescapes_git_c_quoted_unicode_and_reads_nul_delimited_paths(
        self,
    ) -> None:
        quoted = r'".dagger/\346\265\213\350\257\225.py"'
        decision = dt.select_a1_capture_scope([quoted])
        self.assertTrue(decision["run_live_capture"])
        self.assertEqual(decision["matched_paths"], [".dagger/测试.py"])

        with tempfile.TemporaryDirectory() as tmpdir:
            changed_nul = Path(tmpdir) / "changed.nul"
            changed_nul.write_bytes(
                b".dagger/\xe6\xb5\x8b\xe8\xaf\x95.py\x00docs/notes.md\x00"
            )
            gh_out = Path(tmpdir) / "github_output.txt"
            exit_code = dt.main(
                [
                    "a1-scope",
                    "--changed-files-file",
                    str(changed_nul),
                    "--github-output",
                    str(gh_out),
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertIn("run_live_capture=true", gh_out.read_text(encoding="utf-8"))

    def test_real_git_diff_handles_hidden_unicode_and_rename_from_sensitive_dir(
        self,
    ) -> None:
        def git(repo: Path, *args: str) -> bytes:
            proc = subprocess.run(
                ["git", *args],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            return proc.stdout

        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            repo.mkdir()
            git(repo, "init", "-q")
            git(repo, "config", "user.name", "ECI Test")
            git(repo, "config", "user.email", "eci@example.invalid")

            (repo / ".dagger" / "src").mkdir(parents=True)
            (repo / ".github" / "workflows").mkdir(parents=True)
            (repo / "docs").mkdir()
            (repo / ".dagger" / "src" / "tool.py").write_text(
                "X = 1\n", encoding="utf-8"
            )
            (repo / ".github" / "workflows" / "ci.yml").write_text(
                "name: CI\n", encoding="utf-8"
            )
            (repo / "docs" / "readme.md").write_text("base\n", encoding="utf-8")
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "base")
            base_sha = git(repo, "rev-parse", "HEAD").decode("utf-8").strip()

            # 1. Hidden dot path edit (.dagger/...):
            (repo / ".dagger" / "src" / "tool.py").write_text(
                "X = 2\n", encoding="utf-8"
            )
            git(repo, "add", ".dagger/src/tool.py")
            git(repo, "commit", "-q", "-m", "edit hidden dagger file")
            hidden_sha = git(repo, "rev-parse", "HEAD").decode("utf-8").strip()
            hidden_file = Path(tmpdir) / "hidden.bin"
            hidden_file.write_bytes(
                git(
                    repo,
                    "-c",
                    "core.quotePath=false",
                    "diff",
                    "-z",
                    "--no-renames",
                    "--name-only",
                    f"{base_sha}...{hidden_sha}",
                )
            )
            out_hidden = Path(tmpdir) / "hidden.json"
            self.assertEqual(
                dt.main(
                    [
                        "a1-scope",
                        "--changed-files-file",
                        str(hidden_file),
                        "--output",
                        str(out_hidden),
                    ]
                ),
                0,
            )
            self.assertTrue(
                json.loads(out_hidden.read_text(encoding="utf-8"))["run_live_capture"]
            )

            # 2. Rename sensitive path -> non-sensitive dir:
            #    legacy --name-only drops old path, while -c core.quotePath=false -z --no-renames retains it.
            git(repo, "mv", ".dagger/src/tool.py", "docs/moved_tool.py")
            git(repo, "commit", "-q", "-m", "move sensitive file to docs")
            rename_sha = git(repo, "rev-parse", "HEAD").decode("utf-8").strip()
            legacy_rename_paths = (
                git(repo, "diff", "--name-only", f"{hidden_sha}...{rename_sha}")
                .decode("utf-8")
                .splitlines()
            )
            self.assertEqual(legacy_rename_paths, ["docs/moved_tool.py"])
            self.assertFalse(
                dt.select_a1_capture_scope(legacy_rename_paths)["run_live_capture"]
            )
            safe_rename_file = Path(tmpdir) / "rename.bin"
            safe_rename_file.write_bytes(
                git(
                    repo,
                    "-c",
                    "core.quotePath=false",
                    "diff",
                    "-z",
                    "--no-renames",
                    "--name-only",
                    f"{hidden_sha}...{rename_sha}",
                )
            )
            out_rename = Path(tmpdir) / "rename.json"
            self.assertEqual(
                dt.main(
                    [
                        "a1-scope",
                        "--changed-files-file",
                        str(safe_rename_file),
                        "--output",
                        str(out_rename),
                    ]
                ),
                0,
            )
            rename_decision = json.loads(out_rename.read_text(encoding="utf-8"))
            self.assertTrue(rename_decision["run_live_capture"])
            self.assertEqual(rename_decision["matched_paths"], [".dagger/src/tool.py"])

            # 3. Non-ASCII Unicode path under .dagger/ (with both safe NUL and quoted diff):
            uni_path = repo / ".dagger" / "测试.py"
            uni_path.write_text("U = 1\n", encoding="utf-8")
            git(repo, "add", ".dagger/测试.py")
            git(repo, "commit", "-q", "-m", "unicode dagger path")
            uni_sha = git(repo, "rev-parse", "HEAD").decode("utf-8").strip()
            quoted_lines = (
                git(
                    repo,
                    "-c",
                    "core.quotePath=true",
                    "diff",
                    "--name-only",
                    f"{rename_sha}...{uni_sha}",
                )
                .decode("utf-8")
                .splitlines()
            )
            self.assertTrue(
                dt.select_a1_capture_scope(quoted_lines)["run_live_capture"]
            )
            safe_uni_file = Path(tmpdir) / "unicode.bin"
            safe_uni_file.write_bytes(
                git(
                    repo,
                    "-c",
                    "core.quotePath=false",
                    "diff",
                    "-z",
                    "--no-renames",
                    "--name-only",
                    f"{rename_sha}...{uni_sha}",
                )
            )
            out_uni = Path(tmpdir) / "unicode.json"
            dt.main(
                [
                    "a1-scope",
                    "--changed-files-file",
                    str(safe_uni_file),
                    "--output",
                    str(out_uni),
                ]
            )
            uni_decision = json.loads(out_uni.read_text(encoding="utf-8"))
            self.assertTrue(uni_decision["run_live_capture"])
            self.assertEqual(uni_decision["matched_paths"], [".dagger/测试.py"])

    def test_ci_workflow_uses_safe_git_diff_for_a1_scope(self) -> None:
        ci_yml = (
            Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'git -c core.quotePath=false diff -z --no-renames --name-only "${base_sha}...${HEAD_SHA}"',
            ci_yml,
        )

    def test_ci_workflow_reverify_uses_pr_head_and_supports_workflow_dispatch(
        self,
    ) -> None:
        ci_path = (
            Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
        )
        ci_yml = ci_path.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", ci_yml)
        self.assertIn("PR_HEAD_SHA: ${{ github.event.pull_request.head.sha }}", ci_yml)
        self.assertIn("HEAD_SHA: ${{ github.sha }}", ci_yml)
        self.assertIn("reverify_a1_capture:", ci_yml)

        step_marker = "      - name: Determine A.1 live capture scope\n"
        self.assertIn(step_marker, ci_yml)
        after_step = ci_yml.split(step_marker, 1)[1]
        run_match = re.search(
            r"\n        run: \|\n((?:          .*\n|\s*\n)+)", after_step
        )
        self.assertIsNotNone(
            run_match, "Determine A.1 live capture scope run script missing"
        )
        assert run_match is not None
        step_script = textwrap.dedent(run_match.group(1))

        bash_exe: str | None = None
        if sys.platform == "win32":
            git_exe = shutil.which("git")
            if git_exe:
                git_root = Path(git_exe).resolve().parent.parent
                for rel in ("bin/bash.exe", "usr/bin/bash.exe"):
                    candidate = git_root / rel
                    if candidate.is_file():
                        bash_exe = str(candidate)
                        break
        else:
            bash_exe = shutil.which("bash")
            if bash_exe is None:
                self.fail("bash is required on non-Windows runners")
        if bash_exe is None:
            self.skipTest("Git Bash not found on Windows")

        def parse_outputs(text: str) -> dict[str, str]:
            parsed: dict[str, str] = {}
            for line in text.splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    parsed[k] = v
            return parsed

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo = root / "repo"
            repo.mkdir()
            bin_dir = root / "bin"
            bin_dir.mkdir()

            py_shim = bin_dir / "python3"
            py_shim.write_text(
                '#!/bin/sh\nexec "$ECI_TEST_PYTHON" "$@"\n', encoding="utf-8"
            )
            uv_shim = bin_dir / "uv"
            uv_shim.write_text(
                "#!/bin/sh\n"
                'exec "$ECI_TEST_PYTHON" -c \'\n'
                "import subprocess, sys\n"
                "args = sys.argv[1:]\n"
                'if args[:1] == ["run"]: args = args[1:]\n'
                'while args and args[0].startswith("-"): args = args[1:]\n'
                'if args[:1] in (["python"], ["python3"]): args = args[1:]\n'
                "raise SystemExit(subprocess.call([sys.executable, *args]))\n"
                '\' "$@"\n',
                encoding="utf-8",
            )
            os.chmod(py_shim, 0o755)
            os.chmod(uv_shim, 0o755)

            def git(*args: str) -> str:
                return subprocess.run(
                    ["git", *args],
                    cwd=repo,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()

            git("init", "-q")
            git("config", "user.name", "ECI Test")
            git("config", "user.email", "eci@example.invalid")
            (repo / "docs/verification").mkdir(parents=True)
            (repo / "dagger-module.toml").write_text(
                'engineVersion = "v1.0.0-beta.16"\n', encoding="utf-8"
            )
            (repo / "docs/verification/dagger-native-telemetry.md").write_text(
                "Engine: v1.0.0-beta.16\n", encoding="utf-8"
            )
            (repo / "README.md").write_text("base\n", encoding="utf-8")
            git("add", "-A")
            git("commit", "-q", "-m", "base")
            base_sha = git("rev-parse", "HEAD")

            # Case 1: pull_request where PR HEAD contains [reverify-a1] while ephemeral
            # GitHub test merge commit lacks the tag, and only README.md changed.
            git("checkout", "-q", "-b", "pr-reverify", base_sha)
            (repo / "README.md").write_text("docs touch\n", encoding="utf-8")
            git("add", "README.md")
            git("commit", "-q", "-m", "docs: clarify [reverify-a1] downstream check")
            pr_reverify_head = git("rev-parse", "HEAD")

            git("checkout", "-q", "--detach", base_sha)
            git(
                "merge",
                "--no-ff",
                "--no-edit",
                "-m",
                f"Merge {pr_reverify_head} into {base_sha}",
                pr_reverify_head,
            )
            merge_reverify_sha = git("rev-parse", "HEAD")

            # Case 2: unmarked PR HEAD stays offline (even if test merge commit mentions tag).
            git("checkout", "-q", "-b", "pr-unmarked", base_sha)
            (repo / "README.md").write_text("plain docs change\n", encoding="utf-8")
            git("add", "README.md")
            git("commit", "-q", "-m", "docs: plain update without reverify tag")
            pr_unmarked_head = git("rev-parse", "HEAD")

            git("checkout", "-q", "--detach", base_sha)
            git(
                "merge",
                "--no-ff",
                "--no-edit",
                "-m",
                f"Merge {pr_unmarked_head} into {base_sha} [reverify-a1]",
                pr_unmarked_head,
            )
            merge_unmarked_sha = git("rev-parse", "HEAD")

            # Case 3: push event commit with [reverify-a1]
            git("checkout", "-q", "-b", "main-push", base_sha)
            (repo / "README.md").write_text("main doc update\n", encoding="utf-8")
            git("add", "README.md")
            git("commit", "-q", "-m", "docs: push commit with [reverify-a1]")
            push_head = git("rev-parse", "HEAD")

            def run_scope_step(case_name: str, extra_env: dict[str, str]):
                case_temp = root / f"temp-{case_name}"
                case_temp.mkdir()
                gh_out = root / f"gh-{case_name}.txt"
                gh_out.write_text("", encoding="utf-8")
                env = os.environ.copy()
                env.update(
                    {
                        "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
                        "PYTHONPATH": str(SRC),
                        "ECI_TEST_PYTHON": sys.executable,
                        "RUNNER_TEMP": str(case_temp),
                        "GITHUB_OUTPUT": str(gh_out),
                        "EVENT_NAME": "",
                        "PR_BASE_SHA": "",
                        "PR_HEAD_SHA": "",
                        "PUSH_BEFORE_SHA": "",
                        "HEAD_SHA": "",
                        "REVERIFY_INPUT": "",
                    }
                )
                env.update(extra_env)
                proc = subprocess.run(
                    [bash_exe, "-c", step_script],
                    cwd=repo,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                return proc, case_temp, gh_out

            proc_pr_yes, temp_pr_yes, gh_pr_yes = run_scope_step(
                "pr_reverify",
                {
                    "EVENT_NAME": "pull_request",
                    "PR_BASE_SHA": base_sha,
                    "PR_HEAD_SHA": pr_reverify_head,
                    "HEAD_SHA": merge_reverify_sha,
                },
            )
            self.assertEqual(proc_pr_yes.returncode, 0, proc_pr_yes.stderr)
            self.assertEqual(
                parse_outputs(gh_pr_yes.read_text(encoding="utf-8")),
                {
                    "run_live_capture": "true",
                    "reason": "explicit_reverify_requested",
                    "evidence_level": "pending_native_capture",
                },
            )
            dec_yes = json.loads(
                (temp_pr_yes / "a1-scope-decision.json").read_text(encoding="utf-8")
            )
            self.assertEqual(dec_yes["inspected_changed_paths"], ["README.md"])

            proc_pr_no, temp_pr_no, gh_pr_no = run_scope_step(
                "pr_unmarked",
                {
                    "EVENT_NAME": "pull_request",
                    "PR_BASE_SHA": base_sha,
                    "PR_HEAD_SHA": pr_unmarked_head,
                    "HEAD_SHA": merge_unmarked_sha,
                },
            )
            self.assertEqual(proc_pr_no.returncode, 0, proc_pr_no.stderr)
            self.assertEqual(
                parse_outputs(gh_pr_no.read_text(encoding="utf-8")),
                {
                    "run_live_capture": "false",
                    "reason": "unaffected_paths_offline_contract_only",
                    "evidence_level": "offline_contract_only",
                },
            )
            dec_no = json.loads(
                (temp_pr_no / "a1-scope-decision.json").read_text(encoding="utf-8")
            )
            self.assertEqual(dec_no["inspected_changed_paths"], ["README.md"])

            proc_missing, _, gh_missing = run_scope_step(
                "pr_missing_head",
                {
                    "EVENT_NAME": "pull_request",
                    "PR_BASE_SHA": base_sha,
                    "PR_HEAD_SHA": "0" * 40,
                    "HEAD_SHA": merge_unmarked_sha,
                },
            )
            self.assertNotEqual(proc_missing.returncode, 0)
            self.assertIn("Missing or unreadable commit ref", proc_missing.stderr)
            self.assertEqual(gh_missing.read_text(encoding="utf-8"), "")

            proc_push, _, gh_push = run_scope_step(
                "push_reverify",
                {
                    "EVENT_NAME": "push",
                    "PUSH_BEFORE_SHA": base_sha,
                    "HEAD_SHA": push_head,
                },
            )
            self.assertEqual(proc_push.returncode, 0, proc_push.stderr)
            self.assertEqual(
                parse_outputs(gh_push.read_text(encoding="utf-8"))["reason"],
                "explicit_reverify_requested",
            )

            proc_dispatch, _, gh_dispatch = run_scope_step(
                "workflow_dispatch",
                {
                    "EVENT_NAME": "workflow_dispatch",
                    "HEAD_SHA": pr_unmarked_head,
                    "REVERIFY_INPUT": "true",
                },
            )
            self.assertEqual(proc_dispatch.returncode, 0, proc_dispatch.stderr)
            self.assertEqual(
                parse_outputs(gh_dispatch.read_text(encoding="utf-8"))["reason"],
                "explicit_reverify_requested",
            )

    def test_dagger_toml_only_change_triggers_live_capture_and_unrelated_stays_offline(
        self,
    ) -> None:
        decision = dt.select_a1_capture_scope(["dagger.toml"])
        self.assertTrue(decision["run_live_capture"])
        self.assertEqual(decision["matched_paths"], ["dagger.toml"])
        self.assertEqual(decision["reason"], "sensitive_paths_changed")

        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            repo.mkdir()

            def git(*args: str) -> bytes:
                return subprocess.run(
                    ["git", *args], cwd=repo, check=True, capture_output=True
                ).stdout

            git("init", "-q")
            git("config", "user.name", "ECI Test")
            git("config", "user.email", "eci@example.invalid")
            (repo / "dagger.toml").write_text("[modules.root]\n", encoding="utf-8")
            (repo / "README.md").write_text("base\n", encoding="utf-8")
            git("add", "-A")
            git("commit", "-q", "-m", "base")
            base_sha = git("rev-parse", "HEAD").decode("utf-8").strip()

            (repo / "dagger.toml").write_text(
                "[modules.root]\nsource = '.'\n", encoding="utf-8"
            )
            git("add", "dagger.toml")
            git("commit", "-q", "-m", "update workspace dagger.toml")
            head_sha = git("rev-parse", "HEAD").decode("utf-8").strip()

            changed_bin = Path(tmpdir) / "dagger-toml.bin"
            changed_bin.write_bytes(
                git(
                    "-c",
                    "core.quotePath=false",
                    "diff",
                    "-z",
                    "--no-renames",
                    "--name-only",
                    f"{base_sha}...{head_sha}",
                )
            )
            out_json = Path(tmpdir) / "decision.json"
            self.assertEqual(
                dt.main(
                    [
                        "a1-scope",
                        "--changed-files-file",
                        str(changed_bin),
                        "--output",
                        str(out_json),
                    ]
                ),
                0,
            )
            parsed = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertTrue(parsed["run_live_capture"])
            self.assertEqual(parsed["matched_paths"], ["dagger.toml"])
            self.assertEqual(parsed["reason"], "sensitive_paths_changed")

            (repo / "README.md").write_text("doc edit\n", encoding="utf-8")
            git("add", "README.md")
            git("commit", "-q", "-m", "docs only")
            doc_sha = git("rev-parse", "HEAD").decode("utf-8").strip()
            doc_bin = Path(tmpdir) / "docs.bin"
            doc_bin.write_bytes(
                git(
                    "-c",
                    "core.quotePath=false",
                    "diff",
                    "-z",
                    "--no-renames",
                    "--name-only",
                    f"{head_sha}...{doc_sha}",
                )
            )
            doc_json = Path(tmpdir) / "doc-decision.json"
            dt.main(
                [
                    "a1-scope",
                    "--changed-files-file",
                    str(doc_bin),
                    "--output",
                    str(doc_json),
                ]
            )
            doc_parsed = json.loads(doc_json.read_text(encoding="utf-8"))
            self.assertFalse(doc_parsed["run_live_capture"])
            self.assertEqual(
                doc_parsed["reason"], "unaffected_paths_offline_contract_only"
            )

    def test_newline_in_git_path_does_not_inject_github_output_control_fields(
        self,
    ) -> None:
        def parse_github_output(text: str) -> dict[str, str]:
            # Mirror Actions Runner FileCommandManager key=value & <<delimiter rules.
            parsed: dict[str, str] = {}
            lines = text.splitlines()
            idx = 0
            while idx < len(lines):
                line = lines[idx]
                if "<<" in line:
                    key, delimiter = line.split("<<", 1)
                    idx += 1
                    buf = []
                    while idx < len(lines) and lines[idx] != delimiter:
                        buf.append(lines[idx])
                        idx += 1
                    parsed[key] = "\n".join(buf)
                elif "=" in line:
                    key, val = line.split("=", 1)
                    parsed[key] = val
                idx += 1
            return parsed

        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            repo.mkdir()

            def git(*args: str, inp: bytes | None = None) -> bytes:
                return subprocess.run(
                    ["git", *args],
                    cwd=repo,
                    input=inp,
                    check=True,
                    capture_output=True,
                ).stdout

            git("init", "-q")
            git("config", "user.name", "ECI Test")
            git("config", "user.email", "eci@example.invalid")
            blob = git("hash-object", "-w", "--stdin", inp=b"x = 1\n").strip().decode()
            base_tree = (
                git("mktree", "-z", inp=f"100644 blob {blob}\tREADME.md\x00".encode())
                .strip()
                .decode()
            )
            base_commit = git("commit-tree", base_tree, "-m", "base").strip().decode()

            evil_leaf = "evil\nrun_live_capture=false\necho=injected.py"
            inner_tree = (
                git("mktree", "-z", inp=f"100644 blob {blob}\t{evil_leaf}\x00".encode())
                .strip()
                .decode()
            )
            head_tree = (
                git(
                    "mktree",
                    "-z",
                    inp=f"040000 tree {inner_tree}\t.dagger\x00".encode(),
                )
                .strip()
                .decode()
            )
            head_commit = (
                git("commit-tree", head_tree, "-p", base_commit, "-m", "newline path")
                .strip()
                .decode()
            )

            diff_file = Path(tmpdir) / "newline.bin"
            diff_file.write_bytes(
                git(
                    "-c",
                    "core.quotePath=false",
                    "diff",
                    "-z",
                    "--no-renames",
                    "--name-only",
                    f"{base_commit}...{head_commit}",
                )
            )
            gh_out = Path(tmpdir) / "github_output.txt"
            out_json = Path(tmpdir) / "scope.json"
            self.assertEqual(
                dt.main(
                    [
                        "a1-scope",
                        "--changed-files-file",
                        str(diff_file),
                        "--github-output",
                        str(gh_out),
                        "--output",
                        str(out_json),
                    ]
                ),
                0,
            )

            gh_text = gh_out.read_text(encoding="utf-8")
            outputs = parse_github_output(gh_text)
            self.assertEqual(
                outputs,
                {
                    "run_live_capture": "true",
                    "reason": "sensitive_paths_changed",
                    "evidence_level": "pending_native_capture",
                },
            )
            self.assertEqual(len(gh_text.splitlines()), 3)

            decision = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(decision["matched_paths"], [f".dagger/{evil_leaf}"])

            multiline_out = Path(tmpdir) / "multiline_output.txt"
            dt._write_github_outputs(
                multiline_out,
                {"detail": "line1\n__ECI_EOF__\nrun_live_capture=false"},
            )
            self.assertEqual(
                parse_github_output(multiline_out.read_text(encoding="utf-8")),
                {"detail": "line1\n__ECI_EOF__\nrun_live_capture=false"},
            )
            with self.assertRaises(ValueError):
                dt._write_github_outputs(multiline_out, {"bad": "a\x00b"})


if __name__ == "__main__":
    unittest.main()
