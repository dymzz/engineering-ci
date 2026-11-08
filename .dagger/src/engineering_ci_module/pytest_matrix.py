"""Bounded matrix configuration, aggregation and verification of execution facts.

This module is also shipped with exported bundles. Its gate uses original exit
codes, runtime evidence, JUnit and coverage, never the presentation JSON.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

SUPPORTED_PYTHONS = ("3.10", "3.11", "3.12", "3.13", "3.14")
KIND = "python_compatibility_matrix"


def matrix_policy(versions, concurrency=1, max_failures=10, policy=None) -> dict:
    if not isinstance(versions, list) or not 1 <= len(versions) <= len(
        SUPPORTED_PYTHONS
    ):
        raise ValueError("Specify 1 to 5 Python minor versions")
    if any(not isinstance(v, str) or v not in SUPPORTED_PYTHONS for v in versions):
        raise ValueError(f"Supported Python versions: {', '.join(SUPPORTED_PYTHONS)}")
    if len(set(versions)) != len(versions):
        raise ValueError("Duplicate Python versions are not allowed")
    if type(concurrency) is not int or not 1 <= concurrency <= 3:
        raise ValueError("max_concurrency must be between 1 and 3 (default 1)")
    if type(max_failures) is not int or not 1 <= max_failures <= 50:
        raise ValueError("max_failures must be between 1 and 50")
    return {
        "schema_version": 1,
        "kind": KIND,
        "requested_versions": list(versions),
        "max_concurrency": concurrency,
        "max_failures": max_failures,
        "test_policy": policy or {},
    }


def supports_python(requirement: str, actual: str) -> bool:
    # Only the SDK preflight needs packaging; exported delivery stays stdlib.
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version

    if not isinstance(requirement, str) or len(requirement) > 4096:
        raise ValueError("Invalid project.requires-python")
    return SpecifierSet(requirement).contains(Version(actual), prereleases=True)


def runtime_problem(
    runtime: dict, requested: str, process_code: int | None
) -> str | None:
    if not isinstance(runtime, dict) or runtime.get("schema_version") != 1:
        return "runtime_unavailable"
    actual = runtime.get("actual_python", "")
    if not isinstance(actual, str) or not re.fullmatch(
        r"\d+\.\d+\.\d+(?:[a-zA-Z0-9.+-]*)", actual
    ):
        return "runtime_unavailable"
    if actual.split(".")[:2] != requested.split("."):
        return "interpreter_mismatch"
    if runtime.get("requested_python") != requested:
        return "runtime_unavailable"
    if runtime.get("error"):
        return (
            runtime["error"]
            if runtime["error"] == "pytest_unavailable"
            else "runtime_unavailable"
        )
    if runtime.get("pytest_started") is not True:
        return "runtime_unavailable"
    code = runtime.get("pytest_exit_code")
    if type(code) is not int or not 0 <= code <= 255 or code != process_code:
        return "runtime_unavailable"
    if (
        not runtime.get("executable")
        or not runtime.get("prefix")
        or not runtime.get("base_prefix")
        or runtime["prefix"] == runtime["base_prefix"]
    ):
        return "runtime_unavailable"
    return None


def version_result(requested: str, facts: dict, runtime=None, report=None) -> dict:
    runtime = runtime or {}
    row = {
        "requested_python": requested,
        "actual_python": runtime.get("actual_python"),
        "status": "error",
        "failure_category": None,
        "phase": facts.get("phase"),
        "process_exit_code": facts.get("process_exit_code"),
        "pytest_exit_code": None,
        "runtime": runtime,
        "pytest": report,
        "elapsed_seconds": facts.get("elapsed_seconds"),
        "diagnostic": facts.get("diagnostic"),
    }
    phase = facts.get("phase")
    if phase != "pytest":
        category = facts.get("failure_category")
        allowed = {
            "python_unsupported",
            "environment_initialization",
            "dependency_installation",
            "configuration_error",
            "interpreter_mismatch",
            "report_unavailable",
        }
        row["failure_category"] = (
            category if category in allowed else "report_unavailable"
        )
        if category == "python_unsupported":
            row["status"] = "unsupported"
        return row
    problem = runtime_problem(runtime, requested, facts.get("process_exit_code"))
    if (
        facts.get("validated_python")
        and runtime.get("actual_python") != facts["validated_python"]
    ):
        problem = "interpreter_mismatch"
    if problem:
        row["failure_category"] = problem
        return row
    code = runtime["pytest_exit_code"]
    row["pytest_exit_code"] = code
    if code != 0:
        row["status"] = "failed"
        row["failure_category"] = (
            "collection_error"
            if code == 2 and report and (report.get("counts") or {}).get("errors", 0)
            else "pytest_failure"
            if code == 1
            else "pytest_execution_error"
        )
    elif (
        not report
        or not report.get("report_available")
        or report.get("exit_code") != code
        or any(
            (report.get("counts") or {}).get(key, 0) for key in ("failures", "errors")
        )
    ):
        row["failure_category"] = "report_unavailable"
    elif report.get("quality") and not report["quality"]["gate"]["passed"]:
        row["status"] = "failed"
        row["failure_category"] = (
            "coverage_failure"
            if report["quality"]["coverage"]["status"] == "failed"
            else "coverage_unavailable"
        )
    else:
        row["status"] = "passed"
    return row


def aggregate(plan: dict, rows: list[dict]) -> dict:
    expected = plan["requested_versions"]
    by_version = {}
    for row in rows:
        version = row.get("requested_python")
        if version not in expected or version in by_version:
            raise ValueError("Unexpected or duplicate matrix result")
        by_version[version] = row
    ordered = [
        by_version.get(v)
        or version_result(
            v,
            {
                "phase": "delivery",
                "failure_category": "report_unavailable",
                "diagnostic": "Required Python result is missing",
            },
        )
        for v in expected
    ]
    overall = (
        "passed"
        if all(r["status"] == "passed" for r in ordered)
        else "error"
        if any(r["status"] in {"error", "unsupported"} for r in ordered)
        else "failed"
    )
    return {
        "schema_version": 1,
        "kind": KIND,
        "overall_status": overall,
        "required_versions": expected,
        "max_concurrency": plan["max_concurrency"],
        "versions": ordered,
    }


def matrix_summary(report: dict) -> str:
    def cell(value):
        return (
            str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")[:350]
        )

    lines = [
        f"## Python Compatibility Matrix: {report['overall_status'].upper()}",
        "",
        "| Requested | Actual | Tests | Coverage | Result | Category |",
        "|---|---|---:|---:|---|---|",
    ]
    for row in report["versions"]:
        pytest = row.get("pytest") or {}
        coverage = pytest.get("quality", {}).get("coverage", {})
        lines.append(
            "| "
            + " | ".join(
                cell(v)
                for v in (
                    row["requested_python"],
                    row.get("actual_python") or "unavailable",
                    (pytest.get("counts") or {}).get("tests", "—"),
                    coverage.get("line_percent", "—"),
                    row["status"].upper(),
                    row.get("failure_category") or "—",
                )
            )
            + " |"
        )
        if row["status"] != "passed":
            lines.extend(
                [
                    "",
                    f"Python {row['requested_python']}: {cell(row.get('diagnostic') or row['failure_category'])}",
                ]
            )
            for failure in pytest.get("failures", [])[:3]:
                lines.append(
                    f"- {cell(failure.get('test', failure.get('name', 'unknown')))}: "
                    f"{cell(failure.get('message', ''))}"
                )
            omitted = pytest.get("omitted_failures", 0) + max(
                0, len(pytest.get("failures", [])) - 3
            )
            if omitted:
                lines.append(f"- {omitted} additional failures omitted from summary.")
    return "\n".join(lines) + "\n"


def read_json(path: Path, limit=8192) -> dict:
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ValueError("Matrix evidence exceeds size limit")
    result = json.loads(content)
    if not isinstance(result, dict):
        raise TypeError("Invalid matrix evidence")
    return result


def verify_bundle(directory: Path) -> dict:
    # Imports work in an exported directory and in the Dagger package.
    if __package__:
        from .pytest_quality import evaluate_quality, read_coverage, test_policy
        from .pytest_report import summarize_pytest
    else:
        from pytest_quality import evaluate_quality, read_coverage, test_policy
        from pytest_report import summarize_pytest

    supplied = read_json(directory / "matrix-policy.json")
    if supplied.get("schema_version") != 1 or supplied.get("kind") != KIND:
        raise ValueError("Unsupported matrix policy")
    policy = supplied["test_policy"]
    normalized = test_policy(
        policy["test_path"],
        policy["markers"],
        policy["coverage_source"],
        policy["min_coverage"],
    )
    if policy != normalized:
        raise ValueError("Invalid matrix test policy")
    plan = matrix_policy(
        supplied["requested_versions"],
        supplied["max_concurrency"],
        supplied["max_failures"],
        policy,
    )
    rows = []
    for version in plan["requested_versions"]:
        folder = directory / "versions" / version
        try:
            facts = read_json(folder / "execution.json")
            runtime = (
                read_json(folder / "runtime.json")
                if (folder / "runtime.json").exists()
                else {}
            )
            report = None
            if facts.get("phase") == "pytest" and runtime.get("pytest_started"):
                body = (folder / "exit-code.txt").read_text(encoding="ascii")
                if not re.fullmatch(r"\d{1,3}\n?", body) or not 0 <= int(body) <= 255:
                    raise ValueError("Invalid original pytest exit code")
                if type(facts.get("process_exit_code")) is not int or facts[
                    "process_exit_code"
                ] != int(body):
                    raise ValueError("Execution exit code evidence disagrees")
                xml = ""
                if (folder / "junit.xml").exists():
                    with (folder / "junit.xml").open("rb") as stream:
                        content = stream.read(2 * 1024 * 1024 + 1)
                    if len(content) > 2 * 1024 * 1024:
                        raise ValueError("JUnit XML exceeds size limit")
                    xml = content.decode("utf-8")
                report = summarize_pytest(
                    xml, int(body), "", max_failures=plan["max_failures"]
                )
                if policy["markers"] or policy["coverage_source"]:
                    report["quality"] = evaluate_quality(
                        policy,
                        int(body),
                        read_coverage(folder / "coverage.xml", folder / "coverage.json")
                        if policy["coverage_source"]
                        else None,
                    )
            rows.append(version_result(version, facts, runtime, report))
        except (OSError, ValueError, KeyError, TypeError, UnicodeError):
            rows.append(
                version_result(
                    version,
                    {
                        "phase": "delivery",
                        "failure_category": "report_unavailable",
                        "diagnostic": "Missing, invalid or inconsistent execution evidence",
                    },
                )
            )
    return aggregate(plan, rows)
