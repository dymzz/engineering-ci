"""Weaver's native result must not turn missing coverage into a pass."""

import hashlib
import importlib.util
import json
import sys
import types
from pathlib import Path

from engineering_ci import semconv_registry, weaver_tool

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / ".dagger/src/engineering_ci_module/weaver.py"
spec = importlib.util.spec_from_file_location("weaver_unit", path)
weaver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(weaver)
# Container runtime does `import weaver`; reuse the unit module under that name.
sys.modules.setdefault("weaver", weaver)
# Host unit tests do not install container-only deps (PyYAML).
sys.modules.setdefault("yaml", types.ModuleType("yaml"))

runtime_path = ROOT / ".dagger/src/engineering_ci_module/weaver_runtime.py"
runtime_spec = importlib.util.spec_from_file_location(
    "weaver_runtime_unit", runtime_path
)
weaver_runtime = importlib.util.module_from_spec(runtime_spec)
runtime_spec.loader.exec_module(weaver_runtime)

tooling_path = ROOT / ".dagger/src/engineering_ci_module/tooling.py"
tooling_spec = importlib.util.spec_from_file_location("tooling_unit", tooling_path)
tooling = importlib.util.module_from_spec(tooling_spec)
tooling_spec.loader.exec_module(tooling)


def payload(matched=1, errors=0, violations=0, spans=1):
    return json.dumps(
        {
            "statistics": {
                "total_entities_by_type": {"span": spans},
                "advice_level_counts": {"violation": violations},
                "advice_type_counts": {},
                "matchers": [{"id": "pipeline", "matched": matched, "errors": errors}],
            },
            "samples": [
                {
                    "span": {
                        "live_check_result": {
                            "match_info": {
                                "signal_matcher": "pipeline",
                                "signal": "cicd.pipeline.run.server",
                                "unmatched": False,
                            }
                        }
                    }
                }
            ]
            if spans
            else [],
        }
    )


def test_live_result_requires_actual_matching_samples():
    assert weaver.live_result(0, payload(), ["pipeline"])["status"] == "passed"
    assert weaver.live_result(0, payload(matched=0), ["pipeline"])["status"] == "failed"
    assert weaver.live_result(0, payload(spans=0), ["pipeline"])["status"] == "failed"
    assert weaver.live_result(0, payload(), ["missing"])["status"] == "failed"


def test_live_result_keeps_findings_and_tool_failure_distinct():
    assert (
        weaver.live_result(1, payload(violations=1), ["pipeline"])["reason"]
        == "findings"
    )
    assert (
        weaver.live_result(0, payload(violations=1), ["pipeline"])["reason"]
        == "findings"
    )
    assert weaver.live_result(2, "", ["pipeline"])["reason"] == "tool_failed"
    assert weaver.live_result(0, "not-json", ["pipeline"])["reason"] == "report_invalid"
    assert weaver.live_result(0, payload(errors=1), ["pipeline"])["status"] == "failed"


def test_no_expected_matcher_is_never_a_pass():
    assert weaver.live_result(0, payload(), [])["status"] == "failed"


def test_matcher_statistics_alone_do_not_prove_span_was_matched():
    report = json.loads(payload())
    report["samples"][0]["span"]["live_check_result"]["match_info"][
        "signal_matcher"
    ] = None
    assert weaver.live_result(0, json.dumps(report), ["pipeline"])["status"] == "failed"


def test_undefined_enum_variant_does_not_ban_custom_string_results():
    report = json.loads(payload())
    report["statistics"]["advice_type_counts"]["undefined_enum_variant"] = 1
    report["samples"][0]["span"]["attributes"] = [
        {"name": "cicd.pipeline.result", "value": "org.example.deferred"}
    ]
    result = weaver.live_result(0, json.dumps(report), ["pipeline"])
    assert result["status"] == "passed"
    assert result["undefined_enum_variant_count"] == 1


def test_wrong_typed_result_is_not_conformant():
    report = json.loads(payload())
    report["statistics"]["advice_type_counts"]["undefined_enum_variant"] = 1
    report["samples"][0]["span"]["attributes"] = [
        {"name": "cicd.pipeline.result", "value": 123}
    ]
    result = weaver.live_result(0, json.dumps(report), ["pipeline"])
    assert result["status"] == "failed"
    assert result["reason"] == "result_type_invalid"
    assert result["attribute"] == "cicd.pipeline.result"


def test_known_cancellation_result_passes_with_enum_advice_count():
    report = json.loads(payload())
    report["statistics"]["advice_type_counts"]["undefined_enum_variant"] = 0
    report["samples"][0]["span"]["attributes"] = [
        {"name": "cicd.pipeline.result", "value": "cancellation"}
    ]
    assert weaver.live_result(0, json.dumps(report), ["pipeline"])["status"] == "passed"


def otlp_payload(*, result="success", attempt_attr=None, resource_run_id="42"):
    report = json.loads(payload())
    sample_attrs = [{"name": "cicd.pipeline.result", "value": result}]
    if attempt_attr is not None:
        sample_attrs.append(
            {"name": "github.actions.run_attempt", "value": attempt_attr}
        )
    report["samples"][0]["span"].update(
        trace_id="1" * 32,
        span_id="2" * 16,
        start_time="2026-11-10T00:00:00Z",
        end_time="2026-11-10T00:00:01Z",
        attributes=sample_attrs,
    )
    expected = {
        "schema_version": 1,
        "source": {"run_id": "42", "attempt": 1},
        "spans": [
            {
                "trace_id": "1" * 32,
                "span_id": "2" * 16,
                "matcher": "pipeline",
                "signal": "cicd.pipeline.run.server",
            }
        ],
        "terminal_evidence": {
            "run_id": "42",
            "attempt": 1,
            "status": "completed",
            "result": result,
        },
    }
    span_attrs = [
        {"key": "cicd.pipeline.result", "value": {"stringValue": result}},
    ]
    if attempt_attr is not None:
        span_attrs.append(
            {
                "key": "github.actions.run_attempt",
                "value": {"intValue": str(attempt_attr)},
            }
        )
    capture = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "cicd.pipeline.run.id",
                            "value": {"stringValue": resource_run_id},
                        }
                    ]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "1" * 32,
                                "spanId": "2" * 16,
                                "attributes": span_attrs,
                            }
                        ]
                    }
                ],
            }
        ]
    }
    return report, expected, capture


def test_otlp_exact_identity_and_terminal_evidence():
    report, expected, capture = otlp_payload()
    # SemConv-only Live path may PASS without the GitHub Attempt extension.
    live = weaver.otlp_result(
        0, json.dumps(report), expected, terminal=False, capture=capture
    )
    assert live["status"] == "passed"
    # weaver-terminal Run/Attempt gate must not PASS without verified Attempt.
    missing = weaver.otlp_result(
        0, json.dumps(report), expected, terminal=True, capture=capture
    )
    assert missing["status"] == "failed"
    assert missing["reason"] == "terminal_attempt_correlation_unverified"
    assert missing["attempt_correlation"]["status"] == "unverified"
    assert missing["native_live_status"] == "passed"
    assert missing["terminal_correlation_status"] == "failed"
    expected["source"]["attempt"] = 2
    expected["terminal_evidence"]["attempt"] = 2
    again = weaver.otlp_result(
        0, json.dumps(report), expected, terminal=True, capture=capture
    )
    assert again["status"] == "failed"
    assert again["reason"] == "terminal_attempt_correlation_unverified"


def test_terminal_resource_only_run_id_passes():
    report, expected, capture = otlp_payload(attempt_attr=1)
    result = weaver.otlp_result(
        0, json.dumps(report), expected, terminal=True, capture=capture
    )
    assert result["status"] == "passed"
    assert "cicd.pipeline.run.id" not in {
        item["name"] for item in report["samples"][0]["span"]["attributes"]
    }


def test_terminal_conflicting_resource_and_span_run_id_fails():
    report, expected, capture = otlp_payload(attempt_attr=1)
    capture["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"].append(
        {"key": "cicd.pipeline.run.id", "value": {"stringValue": "99"}}
    )
    result = weaver.otlp_result(
        0, json.dumps(report), expected, terminal=True, capture=capture
    )
    assert result["status"] == "failed"
    assert result["reason"] == "terminal_run_id_conflict"


def test_terminal_attempt_extension_cross_check():
    report, expected, capture = otlp_payload(attempt_attr=1)
    ok = weaver.otlp_result(
        0, json.dumps(report), expected, terminal=True, capture=capture
    )
    assert ok["status"] == "passed"
    assert ok["attempt_correlation"]["status"] == "capture_attempt_matched"
    assert ok["attempt_correlation"]["attempt"] == 1
    assert ok["attempt_correlation"]["authenticated"] is False
    assert ok["terminal_correlation_status"] == "passed"
    assert ok["native_live_status"] == "passed"
    # Capture match is not #63 admission / GitHub authentication.
    assert "trusted" not in ok or ok.get("trusted") is not True
    bad_report, bad_expected, bad_capture = otlp_payload(attempt_attr=9)
    bad = weaver.otlp_result(
        0, json.dumps(bad_report), bad_expected, terminal=True, capture=bad_capture
    )
    assert bad["status"] == "failed"
    assert bad["reason"] == "terminal_attempt_mismatch"


def test_terminal_semconv_results_and_mismatch():
    for value in ("success", "cancellation", "skip", "timeout"):
        report, expected, capture = otlp_payload(result=value, attempt_attr=1)
        assert (
            weaver.otlp_result(
                0, json.dumps(report), expected, terminal=True, capture=capture
            )["status"]
            == "passed"
        )
    report, expected, capture = otlp_payload(result="success", attempt_attr=1)
    expected["terminal_evidence"]["result"] = "cancellation"
    failed = weaver.otlp_result(
        0, json.dumps(report), expected, terminal=True, capture=capture
    )
    assert failed["status"] == "failed"
    assert failed["reason"] == "terminal_result_mismatch"


def test_terminal_capture_sample_result_conflict():
    report, expected, capture = otlp_payload(result="success", attempt_attr=1)
    report["samples"][0]["span"]["attributes"] = [
        {"name": "cicd.pipeline.result", "value": "failure"},
        {"name": "github.actions.run_attempt", "value": 1},
    ]
    conflict = weaver.otlp_result(
        0, json.dumps(report), expected, terminal=True, capture=capture
    )
    assert conflict["status"] == "failed"
    assert conflict["reason"] == "terminal_result_conflict"
    matching = weaver.otlp_result(
        0,
        json.dumps(otlp_payload(result="success", attempt_attr=1)[0]),
        expected,
        terminal=True,
        capture=capture,
    )
    assert matching["status"] == "passed"


def test_terminal_capture_missing_duplicate_and_non_root():
    report, expected, capture = otlp_payload(attempt_attr=1)
    missing = weaver.otlp_result(
        0, json.dumps(report), expected, terminal=True, capture={"resourceSpans": []}
    )
    assert missing["reason"] == "missing_capture_span"
    dup = json.loads(json.dumps(capture))
    dup["resourceSpans"].append(dup["resourceSpans"][0])
    duplicated = weaver.otlp_result(
        0, json.dumps(report), expected, terminal=True, capture=dup
    )
    assert duplicated["reason"] == "duplicate_capture_span"
    report2, expected2, capture2 = otlp_payload(attempt_attr=1)
    capture2["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["parentSpanId"] = (
        "3333333333333333"
    )
    non_root = weaver.otlp_result(
        0, json.dumps(report2), expected2, terminal=True, capture=capture2
    )
    assert non_root["reason"] == "terminal_span_not_root"


def test_terminal_ignores_matching_caller_assertions_without_observed_span():
    report, expected, capture = otlp_payload(attempt_attr=1)
    expected["source"]["run_id"] = "B"
    expected["terminal_evidence"]["run_id"] = "B"
    result = weaver.otlp_result(
        0, json.dumps(report), expected, terminal=True, capture=capture
    )
    assert result["status"] == "failed"
    assert result["reason"] == "terminal_run_id_mismatch"


def test_otlp_missing_duplicate_unended_or_wrong_match_is_rejected():
    for mutation in ("missing", "duplicate", "unended", "wrong_match"):
        report, expected, capture = otlp_payload()
        span = report["samples"][0]["span"]
        if mutation == "missing":
            span["span_id"] = "3" * 16
        elif mutation == "duplicate":
            report["samples"].append(report["samples"][0])
        elif mutation == "unended":
            span["end_time"] = None
        else:
            expected["spans"][0]["signal"] = "wrong"
        assert (
            weaver.otlp_result(0, json.dumps(report), expected, capture=capture)[
                "status"
            ]
            == "failed"
        )


def test_terminal_requires_root_and_completed_evidence():
    report, expected, capture = otlp_payload(attempt_attr=1)
    del expected["terminal_evidence"]
    assert (
        weaver.otlp_result(0, json.dumps(report), expected, capture=capture)["status"]
        == "passed"
    )
    assert (
        weaver.otlp_result(
            0, json.dumps(report), expected, terminal=True, capture=capture
        )["status"]
        == "failed"
    )


def test_terminal_native_live_failure_does_not_claim_correlation_executed():
    # Terminal requested, but Native Live fails first — correlation never runs.
    report, expected, capture = otlp_payload()
    expected["spans"][0]["signal"] = "wrong"
    failed_live = weaver.otlp_result(
        0, json.dumps(report), expected, terminal=True, capture=capture
    )
    assert failed_live["status"] == "failed"
    assert failed_live["reason"] == "expected_span_match_mismatch"
    assert failed_live["terminal_correlation_status"] == "not_executed"
    assert failed_live.get("native_live_status") != "passed"
    coverage = weaver_runtime.coverage_from_execution(
        config={"origin": {}, "expected_matchers": []},
        evaluation=failed_live,
        advice_meta=None,
        kind="terminal",
    )
    assert coverage["terminal_correlation"] == "not_verified"


def test_terminal_correlation_failure_still_counts_as_executed_coverage():
    report, expected, capture = otlp_payload()
    failed = weaver.otlp_result(
        0, json.dumps(report), expected, terminal=True, capture=capture
    )
    assert failed["terminal_correlation_status"] == "failed"
    coverage = weaver_runtime.coverage_from_execution(
        config={
            "origin": {"version": "1.45.0", "commit": "x"},
            "expected_matchers": ["pipeline"],
        },
        evaluation=failed,
        advice_meta={"sha256": "abc"},
        kind="terminal",
    )
    assert coverage["terminal_correlation"] == "executed"


def test_weaver_tool_pin_is_single_authority():
    assert tooling.WEAVER_VERSION == weaver_tool.VERSION
    assert tooling.WEAVER_LINUX_SHA256 == weaver_tool.LINUX_SHA256
    assert tooling.WEAVER_LINUX_ASSET == weaver_tool.LINUX_ASSET
    assert weaver_tool.RECORD == {
        "version": weaver_tool.VERSION,
        "sha256": weaver_tool.LINUX_SHA256,
        "asset": weaver_tool.LINUX_ASSET,
    }


def _registry_files(registry):
    return {
        path.relative_to(registry).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(registry.rglob("*"))
        if path.is_file()
    }


def test_semconv_registry_pin_is_single_authority():
    assert tooling.SEMCONV_OFFICIAL_SOURCE == semconv_registry.RECORD
    assert semconv_registry.REPOSITORY == (
        "https://github.com/open-telemetry/semantic-conventions"
    )
    assert semconv_registry.COMMIT == "fb30ca17193b551473b1f43c315482918ab6987c"
    assert semconv_registry.VERSION == "1.45.0"
    assert semconv_registry.RECORD["tree_sha256"] == semconv_registry.TREE_SHA256
    assert semconv_registry.RECORD["git_tree_sha1"] == semconv_registry.GIT_TREE_SHA1


def test_container_runtime_uses_the_same_registry_digest():
    files = {"a.yaml": "0" * 64, "b.yaml": "1" * 64}
    assert weaver_runtime.registry_tree_digest(files) == (
        semconv_registry.registry_tree_digest(files)
    )


def test_official_registry_pin_matches_the_fixture_bytes():
    registry = ROOT / "tests/fixtures/weaver-cicd/registry"
    files = _registry_files(registry)
    assert len(files) == 269
    assert semconv_registry.registry_tree_digest(files) == semconv_registry.TREE_SHA256
    assert files["cicd/spans.yaml"] == semconv_registry.CICD_SPANS_SHA256


def test_fixture_registry_carries_no_local_entity_association():
    spans = (ROOT / "tests/fixtures/weaver-cicd/registry/cicd/spans.yaml").read_text(
        encoding="utf-8"
    )
    assert "entity_associations" not in spans
    # The upstream entity definition itself is unchanged.
    entities = (
        ROOT / "tests/fixtures/weaver-cicd/registry/cicd/entities.yaml"
    ).read_text(encoding="utf-8")
    assert "entity.cicd.pipeline.run" in entities


def test_fixture_bytes_are_platform_independent():
    # The fixture is a byte-stable input closure: checkout must not rewrite its
    # bytes, or the Plan closure digests stop being reproducible.
    fixture = ROOT / "tests/fixtures/weaver-cicd"
    attributes = (fixture / ".gitattributes").read_text(encoding="utf-8")
    assert "* -text" in attributes
    for path in sorted(fixture.rglob("*")):
        if not path.is_file():
            continue
        assert b"\r\n" not in path.read_bytes(), path


def test_official_source_status_verifies_the_pinned_registry():
    files = _registry_files(ROOT / "tests/fixtures/weaver-cicd/registry")
    status = weaver_runtime.official_source_status(
        files=files,
        origin={
            "repository": semconv_registry.REPOSITORY,
            "commit": semconv_registry.COMMIT,
        },
        pin=semconv_registry.RECORD,
    )
    assert status["verified"] is True
    assert status["fatal"] is False
    assert status["tree_sha256"] == semconv_registry.TREE_SHA256
    assert status["git_tree_sha1"] == semconv_registry.GIT_TREE_SHA1


def test_official_source_status_rejects_registry_and_manifest_edited_together():
    files = _registry_files(ROOT / "tests/fixtures/weaver-cicd/registry")
    # Editing the Registry bytes and the manifest together keeps the manifest
    # self-consistent, but cannot reproduce the pinned upstream source.
    tampered = dict(files)
    tampered["cicd/spans.yaml"] = "0" * 64
    assert tampered != files
    status = weaver_runtime.official_source_status(
        files=tampered,
        origin={
            "repository": semconv_registry.REPOSITORY,
            "commit": semconv_registry.COMMIT,
        },
        pin=semconv_registry.RECORD,
    )
    assert status["verified"] is False
    assert status["fatal"] is True
    assert status["reason"] == "official_registry_bytes_mismatch"


def test_official_source_status_requires_the_pin_and_the_pinned_commit():
    missing = weaver_runtime.official_source_status(files={}, origin={}, pin=None)
    assert missing["fatal"] is True
    assert missing["reason"] == "official_source_pin_unavailable"

    other_commit = weaver_runtime.official_source_status(
        files={},
        origin={"repository": semconv_registry.REPOSITORY, "commit": "0" * 40},
        pin=semconv_registry.RECORD,
    )
    assert other_commit["fatal"] is True
    assert other_commit["reason"] == "official_source_commit_unverified"

    other_source = weaver_runtime.official_source_status(
        files={},
        origin={
            "repository": "https://github.com/open-telemetry/weaver",
            "commit": "x",
        },
        pin=semconv_registry.RECORD,
    )
    assert other_source["verified"] is False
    assert other_source["fatal"] is False
    assert other_source["reason"] == "origin_not_pinned_official_source"


def test_coverage_separates_official_registry_from_eci_policy():
    report, expected, capture = otlp_payload(attempt_attr=1)
    passed = weaver.otlp_result(
        0, json.dumps(report), expected, terminal=True, capture=capture
    )
    coverage = weaver_runtime.coverage_from_execution(
        config={
            "origin": {"version": "1.45.0", "commit": semconv_registry.COMMIT},
            "expected_matchers": ["pipeline"],
        },
        evaluation=passed,
        advice_meta={"sha256": "abc"},
        kind="terminal",
        official_source={"verified": True, "reason": None},
    )
    assert coverage["official_registry_verified"] is True
    assert coverage["eci_policy_evaluated"] is True
    # Upstream SemConv declares no Resource Required obligation for the
    # Pipeline span, so none may be claimed as executed.
    assert coverage["resource_required"] == "not_applicable"
    assert coverage["eci_run_id_correlation"]["rule_id"] == (
        "eci_terminal_run_id_correlation"
    )
    assert coverage["eci_run_id_correlation"]["status"] == "executed"


def test_coverage_does_not_claim_official_verification_without_the_pin():
    report, expected, capture = otlp_payload(attempt_attr=1)
    passed = weaver.otlp_result(
        0, json.dumps(report), expected, terminal=True, capture=capture
    )
    coverage = weaver_runtime.coverage_from_execution(
        config={"origin": {}, "expected_matchers": ["pipeline"]},
        evaluation=passed,
        advice_meta={"sha256": "abc"},
        kind="terminal",
        official_source={
            "verified": False,
            "reason": "official_source_pin_unavailable",
        },
    )
    assert coverage["official_registry_verified"] is False
    assert coverage["official_source_reason"] == "official_source_pin_unavailable"
    assert coverage["eci_policy_evaluated"] is True


def test_time_provenance_records_source_without_claiming_duration():
    report, expected, capture = otlp_payload(attempt_attr=1)
    passed = weaver.otlp_result(
        0, json.dumps(report), expected, terminal=True, capture=capture
    )
    provenance = passed["time_provenance"]
    assert provenance["capture_time_source"] == (
        "otlp_span_startTimeUnixNano_endTimeUnixNano"
    )
    assert provenance["native_sample_time_source"] == (
        "weaver_live_check_start_time_end_time_rfc3339"
    )
    # A Terminal PASS must not be read as an exact end time or a duration.
    assert provenance["exact_end_time_claimed"] is False
    assert provenance["duration_claimed"] is False
    assert provenance["sample_time_fraction_digits"] == [0]
    coverage = weaver_runtime.coverage_from_execution(
        config={"origin": {}, "expected_matchers": ["pipeline"]},
        evaluation=passed,
        advice_meta={"sha256": "abc"},
        kind="terminal",
    )
    assert coverage["time_provenance"] == provenance
