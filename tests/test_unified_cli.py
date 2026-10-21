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
from engineering_ci.cli import download, main, release
from engineering_ci.release import manifest, publishing

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def mock_release_request_metadata(monkeypatch):
    """These routing/FSM tests never issue GitHub reads; evidence is tested separately."""

    class API:
        def __init__(self, repository):
            pass

        def __call__(self, path):
            assert path.startswith("actions/runs/")
            return {"head_sha": "a" * 40, "head_branch": "feature"}

    monkeypatch.setattr(release, "GitHub", API)
    monkeypatch.setattr(release, "prepare_publication", lambda options: 0)
    monkeypatch.setattr(release, "execution_checkpoint", lambda *args: None)
    monkeypatch.setattr(
        release,
        "resolve_target",
        lambda api, ref, version: {
            "version": version or "0.4.11",
            "tag": "v" + (version or "0.4.11"),
            "commit_sha": "a" * 40,
            "ref": ref,
        },
    )
    monkeypatch.setattr(release.verification, "published", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        release.verification,
        "validation",
        lambda *args, **kwargs: {
            "run_id": 123,
            "run_attempt": 1,
            "manifest_artifact_id": 99,
        },
    )


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


@pytest.mark.parametrize("command", ["trace", "mine", "workflow"])
def test_removed_harness_commands_are_rejected(command):
    with pytest.raises(SystemExit) as error:
        main([command, "--help"])
    assert error.value.code == 2


@pytest.mark.parametrize("lockfile", ["uv.lock", ".dagger/uv.lock"])
def test_ci_locked_dependency_closure_does_not_include_harness(lockfile):
    data = tomllib.loads((ROOT / lockfile).read_text(encoding="utf-8"))
    packages = {item["name"].lower().replace("_", "-") for item in data["package"]}
    assert packages.isdisjoint({"vesryn", "vesryn-workflow"})


@pytest.mark.parametrize(
    "option", ["--binding", "--provider-root", "--check-target", "--output-root"]
)
def test_checks_rejects_removed_provider_options(option):
    with pytest.raises(SystemExit) as error:
        main(["checks", "pr", option, "retired-provider"])
    assert error.value.code == 2


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
@pytest.mark.parametrize("directory", ["scripts", "src/engineering_ci/cli"])
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


@pytest.mark.parametrize(
    "entry,args",
    [
        ("release.py", ["validate", "--version", "v0.4.11"]),
        ("download.py", ["--repo", "owner/repo", "--run", "45"]),
    ],
)
def test_plain_python_reports_missing_environment_without_traceback(entry, args):
    result = subprocess.run(
        [sys.executable, "-S", str(ROOT / "scripts" / entry), *args],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "Missing project dependency" in result.stderr
    assert "uv run --locked python" in result.stderr
    assert "Traceback" not in result.stderr


def test_download_requires_explicit_mode_before_network(monkeypatch):
    monkeypatch.setattr(download, "api", lambda *args: pytest.fail("No implicit menu"))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    with pytest.raises(SystemExit) as error:
        download.main(["--repo", "owner/repo"])
    assert error.value.code == 2


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
        importlib.import_module(module),
        "main",
        lambda args, **kwargs: calls.append((args, kwargs)) or 7,
    )
    assert workflow_actions.main([operation, "argument"]) == 7
    assert calls == [
        (["argument"], {"executor": "native"} if operation == "checks" else {})
    ]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("true", ["tests", "--coverage"]),
        ("false", ["tests"]),
        ("", ["tests"]),
        (None, ["tests"]),
    ],
)
def test_actions_coverage_input_uses_one_check_execution_and_preserves_exit(
    monkeypatch, value, expected
):
    from engineering_ci import checks

    if value is None:
        monkeypatch.delenv("EXPORT_COVERAGE", raising=False)
    else:
        monkeypatch.setenv("EXPORT_COVERAGE", value)
    calls = []
    monkeypatch.setattr(
        checks, "main", lambda args, **kwargs: calls.append((args, kwargs)) or 7
    )
    assert workflow_actions.main(["checks", "tests", "--coverage-from-env"]) == 7
    assert calls == [(expected, {"executor": "native"})]


@pytest.mark.parametrize("value", ["yes", "True", "SECRET", "1"])
def test_actions_invalid_coverage_input_fails_before_execution(
    monkeypatch, capsys, value
):
    from engineering_ci import checks

    monkeypatch.setenv("EXPORT_COVERAGE", value)
    monkeypatch.setattr(
        checks, "main", lambda *args, **kwargs: pytest.fail("No check execution")
    )
    with pytest.raises(SystemExit) as error:
        workflow_actions.main(["checks", "tests", "--coverage-from-env"])
    assert error.value.code == 2
    assert "SECRET" not in capsys.readouterr().err


def test_actions_coverage_source_is_explicit_and_not_ambient(monkeypatch):
    from engineering_ci import checks

    monkeypatch.setenv("EXPORT_COVERAGE", "true")
    calls = []
    monkeypatch.setattr(checks, "main", lambda args, **kwargs: calls.append(args) or 0)
    assert workflow_actions.main(["checks", "tests"]) == 0
    assert calls == [["tests"]]
    for flags in (
        ["--coverage", "--coverage-from-env"],
        ["--coverage-from-env", "--coverage-from-env"],
    ):
        with pytest.raises(SystemExit):
            workflow_actions.main(["checks", "tests", *flags])
    assert calls == [["tests"]]


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


@pytest.mark.parametrize("mode,publish", [("validate", "false"), ("publish", "true")])
def test_release_requests_existing_pipeline_with_explicit_publication(
    mode, publish, monkeypatch
):
    calls = []
    monkeypatch.setattr(release, "entrypoint", lambda args: calls.append(args) or 1)
    options = [mode, "--version", "0.4.11", "--timeout", "90"]
    assert release.main(options) == 1
    from engineering_ci.execution import cli

    args = cli.parser().parse_args(calls[0])
    assert args.workflow == "release.yml" and args.ref == "main"
    assert args.input == [
        "version=v0.4.11",
        f"publish={publish}",
        "pytest_scenario=pass",
    ]
    assert not args.new_run and not args.save_log
    assert args.timeout == 90 and args.download_backend is None


def test_configured_workflow_reaches_coordinator_and_saved_dispatch_identity(
    tmp_path, monkeypatch
):
    from engineering_ci.execution import cli

    posts = []
    workflow = "reader-probe.yml"
    # Inject a reader input to detect hard-coded routing; not a supported TOML target.
    monkeypatch.setitem(release.RELEASE_CONFIG, "workflow", workflow)

    class Client:
        def validate(self, identity):
            assert identity.workflow == workflow

        def dispatch(self, identity, inputs):
            posts.append(identity)
            return 45

        def get_run(self, identity, run_id):
            assert identity.workflow == workflow and run_id == 45

        def wait(self, identity, run_id, timeout, interval):
            assert identity.workflow == workflow and run_id == 45
            return {"conclusion": "success"}

    monkeypatch.setattr(cli, "default_cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/mock/gh")
    monkeypatch.setattr(cli, "GitHubActionsClient", Client)
    args = ["validate", "--version", "1.2.3", "--ref", "feature"]
    assert release.main(args) == 0
    assert release.main([*args, "--resume", "45"]) == 0
    assert len(posts) == 1
    state = json.loads(next((tmp_path / "cache/runs").glob("*.json")).read_text())
    assert state["dispatch"]["workflow"] == workflow
    assert state["run_id"] == 45 and state["phase"] == "FINISHED"


@pytest.mark.parametrize(
    "args",
    [
        ["publish", "--version", "v0.4.11", "--ref", "feature"],
        ["publish", "--version", "v0.4.11", "--scenario", "assertion"],
        ["verify", "--version", "latest"],
        ["verify", "--version", "v0.4.11", "--repo", "invalid"],
        ["verify", "--version", "v0.4.11", "--save-log"],
        ["verify", "--version", "v0.4.11", "--artifact", "report"],
        ["verify", "--version", "v0.4.11", "--download-backend", "native"],
        ["verify", "--version", "v0.4.11", "--out", "output"],
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
        "validate",
        "--version",
        "v0.4.11",
        "--ref",
        "feature",
    ]
    assert release.main(args) == 1
    assert release.main(args) == 1
    assert len(posts) == 1
    import json

    state = json.loads(next((tmp_path / "cache/runs").glob("*.json")).read_text())
    assert state["phase"] == "UNKNOWN"


@pytest.mark.parametrize("conclusion", ["success", "failure"])
def test_explicit_validate_can_follow_a_known_finished_attempt(
    tmp_path, monkeypatch, conclusion
):
    from engineering_ci.execution import cli

    posts = []

    class Client:
        def validate(self, identity):
            pass

        def dispatch(self, identity, inputs):
            posts.append(dict(inputs))
            return 40 + len(posts)

        def wait(self, identity, run_id, timeout, interval):
            return {"conclusion": conclusion}

    monkeypatch.setattr(cli, "default_cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/mock/gh")
    monkeypatch.setattr(cli, "GitHubActionsClient", Client)
    args = ["validate", "--version", "0.4.11", "--ref", "feature"]
    expected = 0 if conclusion == "success" else 1
    assert release.main(args) == expected
    assert release.main(args) == expected
    assert len(posts) == 2


@pytest.mark.parametrize("conclusion", ["success", "failure", "cancelled"])
def test_release_waits_without_downloading_and_preserves_remote_failure(
    tmp_path, monkeypatch, capsys, conclusion
):
    from engineering_ci.execution import cli

    posts, waits = [], []

    class Client:
        def validate(self, identity):
            assert identity.workflow == "release.yml"

        def dispatch(self, identity, inputs):
            posts.append(inputs)
            return 45

        def get_run(self, identity, run_id):
            assert run_id == 45

        def wait(self, identity, run_id, timeout, interval):
            waits.append(run_id)
            return {"conclusion": conclusion}

    def no_download(*args, **kwargs):
        pytest.fail(
            "Release requests cannot initialize download configuration or collection"
        )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "default_cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/mock/gh")
    monkeypatch.setattr(cli, "GitHubActionsClient", Client)
    for name in ("load_download_config", "DownloadAdapter", "ArtifactCollector"):
        monkeypatch.setattr(cli, name, no_download)
    args = ["validate", "--version", "v0.4.11", "--ref", "feature"]
    expected = 0 if conclusion == "success" else 1
    assert release.main(args) == expected
    assert release.main([*args, "--resume", "45"]) == expected
    assert len(posts) == 1 and waits == [45]
    state = json.loads(next((tmp_path / "cache/runs").glob("*.json")).read_text())
    assert state["phase"] == "FINISHED" and state["remote_conclusion"] == conclusion
    assert state["collection"]["no_artifacts"] and not state["collection"]["save_log"]
    assert not state["files"]
    assert not (tmp_path / "workflow-artifacts").exists()
    assert (
        "https://github.com/dymzz/engineering-ci/actions/runs/45"
        in capsys.readouterr().out
    )


def test_release_wait_interruption_resumes_same_run_without_post(tmp_path, monkeypatch):
    from engineering_ci.execution import cli
    from engineering_ci.execution.models import RecoverableError

    posts, waits = [], []

    class Client:
        def validate(self, identity):
            pass

        def dispatch(self, identity, inputs):
            posts.append(inputs)
            return 45

        def get_run(self, identity, run_id):
            assert run_id == 45

        def wait(self, identity, run_id, timeout, interval):
            waits.append(run_id)
            if len(waits) == 1:
                raise RecoverableError("Network interrupted")
            return {"conclusion": "success"}

    monkeypatch.setattr(cli, "default_cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/mock/gh")
    monkeypatch.setattr(cli, "GitHubActionsClient", Client)
    args = ["validate", "--version", "v0.4.11", "--ref", "feature"]
    assert release.main(args) == 1
    state_path = next((tmp_path / "cache/runs").glob("*.json"))
    paused = json.loads(state_path.read_text())
    assert paused["phase"] == "PAUSED" and paused["paused_from"] == "RUNNING"
    assert release.main([*args, "--resume", "45"]) == 0
    assert len(posts) == 1 and waits == [45, 45]


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
