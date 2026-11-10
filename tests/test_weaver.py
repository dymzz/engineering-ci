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


def test_unknown_enum_variant_is_not_claimed_as_conformant():
    report = json.loads(payload())
    report["statistics"]["advice_type_counts"]["undefined_enum_variant"] = 1
    result = weaver.live_result(0, json.dumps(report), ["pipeline"])
    assert result["reason"] == "enum_variant_unverified"


def otlp_payload():
    report = json.loads(payload())
    report["samples"][0]["span"].update(
        trace_id="1" * 32,
        span_id="2" * 16,
        start_time="2026-11-10T00:00:00Z",
        end_time="2026-11-10T00:00:01Z",
        attributes=[
            {"name": "cicd.pipeline.run.id", "value": "42"},
            {"name": "github.actions.run_attempt", "value": 1},
            {"name": "cicd.pipeline.result", "value": "success"},
        ],
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
            "conclusion": "success",
        },
    }
    capture = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "1" * 32,
                                "spanId": "2" * 16,
                                "attributes": [
                                    {
                                        "key": "cicd.pipeline.run.id",
                                        "value": {"stringValue": "42"},
                                    },
                                    {
                                        "key": "github.actions.run_attempt",
                                        "value": {"intValue": "1"},
                                    },
                                    {
                                        "key": "cicd.pipeline.result",
                                        "value": {"stringValue": "success"},
                                    },
                                ],
                            }
                        ]
                    }
                ]
            }
        ]
    }
    return report, expected, capture


def test_otlp_exact_identity_and_terminal_evidence():
    report, expected, capture = otlp_payload()
    assert (
        weaver.otlp_result(
            0, json.dumps(report), expected, terminal=True, capture=capture
        )["status"]
        == "passed"
    )
    expected["source"]["attempt"] = 2
    expected["terminal_evidence"]["attempt"] = 2
    assert (
        weaver.otlp_result(
            0, json.dumps(report), expected, terminal=True, capture=capture
        )["status"]
        == "failed"
    )


def test_terminal_binds_observed_run_attempt_result_and_root():
    cases = {
        "terminal_run_id_mismatch": lambda span: span["attributes"].__setitem__(
            0, {"name": "cicd.pipeline.run.id", "value": "99"}
        ),
        "terminal_attempt_mismatch": lambda span: span["attributes"].__setitem__(
            1, {"name": "github.actions.run_attempt", "value": 9}
        ),
        "terminal_result_mismatch": lambda span: span["attributes"].__setitem__(
            2, {"name": "cicd.pipeline.result", "value": "failure"}
        ),
        "terminal_span_not_root": lambda span: span.__setitem__(
            "parent_span_id", "3333333333333333"
        ),
        "terminal_observed_identity_missing": lambda span: span.__setitem__(
            "attributes", [{"name": "cicd.pipeline.result", "value": "success"}]
        ),
    }
    for reason, mutate in cases.items():
        mutated_report, mutated_expected, mutated_capture = otlp_payload()
        mutate(mutated_report["samples"][0]["span"])
        if reason == "terminal_span_not_root":
            mutated_capture["resourceSpans"][0]["scopeSpans"][0]["spans"][0][
                "parentSpanId"
            ] = "3333333333333333"
        if reason == "terminal_observed_identity_missing":
            mutated_capture["resourceSpans"][0]["scopeSpans"][0]["spans"][0][
                "attributes"
            ] = [
                {
                    "key": "cicd.pipeline.result",
                    "value": {"stringValue": "success"},
                }
            ]
        if reason == "terminal_run_id_mismatch":
            mutated_capture["resourceSpans"][0]["scopeSpans"][0]["spans"][0][
                "attributes"
            ][0] = {
                "key": "cicd.pipeline.run.id",
                "value": {"stringValue": "99"},
            }
        if reason == "terminal_attempt_mismatch":
            mutated_capture["resourceSpans"][0]["scopeSpans"][0]["spans"][0][
                "attributes"
            ][1] = {
                "key": "github.actions.run_attempt",
                "value": {"intValue": "9"},
            }
        if reason == "terminal_result_mismatch":
            mutated_capture["resourceSpans"][0]["scopeSpans"][0]["spans"][0][
                "attributes"
            ][2] = {
                "key": "cicd.pipeline.result",
                "value": {"stringValue": "failure"},
            }
        result = weaver.otlp_result(
            0,
            json.dumps(mutated_report),
            mutated_expected,
            terminal=True,
            capture=mutated_capture,
        )
        assert result["status"] == "failed"
        assert result["reason"] == reason


def test_terminal_ignores_matching_caller_assertions_without_observed_span():
    report, expected, capture = otlp_payload()
    # Caller source/evidence still agree on Run B, but observed Span is Run A.
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
    report, expected, capture = otlp_payload()
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
