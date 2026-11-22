"""Invoke and verify the native Dagger Plan result used by a Git Hook."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

from . import execution, git_source, hooks, selection

_ECI_MODULE = re.compile(r"github\.com/dymzz/engineering-ci@[0-9a-f]{40}")


def module_reference(value, *, root):
    """Admit the explicit module selected by the Hook configuration."""
    if value is None:
        raise hooks.HookEventError("hook_dagger_module_required")
    if value == ".":
        if Path(root).resolve() != Path(__file__).resolve().parents[2]:
            raise hooks.HookEventError("hook_dagger_local_module_outside_eci_checkout")
    elif not isinstance(value, str) or not _ECI_MODULE.fullmatch(value):
        raise hooks.HookEventError("hook_dagger_module_revision_required")
    return value


def required(plan, *, python_project):
    chosen = plan["selection"]
    return (
        bool(execution.pre_execution_edges(plan))
        or any(
            check.get("name") in {"weaver_registry", "weaver_live", "weaver_terminal"}
            for check in execution._selected_checks(plan)
        )
        or any(
            (plan.get("prerequisite_actions") or {}).get(name, {}).get("type")
            == "dagger_command"
            for name in chosen.get("prerequisites") or ()
        )
        or (bool(chosen.get("tests")) and python_project)
    )


def execute(plan, *, source, checkout, destination, module_ref, run_ref, attempt_ref):
    source_tree_id = plan["git"].get("source_tree_id")
    if not source_tree_id or selection.directory_content_id(source) != source_tree_id:
        raise hooks.HookEventError("hook_dagger_source_unverified")
    executable = shutil.which("dagger")
    if not executable or Path(executable).resolve().is_relative_to(source):
        raise hooks.HookEventError("hook_dagger_cli_unavailable")
    if destination.exists():
        raise hooks.HookEventError("hook_dagger_result_already_exists")
    module_ref = module_reference(module_ref, root=checkout)
    if module_ref == ".":
        manifest_path = source / "dagger-module.toml"
        if manifest_path.is_symlink():
            raise hooks.HookEventError("hook_dagger_local_module_unavailable")
        try:
            with manifest_path.open("rb") as manifest:
                module_name = tomllib.load(manifest).get("name")
        except (OSError, ValueError):
            raise hooks.HookEventError("hook_dagger_local_module_unavailable") from None
        if module_name != "engineering-ci":
            raise hooks.HookEventError("hook_dagger_local_module_identity_mismatch")
    # A Windows child of the CLI can briefly keep the workspace open after
    # export. Publish the verified bundle before best-effort temp cleanup.
    with tempfile.TemporaryDirectory(
        prefix="eci-dagger-plan-", ignore_cleanup_errors=os.name == "nt"
    ) as temporary:
        scratch = Path(temporary).resolve()
        plan_file = scratch / "plan.json"
        plan_file.write_bytes(hooks.canonical_event_bytes(plan))
        export_source = scratch / "source"
        shutil.copytree(
            source, export_source, symlinks=True, ignore=shutil.ignore_patterns(".git")
        )
        if any(
            item.get("name") in {"forwarding", "source", "whitespace"}
            for item in execution._selected_checks(plan)
        ):
            git_source.add_git_context(source, export_source, plan["git"])
        bundle = scratch / "result"
        # Resolve the File and Directory from a common workspace. The Windows
        # CLI otherwise uses the absolute File path as an include pattern.
        result = subprocess.run(
            [
                executable,
                "--progress=plain",
                "--workspace",
                ".",
                "api",
                "call",
                "-m",
                "./source" if module_ref == "." else module_ref,
                "plan-execution",
                "--plan",
                "plan.json",
                "--source",
                "./source",
                "--run-ref",
                run_ref,
                "--attempt-ref",
                attempt_ref,
                "artifacts",
                "export",
                "--path",
                "result",
            ],
            cwd=scratch,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode:
            raise hooks.HookEventError(
                result.stderr.strip()
                or f"hook_dagger_execution_failed ({result.returncode})"
            )
        report = load_result(bundle, plan, run_ref=run_ref, attempt_ref=attempt_ref)
        shutil.copytree(bundle, destination, symlinks=True)
        return report


def load_result(destination, plan, *, run_ref, attempt_ref):
    """Read one retained native result and its original exit without rerunning it."""
    if (
        destination.is_symlink()
        or destination.is_junction()
        or not destination.is_dir()
    ):
        raise hooks.HookEventError("hook_dagger_result_missing")
    try:
        for name in ("plan.json", "report.json", "exit-code.txt"):
            if (destination / name).is_symlink():
                raise hooks.HookEventError("hook_dagger_artifact_outside_result")
        plan_payload = (destination / "plan.json").read_bytes()
        report = json.loads((destination / "report.json").read_bytes())
        original_exit = int((destination / "exit-code.txt").read_bytes())
    except (OSError, ValueError):
        raise hooks.HookEventError("hook_dagger_result_invalid") from None
    if plan_payload != hooks.canonical_event_bytes(plan):
        raise hooks.HookEventError("hook_dagger_plan_mismatch")
    if (
        not isinstance(report, dict)
        or report.get("kind") != execution.REPORT_KIND
        or type(report.get("schema_version")) is not int
        or report.get("schema_version") != execution.SCHEMA_VERSION
        or type(report.get("exit_code")) is not int
        or report["exit_code"] != original_exit
        or (original_exit == 0 and report.get("status") != "passed")
    ):
        raise hooks.HookEventError("hook_dagger_original_exit_mismatch")
    identity = report.get("execution_identity") or {}
    if original_exit == 0 and (
        identity.get("run_ref"),
        identity.get("attempt_ref"),
    ) != (run_ref, attempt_ref):
        raise hooks.HookEventError("hook_dagger_attempt_mismatch")
    return report


def source_bound(plan, report):
    git = plan["git"]
    handoff = report.get("source_handoff") or {}
    identity = report.get("execution_identity") or {}
    return (
        report.get("execution_context") == "dagger_export"
        and bool(git.get("source_tree_id"))
        and report.get("tree_verified") is True
        and report.get("source_tree_verified") == "verified"
        and report.get("plan_execution_tree") == git.get("execution_tree")
        and report.get("source_tree_id") == git["source_tree_id"]
        and report.get("source_digest") == git["source_tree_id"]
        and handoff.get("exported_content_id") == git["source_tree_id"]
        and handoff.get("original_git") == git
        and handoff.get("parent_execution_ref") == identity.get("execution_ref")
        and handoff.get("run_ref") == identity.get("run_ref")
        and handoff.get("attempt_ref") == identity.get("attempt_ref")
    )
