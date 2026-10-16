import asyncio
import json
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import dagger
from dagger import DefaultPath, Ignore, check, dag, function, object_type

from . import pr_checks
from .python_dependency_auth import prepare_private_git, sync_dependencies as sync_python_dependencies

from .pytest_matrix import (
    SUPPORTED_PYTHONS,
    aggregate,
    matrix_policy,
    matrix_summary,
    supports_python,
    version_result,
)
from .pytest_quality import (
    evaluate_quality,
    pytest_arguments,
    quality_summary,
    test_policy,
)
from .pytest_report import failure_summary, markdown_summary, summarize_pytest


@dataclass(frozen=True)
class _PytestExecution:
    exit_code: int
    report: dict
    output: str
    xml: str
    container: dagger.Container
    policy: dict | None = None
    coverage: dict | None = None
    runtime: dict | None = None


@dataclass(frozen=True)
class _MatrixExecution:
    plan: dict
    report: dict
    rows: list


@object_type
class EngineeringCi:
    @function
    async def repository_check_environment(
        self, source: Annotated[dagger.Directory, DefaultPath("/"), Ignore(pr_checks.SOURCE_EXCLUDES)],
        request: str, dagger_lock: Annotated[dagger.File, DefaultPath("/dagger.lock")], executable_paths: str = "[]",
        git_token: dagger.Secret | None = None,
    ) -> str:
        """Inspect actual Linux tools and inputs without executing repository checks."""
        return await pr_checks.environment(source.with_file("dagger.lock", dagger_lock), request, executable_paths, git_token)

    @function
    async def repository_check_artifacts(
        self, source: Annotated[dagger.Directory, DefaultPath("/"), Ignore(pr_checks.SOURCE_EXCLUDES)],
        request: str, dagger_lock: Annotated[dagger.File, DefaultPath("/dagger.lock")], executable_paths: str = "[]",
        git_token: dagger.Secret | None = None,
    ) -> dagger.Directory:
        """Export one repository check execution, including failures and original exits."""
        return await pr_checks.artifacts(source.with_file("dagger.lock", dagger_lock), request, executable_paths, git_token)

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
        sync_dependencies: bool = True,
        git_token: dagger.Secret | None = None,
    ) -> dagger.Container:
        """Create an isolated uv environment for a Python project."""

        supported = set(SUPPORTED_PYTHONS)

        if python_version not in supported:
            raise ValueError(
                f"Unsupported Python version: {python_version}. "
                f"Supported: {sorted(supported)}"
            )

        image = (
            f"ghcr.io/astral-sh/uv:"
            f"python{python_version}-trixie-slim"
        )

        base = dag.container().from_(image)
        if git_token is not None:
            base = prepare_private_git(base)
        container = (
            base.with_env_variable("UV_NO_MANAGED_PYTHON", "1")
            .with_env_variable("UV_PYTHON_DOWNLOADS", "0")
            .with_mounted_cache(
                "/root/.cache/engineering-ci-uv",
                dag.cache_volume(f"engineering-ci-uv-v1-python{python_version}"),
            )
            .with_new_file(
                "/tmp/engineering-ci-uv-sync.py",
                Path(__file__).with_name("uv_sync.py").read_text(encoding="utf-8"),
            )
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
        )
        return (sync_python_dependencies(container, python_version, git_token)
                if sync_dependencies else container)

    @function
    async def python_lint(
        self,
        source: dagger.Directory,
        python_version: str,
        lint_path: str = "src",
        git_token: dagger.Secret | None = None,
    ) -> str:
        """Run Ruff against a uv-managed Python project."""

        return await (
            self._uv_container(source, python_version, git_token=git_token)
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
        git_token: dagger.Secret | None = None,
    ) -> str:
        """Check Ruff formatting without modifying consumer source files."""

        return await (
            self._uv_container(source, python_version, git_token=git_token)
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
        _prepared_container: dagger.Container | None = None,
        _matrix_runtime: bool = False,
        git_token: dagger.Secret | None = None,
    ) -> _PytestExecution:
        """Execute once and collect JUnit evidence even when tests fail."""
        report_path = "/tmp/engineering-ci-pytest.xml"
        if not 1 <= max_failures <= 50:
            raise ValueError("max_failures must be between 1 and 50")
        policy = test_policy(test_path, markers, coverage_source, min_coverage)
        prepared = _prepared_container if _prepared_container is not None else self._uv_container(source, python_version, git_token=git_token)
        arguments = pytest_arguments(policy, report_path)
        if _matrix_runtime:
            prepared = prepared.with_new_file("/tmp/engineering-ci-matrix-runtime.py",
                Path(__file__).with_name("pytest_matrix_runtime.py").read_text(encoding="utf-8"))
            arguments = ["uv", "run", "--no-sync", "python", "/tmp/engineering-ci-matrix-runtime.py",
                         python_version, *arguments[4:]]
        container = prepared.with_exec(
            arguments,
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
        runtime = None
        if _matrix_runtime:
            runtime = json.loads(await reader.with_exec([
                "python", "/tmp/engineering-ci-pytest-ci.py", "read-runtime",
                "/tmp/engineering-ci-pytest-runtime.json",
            ]).stdout())
        return _PytestExecution(exit_code, report, output, xml, container,
                                policy if markers or coverage_source else None, coverage, runtime)

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
        git_token: dagger.Secret | None = None,
    ) -> dagger.Directory:
        """Run pytest once; export original status, JSON, XML and summary even on failure.

        CI must use ci.py gate: original exit code plus explicit coverage policy.
        Do not also call python_test on this source.
        """
        execution = await self._run_pytest(source, python_version, test_path, max_failures,
                                          markers, coverage_source, min_coverage, git_token=git_token)
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
        git_token: dagger.Secret | None = None,
    ) -> str:
        """Run pytest; fail CI with a bounded JUnit-based failure summary."""
        execution = await self._run_pytest(source, python_version, test_path,
                                          markers=markers, coverage_source=coverage_source,
                                          min_coverage=min_coverage, git_token=git_token)
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
        git_token: dagger.Secret | None = None,
    ) -> str:
        """Return JSON summary for both passing and failing pytest runs.

        This informational function does NOT gate CI. Use python_test for
        failure propagation; report.status and exit_code are explicit pytest facts.
        Optional report.quality contains the separate coverage and overall gate.
        """
        execution = await self._run_pytest(
            source, python_version, test_path, max_failures=max_failures,
            markers=markers, coverage_source=coverage_source, min_coverage=min_coverage, git_token=git_token,
        )
        return json.dumps(execution.report, ensure_ascii=False, sort_keys=True)

    async def _run_pytest_matrix(self, source, python_versions, test_path="tests", max_failures=10,
                                 markers="", coverage_source="", min_coverage="", max_concurrency=1):
        policy = test_policy(test_path, markers, coverage_source, min_coverage)
        plan = matrix_policy(python_versions, max_concurrency, max_failures, policy)
        try:
            project_text = await source.file("pyproject.toml").contents()
            if len(project_text) > 1024 * 1024:
                raise ValueError("pyproject.toml exceeds size limit")
            requirement = tomllib.loads(project_text).get("project", {}).get("requires-python", "")
            supports_python(requirement, "3.14.0")  # validate specifier before any container work
        except Exception as exc:  # noqa: BLE001 - configuration errors remain explicit failing matrix rows
            rows = [(v, {"phase": "configuration", "failure_category": "configuration_error",
                         "diagnostic": f"Cannot read project.requires-python ({type(exc).__name__})"}, {}, None)
                    for v in plan["requested_versions"]]
            return _MatrixExecution(plan, aggregate(plan, [version_result(*r[:3]) for r in rows]), rows)

        semaphore = asyncio.Semaphore(max_concurrency)

        async def run_version(version):
            async with semaphore:
                started = time.perf_counter()
                facts = {"schema_version": 1, "phase": "environment", "process_exit_code": None,
                         "failure_category": None, "requires_python": requirement}
                runtime, execution = {}, None
                try:
                    base = self._uv_container(source, version, sync_dependencies=False).with_new_file(
                        "/tmp/engineering-ci-matrix-runtime.py",
                        Path(__file__).with_name("pytest_matrix_runtime.py").read_text(encoding="utf-8"))
                    runtime = json.loads(await base.with_exec([
                        "python", "/tmp/engineering-ci-matrix-runtime.py", "--probe"]).stdout())
                    if runtime["actual_python"].split(".")[:2] != version.split("."):
                        facts.update(failure_category="interpreter_mismatch", diagnostic="Base interpreter differs from requested version")
                    elif not supports_python(requirement, runtime["actual_python"]):
                        facts.update(phase="compatibility", failure_category="python_unsupported",
                                     diagnostic=f"Python {runtime['actual_python']} does not satisfy requires-python {requirement}")
                    else:
                        facts["validated_python"] = runtime["actual_python"]
                        facts["phase"] = "dependencies"
                        synced = base.with_exec(["python", "/tmp/engineering-ci-uv-sync.py", version],
                                                expect=dagger.ReturnType.ANY)
                        code = await synced.exit_code()
                        if code:
                            facts.update(process_exit_code=code, failure_category="dependency_installation",
                                         diagnostic=f"uv sync --locked failed (exit {code}); pytest was not started")
                        else:
                            facts["phase"] = "pytest"
                            execution = await self._run_pytest(source, version, test_path, max_failures,
                                markers, coverage_source, min_coverage, _prepared_container=synced, _matrix_runtime=True)
                            runtime = execution.runtime or {}
                            facts["process_exit_code"] = execution.exit_code
                except Exception as exc:  # noqa: BLE001 - preserve failed rows and continue other required versions
                    phase = facts["phase"]
                    facts.update(failure_category={"environment": "environment_initialization",
                        "dependencies": "dependency_installation"}.get(phase, "report_unavailable"),
                        diagnostic=f"{phase} failed ({type(exc).__name__}); no automatic retry")
                    if phase == "pytest":
                        facts["phase"] = "collection"
                facts["elapsed_seconds"] = round(time.perf_counter() - started, 3)
                return version, facts, runtime, execution

        # A failed version is a result, not a reason to cancel remaining versions.
        rows = await asyncio.gather(*(run_version(v) for v in plan["requested_versions"]))
        report = aggregate(plan, [version_result(v, f, r, e.report if e else None) for v, f, r, e in rows])
        return _MatrixExecution(plan, report, rows)

    def _require_matrix_success(self, execution):
        if execution.report["overall_status"] != "passed":
            details = []
            for row in execution.report["versions"]:
                if row["status"] != "passed":
                    detail = (f"Python {row['requested_python']}: {row['failure_category']}; "
                              f"pytest exit {row['pytest_exit_code']}; {row.get('diagnostic') or ''}")
                    if row.get("pytest"):
                        detail += "\n" + failure_summary(row["pytest"])
                    details.append(detail)
            raise RuntimeError("Python compatibility matrix failed\n" + "\n".join(details))
        return matrix_summary(execution.report)

    def _matrix_bundle(self, execution):
        directory = (dag.directory()
            .with_new_file("matrix-policy.json", json.dumps(execution.plan, sort_keys=True))
            .with_new_file("matrix.json", json.dumps(execution.report, sort_keys=True))
            .with_new_file("summary.md", matrix_summary(execution.report)))
        for filename in ("pytest_ci.py", "pytest_matrix.py", "pytest_report.py", "pytest_quality.py"):
            directory = directory.with_new_file("ci.py" if filename == "pytest_ci.py" else filename,
                Path(__file__).with_name(filename).read_text(encoding="utf-8"))
        for version, facts, runtime, pytest in execution.rows:
            folder = dag.directory()
            if pytest is not None and runtime.get("pytest_started"):
                folder = self._pytest_bundle(pytest)
            folder = (folder.with_new_file("execution.json", json.dumps(facts, sort_keys=True))
                .with_new_file("runtime.json", json.dumps(runtime, sort_keys=True)))
            directory = directory.with_directory(f"versions/{version}", folder)
        return directory

    @function
    async def python_test_matrix(self, source: dagger.Directory, python_versions: list[str],
        test_path: str = "tests", max_failures: int = 10, markers: str = "",
        coverage_source: str = "", min_coverage: str = "", max_concurrency: int = 1) -> str:
        """Strict check: all requested interpreters, pytest executions and enabled gates must pass."""
        execution = await self._run_pytest_matrix(source, python_versions, test_path, max_failures,
            markers, coverage_source, min_coverage, max_concurrency)
        return self._require_matrix_success(execution)

    @function
    async def python_test_matrix_report(self, source: dagger.Directory, python_versions: list[str],
        test_path: str = "tests", max_failures: int = 10, markers: str = "",
        coverage_source: str = "", min_coverage: str = "", max_concurrency: int = 1) -> str:
        """Informational matrix JSON; failed versions remain present. Does not gate CI."""
        execution = await self._run_pytest_matrix(source, python_versions, test_path, max_failures,
            markers, coverage_source, min_coverage, max_concurrency)
        return json.dumps(execution.report, sort_keys=True)

    @function
    async def python_test_matrix_artifacts(self, source: dagger.Directory, python_versions: list[str],
        test_path: str = "tests", max_failures: int = 10, markers: str = "",
        coverage_source: str = "", min_coverage: str = "", max_concurrency: int = 1) -> dagger.Directory:
        """Execute once per interpreter; export matrix and facts. Gate with the exported ci.py."""
        execution = await self._run_pytest_matrix(source, python_versions, test_path, max_failures,
            markers, coverage_source, min_coverage, max_concurrency)
        return self._matrix_bundle(execution)

    @function
    @check
    async def fixture_test(
        self,
        source: Annotated[
            dagger.Directory,
            DefaultPath("/tests/fixtures/python-app"),
        ],
    ) -> None:
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
        print(self._require_pytest_success(execution))

    async def _matrix_fixture(self, source, scenario, concurrency=1):
        versions = ["3.12", "3.13", "3.14"]
        coverage_source, threshold = "", ""
        if scenario == "version-fail":
            source = source.with_new_file("tests/test_version_negative.py",
                "import sys\ndef test_version_specific_failure():\n    assert sys.version_info[:2] != (3, 14)\n")
        elif scenario == "unsupported":
            versions = ["3.11", *versions]
        elif scenario == "coverage-low":
            source = source.with_new_file("matrix_sample.py",
                await source.file("matrix_sample.py").contents() + "\n\ndef extra():\n    return 42\n")
            source = source.with_new_file("tests/test_extra.py",
                "import sys\nfrom matrix_sample import extra\ndef test_extra():\n"
                "    if sys.version_info[:2] != (3, 14):\n        assert extra() == 42\n")
            coverage_source, threshold = "matrix_sample", "100"
        elif scenario == "dependency-error":
            source = source.with_new_file("uv.lock", "version = 1\nrequires-python = '>=3.12'\npackage = []\n")
        elif scenario != "pass":
            raise ValueError("Unknown matrix fixture scenario")
        return await self._run_pytest_matrix(source, versions, coverage_source=coverage_source,
            min_coverage=threshold, max_concurrency=concurrency)

    async def _matrix_fixture_bundle(self, execution):
        bundle = self._matrix_bundle(execution)
        for version, _, _, pytest in execution.rows:
            if pytest is not None and pytest.runtime.get("pytest_started"):
                counter = await pytest.container.file("/tmp/engineering-ci-pytest-invocations").contents()
                if counter != "1":
                    raise RuntimeError(f"Python {version}: pytest ran {counter} times")
                bundle = (bundle.with_new_file(f"versions/{version}/fixture-invocations.txt", counter)
                    .with_new_file(f"versions/{version}/fixture-timing.json",
                        await pytest.container.file("/tmp/engineering-ci-fixture-timing.json").contents()))
        return bundle

    @function
    async def fixture_matrix_artifacts(self,
        source: Annotated[dagger.Directory, DefaultPath("/tests/fixtures/matrix-app")],
        scenario: str = "pass") -> dagger.Directory:
        """Real matrix probe; export original evidence even when a version fails."""
        if scenario == "performance":
            return await self.fixture_matrix_performance(source)
        execution = await self._matrix_fixture(source, scenario)
        return await self._matrix_fixture_bundle(execution)

    @function
    @check
    async def fixture_python_matrix(self,
        source: Annotated[dagger.Directory, DefaultPath("/tests/fixtures/matrix-app")]) -> None:
        """Verify real interpreters, per-version failures, coverage and original-fact gate."""
        verified = []
        for scenario in ("pass", "version-fail", "unsupported", "coverage-low", "dependency-error"):
            execution = await self._matrix_fixture(source, scenario)
            rows = {r["requested_python"]: r for r in execution.report["versions"]}
            for version in ("3.12", "3.13", "3.14"):
                row = rows[version]
                expected = ("dependency_installation" if scenario == "dependency-error" else
                    "pytest_failure" if scenario == "version-fail" and version == "3.14" else
                    "coverage_failure" if scenario == "coverage-low" and version == "3.14" else None)
                if row["failure_category"] != expected:
                    raise RuntimeError(f"{scenario}: unexpected Python {version} result: {row}")
                if expected != "dependency_installation" and (
                    not row["actual_python"].startswith(version + ".") or not row["runtime"]["pytest_started"]
                ):
                    raise RuntimeError(f"Missing real pytest runtime: {row}")
                if expected == "coverage_failure" and row["pytest_exit_code"] != 0:
                    raise RuntimeError("Coverage failure changed original pytest exit code")
            if scenario == "unsupported" and rows["3.11"]["failure_category"] != "python_unsupported":
                raise RuntimeError("Project constraint was ignored")
            try:
                self._require_matrix_success(execution)
            except RuntimeError:
                if scenario == "pass":
                    raise
            else:
                if scenario != "pass":
                    raise RuntimeError("Failed matrix incorrectly passed strict check")
            bundle = await self._matrix_fixture_bundle(execution)
            delivery = (dag.container().from_("python:3.14-slim")
                .with_directory("/reports", bundle).with_workdir("/reports"))
            code = await delivery.with_exec(["python", "ci.py", "gate", "/reports"],
                expect=dagger.ReturnType.ANY).exit_code()
            if (code == 0) != (scenario == "pass"):
                raise RuntimeError(f"Original-fact gate disagrees for {scenario}")
            await delivery.with_exec(["python", "ci.py", "summary", "/reports", "/tmp/summary.md"]).sync()
            verified.append(scenario)
        print("PASS: real matrix and delivery gate: " + ", ".join(verified))

    @function
    async def fixture_matrix_performance(self,
        source: Annotated[dagger.Directory, DefaultPath("/tests/fixtures/matrix-app")]) -> dagger.Directory:
        """Measure one Engine: single, three sequential and bounded concurrent executions."""
        measurements = []
        bundle = dag.directory()
        for mode, versions, concurrency in (("single", ["3.14"], 1),
                                            ("sequential", ["3.12", "3.13", "3.14"], 1),
                                            ("concurrent", ["3.12", "3.13", "3.14"], 2)):
            # Different fixture input avoids reusing a prior completed pytest step;
            # image layers and downloaded packages may still be warm.
            candidate = source.with_new_file("benchmark-mode.txt", mode)
            started = time.perf_counter()
            if mode == "single":
                pytest = await self._run_pytest(candidate, "3.14", "tests")
                self._require_pytest_success(pytest)
                counter = await pytest.container.file("/tmp/engineering-ci-pytest-invocations").contents()
                if counter != "1":
                    raise RuntimeError("Single benchmark repeated pytest")
                result = self._pytest_bundle(pytest).with_new_file("fixture-invocations.txt", counter)
            else:
                execution = await self._run_pytest_matrix(candidate, versions, max_concurrency=concurrency)
                self._require_matrix_success(execution)
                result = await self._matrix_fixture_bundle(execution)
            measurements.append({"mode": mode, "wall_seconds": round(time.perf_counter() - started, 3),
                                 "pytest_invocations": len(versions), "max_concurrency": concurrency})
            bundle = bundle.with_directory(mode, result)
        return result.with_directory("benchmarks", bundle).with_new_file("matrix-benchmark.json", json.dumps({"measurements": measurements,
            "engine": "one shared Dagger Engine; no per-version startup",
            "cache": "Distinct fixture input per mode; image/package caches may be warm; one sample, no statistical guarantee"}, sort_keys=True))

    @function
    @check
    async def fixture_lint(
        self,
        source: Annotated[
            dagger.Directory,
            DefaultPath("/tests/fixtures/python-app"),
        ],
    ) -> None:
        """Verify Ruff linting on the Python fixture."""
        print(await self.python_lint(
            source=source,
            python_version="3.14",
            lint_path="tests",
        ))

    @function
    @check
    async def fixture_format(
        self,
        source: Annotated[
            dagger.Directory,
            DefaultPath("/tests/fixtures/python-app"),
        ],
    ) -> None:
        """Verify that shared Ruff format checks pass on the Python fixture."""
        print(await self.python_format_check(
            source=source,
            python_version="3.14",
            format_path="tests",
        ))

    @function
    @check
    async def fixture_failure_report(
        self,
        source: Annotated[
            dagger.Directory,
            DefaultPath("/tests/fixtures/python-app"),
        ],
    ) -> None:
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
        print("PASS: JSON reported assertion failure with exit 1; python_test rejected it")

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
    ) -> None:
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
        print(json.dumps(results, sort_keys=True))

