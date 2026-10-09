import json
from typing import Annotated

import dagger
from dagger import DefaultPath, check, dag, function, object_type

from .pytest_report import failure_summary, summarize_pytest


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
    ) -> tuple[dict, str]:
        """Execute once and collect JUnit evidence even when tests fail."""
        report_path = "/tmp/engineering-ci-pytest.xml"
        container = self._uv_container(source, python_version).with_exec(
            [
                "uv", "run", "--no-sync", "pytest", "-q",
                "--tb=short", f"--junitxml={report_path}", test_path,
            ],
            expect=dagger.ReturnType.ANY,
        )
        exit_code = await container.exit_code()
        output = await container.stdout()
        stderr = await container.stderr()
        # JUnit XML is optional when pytest exits before initializing plugins.
        xml = await container.with_exec([
            "sh", "-c",
            'if test -f "$1"; then cat "$1"; fi',
            "sh", report_path,
        ]).stdout()
        report = summarize_pytest(xml, exit_code, output + "\n" + stderr,
                                  max_failures=max_failures)
        return report, output

    @function
    async def python_test(
        self,
        source: dagger.Directory,
        python_version: str,
        test_path: str = "tests",
    ) -> str:
        """Run pytest; fail CI with a bounded JUnit-based failure summary."""
        report, output = await self._run_pytest(source, python_version, test_path)
        if report["status"] != "passed":
            raise RuntimeError(failure_summary(report))
        return output

    @function
    async def python_test_report(
        self,
        source: dagger.Directory,
        python_version: str,
        test_path: str = "tests",
        max_failures: int = 10,
    ) -> str:
        """Return JSON summary for both passing and failing pytest runs.

        This informational function does NOT gate CI. Use python_test for
        failure propagation; report.status and exit_code are explicit.
        """
        report, _ = await self._run_pytest(
            source, python_version, test_path, max_failures=max_failures
        )
        return json.dumps(report, ensure_ascii=False, sort_keys=True)

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
        output = await self.python_test(
            source=source,
            python_version="3.14",
            test_path="tests",
        )
        report = json.loads(await self.python_test_report(
            source=source, python_version="3.14", test_path="tests",
        ))
        if (not report["report_available"] or report["status"] != "passed"
                or report["exit_code"] != 0 or report["counts"]["tests"] == 0):
            raise ValueError(f"Unexpected passing test report: {report}")
        return output

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
        report = json.loads(await self.python_test_report(
            source=failing, python_version="3.14", test_path="tests",
        ))
        if (not report["report_available"] or report["status"] != "failed"
                or report["exit_code"] != 1
                or report["counts"]["failures"] != 1
                or not any(
                    item["category"] == "assertion_failure"
                    for item in report["failures"]
                )):
            raise ValueError(f"Unexpected negative test report: {report}")
        try:
            await self.python_test(source=failing, python_version="3.14", test_path="tests")
        except RuntimeError as exc:
            summary = str(exc)
            if not all(evidence in summary for evidence in (
                "pytest exit 1", "test_intentional_failure", "assert 1 == 2",
            )):
                raise ValueError(f"Unexpected pytest failure summary: {summary}") from exc
        else:
            raise ValueError("python_test incorrectly accepted the failing pytest fixture")
        return "PASS: JSON reported assertion failure with exit 1; python_test rejected it"

