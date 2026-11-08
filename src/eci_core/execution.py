"""Execute a selection plan against its bound Git snapshot.

This is the host/Actions consumer of the plan. It never re-selects tests: it
runs exactly the plan targets, verifies the checkout tree for immutable
snapshots, and records native results separately from selection.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import selection

SCHEMA_VERSION = 1
REPORT_KIND = "engineering-ci-execution-report"


class ExecutionError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def load_plan(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise ExecutionError("plan_unreadable") from None
    if (
        not isinstance(value, dict)
        or value.get("kind") != selection.PLAN_KIND
        or value.get("schema_version") != SCHEMA_VERSION
    ):
        raise ExecutionError("plan_invalid")
    chosen = value.get("selection")
    git = value.get("git")
    if not isinstance(chosen, dict) or not isinstance(git, dict):
        raise ExecutionError("plan_invalid")
    tests = chosen.get("tests")
    if not isinstance(tests, list) or any(not isinstance(item, str) for item in tests):
        raise ExecutionError("tests_invalid")
    framework = chosen.get("framework", "unittest")
    if framework not in {"unittest", "pytest"}:
        raise ExecutionError("framework_invalid")
    return value


def execute(plan, *, root, junit_path=None):
    root = Path(root)
    chosen = plan["selection"]
    tests = tuple(chosen["tests"])
    framework = chosen.get("framework", "unittest")
    tree = plan["git"].get("execution_tree")
    report = {
        "schema_version": SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "plan": {
            "generator": plan.get("generator"),
            "git": plan.get("git"),
        },
        "targets": list(tests),
        "framework": framework,
        "junit": str(junit_path) if junit_path else None,
        "status": "not_executed",
        "exit_code": 3,
        "reason": "no_selected_tests",
        "coverage": {
            "required": bool(chosen.get("coverage_required")),
            "satisfied": False,
            "scope": "unverified",
        },
    }
    if not tests:
        return report
    if plan["git"].get("snapshot") == "multi":
        report["reason"] = "multi_ref_requires_per_ref_execution"
        return report
    if tree and selection._tree_of(root, "HEAD") != tree:
        report["status"] = "not_executed"
        report["exit_code"] = 4
        report["reason"] = "tree_mismatch"
        return report
    graph = chosen.get("tach_graph_validation") or {}
    if (
        chosen.get("requires_tach_check")
        and not chosen.get("full_tests")
        and graph.get("status") != "passed"
    ):
        report["status"] = "blocked"
        report["exit_code"] = 5
        report["reason"] = "tach_graph_unvalidated"
        return report
    if chosen.get("coverage_required"):
        report["status"] = "blocked"
        report["exit_code"] = 5
        report["reason"] = "coverage_evidence_required"
        report["coverage"]["scope"] = "not_measured"
        return report
    native = {}
    exit_code = selection.run_tests(
        tests,
        root=root,
        framework=framework,
        src_root=chosen.get("src_root", "src"),
        expected_tree=None,
        junit_path=junit_path,
        capture=native,
    )
    report["exit_code"] = exit_code
    report["native_stdout"] = native.get("stdout", "")
    report["native_stderr"] = native.get("stderr", "")
    tests_status = "passed" if exit_code == 0 else "failed"
    report["tests"] = {"status": tests_status, "exit_code": exit_code}
    selected_checks = [
        item
        for item in chosen.get("check_scope", [])
        if item.get("status") in {"selected", "unknown"}
    ]
    if not selected_checks and chosen.get("checks"):
        selected_checks = [
            {"name": name, "status": "unknown"} for name in chosen["checks"]
        ]
    report["checks"] = [
        {
            "name": item.get("name"),
            "selected": item.get("status"),
            "status": "not_executed",
            "reason": "no native check execution wired",
        }
        for item in selected_checks
    ]
    report["prerequisites"] = [
        {"name": name, "status": "not_executed"}
        for name in chosen.get("prerequisites", [])
    ]
    if exit_code != 0:
        report["status"], report["reason"] = "failed", "tests_failed"
    elif report["checks"] or report["prerequisites"]:
        report["status"] = "blocked"
        report["exit_code"] = 5
        report["reason"] = "required_checks_not_executed"
    else:
        report["status"], report["reason"] = "passed", None
    return report


def _print(value):
    sys.stdout.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="engineering-ci execute",
        description="Execute exactly the tests in a selection plan and report native results.",
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--junit", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        plan = load_plan(args.plan)
    except ExecutionError as error:
        parser.exit(2, f"execution rejected: {error.code}\n")
    report = execute(plan, root=args.root, junit_path=args.junit)
    if args.json:
        _print(report)
    else:
        print(f"status: {report['status']}")
        print(f"targets: {' '.join(report['targets']) or '(none)'}")
        print(f"exit_code: {report['exit_code']}")
        if report["reason"]:
            print(f"reason: {report['reason']}")
    if report["status"] == "passed":
        return 0
    if report["status"] == "not_executed":
        return 3
    if report["status"] == "blocked":
        return 5
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
