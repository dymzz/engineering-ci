"""Unified CLI delegates to existing business functions without dispatching in menus."""

import subprocess
import sys
from pathlib import Path

import pytest

from engineering_ci import workflow_actions
from engineering_ci.cli import download, main, release
from engineering_ci.release import manifest, publishing

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "command,module",
    [
        ("checks", "engineering_ci.checks"),
        ("release", "engineering_ci.cli.release"),
        ("download", "engineering_ci.cli.download"),
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
        download.interactive(
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
        download.choose(["run"], "runs", str, ask=lambda _: "q", write=lambda _: None)
    with pytest.raises(ValueError, match="No"):
        download.choose([], "runs", str)


def test_noninteractive_menu_is_rejected(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit) as error:
        download.main(["--interactive"])
    assert error.value.code == 2


def test_scripts_have_only_the_two_user_entrypoints():
    assert {path.name for path in (ROOT / "scripts").glob("*.py")} == {
        "release.py",
        "download.py",
    }


@pytest.mark.parametrize("entry", ["release.py", "download.py"])
def test_user_entrypoints_offer_help(entry):
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
    assert "engineering_ci.workflow_actions checks ruff-lint" in ci
    assert "engineering_ci.workflow_actions checks ruff-format" in ci
    assert "engineering_ci.workflow_actions checks tests" in ci
    assert "engineering_ci.workflow_actions checks version lock" in ci
    assert "scripts/manual_release.py" not in release
    assert "engineering_ci.workflow_actions release publish" in release
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
        download.api("owner/repo", "actions/workflows")
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
    download.interactive(
        "owner/repo",
        "out",
        query=query,
        ask=lambda _: "1",
        write=lambda _: None,
        invoke=lambda values: args.extend(values) or 0,
    )
    assert ("--no-artifacts" in args) is logs_only
    assert "--resume" in args and "--save-log" in args


@pytest.mark.parametrize(
    "operation,module",
    [
        ("checks", "engineering_ci.checks"),
        ("release", "engineering_ci.release.publishing"),
        ("manifest", "engineering_ci.release.manifest"),
        ("security", "engineering_ci.tools.security"),
    ],
)
def test_actions_dispatcher_preserves_original_result(operation, module, monkeypatch):
    import importlib

    calls = []
    monkeypatch.setattr(
        importlib.import_module(module), "main", lambda args: calls.append(args) or 7
    )
    assert workflow_actions.main([operation, "argument"]) == 7
    assert calls == [["argument"]]


def run_metadata(**updates):
    return dict(
        id=45,
        workflow_id=12,
        head_branch="feature",
        path=".github/workflows/ci.yml",
        repository={"full_name": "owner/repo"},
        **updates,
    )


def test_download_resolves_real_run_identity_and_forwards_transport(monkeypatch):
    calls = []
    monkeypatch.setattr(download, "api", lambda repo, path: run_metadata())
    monkeypatch.setattr(download, "entrypoint", lambda args: calls.append(args) or 5)
    assert (
        download.main(
            [
                "--repo",
                "owner/repo",
                "--run",
                "45",
                "--out",
                "output with spaces",
                "--aria2c",
                "D:/tools/aria2c.exe",
                "--connections",
                "2",
                "--download-backend",
                "native",
                "--save-log",
            ]
        )
        == 5
    )
    from engineering_ci.execution import cli

    args = cli.parser().parse_args(calls[0])
    assert args.workflow == "12" and args.ref == "feature" and args.resume == "45"
    assert args.out == "output with spaces" and args.save_log and args.connections == 2
    assert args.aria2c == "D:/tools/aria2c.exe" and args.download_backend == "native"
    assert not args.new_run and not args.input


@pytest.mark.parametrize(
    "args",
    [
        ["--new-run"],
        ["--input", "publish=true"],
        ["--resume"],
        ["--run", "0"],
        ["--run", "last"],
        ["--run", "45", "--new-run"],
    ],
)
def test_download_rejects_dispatch_or_missing_id_before_network(args, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid download arguments must not access GitHub")

    monkeypatch.setattr(download, "api", forbidden)
    monkeypatch.setattr(download, "entrypoint", forbidden)
    with pytest.raises(SystemExit) as error:
        download.main(args)
    assert error.value.code == 2


@pytest.mark.parametrize(
    "patch",
    [
        {"id": 46},
        {"workflow_id": None},
        {"head_branch": ""},
        {"repository": {"full_name": "other/repo"}},
        {"repository": None},
    ],
)
def test_invalid_or_foreign_run_is_rejected(patch):
    value = run_metadata()
    value.update(patch)
    with pytest.raises(ValueError):
        download.existing_run("owner/repo", 45, query=lambda *args: value)


@pytest.mark.parametrize("workflow,ref", [("other.yml", None), (None, "main")])
def test_caller_constraints_cannot_change_run_identity(workflow, ref):
    with pytest.raises(ValueError):
        download.existing_run(
            "owner/repo", 45, workflow, ref, query=lambda *args: run_metadata()
        )


@pytest.mark.parametrize("mode,publish", [("verify", "false"), ("publish", "true")])
def test_release_requests_existing_pipeline_with_explicit_publication(
    mode, publish, monkeypatch
):
    calls = []
    monkeypatch.setattr(release, "entrypoint", lambda args: calls.append(args) or 1)
    assert (
        release.main([mode, "--version", "0.4.11", "--download-backend", "native"]) == 1
    )
    from engineering_ci.execution import cli

    args = cli.parser().parse_args(calls[0])
    assert args.workflow == "release.yml" and args.ref == "main"
    assert args.input == [
        "version=v0.4.11",
        f"publish={publish}",
        "pytest_scenario=pass",
    ]
    assert not args.new_run and args.save_log


@pytest.mark.parametrize(
    "args",
    [
        ["publish", "--version", "v0.4.11", "--ref", "feature"],
        ["publish", "--version", "v0.4.11", "--scenario", "assertion"],
        ["verify", "--version", "latest"],
        ["verify", "--version", "v0.4.11", "--repo", "invalid"],
    ],
)
def test_invalid_release_request_does_not_dispatch(args, monkeypatch):
    monkeypatch.setattr(
        release, "entrypoint", lambda args: pytest.fail("Must reject before dispatch")
    )
    with pytest.raises(SystemExit) as error:
        release.main(args)
    assert error.value.code == 2


def test_release_unknown_response_still_blocks_second_post(tmp_path, monkeypatch):
    from engineering_ci.execution import cli
    from engineering_ci.execution.models import DispatchUnknown

    posts = []

    class Client:
        def validate(self, identity):
            pass

        def dispatch(self, identity, inputs):
            posts.append(dict(inputs))
            raise DispatchUnknown()

    monkeypatch.setattr(cli, "default_cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/mock/gh")
    monkeypatch.setattr(cli, "GitHubActionsClient", Client)
    args = [
        "verify",
        "--version",
        "v0.4.11",
        "--ref",
        "feature",
        "--download-backend",
        "native",
        "--out",
        str(tmp_path / "out"),
    ]
    assert release.main(args) == 1
    assert release.main(args) == 1
    assert len(posts) == 1
    import json

    state = json.loads(next((tmp_path / "cache/runs").glob("*.json")).read_text())
    assert state["phase"] == "UNKNOWN"


def test_download_uses_coordinator_without_validation_or_dispatch(
    tmp_path, monkeypatch
):
    from engineering_ci.execution import cli

    runs = []

    class Client:
        def validate(self, identity):
            pytest.fail("Attaching an existing Run does not validate a new request")

        def dispatch(self, identity, inputs):
            pytest.fail("Downloads cannot dispatch")

        def get_run(self, identity, run_id):
            runs.append(run_id)
            return run_metadata()

        def wait(self, identity, run_id, timeout, interval):
            runs.append(run_id)
            return {"conclusion": "success"}

    class Collector:
        def __init__(self, *args):
            pass

        def intact(self, record):
            return True

    monkeypatch.setattr(download, "api", lambda *args: run_metadata())
    monkeypatch.setattr(cli, "default_cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/mock/gh")
    monkeypatch.setattr(cli, "GitHubActionsClient", Client)
    monkeypatch.setattr(cli, "ArtifactCollector", Collector)
    args = [
        "--repo",
        "owner/repo",
        "--run",
        "45",
        "--download-backend",
        "native",
        "--out",
        str(tmp_path / "out"),
    ]
    assert download.main(args) == 0
    assert download.main(args) == 0
    assert runs == [45, 45, 45]
