"""Enumerating the files a script check must cover.

The scope is declared, not inferred: the check is given the files the scope
contains, so the covered input set is observable and an empty scope is a
provable fact rather than an assumption.
"""

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scope = load("script_scope_unit", ".dagger/src/engineering_ci/script_scope.py")


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / ".github/workflows/ci.yml").write_text("name: ci\n", encoding="utf-8")
    (tmp_path / ".github/workflows/nested").mkdir()
    (tmp_path / ".github/workflows/nested/release.yaml").write_text(
        "name: release\n", encoding="utf-8"
    )
    (tmp_path / ".github/workflows/notes.md").write_text("notes\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/build.ps1").write_text("Get-Date\n", encoding="utf-8")
    (tmp_path / "scripts/helper.psm1").write_text(
        "function Get-X { }\n", encoding="utf-8"
    )
    (tmp_path / "scripts/readme.md").write_text("notes\n", encoding="utf-8")
    return tmp_path


def test_workflow_scope_lists_only_workflow_files(workspace):
    files = scope.select(workspace, [".github/workflows"], "workflow")
    assert files == [
        ".github/workflows/ci.yml",
        ".github/workflows/nested/release.yaml",
    ]


def test_powershell_scope_lists_only_powershell_files(workspace):
    files = scope.select(workspace, ["scripts"], "powershell")
    assert files == ["scripts/build.ps1", "scripts/helper.psm1"]


def test_an_explicitly_named_file_is_listed_as_requested(workspace):
    files = scope.select(workspace, ["scripts/build.ps1"], "powershell")
    assert files == ["scripts/build.ps1"]


def test_a_missing_scope_path_is_an_error(workspace):
    with pytest.raises(ValueError, match="Scope path not found"):
        scope.select(workspace, ["nowhere"], "workflow")


def test_an_unknown_scope_kind_is_an_error(workspace):
    with pytest.raises(ValueError, match="Unknown scope kind"):
        scope.select(workspace, ["scripts"], "shell")


def test_the_command_line_reports_the_selected_files(workspace, monkeypatch):
    monkeypatch.chdir(workspace)
    assert scope.main(["workflow", ".github/workflows"]) == 0
    assert scope.main(["shell", "scripts"]) == 2


def test_the_invocation_script_accepts_every_path_after_one_flag(tmp_path):
    """Both callers pass every path after a single -Path.

    The script used to consume one path per flag, so analysing more than one
    script failed with `Unknown argument`. It now consumes every value that
    follows the flag, and reaches the analyzer instead.
    """
    if shutil.which("pwsh") is None:
        pytest.skip("PowerShell is not available on this host")
    script = ROOT / ".dagger/src/engineering_ci/powershell_invoke.ps1"
    result = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(script),
            "-ModuleRoot",
            str(tmp_path),
            "-Version",
            "1.9.0",
            "-Path",
            "first.ps1",
            "second.ps1",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    assert result.returncode != 0
    envelope = json.loads(result.stdout.splitlines()[0])
    assert envelope["status"] == "error"
    assert "Unknown argument" not in envelope["error"]
