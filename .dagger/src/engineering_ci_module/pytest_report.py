"""Deterministic pytest/JUnit summary; does not attempt root-cause inference.

The report function is deliberately dependency-free so it can be tested
without a Dagger engine. It treats pytest exit codes and XML as separate
signals: missing/malformed XML is never reported as a successful test run.
"""

from __future__ import annotations

import html
import re
from xml.etree import ElementTree as ET

EXIT_REASONS = {
    0: "success",
    1: "tests_failed",
    2: "interrupted",
    3: "internal_error",
    4: "usage_error",
    5: "no_tests_collected",
    6: "warnings_limit",
}


def _bounded(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n[truncated]"


def _diagnosis(kind: str, message: str, details: str, exception_type: str = "") -> str:
    """Labels directly supported by pytest's structured outcome."""
    if kind == "error":
        if "collection failure" in message.lower():
            return "collection_error"
        if "setup" in message.lower() or "teardown" in message.lower():
            return "setup_teardown_error"
        return "test_error"
    # Pytest assertion rewriting omits the exception name: the JUnit message
    # starts with `assert ...`, and the traceback ends with `E   assert ...`.
    # Source lines in a traceback can also contain assert; they are not proof.
    explicit_assertion = exception_type.rsplit(".", 1)[-1] == "AssertionError"
    assertion_message = re.match(r"^(?:AssertionError\b|assert\s+\S)", message)
    assertion_trace = re.search(
        r"^(?:\s*(?:E\s+)?AssertionError\b|E\s+assert\s+\S)", details, re.MULTILINE
    )
    if explicit_assertion or assertion_message or assertion_trace:
        return "assertion_failure"
    return "test_failure"


def summarize_pytest(
    xml_text: str, exit_code: int, console_output: str, *, max_failures: int = 10
) -> dict:
    """Summarize one run from JUnit XML and pytest exit status.

    The diagnostic categories describe observable outcomes, not proven causes.
    """
    if not 1 <= max_failures <= 50:
        raise ValueError("max_failures must be between 1 and 50")

    result = {
        "schema_version": 1,
        "exit_code": exit_code,
        "reason": EXIT_REASONS.get(exit_code, "unexpected_exit"),
        "status": "error",
        "report_available": False,
        "counts": None,
        "failures": [],
        "omitted_failures": 0,
    }

    if not xml_text.strip():
        result["diagnostic"] = "pytest did not produce a JUnit XML report"
    else:
        try:
            root = ET.fromstring(xml_text)
            if root.tag == "testsuite":
                suites = [root]
            elif root.tag == "testsuites":
                suites = root.findall("testsuite")
            else:
                raise ValueError("Unexpected JUnit XML root element")
            if not suites:
                raise ValueError("JUnit XML contains no test suites")

            cases = list(root.iter("testcase"))
            counts = {}
            for key in ("tests", "failures", "errors", "skipped"):
                counts[key] = sum(int(suite.get(key, "0")) for suite in suites)
            counts["passed"] = max(
                0,
                counts["tests"]
                - counts["failures"]
                - counts["errors"]
                - counts["skipped"],
            )
            failures = []
            for case in cases:
                issue = case.find("error")
                kind = "error"
                if issue is None:
                    issue = case.find("failure")
                    kind = "failure"
                if issue is None:
                    continue
                message = issue.get("message", "")
                detail = issue.text or ""
                node = "::".join(
                    part
                    for part in (case.get("classname", ""), case.get("name", ""))
                    if part
                )
                failures.append(
                    {
                        "test": _bounded(node, 300),
                        "kind": kind,
                        "category": _diagnosis(
                            kind, message, detail, issue.get("type", "")
                        ),
                        "message": _bounded(message, 400),
                        "trace_excerpt": _bounded(detail, 1600),
                    }
                )
            result["report_available"] = True
            result["counts"] = counts
            result["failures"] = failures[:max_failures]
            result["omitted_failures"] = max(0, len(failures) - max_failures)
            if exit_code == 0 and counts["failures"] == 0 and counts["errors"] == 0:
                result["status"] = "passed"
            elif exit_code == 1:
                result["status"] = "failed"
            else:
                result["status"] = "error"
        except (ET.ParseError, ValueError) as exc:
            result["diagnostic"] = "Invalid JUnit XML: " + _bounded(str(exc), 400)

    if result["status"] != "passed":
        result["console_tail"] = _bounded(console_output[-2000:], 2000)
    return result


def failure_summary(report: dict) -> str:
    """Compact explanation embedded in an ordinary failing Dagger check."""
    counts = report["counts"]
    if counts is None:
        headline = report.get("diagnostic", "JUnit report unavailable")
    else:
        headline = (
            f"{counts['tests']} tests; {counts['passed']} passed, "
            f"{counts['failures']} failed, {counts['errors']} errors, "
            f"{counts['skipped']} skipped"
        )
    lines = [f"pytest exit {report['exit_code']} ({report['reason']}): {headline}"]
    for item in report["failures"]:
        lines.append(f"- {item['test']} [{item['category']}]: {item['message']}")
        if item["trace_excerpt"]:
            lines.append(item["trace_excerpt"])
    if report["omitted_failures"]:
        lines.append(f"{report['omitted_failures']} additional failures omitted")
    if not report["failures"] and report.get("console_tail"):
        lines.append(report["console_tail"])
    return "\n".join(lines)


def markdown_summary(report: dict) -> str:
    """Bounded presentation of the recorded outcome; never decides execution."""

    def cell(value, limit=400):
        text = " ".join(str(value).split())[:limit]
        return (
            html.escape(text, quote=True)
            .replace("\\", "&#92;")
            .replace("|", "&#124;")
            .replace("`", "&#96;")
        )

    execution_status = (
        "PASSED"
        if report["exit_code"] == 0
        else "FAILED"
        if report["exit_code"] == 1
        else "ERROR"
    )
    lines = [
        f"## Pytest Results — {cell(report['status'].upper())}",
        "",
        f"Test execution: **{execution_status}**; report status: **{cell(report['status'].upper())}**",
        f"Original pytest exit code: **{report['exit_code']}** ({cell(report['reason'])})",
        f"JUnit report available: **{str(report['report_available']).lower()}**",
        "",
    ]
    quality = report.get("quality")
    if quality:
        selection, coverage = quality["selection"], quality["coverage"]
        lines += [
            "| Test policy / quality | Result |",
            "| --- | --- |",
            f"| Test path | {cell(selection['test_path'], 300)} |",
            f"| Selected markers | {cell(selection['markers'] or 'all in configured path', 300)} |",
            f"| Coverage source | {cell(coverage['source'] or 'disabled', 300)} |",
        ]
        percent = (
            f"{coverage['line_percent']:.2f}%"
            if coverage["available"]
            else "unavailable"
            if coverage["enabled"]
            else "disabled"
        )
        lines += [
            f"| Line coverage | {percent} |",
            f"| Required coverage | {cell(coverage['required_percent'] or 'not configured')} |",
            f"| Coverage gate | {cell(coverage['status'].upper())} |",
            f"| Overall test quality gate | **{cell(quality['gate']['status'].upper())}** |",
            "",
        ]
        if coverage.get("diagnostic"):
            lines += ["Coverage diagnostic: " + cell(coverage["diagnostic"]), ""]
    counts = report["counts"]
    if counts is not None:
        lines += ["| Metric | Count |", "| --- | ---: |"]
        for label, key in (
            ("Total", "tests"),
            ("Passed", "passed"),
            ("Failed", "failures"),
            ("Errors", "errors"),
            ("Skipped", "skipped"),
        ):
            lines.append(f"| {label} | {counts[key]} |")
    if report.get("diagnostic"):
        lines += ["", "Report diagnostic: " + cell(report["diagnostic"])]
    failures = report["failures"]
    if failures:
        lines += [
            "",
            "### Failures",
            "",
            "| Test | Category | Message |",
            "| --- | --- | --- |",
        ]
        shown = 0
        for item in failures:
            row = f"| {cell(item['test'], 300)} | {cell(item['category'], 100)} | {cell(item['message'])} |"
            if len(("\n".join(lines) + row).encode("utf-8")) > 23000:
                break
            lines.append(row)
            shown += 1
        omitted = report["omitted_failures"] + len(failures) - shown
        if omitted:
            lines += ["", f"{omitted} additional failures omitted from this summary."]
    lines += [
        "",
        "Detailed evidence: download this job's pytest report artifact (`report.json`, optional `junit.xml`).",
        "",
    ]
    return "\n".join(lines)
