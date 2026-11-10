"""Weaver's native result must not turn missing coverage into a pass."""

import importlib.util
import json
from pathlib import Path

path = (
    Path(__file__).resolve().parents[1] / ".dagger/src/engineering_ci_module/weaver.py"
)
spec = importlib.util.spec_from_file_location("weaver_unit", path)
weaver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(weaver)


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
    assert missing["semconv_live"] == "passed"
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
    assert ok["attempt_correlation"] == {"status": "verified", "attempt": 1}
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
