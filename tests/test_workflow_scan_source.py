"""NEW-055: Workflow/Shell scan-target Source admission (resolved path + digest)."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from engineering_ci import workflow_analysis


def _symlink(target: Path, link: Path) -> None:
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink unavailable on this host")


def _write_workflow(path: Path, name: str = "ci") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"name: {name}\non: push\njobs:\n  x:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: echo ok\n",
        encoding="utf-8",
    )


def _write_shell(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")


def _actionlint_runner(argv, root, data=None):
    text = " ".join(str(part) for part in argv)
    if "-version" in argv or "--version" in argv:
        return subprocess.CompletedProcess(
            argv, 0, f"{workflow_analysis.ACTIONLINT_VERSION}\n", ""
        )
    if "actionlint" in text:
        return subprocess.CompletedProcess(argv, 0, "[]\n", "")
    return subprocess.CompletedProcess(argv, 0, "", "")


def _patch_resolve_escape(monkeypatch, lexical: Path, outside: Path) -> None:
    real_resolve = Path.resolve

    def fake_resolve(self, *args, **kwargs):
        parts = Path(self).parts
        if Path(self).name == lexical.name and "workflows" in parts:
            return real_resolve(outside)
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fake_resolve)


def test_discover_includes_normal_file_with_source_binding(tmp_path):
    repo = tmp_path / "repo"
    real = repo / ".github" / "workflows" / "real.yml"
    _write_workflow(real)
    digest = hashlib.sha256(real.read_bytes()).hexdigest()

    found = workflow_analysis.discover_workflow_files(repo)
    assert found == [".github/workflows/real.yml"]
    record = workflow_analysis._inspect_scan_target(repo, found[0])
    assert record == {
        "path": ".github/workflows/real.yml",
        "status": "verified",
        "policy_scope": "repo_target_tree",
        "sha256": digest,
    }


def test_discover_includes_in_repo_symlink(tmp_path):
    repo = tmp_path / "repo"
    real = repo / ".github" / "workflows" / "real.yml"
    _write_workflow(real)
    _symlink(real, repo / ".github" / "workflows" / "alias.yml")

    found = workflow_analysis.discover_workflow_files(repo)
    assert found == [
        ".github/workflows/alias.yml",
        ".github/workflows/real.yml",
    ]


def test_discover_excludes_resolve_escape_and_dangling(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    wf_dir = repo / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    outside = tmp_path / "outside.yml"
    _write_workflow(outside, name="outside")
    escape = wf_dir / "escape.yml"
    escape.write_text("placeholder\n", encoding="utf-8")
    broken = wf_dir / "broken.yml"
    broken.write_text("placeholder\n", encoding="utf-8")

    _patch_resolve_escape(monkeypatch, escape, outside)

    real_is_symlink = Path.is_symlink
    real_exists = Path.exists
    real_readlink = os.readlink

    def fake_is_symlink(self):
        if Path(self).name == "broken.yml" and "workflows" in Path(self).parts:
            return True
        return real_is_symlink(self)

    def fake_exists(self, *args, **kwargs):
        if Path(self).name == "broken.yml" and "workflows" in Path(self).parts:
            return False
        return real_exists(self, *args, **kwargs)

    def fake_readlink(path, *args, **kwargs):
        if Path(path).name == "broken.yml":
            return "missing.yml"
        return real_readlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)
    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr(os, "readlink", fake_readlink)

    assert workflow_analysis.discover_workflow_files(repo) == []
    assert (
        workflow_analysis._inspect_scan_target(repo, ".github/workflows/escape.yml")[
            "reason"
        ]
        == "workflow_target_outside_root"
    )
    assert (
        workflow_analysis._inspect_scan_target(repo, ".github/workflows/broken.yml")[
            "reason"
        ]
        == "workflow_target_dangling"
    )


def test_inspect_absolute_outside_with_and_without_admission(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "ext.yml"
    _write_workflow(outside, name="ext")
    abs_path = str(outside.resolve())
    key = abs_path.replace("\\", "/")
    denied = workflow_analysis._inspect_scan_target(repo, key)
    assert denied["status"] == "outside_root"
    assert denied["reason"] == "workflow_target_outside_root"

    digest = hashlib.sha256(outside.read_bytes()).hexdigest()
    admitted = workflow_analysis._inspect_scan_target(
        repo,
        key,
        admitted_external_targets={
            key: {
                "policy_scope": "event_external",
                "path": abs_path,
                "sha256": digest,
            }
        },
    )
    assert admitted["status"] == "verified_external"
    assert admitted["sha256"] == digest


def test_suite_rejects_absolute_declared_path_even_if_admitted(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "ext.yml"
    _write_workflow(outside, name="ext")
    abs_path = str(outside.resolve())
    declared = abs_path.replace("\\", "/")
    digest = hashlib.sha256(outside.read_bytes()).hexdigest()

    result = workflow_analysis.run_workflow_script_suite(
        {"actionlint": {"status": "selected", "inputs": [declared]}},
        root=repo,
        runner=_actionlint_runner,
        admitted_external_targets={
            declared: {
                "policy_scope": "event_external",
                "path": abs_path,
                "sha256": digest,
            }
        },
    )
    assert result["actionlint"]["status"] == "not_executed"
    assert result["actionlint"]["reason"] == "workflow_target_outside_root"


def test_extra_paths_outside_without_admission_blocks(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _write_workflow(repo / ".github" / "workflows" / "ci.yml")
    outside = tmp_path / "ext.yml"
    _write_workflow(outside, name="ext")
    escape = repo / ".github" / "workflows" / "escape.yml"
    escape.write_text("placeholder\n", encoding="utf-8")
    _patch_resolve_escape(monkeypatch, escape, outside)

    result = workflow_analysis.run_workflow_script_suite(
        {
            "actionlint": {
                "status": "selected",
                "inputs": [".github/workflows/ci.yml"],
            }
        },
        root=repo,
        runner=_actionlint_runner,
        extra_paths=(".github/workflows/escape.yml",),
    )
    assert result["actionlint"]["status"] == "not_executed"
    assert result["actionlint"]["reason"] == "workflow_target_outside_root"


def test_extra_paths_outside_with_admission_binds_and_runs(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _write_workflow(repo / ".github" / "workflows" / "ci.yml")
    outside = tmp_path / "ext.yml"
    _write_workflow(outside, name="ext")
    digest = hashlib.sha256(outside.read_bytes()).hexdigest()
    escape_rel = ".github/workflows/escape.yml"
    escape = repo / escape_rel
    escape.write_text("placeholder\n", encoding="utf-8")
    _patch_resolve_escape(monkeypatch, escape, outside)

    result = workflow_analysis.run_workflow_script_suite(
        {
            "actionlint": {
                "status": "selected",
                "inputs": [".github/workflows/ci.yml"],
            }
        },
        root=repo,
        runner=_actionlint_runner,
        extra_paths=(escape_rel,),
        admitted_external_targets={
            escape_rel: {
                "policy_scope": "event_external",
                "path": str(outside.resolve()),
                "sha256": digest,
            }
        },
    )
    check = result["actionlint"]
    assert check["status"] == "passed"
    assert escape_rel in check["input_paths"]
    refs = {item["path"]: item for item in check["target_source_refs"]}
    assert refs[escape_rel]["status"] == "verified_external"
    assert refs[escape_rel]["sha256"] == digest
    assert refs[".github/workflows/ci.yml"]["policy_scope"] == "repo_target_tree"


def test_declared_dangling_or_missing_target_blocks(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".github" / "workflows").mkdir(parents=True)

    missing = workflow_analysis.run_workflow_script_suite(
        {
            "actionlint": {
                "status": "selected",
                "inputs": [".github/workflows/missing.yml"],
            }
        },
        root=repo,
        runner=_actionlint_runner,
    )
    assert missing["actionlint"]["status"] == "not_executed"
    assert missing["actionlint"]["reason"] == "workflow_target_missing"

    broken = repo / ".github" / "workflows" / "gone.yml"
    broken.write_text("placeholder\n", encoding="utf-8")
    real_is_symlink = Path.is_symlink
    real_exists = Path.exists
    real_readlink = os.readlink

    def fake_is_symlink(self):
        if Path(self).name == "gone.yml" and "workflows" in Path(self).parts:
            return True
        return real_is_symlink(self)

    def fake_exists(self, *args, **kwargs):
        if Path(self).name == "gone.yml" and "workflows" in Path(self).parts:
            return False
        return real_exists(self, *args, **kwargs)

    def fake_readlink(path, *args, **kwargs):
        if Path(path).name == "gone.yml":
            return "nope.yml"
        return real_readlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)
    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr(os, "readlink", fake_readlink)

    result = workflow_analysis.run_workflow_script_suite(
        {
            "actionlint": {
                "status": "selected",
                "inputs": [".github/workflows/gone.yml"],
            }
        },
        root=repo,
        runner=_actionlint_runner,
    )
    assert result["actionlint"]["status"] == "not_executed"
    assert result["actionlint"]["reason"] == "workflow_target_dangling"


def test_shell_discover_and_native_failure_propagation(tmp_path):
    repo = tmp_path / "repo"
    script = repo / "scripts" / "ok.sh"
    _write_shell(script)

    found = workflow_analysis.discover_shell_files(repo, ("scripts/",))
    assert found == ["scripts/ok.sh"]

    def runner(argv, root, data=None):
        joined = " ".join(str(part) for part in argv)
        if "--version" in argv:
            return subprocess.CompletedProcess(
                argv, 0, f"version: {workflow_analysis.SHELLCHECK_VERSION}\n", ""
            )
        if "shellcheck" in joined:
            return subprocess.CompletedProcess(argv, 2, "", "shellcheck boom")
        return subprocess.CompletedProcess(argv, 0, "", "")

    result = workflow_analysis.run_workflow_script_suite(
        {"shellcheck": {"status": "selected", "inputs": ["scripts/ok.sh"]}},
        root=repo,
        runner=runner,
        shell_prefixes=("scripts/",),
    )
    check = result["shellcheck"]
    assert check["status"] == "failed"
    assert check["exit_code"] == 2
    assert check["target_source_refs"][0]["path"] == "scripts/ok.sh"
    assert check["target_source_refs"][0]["status"] == "verified"
