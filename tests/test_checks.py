"""Check execution and freshness contracts; child pytest failures remain failures."""

import json
import shutil
import stat
import subprocess
import sys

import pytest

from engineering_ci import checks


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/checks.py").write_text("# input\n")
    (tmp_path / "scripts/release_version.py").write_text("# version\n")
    (tmp_path / "pyproject.toml").write_text("# configuration\n")
    (tmp_path / "uv.lock").write_text("# lock\n")
    (tmp_path / "tests").mkdir()
    parser = tmp_path / ".dagger/src/engineering_ci/pytest_report.py"
    parser.parent.mkdir(parents=True)
    shutil.copyfile(checks.ROOT / ".dagger/src/engineering_ci/pytest_report.py", parser)
    monkeypatch.setattr(
        checks, "preflight", lambda *args: {"python": {"version": "3.14.3"}}
    )
    return tmp_path


def fake_process(code=0, action=None):
    def invoke(args, **kwargs):
        assert kwargs["check"] is False
        if "pytest" in args:
            (kwargs["cwd"] / "core-junit.xml").write_text(
                '<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase name="ok"/></testsuite>'
            )
        if action:
            action(args, kwargs)
        return subprocess.CompletedProcess(args, code)

    return invoke


def test_selection_deduplicates_groups_and_rejects_unknown():
    assert checks.selected(["pr", "ruff", "tests"]) == list(checks.PR_CHECKS)
    assert checks.selected(["ruff-format", "version"]) == ["ruff-format", "version"]
    with pytest.raises(ValueError, match="Unsupported"):
        checks.selected(["not-a-check"])


def test_shared_parameters_and_coverage_do_not_change_check_scope():
    for name in checks.PR_CHECKS:
        assert checks.command(name) == checks.command(checks.selected([name])[0])
    lint = checks.command("ruff-lint")
    assert lint[:7] == [
        "uvx",
        "--no-config",
        f"ruff=={checks.RUFF_VERSION}",
        "check",
        "--isolated",
        "--target-version",
        "py311",
    ]
    assert lint[7:] == list(checks.RUFF_SCOPE)
    assert "--check" in checks.command("ruff-format")
    plain = checks.command("pytest")
    covered = checks.command("pytest", coverage=True)
    assert covered[: len(plain)] == plain
    assert "--cov-report=json:core-coverage.json" in covered
    assert plain.count("pytest") == 1
    assert "--locked" in plain and "--python" in plain
    assert checks.command("lock") == [
        "uv",
        "lock",
        "--project",
        ".",
        "--check",
        "--python",
        checks.PYTHON_VERSION,
    ]


@pytest.mark.parametrize("code", [0, 1, 2, 7, -15])
def test_raw_exit_and_streams_are_preserved(checkout, code):
    calls = []

    def invoke(args, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(args, code)

    report, status = checks.execute(["version"], root=checkout, run=invoke, environ={})
    entry = report["checks"][0]
    assert entry["executed"] and entry["exit_code"] == code
    assert status == (128 - code if code < 0 else code)
    assert entry["status"] == ("passed" if code == 0 else "failed")
    assert (
        "capture_output" not in calls[0]
        and "stdout" not in calls[0]
        and "stderr" not in calls[0]
    )


def test_aggregate_saves_every_requested_check_and_no_environment_values(checkout):
    out = checkout / "result.json"
    report, code = checks.execute(
        ["pr"],
        root=checkout,
        run=fake_process(),
        output=out,
        environ={
            "GH_TOKEN": "SECRET_TOKEN",
            "UV_INDEX_URL": "https://user:SECRET_PASSWORD@example.invalid/simple",
        },
    )
    assert code == 0 and report["overall_status"] == "passed"
    saved = json.loads(out.read_text())
    assert [entry["name"] for entry in saved["checks"]] == list(checks.PR_CHECKS)
    assert all(
        entry["executed"] and entry["passed_for_current_input"]
        for entry in saved["checks"]
    )
    assert "SECRET" not in out.read_text()


def test_failure_does_not_skip_other_requested_checks(checkout):
    report, code = checks.execute(
        ["ruff", "version"], root=checkout, run=fake_process(4), environ={}
    )
    assert code == 4
    assert all(
        entry["executed"] and entry["status"] == "failed" for entry in report["checks"]
    )


def test_missing_tool_is_not_executed_and_saves_results(checkout, monkeypatch):
    def absent(*args):
        raise checks.PreconditionError("missing_tool", "uv is required")

    monkeypatch.setattr(checks, "preflight", absent)
    out = checkout / "result.json"
    report, code = checks.execute(["ruff"], root=checkout, output=out, environ={})
    assert code == 127 and out.exists()
    assert all(
        entry["status"] == "missing_tool"
        and not entry["executed"]
        and entry["exit_code"] is None
        for entry in report["checks"]
    )


def test_real_preflight_distinguishes_tool_and_environment(monkeypatch, tmp_path):
    with pytest.raises(checks.PreconditionError) as error:
        checks.preflight("ruff-lint", tmp_path, None, lambda _: None, {})
    assert error.value.status == "missing_tool"
    monkeypatch.setattr(
        checks, "tool", lambda *args: {"executable": "uv", "version": "0.0.1"}
    )
    with pytest.raises(checks.PreconditionError) as error:
        checks.preflight("ruff-lint", tmp_path, None, lambda _: "uv", {})
    assert error.value.status == "environment_error"


def test_invalid_aria2_directory_and_pytest_overrides_are_explicit(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        checks, "tool", lambda *args: {"executable": "uv", "version": checks.UV_VERSION}
    )
    with pytest.raises(checks.PreconditionError, match="aria2c"):
        checks.preflight(
            "pytest", tmp_path, None, lambda _: "uv", {"ARIA2C": str(tmp_path)}
        )
    with pytest.raises(checks.PreconditionError, match="PYTEST_ADDOPTS"):
        checks.preflight(
            "pytest",
            tmp_path,
            None,
            lambda _: "uv",
            {"PYTEST_ADDOPTS": "--ignore=tests"},
        )


@pytest.mark.parametrize(
    "file",
    [
        "tests/uncommitted.py",
        "pyproject.toml",
        "uv.lock",
        "scripts/checks.py",
        ".github/workflows/release.yml",
        ".dagger/pyproject.toml",
        "docs/release-manifest.schema.json",
    ],
)
def test_changed_and_untracked_inputs_expire_old_pass(checkout, file):
    out = checkout / "result.json"
    report, code = checks.execute(
        ["tests"], root=checkout, run=fake_process(), output=out, environ={}
    )
    assert code == 0
    assert checks.inspect(out, root=checkout, environ={})[1] == 0
    (checkout / file).parent.mkdir(parents=True, exist_ok=True)
    (checkout / file).write_text("# changed input\n")
    current, code = checks.inspect(out, root=checkout, environ={})
    assert code == 3 and current["checks"][0]["freshness"] == "stale"
    assert not current["checks"][0]["passed_for_current_input"]
    assert json.loads(out.read_text()) == report  # inspection never rewrites history


def test_file_permissions_expire_old_pass(checkout):
    source = checkout / "scripts/release_version.py"
    source.write_text("print('version')\n")
    out = checkout / "result.json"
    _, code = checks.execute(
        ["version"], root=checkout, run=fake_process(), output=out, environ={}
    )
    assert code == 0
    assert checks.inspect(out, root=checkout, environ={})[1] == 0
    original_mode = source.stat().st_mode
    try:
        source.chmod(original_mode & ~stat.S_IWRITE)
        current, code = checks.inspect(out, root=checkout, environ={})
        assert code == 3 and current["checks"][0]["freshness"] == "stale"
        assert not current["checks"][0]["passed_for_current_input"]
    finally:
        source.chmod(original_mode)


def test_tool_version_and_check_parameters_expire_old_pass(checkout, monkeypatch):
    out = checkout / "result.json"
    checks.execute(
        ["ruff-lint"], root=checkout, run=fake_process(), output=out, environ={}
    )
    monkeypatch.setattr(
        checks, "preflight", lambda *args: {"python": {"version": "3.14.4"}}
    )
    assert (
        checks.inspect(out, root=checkout, environ={})[0]["checks"][0]["freshness"]
        == "stale"
    )
    monkeypatch.setattr(
        checks, "preflight", lambda *args: {"python": {"version": "3.14.3"}}
    )
    previous = checks.command
    monkeypatch.setattr(
        checks,
        "command",
        lambda *args, **kwargs: [*previous(*args, **kwargs), "--new-parameter"],
    )
    assert checks.inspect(out, root=checkout, environ={})[1] == 3


@pytest.mark.parametrize("restore", [False, True])
def test_inputs_changed_during_execution_never_pass(checkout, restore):
    path = checkout / "pyproject.toml"
    original = path.read_text()

    def mutate(*args):
        path.write_text("# new configuration\n")
        if restore:
            path.write_text(original)

    report, code = checks.execute(
        ["version"], root=checkout, run=fake_process(action=mutate), environ={}
    )
    assert code == 3 and report["checks"][0]["status"] == "stale"
    assert not report["checks"][0]["passed_for_current_input"]


def test_skipped_tests_do_not_claim_complete_parity(checkout, monkeypatch):
    monkeypatch.setattr(
        checks,
        "pytest_counts",
        lambda *args: {
            "tests": 2,
            "failures": 0,
            "errors": 0,
            "skipped": 1,
            "passed": 1,
        },
    )
    report, code = checks.execute(
        ["tests"], root=checkout, run=fake_process(), environ={}
    )
    assert code == 3 and report["checks"][0]["status"] == "incomplete"
    assert report["checks"][0]["exit_code"] == 0


def test_missing_junit_does_not_reuse_an_old_success(checkout):
    report, code = checks.execute(
        ["tests"],
        root=checkout,
        run=lambda args, **kwargs: subprocess.CompletedProcess(args, 0),
        environ={},
    )
    assert code == 3 and not report["checks"][0]["passed_for_current_input"]


def test_interrupt_preserves_not_run_entries(checkout):
    out = checkout / "result.json"

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    report, code = checks.execute(
        ["ruff", "version"], root=checkout, run=interrupted, output=out, environ={}
    )
    assert code == 130 and out.exists()
    assert report["checks"][0]["status"] == "interrupted"
    assert all(
        entry["status"] == "not_run" and not entry["executed"]
        for entry in report["checks"][1:]
    )


def test_json_failure_does_not_replace_original_tool_failure(checkout, monkeypatch):
    monkeypatch.setattr(
        checks, "atomic_json", lambda *args: (_ for _ in ()).throw(OSError("disk full"))
    )
    report, code = checks.execute(
        ["version"],
        root=checkout,
        run=fake_process(7),
        output=checkout / "report.json",
        environ={},
    )
    assert code == 7 and report["checks"][0]["exit_code"] == 7


def test_report_cannot_overwrite_source_or_execution_evidence(checkout):
    for path in (
        checkout / "pyproject.toml",
        checkout / "tests/source.json",
        checkout / "core-junit.xml",
    ):
        with pytest.raises(ValueError, match="overwrite"):
            checks.execute(["version"], root=checkout, output=path, environ={})


def test_real_child_pytest_failure_remains_failure(checkout, monkeypatch):
    source = checkout / "tests/test_failure.py"
    source.write_text("def test_actual_assertion():\n    assert 1 == 2\n")
    monkeypatch.setattr(
        checks,
        "command",
        lambda *args, **kwargs: [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            str(source),
            "--junitxml=core-junit.xml",
        ],
    )
    out = checkout / "result.json"
    report, code = checks.execute(
        ["tests"], root=checkout, output=out, environ=dict(__import__("os").environ)
    )
    assert code == 1 and report["checks"][0]["exit_code"] == 1
    assert report["checks"][0]["status"] == "failed"
    assert not report["checks"][0]["passed_for_current_input"]
    assert "test_actual_assertion" in (checkout / "core-junit.xml").read_text()


def test_actions_bootstrap_uses_the_registry(capsys, tmp_path):
    out = tmp_path / "github-output"
    assert checks.main(["toolchain", "--github-output", str(out)]) == 0
    assert f"uv_version={checks.UV_VERSION}\n" in out.read_text()
    assert (
        json.loads(capsys.readouterr().out)["python_version"] == checks.PYTHON_VERSION
    )


def test_later_check_cannot_leave_an_earlier_pass_current(checkout):
    def mutate(args, kwargs):
        if "version" in args:
            (checkout / "scripts/manual_release.py").write_text(
                "# new input after Ruff\n"
            )

    report, code = checks.execute(
        ["ruff-lint", "version"],
        root=checkout,
        run=fake_process(action=mutate),
        environ={},
    )
    assert code == 3 and report["checks"][0]["status"] == "stale"
    assert not report["checks"][0]["passed_for_current_input"]


def test_tool_configuration_file_changes_expire_pass(checkout):
    config = checkout / "uv-machine.toml"
    config.write_text('index-url = "https://example.invalid/simple"\n')
    env = {"UV_CONFIG_FILE": str(config)}
    out = checkout / "result.json"
    checks.execute(
        ["version"], root=checkout, run=fake_process(), output=out, environ=env
    )
    assert checks.inspect(out, root=checkout, environ=env)[1] == 0
    config.write_text('index-url = "https://changed.invalid/simple"\n')
    assert checks.inspect(out, root=checkout, environ=env)[1] == 3
