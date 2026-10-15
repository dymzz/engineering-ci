"""Consumer scaffold for the published baseline used by the onboarding guide."""

import json
from textwrap import dedent

UPSTREAM = "dymzz/engineering-ci"
VERSION = "v0.6.5"
SHA = "804d791c60d34468166b6910c88fbc8149fdbf26"
ENGINE = "1.0.0-beta.16"
BRANCH = "codex/bootstrap-ci"


def files(repository):
    """Render only known relative paths; repository names are validated by the caller."""
    name = repository.split("/")[1]
    module = name + "-ci"
    package = name.replace("-", "_") + "_ci"
    class_name = "".join(part.title() for part in name.split("-")) + "Ci"
    workflow = f"""\
name: CI
on:
  pull_request:
    branches: [main]
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  python:
    uses: {UPSTREAM}/.github/workflows/reusable-python-ci.yml@{SHA} # {VERSION}
    with:
      pytest_report_function: test-results
      pytest_check: {module}:tests
    secrets:
      ENGINEERING_CI_READ_TOKEN: ${{{{ secrets.ENGINEERING_CI_READ_TOKEN }}}}
"""
    return {
        "README.md": f"# {name}\n\nPython 示例项目；业务代码在 app.py，测试在 tests/。\n\n"
        f"CI 使用 engineering-ci {VERSION}（{SHA}）。\n"
        "本地入口：`dagger check`；报告入口：`dagger api call test-results`。\n"
        "检查尚未执行，生成骨架不代表 CI 通过。\n",
        ".gitignore": ".venv/\n__pycache__/\n.pytest_cache/\n.ruff_cache/\n.test-tools/\n.env\n.env.*\n!.env.example\ndist/\n",
        "pyproject.toml": dedent(f'''\
            [project]
            name = "{name}"
            version = "0.1.0"
            requires-python = ">=3.14"

            [dependency-groups]
            dev = ["pytest==9.0.3", "ruff==0.16.10"]

            [tool.pytest.ini_options]
            testpaths = ["tests"]
            pythonpath = ["."]

            [tool.ruff]
            target-version = "py314"
            extend-exclude = [".dagger"]
            '''),
        "app.py": "def add(left: int, right: int) -> int:\n    return left + right\n",
        "tests/test_app.py": "from app import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        "dagger.json": json.dumps(
            {
                "name": module,
                "engineVersion": "v" + ENGINE,
                "sdk": {"source": "python"},
                "source": ".dagger",
                "dependencies": [
                    {
                        "name": "engineering-ci",
                        "source": f"github.com/{UPSTREAM}@{VERSION}",
                        "pin": SHA,
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        ".dagger/pyproject.toml": dedent(f'''\
            [project]
            name = "{module}"
            version = "0.1.0"
            requires-python = ">=3.14"
            dependencies = ["dagger-io"]

            [build-system]
            requires = ["uv_build==0.12.23"]
            build-backend = "uv_build"

            [tool.uv.sources]
            dagger-io = {{ path = "sdk", editable = true }}
            '''),
        f".dagger/src/{package}/__init__.py": f'from .main import {class_name}\n\n__all__ = ["{class_name}"]\n',
        f".dagger/src/{package}/main.py": dedent('''\
            from typing import Annotated

            import dagger
            from dagger import DefaultPath, Ignore, check, dag, function, object_type

            Source = Annotated[
                dagger.Directory, DefaultPath("/"),
                Ignore([".git", ".venv", ".dagger", ".test-tools", "dist", ".env", ".env.*",
                        "**/__pycache__", "**/.pytest_cache", "**/.ruff_cache"]),
            ]
            PYTHON = "3.14"
            TEST_POLICY = {"test_path": "tests"}


            @object_type
            class CLASS_NAME:
                @function
                @check
                async def lint(self, source: Source) -> None:
                    """Check project lint policy."""
                    await dag.engineering_ci().python_lint(
                        source=source, python_version=PYTHON, lint_path="."
                    )

                @function
                @check
                async def format_check(self, source: Source) -> None:
                    """Check formatting without changing source."""
                    await dag.engineering_ci().python_format_check(
                        source=source, python_version=PYTHON, format_path="."
                    )

                @function
                @check
                async def tests(self, source: Source) -> None:
                    """Keep the original pytest failure authoritative."""
                    await dag.engineering_ci().python_test(
                        source=source, python_version=PYTHON, **TEST_POLICY
                    )

                @function
                async def test_results(self, source: Source) -> dagger.Directory:
                    """Export one pytest execution for the Workflow's gate."""
                    return await dag.engineering_ci().python_test_artifacts(
                        source=source, python_version=PYTHON, **TEST_POLICY
                    )
            ''').replace("CLASS_NAME", class_name),
        ".github/workflows/ci.yml": workflow,
    }
