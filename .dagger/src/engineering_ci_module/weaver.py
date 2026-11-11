"""Interpret native Weaver live-check evidence without replacing its findings."""

from __future__ import annotations

import json
import re
from datetime import datetime

# SemConv result attrs are extensible strings (well-known values preferred).
# Wrong JSON/OTLP types must not be treated as conformant (PR #78 P1-2).
_EXTENSIBLE_RESULT_ATTRS = frozenset(
    {
        "cicd.pipeline.result",
        "cicd.pipeline.task.run.result",
    }
)


def _invalid_extensible_result_type(samples):
    """Return attribute name when a result value is not a string."""
    for sample in samples:
        span = sample.get("span") if isinstance(sample, dict) else None
        if not isinstance(span, dict):
            continue
        for entry in span.get("attributes") or []:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if name not in _EXTENSIBLE_RESULT_ATTRS:
                continue
            if type(entry.get("value")) is not str:
                return str(name)
    return None


def _time_fraction_digits(value):
    """Fractional-second digits in a native RFC 3339 timestamp (0 = whole second)."""
    if not isinstance(value, str) or "." not in value:
        return 0
    digits = 0
    for char in value.split(".", 1)[1]:
        if not char.isdigit():
            break
        digits += 1
    return digits


def _time_provenance(items, *, capture_source):
    """Record where the observed span times came from and what they do not prove.

    A Terminal PASS proves the declared Run/Attempt/result binding and a
    completed Root span. It does not prove an exact pipeline end time or a
    duration, so no such claim is made here.
    """
    digits = set()
    for item in items:
        for key in ("start_time", "end_time"):
            digits.add(_time_fraction_digits((item or {}).get(key)))
    return {
        "capture_time_source": capture_source,
        "native_sample_time_source": ("weaver_live_check_start_time_end_time_rfc3339"),
        "sample_time_fraction_digits": sorted(digits),
        "exact_end_time_claimed": False,
        "duration_claimed": False,
    }


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
        bad_result_attr = _invalid_extensible_result_type(samples)
    except (ValueError, TypeError, KeyError, AttributeError):
        if exit_code:
            result["reason"] = "tool_failed"
        return result
    result["native_report"] = report
    if unknown_enum:
        # Preserve native count; do not globally fail extensible custom strings.
        result["undefined_enum_variant_count"] = unknown_enum
    if violations:
        result["reason"] = "findings"
    elif exit_code:
        result["reason"] = "tool_failed"
    elif bad_result_attr:
        result["reason"] = "result_type_invalid"
        result["attribute"] = bad_result_attr
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


def _otlp_attr_value(value):
    if not isinstance(value, dict):
        return None
    if "stringValue" in value:
        return value["stringValue"]
    if "intValue" in value:
        try:
            return int(value["intValue"])
        except (TypeError, ValueError):
            return value["intValue"]
    if "boolValue" in value:
        return value["boolValue"]
    return None


def _otlp_attributes(entries):
    attrs = {}
    for entry in entries or []:
        if not isinstance(entry, dict) or entry.get("key") is None:
            continue
        parsed = _otlp_attr_value(entry.get("value") or {})
        if parsed is not None:
            attrs[str(entry["key"])] = parsed
    return attrs


def _absent_parent(value):
    return value in (None, "", "0" * 16)


def lookup_capture_span(capture, trace_id, span_id):
    """Return exactly one capture hit: Resource attrs + Span + parent.

    Failures: missing_capture_span / duplicate_capture_span.
    """
    if not isinstance(capture, dict):
        return None, "missing_capture_span"
    matches = []
    for resource_span in capture.get("resourceSpans") or []:
        resource = resource_span.get("resource") or {}
        resource_attrs = _otlp_attributes(resource.get("attributes") or [])
        for scope in resource_span.get("scopeSpans") or []:
            for span in scope.get("spans") or []:
                if span.get("traceId") != trace_id or span.get("spanId") != span_id:
                    continue
                matches.append(
                    {
                        "resource_attrs": resource_attrs,
                        "span_attrs": _otlp_attributes(span.get("attributes") or []),
                        "parent_span_id": span.get("parentSpanId") or None,
                        "span": span,
                    }
                )
    if not matches:
        return None, "missing_capture_span"
    if len(matches) > 1:
        return None, "duplicate_capture_span"
    return matches[0], None


def _bind_terminal_pipeline(span, wanted, source, evidence, *, capture=None):
    """Bind observed Pipeline Root to declared provenance using SemConv fields.

    Canonical Run ID comes from the enclosing OTLP Resource. Pipeline result
    comes from the Span. Attempt is ECI/GitHub correlation, not SemConv Required.
    """
    if capture is None:
        return "terminal_capture_required", None
    observed, error = lookup_capture_span(
        capture, wanted["trace_id"], wanted["span_id"]
    )
    if error:
        return error, None

    resource_attrs = observed["resource_attrs"]
    span_attrs = observed["span_attrs"]
    sample_attrs = _sample_attributes(span)
    run_id = resource_attrs.get("cicd.pipeline.run.id")
    if run_id is None:
        return "terminal_observed_identity_missing", None
    for extra in (
        span_attrs.get("cicd.pipeline.run.id"),
        sample_attrs.get("cicd.pipeline.run.id"),
    ):
        if extra is not None and str(extra) != str(run_id):
            return "terminal_run_id_conflict", None
    capture_result = span_attrs.get("cicd.pipeline.result")
    sample_result = sample_attrs.get("cicd.pipeline.result")
    if (
        capture_result is not None
        and sample_result is not None
        and str(capture_result) != str(sample_result)
    ):
        return "terminal_result_conflict", None
    result = capture_result if capture_result is not None else sample_result
    if result is None:
        return "terminal_observed_result_missing", None
    if str(run_id) != str(source["run_id"]) or str(run_id) != str(
        evidence.get("run_id")
    ):
        return "terminal_run_id_mismatch", None
    expected_result = evidence.get("result")
    if expected_result is None:
        return "terminal_evidence_missing_or_mismatched", None
    if str(result) != str(expected_result):
        return "terminal_result_mismatch", None

    sample_parent = span.get("parent_span_id") or span.get("parentSpanId")
    capture_parent = observed["parent_span_id"]
    if (
        not _absent_parent(sample_parent)
        and not _absent_parent(capture_parent)
        and str(sample_parent) != str(capture_parent)
    ):
        return "terminal_capture_sample_conflict", None
    if not _absent_parent(sample_parent) or not _absent_parent(capture_parent):
        return "terminal_span_not_root", None

    # github.actions.run_attempt is ECI/GitHub correlation, not SemConv Required.
    # weaver-terminal still cannot PASS a Run/Attempt gate without verified
    # Attempt evidence bound to the matched capture (PR #78 P1-1).
    attempt_attr = span_attrs.get("github.actions.run_attempt")
    if attempt_attr is None:
        attempt_attr = sample_attrs.get("github.actions.run_attempt")
    if attempt_attr is None:
        return "terminal_attempt_correlation_unverified", {
            "status": "unverified",
            "reason": "attempt_extension_absent",
            "scope": "capture_extension_vs_declared",
            "source": "otlp_capture_or_sample",
            "declared_attempt": source["attempt"],
            # Not #75/#63 GitHub authentication or admission.
            "authenticated": False,
        }
    try:
        attempt_value = int(attempt_attr)
    except (TypeError, ValueError):
        return "terminal_attempt_extension_invalid", None
    if attempt_value != source["attempt"] or attempt_value != evidence.get("attempt"):
        return "terminal_attempt_mismatch", None
    return None, {
        # Capture/sample attribute matched declared Attempt — not provenance auth.
        "status": "capture_attempt_matched",
        "attempt": attempt_value,
        "scope": "capture_extension_vs_declared",
        "source": "otlp_capture_or_sample",
        "authenticated": False,
    }


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
        # Terminal correlation is a separate phase. Until it runs, mark it
        # not_executed so coverage does not invent "executed" from Check kind.
        if terminal:
            result["terminal_correlation_status"] = "not_executed"
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
        result["time_provenance"] = _time_provenance(
            [item for _, item in matched_items],
            capture_source="otlp_span_startTimeUnixNano_endTimeUnixNano",
        )
        if terminal:
            evidence = expected.get("terminal_evidence", {})
            if (
                not isinstance(evidence, dict)
                or evidence.get("status") != "completed"
                or not evidence.get("result")
                or evidence.get("run_id") != source["run_id"]
                or evidence.get("attempt") != source["attempt"]
            ):
                result.update(
                    status="failed",
                    reason="terminal_evidence_missing_or_mismatched",
                    native_live_status="passed",
                    terminal_correlation_status="failed",
                )
                return result
            pipeline = [
                (wanted, item)
                for wanted, item in matched_items
                if wanted["signal"] == "cicd.pipeline.run.server"
            ]
            if not pipeline:
                result.update(
                    status="failed",
                    reason="terminal_pipeline_signal_missing",
                    native_live_status="passed",
                    terminal_correlation_status="failed",
                )
                return result
            correlations = []
            for wanted, item in pipeline:
                reason, attempt_correlation = _bind_terminal_pipeline(
                    item, wanted, source, evidence, capture=capture
                )
                if attempt_correlation is not None:
                    correlations.append(attempt_correlation)
                if reason:
                    result.update(status="failed", reason=reason)
                    if correlations:
                        result["attempt_correlation"] = (
                            correlations[0] if len(correlations) == 1 else correlations
                        )
                    # Native matcher/finding policy for this declared set only —
                    # not a claim of full CI/CD SemConv Required coverage.
                    result["native_live_status"] = "passed"
                    result["terminal_correlation_status"] = "failed"
                    return result
            result["attempt_correlation"] = (
                correlations[0] if len(correlations) == 1 else correlations
            )
            result["native_live_status"] = "passed"
            result["terminal_correlation_status"] = "passed"
        result["coverage"] = {
            "expected_spans": len(spans),
            "matched_spans": len(spans),
            "received_spans": len(actual),
            "scope": "declared_expected_set",
        }
        return result
    except (ValueError, TypeError, KeyError, AttributeError):
        return failure
