"""Interpret native Weaver live-check evidence without replacing its findings."""

from __future__ import annotations

import json
import re
from datetime import datetime


def live_result(exit_code: int, raw_report: str, expected_matchers: list[str]) -> dict:
    """Keep the native exit and require explicit sample and matcher coverage."""
    result = {"status": "failed", "exit_code": exit_code, "reason": "report_invalid"}
    try:
        report = json.loads(raw_report)
        stats = report["statistics"]
        spans = stats["total_entities_by_type"].get("span", 0)
        violations = stats["advice_level_counts"].get("violation", 0)
        unknown_enum = stats["advice_type_counts"].get("undefined_enum_variant", 0)
        matchers = stats["matchers"]
        samples = report["samples"]
        if (
            type(spans) is not int
            or type(violations) is not int
            or type(unknown_enum) is not int
            or min(spans, violations, unknown_enum) < 0
        ):
            raise ValueError
        if not isinstance(matchers, list) or not isinstance(samples, list):
            raise TypeError
        ids = [item["id"] for item in matchers]
        if len(ids) != len(set(ids)):
            raise ValueError
        by_id = {item["id"]: item for item in matchers}
        for item in matchers:
            if (
                type(item["matched"]) is not int
                or item["matched"] < 0
                or type(item["errors"]) is not int
                or item["errors"] < 0
            ):
                raise ValueError
        matcher_errors = any(item["errors"] for item in matchers)
        matched_spans = set()
        for sample in samples:
            if "span" not in sample:
                continue
            match = sample["span"]["live_check_result"]["match_info"]
            if match.get("signal") and match.get("unmatched") is False:
                matched_spans.add(match.get("signal_matcher"))
    except (ValueError, TypeError, KeyError, AttributeError):
        if exit_code:
            result["reason"] = "tool_failed"
        return result
    result["native_report"] = report
    if violations:
        result["reason"] = "findings"
    elif exit_code:
        result["reason"] = "tool_failed"
    elif unknown_enum:
        # Weaver 0.28 may report even a wrong-typed enum only as information.
        result["reason"] = "enum_variant_unverified"
    elif matcher_errors:
        result["reason"] = "matcher_errors"
    elif (
        not expected_matchers
        or spans < 1
        or any(not isinstance(name, str) or not name for name in expected_matchers)
    ) or any(
        name not in by_id
        or name not in matched_spans
        or by_id[name]["matched"] < 1
        or by_id[name]["errors"] != 0
        for name in expected_matchers
    ):
        result["reason"] = "coverage_missing"
    else:
        result.update(status="passed", reason=None)
    return result


def otlp_result(exit_code, raw_report, expected, *, terminal=False):
    """Validate caller-declared OTLP identities, independently of finding severity."""
    failure = {
        "status": "failed",
        "reason": "expectations_invalid",
        "exit_code": exit_code,
    }
    try:
        spans = expected["spans"]
        source = expected["source"]
        if (
            expected.get("schema_version") != 1
            or not isinstance(spans, list)
            or not spans
            or not isinstance(source, dict)
            or not source.get("run_id")
            or type(source.get("attempt")) is not int
            or source["attempt"] < 1
        ):
            return failure
        identities = []
        matchers = []
        for span in spans:
            if (
                not re.fullmatch(r"[0-9a-f]{32}", span["trace_id"])
                or not re.fullmatch(r"[0-9a-f]{16}", span["span_id"])
                or int(span["trace_id"], 16) == 0
                or int(span["span_id"], 16) == 0
                or not span["matcher"]
                or not span["signal"]
            ):
                return failure
            identities.append((span["trace_id"], span["span_id"]))
            matchers.append(span["matcher"])
        if len(set(identities)) != len(identities):
            return failure
        result = live_result(exit_code, raw_report, list(dict.fromkeys(matchers)))
        result["source"] = source
        if result["status"] != "passed":
            return result
        actual = [
            sample["span"]
            for sample in result["native_report"]["samples"]
            if "span" in sample
        ]
        for wanted, identity in zip(spans, identities, strict=True):
            found = [
                item
                for item in actual
                if (item.get("trace_id"), item.get("span_id")) == identity
            ]
            if len(found) != 1:
                result.update(
                    status="failed", reason="expected_span_missing_or_duplicated"
                )
                return result
            item = found[0]
            match = item["live_check_result"]["match_info"]
            if (
                match.get("signal") != wanted["signal"]
                or match.get("signal_matcher") != wanted["matcher"]
            ):
                result.update(status="failed", reason="expected_span_match_mismatch")
                return result
            start = datetime.fromisoformat(item["start_time"])
            end = datetime.fromisoformat(item["end_time"])
            if (
                start.tzinfo is None
                or end.tzinfo is None
                or end < start
                or end.timestamp() <= 0
            ):
                result.update(status="failed", reason="span_not_completed")
                return result
        if terminal:
            evidence = expected.get("terminal_evidence", {})
            if (
                evidence.get("status") != "completed"
                or not evidence.get("conclusion")
                or evidence.get("run_id") != source["run_id"]
                or evidence.get("attempt") != source["attempt"]
                or not any(
                    item["signal"] == "cicd.pipeline.run.server" for item in spans
                )
            ):
                result.update(
                    status="failed", reason="terminal_evidence_missing_or_mismatched"
                )
                return result
        result["coverage"] = {
            "expected_spans": len(spans),
            "matched_spans": len(spans),
            "received_spans": len(actual),
            "scope": "declared_expected_set",
        }
        return result
    except (ValueError, TypeError, KeyError, AttributeError):
        return failure
