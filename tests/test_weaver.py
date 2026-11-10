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
    return report, expected


def test_otlp_exact_identity_and_terminal_evidence():
    report, expected = otlp_payload()
    assert (
        weaver.otlp_result(0, json.dumps(report), expected, terminal=True)["status"]
        == "passed"
    )
    expected["source"]["attempt"] = 2
    assert (
        weaver.otlp_result(0, json.dumps(report), expected, terminal=True)["status"]
        == "failed"
    )


def test_otlp_missing_duplicate_unended_or_wrong_match_is_rejected():
    for mutation in ("missing", "duplicate", "unended", "wrong_match"):
        report, expected = otlp_payload()
        span = report["samples"][0]["span"]
        if mutation == "missing":
            span["span_id"] = "3" * 16
        elif mutation == "duplicate":
            report["samples"].append(report["samples"][0])
        elif mutation == "unended":
            span["end_time"] = None
        else:
            expected["spans"][0]["signal"] = "wrong"
        assert weaver.otlp_result(0, json.dumps(report), expected)["status"] == "failed"


def test_terminal_requires_root_and_completed_evidence():
    report, expected = otlp_payload()
    del expected["terminal_evidence"]
    assert weaver.otlp_result(0, json.dumps(report), expected)["status"] == "passed"
    assert (
        weaver.otlp_result(0, json.dumps(report), expected, terminal=True)["status"]
        == "failed"
    )
