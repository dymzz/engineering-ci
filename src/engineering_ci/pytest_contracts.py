"""Shared pure-Python pytest schema v1, JUnit XML, and ReportLog contracts.

This module intentionally uses only the Python standard library so both the
host verification layer and pure-data contract checks share the exact same
rules without adding runtime dependencies or touching standalone Dagger bundles.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import Any

NATIVE_PYTEST_SCHEMA_VERSION = 1

PYTEST_EXIT_REASONS = {
    0: "success",
    1: "tests_failed",
    2: "interrupted",
    3: "internal_error",
    4: "usage_error",
    5: "no_tests_collected",
    6: "warnings_limit",
}

NATIVE_PYTEST_STATUSES = frozenset({"passed", "failed", "error"})
NATIVE_PYTEST_COUNT_FIELDS = ("tests", "failures", "errors", "skipped", "passed")
PYTEST_INVOCATION_KIND = "engineering-ci-pytest-invocation"
PYTEST_INVOCATION_SCHEMA_VERSION = 1


def is_native_pytest_v1_shape(payload: dict[str, Any]) -> bool:
    """Return True when payload matches Dagger python_test_artifacts schema v1 JSON."""
    if "kind" in payload or "checks" in payload or "execution_identity" in payload:
        return False
    return {"report_available", "failures", "omitted_failures"}.issubset(payload)


def validate_native_pytest_v1(payload: dict[str, Any]) -> str | None:
    """Validate native Dagger pytest schema v1 contract per docs/architecture/testing/pytest-reporting.md."""
    if payload.get("schema_version") != NATIVE_PYTEST_SCHEMA_VERSION:
        return "native_pytest_schema_unsupported"
    exit_code = payload.get("exit_code")
    if type(exit_code) is not int or not 0 <= exit_code <= 255:
        return "native_pytest_exit_code_invalid"
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason:
        return "native_pytest_reason_invalid"
    status = payload.get("status")
    if status not in NATIVE_PYTEST_STATUSES:
        return "native_pytest_status_invalid"
    report_available = payload.get("report_available")
    if type(report_available) is not bool:
        return "native_pytest_report_available_invalid"
    failures = payload.get("failures")
    omitted = payload.get("omitted_failures")
    if not isinstance(failures, list) or type(omitted) is not int or omitted < 0:
        return "native_pytest_failures_invalid"
    counts = payload.get("counts")
    if report_available:
        if not isinstance(counts, dict):
            return "native_pytest_counts_missing"
        for field in NATIVE_PYTEST_COUNT_FIELDS:
            val = counts.get(field)
            if type(val) is not int or val < 0:
                return "native_pytest_counts_invalid"
        if exit_code == 0 and counts["failures"] == 0 and counts["errors"] == 0:
            expected_status = "passed"
        elif exit_code == 1:
            expected_status = "failed"
        else:
            expected_status = "error"
        if status != expected_status:
            return "native_pytest_status_mismatch"
    else:
        if counts is not None or status != "error":
            return "native_pytest_unavailable_status_invalid"
        diag = payload.get("diagnostic")
        if not isinstance(diag, str) or not diag:
            return "native_pytest_diagnostic_missing"
    return None


def parse_junit_suites_and_counts(
    root: ET.Element,
) -> tuple[list[ET.Element], dict[str, int]]:
    """Validate JUnit root structure and aggregate non-negative suite counts strictly."""
    if root.tag == "testsuite":
        suites = [root]
    elif root.tag == "testsuites":
        # Direct child testsuites only so nested sub-suites never double-count.
        suites = root.findall("testsuite")
    else:
        raise ValueError(f"Unexpected JUnit XML root element: {root.tag}")
    if not suites:
        raise ValueError("JUnit XML contains no test suites")

    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for attr in ("tests", "failures", "errors", "skipped"):
            raw_num = suite.attrib.get(attr)
            if raw_num is None:
                continue
            stripped = raw_num.strip()
            if not stripped.isdigit():
                raise ValueError(f"Invalid JUnit count attribute {attr!r}: {raw_num!r}")
            totals[attr] += int(stripped)
    totals["passed"] = max(
        0,
        totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"],
    )
    return suites, totals


def is_pytest_invocation_metadata(payload: Any) -> bool:
    """Return True when JSON matches #168 pytest invocation metadata/summary schema."""
    return (
        isinstance(payload, dict)
        and payload.get("kind") == PYTEST_INVOCATION_KIND
        and payload.get("schema_version") == PYTEST_INVOCATION_SCHEMA_VERSION
        and isinstance(payload.get("invocation_id"), str)
        and bool(payload.get("invocation_id"))
    )


def canonical_tuple_key(*parts: Any) -> str:
    """Build an injection-proof canonical key string without ambiguity on delimiters."""
    return json.dumps(list(parts), separators=(",", ":"), ensure_ascii=True)
