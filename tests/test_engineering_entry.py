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

FAKE_TOOL = """\
#!/bin/sh
here=$(dirname "$0")
if [ -x "$here/python" ]; then py="$here/python"; else py="$here/python.exe"; fi
exec "$py" -c 'import json, os, pathlib, sys
if "--version" in sys.argv:
    print(sys.argv[1] + " " + sys.argv[2])
    raise SystemExit(0)
print(json.dumps({
    "tool": sys.argv[1],
    "prefix": sys.prefix,
    "cwd": str(pathlib.Path.cwd()),
    "argv": sys.argv[3:],
    "env": {
        "PYTHONPATH": os.environ.get("PYTHONPATH"),
        "UV_NO_SYNC": os.environ.get("UV_NO_SYNC"),
        "UV_PYTHON_DOWNLOADS": os.environ.get("UV_PYTHON_DOWNLOADS"),
        "UV_NO_DEV": os.environ.get("UV_NO_DEV"),
        "UV_NO_GROUP": os.environ.get("UV_NO_GROUP"),
        "UV_PYTHON": os.environ.get("UV_PYTHON"),
    },
}))
' "__TOOL__" "__VERSION__" "$@"
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

LEFTHOOK_STUB = """\
#!/bin/sh
if [ "$1" = "version" ]; then
  printf '2.2.2\\n'
  exit 0
fi
printf 'lefthook-argv=%s\\n' "$*"
"""

UV_STUB = """\
#!/bin/sh
if [ "$1" = "cache" ] && [ "$2" = "dir" ]; then
  if [ "${UV_STUB_CACHE_UNSTABLE:-}" = "1" ]; then
    count=$(cat "${UV_STUB_CACHE_COUNTER:?}" 2>/dev/null || printf '0')
    count=$((count + 1))
    printf '%s\\n' "$count" > "${UV_STUB_CACHE_COUNTER:?}"
    printf '/tmp/eci-cache-%s\\n' "$count"
  else
    printf '%s\\n' "${UV_STUB_CACHE_DIR:-/tmp/eci-cache}"
  fi
  exit 0
fi
{
  printf 'args=%s\\n' "$*"
  printf 'UV_PROJECT_ENVIRONMENT=%s\\n' "${UV_PROJECT_ENVIRONMENT:-}"
  printf 'UV_PROJECT=%s\\n' "${UV_PROJECT:-}"
  printf 'UV_CACHE_DIR=%s\\n' "${UV_CACHE_DIR:-}"
  printf 'UV_NO_CACHE=%s\\n' "${UV_NO_CACHE:-}"
  printf 'UV_NO_DEV=%s\\n' "${UV_NO_DEV:-}"
  printf 'UV_NO_GROUP=%s\\n' "${UV_NO_GROUP:-}"
  printf 'UV_PYTHON=%s\\n' "${UV_PYTHON:-}"
} >> "${UV_STUB_LOG:?}"
if [ "${UV_STUB_FAIL:-}" = "1" ]; then
  printf 'stub uv readiness failure\\n' >&2
  exit 1
fi
exit 0
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
        "UV_NO_DEV",
        "UV_NO_GROUP",
        "UV_PYTHON",
    ):
        env.pop(key, None)
    return env


def _venv_bin(venv: Path) -> Path:
    scripts = venv / "Scripts"
    return scripts if scripts.is_dir() else venv / "bin"


def _venv_site(venv: Path) -> Path:
    scripts = _venv_bin(venv)
    if scripts.name == "Scripts":
        return venv / "Lib" / "site-packages"
    candidates = sorted((venv / "lib").glob("python*/site-packages"))
    assert candidates, f"site-packages missing under {venv}"
    return candidates[0]


def _write_dist(venv: Path, name: str, version: str) -> None:
    dist = _venv_site(venv) / f"{name}-{version}.dist-info"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        encoding="utf-8",
    )


def _write_tool_script(venv: Path, name: str, version: str) -> None:
    target = _venv_bin(venv) / name
    target.write_text(
        FAKE_TOOL.replace("__TOOL__", name).replace("__VERSION__", version),
        encoding="utf-8",
    )
    target.chmod(0o755)


def _write_tools(venv: Path, *, lefthook: bool = False) -> None:
    for name, version in (("pytest", "9.0.3"), ("ruff", "0.16.10")):
        _write_dist(venv, name, version)
        _write_tool_script(venv, name, version)
    if lefthook:
        _write_dist(venv, "lefthook", "2.2.2")
        target = _venv_bin(venv) / "lefthook"
        target.write_text(LEFTHOOK_STUB, encoding="utf-8")
        target.chmod(0o755)


def _remove_tool(venv: Path, name: str) -> None:
    for dist in _venv_site(venv).glob(f"{name}-*.dist-info"):
        shutil.rmtree(dist)
    (_venv_bin(venv) / name).unlink(missing_ok=True)


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
        'dev = ["lefthook==2.2.2", "pytest==9.0.3", "ruff==0.16.10"]\n'
        "\n"
        "[tool.pytest.ini_options]\n"
        'pythonpath = ["src"]\n',
        encoding="utf-8",
    )
    (root / "uv.lock").write_text(UV_LOCK, encoding="utf-8")
    (root / ".python-version").write_text("3.14\n", encoding="utf-8")
    package = root / "src" / "engineering_ci"
    (package / "cli").mkdir(parents=True)
    (package / "__init__.py").write_text('MARKER = "primary"\n', encoding="utf-8")
    (package / "cli" / "__init__.py").write_text("", encoding="utf-8")
    (package / "cli" / "__main__.py").write_text(FAKE_CLI, encoding="utf-8")
    git(root, "add", "--all", env=env)
    git(root, "commit", "-qm", "baseline", env=env)


def _entry(root: Path, env: dict[str, str], *args: str):
    sh_bin = shutil.which("sh", path=env.get("PATH"))
    assert sh_bin, "POSIX sh executable not found"
    return subprocess.run(
        [sh_bin, str(ENTRY), *args],
        cwd=root,
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
    )


def _json_output(result: subprocess.CompletedProcess) -> dict:
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines, result.stdout + result.stderr
    return json.loads(lines[-1])


def _fixture(tmp_path: Path, env: dict[str, str]):
    primary = tmp_path / "primary repo"
    linked = tmp_path / "linked worktree"
    _init_repo(primary, env)
    _create_test_venv(primary / ".venv", primary / "src")
    _write_tools(primary / ".venv")
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


def _sync_lines(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines()


def _assert_sync_binding(lines: list[str], environment: Path, scope: str) -> None:
    assert "args=sync --project " in lines[0]
    assert "--check" in lines[0]
    assert "--no-python-downloads" in lines[0]
    assert "--python " in lines[0]
    assert scope in lines[0]
    assert f"UV_PROJECT_ENVIRONMENT={environment}" in lines[1]
    assert "UV_NO_DEV=" in lines[5]
    assert "UV_NO_GROUP=" in lines[6]
    assert "UV_PYTHON=" in lines[7]


def test_entry_binds_shared_environment_and_candidate_source(tmp_path, entry_env):
    primary, linked, env, log = _fixture(tmp_path, entry_env)
    result = _entry(linked, env, "pytest", "-q", "tests/example.py")
    assert result.returncode == 0, result.stdout + result.stderr
    output = _json_output(result)
    assert Path(output["prefix"]).resolve() == (primary / ".venv").resolve()
    assert output["cwd"] == str(linked.resolve())
    assert output["argv"] == ["-q", "tests/example.py"]
    assert output["env"]["PYTHONPATH"] is None
    assert output["env"]["UV_NO_SYNC"] is None
    assert output["env"]["UV_PYTHON_DOWNLOADS"] is None
    assert not (linked / ".venv").exists()
    lines = _sync_lines(log)
    _assert_sync_binding(lines, primary / ".venv", "--no-dev")


def test_entry_default_ignores_linked_leftover_venv(tmp_path, entry_env):
    primary, linked, env, log = _fixture(tmp_path, entry_env)
    _create_test_venv(linked / ".venv", primary / "src")
    _write_tools(linked / ".venv")
    result = _entry(linked, env, "pytest", "-q")
    assert result.returncode == 0, result.stdout + result.stderr
    output = _json_output(result)
    assert Path(output["prefix"]).resolve() == (primary / ".venv").resolve()
    _assert_sync_binding(_sync_lines(log), primary / ".venv", "--no-dev")


def test_entry_explicit_environment_selection(tmp_path, entry_env):
    primary, linked, env, log = _fixture(tmp_path, entry_env)
    _create_test_venv(linked / ".venv", primary / "src")
    _write_tools(linked / ".venv")
    result = _entry(
        linked, env, "--environment", str(linked / ".venv"), "ruff", "--version"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ruff 0.16.10" in result.stdout
    _assert_sync_binding(_sync_lines(log), linked / ".venv", "--no-dev")


def test_entry_ignores_inherited_binding_tool_and_cache_overrides(tmp_path, entry_env):
    primary, linked, env, log = _fixture(tmp_path, entry_env)
    _create_test_venv(linked / ".venv", primary / "src")
    _write_tools(linked / ".venv")
    env.update(
        {
            "UV_PROJECT_ENVIRONMENT": str(linked / ".venv"),
            "PYTHONPATH": str(primary / "src"),
            "UV_CACHE_DIR": str(tmp_path / "inherited cache"),
            "UV_NO_CACHE": "1",
            "UV_NO_DEV": "1",
            "UV_NO_GROUP": "dev",
            "UV_PYTHON": str(tmp_path / "missing-python"),
            "UV_NO_SYNC": "1",
            "UV_PYTHON_DOWNLOADS": "never",
        }
    )
    result = _entry(linked, env, "pytest", "-q")
    assert result.returncode == 0, result.stdout + result.stderr
    output = _json_output(result)
    assert Path(output["prefix"]).resolve() == (primary / ".venv").resolve()
    assert output["env"]["PYTHONPATH"] is None
    assert output["env"]["UV_NO_SYNC"] is None
    assert output["env"]["UV_PYTHON_DOWNLOADS"] is None
    lines = _sync_lines(log)
    _assert_sync_binding(lines, primary / ".venv", "--no-dev")
    assert "UV_CACHE_DIR=" in lines[3]
    assert "UV_NO_CACHE=" in lines[4]
    assert "UV_NO_DEV=" in lines[5]
    assert "UV_NO_GROUP=" in lines[6]
    assert "UV_PYTHON=" in lines[7]


def test_entry_rejects_unstable_cache(tmp_path, entry_env):
    _primary, linked, env, log = _fixture(tmp_path, entry_env)
    env["UV_STUB_CACHE_UNSTABLE"] = "1"
    env["UV_STUB_CACHE_COUNTER"] = str(tmp_path / "cache-counter")
    result = _entry(linked, env, "pytest", "-q")
    assert result.returncode != 0
    assert (
        "effective uv configuration disables or randomizes the cache" in result.stderr
    )
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
    _write_tool_script(primary / ".venv", "pytest", "9.9.9")
    result = _entry(linked, env, "pytest", "-q")
    assert result.returncode != 0
    assert "pytest version mismatch" in result.stderr
    assert "9.9.9" in result.stderr
    assert not log.exists()


def test_entry_pytest_ignores_candidate_same_name_module(tmp_path, entry_env):
    primary, linked, env, log = _fixture(tmp_path, entry_env)
    (linked / "src" / "pytest.py").write_text(
        'raise SystemExit("candidate pytest module must not run")\n',
        encoding="utf-8",
    )
    result = _entry(linked, env, "pytest", "-q")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _json_output(result)["tool"] == "pytest"
    _assert_sync_binding(_sync_lines(log), primary / ".venv", "--no-dev")


def test_entry_missing_installed_tool_does_not_fall_back_to_path(tmp_path, entry_env):
    primary, linked, env, log = _fixture(tmp_path, entry_env)
    _remove_tool(primary / ".venv", "pytest")
    fallback = tmp_path / "fallback bin"
    fallback.mkdir()
    (fallback / "pytest").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (fallback / "pytest").chmod(0o755)
    env["PATH"] = os.pathsep.join([str(fallback), env["PATH"]])
    result = _entry(linked, env, "pytest", "-q")
    assert result.returncode != 0
    assert "pytest is not available" in result.stderr
    assert not log.exists()


def test_entry_same_name_pth_module_is_not_an_installed_tool(tmp_path, entry_env):
    primary, linked, env, log = _fixture(tmp_path, entry_env)
    _remove_tool(primary / ".venv", "pytest")
    (primary / "src" / "pytest.py").write_text(
        'print("pytest 9.0.3")\n',
        encoding="utf-8",
    )
    result = _entry(linked, env, "pytest", "-q")
    assert result.returncode != 0
    assert "pytest is not available" in result.stderr
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
    primary, linked, env, log = _fixture(tmp_path, entry_env)
    _remove_tool(primary / ".venv", "pytest")
    _remove_tool(primary / ".venv", "ruff")
    result = _entry(linked, env, "cli", "version", "show")
    assert result.returncode == 0, result.stdout + result.stderr
    output = _json_output(result)
    assert output["marker"] == "linked"
    assert output["argv"] == ["version", "show"]
    lines = _sync_lines(log)
    assert "--no-dev" in lines[0]
    assert "--group dev" not in lines[0]


def test_entry_pytest_scope_does_not_require_ruff(tmp_path, entry_env):
    primary, linked, env, log = _fixture(tmp_path, entry_env)
    _remove_tool(primary / ".venv", "ruff")
    result = _entry(linked, env, "pytest", "-q")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _json_output(result)["tool"] == "pytest"
    lines = _sync_lines(log)
    assert "--no-dev" in lines[0]
    assert "--group dev" not in lines[0]


def test_entry_ruff_scope_does_not_require_pytest(tmp_path, entry_env):
    primary, linked, env, log = _fixture(tmp_path, entry_env)
    _remove_tool(primary / ".venv", "pytest")
    result = _entry(linked, env, "ruff", "check", "src")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _json_output(result)["tool"] == "ruff"
    lines = _sync_lines(log)
    assert "--no-dev" in lines[0]
    assert "--group dev" not in lines[0]


def test_entry_verify_help_does_not_require_dev_tools(tmp_path, entry_env):
    primary, linked, env, log = _fixture(tmp_path, entry_env)
    _remove_tool(primary / ".venv", "pytest")
    _remove_tool(primary / ".venv", "ruff")
    result = _entry(linked, env, "verify", "--help")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _json_output(result)["tool"] == "cli"
    lines = _sync_lines(log)
    assert "--no-dev" in lines[0]
    assert "--group dev" not in lines[0]


def test_entry_read_only_verify_report_does_not_require_dev_tools(tmp_path, entry_env):
    primary, linked, env, log = _fixture(tmp_path, entry_env)
    _remove_tool(primary / ".venv", "pytest")
    _remove_tool(primary / ".venv", "ruff")
    result = _entry(linked, env, "cli", "verify", "report", "--verify-dir", "x")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _json_output(result)["tool"] == "cli"
    lines = _sync_lines(log)
    assert "--no-dev" in lines[0]
    assert "--group dev" not in lines[0]


def test_entry_full_verify_requires_bound_lefthook(tmp_path, entry_env):
    primary, linked, env, log = _fixture(tmp_path, entry_env)
    result = _entry(linked, env, "verify", "--out", ".artifacts/verify")
    assert result.returncode != 0
    assert "lefthook is not available" in result.stderr
    assert not log.exists()

    _write_tools(primary / ".venv", lefthook=True)
    ready = _entry(linked, env, "verify", "--out", ".artifacts/verify")
    assert ready.returncode == 0, ready.stdout + ready.stderr
    assert _json_output(ready)["tool"] == "cli"
    lines = _sync_lines(log)
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


def _cache_project(tmp_path: Path, *, uv_toml: str | None = None) -> Path:
    project = tmp_path / "cache project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "cache-probe"\nversion = "0.0.0"\n'
        'requires-python = ">=3.14"\n',
        encoding="utf-8",
    )
    if uv_toml is not None:
        (project / "uv.toml").write_text(uv_toml, encoding="utf-8")
    return project


def _run_cache_check(project: Path, env: dict[str, str]):
    sh_bin = shutil.which("sh", path=env.get("PATH"))
    assert sh_bin, "POSIX sh executable not found"
    return subprocess.run(
        [
            sh_bin,
            "-c",
            f'. "{ROOT / "scripts" / "eci-env.sh"}"; _eci_require_persistent_cache',
        ],
        cwd=project,
        env={**env, "ECI_PROJECT_ROOT": str(project)},
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
    )


def test_real_uv_cache_default_is_persistent(tmp_path, entry_env):
    project = _cache_project(tmp_path)
    result = _run_cache_check(project, entry_env)
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_uv_project_no_cache_is_rejected(tmp_path, entry_env):
    project = _cache_project(tmp_path, uv_toml="no-cache = true\n")
    result = _run_cache_check(project, entry_env)
    assert result.returncode != 0
    assert "randomizes the cache" in result.stderr


def test_real_uv_quoted_no_cache_is_rejected(tmp_path, entry_env):
    project = _cache_project(tmp_path, uv_toml='"no-cache" = true\n')
    result = _run_cache_check(project, entry_env)
    assert result.returncode != 0
    assert "randomizes the cache" in result.stderr


def test_real_uv_unrelated_no_cache_table_is_ignored(tmp_path, entry_env):
    project = _cache_project(tmp_path)
    (project / "pyproject.toml").write_text(
        (project / "pyproject.toml").read_text(encoding="utf-8")
        + "\n[tool.other]\nno-cache = true\n",
        encoding="utf-8",
    )
    result = _run_cache_check(project, entry_env)
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_uv_custom_cache_dir_is_accepted(tmp_path, entry_env):
    custom = tmp_path / "custom cache"
    project = _cache_project(tmp_path, uv_toml=f'cache-dir = "{custom.as_posix()}"\n')
    result = _run_cache_check(project, entry_env)
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_uv_uv_toml_overrides_stale_pyproject_no_cache(tmp_path, entry_env):
    project = _cache_project(tmp_path, uv_toml="no-cache = false\n")
    (project / "pyproject.toml").write_text(
        (project / "pyproject.toml").read_text(encoding="utf-8")
        + "\n[tool.uv]\nno-cache = true\n",
        encoding="utf-8",
    )
    result = _run_cache_check(project, entry_env)
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_uv_inherited_cache_overrides_are_ignored(tmp_path, entry_env):
    project = _cache_project(tmp_path)
    env = {
        **entry_env,
        "UV_NO_CACHE": "1",
        "UV_CACHE_DIR": str(tmp_path / "inherited cache"),
    }
    result = _run_cache_check(project, env)
    assert result.returncode == 0, result.stdout + result.stderr
