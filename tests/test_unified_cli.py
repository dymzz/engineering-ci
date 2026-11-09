"""Unified CLI delegates to existing business functions without dispatching in menus."""

import json
import os
import shlex
import shutil
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest

from engineering_ci import workflow_actions
from engineering_ci.cli import download, main
from engineering_ci.release import publishing

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "command,module",
    [
        ("release", "engineering_ci.cli.release"),
        ("download", "engineering_ci.cli.download"),
        ("version", "engineering_ci.release.version"),
        ("policy", "engineering_ci.security_policy"),
        ("select", "eci_core.selection"),
        ("hygiene", "eci_core.hygiene"),
        ("sources", "eci_core.sources"),
        ("execute", "eci_core.execution"),
        ("run", "engineering_ci.cli.run"),
    ],
)
def test_unified_cli_preserves_exit_status(command, module, monkeypatch):
    import importlib

    calls = []
    monkeypatch.setattr(
        importlib.import_module(module), "main", lambda args: calls.append(args) or 7
    )
    assert main([command, "parameter"]) == 7 and calls == [["parameter"]]


@pytest.mark.parametrize("command", ["trace", "mine", "workflow", "checks"])
def test_removed_harness_commands_are_rejected(command):
    with pytest.raises(SystemExit) as error:
        main([command, "--help"])
    assert error.value.code == 2


@pytest.mark.parametrize("lockfile", ["uv.lock", ".dagger/uv.lock"])
def test_ci_locked_dependency_closure_does_not_include_harness(lockfile):
    data = tomllib.loads((ROOT / lockfile).read_text(encoding="utf-8"))
    packages = {item["name"].lower().replace("_", "-") for item in data["package"]}
    assert packages.isdisjoint({"vesryn", "vesryn-workflow"})


def test_scripts_have_only_the_two_user_entrypoints():
    assert {path.name for path in (ROOT / "scripts").glob("*.py")} == {
        "release.py",
        "download.py",
    }


@pytest.mark.parametrize("entry", ["release.py", "download.py"])
@pytest.mark.parametrize("directory", ["scripts"])
@pytest.mark.parametrize("args", [[], ["--help"]])
def test_user_entrypoints_offer_help_without_project_dependencies(
    entry, directory, args
):
    result = subprocess.run(
        [sys.executable, "-S", str(ROOT / directory / entry), *args],
        input="",
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0 and "usage:" in result.stdout.lower()
    assert not result.stderr


def test_download_requires_explicit_mode_before_network(monkeypatch):
    monkeypatch.setattr(download, "api", lambda *args: pytest.fail("No implicit menu"))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    with pytest.raises(SystemExit) as error:
        download.main(["--repo", "owner/repo"])
    assert error.value.code == 2


def test_api_error_does_not_echo_sensitive_response(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            [], 1, stdout='{"token":"SECRET"}', stderr="SECRET (HTTP 403)"
        ),
    )
    with pytest.raises(publishing.ApiError) as error:
        download.api("owner/repo", "actions/workflows")
    assert "SECRET" not in str(error.value)


@pytest.mark.parametrize(
    "operation,module",
    [
        ("release", "engineering_ci.release.publishing"),
        ("manifest", "engineering_ci.release.manifest"),
        ("security", "engineering_ci.tools.security"),
    ],
)
def test_actions_dispatcher_preserves_original_result(operation, module, monkeypatch):
    import importlib

    calls = []
    monkeypatch.setattr(
        importlib.import_module(module),
        "main",
        lambda args, **kwargs: calls.append((args, kwargs)) or 7,
    )
    assert workflow_actions.main([operation, "argument"]) == 7
    assert calls == [(["argument"], {})]


@pytest.mark.parametrize("modern", [True, False])
def test_security_workflow_accepts_published_helper_and_preserves_failure(
    tmp_path, modern
):
    """Execute the real routing block against helper stubs, including native Git Bash."""
    if sys.platform == "win32":
        git = shutil.which("git")
        bash = Path(git).parents[1] / "bin/bash.exe" if git else None
        if bash is None or not bash.is_file():
            pytest.skip("Git Bash unavailable for security helper routing")
    else:
        bash = shutil.which("bash")
        if not bash:
            pytest.skip("Bash unavailable for security helper routing")
    helper = tmp_path / "engineering-ci-security"
    legacy = helper / "scripts/dependency_security.py"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        "import json, os, sys\nprint(json.dumps({'entry':'legacy','args':sys.argv[1:]}))\n"
        "raise SystemExit(int(os.environ['HELPER_EXIT']))\n"
    )
    if modern:
        package = helper / "src/engineering_ci"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("")
        (package / "workflow_actions.py").write_text(
            "import json, os, sys\nprint(json.dumps({'entry':'module','args':sys.argv[1:]}))\n"
            "raise SystemExit(int(os.environ['HELPER_EXIT']))\n"
        )
    workflow = (ROOT / ".github/workflows/reusable-python-ci.yml").read_text(
        encoding="utf-8"
    )
    start = workflow.index(
        '          if [[ -f "$RUNNER_TEMP/engineering-ci-security/src/engineering_ci/workflow_actions.py"'
    )
    end = workflow.index("          fi", start) + len("          fi")
    block = textwrap.dedent(workflow[start:end])
    block = block.replace(
        "python3 ", shlex.quote(Path(sys.executable).as_posix()) + " "
    )
    for exit_code in (0, 7):
        result = subprocess.run(
            [
                str(bash),
                "-c",
                'set -euo pipefail\nargs=(--project "project with spaces" --mode enforce)\n'
                + block,
            ],
            env={
                **os.environ,
                "RUNNER_TEMP": tmp_path.as_posix(),
                "HELPER_EXIT": str(exit_code),
            },
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        assert result.returncode == exit_code, result.stderr
        output = json.loads(result.stdout)
        assert output["entry"] == ("module" if modern else "legacy")
        assert output["args"] == (["security"] if modern else []) + [
            "--project",
            "project with spaces",
            "--mode",
            "enforce",
        ]


def test_unified_run_pre_commit_invokes_and_reports(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "run@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Run Test"], cwd=repo, check=True)
    (repo / "file.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    report_file = tmp_path / "report.json"
    code = main(["run", "--event", "pre-commit", "--root", str(repo), "--report", str(report_file)])
    assert code == 0
    data = json.loads(report_file.read_text(encoding="utf-8"))
    assert data["event"] == "pre-commit"
    assert data["status"] == "passed"
    assert data["exit_code"] == 0
