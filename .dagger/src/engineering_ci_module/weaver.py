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


def _sample_attributes(span):
    attrs = {}
    for entry in span.get("attributes") or []:
        if isinstance(entry, dict) and entry.get("name") is not None:
            attrs[str(entry["name"])] = entry.get("value")
    return attrs


def _capture_attributes(capture, trace_id, span_id):
    """Read attributes from the verified OTLP/JSON capture by Trace/Span identity."""
    if not isinstance(capture, dict):
        return {}
    attrs = {}
    for resource in capture.get("resourceSpans") or []:
        for scope in resource.get("scopeSpans") or []:
            for span in scope.get("spans") or []:
                if span.get("traceId") != trace_id or span.get("spanId") != span_id:
                    continue
                for entry in span.get("attributes") or []:
                    if not isinstance(entry, dict):
                        continue
                    key = entry.get("key")
                    value = entry.get("value") or {}
                    if key is None:
                        continue
                    if "stringValue" in value:
                        attrs[str(key)] = value["stringValue"]
                    elif "intValue" in value:
                        try:
                            attrs[str(key)] = int(value["intValue"])
                        except (TypeError, ValueError):
                            attrs[str(key)] = value["intValue"]
                    elif "boolValue" in value:
                        attrs[str(key)] = value["boolValue"]
                return attrs
    return attrs


def _capture_parent(capture, trace_id, span_id):
    if not isinstance(capture, dict):
        return None
    for resource in capture.get("resourceSpans") or []:
        for scope in resource.get("scopeSpans") or []:
            for span in scope.get("spans") or []:
                if span.get("traceId") == trace_id and span.get("spanId") == span_id:
                    return span.get("parentSpanId") or None
    return None


def _is_root_span(span, *, capture=None, trace_id=None, span_id=None):
    parent = span.get("parent_span_id") or span.get("parentSpanId")
    if parent in (None, "", "0" * 16):
        if capture is None:
            return True
        captured = _capture_parent(capture, trace_id, span_id)
        return captured in (None, "", "0" * 16)
    return False


def _merge_observed_attributes(span, *, capture=None, trace_id=None, span_id=None):
    observed = _sample_attributes(span)
    captured = _capture_attributes(capture, trace_id, span_id) if capture else {}
    for key, value in captured.items():
        if key in observed and observed[key] != value:
            raise ValueError("capture_sample_attribute_conflict")
        observed.setdefault(key, value)
    return observed


def _bind_terminal_pipeline(span, wanted, source, evidence, *, capture=None):
    """Bind observed Pipeline Root attributes to declared source/terminal facts."""
    try:
        attrs = _merge_observed_attributes(
            span,
            capture=capture,
            trace_id=wanted["trace_id"],
            span_id=wanted["span_id"],
        )
    except ValueError:
        return "terminal_capture_sample_conflict"
    run_id = attrs.get("cicd.pipeline.run.id")
    attempt = attrs.get("github.actions.run_attempt")
    result = attrs.get("cicd.pipeline.result")
    if run_id is None or attempt is None or result is None:
        return "terminal_observed_identity_missing"
    try:
        attempt_value = int(attempt)
    except (TypeError, ValueError):
        return "terminal_observed_identity_missing"
    if str(run_id) != str(source["run_id"]) or str(run_id) != str(
        evidence.get("run_id")
    ):
        return "terminal_run_id_mismatch"
    if attempt_value != source["attempt"] or attempt_value != evidence.get("attempt"):
        return "terminal_attempt_mismatch"
    if str(result) != str(evidence.get("conclusion")):
        return "terminal_result_mismatch"
    if not _is_root_span(
        span, capture=capture, trace_id=wanted["trace_id"], span_id=wanted["span_id"]
    ):
        return "terminal_span_not_root"
    return None


def otlp_result(exit_code, raw_report, expected, *, terminal=False, capture=None):
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
        matched_items = []
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
            matched_items.append((wanted, item))
        if terminal:
            evidence = expected.get("terminal_evidence", {})
            if (
                not isinstance(evidence, dict)
                or evidence.get("status") != "completed"
                or not evidence.get("conclusion")
                or evidence.get("run_id") != source["run_id"]
                or evidence.get("attempt") != source["attempt"]
            ):
                result.update(
                    status="failed", reason="terminal_evidence_missing_or_mismatched"
                )
                return result
            pipeline = [
                (wanted, item)
                for wanted, item in matched_items
                if wanted["signal"] == "cicd.pipeline.run.server"
            ]
            if not pipeline:
                result.update(
                    status="failed", reason="terminal_pipeline_signal_missing"
                )
                return result
            for wanted, item in pipeline:
                reason = _bind_terminal_pipeline(
                    item, wanted, source, evidence, capture=capture
                )
                if reason:
                    result.update(status="failed", reason=reason)
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
