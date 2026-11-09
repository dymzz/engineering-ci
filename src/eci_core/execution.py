"""Execute a selection plan against its bound Git snapshot.

This is the host/Actions consumer of the plan. It never re-selects tests: it
runs exactly the plan targets, verifies the checkout tree for immutable
snapshots, and records native results separately from selection. Selected
Hygiene and source checks run even when no business tests were chosen.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from . import hygiene, selection, sources

SCHEMA_VERSION = 1
REPORT_KIND = "engineering-ci-execution-report"
_HYGIENE_CHECKS = frozenset({"docs", "tach", "vulture", "whitespace", "forwarding"})


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


def _selected_checks(plan):
    chosen = plan["selection"]
    selected = [
        item
        for item in plan.get("check_scope", chosen.get("check_scope", []))
        if item.get("status") in {"selected", "unknown"}
    ]
    if not selected and chosen.get("checks"):
        selected = [{"name": name, "status": "unknown"} for name in chosen["checks"]]
    return selected


def _config_path(plan, root, override, *, key, default_name):
    if override is not None:
        path = Path(override)
        return path if path.is_absolute() else root / path
    inputs = plan.get("inputs") or {}
    declared = inputs.get(key) if isinstance(inputs, dict) else None
    if isinstance(declared, dict) and declared.get("path"):
        path = Path(declared["path"])
        return path if path.is_absolute() else root / path
    return root / default_name


def _hygiene_config_path(plan, root, hygiene_config):
    return _config_path(
        plan, root, hygiene_config, key="hygiene", default_name="ci-hygiene.toml"
    )


def _sources_config_path(plan, root, sources_config):
    return _config_path(
        plan, root, sources_config, key="sources", default_name="ci-sources.toml"
    )


def _verify_execution_binding(plan, root):
    """Return (ok, reason, expected_tree). ok False means refuse execution."""
    git = plan["git"]
    expected_tree = git.get("execution_tree")
    snapshot = git.get("snapshot")
    if not snapshot:
        snapshot = "commit" if expected_tree else "worktree"
    if snapshot == "index":
        expected_tree = expected_tree or git.get("index_tree")
        if not expected_tree:
            return True, None, None
        current = selection._git(root, "write-tree")
        actual = current.stdout.strip() if current.returncode == 0 else None
        if actual != expected_tree:
            return False, "index_tree_mismatch", expected_tree
        return True, None, expected_tree
    if snapshot == "worktree":
        binding = git.get("content_binding")
        if isinstance(binding, dict) and binding.get("digest"):
            current = selection.worktree_content_binding(
                root, git.get("changed_paths") or ()
            )
            if current.get("digest") != binding.get("digest"):
                return False, "worktree_snapshot_stale", None
        return True, None, None
    if snapshot == "multi":
        return False, "multi_ref_requires_per_ref_execution", None
    if expected_tree:
        actual = selection._tree_of(root, "HEAD")
        if actual != expected_tree:
            reason = (
                "pr_merge_tree_unmaterialized"
                if snapshot == "pr_merge"
                else "tree_mismatch"
            )
            return False, reason, expected_tree
    head_commit = git.get("head_commit")
    if snapshot in {"commit", "push"} and head_commit:
        actual_head = selection._resolve(root, "HEAD")
        if actual_head != head_commit:
            return False, "head_commit_mismatch", expected_tree
    return True, None, expected_tree


def _changeset_from_plan(plan, root):
    git = plan["git"]
    paths = tuple(git.get("changed_paths") or ())
    complete = bool(git.get("base_commit")) and "changed_paths" in git
    return selection.ChangeSet(
        root=Path(root),
        base_ref=str(git.get("base_ref") or ""),
        head_ref=str(git.get("head_ref") or "HEAD"),
        base_commit=git.get("base_commit"),
        head_commit=git.get("head_commit"),
        tree=git.get("tree"),
        paths=paths,
        worktree_dirty=bool(git.get("worktree_dirty")),
        complete=complete,
        error=None if complete else "incomplete_plan_git",
        snapshot=str(git.get("snapshot") or "worktree"),
        index_tree=git.get("index_tree"),
        merge_tree=git.get("merge_tree"),
        base_tip=git.get("base_tip"),
    )


def _run_hygiene_checks(plan, selected, *, root, hygiene_config, runner):
    selected_by_name = {
        item["name"]: item for item in selected if item.get("name") in _HYGIENE_CHECKS
    }
    wanted = tuple(selected_by_name)
    if not wanted:
        return [], False
    config_path = _hygiene_config_path(plan, root, hygiene_config)
    try:
        config = hygiene.load_config(config_path)
    except hygiene.HygieneError as error:
        return [
            {
                "name": name,
                "selected": selected_by_name[name].get("status"),
                "status": "failed",
                "reason": f"hygiene_config_{error.code}",
            }
            for name in wanted
        ], True
    checks = tuple(name for name in config.checks if name in selected_by_name)
    if not checks:
        checks = wanted
    report = hygiene.run(
        _changeset_from_plan(plan, root),
        dataclasses.replace(config, checks=checks),
        root=root,
        runner=runner or hygiene._run,
    )
    results = []
    seen = set()
    for native in report["checks"]:
        name = native["name"]
        base = name.removesuffix("_external")
        if base not in selected_by_name and name not in selected_by_name:
            continue
        payload = dict(native)
        origin = selected_by_name.get(base) or selected_by_name.get(name)
        payload["selected"] = origin.get("status") if origin else "selected"
        results.append(payload)
        seen.add(name)
        seen.add(base)
    for name, item in selected_by_name.items():
        if name in seen:
            continue
        results.append(
            {
                "name": name,
                "selected": item.get("status"),
                "status": "not_executed",
                "reason": "hygiene_check_missing",
            }
        )
    return results, report["status"] == "failed"


def _run_source_check(plan, selected, *, root, sources_config, runner):
    item = next((entry for entry in selected if entry.get("name") == "source"), None)
    if item is None:
        return None, False
    config_path = _sources_config_path(plan, root, sources_config)
    try:
        config = sources.load_config(config_path)
    except sources.SourcesError as error:
        return {
            "name": "source",
            "selected": item.get("status"),
            "status": "failed",
            "reason": f"sources_config_{error.code}",
        }, True
    source_runner = runner or sources._run
    report = sources.reconcile(config, root=root, runner=source_runner)
    payload = {
        "name": "source",
        "selected": item.get("status"),
        "status": report.status,
        "reason": None if report.status == "passed" else "source_reconcile_failed",
        "counts": dict(report.counts),
        "failures": list(report.failures),
    }
    return payload, report.status == "failed"


def _prerequisite_actions(plan, override):
    if override is not None:
        return dict(override)
    actions = plan.get("prerequisite_actions")
    if isinstance(actions, dict):
        return dict(actions)
    inputs = plan.get("inputs") or {}
    declared = inputs.get("prerequisite_actions") if isinstance(inputs, dict) else None
    return dict(declared) if isinstance(declared, dict) else {}


def _run_prerequisites(names, actions, *, root, runner):
    results = []
    failed = False
    for name in names:
        spec = actions.get(name)
        if not isinstance(spec, dict) or not spec.get("type"):
            results.append(
                {
                    "name": name,
                    "status": "not_executed",
                    "reason": "prerequisite_parameters_missing",
                }
            )
            continue
        kind = spec.get("type")
        if kind == "git_commit_present":
            commit = spec.get("commit")
            if not isinstance(commit, str) or not commit:
                results.append(
                    {
                        "name": name,
                        "type": kind,
                        "status": "not_executed",
                        "reason": "prerequisite_parameters_missing",
                    }
                )
                continue
            probe = selection._git(root, "cat-file", "-e", f"{commit}^{{commit}}")
            if probe.returncode == 0:
                results.append(
                    {
                        "name": name,
                        "type": kind,
                        "status": "passed",
                        "commit": commit,
                    }
                )
            else:
                failed = True
                results.append(
                    {
                        "name": name,
                        "type": kind,
                        "status": "failed",
                        "reason": "identity_commit_missing",
                        "commit": commit,
                    }
                )
            continue
        if kind == "command":
            argv = spec.get("argv")
            if (
                not isinstance(argv, list)
                or not argv
                or any(not isinstance(item, str) for item in argv)
            ):
                results.append(
                    {
                        "name": name,
                        "type": kind,
                        "status": "not_executed",
                        "reason": "prerequisite_parameters_missing",
                    }
                )
                continue
            command_runner = runner or hygiene._run
            try:
                completed = command_runner(list(argv), root)
            except TypeError:
                completed = command_runner(list(argv), root, None)
            if completed.returncode == 0:
                results.append(
                    {
                        "name": name,
                        "type": kind,
                        "status": "passed",
                        "exit_code": 0,
                        "argv": list(argv),
                    }
                )
            else:
                failed = True
                results.append(
                    {
                        "name": name,
                        "type": kind,
                        "status": "failed",
                        "reason": "prerequisite_command_failed",
                        "exit_code": completed.returncode,
                        "argv": list(argv),
                        "stdout": getattr(completed, "stdout", "") or "",
                        "stderr": getattr(completed, "stderr", "") or "",
                    }
                )
            continue
        results.append(
            {
                "name": name,
                "type": kind,
                "status": "not_executed",
                "reason": "prerequisite_type_unsupported",
            }
        )
    return results, failed


def execute(
    plan,
    *,
    root,
    junit_path=None,
    hygiene_config=None,
    sources_config=None,
    prerequisite_actions=None,
    runner=None,
):
    root = Path(root)
    chosen = plan["selection"]
    tests = tuple(chosen["tests"])
    framework = chosen.get("framework", "unittest")
    selected = _selected_checks(plan)
    prerequisites = list(chosen.get("prerequisites", []))
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
    if not tests and not selected and not prerequisites:
        return report
    bound_ok, bind_reason, expected_tree = _verify_execution_binding(plan, root)
    if not bound_ok:
        report["status"] = "not_executed"
        report["exit_code"] = (
            4 if bind_reason != "multi_ref_requires_per_ref_execution" else 3
        )
        report["reason"] = bind_reason
        return report
    graph = chosen.get("tach_graph_validation") or {}
    if (
        tests
        and chosen.get("requires_tach_check")
        and not chosen.get("full_tests")
        and graph.get("status") != "passed"
    ):
        report["status"] = "blocked"
        report["exit_code"] = 5
        report["reason"] = "tach_graph_unvalidated"
        return report
    if tests and chosen.get("coverage_required"):
        report["status"] = "blocked"
        report["exit_code"] = 5
        report["reason"] = "coverage_evidence_required"
        report["coverage"]["scope"] = "not_measured"
        return report

    exit_code = 0
    if tests:
        native = {}
        exit_code = selection.run_tests(
            tests,
            root=root,
            framework=framework,
            src_root=chosen.get("src_root", "src"),
            expected_tree=expected_tree,
            junit_path=junit_path,
            capture=native,
        )
        report["exit_code"] = exit_code
        report["native_stdout"] = native.get("stdout", "")
        report["native_stderr"] = native.get("stderr", "")
        tests_status = "passed" if exit_code == 0 else "failed"
        report["tests"] = {"status": tests_status, "exit_code": exit_code}
    else:
        report["tests"] = {"status": "not_executed", "exit_code": 3}

    hygiene_results, hygiene_failed = _run_hygiene_checks(
        plan,
        selected,
        root=root,
        hygiene_config=hygiene_config,
        runner=runner,
    )
    source_result, source_failed = _run_source_check(
        plan,
        selected,
        root=root,
        sources_config=sources_config,
        runner=runner,
    )
    native_checks = list(hygiene_results)
    if source_result is not None:
        native_checks.append(source_result)
    covered = {item["name"] for item in native_checks}
    covered.update(name.removesuffix("_external") for name in covered)
    remaining = [
        {
            "name": item.get("name"),
            "selected": item.get("status"),
            "status": "not_executed",
            "reason": "no native check execution wired",
        }
        for item in selected
        if item.get("name") not in covered
    ]
    report["checks"] = native_checks + remaining
    prereq_results, prereq_failed = _run_prerequisites(
        prerequisites,
        _prerequisite_actions(plan, prerequisite_actions),
        root=root,
        runner=runner,
    )
    report["prerequisites"] = prereq_results

    unfinished = [
        item for item in report["checks"] if item.get("status") == "not_executed"
    ] + [
        item for item in report["prerequisites"] if item.get("status") == "not_executed"
    ]
    if tests and exit_code != 0:
        report["status"], report["reason"] = "failed", "tests_failed"
    elif hygiene_failed or source_failed:
        report["status"] = "failed"
        report["exit_code"] = 1
        report["reason"] = "checks_failed"
    elif prereq_failed:
        report["status"] = "failed"
        report["exit_code"] = 1
        report["reason"] = "prerequisites_failed"
    elif unfinished:
        report["status"] = "blocked"
        report["exit_code"] = 5
        report["reason"] = "required_checks_not_executed"
    else:
        report["status"], report["reason"] = "passed", None
        report["exit_code"] = 0
    return report


def _print(value):
    sys.stdout.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="engineering-ci execute",
        description=(
            "Execute the tests and selected native checks in a selection plan "
            "and report native results."
        ),
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--junit", type=Path)
    parser.add_argument(
        "--hygiene-config",
        type=Path,
        help="Hygiene config used for selected docs/tach/vulture checks",
    )
    parser.add_argument(
        "--sources-config",
        type=Path,
        help="Sources config used when check_scope selects source",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        plan = load_plan(args.plan)
    except ExecutionError as error:
        parser.exit(2, f"execution rejected: {error.code}\n")
    report = execute(
        plan,
        root=args.root,
        junit_path=args.junit,
        hygiene_config=args.hygiene_config,
        sources_config=args.sources_config,
    )
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
