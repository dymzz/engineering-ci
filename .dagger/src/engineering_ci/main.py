from typing import Annotated

import dagger
from dagger import DefaultPath, check, dag, function, object_type


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
    async def python_test(
        self,
        source: dagger.Directory,
        python_version: str,
        test_path: str = "tests",
    ) -> str:
        """Run pytest against a uv-managed Python project."""

        return await (
            self._uv_container(source, python_version)
            .with_exec([
                "uv", "run", "--no-sync",
                "pytest", "-q", test_path,
            ])
            .stdout()
        )
    
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
        return await self.python_test(
            source=source,
            python_version="3.14",
            test_path="tests",
        )

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