import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import dagger
from dagger import DefaultPath, check, dag, function, object_type

from .pytest_report import failure_summary, markdown_summary, summarize_pytest
from .pytest_quality import evaluate_quality, pytest_arguments, quality_summary, test_policy


@dataclass(frozen=True)
class _PytestExecution:
    exit_code: int
    report: dict
    output: str
    xml: str
    container: dagger.Container
    policy: dict | None = None
    coverage: dict | None = None


@object_type
class EngineeringCi:
    @function
    def container_echo(self, string_arg: str) -> dagger.Container:
        """Returns a container that echoes whatever string argument is provided"""
        return dag.container().from_("alpine:latest").with_exec(["echo", string_arg])

    @function
    async def grep_dir(self, directory_arg: dagger.Directory, pattern: str) -> str:
        """Returns lines that match a pattern in the files of the provided Directory"""
        return await (
            dag.container()
            .from_("alpine:latest")
            .with_mounted_directory("/mnt", directory_arg)
            .with_workdir("/mnt")
            .with_exec(["grep", "-R", pattern, "."])
            .stdout()
        )

    def _uv_container(
        self,
        source: dagger.Directory,
        python_version: str,
    ) -> dagger.Container:
        """Create an isolated uv environment for a Python project."""

        supported = {"3.10", "3.11", "3.12", "3.13", "3.14"}

        if python_version not in supported:
            raise ValueError(
                f"Unsupported Python version: {python_version}. "
                f"Supported: {sorted(supported)}"
            )

        image = (
            f"ghcr.io/astral-sh/uv:"
            f"python{python_version}-trixie-slim"
        )

        return (
            dag.container()
            .from_(image)
            .with_env_variable("UV_NO_MANAGED_PYTHON", "1")
            .with_env_variable("UV_PYTHON_DOWNLOADS", "0")
            .with_directory(
                "/workspace",
                source,
                exclude=[
                    ".git",
                    ".venv",
                    "**/.venv",
                    "__pycache__",
                    "**/__pycache__",
                    ".ruff_cache",
                    "**/.ruff_cache",
                ],
            )
            .with_workdir("/workspace")
            .with_exec([
                "uv", "sync",
                "--locked",
                "--python", python_version,
            ])
        )

    @function
    async def python_lint(
        self,
        source: dagger.Directory,
        python_version: str,
        lint_path: str = "src",
    ) -> str:
        """Run Ruff against a uv-managed Python project."""

        return await (
            self._uv_container(source, python_version)
            .with_exec([
                "uv", "run", "--no-sync",
                "ruff", "check", lint_path,
            ])
            .stdout()
        )

    @function
    async def python_format_check(
        self,
        source: dagger.Directory,
        python_version: str,
        format_path: str = "src",
    ) -> str:
        """Check Ruff formatting without modifying consumer source files."""

        return await (
            self._uv_container(source, python_version)
            .with_exec([
                "uv", "run", "--no-sync",
                "ruff", "format", "--check", format_path,
            ])
            .stdout()
        )

    async def _run_pytest(
        self,
        source: dagger.Directory,
        python_version: str,
        test_path: str,
        max_failures: int = 10,
        markers: str = "",
        coverage_source: str = "",
        min_coverage: str = "",
    ) -> _PytestExecution:
        """Execute once and collect JUnit evidence even when tests fail."""
        report_path = "/tmp/engineering-ci-pytest.xml"
        if not 1 <= max_failures <= 50:
            raise ValueError("max_failures must be between 1 and 50")
        policy = test_policy(test_path, markers, coverage_source, min_coverage)
        container = self._uv_container(source, python_version).with_exec(
            pytest_arguments(policy, report_path),
            expect=dagger.ReturnType.ANY,
        )
        exit_code = await container.exit_code()
        output = await container.stdout()
        stderr = await container.stderr()
        # JUnit XML is optional when pytest exits before initializing plugins.
        reader = container.with_new_file(
            "/tmp/engineering-ci-pytest-ci.py",
            Path(__file__).with_name("pytest_ci.py").read_text(encoding="utf-8"),
        )
        evidence = json.loads(await reader.with_exec([
            "python", "/tmp/engineering-ci-pytest-ci.py", "read-xml", report_path,
        ]).stdout())
        xml = evidence["xml"]
        report = summarize_pytest(xml, exit_code, output + "\n" + stderr,
                                  max_failures=max_failures)
        if evidence["diagnostic"]:
            report["diagnostic"] = evidence["diagnostic"]
        coverage = None
        if coverage_source:
            reader = reader.with_new_file("/tmp/pytest_quality.py",
                Path(__file__).with_name("pytest_quality.py").read_text(encoding="utf-8"))
            coverage = json.loads(await reader.with_exec([
                "python", "/tmp/engineering-ci-pytest-ci.py", "read-coverage",
                "/tmp/engineering-ci-coverage.xml", "/tmp/engineering-ci-coverage.json",
            ]).stdout())
        if markers or coverage_source:
            report["quality"] = evaluate_quality(policy, exit_code, coverage)
        return _PytestExecution(exit_code, report, output, xml, container,
                                policy if markers or coverage_source else None, coverage)

    def _require_pytest_success(self, execution: _PytestExecution) -> str:
        if execution.exit_code != 0:
            summary = failure_summary(execution.report)
            if execution.report.get("quality"):
                summary += "\n" + quality_summary(execution.report["quality"])
            raise RuntimeError(summary)
        if execution.report.get("quality") and not execution.report["quality"]["gate"]["passed"]:
            raise RuntimeError(quality_summary(execution.report["quality"]))
        return execution.output

    def _pytest_bundle(self, execution: _PytestExecution) -> dagger.Directory:
        directory = (dag.directory()
            .with_new_file("exit-code.txt", str(execution.exit_code) + "\n")
            .with_new_file("report.json", json.dumps(execution.report, ensure_ascii=False, sort_keys=True))
            .with_new_file("summary.md", markdown_summary(execution.report))
            .with_new_file("ci.py", Path(__file__).with_name("pytest_ci.py").read_text(encoding="utf-8")))
        if execution.xml:
            directory = directory.with_new_file("junit.xml", execution.xml)
        if execution.policy:
            directory = (directory.with_new_file("quality-policy.json", json.dumps(execution.policy))
                .with_new_file("pytest_quality.py", Path(__file__).with_name("pytest_quality.py").read_text(encoding="utf-8")))
        if execution.coverage:
            for key, filename in (("xml", "coverage.xml"), ("json", "coverage.json")):
                if execution.coverage[key]:
                    directory = directory.with_new_file(filename, execution.coverage[key])
        return directory

    @function
    async def python_test_artifacts(
        self, source: dagger.Directory, python_version: str,
        test_path: str = "tests", max_failures: int = 10,
        markers: str = "", coverage_source: str = "", min_coverage: str = "",
    ) -> dagger.Directory:
        """Run pytest once; export original status, JSON, XML and summary even on failure.

        CI must use ci.py gate: original exit code plus explicit coverage policy.
        Do not also call python_test on this source.
        """
        execution = await self._run_pytest(source, python_version, test_path, max_failures,
                                          markers, coverage_source, min_coverage)
        return self._pytest_bundle(execution)

    @function
    async def python_test(
        self,
        source: dagger.Directory,
        python_version: str,
        test_path: str = "tests",
        markers: str = "",
        coverage_source: str = "",
        min_coverage: str = "",
    ) -> str:
        """Run pytest; fail CI with a bounded JUnit-based failure summary."""
        execution = await self._run_pytest(source, python_version, test_path,
                                          markers=markers, coverage_source=coverage_source,
                                          min_coverage=min_coverage)
        return self._require_pytest_success(execution)

    @function
    async def python_test_report(
        self,
        source: dagger.Directory,
        python_version: str,
        test_path: str = "tests",
        max_failures: int = 10,
        markers: str = "",
        coverage_source: str = "",
        min_coverage: str = "",
    ) -> str:
        """Return JSON summary for both passing and failing pytest runs.

        This informational function does NOT gate CI. Use python_test for
        failure propagation; report.status and exit_code are explicit pytest facts.
        Optional report.quality contains the separate coverage and overall gate.
        """
        execution = await self._run_pytest(
            source, python_version, test_path, max_failures=max_failures,
            markers=markers, coverage_source=coverage_source, min_coverage=min_coverage,
        )
        return json.dumps(execution.report, ensure_ascii=False, sort_keys=True)

    @function
    @check
    async def fixture_test(
        self,
        source: Annotated[
            dagger.Directory,
            DefaultPath("/tests/fixtures/python-app"),
        ],
    ) -> str:
        """Verify the Python test fixture."""
        execution = await self._run_pytest(
            source=source,
            python_version="3.14",
            test_path="tests",
        )
        report = execution.report
        if (not report["report_available"] or report["status"] != "passed"
                or report["exit_code"] != 0 or report["counts"]["tests"] == 0):
            raise ValueError(f"Unexpected passing test report: {report}")
        return self._require_pytest_success(execution)

    @function
    @check
    async def fixture_lint(
        self,
        source: Annotated[
            dagger.Directory,
            DefaultPath("/tests/fixtures/python-app"),
        ],
    ) -> str:
        """Verify Ruff linting on the Python fixture."""
        return await self.python_lint(
            source=source,
            python_version="3.14",
            lint_path="tests",
        )

    @function
    @check
    async def fixture_format(
        self,
        source: Annotated[
            dagger.Directory,
            DefaultPath("/tests/fixtures/python-app"),
        ],
    ) -> str:
        """Verify that shared Ruff format checks pass on the Python fixture."""
        return await self.python_format_check(
            source=source,
            python_version="3.14",
            format_path="tests",
        )

    @function
    @check
    async def fixture_failure_report(
        self,
        source: Annotated[
            dagger.Directory,
            DefaultPath("/tests/fixtures/python-app"),
        ],
    ) -> str:
        """Verify failing pytest returns structured evidence without hiding failure."""
        failing = source.with_new_file(
            "tests/test_report_negative.py",
            "def test_intentional_failure():\n    assert 1 == 2\n",
        )
        execution = await self._run_pytest(
            source=failing, python_version="3.14", test_path="tests",
        )
        report = execution.report
        if (not report["report_available"] or report["status"] != "failed"
                or report["exit_code"] != 1
                or report["counts"]["failures"] != 1
                or not any(
                    item["category"] == "assertion_failure"
                    for item in report["failures"]
                )):
            raise ValueError(f"Unexpected negative test report: {report}")
        try:
            self._require_pytest_success(execution)
        except RuntimeError as exc:
            summary = str(exc)
            if not all(evidence in summary for evidence in (
                "pytest exit 1", "test_intentional_failure", "assert 1 == 2",
            )):
                raise ValueError(f"Unexpected pytest failure summary: {summary}") from exc
        else:
            raise ValueError("python_test incorrectly accepted the failing pytest fixture")
        return "PASS: JSON reported assertion failure with exit 1; python_test rejected it"

    @function
    async def fixture_pytest_artifacts(
        self, source: Annotated[dagger.Directory, DefaultPath("/tests/fixtures/python-app")],
        scenario: str = "pass",
    ) -> dagger.Directory:
        """Small real CI probes; intentional failures are injected, never committed."""
        if scenario not in ("pass", "assertion", "missing-xml", "invalid-xml", "missing-xml-pass"):
            raise ValueError("Unknown pytest report fixture scenario")
        probe = "from pathlib import Path\n\ndef pytest_sessionstart(session):\n"
        probe += "    p = Path('/tmp/engineering-ci-pytest-invocations')\n"
        probe += "    p.write_text(str(int(p.read_text()) + 1) if p.exists() else '1')\n"
        if scenario != "pass" and scenario != "missing-xml-pass":
            source = source.with_new_file("tests/test_report_negative.py",
                "def test_intentional_failure():\n    assert 1 == 2\n")
        if scenario.startswith("missing-xml") or scenario == "invalid-xml":
            probe += "\nimport atexit\n"
            if scenario == "invalid-xml":
                probe += "atexit.register(lambda: Path('/tmp/engineering-ci-pytest.xml').write_text('<testsuite'))\n"
            else:
                probe += "atexit.register(lambda: Path('/tmp/engineering-ci-pytest.xml').unlink(missing_ok=True))\n"
        source = source.with_new_file("conftest.py", probe)
        execution = await self._run_pytest(source, "3.14", "tests")
        invocations = await execution.container.file("/tmp/engineering-ci-pytest-invocations").contents()
        if invocations != "1":
            raise RuntimeError(f"Pytest executed {invocations} times instead of once")
        return self._pytest_bundle(execution).with_new_file("fixture-invocations.txt", invocations)

    async def _quality_fixture_execution(self, source, missing_dependency_source, scenario):
        scenarios = {"baseline", "coverage-pass", "coverage-low", "coverage-test-fail",
                     "coverage-both-fail", "markers", "markers-empty", "invalid-markers",
                     "coverage-missing-dependency", "coverage-report-missing",
                     "coverage-report-invalid", "coverage-source-missing", "invalid-path",
                     "invalid-threshold"}
        if scenario not in scenarios:
            raise ValueError("Unknown pytest quality fixture scenario")
        coverage_source = "" if scenario == "baseline" else "quality_sample"
        threshold = "" if scenario == "baseline" else "100" if scenario in (
            "coverage-low", "coverage-both-fail") else "50" if scenario.startswith("markers") else "80"
        markers = "fast" if scenario == "markers" else "not fast and not slow" if scenario == "markers-empty" else "fast and (" if scenario == "invalid-markers" else ""
        test_path = "missing-tests" if scenario == "invalid-path" else "tests"
        if scenario == "coverage-missing-dependency":
            source, coverage_source = missing_dependency_source, "tests"
        elif scenario == "coverage-source-missing":
            coverage_source = "missing_source_module"
        elif scenario == "invalid-threshold":
            threshold = "101"
        if scenario in ("coverage-test-fail", "coverage-both-fail"):
            source = source.with_new_file("tests/test_intentional_failure.py",
                "def test_intentional_failure():\n    assert 1 == 2\n")
        probe = '''import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

def pytest_configure(config):
    config.addinivalue_line("markers", "fast: small feedback suite")
    config.addinivalue_line("markers", "slow: full regression example")

def pytest_sessionstart(session):
    global started
    started = time.perf_counter()
    p = Path("/tmp/engineering-ci-pytest-invocations")
    p.write_text(str(int(p.read_text()) + 1) if p.exists() else "1")

def pytest_sessionfinish(session, exitstatus):
    Path("/tmp/engineering-ci-fixture-timing.json").write_text(json.dumps({
        "pytest_session_seconds": time.perf_counter() - started,
        "tests_collected": session.testscollected, "exit_code": int(exitstatus),
    }))
'''
        if scenario in ("coverage-report-missing", "coverage-report-invalid"):
            probe += "\nimport atexit\n"
            if scenario == "coverage-report-missing":
                probe += "atexit.register(lambda: Path('/tmp/engineering-ci-coverage.json').unlink(missing_ok=True))\n"
            else:
                probe += "atexit.register(lambda: Path('/tmp/engineering-ci-coverage.json').write_text('{broken'))\n"
        source = source.with_new_file("conftest.py", probe)
        return await self._run_pytest(source, "3.14", test_path, markers=markers,
                                      coverage_source=coverage_source, min_coverage=threshold)

    @function
    async def fixture_quality_artifacts(
        self, source: Annotated[dagger.Directory, DefaultPath("/tests/fixtures/coverage-app")],
        missing_dependency_source: Annotated[dagger.Directory, DefaultPath("/tests/fixtures/python-app")],
        scenario: str = "coverage-pass",
    ) -> dagger.Directory:
        """Real opt-in quality probes with execution count and session timing evidence."""
        execution = await self._quality_fixture_execution(source, missing_dependency_source, scenario)
        evidence = json.loads(await execution.container.with_exec(["python", "-c",
            "import json; from pathlib import Path; "
            "p=Path('/tmp/engineering-ci-pytest-invocations'); "
            "t=Path('/tmp/engineering-ci-fixture-timing.json'); "
            "print(json.dumps({'count': p.read_text() if p.exists() else '0', "
            "'timing': t.read_text() if t.exists() else '{}'}))",
        ]).stdout())
        expected = "0" if scenario == "coverage-missing-dependency" else "1"
        if evidence["count"] != expected:
            raise RuntimeError(f'Expected {expected} pytest sessions; got {evidence["count"]}')
        return (self._pytest_bundle(execution)
                .with_new_file("fixture-invocations.txt", evidence["count"])
                .with_new_file("fixture-timing.json", evidence["timing"]))

    @function
    @check
    async def fixture_quality_gates(
        self, source: Annotated[dagger.Directory, DefaultPath("/tests/fixtures/coverage-app")],
        missing_dependency_source: Annotated[dagger.Directory, DefaultPath("/tests/fixtures/python-app")],
    ) -> str:
        """Verify real pytest and coverage failures independently, without duplicate suite runs."""
        results = []
        for scenario in ("baseline", "coverage-pass", "coverage-low", "coverage-test-fail",
                         "coverage-both-fail", "markers", "markers-empty", "invalid-markers",
                         "coverage-missing-dependency", "coverage-report-missing",
                         "coverage-report-invalid", "coverage-source-missing", "invalid-path"):
            execution = await self._quality_fixture_execution(source, missing_dependency_source, scenario)
            should_pass = scenario in ("baseline", "coverage-pass", "markers")
            try:
                self._require_pytest_success(execution)
            except RuntimeError:
                if should_pass:
                    raise
            else:
                if not should_pass:
                    raise RuntimeError(f"Quality fixture unexpectedly passed: {scenario}")
            quality = execution.report.get("quality")
            if scenario == "coverage-low" and (execution.exit_code != 0
                    or execution.report["counts"]["failures"] != 0
                    or quality["coverage"]["status"] != "failed"):
                raise RuntimeError("Coverage failure changed pytest execution facts")
            if scenario == "coverage-test-fail" and (execution.exit_code != 1
                    or quality["coverage"]["status"] != "passed"):
                raise RuntimeError("Passing coverage hid a test failure")
            if scenario == "markers" and execution.report["counts"]["tests"] != 1:
                raise RuntimeError("Explicit marker selection did not select one test")
            results.append({"scenario": scenario, "exit_code": execution.exit_code,
                            "quality": quality, "counts": execution.report["counts"]})
        try:
            await self._quality_fixture_execution(source, missing_dependency_source, "invalid-threshold")
        except ValueError:
            pass
        else:
            raise RuntimeError("Invalid threshold was accepted")
        return json.dumps(results, sort_keys=True)

