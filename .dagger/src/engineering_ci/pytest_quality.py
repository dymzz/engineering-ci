"""Explicit pytest policy and deterministic coverage gates; standard library only."""

import json
import math
import re
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path, PurePosixPath

MAX_COVERAGE_BYTES = 2 * 1024 * 1024


def test_policy(
    test_path="tests", markers="", coverage_source="", min_coverage=""
) -> dict:
    for name, value in (
        ("test_path", test_path),
        ("markers", markers),
        ("coverage_source", coverage_source),
        ("min_coverage", min_coverage),
    ):
        if (
            not isinstance(value, str)
            or len(value) > 512
            or any(c in value for c in "\0\r\n")
        ):
            raise ValueError(f"{name} must be a bounded single-line string")
    for name, value in (
        ("test_path", test_path.split("::", 1)[0]),
        ("coverage_source", coverage_source),
    ):
        if value and (
            value.startswith("-")
            or PurePosixPath(value).is_absolute()
            or ".." in PurePosixPath(value.replace("\\", "/")).parts
            or re.match(r"^[A-Za-z]:", value)
        ):
            raise ValueError(f"{name} must be a relative workspace path or module name")
    if not test_path.strip():
        raise ValueError("test_path must not be empty")
    threshold = None
    if min_coverage:
        if not coverage_source.strip():
            raise ValueError("min_coverage requires coverage_source")
        if not re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,12})?", min_coverage):
            raise ValueError(
                "min_coverage must be a plain percentage with at most 12 decimal places"
            )
        try:
            threshold = Decimal(min_coverage)
        except InvalidOperation as exc:
            raise ValueError(
                "min_coverage must be a finite percentage between 0 and 100"
            ) from exc
        if not threshold.is_finite() or not 0 <= threshold <= 100:
            raise ValueError(
                "min_coverage must be a finite percentage between 0 and 100"
            )
    if coverage_source and not coverage_source.strip():
        raise ValueError("coverage_source must not be whitespace")
    return {
        "schema_version": 1,
        "test_path": test_path,
        "markers": markers,
        "coverage_source": coverage_source,
        "min_coverage": format(threshold, "f") if threshold is not None else "",
    }


def pytest_arguments(policy: dict, junit_path: str) -> list[str]:
    args = [
        "uv",
        "run",
        "--no-sync",
        "pytest",
        "-q",
        "--tb=short",
        f"--junitxml={junit_path}",
    ]
    if policy["markers"]:
        args += ["-m", policy["markers"]]
    if policy["coverage_source"]:
        # Separate the plugin's process status from our independently evaluated threshold.
        args += [
            f"--cov={policy['coverage_source']}",
            "--cov-fail-under=0",
            "--cov-report=xml:/tmp/engineering-ci-coverage.xml",
            "--cov-report=json:/tmp/engineering-ci-coverage.json",
        ]
    return args + [policy["test_path"]]


def read_coverage(xml_path: Path, json_path: Path) -> dict:
    evidence = {"xml": "", "json": "", "diagnostic": None}
    for key, path in (("xml", xml_path), ("json", json_path)):
        try:
            with path.open("rb") as stream:
                raw = stream.read(MAX_COVERAGE_BYTES + 1)
            if len(raw) > MAX_COVERAGE_BYTES:
                raise ValueError("coverage report exceeds the 2 MiB limit")
            evidence[key] = raw.decode("utf-8")
        except (OSError, ValueError) as exc:
            evidence["diagnostic"] = (
                f"Coverage {key} unavailable ({type(exc).__name__}); "
                + "check coverage_source and the consumer's pytest-cov dependency"
            )
    return evidence


def _coverage_counts(xml: str, raw_json: str) -> tuple[int, int]:
    if not xml or not raw_json:
        raise ValueError("Coverage XML and JSON are both required")
    if (
        len(xml.encode("utf-8")) > MAX_COVERAGE_BYTES
        or len(raw_json.encode("utf-8")) > MAX_COVERAGE_BYTES
    ):
        raise ValueError("Coverage report exceeds the 2 MiB limit")
    root = ET.fromstring(xml)
    if root.tag != "coverage":
        raise ValueError("Expected a coverage.py XML report")
    covered, total = int(root.attrib["lines-covered"]), int(root.attrib["lines-valid"])
    totals = json.loads(raw_json)["totals"]
    jcovered, jtotal = totals["covered_lines"], totals["num_statements"]
    if type(jcovered) is not int or type(jtotal) is not int:
        raise ValueError("Coverage counts must be integers")
    if (
        not 0 <= covered <= total
        or total <= 0
        or (covered, total) != (jcovered, jtotal)
    ):
        raise ValueError(
            "Coverage reports disagree or contain no measurable source lines"
        )
    return covered, total


def evaluate_quality(
    policy: dict, exit_code: int, evidence: dict | None = None
) -> dict:
    policy = test_policy(
        policy["test_path"],
        policy["markers"],
        policy["coverage_source"],
        policy["min_coverage"],
    )
    if type(exit_code) is not int or not 0 <= exit_code <= 255:
        raise ValueError("Invalid original pytest exit code")
    coverage = {
        "enabled": bool(policy["coverage_source"]),
        "source": policy["coverage_source"] or None,
        "available": None,
        "covered_lines": None,
        "total_lines": None,
        "line_percent": None,
        "required_percent": policy["min_coverage"] or None,
        "status": "disabled",
        "diagnostic": None,
    }
    reasons = (
        []
        if exit_code == 0
        else ["pytest_failed" if exit_code == 1 else "pytest_execution_error"]
    )
    if coverage["enabled"]:
        try:
            if not evidence or evidence.get("diagnostic"):
                raise ValueError(
                    (evidence or {}).get("diagnostic")
                    or "Coverage evidence unavailable"
                )
            covered, total = _coverage_counts(evidence["xml"], evidence["json"])
            coverage.update(
                available=True,
                covered_lines=covered,
                total_lines=total,
                line_percent=100 * covered / total,
                status="passed",
            )
            if policy["min_coverage"]:
                threshold = Fraction(Decimal(policy["min_coverage"]))
                if covered * 100 * threshold.denominator < threshold.numerator * total:
                    coverage["status"] = "failed"
                    reasons.append("coverage_below_threshold")
        except (ValueError, KeyError, TypeError, ET.ParseError) as exc:
            coverage.update(available=False, status="error", diagnostic=str(exc)[:400])
            reasons.append("coverage_unavailable")
    status = (
        "error"
        if "coverage_unavailable" in reasons or "pytest_execution_error" in reasons
        else "failed"
        if reasons
        else "passed"
    )
    return {
        "pytest_exit_code": exit_code,
        "selection": {
            "test_path": policy["test_path"],
            "markers": policy["markers"] or None,
            "mode": "markers" if policy["markers"] else "all_in_path",
        },
        "coverage": coverage,
        "gate": {"status": status, "passed": not reasons, "failure_reasons": reasons},
    }


def quality_summary(quality: dict) -> str:
    coverage = quality["coverage"]
    if not coverage["enabled"]:
        return f"Quality gate: {quality['gate']['status']}; original pytest exit {quality['pytest_exit_code']}"
    if not coverage["available"]:
        return f"Coverage gate error: {coverage['diagnostic']}; original pytest exit {quality['pytest_exit_code']}"
    # Only the presentation is rounded. The gate used integer counts and Decimal above.
    percent = coverage["line_percent"]
    if not math.isfinite(percent):
        raise ValueError("Invalid coverage percentage")
    threshold = coverage["required_percent"] or "not configured (collection only)"
    return (
        f"Coverage gate {coverage['status']}: line coverage {percent:.2f}%, required {threshold}%; "
        + f"original pytest exit {quality['pytest_exit_code']}"
    )
