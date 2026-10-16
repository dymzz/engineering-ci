"""Check execution and freshness contracts; child pytest failures remain failures."""

import json
import re
import shutil
import stat
import subprocess
import sys

import pytest

from engineering_ci import checks, pipeline


def test_optional_backend_config_loads_without_execution_dependencies(tmp_path):
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from engineering_ci.checks import aria2_executable; "
        "from pathlib import Path; "
        "assert aria2_executable(Path(sys.argv[2]), which=lambda value: None, environ={}) is None; "
        "assert 'transitions' not in sys.modules and 'filelock' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-S", "-c", code, str(checks.ROOT / "src"), str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_invalid_optional_config_has_a_structured_environment_error(tmp_path):
    path = tmp_path / "config/ghwf.local.toml"
    path.parent.mkdir()
    path.write_text("[download]\nconnections = 0\n")
    with pytest.raises(checks.PreconditionError) as error:
        checks.aria2_executable(tmp_path, environ={})
    assert error.value.status == "environment_error"


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    (tmp_path / "src/engineering_ci/release").mkdir(parents=True)
    (tmp_path / "src/engineering_ci/checks.py").write_text("# input\n")
    (tmp_path / "src/engineering_ci/release/version.py").write_text("# version\n")
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


@pytest.mark.parametrize("selectors", [[], (), "pytest", [""], [False], None])
def test_empty_or_invalid_request_never_generates_a_passing_report(checkout, selectors):
    with pytest.raises(ValueError, match="explicit check"):
        checks.execute(
            selectors,
            root=checkout,
            run=lambda *args, **kwargs: pytest.fail("No process"),
        )


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


def test_matching_passes_are_reused_without_running_checks(checkout):
    out = checkout / "result.json"
    checks.execute(["pr"], root=checkout, run=fake_process(), output=out, environ={})

    def unexpected(*args, **kwargs):
        pytest.fail("Matching check must not execute again")

    report, code = checks.execute(
        ["pr"], root=checkout, run=unexpected, output=out, environ={}, reuse=True
    )
    assert code == 0
    assert all(
        item["reused"] and item["executed"] and not item["executed_this_invocation"]
        for item in report["checks"]
    )


def test_valid_pipeline_change_expires_and_refreshes_complete_receipt(
    checkout, monkeypatch
):
    path = checkout / "config/pipeline.toml"
    path.parent.mkdir()
    original_config = (checks.ROOT / "config/pipeline.toml").read_text(encoding="utf-8")
    path.write_text(original_config, encoding="utf-8")
    out = checkout / "result.json"
    saved, code = checks.execute(
        ["pr"], root=checkout, run=fake_process(), output=out, environ={}
    )
    assert code == checks.inspect(out, root=checkout, environ={})[1] == 0

    order = list(reversed(checks.PR_CHECKS))
    changed_config = re.sub(
        r"(?m)^pr = \[.*\]$", "pr = " + json.dumps(order), original_config
    )
    path.write_text(changed_config, encoding="utf-8")
    loaded = pipeline.load(path)
    assert loaded["profiles"]["pr"] == order
    current, code = checks.inspect(out, root=checkout, environ={})
    assert code == 3
    assert all(entry["freshness"] == "stale" for entry in current["checks"])
    assert not any(entry["passed_for_current_input"] for entry in current["checks"])
    assert json.loads(out.read_text()) == saved

    # Model the next invocation's definitions from the validated file.
    monkeypatch.setattr(
        checks,
        "GROUPS",
        {name: tuple(values) for name, values in loaded["profiles"].items()},
    )
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return fake_process()(args, **kwargs)

    refreshed, code = checks.execute(
        [loaded["delivery"]["checks_profile"]],
        root=checkout,
        run=run,
        output=out,
        environ={},
        reuse=True,
    )
    assert code == 0
    assert calls == [checks.command(name) for name in order]
    assert all(
        entry["executed_this_invocation"] and not entry["reused"]
        for entry in refreshed["checks"]
    )


def test_version_changes_only_refresh_dependent_checks(checkout):
    out = checkout / "result.json"
    checks.execute(["pr"], root=checkout, run=fake_process(), output=out, environ={})
    (checkout / "pyproject.toml").write_text('version = "0.4.12"\n')
    calls = []

    def track(args, **kwargs):
        calls.append(args)
        return fake_process()(args, **kwargs)

    report, code = checks.execute(
        ["pr"], root=checkout, run=track, output=out, environ={}, reuse=True
    )
    assert code == 0
    assert [entry["name"] for entry in report["checks"] if entry["reused"]] == [
        "ruff-lint",
        "ruff-format",
    ]
    assert calls == [checks.command(name) for name in ("pytest", "version", "lock")]


def release_metadata(checkout, version):
    for prefix in ("", ".dagger/"):
        project = checkout / (prefix + "pyproject.toml")
        project.parent.mkdir(parents=True, exist_ok=True)
        project.write_text(
            f'[project]\nname = "engineering-ci"\nversion = "{version}"\n'
            'dependencies = ["example==1.0.0"]\n'
        )
        (checkout / (prefix + "uv.lock")).write_text(
            f'[[package]]\nname = "engineering-ci"\nversion = "{version}"\n'
            '[[package]]\nname = "example"\nversion = "1.0.0"\n'
        )


def test_completed_profile_survives_version_preparation_without_rerunning_pytest(
    checkout,
):
    release_metadata(checkout, "0.4.11")
    out = checkout / "result.json"
    original, _ = checks.execute(
        ["pr"], root=checkout, run=fake_process(), output=out, environ={}
    )
    release_metadata(checkout, "0.4.12")
    calls = []

    def track(args, **kwargs):
        calls.append(args)
        assert "pytest" not in args, "A version-only update must reuse pytest evidence"
        return fake_process()(args, **kwargs)

    report, code = checks.execute(
        ["pr"], root=checkout, run=track, output=out, environ={}, reuse=True
    )
    assert code == 0
    assert calls == [checks.command(name) for name in ("version", "lock")]
    assert [entry["name"] for entry in report["checks"] if entry["reused"]] == [
        "ruff-lint",
        "ruff-format",
        "pytest",
    ]
    previous = original["checks"][2]
    assert report["checks"][2]["identity"] == previous["identity"]
    assert previous["identity"]["inputs"]["version_neutral_metadata"] == sorted(
        checks.PIPELINE["inputs"]["release_metadata"]
    )


def test_real_pytest_result_survives_only_project_version_change(checkout, monkeypatch):
    release_metadata(checkout, "0.4.11")
    sample = checkout / "tests/test_version_independent.py"
    sample.write_text("def test_behavior():\n    assert 2 + 2 == 4\n")
    monkeypatch.setattr(
        checks,
        "command",
        lambda *a, **k: [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            str(sample),
            "--junitxml=core-junit.xml",
        ],
    )
    out = checkout / "result.json"
    original, code = checks.execute(["tests"], root=checkout, output=out)
    assert code == 0 and original["checks"][0]["counts"]["passed"] == 1
    release_metadata(checkout, "0.4.12")
    report, code = checks.execute(
        ["tests"],
        root=checkout,
        output=out,
        reuse=True,
        run=lambda *a, **k: pytest.fail("Do not rerun already completed real pytest"),
    )
    assert code == 0 and report["checks"][0]["reused"]
    assert report["checks"][0]["counts"] == original["checks"][0]["counts"]


@pytest.mark.parametrize(
    "mutation",
    ["source", "dependency", "lock_dependency", "partial_version", "configuration"],
)
def test_version_reuse_never_masks_other_changes(checkout, mutation):
    release_metadata(checkout, "0.4.11")
    before, _ = checks.snapshot(checkout, "pytest")
    release_metadata(checkout, "0.4.12")
    if mutation == "source":
        (checkout / "tests/changed.py").write_text("# new input\n")
    else:
        path = checkout / (
            "uv.lock" if mutation == "lock_dependency" else "pyproject.toml"
        )
        text = path.read_text()
        if mutation == "dependency":
            text = text.replace("example==1.0.0", "example==2.0.0")
        elif mutation == "lock_dependency":
            text = text.replace('version = "1.0.0"', 'version = "2.0.0"')
        elif mutation == "partial_version":
            text = text.replace("0.4.12", "0.4.13")
        else:
            text += '\n[tool.pytest.ini_options]\naddopts = "-x"\n'
        path.write_text(text)
    after, _ = checks.snapshot(checkout, "pytest")
    assert before != after
    if mutation == "partial_version":
        assert after["version_neutral_metadata"] == []


def test_partial_report_reuses_only_its_valid_selection(checkout):
    out = checkout / "result.json"
    checks.execute(["ruff"], root=checkout, run=fake_process(), output=out, environ={})
    report, code = checks.execute(
        ["pr"], root=checkout, run=fake_process(), output=out, environ={}, reuse=True
    )
    assert code == 0
    assert [entry["name"] for entry in report["checks"] if entry["reused"]] == [
        "ruff-lint",
        "ruff-format",
    ]
    assert len(report["checks"]) == len(checks.PR_CHECKS)


@pytest.mark.parametrize("original_code", [1, 7, -15])
def test_matching_failure_is_preserved_without_automatic_retry(checkout, original_code):
    out = checkout / "result.json"
    _, expected = checks.execute(
        ["version"],
        root=checkout,
        run=fake_process(original_code),
        output=out,
        environ={},
    )
    report, code = checks.execute(
        ["version"],
        root=checkout,
        run=lambda *a, **k: pytest.fail("Unchanged failure must not be retried"),
        output=out,
        environ={},
        reuse=True,
    )
    assert code == expected
    assert report["checks"][0]["exit_code"] == original_code
    assert report["checks"][0]["status"] == "failed"
    assert report["checks"][0]["reused"]


def test_matching_incomplete_execution_is_reused_and_does_not_admit_pass(
    checkout, monkeypatch
):
    monkeypatch.setattr(
        checks,
        "pytest_counts",
        lambda *args: {
            "tests": 2,
            "passed": 1,
            "skipped": 1,
            "failures": 0,
            "errors": 0,
        },
    )
    out = checkout / "result.json"
    checks.execute(["tests"], root=checkout, run=fake_process(), output=out, environ={})
    report, code = checks.execute(
        ["pr"], root=checkout, run=fake_process(), output=out, environ={}, reuse=True
    )
    entry = next(item for item in report["checks"] if item["name"] == "pytest")
    assert code == 3 and entry["reused"] and not entry["executed_this_invocation"]
    assert entry["execution_status"] == "passed" and entry["exit_code"] == 0
    assert (
        entry["completeness"] == "incomplete" and not entry["passed_for_current_input"]
    )


@pytest.mark.parametrize(
    "change", ["tool", "environment", "platform", "parameters", "pipeline"]
)
def test_changed_execution_identity_forces_refresh(checkout, monkeypatch, change):
    out = checkout / "result.json"
    checks.execute(
        ["version"], root=checkout, run=fake_process(), output=out, environ={}
    )
    environ, coverage = {}, False
    if change == "tool":
        monkeypatch.setattr(
            checks, "preflight", lambda *args: {"python": {"version": "3.14.4"}}
        )
    elif change == "environment":
        environ = {"PYTHONHASHSEED": "1"}
    elif change == "platform":
        monkeypatch.setattr(checks.platform, "system", lambda: "OtherOS")
    elif change == "parameters":
        coverage = True
    else:
        path = checkout / "config/pipeline.toml"
        path.parent.mkdir()
        path.write_text("# new definition\n")
    report, code = checks.execute(
        ["version"],
        root=checkout,
        run=fake_process(),
        output=out,
        environ=environ,
        coverage=coverage,
        reuse=True,
    )
    assert code == 0
    assert report["checks"][0]["executed_this_invocation"]
    assert not report["checks"][0]["reused"]


def test_later_check_cannot_mutate_a_reused_pass_and_admit_it(checkout):
    out = checkout / "result.json"
    checks.execute(["ruff"], root=checkout, run=fake_process(), output=out, environ={})

    def mutate(*args):
        (checkout / "src/engineering_ci/checks.py").write_text(
            "# changed after reuse\n"
        )

    report, code = checks.execute(
        ["pr"],
        root=checkout,
        run=fake_process(action=mutate),
        output=out,
        environ={},
        reuse=True,
    )
    entry = report["checks"][0]
    assert code == 3 and entry["reused"] and entry["status"] == "stale"
    assert not entry["input_stable"] and not entry["passed_for_current_input"]


def test_explicit_check_without_reuse_always_runs(checkout):
    out = checkout / "result.json"
    checks.execute(
        ["version"], root=checkout, run=fake_process(), output=out, environ={}
    )
    report, code = checks.execute(
        ["version"], root=checkout, run=fake_process(1), output=out, environ={}
    )
    assert code == 1 and report["checks"][0]["executed_this_invocation"]
    assert not report["checks"][0]["reused"]


def test_failure_does_not_skip_other_requested_checks(checkout):
    report, code = checks.execute(
        ["ruff", "version"], root=checkout, run=fake_process(4), environ={}
    )
    assert code == 4
    assert all(
        entry["executed"] and entry["status"] == "failed" for entry in report["checks"]
    )


def test_missing_tool_is_not_executed_and_saves_results(checkout, monkeypatch, capsys):
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
    assert "[ruff-lint] uv is required" in capsys.readouterr().out
    result, status = checks.inspect(out, root=checkout, environ={})
    assert status == 3
    assert all(entry["reason"] == "uv is required" for entry in result["checks"])


def test_real_missing_executable_is_not_recorded_as_executed(checkout, monkeypatch):
    missing = checkout / "missing-check-tool"
    monkeypatch.setattr(checks, "command", lambda *args, **kwargs: [str(missing)])
    out = checkout / "result.json"
    report, code = checks.execute(["version"], root=checkout, output=out, environ={})
    entry = report["checks"][0]
    assert code == 127 and report["exit_code"] == 127
    assert entry["status"] == "missing_tool"
    assert entry["executed"] is False and entry["exit_code"] is None
    assert entry["passed_for_current_input"] is False
    assert json.loads(out.read_text()) == report


def test_launch_permission_error_is_not_recorded_as_executed(checkout):
    def denied(*args, **kwargs):
        raise PermissionError("Cannot launch the check")

    out = checkout / "result.json"
    report, code = checks.execute(
        ["version"], root=checkout, run=denied, output=out, environ={}
    )
    entry = report["checks"][0]
    assert code == 3 and entry["status"] == "environment_error"
    assert entry["executed"] is False and entry["exit_code"] is None
    assert entry["passed_for_current_input"] is False
    assert json.loads(out.read_text()) == report


@pytest.mark.parametrize("raw_code, expected", [(1, 1), (7, 7), (-15, 143)])
def test_postflight_error_preserves_completed_check_exit(
    checkout, monkeypatch, raw_code, expected
):
    attempts = 0

    def changed_environment(*args):
        nonlocal attempts
        attempts += 1
        if attempts > 1:
            raise checks.PreconditionError("missing_tool", "Tool disappeared")
        return {"python": {"version": "3.14.3"}}

    monkeypatch.setattr(checks, "preflight", changed_environment)
    out = checkout / "result.json"
    report, code = checks.execute(
        ["version"], root=checkout, run=fake_process(raw_code), output=out, environ={}
    )
    entry = report["checks"][0]
    assert code == expected and report["exit_code"] == expected
    assert entry["executed"] is True and entry["exit_code"] == raw_code
    assert entry["status"] == "failed" and report["overall_status"] == "failed"
    assert entry["passed_for_current_input"] is False
    assert entry["reason"] == "Tool disappeared"
    assert json.loads(out.read_text()) == report


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


def test_pytest_allows_absent_optional_aria2_and_rejects_selection_overrides(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        checks, "tool", lambda *args: {"executable": "uv", "version": checks.UV_VERSION}
    )
    monkeypatch.setattr(
        checks,
        "probe",
        lambda *args: json.dumps(
            {"python": "3.14.8", "pytest": "9.0.3", "pytest-cov": "7.0.0"}
        ),
    )
    tools = checks.preflight(
        "pytest",
        tmp_path,
        None,
        lambda name: None if name in ("aria2c", str(tmp_path)) else sys.executable,
        {"ARIA2C": str(tmp_path)},
    )
    assert tools["aria2c"] == {"status": "not_enabled"}
    assert tools["python"]["version"] == "3.14.8"
    with pytest.raises(checks.PreconditionError, match="PYTEST_ADDOPTS"):
        checks.preflight(
            "pytest",
            tmp_path,
            None,
            lambda _: "uv",
            {"PYTEST_ADDOPTS": "--ignore=tests"},
        )


def test_optional_aria2_uses_existing_local_config(tmp_path):
    executable = tmp_path / "tools/aria2c.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"fixture")
    config = tmp_path / "config/ghwf.local.toml"
    config.parent.mkdir()
    config.write_text('[download]\nbackend="auto"\naria2c_path="tools/aria2c.exe"\n')
    assert checks.aria2_executable(
        tmp_path, environ={}, which=lambda path: path
    ) == str(executable)
    executable.unlink()
    assert (
        checks.aria2_executable(tmp_path, environ={}, which=lambda path: path) is None
    )


@pytest.mark.parametrize(
    "file",
    [
        "tests/uncommitted.py",
        "pyproject.toml",
        "uv.lock",
        "src/engineering_ci/checks.py",
        ".github/workflows/release.yml",
        ".dagger/pyproject.toml",
        "docs/architecture/release/manifest.schema.json",
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
    source = checkout / "src/engineering_ci/release/version.py"
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


@pytest.mark.parametrize("required_skip", [False, True])
def test_real_pytest_only_explicit_optional_aria2_skips_do_not_block_core(
    checkout, monkeypatch, required_skip
):
    configuration = checkout / "pytest.ini"
    configuration.write_text("[pytest]\n", encoding="utf-8")
    sample = checkout / "tests/test_optional.py"
    sample.write_text(
        "import pytest\n"
        "def test_core():\n    assert 1 == 1\n"
        "def test_optional(request):\n"
        "    request.node.user_properties.append(('engineering_ci.optional_backend', 'aria2'))\n"
        "    pytest.skip('Optional aria2 unavailable')\n"
        "def test_required():\n"
        + (
            "    pytest.skip('Required environment unavailable')\n"
            if required_skip
            else "    assert True\n"
        )
    )
    monkeypatch.setattr(
        checks,
        "command",
        lambda *args, **kwargs: [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-c",
            str(configuration),
            str(sample),
            "--junitxml=core-junit.xml",
        ],
    )
    report, code = checks.execute(["tests"], root=checkout)
    entry = report["checks"][0]
    assert code == (3 if required_skip else 0)
    assert entry["status"] == ("incomplete" if required_skip else "passed")
    assert entry["exit_code"] == 0
    assert entry["optional_tests"]["aria2"]["status"] == "not_enabled"
    assert entry["optional_tests"]["aria2"]["tests"] == [
        "tests.test_optional::test_optional"
    ]
    assert entry["passed_for_current_input"] is not required_skip


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
            (checkout / "src/engineering_ci/cli").mkdir(exist_ok=True)
            (checkout / "src/engineering_ci/cli/release.py").write_text(
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
