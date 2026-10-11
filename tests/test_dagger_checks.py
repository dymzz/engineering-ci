"""Mock transport contracts; real Linux execution is verified separately with Dagger."""

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from engineering_ci import checks, checks_container, dagger_checks

LINUX = {"platform": "Linux", "machine": "x86_64", "configuration_sha256": "a" * 64}
TOOLS = {"python": {"version": "3.14.8"}}


@pytest.fixture
def source(tmp_path, monkeypatch):
    for prefix in ("", ".dagger/"):
        project = tmp_path / (prefix + "pyproject.toml")
        project.parent.mkdir(parents=True, exist_ok=True)
        project.write_text('[project]\nname = "engineering-ci"\nversion = "0.5.0"\n')
        (tmp_path / (prefix + "uv.lock")).write_text(
            '[[package]]\nname = "engineering-ci"\nversion = "0.5.0"\n'
        )
    (tmp_path / "dagger.json").write_text('{"engineVersion":"v1.0.0-beta.16"}')
    (tmp_path / "dagger.lock").write_text("# Explicit Dagger lock input\n")
    parser = tmp_path / ".dagger/src/engineering_ci/pytest_report.py"
    parser.parent.mkdir(parents=True)
    shutil.copyfile(checks.ROOT / ".dagger/src/engineering_ci/pytest_report.py", parser)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_sample.py").write_text(
        "def test_sample():\n    assert True\n"
    )
    monkeypatch.setattr(checks, "preflight", lambda *args: copy.deepcopy(TOOLS))
    return tmp_path


class Dagger:
    def __init__(self, root, monkeypatch):
        self.root, self.monkeypatch = root, monkeypatch
        self.exports = []
        self.probes = 0
        self.exit = 0
        self.skipped = False
        self.runtime = {
            "engine_version": "v1.0.0-beta.16+2d95ef98",
            "container_sha256": "b" * 64,
        }
        self.action = lambda: None
        self.fail_export = False
        self.bad_binding = False
        self.xml_marker = 0

    def which(self, name):
        assert name == "dagger"
        return sys.executable

    def environment(self, names):
        inputs = {
            name: checks.snapshot(
                self.root, name, portable=True, omit=dagger_checks.OMITTED_INPUTS
            )[0]
            for name in names
        }
        if self.bad_binding:
            inputs[names[0]]["sha256"] = "0" * 64
        return {
            "schema_version": 1,
            "runtime": self.runtime,
            "environment": LINUX,
            "tools": {name: copy.deepcopy(TOOLS) for name in names},
            "source_inputs": inputs,
        }

    def run(self, args, **kwargs):
        if args == [sys.executable, "version"]:
            assert kwargs["env"]["FORCE_DARK_MODE"]
            assert kwargs["timeout"] == 30
            return subprocess.CompletedProcess(args, 0, "version: v1.0.0-beta.16\n", "")
        if args[:2] == ["git", "ls-files"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        requested = json.loads(args[args.index("--request") + 1])
        assert args[args.index("--dagger-lock") + 1] == "dagger.lock"
        names = checks.selected(requested["selectors"])
        if "repository-check-environment" in args:
            self.probes += 1
            return subprocess.CompletedProcess(
                args, 0, json.dumps(self.environment(names)), ""
            )
        assert "repository-check-artifacts" in args
        self.exports.append(names)
        if self.fail_export:
            return subprocess.CompletedProcess(args, 1)
        out = Path(args[-1])

        def native(command, **native_kwargs):
            if "pytest" in command:
                skip = (
                    '<skipped message="required Bash unavailable"/>'
                    if self.skipped
                    else ""
                )
                failure = (
                    '<failure message="assert 1 == 2">E   assert 1 == 2</failure>'
                    if self.exit
                    else ""
                )
                xml = f'<testsuite tests="2" failures="{int(bool(self.exit))}" errors="0" skipped="{int(self.skipped)}" timestamp="run-{self.xml_marker}"><testcase name="one">{skip}{failure}</testcase><testcase name="two"/></testsuite>'
                (self.root / requested["junit"]).write_text(xml)
            return subprocess.CompletedProcess(
                command, self.exit if "pytest" in command else 0
            )

        with self.monkeypatch.context() as context:
            context.setattr(
                checks, "environment_identity", lambda env: copy.deepcopy(LINUX)
            )
            report, code = checks.execute(
                names,
                root=self.root,
                coverage=requested["coverage"],
                junit=requested["junit"],
                run=native,
                environ={},
            )
        checks.atomic_json(out / "container-report.json", report)
        checks.atomic_json(out / "environment.json", self.environment(names))
        if "pytest" in names:
            shutil.copyfile(self.root / requested["junit"], out / "core-junit.xml")
        (out / "exit-code.txt").write_text(str(code))
        self.action()
        return subprocess.CompletedProcess(args, 0)


@pytest.fixture
def backend(source, monkeypatch):
    return Dagger(source, monkeypatch)


def execute(backend, selectors=None, **options):
    return dagger_checks.execute(
        selectors or ["pr"],
        root=backend.root,
        output=backend.root / "result.json",
        run=backend.run,
        which=backend.which,
        environ={},
        **options,
    )


def inspect(backend):
    return checks.inspect(
        backend.root / "result.json",
        root=backend.root,
        run=backend.run,
        which=backend.which,
        environ={},
    )


def test_linux_evidence_binds_the_original_checkout_and_exports_once(backend):
    report, code = execute(backend)
    assert code == 0 and backend.exports == [list(checks.PR_CHECKS)]
    assert report["environment"]["platform"] == "Linux"
    assert all(
        entry["identity"]["root"] == str(backend.root) for entry in report["checks"]
    )
    assert inspect(backend)[1] == 0
    assert backend.exports == [list(checks.PR_CHECKS)]


@pytest.mark.parametrize("failure,skip,expected", [(1, False, 1), (0, True, 3)])
def test_raw_failure_and_required_skip_never_admit_delivery(
    backend, failure, skip, expected
):
    backend.exit, backend.skipped = failure, skip
    report, code = execute(backend)
    entry = next(item for item in report["checks"] if item["name"] == "pytest")
    assert code == expected and entry["exit_code"] == failure
    assert entry["status"] == ("failed" if failure else "incomplete")
    assert not entry["passed_for_current_input"]
    assert inspect(backend)[1] == 3
    assert execute(backend, reuse=True)[1] == expected
    assert len(backend.exports) == 1


def test_matching_evidence_reuses_without_another_pytest(backend):
    execute(backend)
    report, code = execute(backend, reuse=True)
    assert code == 0 and len(backend.exports) == 1
    assert all(
        item["reused"] and not item["executed_this_invocation"]
        for item in report["checks"]
    )


def test_new_export_does_not_overwrite_an_earlier_execution_bundle(backend):
    first, _ = execute(backend)
    entry = next(item for item in first["checks"] if item["name"] == "pytest")
    old_path = backend.root / entry["evidence"]["core-junit.xml"]["path"]
    original = old_path.read_bytes()
    backend.xml_marker += 1
    second, code = execute(backend)
    new_entry = next(item for item in second["checks"] if item["name"] == "pytest")
    assert code == 0 and entry["evidence"] != new_entry["evidence"]
    assert old_path.read_bytes() == original
    dagger_checks.read_evidence(entry, backend.root)


def test_signal_exit_is_exportable_without_changing_the_original_failure(
    source, monkeypatch
):
    monkeypatch.setattr(checks, "ROOT", source)
    monkeypatch.setattr(
        checks_container, "prepare", lambda *args: {"runtime": {}, "tools": {}}
    )

    def execute_signal(*args, **kwargs):
        report = {
            "checks": [{"name": "pytest", "status": "failed", "exit_code": -15}],
            "exit_code": 143,
        }
        checks.atomic_json(kwargs["output"], report)
        return report, 143

    monkeypatch.setattr(checks, "execute", execute_signal)
    destination = source / "output"
    result = checks_container.main(
        [
            "execute",
            "--request",
            json.dumps(
                {"selectors": ["tests"], "coverage": False, "junit": "core-junit.xml"}
            ),
            "--runtime",
            "{}",
        ],
        output=destination,
    )
    assert result == 1
    assert (destination / "exit-code.txt").read_text() == "143"
    assert (
        json.loads((destination / "container-report.json").read_text())["checks"][0][
            "exit_code"
        ]
        == -15
    )


def test_coherent_release_version_change_keeps_pytest_evidence(backend):
    execute(backend)
    for relative in checks.PIPELINE["inputs"]["release_metadata"]:
        path = backend.root / relative
        path.write_text(path.read_text().replace("0.5.0", "0.5.1"))
    report, code = execute(backend, reuse=True)
    assert code == 0
    assert backend.exports[-1] == ["version", "lock"]
    assert [entry["name"] for entry in report["checks"] if entry["reused"]] == [
        "ruff-lint",
        "ruff-format",
        "pytest",
    ]


def test_changed_source_expires_the_related_evidence(backend):
    execute(backend)
    (backend.root / "tests/test_sample.py").write_text(
        "def test_changed():\n    assert True\n"
    )
    assert inspect(backend)[1] == 3
    report, code = execute(backend, reuse=True)
    assert code == 0 and backend.exports[-1] == ["pytest"]
    assert not next(entry for entry in report["checks"] if entry["name"] == "pytest")[
        "reused"
    ]


def test_engine_or_tool_environment_change_expires_evidence(backend):
    execute(backend)
    backend.runtime["container_sha256"] = "c" * 64
    assert inspect(backend)[1] == 3
    assert execute(backend, reuse=True)[1] == 0
    assert backend.exports[-1] == list(checks.PR_CHECKS)


def test_dagger_lock_remains_an_input_even_when_directory_import_omits_it(backend):
    execute(backend)
    (backend.root / "dagger.lock").write_text("# Changed pinned execution input\n")
    assert inspect(backend)[1] == 3
    assert execute(backend, reuse=True)[1] == 0
    assert backend.exports[-1] == ["pytest"]


def test_executor_implementation_changes_expire_all_checks(backend):
    execute(backend)
    path = backend.root / ".dagger/src/engineering_ci/main.py"
    path.write_text("# Changed Dagger interface\n")
    assert inspect(backend)[1] == 3
    assert execute(backend, reuse=True)[1] == 0
    assert backend.exports[-1] == list(checks.PR_CHECKS)


def test_inspection_reports_the_actual_missing_executor(backend):
    execute(backend)
    report, code = checks.inspect(
        backend.root / "result.json",
        root=backend.root,
        run=backend.run,
        which=lambda name: None,
        environ={},
    )
    assert code == 3
    assert all(
        "Dagger is required" in entry["invalidation_reason"]
        for entry in report["checks"]
    )


@pytest.mark.parametrize("executor", [False, "dagger", {}, {"backend": "unknown"}])
def test_invalid_report_executor_is_rejected_before_operations(backend, executor):
    report, _ = execute(backend)
    report["executor"] = executor
    checks.atomic_json(backend.root / "result.json", report)
    with pytest.raises(ValueError, match="executor"):
        inspect(backend)


def test_host_credentials_do_not_enter_local_check_json(backend):
    report, code = dagger_checks.execute(
        ["pr"],
        root=backend.root,
        output=backend.root / "result.json",
        run=backend.run,
        which=backend.which,
        environ={
            "GH_TOKEN": "SECRET_TEST_TOKEN",
            "UV_INDEX_URL": "https://user:SECRET_INDEX@example.invalid",
        },
    )
    assert code == 0 and report["overall_status"] == "passed"
    assert "SECRET" not in (backend.root / "result.json").read_text()


def test_change_during_execution_is_stale_even_if_restored(backend):
    path = backend.root / "tests/test_sample.py"
    before = path.read_bytes()
    backend.action = lambda: (path.write_bytes(b"# changed"), path.write_bytes(before))
    report, code = execute(backend)
    assert code == 3
    entry = next(item for item in report["checks"] if item["name"] == "pytest")
    assert entry["status"] == "stale" and not entry["passed_for_current_input"]


@pytest.mark.parametrize(
    "field", ["core-junit.xml", "container-report.json", "environment.json"]
)
def test_corrupt_or_missing_export_never_remains_a_pass(backend, field):
    report, _ = execute(backend)
    entry = next(item for item in report["checks"] if item["name"] == "pytest")
    (backend.root / entry["evidence"][field]["path"]).write_text("damaged")
    assert inspect(backend)[1] == 3


def test_missing_tool_does_not_fall_back_or_claim_execution(backend):
    report, code = dagger_checks.execute(
        ["pr"],
        root=backend.root,
        output=backend.root / "result.json",
        which=lambda name: None,
        run=backend.run,
        environ={},
    )
    assert code == 127 and backend.exports == []
    assert all(
        entry["status"] == "missing_tool" and not entry["executed"]
        for entry in report["checks"]
    )


def test_version_probe_timeout_preserves_not_executed_results(backend):
    def timeout(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    report, code = dagger_checks.execute(
        ["pr"], root=backend.root, run=timeout, which=backend.which, environ={}
    )
    assert code == 3 and not backend.exports
    assert all(
        entry["status"] == "environment_error"
        and not entry["executed"]
        and "timed out" in entry["reason"]
        for entry in report["checks"]
    )


def test_export_failure_saves_non_success_and_no_raw_credentials(backend):
    backend.fail_export = True
    report, code = execute(backend)
    assert code == 3 and report["overall_status"] == "failed"
    assert all(not entry["passed_for_current_input"] for entry in report["checks"])
    assert "SECRET" not in (backend.root / "result.json").read_text()


def test_different_container_source_is_rejected_before_execution(backend):
    backend.bad_binding = True
    report, code = execute(backend)
    assert code == 3 and not backend.exports
    assert all(entry["status"] == "stale" for entry in report["checks"])


def test_explicit_native_and_dagger_use_the_shared_selection(monkeypatch):
    calls = []
    monkeypatch.setattr(
        checks,
        "execute",
        lambda selectors, **kw: calls.append(("native", selectors, kw)) or ({}, 7),
    )
    monkeypatch.setattr(
        dagger_checks,
        "execute",
        lambda selectors, **kw: calls.append(("dagger", selectors, kw)) or ({}, 1),
    )
    assert checks.run_checks(["tests"], executor="native", coverage=True)[1] == 7
    assert checks.run_checks(["tests"], executor="dagger", coverage=True)[1] == 1
    assert calls == [
        ("native", ["tests"], {"coverage": True}),
        ("dagger", ["tests"], {"coverage": True}),
    ]


def test_default_dagger_check_list_does_not_repeat_repository_pytest():
    text = (checks.ROOT / ".dagger/src/engineering_ci/main.py").read_text()
    for name in ("repository_check_environment", "repository_check_artifacts"):
        before = text.split(f"async def {name}(")[0].rsplit("\n\n", 1)[-1]
        assert "@check" not in before


@pytest.mark.parametrize(
    "junit", ["../outside.xml", "/tmp/out.xml", "D:/outside.xml", "..\\outside.xml"]
)
def test_invalid_report_path_is_rejected_before_any_operation(backend, junit):
    with pytest.raises(ValueError):
        execute(backend, junit=junit)
    assert not backend.exports and backend.probes == 0
