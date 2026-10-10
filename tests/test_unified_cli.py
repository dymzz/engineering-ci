"""Unified CLI delegates to existing business functions without dispatching in menus."""

import subprocess
import sys
from pathlib import Path

import pytest

from engineering_ci.cli import main, workflow
from engineering_ci.release import manifest, publishing

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "command,module",
    [
        ("checks", "engineering_ci.checks"),
        ("release", "engineering_ci.release.publishing"),
        ("manifest", "engineering_ci.release.manifest"),
        ("version", "engineering_ci.release.version"),
    ],
)
def test_unified_cli_preserves_exit_status(command, module, monkeypatch):
    import importlib

    calls = []
    monkeypatch.setattr(
        importlib.import_module(module), "main", lambda args: calls.append(args) or 7
    )
    assert main([command, "parameter"]) == 7 and calls == [["parameter"]]


def test_menu_chooses_existing_run_and_artifact_without_post():
    calls, selected = [], []

    def query(repo, path):
        calls.append(path)
        if path.startswith("actions/workflows?"):
            return {
                "workflows": [
                    {"id": 12, "name": "CI", "path": ".github/workflows/ci.yml"}
                ]
            }
        if "/runs?" in path:
            return {
                "workflow_runs": [
                    {
                        "id": 45,
                        "workflow_id": 12,
                        "run_number": 3,
                        "head_branch": "feature",
                        "status": "completed",
                        "conclusion": "success",
                        "display_title": "Test",
                    }
                ]
            }
        return {
            "artifacts": [
                {"id": 67, "name": "report", "expired": False},
                {"id": 68, "name": "old", "expired": True},
            ]
        }

    answers = iter(["1", "1", "1"])
    assert (
        workflow.interactive(
            "owner/repo",
            "path with spaces",
            query=query,
            ask=lambda _: next(answers),
            write=lambda _: None,
            invoke=lambda args: selected.append(args) or 0,
        )
        == 0
    )
    args = selected[0]
    assert args[args.index("--resume") + 1] == "45"
    assert args[args.index("--artifact") + 1] == "report"
    assert args[args.index("--ref") + 1] == "feature"
    assert "--new-run" not in args and "dispatches" not in " ".join(calls)


def test_cancel_and_empty_menu_do_not_execute():
    with pytest.raises(KeyboardInterrupt):
        workflow.choose(["run"], "runs", str, ask=lambda _: "q", write=lambda _: None)
    with pytest.raises(ValueError, match="No"):
        workflow.choose([], "runs", str)


def test_noninteractive_menu_is_rejected(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit) as error:
        workflow.main(["--interactive"])
    assert error.value.code == 2


def test_legacy_imports_reference_the_moved_implementation():
    sys.path.insert(0, str(ROOT / "scripts"))
    import manual_release
    import release_manifest

    assert manual_release is publishing and release_manifest is manifest


@pytest.mark.parametrize(
    "entry",
    [
        "manual_release.py",
        "release_manifest.py",
        "release_version.py",
        "gh_workflow_artifacts.py",
        "ghwf.py",
        "checks.py",
    ],
)
def test_compatibility_entrypoints_still_offer_help(entry):
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / entry), "--help"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0 and "usage:" in result.stdout.lower()


def test_workflows_call_modules_and_preserve_release_step_names():
    from engineering_ci import checks

    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    release = (ROOT / ".github/workflows/release.yml").read_text()
    assert (
        "scripts/checks.py" not in ci
        and "uvx ruff" not in ci
        and "uv run --locked --group dev pytest" not in ci
    )
    assert "engineering_ci.cli checks ruff-lint" in ci
    assert "engineering_ci.cli checks ruff-format" in ci
    assert "engineering_ci.cli checks tests" in ci
    assert "engineering_ci.cli checks version lock" in ci
    assert "scripts/manual_release.py" not in release
    assert "engineering_ci.cli release publish" in release
    assert all(f"name: {name}" in ci for name in checks.CI_STEPS.values())
    for key, step in (
        ("ruff_lint", "ruff-lint"),
        ("ruff_format", "ruff-format"),
        ("core_tests", "pytest"),
        ("version_integrity", "version"),
    ):
        assert manifest.CHECKS[key][1] == checks.CI_STEPS[step]
    assert "artifact-ids:" in release and "workflow_dispatch:" in release
    assert (
        "core-coverage.xml" in ci
        and "core-coverage.json" in ci
        and "core-junit.xml" in ci
    )


def test_api_error_does_not_echo_sensitive_response(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            [], 1, stdout='{"token":"SECRET"}', stderr="SECRET (HTTP 403)"
        ),
    )
    with pytest.raises(publishing.ApiError) as error:
        workflow.api("owner/repo", "actions/workflows")
    assert "SECRET" not in str(error.value)


@pytest.mark.parametrize(
    "status,logs_only", [("in_progress", False), ("completed", True)]
)
def test_empty_artifact_list_does_not_disable_future_run_outputs(status, logs_only):
    def query(repo, path):
        if path.startswith("actions/workflows?"):
            return {"workflows": [{"id": 12, "name": "CI", "path": "ci.yml"}]}
        if "/runs?" in path:
            return {
                "workflow_runs": [
                    {
                        "id": 45,
                        "workflow_id": 12,
                        "run_number": 1,
                        "head_branch": "main",
                        "status": status,
                        "conclusion": None,
                    }
                ]
            }
        return {"artifacts": []}

    args = []
    workflow.interactive(
        "owner/repo",
        "out",
        query=query,
        ask=lambda _: "1",
        write=lambda _: None,
        invoke=lambda values: args.extend(values) or 0,
    )
    assert ("--no-artifacts" in args) is logs_only
    assert "--resume" in args and "--save-log" in args
