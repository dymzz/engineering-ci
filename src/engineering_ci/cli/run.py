"""Unified Git hook and verification entry point: run --event pre-commit|pre-push.

Combines selection, hygiene, source reconciliation, and test execution into a
single consumer call with structured reporting, exit code forwarding, and
per-ref deduplication.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from eci_core import execution, hygiene, selection, sources


def run_pre_commit(
    *,
    root: Path,
    selection_config: Path | None = None,
    hygiene_config: Path | None = None,
    sources_config: Path | None = None,
    report_path: Path | None = None,
    junit_path: Path | None = None,
) -> dict:
    root = Path(root).resolve()
    # 1. Obtain Index snapshot
    changes = selection.changeset(root, mode="index")
    if not changes.complete:
        report = {
            "schema_version": 1,
            "kind": "engineering-ci-run-report",
            "event": "pre-commit",
            "status": "failed",
            "exit_code": 1,
            "reason": changes.error or "index_snapshot_failed",
            "changeset": {"complete": False, "paths": []},
        }
        if report_path:
            Path(report_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report

    check_results = {}

    # 2. Hygiene check if configured
    if hygiene_config and Path(hygiene_config).exists():
        try:
            cfg = hygiene.load_config(hygiene_config)
            h_report = hygiene.run_hygiene(cfg, root=root, base="HEAD")
            for c_name, c_val in h_report.get("checks", {}).items():
                st = c_val.get("status", "unknown")
                check_results[c_name] = {
                    "status": st,
                    "native_exit_code": c_val.get("native_exit_code", 0 if st == "passed" else 1),
                }
        except Exception as err:
            check_results["hygiene"] = {"status": "failed", "reason": str(err), "native_exit_code": 1}

    # 3. Sources check if configured
    if sources_config and Path(sources_config).exists():
        try:
            s_cfg = sources.load_config(sources_config)
            s_report = sources.reconcile(s_cfg, root=root)
            st = s_report.get("status", "unknown")
            check_results["source"] = {
                "status": st,
                "native_exit_code": s_report.get("exit_code", 0 if st == "passed" else 1),
            }
        except Exception as err:
            check_results["source"] = {"status": "failed", "reason": str(err), "native_exit_code": 1}

    # 4. Plan and execute tests if selection configured
    if selection_config and Path(selection_config).exists():
        cfg = selection.load_config(selection_config)
        selected = selection.select(changes, cfg, root=root)
        plan = selection.plan(selected, config=cfg, config_path=selection_config, root=root)
        exec_report = execution.execute(plan, root=root, junit_path=junit_path, check_results=check_results)
    else:
        # No test selection config: check results only
        has_failure = any(v.get("status") == "failed" for v in check_results.values())
        exec_report = {
            "schema_version": 1,
            "kind": execution.REPORT_KIND,
            "status": "failed" if has_failure else "passed",
            "exit_code": 1 if has_failure else 0,
            "targets": [],
            "checks": [
                {"name": k, "status": v.get("status")} for k, v in check_results.items()
            ],
            "execution": {"checks": check_results, "tests": {"status": "not_applicable", "exit_code": 0}},
        }

    report = {
        "schema_version": 1,
        "kind": "engineering-ci-run-report",
        "event": "pre-commit",
        "status": exec_report.get("status", "passed"),
        "exit_code": exec_report.get("exit_code", 0),
        "reason": exec_report.get("reason"),
        "execution": exec_report,
    }
    if report_path:
        Path(report_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def run_pre_push(
    *,
    root: Path,
    selection_config: Path | None = None,
    hygiene_config: Path | None = None,
    sources_config: Path | None = None,
    report_path: Path | None = None,
    junit_path: Path | None = None,
    stdin_lines: list[str] | None = None,
) -> dict:
    root = Path(root).resolve()
    if stdin_lines is None:
        stdin_lines = sys.stdin.read().splitlines()

    ref_pairs = []
    for line in stdin_lines:
        parts = line.strip().split()
        if len(parts) >= 4:
            local_ref, local_oid, remote_ref, remote_oid = parts[:4]
            ref_pairs.append((local_ref, local_oid, remote_ref, remote_oid))

    if not ref_pairs:
        # Nothing pushed
        report = {
            "schema_version": 1,
            "kind": "engineering-ci-run-report",
            "event": "pre-push",
            "status": "passed",
            "exit_code": 0,
            "refs": [],
        }
        if report_path:
            Path(report_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report

    executed_trees = {}
    ref_reports = []
    overall_exit_code = 0
    overall_status = "passed"

    for local_ref, local_oid, remote_ref, remote_oid in ref_pairs:
        if local_oid == "0" * 40:
            # Branch deletion
            ref_reports.append({
                "local_ref": local_ref,
                "status": "passed",
                "action": "delete",
            })
            continue

        target_tree = selection._tree_of(root, local_oid)
        if target_tree in executed_trees:
            cached = executed_trees[target_tree]
            ref_reports.append({
                "local_ref": local_ref,
                "local_oid": local_oid,
                "target_tree": target_tree,
                "status": cached["status"],
                "reused": True,
            })
            continue

        base_commit = remote_oid if remote_oid != "0" * 40 else None
        changes = selection.changeset(
            root,
            base=base_commit or "origin/main",
            head=local_oid,
            mode="push",
            before=base_commit,
            after=local_oid,
        )

        check_results = {}
        if hygiene_config and Path(hygiene_config).exists():
            cfg = hygiene.load_config(hygiene_config)
            h_report = hygiene.run_hygiene(cfg, root=root, base=base_commit or "HEAD")
            for c_name, c_val in h_report.get("checks", {}).items():
                st = c_val.get("status", "unknown")
                check_results[c_name] = {
                    "status": st,
                    "native_exit_code": c_val.get("native_exit_code", 0 if st == "passed" else 1),
                }

        if sources_config and Path(sources_config).exists():
            s_cfg = sources.load_config(sources_config)
            s_report = sources.reconcile(s_cfg, root=root)
            st = s_report.get("status", "unknown")
            check_results["source"] = {
                "status": st,
                "native_exit_code": s_report.get("exit_code", 0 if st == "passed" else 1),
            }

        if selection_config and Path(selection_config).exists():
            cfg = selection.load_config(selection_config)
            selected = selection.select(changes, cfg, root=root)
            plan = selection.plan(selected, config=cfg, config_path=selection_config, root=root)
            exec_report = execution.execute(plan, root=root, junit_path=junit_path, check_results=check_results)
        else:
            has_failure = any(v.get("status") == "failed" for v in check_results.values())
            exec_report = {
                "status": "failed" if has_failure else "passed",
                "exit_code": 1 if has_failure else 0,
                "targets": [],
                "execution": {"checks": check_results},
            }

        ref_entry = {
            "local_ref": local_ref,
            "local_oid": local_oid,
            "target_tree": target_tree,
            "status": exec_report.get("status", "passed"),
            "exit_code": exec_report.get("exit_code", 0),
            "execution": exec_report,
        }
        executed_trees[target_tree] = ref_entry
        ref_reports.append(ref_entry)

        if exec_report.get("exit_code", 0) != 0:
            overall_exit_code = exec_report.get("exit_code")
            overall_status = exec_report.get("status", "failed")

    report = {
        "schema_version": 1,
        "kind": "engineering-ci-run-report",
        "event": "pre-push",
        "status": overall_status,
        "exit_code": overall_exit_code,
        "refs": ref_reports,
    }
    if report_path:
        Path(report_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="engineering-ci run",
        description="Unified hook and verification entry point for pre-commit and pre-push events.",
    )
    parser.add_argument("--event", choices=("pre-commit", "pre-push"), required=True)
    parser.add_argument("--selection-config", type=Path)
    parser.add_argument("--hygiene-config", type=Path)
    parser.add_argument("--sources-config", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--junit", type=Path)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.event == "pre-commit":
        report = run_pre_commit(
            root=args.root,
            selection_config=args.selection_config,
            hygiene_config=args.hygiene_config,
            sources_config=args.sources_config,
            report_path=args.report,
            junit_path=args.junit,
        )
    else:
        report = run_pre_push(
            root=args.root,
            selection_config=args.selection_config,
            hygiene_config=args.hygiene_config,
            sources_config=args.sources_config,
            report_path=args.report,
            junit_path=args.junit,
        )

    if args.json:
        sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    else:
        print(f"event: {report['event']}")
        print(f"status: {report['status']}")
        print(f"exit_code: {report['exit_code']}")
        if report.get("reason"):
            print(f"reason: {report['reason']}")

    return report.get("exit_code", 0)


if __name__ == "__main__":
    raise SystemExit(main())
