import asyncio
import base64
import json
import re
import tempfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import dagger
from dagger import DefaultPath, Ignore, check, dag, function, object_type

from . import link_check, repository, tooling
from . import python_static as static
from . import script_analysis as script
from .plan_result import PlanExecution
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
from .python_dependency_auth import prepare_private_git
from .python_dependency_auth import sync_dependencies as sync_python_dependencies


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
    dependency_packages: str | None = None


@dataclass(frozen=True)
class _MatrixExecution:
    plan: dict
    report: dict
    rows: list


# Only Workflow analysis needs the source tree itself, so it skips the uv sync
# a Python project would require. A repository can be checked for Workflow
# correctness without declaring a Python project at all.


# Where _uv_container leaves the consumer's own locked environment. Pyright
# reads it for import resolution while its own packages stay isolated.
_PROJECT_ENVIRONMENT = "/workspace/.venv/bin/python"
_DEPTRY_JSON = "/tmp/engineering-ci-deptry.json"
# Filter historical VCS/venv/cache noise before CLI upload on single-view
# execution entrypoints; workflow cross-check scanners, Weaver, and Plan keep
# raw dagger.Directory.
_FilteredSource = Annotated[dagger.Directory, Ignore(tooling.SOURCE_NOISE)]
# Reads a file the scan already wrote. A missing file yields empty output,
# which the classifier turns into an explicit failure rather than a pass.
_READ_OPTIONAL = (
    "import pathlib,sys;"
    "target=pathlib.Path(sys.argv[1]);"
    "sys.stdout.write(target.read_text(encoding='utf-8') if target.is_file() else '')"
)
# Capture arbitrary 0-255 exit codes cleanly because Dagger ReturnType.ANY omits 128-191
# (including git exit 128 when .git is absent or invalid).
_EXEC_CAPTURE_SCRIPT = (
    "import base64, datetime, json, subprocess, sys\n"
    "started_at = datetime.datetime.now(datetime.UTC).isoformat()\n"
    "try:\n"
    "    proc = subprocess.run(\n"
    "        sys.argv[1:], stdin=subprocess.DEVNULL, capture_output=True, check=False\n"
    "    )\n"
    "    code, out, err = proc.returncode, proc.stdout, proc.stderr\n"
    "except FileNotFoundError:\n"
    "    code = 127\n"
    "    out = b''\n"
    "    name = sys.argv[1] if len(sys.argv) > 1 else ''\n"
    "    err = f'Command not found: {name}'.encode('utf-8')\n"
    "print(\n"
    "    json.dumps(\n"
    "        {\n"
    "            'exit_code': code,\n"
    "            'process_started_at': started_at,\n"
    "            'process_finished_at': datetime.datetime.now(datetime.UTC).isoformat(),\n"
    "            'stdout_b64': base64.b64encode(out).decode('ascii'),\n"
    "            'stderr_b64': base64.b64encode(err).decode('ascii'),\n"
    "        }\n"
    "    )\n"
    ")\n"
)


async def _exec_capture(
    container: dagger.Container,
    argv: list[str],
    *,
    with_container: bool = False,
    script_suffix: str = "",
):
    """Capture container command exit/output across 0-255 cleanly."""
    executed = container.with_exec(
        ["python", "-I", "-c", _EXEC_CAPTURE_SCRIPT + script_suffix, *argv]
    )
    payload = json.loads(await executed.stdout())
    result = {
        "exit_code": int(payload["exit_code"]),
        "process_started_at": payload["process_started_at"],
        "process_finished_at": payload["process_finished_at"],
        "stdout": base64.b64decode(payload["stdout_b64"]).decode(
            "utf-8", errors="replace"
        ),
        "stderr": base64.b64decode(payload["stderr_b64"]).decode(
            "utf-8", errors="replace"
        ),
    }
    return (result, executed) if with_container else result


@object_type
class EngineeringCi:
    weaver_source: dagger.Directory | None = dagger.field(default=None)

    def _weaver_container(self) -> dagger.Container:
        # Verify the complete release asset before extracting its one executable.
        install = (
            "import hashlib,io,os,tarfile,urllib.request;"
            f"data=urllib.request.urlopen({tooling.WEAVER_LINUX_ASSET!r},timeout=60).read();"
            f"assert hashlib.sha256(data).hexdigest()=={tooling.WEAVER_LINUX_SHA256!r},'Weaver checksum mismatch';"
            "archive=tarfile.open(fileobj=io.BytesIO(data),mode='r:xz');"
            "members=[m for m in archive.getmembers() if m.isfile() and m.name.split('/')[-1]=='weaver'];"
            "assert len(members)==1,'Weaver executable missing or ambiguous';"
            "open('/usr/local/bin/weaver','wb').write(archive.extractfile(members[0]).read());"
            "os.chmod('/usr/local/bin/weaver',0o755)"
        )
        return (
            dag.container(platform=dagger.Platform("linux/amd64"))
            .from_(tooling.UV_IMAGE)
            .with_exec(["python", "-c", install])
            .with_new_file(
                "/check/requirements.txt",
                Path(__file__).with_name("weaver-requirements.txt").read_text(),
            )
            .with_exec(
                ["uv", "pip", "install", "--system", "-r", "/check/requirements.txt"]
            )
            .with_new_file(
                "/check/weaver.py", Path(__file__).with_name("weaver.py").read_text()
            )
            .with_new_file(
                "/check/runtime.py",
                Path(__file__).with_name("weaver_runtime.py").read_text(),
            )
            .with_new_file("/check/empty.toml", "")
            .with_workdir("/workspace")
        )

    @function
    async def weaver_artifacts(
        self,
        source: dagger.Directory,
        kind: str = "registry",
        config_path: str = "ci-weaver.toml",
    ) -> dagger.Directory:
        """Export one Weaver execution, its native logs, package and report."""
        return await self._weaver_execution(source, kind, config_path)

    _SCAN_SKIPPED_NOISE = (
        ".git",
        ".venv",
        "**/.venv",
        "__pycache__",
        "**/__pycache__",
    )
    _SCAN_SKIPPED_PARTS = frozenset({".git", ".venv", "__pycache__"})

    def _bound_public_source(self, source: dagger.Directory) -> dagger.Directory:
        """Bind caller source with generic VCS/cache/OS exclusions only.

        Never guesses project filenames such as ``.env.*``, ``*.local.toml``,
        or ``.docker/*``, and never strips Plan overlays or consumer targets;
        host credential/local excludes live in ``repository.py``.
        """
        return dag.directory().with_directory(
            "/", source, exclude=list(tooling.SOURCE_NOISE)
        )

    def _scan_equivalent_source(self, source: dagger.Directory) -> dagger.Directory:
        """Bind source excluding only segments always skipped by scan discovery."""
        return dag.directory().with_directory(
            "/", source, exclude=list(self._SCAN_SKIPPED_NOISE)
        )

    async def _needs_unfiltered_scan_cross_check(
        self, source: dagger.Directory, bound: dagger.Directory, policy: dict
    ) -> bool:
        """Skip duplicate raw scan unless filter removed discoverable dirs/paths."""
        configured = [*policy.get("scan_paths", ()), policy.get("config") or ""]
        if any(
            part in self._SCAN_SKIPPED_PARTS
            for raw in configured
            if raw
            for part in str(raw).replace("\\", "/").split("/")
        ):
            return True
        scan_equivalent = self._scan_equivalent_source(source)
        return await bound.digest() != await scan_equivalent.digest()

    async def _weaver_execution(
        self,
        source,
        kind,
        config_path,
        *,
        context=None,
        plan=None,
        generated_inputs=(),
        _bound_source=None,
        _source_excludes=None,
    ):
        from . import native_results

        if kind not in {"registry", "sample", "live", "terminal"}:
            raise ValueError("Unknown Weaver check")
        bound = (
            _bound_source
            if _bound_source is not None
            else self._bound_public_source(source)
        )
        result = (
            self._weaver_container()
            .with_directory("/workspace", bound)
            .with_env_variable("ECI_SOURCE_DIGEST", await bound.digest())
            .with_env_variable("ECI_WEAVER_ASSET_SHA256", tooling.WEAVER_LINUX_SHA256)
            .with_env_variable(
                "ECI_SEMCONV_OFFICIAL_SOURCE",
                json.dumps(tooling.SEMCONV_OFFICIAL_SOURCE, sort_keys=True),
            )
            .with_exec(["python", "/check/runtime.py", kind, "--config", config_path])
        )
        return await native_results.weaver_result(
            result.directory("/out"),
            bound,
            kind,
            context=context,
            plan=plan,
            generated_inputs=generated_inputs,
            input_source=source,
            source_excludes=(
                tooling.SOURCE_NOISE if _source_excludes is None else _source_excludes
            ),
        )

    @function
    async def weaver_report(
        self,
        source: dagger.Directory,
        kind: str = "registry",
        config_path: str = "ci-weaver.toml",
    ) -> str:
        """Read the same native execution used by the artifact and strict checks."""
        artifacts = await self.weaver_artifacts(source, kind, config_path)
        filename = "report.json" if kind == "sample" else "check-result.json"
        return await artifacts.file(filename).contents()

    async def _require_weaver(self, source, kind, config_path):
        report = json.loads(await self.weaver_report(source, kind, config_path))
        if report["status"] != "passed":
            raise ValueError(f"{report['name']}: {report['reason']}")
        if kind != "sample" and report.get("metadata_status") == "unavailable":
            raise ValueError(f"{report['name']}: metadata_unavailable")
        if kind != "sample" and (
            report.get("check_contract", {}).get("status") != "valid"
            or report.get("native_satisfaction", {}).get("status") != "satisfied"
        ):
            raise ValueError(f"{report['name']}: native_contract_unsatisfied")

    @function
    async def weaver_enforce(
        self,
        source: dagger.Directory,
        kind: str = "live",
        config_path: str = "ci-weaver.toml",
    ) -> None:
        """API gate: fail the call when the selected native Weaver report fails."""
        await self._require_weaver(source, kind, config_path)

    @function
    @check
    async def weaver_registry(
        self,
        source: Annotated[
            dagger.Directory,
            DefaultPath("/"),
            Ignore(repository.WEAVER_DEFAULT_EXCLUDES),
        ],
        config_path: str = "ci-weaver.toml",
    ) -> None:
        """Check and package an explicitly selected, fixed local Registry."""
        await self._require_weaver(
            self.weaver_source or source, "registry", config_path
        )

    @function
    @check
    async def weaver_live(
        self,
        source: Annotated[
            dagger.Directory,
            DefaultPath("/"),
            Ignore(repository.WEAVER_DEFAULT_EXCLUDES),
        ],
        config_path: str = "ci-weaver.toml",
    ) -> None:
        """Validate a completed OTLP capture through Weaver's gRPC receiver."""
        await self._require_weaver(self.weaver_source or source, "live", config_path)

    @function
    @check
    async def weaver_terminal(
        self,
        source: Annotated[
            dagger.Directory,
            DefaultPath("/"),
            Ignore(repository.WEAVER_DEFAULT_EXCLUDES),
        ],
        config_path: str = "ci-weaver.toml",
    ) -> None:
        """Post-completion Pipeline validation; requires explicit terminal evidence."""
        await self._require_weaver(
            self.weaver_source or source, "terminal", config_path
        )

    @function(cache="never")
    async def plan_execution(
        self,
        plan: dagger.File,
        source: dagger.Directory,
        python_version: str = "3.14",
        weaver_config: str = "ci-weaver.toml",
        run_ref: str = "",
        attempt_ref: str = "",
    ) -> PlanExecution:
        """Build one result object for Report, Artifact and strict Enforce."""
        from . import plan_execution

        return await plan_execution.execute(
            self,
            plan,
            source,
            python_version,
            weaver_config,
            run_ref=run_ref or None,
            attempt_ref=attempt_ref or None,
        )

    @function(cache="never")
    async def execute_plan(
        self,
        plan: dagger.File,
        source: dagger.Directory,
        python_version: str = "3.14",
        weaver_config: str = "ci-weaver.toml",
        run_ref: str = "",
        attempt_ref: str = "",
    ) -> str:
        """Read the Report from one Plan result; API exit is not its verdict."""
        execution = await self.plan_execution(
            plan, source, python_version, weaver_config, run_ref, attempt_ref
        )
        return await execution.report()

    @function(cache="never")
    async def fixture_plan_execution(
        self,
        plan: dagger.File,
        source: dagger.Directory,
        python_version: str = "3.14",
        run_ref: str = "",
        attempt_ref: str = "",
    ) -> dagger.Directory:
        """Inspect all consumers of one result; strict production API is enforce."""
        execution = await self.plan_execution(
            plan, source, python_version, run_ref=run_ref, attempt_ref=attempt_ref
        )
        bundle = execution.artifacts
        original_id = await bundle.id()
        report = await execution.report()
        original_exit = await bundle.file("exit-code.txt").contents()
        try:
            await execution.enforce()
            strict = {"status": "passed"}
        except RuntimeError as error:
            strict = {"status": "failed", "diagnostic": str(error)}
        inspection = {
            "scope": "one_explicit_PlanExecution_object",
            "report_equals_artifact": report
            == await bundle.file("report.json").contents(),
            "same_bundle_id_after_enforce": original_id
            == await execution.artifacts.id(),
            "original_exit": original_exit.strip(),
            "strict": strict,
            "origin_execution_attributed": False,
        }
        return (
            dag.directory()
            .with_directory("result", bundle)
            .with_new_file("inspection.json", json.dumps(inspection, sort_keys=True))
        )

    @function(cache="never")
    async def fixture_link_cache(
        self, plan: dagger.File, source: dagger.Directory
    ) -> dagger.Directory:
        """Observe real Lychee against a changing, controlled HTTP service."""
        from .link_cache_fixture import observe

        return await observe(self, plan, source)

    @function(cache="never")
    async def fixture_mixed_plan_cache(
        self, plan: dagger.File, source: dagger.Directory
    ) -> dagger.Directory:
        """Observe prerequisites and local checks beside fresh online links."""
        from .mixed_plan_fixture import observe

        return await observe(self, plan, source)

    @function
    async def sources_self_test(self) -> str:
        """Run the installed source reconciler on a disposable fixture."""
        import hashlib
        import json
        import tempfile
        from pathlib import Path

        from engineering_ci import sources

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data/acquired").mkdir(parents=True)
            content = b"source bytes"
            (root / "data/acquired/a.bin").write_bytes(content)
            (root / "data/catalog").mkdir()
            (root / "data/catalog/manifest.json").write_text(
                json.dumps(
                    [
                        {
                            "path": "data/acquired/a.bin",
                            "sha256": hashlib.sha256(content).hexdigest(),
                            "size_bytes": len(content),
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (root / "ci-sources.toml").write_text(
                "schema_version = 1\n"
                'manifest = "data/catalog/manifest.json"\n'
                'acquired_roots = ["data/acquired"]\n'
                "require_text_unset = false\n",
                encoding="utf-8",
            )
            report = sources.reconcile(
                sources.load_config(root / "ci-sources.toml"), root=root
            )
            return f"{sources.REPORT_KIND}:{report.status}:{report.counts['checked']}"

    @function
    async def selection_self_test(self) -> str:
        """Run the installed selector on a disposable fixture, without Git."""
        import tempfile
        from pathlib import Path

        from engineering_ci import selection

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src/pkg").mkdir(parents=True)
            (root / "tests").mkdir()
            (root / "src/pkg/__init__.py").write_text("", encoding="utf-8")
            (root / "src/pkg/sub.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "tests/test_pkg.py").write_text(
                "import unittest\n\n\nclass T(unittest.TestCase):\n"
                "    def test_ok(self):\n        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            (root / "tach.toml").write_text(
                'source_roots = ["src"]\n\n[[modules]]\npath = "pkg"\n'
                "depends_on = []\n",
                encoding="utf-8",
            )
            (root / "ci-selection.toml").write_text(
                'schema_version = 1\n\n[module_tests]\n"pkg" = ["tests.test_pkg"]\n',
                encoding="utf-8",
            )
            config = selection.load_config(root / "ci-selection.toml")
            changes = selection.ChangeSet(
                root=root,
                base_ref="base",
                head_ref="HEAD",
                base_commit="a" * 40,
                head_commit="b" * 40,
                tree="c" * 40,
                paths=("src/pkg/sub.py",),
                worktree_dirty=True,
                complete=True,
            )
            result = selection.select(changes, config, root=root)
            return f"{selection.PLAN_KIND}:{result.status}:{','.join(result.tests)}"

    @function
    async def shared_identity(self) -> str:
        """Identify the installed shared package used by CLI and Dagger."""
        from importlib.metadata import version

        from engineering_ci import selection

        return f"{version('engineering-ci')}:{selection.SCHEMA_VERSION}:{selection.PLAN_KIND}"

    repository_coverage: bool = dagger.field(default=False)

    @function
    @check
    async def lint(
        self,
        source: Annotated[
            dagger.Directory, DefaultPath("/"), Ignore(repository.SOURCE_EXCLUDES)
        ],
    ) -> None:
        """Lint this repository's actual Python source."""
        print(
            await repository.environment(source)
            .with_exec(["uv", "run", "--no-sync", "ruff", "check", "."])
            .stdout()
        )

    @function
    @check
    async def format_check(
        self,
        source: Annotated[
            dagger.Directory, DefaultPath("/"), Ignore(repository.SOURCE_EXCLUDES)
        ],
    ) -> None:
        """Check formatting in the current source tree."""
        print(
            await repository.environment(source)
            .with_exec(["uv", "run", "--no-sync", "ruff", "format", "--check", "."])
            .stdout()
        )

    async def _repository_tests(self, source):
        return await self._run_pytest(
            source,
            "3.14",
            "tests",
            coverage_source="src/engineering_ci" if self.repository_coverage else "",
            _prepared_container=repository.environment(source, tests=True),
        )

    @function
    @check
    async def tests(
        self,
        source: Annotated[
            dagger.Directory, DefaultPath("/"), Ignore(repository.SOURCE_EXCLUDES)
        ],
    ) -> None:
        """Require the actual pytest result; reports do not override its exit."""
        self._require_pytest_success(await self._repository_tests(source))

    @function
    async def repository_test_artifacts(
        self,
        source: Annotated[
            dagger.Directory, DefaultPath("/"), Ignore(repository.SOURCE_EXCLUDES)
        ],
        core_evidence: bool = True,
    ) -> dagger.Directory:
        """Export the same pytest node used by tests, even when it failed."""
        execution = await self._repository_tests(source)
        bundle = self._pytest_bundle(execution)
        if not core_evidence:
            return bundle
        bundle = bundle.with_file("core-junit.xml", bundle.file("junit.xml"))
        if self.repository_coverage:
            bundle = bundle.with_file(
                "core-coverage.xml", bundle.file("coverage.xml")
            ).with_file("core-coverage.json", bundle.file("coverage.json"))
        tools = execution.container.with_exec(
            [
                "uv",
                "run",
                "--no-sync",
                "python",
                "-m",
                "engineering_ci.workflow_actions",
                "manifest",
                "tools",
                "--scope",
                "core",
                "--output",
                "/tmp/tool-versions.json",
            ]
        )
        return bundle.with_file(
            "tool-versions.json", tools.file("/tmp/tool-versions.json")
        )

    @function
    async def repository_ruff_tools(
        self,
        source: Annotated[
            dagger.Directory, DefaultPath("/"), Ignore(repository.SOURCE_EXCLUDES)
        ],
    ) -> dagger.File:
        """Read the actual Ruff/uv versions from the check environment."""
        return (
            repository.environment(source)
            .with_exec(
                [
                    "uv",
                    "run",
                    "--no-sync",
                    "python",
                    "-m",
                    "engineering_ci.workflow_actions",
                    "manifest",
                    "tools",
                    "--scope",
                    "ruff",
                    "--output",
                    "/tmp/tool-versions.json",
                ]
            )
            .file("/tmp/tool-versions.json")
        )

    @function
    @check
    async def version(
        self,
        source: Annotated[
            dagger.Directory, DefaultPath("/"), Ignore(repository.SOURCE_EXCLUDES)
        ],
    ) -> None:
        """Verify the four version fields without mirroring Workflow YAML."""
        print(
            await repository.environment(source)
            .with_exec(
                [
                    "uv",
                    "run",
                    "--no-sync",
                    "python",
                    "-m",
                    "engineering_ci.release.version",
                    "check",
                ]
            )
            .stdout()
        )

    @function
    @check
    async def lock(
        self,
        source: Annotated[
            dagger.Directory, DefaultPath("/"), Ignore(repository.SOURCE_EXCLUDES)
        ],
    ) -> None:
        """Let uv validate both project lockfiles."""
        result = repository.environment(source).with_exec(["uv", "lock", "--check"])
        print(
            await result.with_exec(
                ["uv", "lock", "--project", ".dagger", "--check"]
            ).stdout()
        )

    def _uv_container(
        self,
        source: dagger.Directory,
        python_version: str,
        sync_dependencies: bool = True,
        git_token: dagger.Secret | None = None,
        dependency_packages: dagger.Directory | None = None,
        *,
        _bound_source: dagger.Directory | None = None,
        system_packages: tuple[str, ...] = (),
    ) -> dagger.Container:
        """Create an isolated uv environment for a Python project."""

        supported = set(SUPPORTED_PYTHONS)

        if python_version not in supported:
            raise ValueError(
                f"Unsupported Python version: {python_version}. "
                f"Supported: {sorted(supported)}"
            )

        image = f"ghcr.io/astral-sh/uv:python{python_version}-trixie-slim"

        if dependency_packages is not None and git_token is not None:
            raise ValueError("Local dependency packages do not accept Git credentials")
        base = dag.container().from_(image)
        if system_packages:
            base = base.with_exec(["apt-get", "update", "-qq"]).with_exec(
                [
                    "apt-get",
                    "install",
                    "-y",
                    "-qq",
                    "--no-install-recommends",
                    *system_packages,
                ]
            )
        if git_token is not None:
            base = prepare_private_git(base)
        bound = (
            _bound_source
            if _bound_source is not None
            else self._bound_public_source(source)
        )
        container = (
            base.with_env_variable("UV_NO_MANAGED_PYTHON", "1")
            .with_env_variable("UV_PYTHON_DOWNLOADS", "0")
            .with_mounted_cache(
                "/root/.cache/uv",
                dag.cache_volume(f"engineering-ci-uv-python{python_version}"),
            )
            .with_env_variable("UV_LINK_MODE", "copy")
            .with_directory("/workspace", bound)
            .with_workdir("/workspace")
        )
        if dependency_packages is not None:
            container = container.with_directory(
                "/tmp/engineering-ci-dependency-packages", dependency_packages
            ).with_new_file(
                "/tmp/python_dependency_packages.py",
                Path(__file__)
                .with_name("python_dependency_packages.py")
                .read_text(encoding="utf-8"),
            )
        return (
            sync_python_dependencies(
                container,
                python_version,
                git_token,
                dependency_packages=dependency_packages is not None,
            )
            if sync_dependencies
            else container
        )

    @function
    async def python_lint(
        self,
        source: _FilteredSource,
        python_version: str,
        lint_path: str = "src",
        git_token: dagger.Secret | None = None,
        dependency_packages: dagger.Directory | None = None,
    ) -> str:
        """Run Ruff against a uv-managed Python project."""

        return await (
            self._uv_container(
                source,
                python_version,
                git_token=git_token,
                dependency_packages=dependency_packages,
            )
            .with_exec(
                [
                    "uv",
                    "run",
                    "--no-sync",
                    "ruff",
                    "check",
                    lint_path,
                ]
            )
            .stdout()
        )

    @function
    async def python_format_check(
        self,
        source: _FilteredSource,
        python_version: str,
        format_path: str = "src",
        git_token: dagger.Secret | None = None,
        dependency_packages: dagger.Directory | None = None,
    ) -> str:
        """Check Ruff formatting without modifying consumer source files."""

        return await (
            self._uv_container(
                source,
                python_version,
                git_token=git_token,
                dependency_packages=dependency_packages,
            )
            .with_exec(
                [
                    "uv",
                    "run",
                    "--no-sync",
                    "ruff",
                    "format",
                    "--check",
                    format_path,
                ]
            )
            .stdout()
        )

    def _uv_tool_container(
        self,
        source: dagger.Directory,
        *,
        _bound_source: dagger.Directory | None = None,
    ) -> dagger.Container:
        bound = (
            _bound_source
            if _bound_source is not None
            else self._bound_public_source(source)
        )
        return (
            dag.container()
            .from_("ghcr.io/astral-sh/uv:python3.14-trixie-slim")
            .with_mounted_cache(
                "/root/.cache/uv", dag.cache_volume("engineering-ci-uv-python3.14")
            )
            .with_env_variable("UV_LINK_MODE", "copy")
            .with_env_variable("UV_PYTHON_DOWNLOADS", "never")
            .without_env_variable("SHELLCHECK_OPTS")
            .with_directory("/workspace", bound)
            .with_workdir("/workspace")
        )

    async def _exec_uv_tool(
        self,
        source: dagger.Directory,
        argv: list[str],
        *,
        _bound_source: dagger.Directory | None = None,
        _source_excludes: list[str] | tuple[str, ...] | None = None,
    ) -> dict:
        from . import native_results

        bound = (
            _bound_source
            if _bound_source is not None
            else self._bound_public_source(source)
        )
        metadata_errors = {}
        try:
            input_ref = await native_results.directory_reference(source)
        except Exception as error:  # noqa: BLE001 - retain container exit on optional metadata failure
            input_ref = {"status": "unavailable", "reason": str(error)}
            metadata_errors["source_directory"] = input_ref
        try:
            consumed_ref = await native_results.directory_reference(bound)
        except Exception as error:  # noqa: BLE001 - retain container exit on optional metadata failure
            consumed_ref = {"status": "unavailable", "reason": str(error)}
            metadata_errors["consumed_source_directory"] = consumed_ref
        filtered = (
            consumed_ref["digest"] != input_ref["digest"]
            if "digest" in input_ref and "digest" in consumed_ref
            else None
        )
        excludes = list(
            tooling.SOURCE_NOISE if _source_excludes is None else _source_excludes
        )
        container = self._uv_tool_container(source, _bound_source=bound)
        result = await _exec_capture(container, argv)
        observation = {
            "execution_location": "dagger_container",
            "freshness_policy": "native_exec_cache",
            "process_started_at": result["process_started_at"],
            "process_finished_at": result["process_finished_at"],
            "argv": list(argv),
            "source_directory": input_ref,
            "consumed_source_directory": consumed_ref,
            "container_source_filtered": filtered,
            "origin_execution_attributed": False,
        }
        if filtered and excludes:
            observation["container_source_excludes"] = excludes
        if metadata_errors:
            observation["metadata_status"] = "unavailable"
            observation["metadata_errors"] = metadata_errors
        result["observation"] = observation
        return result

    async def _run_repository_script(self, source, policy, arguments, tool_key):
        from engineering_ci import workflow_analysis

        bound = self._bound_public_source(source)
        input_digest = await source.digest()
        consumed_digest = await bound.digest()
        container_source_filtered = consumed_digest != input_digest
        container = self._uv_tool_container(source, _bound_source=bound)
        scan_source = Path(workflow_analysis.__file__).read_text(encoding="utf-8")
        scan_command = [
            "python",
            "-c",
            (
                "import json, sys; sys.path.insert(0, '/tmp'); "
                "from eci_scan_source import repository_scan_targets; "
                "print(json.dumps(repository_scan_targets('/workspace', json.loads(sys.argv[1]))))"
            ),
            json.dumps(policy),
        ]
        # Inspect the exact filtered mount in the same immutable container
        # snapshot used by the native tool.
        inspected = container.with_new_file(
            "/tmp/eci_scan_source.py",
            scan_source,
        ).with_exec(scan_command)
        observed = json.loads(await inspected.stdout())
        if container_source_filtered and await self._needs_unfiltered_scan_cross_check(
            source, bound, policy
        ):
            raw_container = self._uv_tool_container(source, _bound_source=source)
            raw_inspected = raw_container.with_new_file(
                "/tmp/eci_scan_source.py",
                scan_source,
            ).with_exec(scan_command)
            raw_observed = json.loads(await raw_inspected.stdout())
            filter_refusal = workflow_analysis.repository_scan_filter_refusal(
                policy, observed, raw_observed
            )
            if filter_refusal is not None:
                filter_refusal["source_directory_digest"] = input_digest
                filter_refusal["consumed_directory_digest"] = consumed_digest
                filter_refusal["container_source_filtered"] = True
                filter_refusal["container_source_excludes"] = list(tooling.SOURCE_NOISE)
                return filter_refusal
        refusal = workflow_analysis.repository_scan_refusal(policy, observed)
        if refusal is not None:
            refusal["source_directory_digest"] = input_digest
            refusal["consumed_directory_digest"] = consumed_digest
            refusal["container_source_filtered"] = container_source_filtered
            if container_source_filtered:
                refusal["container_source_excludes"] = list(tooling.SOURCE_NOISE)
            return refusal
        policy = {
            **policy,
            "scan_paths": [target["path"] for target in observed["targets"]],
        }
        result = container.with_exec(
            script.runner_prefix(tool_key) + arguments(policy),
            expect=dagger.ReturnType.ANY,
        )
        stdout = await result.stdout()
        report = script.script_report(
            policy,
            await result.exit_code(),
            stdout,
            await result.stderr(),
            native_raw=stdout
            if policy["tool"] != "actionlint"
            or policy.get("json_format")
            or stdout.lstrip().startswith("[")
            else "",
        )
        report["target_sources"] = observed["targets"]
        report["source_directory_digest"] = input_digest
        report["consumed_directory_digest"] = consumed_digest
        report["container_source_filtered"] = container_source_filtered
        if report["container_source_filtered"]:
            report["container_source_excludes"] = list(tooling.SOURCE_NOISE)
        return report

    async def _run_actionlint(self, source: dagger.Directory, policy: dict) -> dict:
        tool_key = "actionlint+shellcheck" if policy.get("shellcheck") else "actionlint"
        return await self._run_repository_script(
            source, policy, script.actionlint_arguments, tool_key
        )

    async def _run_shellcheck(self, source: dagger.Directory, policy: dict) -> dict:
        return await self._run_repository_script(
            source, policy, script.shellcheck_arguments, "shellcheck"
        )

    async def _run_zizmor(self, source: dagger.Directory, policy: dict) -> dict:
        return await self._run_repository_script(
            source, policy, script.zizmor_arguments, "zizmor"
        )

    @function
    async def actionlint_workflows(
        self,
        source: dagger.Directory,
        scan_paths: list[str] | None = None,
        config: str = "",
        shellcheck: bool = False,
        json_format: bool = False,
    ) -> str:
        """Check GitHub Actions Workflows with pinned actionlint (opt-in ShellCheck).

        ShellCheck and pyflakes stay explicitly disabled by default so checked
        scope never depends on implicit PATH tools. Set ``shellcheck=True`` to
        pair pinned actionlint with pinned shellcheck cleanly.
        """
        report = await self._run_actionlint(
            source,
            script.actionlint_policy(
                scan_paths,
                config,
                shellcheck=shellcheck,
                json_format=json_format,
            ),
        )
        return json.dumps(report, ensure_ascii=False, sort_keys=True)

    @function
    async def shellcheck_scripts(
        self,
        source: dagger.Directory,
        scan_paths: list[str] | None = None,
    ) -> str:
        """Check standalone POSIX shell scripts with pinned ShellCheck."""
        report = await self._run_shellcheck(
            source, script.shellcheck_policy(scan_paths)
        )
        return json.dumps(report, ensure_ascii=False, sort_keys=True)

    @function
    async def zizmor_workflows(
        self,
        source: dagger.Directory,
        scan_paths: list[str] | None = None,
        config: str = "",
        persona: str = "",
        offline: bool = True,
    ) -> str:
        """Check GitHub Actions Workflows for security issues with pinned zizmor."""
        report = await self._run_zizmor(
            source,
            script.zizmor_policy(
                scan_paths,
                config=config,
                persona=persona,
                offline=offline,
            ),
        )
        return json.dumps(report, ensure_ascii=False, sort_keys=True)

    @function
    async def analyze_script_scope(
        self,
        source: dagger.Directory,
        scan_paths: list[str],
    ) -> str:
        """Route each explicit path to the tool that can actually check it.

        Selection uses the declared file suffix only. PowerShell is reported as
        a Windows runner responsibility rather than run here, and anything
        outside the supported scope is reported as not executed with the
        reason, never skipped silently.
        """
        reports = []
        for scan_path in script.path_list("scan_paths", scan_paths, []):
            kind = script.target_kind(scan_path)
            if kind == "actionlint":
                report = await self._run_actionlint(
                    source, script.actionlint_policy([scan_path])
                )
            elif kind == "shellcheck":
                report = await self._run_shellcheck(
                    source, script.shellcheck_policy([scan_path])
                )
            elif kind == "psscriptanalyzer":
                report = script.deferred_report(
                    scan_path,
                    "PowerShell scripts are analysed by the Windows runner in "
                    "reusable-python-ci.yml, not by this Linux Dagger check",
                )
            else:
                report = script.unsupported_report(
                    scan_path,
                    "Other script types are out of scope for this check; "
                    "only Workflow files, POSIX shell scripts and PowerShell modules are analysed",
                )
            reports.append(report)
        return json.dumps(
            {"schema_version": 1, "reports": reports},
            ensure_ascii=False,
            sort_keys=True,
        )

    async def _run_static(
        self,
        source: dagger.Directory,
        python_version: str,
        policy: dict,
        native_output: str = "",
        git_token: dagger.Secret | None = None,
        dependency_packages: dagger.Directory | None = None,
    ) -> dict:
        """Execute one pinned scanner once and classify what it already produced.

        uvx builds the scanner environment on its own, so the consumer's own
        dependencies, pyproject.toml and uv.lock stay untouched. The optional
        second exec only reads a file the scan already wrote; it never scans.
        """
        tool = policy["tool"]
        container = self._uv_container(
            source,
            python_version,
            git_token=git_token,
            dependency_packages=dependency_packages,
            system_packages=("libatomic1",) if tool == "pyright" else (),
        )
        if tool == "pyright":
            # Libatomic1 is installed on the base image in _uv_container before
            # mounting source; caching the Node download avoids repeat fetches.
            container = container.with_mounted_cache(
                "/root/.cache/pyright-python",
                dag.cache_volume("engineering-ci-pyright-node"),
            )
        result = container.with_exec(
            static.runner_prefix(tool, policy["pinned_version"])
            + static.ARGUMENTS[tool](policy, native_output),
            expect=dagger.ReturnType.ANY,
        )
        stdout = await result.stdout()
        stderr = await result.stderr()
        native_raw = ""
        if native_output:
            native_raw = await result.with_exec(
                ["python", "-c", _READ_OPTIONAL, native_output],
                expect=dagger.ReturnType.ANY,
            ).stdout()
        elif tool in static.STDOUT_JSON_TOOLS:
            # Pyright (--outputjson) and Pylint (--output-format=json) emit their
            # structured report directly to stdout from the same execution.
            native_raw = stdout
        return static.static_report(
            policy,
            await result.exit_code(),
            stdout,
            stderr,
            native_raw,
        )

    @function
    async def python_vulture(
        self,
        source: _FilteredSource,
        python_version: str,
        scan_path: str = ".",
        min_confidence: int = 0,
        exclude: str = "",
        whitelist_path: str = "",
        git_token: dagger.Secret | None = None,
    ) -> str:
        """Report Vulture dead-code findings as JSON without gating CI.

        outcome is clean, findings, not_executed or failure, and reason carries
        the native exit-code semantics. Callers enforce by raising on findings.
        """
        report = await self._run_static(
            source,
            python_version,
            static.vulture_policy(scan_path, min_confidence, exclude, whitelist_path),
            git_token=git_token,
        )
        return json.dumps(report, ensure_ascii=False, sort_keys=True)

    @function
    async def tach_check(
        self,
        source: _FilteredSource,
    ) -> str:
        """Run pinned Tach inside the existing uv image; Source keeps non-Python files."""
        container = (
            dag.container()
            .from_(tooling.UV_IMAGE)
            .with_env_variable("UV_NO_MANAGED_PYTHON", "1")
            .with_env_variable("UV_PYTHON_DOWNLOADS", "0")
            .with_mounted_cache(
                "/root/.cache/uv",
                dag.cache_volume("engineering-ci-uv-native-tools"),
            )
            .with_directory("/workspace", self._bound_public_source(source))
            .with_workdir("/workspace")
        )
        result = container.with_exec(
            ["uvx", "--from", f"tach=={tooling.TACH_VERSION}", "tach", "check"],
            expect=dagger.ReturnType.ANY,
        )
        exit_code = await result.exit_code()
        return json.dumps(
            {
                "tool": "tach",
                "version": tooling.TACH_VERSION,
                "status": "passed" if exit_code == 0 else "failed",
                "exit_code": exit_code,
                "stdout": await result.stdout(),
                "stderr": await result.stderr(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    async def _lychee_container(self) -> tuple[dagger.Container, dict | None]:
        """Install pinned Lychee inside the cached uv image."""
        download = (
            "import io,os,tarfile,urllib.request;"
            f"data=urllib.request.urlopen({tooling.LYCHEE_ASSET!r}).read();"
            "tar=tarfile.open(fileobj=io.BytesIO(data));"
            "member=tar.extractfile('lychee-x86_64-unknown-linux-gnu/lychee');"
            "open('/usr/local/bin/lychee','wb').write(member.read());"
            "os.chmod('/usr/local/bin/lychee',0o755)"
        )
        installed = (
            dag.container()
            .from_(tooling.UV_IMAGE)
            .with_exec(["python", "-c", download], expect=dagger.ReturnType.ANY)
        )
        install_exit = await installed.exit_code()
        if install_exit != 0:
            return installed, {
                "tool": "lychee",
                "version": tooling.LYCHEE_VERSION,
                "status": "failed",
                "exit_code": install_exit,
                "stdout": await installed.stdout(),
                "stderr": await installed.stderr(),
                "reason": "tool_install_failed",
            }
        return installed, None

    async def _run_lychee(
        self,
        source: dagger.Directory,
        argv_tail: list[str],
        *,
        installed: dagger.Container | None = None,
        capture=None,
        _bound_source: dagger.Directory | None = None,
        _source_excludes: list[str] | tuple[str, ...] | None = None,
    ) -> dict:
        """Run pinned Lychee inside a container over an immutable Directory."""
        if installed is None:
            installed, error = await self._lychee_container()
            if error is not None:
                return {
                    "exit_code": 127,
                    "stdout": error.get("stdout", ""),
                    "stderr": (
                        f"{error.get('reason', 'tool_install_failed')}: "
                        f"{error.get('stderr', '')}"
                    ).strip(),
                }
        return await link_check.run(
            dag,
            installed,
            source,
            argv_tail,
            capture=capture or _exec_capture,
            source_noise=tooling.SOURCE_NOISE,
            _bound_source=_bound_source,
            _source_excludes=_source_excludes,
        )

    def _git_container(self, source: dagger.Directory) -> dagger.Container:
        return (
            prepare_private_git(dag.container().from_(tooling.UV_IMAGE))
            .with_directory("/workspace", source)
            .with_workdir("/workspace")
        )

    async def _run_git(
        self,
        source: dagger.Directory,
        argv_tail: list[str],
        *,
        capture=None,
    ) -> dict:
        """Run git inside a container when the Python module lacks git."""
        argv = ["git", "-c", "safe.directory=/workspace", *argv_tail]
        result = await (capture or _exec_capture)(self._git_container(source), argv)
        result["observation"] = {
            "execution_location": "dagger_container",
            "freshness_policy": "native_exec_cache",
            "process_started_at": result["process_started_at"],
            "process_finished_at": result["process_finished_at"],
            "argv": argv,
            "origin_execution_attributed": False,
        }
        return result

    async def _run_index_whitespace(self, source, git, paths):
        from engineering_ci import git_source

        # Use the module's installed code and the actual Git container. The
        # input Directory supplies only the repository bytes and object store.
        helper = "/tmp/eci-git-source.py"
        output = await (
            self._git_container(source)
            .with_new_file(
                helper, Path(git_source.__file__).read_text(encoding="utf-8")
            )
            .with_exec(
                [
                    "python",
                    "-I",
                    "-c",
                    (
                        "import json, runpy, sys\n"
                        "run = runpy.run_path(sys.argv[1])['run_exported_index_whitespace']\n"
                        "print(json.dumps(run('.', **json.load(sys.stdin))))"
                    ),
                    helper,
                ],
                stdin=json.dumps({"git": git, "paths": paths}),
            )
            .stdout()
        )
        return json.loads(output)

    async def _run_dagger_prerequisite(self, source, name, spec, *, capture=None):
        from .native_prerequisite import run

        return await run(dag, source, name, spec, capture=capture or _exec_capture)

    @function
    async def lychee_check(
        self,
        source: _FilteredSource,
        files: str,
    ) -> str:
        """Install and run pinned Lychee inside the cached uv image."""
        targets = [line.strip() for line in files.splitlines() if line.strip()]
        if not targets:
            return json.dumps(
                {
                    "tool": "lychee",
                    "version": tooling.LYCHEE_VERSION,
                    "status": "not_applicable",
                    "exit_code": None,
                    "stdout": "",
                    "stderr": "no applicable documents",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        installed, error = await self._lychee_container()
        if error is not None:
            return json.dumps(error, ensure_ascii=False, sort_keys=True)
        result = await self._run_lychee(
            source,
            ["--offline", "--no-progress", *targets],
            installed=installed,
        )
        exit_code = result["exit_code"]
        return json.dumps(
            {
                "tool": "lychee",
                "version": tooling.LYCHEE_VERSION,
                "status": "passed" if exit_code == 0 else "failed",
                "exit_code": exit_code,
                "stdout": result["stdout"],
                "stderr": result["stderr"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    @function
    async def python_pyright(
        self,
        source: _FilteredSource,
        python_version: str,
        scan_path: str = ".",
        config: str = "",
        level: str = "",
        target_python_version: str = "",
        use_project_environment: bool = True,
        git_token: dagger.Secret | None = None,
    ) -> str:
        """Type-check with Pyright in its own environment, reading consumer config.

        The scanner itself runs isolated from the consumer's dependencies, while
        import resolution defaults to the environment this check already synced.
        Unresolved imports are kept as diagnostics and reported as an explicit
        limitation; an incomplete type pass is never presented as a clean one.
        """
        report = await self._run_static(
            source,
            python_version,
            static.pyright_policy(
                scan_path,
                config,
                level,
                _PROJECT_ENVIRONMENT if use_project_environment else "",
                target_python_version,
            ),
            git_token=git_token,
        )
        return json.dumps(report, ensure_ascii=False, sort_keys=True)

    @function
    async def python_deptry(
        self,
        source: _FilteredSource,
        python_version: str,
        scan_path: str = "src",
        config: str = "",
        extend_exclude: str = "",
        per_rule_ignores: str = "",
        git_token: dagger.Secret | None = None,
    ) -> str:
        """Compare declared dependencies with imported modules using native deptry.

        The structured report and the exit code come from one execution; an
        exception exit stays a failure instead of masquerading as a finding.
        """
        report = await self._run_static(
            source,
            python_version,
            static.deptry_policy(scan_path, config, extend_exclude, per_rule_ignores),
            native_output=_DEPTRY_JSON,
            git_token=git_token,
        )
        return json.dumps(report, ensure_ascii=False, sort_keys=True)

    @function
    async def python_pylint(
        self,
        source: _FilteredSource,
        python_version: str,
        scan_paths: list[str] | None = None,
        min_similarity_lines: int = 4,
        rcfile: str = "",
        ignore_paths: list[str] | None = None,
        git_token: dagger.Secret | None = None,
    ) -> str:
        """Detect R0801 duplicate code across files; Ruff keeps covering the rest.

        Only duplicate-code is enabled so this does not repeat rules Ruff
        already enforces for the same sources.
        """
        report = await self._run_static(
            source,
            python_version,
            static.pylint_policy(
                scan_paths, min_similarity_lines, rcfile, ignore_paths
            ),
            git_token=git_token,
        )
        return json.dumps(report, ensure_ascii=False, sort_keys=True)

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
        dependency_packages: dagger.Directory | None = None,
        _targets: list[str] | None = None,
        _resolved_targets: list[str] | None = None,
        _bound_source: dagger.Directory | None = None,
    ) -> _PytestExecution:
        """Execute once and collect JUnit evidence even when tests fail."""
        report_path = "/tmp/engineering-ci-pytest.xml"
        if not 1 <= max_failures <= 50:
            raise ValueError("max_failures must be between 1 and 50")
        policy = test_policy(test_path, markers, coverage_source, min_coverage)
        prepared = (
            _prepared_container
            if _prepared_container is not None
            else self._uv_container(
                source,
                python_version,
                git_token=git_token,
                dependency_packages=dependency_packages,
                _bound_source=_bound_source,
            )
        )
        arguments = pytest_arguments(policy, report_path)
        resolved_targets: list[str] | None = None
        if _targets is not None and _resolved_targets is not None:
            raise ValueError("cannot specify both _targets and _resolved_targets")
        if _resolved_targets is not None:
            from engineering_ci import selection

            if not _resolved_targets:
                raise ValueError("selected pytest targets must not be empty")
            selected = []
            for target in _resolved_targets:
                ok, refusal = selection._validate_bound_pytest_cli_target(target)
                if not ok:
                    raise ValueError(
                        f"selected pytest target refused ({refusal}): {target}"
                    )
                test_policy(target)
                selected.append(target)
            resolved_targets = selected
            # Pre-bound CLI paths from plan_execution._tests avoid re-export and
            # avoid re-interpreting path directories (e.g. ./chosen) as dotted modules.
            arguments = (
                ["uv", "run", "--no-sync", "python", "-m", "pytest"]
                + arguments[4:-1]
                + ["--", *selected]
            )
        elif _targets is not None:
            from engineering_ci import selection

            if not _targets:
                raise ValueError("selected pytest targets must not be empty")
            bound_dir = (
                _bound_source
                if _bound_source is not None
                else self._bound_public_source(source)
            )
            with tempfile.TemporaryDirectory() as resolve_tmp:
                if hasattr(bound_dir, "export"):
                    await bound_dir.export(resolve_tmp)
                resolved_targets, refusal, bad_target = (
                    selection._resolve_pytest_targets(_targets, Path(resolve_tmp))
                )
            if refusal:
                raise ValueError(
                    f"selected pytest target refused ({refusal}): {bad_target}"
                )
            selected = []
            for target in resolved_targets:
                test_policy(target)
                selected.append(target)
            # Running pytest as a module keeps cwd (/workspace) on sys.path while
            # "--" and bound repository paths block external PYTHONPATH shadowing.
            arguments = (
                ["uv", "run", "--no-sync", "python", "-m", "pytest"]
                + arguments[4:-1]
                + ["--", *selected]
            )
        if _matrix_runtime:
            prepared = prepared.with_new_file(
                "/tmp/engineering-ci-matrix-runtime.py",
                Path(__file__)
                .with_name("pytest_matrix_runtime.py")
                .read_text(encoding="utf-8"),
            )
            arguments = [
                "uv",
                "run",
                "--no-sync",
                "python",
                "/tmp/engineering-ci-matrix-runtime.py",
                python_version,
                *arguments[4:],
            ]
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
        evidence = json.loads(
            await reader.with_exec(
                [
                    "python",
                    "/tmp/engineering-ci-pytest-ci.py",
                    "read-xml",
                    report_path,
                ]
            ).stdout()
        )
        xml = evidence["xml"]
        report = summarize_pytest(
            xml, exit_code, output + "\n" + stderr, max_failures=max_failures
        )
        if resolved_targets is not None:
            report["selected_targets"] = list(resolved_targets)
        if evidence["diagnostic"]:
            report["diagnostic"] = evidence["diagnostic"]
        coverage = None
        if coverage_source:
            reader = reader.with_new_file(
                "/tmp/pytest_quality.py",
                Path(__file__)
                .with_name("pytest_quality.py")
                .read_text(encoding="utf-8"),
            )
            coverage = json.loads(
                await reader.with_exec(
                    [
                        "python",
                        "/tmp/engineering-ci-pytest-ci.py",
                        "read-coverage",
                        "/tmp/engineering-ci-coverage.xml",
                        "/tmp/engineering-ci-coverage.json",
                    ]
                ).stdout()
            )
        if markers or coverage_source:
            report["quality"] = evaluate_quality(policy, exit_code, coverage)
        runtime = None
        if _matrix_runtime:
            runtime = json.loads(
                await reader.with_exec(
                    [
                        "python",
                        "/tmp/engineering-ci-pytest-ci.py",
                        "read-runtime",
                        "/tmp/engineering-ci-pytest-runtime.json",
                    ]
                ).stdout()
            )
        packages_receipt = (
            await container.file(
                "/tmp/engineering-ci-dependency-packages.json"
            ).contents()
            if dependency_packages is not None
            else None
        )
        if packages_receipt is not None:
            receipt = json.loads(packages_receipt)
            receipt["source_digest"] = await source.digest()
            packages_receipt = json.dumps(receipt, sort_keys=True)
        return _PytestExecution(
            exit_code,
            report,
            output,
            xml,
            container,
            policy if markers or coverage_source else None,
            coverage,
            runtime,
            packages_receipt,
        )

    def _require_pytest_success(self, execution: _PytestExecution) -> str:
        if execution.exit_code != 0:
            summary = failure_summary(execution.report)
            if execution.report.get("quality"):
                summary += "\n" + quality_summary(execution.report["quality"])
            raise RuntimeError(summary)
        if (
            execution.report.get("quality")
            and not execution.report["quality"]["gate"]["passed"]
        ):
            raise RuntimeError(quality_summary(execution.report["quality"]))
        return execution.output

    def _pytest_bundle(self, execution: _PytestExecution) -> dagger.Directory:
        directory = (
            dag.directory()
            .with_new_file("exit-code.txt", str(execution.exit_code) + "\n")
            .with_new_file(
                "report.json",
                json.dumps(execution.report, ensure_ascii=False, sort_keys=True),
            )
            .with_new_file("summary.md", markdown_summary(execution.report))
            .with_new_file(
                "ci.py",
                Path(__file__).with_name("pytest_ci.py").read_text(encoding="utf-8"),
            )
        )
        if execution.dependency_packages is not None:
            directory = directory.with_new_file(
                "dependency-packages.json", execution.dependency_packages
            )
        if execution.xml:
            directory = directory.with_new_file("junit.xml", execution.xml)
        if execution.policy:
            directory = directory.with_new_file(
                "quality-policy.json", json.dumps(execution.policy)
            ).with_new_file(
                "pytest_quality.py",
                Path(__file__)
                .with_name("pytest_quality.py")
                .read_text(encoding="utf-8"),
            )
        if execution.coverage:
            for key, filename in (("xml", "coverage.xml"), ("json", "coverage.json")):
                if execution.coverage[key]:
                    directory = directory.with_new_file(
                        filename, execution.coverage[key]
                    )
        return directory

    @function
    async def python_test_artifacts(
        self,
        source: _FilteredSource,
        python_version: str,
        test_path: str = "tests",
        max_failures: int = 10,
        markers: str = "",
        coverage_source: str = "",
        min_coverage: str = "",
        git_token: dagger.Secret | None = None,
        dependency_packages: dagger.Directory | None = None,
    ) -> dagger.Directory:
        """Run pytest once; export original status, JSON, XML and summary even on failure.

        CI must use ci.py gate: original exit code plus explicit coverage policy.
        Do not also call python_test on this source.
        """
        execution = await self._run_pytest(
            source,
            python_version,
            test_path,
            max_failures,
            markers,
            coverage_source,
            min_coverage,
            git_token=git_token,
            dependency_packages=dependency_packages,
        )
        return self._pytest_bundle(execution)

    @function
    async def python_test(
        self,
        source: _FilteredSource,
        python_version: str,
        test_path: str = "tests",
        markers: str = "",
        coverage_source: str = "",
        min_coverage: str = "",
        git_token: dagger.Secret | None = None,
        dependency_packages: dagger.Directory | None = None,
    ) -> str:
        """Run pytest; fail CI with a bounded JUnit-based failure summary."""
        execution = await self._run_pytest(
            source,
            python_version,
            test_path,
            markers=markers,
            coverage_source=coverage_source,
            min_coverage=min_coverage,
            git_token=git_token,
            dependency_packages=dependency_packages,
        )
        return self._require_pytest_success(execution)

    @function
    async def python_test_report(
        self,
        source: _FilteredSource,
        python_version: str,
        test_path: str = "tests",
        max_failures: int = 10,
        markers: str = "",
        coverage_source: str = "",
        min_coverage: str = "",
        git_token: dagger.Secret | None = None,
        dependency_packages: dagger.Directory | None = None,
    ) -> str:
        """Return JSON summary for both passing and failing pytest runs.

        This informational function does NOT gate CI. Use python_test for
        failure propagation; report.status and exit_code are explicit pytest facts.
        Optional report.quality contains the separate coverage and overall gate.
        """
        execution = await self._run_pytest(
            source,
            python_version,
            test_path,
            max_failures=max_failures,
            markers=markers,
            coverage_source=coverage_source,
            min_coverage=min_coverage,
            git_token=git_token,
            dependency_packages=dependency_packages,
        )
        return json.dumps(execution.report, ensure_ascii=False, sort_keys=True)

    async def _run_pytest_matrix(
        self,
        source,
        python_versions,
        test_path="tests",
        max_failures=10,
        markers="",
        coverage_source="",
        min_coverage="",
        max_concurrency=1,
    ):
        policy = test_policy(test_path, markers, coverage_source, min_coverage)
        plan = matrix_policy(python_versions, max_concurrency, max_failures, policy)
        try:
            project_text = await source.file("pyproject.toml").contents()
            if len(project_text) > 1024 * 1024:
                raise ValueError("pyproject.toml exceeds size limit")
            requirement = (
                tomllib.loads(project_text)
                .get("project", {})
                .get("requires-python", "")
            )
            supports_python(
                requirement, "3.14.0"
            )  # validate specifier before any container work
        except Exception as exc:  # noqa: BLE001 - configuration errors remain explicit failing matrix rows
            rows = [
                (
                    v,
                    {
                        "phase": "configuration",
                        "failure_category": "configuration_error",
                        "diagnostic": f"Cannot read project.requires-python ({type(exc).__name__})",
                    },
                    {},
                    None,
                )
                for v in plan["requested_versions"]
            ]
            return _MatrixExecution(
                plan, aggregate(plan, [version_result(*r[:3]) for r in rows]), rows
            )

        semaphore = asyncio.Semaphore(max_concurrency)

        async def run_version(version):
            async with semaphore:
                started = time.perf_counter()
                facts = {
                    "schema_version": 1,
                    "phase": "environment",
                    "process_exit_code": None,
                    "failure_category": None,
                    "requires_python": requirement,
                }
                runtime, execution = {}, None
                try:
                    base = self._uv_container(
                        source, version, sync_dependencies=False
                    ).with_new_file(
                        "/tmp/engineering-ci-matrix-runtime.py",
                        Path(__file__)
                        .with_name("pytest_matrix_runtime.py")
                        .read_text(encoding="utf-8"),
                    )
                    runtime = json.loads(
                        await base.with_exec(
                            [
                                "python",
                                "/tmp/engineering-ci-matrix-runtime.py",
                                "--probe",
                            ]
                        ).stdout()
                    )
                    if runtime["actual_python"].split(".")[:2] != version.split("."):
                        facts.update(
                            failure_category="interpreter_mismatch",
                            diagnostic="Base interpreter differs from requested version",
                        )
                    elif not supports_python(requirement, runtime["actual_python"]):
                        facts.update(
                            phase="compatibility",
                            failure_category="python_unsupported",
                            diagnostic=f"Python {runtime['actual_python']} does not satisfy requires-python {requirement}",
                        )
                    else:
                        facts["validated_python"] = runtime["actual_python"]
                        facts["phase"] = "dependencies"
                        synced = base.with_exec(
                            [
                                "uv",
                                "sync",
                                "--locked",
                                "--refresh",
                                "--python",
                                version,
                            ],
                            expect=dagger.ReturnType.ANY,
                        )
                        code = await synced.exit_code()
                        if code:
                            facts.update(
                                process_exit_code=code,
                                failure_category="dependency_installation",
                                diagnostic=f"uv sync --locked failed (exit {code}); pytest was not started",
                            )
                        else:
                            facts["phase"] = "pytest"
                            execution = await self._run_pytest(
                                source,
                                version,
                                test_path,
                                max_failures,
                                markers,
                                coverage_source,
                                min_coverage,
                                _prepared_container=synced,
                                _matrix_runtime=True,
                            )
                            runtime = execution.runtime or {}
                            facts["process_exit_code"] = execution.exit_code
                except Exception as exc:  # noqa: BLE001 - preserve failed rows and continue other required versions
                    phase = facts["phase"]
                    facts.update(
                        failure_category={
                            "environment": "environment_initialization",
                            "dependencies": "dependency_installation",
                        }.get(phase, "report_unavailable"),
                        diagnostic=f"{phase} failed ({type(exc).__name__}); no automatic retry",
                    )
                    if phase == "pytest":
                        facts["phase"] = "collection"
                facts["elapsed_seconds"] = round(time.perf_counter() - started, 3)
                return version, facts, runtime, execution

        # A failed version is a result, not a reason to cancel remaining versions.
        rows = await asyncio.gather(
            *(run_version(v) for v in plan["requested_versions"])
        )
        report = aggregate(
            plan,
            [version_result(v, f, r, e.report if e else None) for v, f, r, e in rows],
        )
        return _MatrixExecution(plan, report, rows)

    def _require_matrix_success(self, execution):
        if execution.report["overall_status"] != "passed":
            details = []
            for row in execution.report["versions"]:
                if row["status"] != "passed":
                    detail = (
                        f"Python {row['requested_python']}: {row['failure_category']}; "
                        f"pytest exit {row['pytest_exit_code']}; {row.get('diagnostic') or ''}"
                    )
                    if row.get("pytest"):
                        detail += "\n" + failure_summary(row["pytest"])
                    details.append(detail)
            raise RuntimeError(
                "Python compatibility matrix failed\n" + "\n".join(details)
            )
        return matrix_summary(execution.report)

    def _matrix_bundle(self, execution):
        directory = (
            dag.directory()
            .with_new_file(
                "matrix-policy.json", json.dumps(execution.plan, sort_keys=True)
            )
            .with_new_file("matrix.json", json.dumps(execution.report, sort_keys=True))
            .with_new_file("summary.md", matrix_summary(execution.report))
        )
        for filename in (
            "pytest_ci.py",
            "pytest_matrix.py",
            "pytest_report.py",
            "pytest_quality.py",
        ):
            directory = directory.with_new_file(
                "ci.py" if filename == "pytest_ci.py" else filename,
                Path(__file__).with_name(filename).read_text(encoding="utf-8"),
            )
        from engineering_ci import pytest_contracts

        directory = directory.with_new_file(
            "pytest_contracts.py",
            Path(pytest_contracts.__file__).read_text(encoding="utf-8"),
        )
        for version, facts, runtime, pytest in execution.rows:
            folder = dag.directory()
            if pytest is not None and runtime.get("pytest_started"):
                folder = self._pytest_bundle(pytest)
            folder = folder.with_new_file(
                "execution.json", json.dumps(facts, sort_keys=True)
            ).with_new_file("runtime.json", json.dumps(runtime, sort_keys=True))
            directory = directory.with_directory(f"versions/{version}", folder)
        return directory

    @function
    async def python_test_matrix(
        self,
        source: _FilteredSource,
        python_versions: list[str],
        test_path: str = "tests",
        max_failures: int = 10,
        markers: str = "",
        coverage_source: str = "",
        min_coverage: str = "",
        max_concurrency: int = 1,
    ) -> str:
        """Strict check: all requested interpreters, pytest executions and enabled gates must pass."""
        execution = await self._run_pytest_matrix(
            source,
            python_versions,
            test_path,
            max_failures,
            markers,
            coverage_source,
            min_coverage,
            max_concurrency,
        )
        return self._require_matrix_success(execution)

    @function
    async def python_test_matrix_report(
        self,
        source: _FilteredSource,
        python_versions: list[str],
        test_path: str = "tests",
        max_failures: int = 10,
        markers: str = "",
        coverage_source: str = "",
        min_coverage: str = "",
        max_concurrency: int = 1,
    ) -> str:
        """Informational matrix JSON; failed versions remain present. Does not gate CI."""
        execution = await self._run_pytest_matrix(
            source,
            python_versions,
            test_path,
            max_failures,
            markers,
            coverage_source,
            min_coverage,
            max_concurrency,
        )
        return json.dumps(execution.report, sort_keys=True)

    @function
    async def python_test_matrix_artifacts(
        self,
        source: _FilteredSource,
        python_versions: list[str],
        test_path: str = "tests",
        max_failures: int = 10,
        markers: str = "",
        coverage_source: str = "",
        min_coverage: str = "",
        max_concurrency: int = 1,
    ) -> dagger.Directory:
        """Execute once per interpreter; export matrix and facts. Gate with the exported ci.py."""
        execution = await self._run_pytest_matrix(
            source,
            python_versions,
            test_path,
            max_failures,
            markers,
            coverage_source,
            min_coverage,
            max_concurrency,
        )
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
        if (
            not report["report_available"]
            or report["status"] != "passed"
            or report["exit_code"] != 0
            or report["counts"]["tests"] == 0
        ):
            raise ValueError(f"Unexpected passing test report: {report}")
        print(self._require_pytest_success(execution))

    async def _matrix_fixture(self, source, scenario, concurrency=1):
        versions = ["3.12", "3.13", "3.14"]
        coverage_source, threshold = "", ""
        if scenario == "version-fail":
            source = source.with_new_file(
                "tests/test_version_negative.py",
                "import sys\ndef test_version_specific_failure():\n    assert sys.version_info[:2] != (3, 14)\n",
            )
        elif scenario == "unsupported":
            versions = ["3.11", *versions]
        elif scenario == "coverage-low":
            source = source.with_new_file(
                "matrix_sample.py",
                await source.file("matrix_sample.py").contents()
                + "\n\ndef extra():\n    return 42\n",
            )
            source = source.with_new_file(
                "tests/test_extra.py",
                "import sys\nfrom matrix_sample import extra\ndef test_extra():\n"
                "    if sys.version_info[:2] != (3, 14):\n        assert extra() == 42\n",
            )
            coverage_source, threshold = "matrix_sample", "100"
        elif scenario == "dependency-error":
            source = source.with_new_file(
                "uv.lock", "version = 1\nrequires-python = '>=3.12'\npackage = []\n"
            )
        elif scenario != "pass":
            raise ValueError("Unknown matrix fixture scenario")
        return await self._run_pytest_matrix(
            source,
            versions,
            coverage_source=coverage_source,
            min_coverage=threshold,
            max_concurrency=concurrency,
        )

    async def _matrix_fixture_bundle(self, execution):
        bundle = self._matrix_bundle(execution)
        for version, _, _, pytest in execution.rows:
            if pytest is not None and pytest.runtime.get("pytest_started"):
                counter = await pytest.container.file(
                    "/tmp/engineering-ci-pytest-invocations"
                ).contents()
                if counter != "1":
                    raise RuntimeError(f"Python {version}: pytest ran {counter} times")
                bundle = bundle.with_new_file(
                    f"versions/{version}/fixture-invocations.txt", counter
                ).with_new_file(
                    f"versions/{version}/fixture-timing.json",
                    await pytest.container.file(
                        "/tmp/engineering-ci-fixture-timing.json"
                    ).contents(),
                )
        return bundle

    @function
    async def fixture_matrix_artifacts(
        self,
        source: Annotated[dagger.Directory, DefaultPath("/tests/fixtures/matrix-app")],
        scenario: str = "pass",
    ) -> dagger.Directory:
        """Real matrix probe; export original evidence even when a version fails."""
        if scenario == "performance":
            return await self.fixture_matrix_performance(source)
        execution = await self._matrix_fixture(source, scenario)
        return await self._matrix_fixture_bundle(execution)

    @function
    @check
    async def fixture_python_matrix(
        self,
        source: Annotated[dagger.Directory, DefaultPath("/tests/fixtures/matrix-app")],
    ) -> None:
        """Verify real interpreters, per-version failures, coverage and original-fact gate."""
        verified = []
        for scenario in (
            "pass",
            "version-fail",
            "unsupported",
            "coverage-low",
            "dependency-error",
        ):
            execution = await self._matrix_fixture(source, scenario)
            rows = {r["requested_python"]: r for r in execution.report["versions"]}
            for version in ("3.12", "3.13", "3.14"):
                row = rows[version]
                expected = (
                    "dependency_installation"
                    if scenario == "dependency-error"
                    else "pytest_failure"
                    if scenario == "version-fail" and version == "3.14"
                    else "coverage_failure"
                    if scenario == "coverage-low" and version == "3.14"
                    else None
                )
                if row["failure_category"] != expected:
                    raise RuntimeError(
                        f"{scenario}: unexpected Python {version} result: {row}"
                    )
                if expected != "dependency_installation" and (
                    not row["actual_python"].startswith(version + ".")
                    or not row["runtime"]["pytest_started"]
                ):
                    raise RuntimeError(f"Missing real pytest runtime: {row}")
                if expected == "coverage_failure" and row["pytest_exit_code"] != 0:
                    raise RuntimeError(
                        "Coverage failure changed original pytest exit code"
                    )
            if (
                scenario == "unsupported"
                and rows["3.11"]["failure_category"] != "python_unsupported"
            ):
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
            delivery = (
                dag.container()
                .from_("python:3.14-slim")
                .with_directory("/reports", bundle)
                .with_workdir("/reports")
            )
            code = await delivery.with_exec(
                ["python", "ci.py", "gate", "/reports"], expect=dagger.ReturnType.ANY
            ).exit_code()
            if (code == 0) != (scenario == "pass"):
                raise RuntimeError(f"Original-fact gate disagrees for {scenario}")
            await delivery.with_exec(
                ["python", "ci.py", "summary", "/reports", "/tmp/summary.md"]
            ).sync()
            verified.append(scenario)
        print("PASS: real matrix and delivery gate: " + ", ".join(verified))

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
            source=failing,
            python_version="3.14",
            test_path="tests",
        )
        report = execution.report
        if (
            not report["report_available"]
            or report["status"] != "failed"
            or report["exit_code"] != 1
            or report["counts"]["failures"] != 1
            or not any(
                item["category"] == "assertion_failure" for item in report["failures"]
            )
        ):
            raise ValueError(f"Unexpected negative test report: {report}")
        try:
            self._require_pytest_success(execution)
        except RuntimeError as exc:
            summary = str(exc)
            if not all(
                evidence in summary
                for evidence in (
                    "pytest exit 1",
                    "test_intentional_failure",
                    "assert 1 == 2",
                )
            ):
                raise ValueError(
                    f"Unexpected pytest failure summary: {summary}"
                ) from exc
        else:
            raise ValueError(
                "python_test incorrectly accepted the failing pytest fixture"
            )
        print(
            "PASS: JSON reported assertion failure with exit 1; python_test rejected it"
        )

    @function
    async def fixture_pytest_artifacts(
        self,
        source: Annotated[dagger.Directory, DefaultPath("/tests/fixtures/python-app")],
        scenario: str = "pass",
    ) -> dagger.Directory:
        """Small real CI probes; intentional failures are injected, never committed."""
        if scenario not in (
            "pass",
            "assertion",
            "missing-xml",
            "invalid-xml",
            "missing-xml-pass",
        ):
            raise ValueError("Unknown pytest report fixture scenario")
        probe = "from pathlib import Path\n\ndef pytest_sessionstart(session):\n"
        probe += "    p = Path('/tmp/engineering-ci-pytest-invocations')\n"
        probe += (
            "    p.write_text(str(int(p.read_text()) + 1) if p.exists() else '1')\n"
        )
        if scenario != "pass" and scenario != "missing-xml-pass":
            source = source.with_new_file(
                "tests/test_report_negative.py",
                "def test_intentional_failure():\n    assert 1 == 2\n",
            )
        if scenario.startswith("missing-xml") or scenario == "invalid-xml":
            probe += "\nimport atexit\n"
            if scenario == "invalid-xml":
                probe += "atexit.register(lambda: Path('/tmp/engineering-ci-pytest.xml').write_text('<testsuite'))\n"
            else:
                probe += "atexit.register(lambda: Path('/tmp/engineering-ci-pytest.xml').unlink(missing_ok=True))\n"
        source = source.with_new_file("conftest.py", probe)
        execution = await self._run_pytest(source, "3.14", "tests")
        invocations = await execution.container.file(
            "/tmp/engineering-ci-pytest-invocations"
        ).contents()
        if invocations != "1":
            raise RuntimeError(f"Pytest executed {invocations} times instead of once")
        return self._pytest_bundle(execution).with_new_file(
            "fixture-invocations.txt", invocations
        )

    async def _quality_fixture_execution(
        self, source, missing_dependency_source, scenario
    ):
        scenarios = {
            "baseline",
            "coverage-pass",
            "coverage-low",
            "coverage-test-fail",
            "coverage-both-fail",
            "markers",
            "markers-empty",
            "invalid-markers",
            "coverage-missing-dependency",
            "coverage-report-missing",
            "coverage-report-invalid",
            "coverage-source-missing",
            "invalid-path",
            "invalid-threshold",
        }
        if scenario not in scenarios:
            raise ValueError("Unknown pytest quality fixture scenario")
        coverage_source = "" if scenario == "baseline" else "quality_sample"
        threshold = (
            ""
            if scenario == "baseline"
            else "100"
            if scenario in ("coverage-low", "coverage-both-fail")
            else "50"
            if scenario.startswith("markers")
            else "80"
        )
        markers = (
            "fast"
            if scenario == "markers"
            else "not fast and not slow"
            if scenario == "markers-empty"
            else "fast and ("
            if scenario == "invalid-markers"
            else ""
        )
        test_path = "missing-tests" if scenario == "invalid-path" else "tests"
        if scenario == "coverage-missing-dependency":
            source, coverage_source = missing_dependency_source, "tests"
        elif scenario == "coverage-source-missing":
            coverage_source = "missing_source_module"
        elif scenario == "invalid-threshold":
            threshold = "101"
        if scenario in ("coverage-test-fail", "coverage-both-fail"):
            source = source.with_new_file(
                "tests/test_intentional_failure.py",
                "def test_intentional_failure():\n    assert 1 == 2\n",
            )
        probe = """import json
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
"""
        if scenario in ("coverage-report-missing", "coverage-report-invalid"):
            probe += "\nimport atexit\n"
            if scenario == "coverage-report-missing":
                probe += "atexit.register(lambda: Path('/tmp/engineering-ci-coverage.json').unlink(missing_ok=True))\n"
            else:
                probe += "atexit.register(lambda: Path('/tmp/engineering-ci-coverage.json').write_text('{broken'))\n"
        source = source.with_new_file("conftest.py", probe)
        return await self._run_pytest(
            source,
            "3.14",
            test_path,
            markers=markers,
            coverage_source=coverage_source,
            min_coverage=threshold,
        )

    @function
    async def fixture_quality_artifacts(
        self,
        source: Annotated[
            dagger.Directory, DefaultPath("/tests/fixtures/coverage-app")
        ],
        missing_dependency_source: Annotated[
            dagger.Directory, DefaultPath("/tests/fixtures/python-app")
        ],
        scenario: str = "coverage-pass",
    ) -> dagger.Directory:
        """Real opt-in quality probes with execution count and session timing evidence."""
        execution = await self._quality_fixture_execution(
            source, missing_dependency_source, scenario
        )
        evidence = json.loads(
            await execution.container.with_exec(
                [
                    "python",
                    "-c",
                    (
                        "import json; from pathlib import Path; "
                        "p=Path('/tmp/engineering-ci-pytest-invocations'); "
                        "t=Path('/tmp/engineering-ci-fixture-timing.json'); "
                        "print(json.dumps({'count': p.read_text() if p.exists() else '0', "
                        "'timing': t.read_text() if t.exists() else '{}'}))"
                    ),
                ]
            ).stdout()
        )
        expected = "0" if scenario == "coverage-missing-dependency" else "1"
        if evidence["count"] != expected:
            raise RuntimeError(
                f"Expected {expected} pytest sessions; got {evidence['count']}"
            )
        return (
            self._pytest_bundle(execution)
            .with_new_file("fixture-invocations.txt", evidence["count"])
            .with_new_file("fixture-timing.json", evidence["timing"])
        )

    @function
    @check
    async def fixture_quality_gates(
        self,
        source: Annotated[
            dagger.Directory, DefaultPath("/tests/fixtures/coverage-app")
        ],
        missing_dependency_source: Annotated[
            dagger.Directory, DefaultPath("/tests/fixtures/python-app")
        ],
    ) -> None:
        """Verify real pytest and coverage failures independently, without duplicate suite runs."""
        results = []
        for scenario in (
            "baseline",
            "coverage-pass",
            "coverage-low",
            "coverage-test-fail",
            "coverage-both-fail",
            "markers",
            "markers-empty",
            "invalid-markers",
            "coverage-missing-dependency",
            "coverage-report-missing",
            "coverage-report-invalid",
            "coverage-source-missing",
            "invalid-path",
        ):
            execution = await self._quality_fixture_execution(
                source, missing_dependency_source, scenario
            )
            should_pass = scenario in ("baseline", "coverage-pass", "markers")
            try:
                self._require_pytest_success(execution)
            except RuntimeError:
                if should_pass:
                    raise
            else:
                if not should_pass:
                    raise RuntimeError(
                        f"Quality fixture unexpectedly passed: {scenario}"
                    )
            quality = execution.report.get("quality")
            if scenario == "coverage-low" and (
                execution.exit_code != 0
                or execution.report["counts"]["failures"] != 0
                or quality["coverage"]["status"] != "failed"
            ):
                raise RuntimeError("Coverage failure changed pytest execution facts")
            if scenario == "coverage-test-fail" and (
                execution.exit_code != 1 or quality["coverage"]["status"] != "passed"
            ):
                raise RuntimeError("Passing coverage hid a test failure")
            if scenario == "markers" and execution.report["counts"]["tests"] != 1:
                raise RuntimeError("Explicit marker selection did not select one test")
            results.append(
                {
                    "scenario": scenario,
                    "exit_code": execution.exit_code,
                    "quality": quality,
                    "counts": execution.report["counts"],
                }
            )
        try:
            await self._quality_fixture_execution(
                source, missing_dependency_source, "invalid-threshold"
            )
        except ValueError:
            pass
        else:
            raise RuntimeError("Invalid threshold was accepted")
        print(json.dumps(results, sort_keys=True))

    @function
    @check
    async def fixture_workflow_analysis(
        self,
        source: Annotated[
            dagger.Directory, DefaultPath("/tests/fixtures/workflow-app")
        ],
    ) -> None:
        """Run the pinned actionlint against committed clean and broken Workflows.

        This is what turns the documented actionlint assumptions into observed
        facts. A mismatch fails the check and prints what the tool actually
        returned, instead of leaving the assumption in place.
        """
        results = {}
        for name, scan_path, outcome in (
            ("clean", ".github/workflows/valid.yml", "clean"),
            ("broken", ".github/workflows/broken.yml", "findings"),
        ):
            report = await self._run_actionlint(
                source, script.actionlint_policy([scan_path])
            )
            if report["outcome"] != outcome:
                raise RuntimeError(
                    f"actionlint on {name} expected {outcome}, got "
                    f"{report['outcome']} ({report['reason']}) with exit "
                    f"{report['exit_code']}: {script.findings_summary(report)}"
                )
            results[name] = report

        broken = results["broken"]
        if not broken["findings"]:
            raise RuntimeError("actionlint reported no usable diagnostic")
        if not broken["stdout"].strip():
            raise RuntimeError("actionlint diagnostics were not preserved")
        alias = ".github/workflows/alias.yml"
        alias_report = await self._run_actionlint(
            source.with_symlink("valid.yml", alias), script.actionlint_policy([alias])
        )
        if (
            alias_report["outcome"] != "clean"
            or alias_report["target_sources"][0]["resolved_path"]
            != ".github/workflows/valid.yml"
            or alias_report["target_sources"][0]["sha256"]
            != results["clean"]["target_sources"][0]["sha256"]
        ):
            raise RuntimeError("actionlint did not observe the internal alias's bytes")
        invalid = ".github/workflows/invalid.yml"
        invalid_report = await self._run_actionlint(
            source.with_symlink("missing.yml", invalid),
            script.actionlint_policy([invalid]),
        )
        if (
            invalid_report["outcome"] != "not_executed"
            or invalid_report["exit_code"] is not None
        ):
            raise RuntimeError("An unavailable Directory target started actionlint")
        results["alias"] = alias_report
        results["missing"] = invalid_report
        version = await (
            dag.container()
            .from_("ghcr.io/astral-sh/uv:python3.14-trixie-slim")
            .with_mounted_cache(
                "/root/.cache/uv", dag.cache_volume("engineering-ci-uv-python3.14")
            )
            .with_env_variable("UV_LINK_MODE", "copy")
            .with_exec(
                script.runner_prefix("actionlint") + ["-version"],
                expect=dagger.ReturnType.ANY,
            )
            .stdout()
        )
        if script.ACTIONLINT_VERSION not in version:
            raise RuntimeError(
                f"actionlint did not report the pinned version "
                f"{script.ACTIONLINT_VERSION}: {version[:200]}"
            )
        print(
            f"PASS: actionlint {script.ACTIONLINT_VERSION}; clean exit 0, "
            f"broken exit {broken['exit_code']} with {broken['finding_count']} "
            "diagnostic(s) preserved"
        )

    async def _static_fixture_versions(self, source, python_version):
        """Confirm uvx resolved each pinned version instead of trusting it."""
        base_container = self._uv_container(source, python_version)
        pyright_container = self._uv_container(
            source,
            python_version,
            system_packages=("libatomic1",),
        ).with_mounted_cache(
            "/root/.cache/pyright-python",
            dag.cache_volume("engineering-ci-pyright-node"),
        )
        observed = {}
        for tool, pinned in static.SCANNER_VERSIONS.items():
            container = pyright_container if tool == "pyright" else base_container
            result = container.with_exec(
                static.runner_prefix(tool, pinned) + ["--version"],
                expect=dagger.ReturnType.ANY,
            )
            text = (await result.stdout()) + (await result.stderr())
            if not re.search(rf"(?<![\d.]){re.escape(pinned)}(?![\d.])", text):
                raise RuntimeError(
                    f"{tool} did not report the pinned version {pinned}: {text[:300]}"
                )
            observed[tool] = pinned
        return observed

    @function
    @check
    async def fixture_static_analysis(
        self,
        source: Annotated[dagger.Directory, DefaultPath("/tests/fixtures/static-app")],
    ) -> None:
        """Run each pinned scanner against real findings and real clean sources.

        Every negative case must surface as findings with the tool's own exit
        code, and every positive case as a clean run. A scanner that cannot
        finish fails the check instead of being recorded as a pass.
        """
        versions = await self._static_fixture_versions(source, "3.14")
        scenarios = {
            "vulture-clean": (static.vulture_policy("src/clean.py"), "clean", 0),
            "vulture-dead": (static.vulture_policy("src/dead_code.py"), "findings", 3),
            "pyright-clean": (static.pyright_policy("src/clean.py"), "clean", 0),
            "pyright-type-error": (
                static.pyright_policy("src/type_error.py"),
                "findings",
                1,
            ),
            # The same file passes only because the consumer's own
            # configuration was honoured, which proves it was read.
            "pyright-consumer-config": (
                static.pyright_policy("src/type_error.py", config="pyright-off.json"),
                "clean",
                0,
            ),
            "pyright-unresolved-import": (
                static.pyright_policy("probes/unresolved_import.py"),
                "findings",
                1,
            ),
            "deptry-unused-dependency": (static.deptry_policy("src"), "findings", 1),
            "deptry-ignored": (
                static.deptry_policy("src", per_rule_ignores="DEP002=requests"),
                "clean",
                0,
            ),
            "pylint-clean": (static.pylint_policy(["src/clean.py"]), "clean", 0),
            "pylint-duplicate": (
                static.pylint_policy(["src/duplicate_a.py", "src/duplicate_b.py"]),
                "findings",
                8,
            ),
        }
        reports = {}
        for name, (policy, outcome, exit_code) in scenarios.items():
            report = await self._run_static(
                source,
                "3.14",
                policy,
                native_output=_DEPTRY_JSON if policy["tool"] == "deptry" else "",
            )
            if report["outcome"] != outcome or report["exit_code"] != exit_code:
                raise RuntimeError(
                    f"{name}: expected {outcome} with native exit {exit_code}, got "
                    f"{report['outcome']} with exit {report['exit_code']}: "
                    f"{static.findings_summary(report)}"
                )
            reports[name] = report

        def require(condition, message):
            if not condition:
                raise RuntimeError(message)

        require(
            any(
                "never_called_anywhere" in item["message"]
                for item in reports["vulture-dead"]["findings"]
            ),
            f"Vulture lost the dead-code diagnostic: {reports['vulture-dead']['findings']}",
        )
        require(
            reports["pyright-clean"]["native_json_available"]
            and reports["pyright-type-error"]["native_json_available"]
            and any(
                item["rule"] == "reportReturnType"
                for item in reports["pyright-type-error"]["findings"]
            ),
            f"Pyright lost structured diagnostics: {reports['pyright-type-error']}",
        )
        require(
            reports["pyright-type-error"]["observed_version"]
            == static.SCANNER_VERSIONS["pyright"],
            "Pyright did not report its own version alongside the diagnostics",
        )
        require(
            any(
                "did not resolve" in item
                for item in reports["pyright-unresolved-import"]["limitations"]
            ),
            "Unresolved imports were presented without a limitation: "
            f"{reports['pyright-unresolved-import']}",
        )
        require(
            any(
                item["rule"].startswith("DEP") and item.get("module") == "requests"
                for item in reports["deptry-unused-dependency"]["findings"]
            ),
            "deptry lost the unused dependency: "
            f"{reports['deptry-unused-dependency']['findings']}",
        )
        duplicated = reports["pylint-duplicate"]
        # pylint emits one R0801 per duplicate group and names both members
        # inside that single message, so the count stays at one.
        require(
            reports["pylint-clean"]["native_json_available"]
            and duplicated["native_json_available"],
            f"Pylint lost structured JSON output: {duplicated}",
        )
        duplicate_text = " ".join(
            f"{item['message']} {item.get('symbol') or ''}"
            for item in duplicated["findings"]
        )
        require(
            "R0801" in {item["rule"] for item in duplicated["findings"]}
            and "duplicate_a" in duplicate_text
            and "duplicate_b" in duplicate_text,
            f"Cross-file duplication was not detected: {duplicated['findings']}",
        )
        # One execution per scenario already carried both the gate decision and
        # the structured result; re-running here would prove nothing extra.
        print(
            "PASS: pinned "
            + ", ".join(f"{tool} {value}" for tool, value in versions.items())
            + "; findings, clean runs, consumer config and stated limits verified: "
            + ", ".join(sorted(reports))
        )
