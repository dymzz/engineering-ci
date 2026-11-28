"""Probe the thin local engineering-ci entry's environment contract."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from test_hooks import _git_sh_env, git
from test_native_hooks import _clean_uv_env, _create_test_venv

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "scripts" / "engineering-ci.sh"

UV_LOCK = """\
version = 1

[[package]]
name = "lefthook"
version = "2.2.2"

[[package]]
name = "pytest"
version = "9.0.3"

[[package]]
name = "ruff"
version = "0.16.10"
"""

FAKE_PYTEST = """\
import json
import pathlib
import sys

import engineering_ci

if "--version" in sys.argv:
    print("pytest 9.0.3")
    raise SystemExit(0)

print(json.dumps({
    "tool": "pytest",
    "prefix": sys.prefix,
    "marker": engineering_ci.MARKER,
    "cwd": str(pathlib.Path.cwd()),
    "argv": sys.argv[1:],
}))
"""

FAKE_RUFF = """\
import json
import pathlib
import sys

import engineering_ci

if "--version" in sys.argv:
    print("ruff 0.16.10")
    raise SystemExit(0)

print(json.dumps({
    "tool": "ruff",
    "prefix": sys.prefix,
    "marker": engineering_ci.MARKER,
    "cwd": str(pathlib.Path.cwd()),
    "argv": sys.argv[1:],
}))
"""

FAKE_CLI = """\
import json
import pathlib
import sys

import engineering_ci

print(json.dumps({
    "tool": "cli",
    "prefix": sys.prefix,
    "marker": engineering_ci.MARKER,
    "cwd": str(pathlib.Path.cwd()),
    "argv": sys.argv[1:],
}))
"""

UV_STUB = """\
#!/bin/sh
{
  printf 'args=%s\\n' "$*"
  printf 'UV_PROJECT_ENVIRONMENT=%s\\n' "${UV_PROJECT_ENVIRONMENT:-}"
  printf 'UV_PROJECT=%s\\n' "${UV_PROJECT:-}"
  printf 'UV_CACHE_DIR=%s\\n' "${UV_CACHE_DIR:-}"
  printf 'UV_NO_CACHE=%s\\n' "${UV_NO_CACHE:-}"
} >> "${UV_STUB_LOG:?}"
if [ "${UV_STUB_FAIL:-}" = "1" ]; then
  printf 'stub uv readiness failure\\n' >&2
  exit 1
fi
exit 0
"""

LEFTHOOK_STUB = """\
#!/bin/sh
if [ "$1" = "version" ]; then
  printf '2.2.2\\n'
  exit 0
fi
printf 'lefthook-argv=%s\\n' "$*"
"""


@pytest.fixture
def entry_env():
    env = _clean_uv_env(_git_sh_env())
    for key in (
        "UV_CACHE_DIR",
        "UV_NO_CACHE",
        "UV_CONFIG_FILE",
        "UV_NO_CONFIG",
        "UV_NO_SYNC",
        "UV_PYTHON_DOWNLOADS",
    ):
        env.pop(key, None)
    return env


def _venv_bin(venv: Path) -> Path:
    scripts = venv / "Scripts"
    return scripts if scripts.is_dir() else venv / "bin"


def _write_tools(venv: Path, *, lefthook: bool = False) -> None:
    if lefthook:
        target = _venv_bin(venv) / "lefthook"
        target.write_text(LEFTHOOK_STUB, encoding="utf-8")
        target.chmod(0o755)


def _init_repo(root: Path, env: dict[str, str]) -> None:
    root.mkdir()
    subprocess.run(
        ["git", "init", "-q", "-b", "main", "--template="],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
    )
    for key, value in (
        ("user.email", "entry@example.invalid"),
        ("user.name", "Entry Fixture"),
        ("core.autocrlf", "false"),
    ):
        git(root, "config", key, value, env=env)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "entry-probe"\nversion = "0.0.0"\n'
        'requires-python = ">=3.14"\n'
        "\n"
        "[dependency-groups]\n"
        'dev = ["lefthook==2.2.2", "pytest==9.0.3", "ruff==0.16.10"]\n',
        encoding="utf-8",
    )
    (root / "uv.lock").write_text(UV_LOCK, encoding="utf-8")
    (root / ".python-version").write_text("3.14\n", encoding="utf-8")
    package = root / "src" / "engineering_ci"
    (package / "cli").mkdir(parents=True)
    (package / "__init__.py").write_text('MARKER = "primary"\n', encoding="utf-8")
    (package / "cli" / "__init__.py").write_text("", encoding="utf-8")
    (package / "cli" / "__main__.py").write_text(FAKE_CLI, encoding="utf-8")
    (root / "src" / "pytest.py").write_text(FAKE_PYTEST, encoding="utf-8")
    (root / "src" / "ruff.py").write_text(FAKE_RUFF, encoding="utf-8")
    git(root, "add", "--all", env=env)
    git(root, "commit", "-qm", "baseline", env=env)


def _entry(root: Path, env: dict[str, str], *args: str, stdin_text: str | None = None):
    sh_bin = shutil.which("sh", path=env.get("PATH"))
    assert sh_bin, "POSIX sh executable not found"
    result = subprocess.run(
        [sh_bin, str(ENTRY), *args],
        cwd=root,
        env=env,
        input=stdin_text,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
    )
    return result


def _json_output(result: subprocess.CompletedProcess) -> dict:
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines, result.stdout + result.stderr
    return json.loads(lines[-1])


def _fixture(tmp_path: Path, env: dict[str, str]):
    primary = tmp_path / "primary repo"
    linked = tmp_path / "linked worktree"
    _init_repo(primary, env)
    _create_test_venv(primary / ".venv", primary / "src")
    git(
        primary,
        "worktree",
        "add",
        "-qb",
        "linked",
        str(linked),
        "HEAD",
        env=env,
    )
    (linked / "src" / "engineering_ci" / "__init__.py").write_text(
        'MARKER = "linked"\n', encoding="utf-8"
    )
    stub_bin = tmp_path / "stub bin"
    stub_bin.mkdir()
    uv_stub = stub_bin / "uv"
    uv_stub.write_text(UV_STUB, encoding="utf-8")
    uv_stub.chmod(0o755)
    log = tmp_path / "uv.log"
    call_env = {
        **env,
        "PATH": os.pathsep.join([str(stub_bin), env.get("PATH", "")]),
        "UV_STUB_LOG": str(log),
    }
    return primary, linked, call_env, log


def test_entry_binds_shared_environment_and_candidate_source(tmp_path, entry_env):
    primary, linked, env, log = _fixture(tmp_path, entry_env)
    result = _entry(linked, env, "pytest", "-q", "tests/example.py")
    assert result.returncode == 0, result.stdout + result.stderr
    output = _json_output(result)
    assert Path(output["prefix"]).resolve() == (primary / ".venv").resolve()
    assert output["marker"] == "linked"
    assert output["cwd"] == str(linked.resolve())
    assert output["argv"] == ["-q", "tests/example.py"]
    assert not (linked / ".venv").exists()
    lines = log.read_text(encoding="utf-8").splitlines()
    assert "args=sync --project " in lines[0]
    assert "--check" in lines[0]
    assert "--group dev" in lines[0]
    assert f"UV_PROJECT_ENVIRONMENT={primary / '.venv'}" in lines[1]


def test_entry_default_ignores_linked_leftover_venv(tmp_path, entry_env):
    primary, linked, env, log = _fixture(tmp_path, entry_env)
    _create_test_venv(linked / ".venv", primary / "src")
    result = _entry(linked, env, "pytest", "-q")
    assert result.returncode == 0, result.stdout + result.stderr
    output = _json_output(result)
    assert Path(output["prefix"]).resolve() == (primary / ".venv").resolve()
    lines = log.read_text(encoding="utf-8").splitlines()
    assert f"UV_PROJECT_ENVIRONMENT={primary / '.venv'}" in lines[1]


def test_entry_explicit_environment_selection(tmp_path, entry_env):
    primary, linked, env, log = _fixture(tmp_path, entry_env)
    _create_test_venv(linked / ".venv", primary / "src")
    result = _entry(
        linked, env, "--environment", str(linked / ".venv"), "ruff", "--version"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ruff 0.16.10" in result.stdout
    lines = log.read_text(encoding="utf-8").splitlines()
    assert f"UV_PROJECT_ENVIRONMENT={linked / '.venv'}" in lines[1]


def test_entry_ignores_inherited_binding_and_cache_overrides(tmp_path, entry_env):
    primary, linked, env, log = _fixture(tmp_path, entry_env)
    _create_test_venv(linked / ".venv", primary / "src")
    env.update(
        {
            "UV_PROJECT_ENVIRONMENT": str(linked / ".venv"),
            "PYTHONPATH": str(primary / "src"),
            "UV_CACHE_DIR": str(tmp_path / "inherited cache"),
            "UV_NO_CACHE": "1",
        }
    )
    result = _entry(linked, env, "pytest", "-q")
    assert result.returncode == 0, result.stdout + result.stderr
    output = _json_output(result)
    assert Path(output["prefix"]).resolve() == (primary / ".venv").resolve()
    assert output["marker"] == "linked"
    lines = log.read_text(encoding="utf-8").splitlines()
    assert f"UV_PROJECT_ENVIRONMENT={primary / '.venv'}" in lines[1]
    assert "UV_CACHE_DIR=" in lines[3]
    assert "UV_NO_CACHE=" in lines[4]


def test_entry_rejects_project_cache_disable(tmp_path, entry_env):
    _primary, linked, env, log = _fixture(tmp_path, entry_env)
    (linked / "uv.toml").write_text("no-cache = true\n", encoding="utf-8")
    result = _entry(linked, env, "pytest", "-q")
    assert result.returncode != 0
    assert "disables the uv cache" in result.stderr
    assert not log.exists()


def test_entry_missing_environment_blocks_before_operation(tmp_path, entry_env):
    _primary, linked, env, log = _fixture(tmp_path, entry_env)
    missing = tmp_path / "missing environment"
    result = _entry(linked, env, "--environment", str(missing), "pytest", "-q")
    assert result.returncode != 0
    assert "environment is missing" in result.stderr
    assert "uv sync --locked --inexact --group dev" in result.stderr
    assert not (linked / ".venv").exists()
    assert not log.exists()


def test_entry_tool_version_mismatch_blocks_before_operation(tmp_path, entry_env):
    primary, linked, env, log = _fixture(tmp_path, entry_env)
    (primary / "src" / "pytest.py").write_text(
        FAKE_PYTEST.replace("pytest 9.0.3", "pytest 9.9.9"), encoding="utf-8"
    )
    result = _entry(linked, env, "pytest", "-q")
    assert result.returncode != 0
    assert "pytest version mismatch" in result.stderr
    assert "9.9.9" in result.stderr
    assert not log.exists()


def test_entry_readiness_failure_blocks_before_operation(tmp_path, entry_env):
    _primary, linked, env, log = _fixture(tmp_path, entry_env)
    env["UV_STUB_FAIL"] = "1"
    result = _entry(linked, env, "pytest", "-q")
    assert result.returncode == 1
    assert "readiness failed" in result.stderr
    assert "stub uv readiness failure" in result.stderr
    assert log.exists()


def test_entry_runtime_cli_does_not_require_dev_tools(tmp_path, entry_env):
    _primary, linked, env, log = _fixture(tmp_path, entry_env)
    result = _entry(linked, env, "cli", "version", "show")
    assert result.returncode == 0, result.stdout + result.stderr
    output = _json_output(result)
    assert output["marker"] == "linked"
    assert output["argv"] == ["version", "show"]
    lines = log.read_text(encoding="utf-8").splitlines()
    assert "--no-dev" in lines[0]
    assert "--group dev" not in lines[0]


def test_entry_verify_requires_bound_lefthook(tmp_path, entry_env):
    primary, linked, env, log = _fixture(tmp_path, entry_env)
    result = _entry(linked, env, "verify", "--help")
    assert result.returncode != 0
    assert "lefthook is not available" in result.stderr
    assert not log.exists()

    _write_tools(primary / ".venv", lefthook=True)
    ready = _entry(linked, env, "verify", "--help")
    assert ready.returncode == 0, ready.stdout + ready.stderr
    assert _json_output(ready)["tool"] == "cli"
    lines = log.read_text(encoding="utf-8").splitlines()
    assert "--group dev" in lines[0]


@pytest.mark.skipif(os.name != "nt", reason="PowerShell wrapper is Windows-only")
def test_powershell_wrapper_delegates_to_git_bash(tmp_path, entry_env):
    _primary, linked, env, _log = _fixture(tmp_path, entry_env)
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    assert powershell, "PowerShell executable not found"
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-File",
            str(ROOT / "scripts/engineering-ci.ps1"),
            "-h",
        ],
        cwd=linked,
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Usage: engineering-ci.sh" in result.stdout
