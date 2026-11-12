import asyncio
import json
import re
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import dagger
from dagger import DefaultPath, Ignore, check, dag, function, object_type

from . import python_static as static
from . import repository, tooling
from . import script_analysis as script
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
# Reads a file the scan already wrote. A missing file yields empty output,
# which the classifier turns into an explicit failure rather than a pass.
_READ_OPTIONAL = (
    "import pathlib,sys;"
    "target=pathlib.Path(sys.argv[1]);"
    "sys.stdout.write(target.read_text(encoding='utf-8') if target.is_file() else '')"
)


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
        if kind not in {"registry", "sample", "live", "terminal"}:
            raise ValueError("Unknown Weaver check")
        bound = dag.directory().with_directory(
            "/", source, exclude=list(tooling.SOURCE_NOISE)
        )
        result = (
            self._weaver_container()
            .with_directory("/workspace", bound)
            .with_env_variable("ECI_SOURCE_DIGEST", await bound.digest())
            .with_env_variable("ECI_WEAVER_ASSET_SHA256", tooling.WEAVER_LINUX_SHA256)
            .with_exec(["python", "/check/runtime.py", kind, "--config", config_path])
        )
        return result.directory("/out")

    @function
    async def weaver_report(
        self,
        source: dagger.Directory,
        kind: str = "registry",
        config_path: str = "ci-weaver.toml",
    ) -> str:
        """Read the same native execution used by the artifact and strict checks."""
        artifacts = await self.weaver_artifacts(source, kind, config_path)
        return await artifacts.file("report.json").contents()

    async def _require_weaver(self, source, kind, config_path):
        report = json.loads(await self.weaver_report(source, kind, config_path))
        if report["status"] != "passed":
            raise ValueError(f"{report['name']}: {report['reason']}")

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
        source: Annotated[dagger.Directory, DefaultPath("/")],
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
        source: Annotated[dagger.Directory, DefaultPath("/")],
        config_path: str = "ci-weaver.toml",
    ) -> None:
        """Validate a completed OTLP capture through Weaver's gRPC receiver."""
        await self._require_weaver(self.weaver_source or source, "live", config_path)

    @function
    @check
    async def weaver_terminal(
        self,
        source: Annotated[dagger.Directory, DefaultPath("/")],
        config_path: str = "ci-weaver.toml",
    ) -> None:
        """Post-completion Pipeline validation; requires explicit terminal evidence."""
        await self._require_weaver(
            self.weaver_source or source, "terminal", config_path
        )

    @function
    async def execute_plan(
        self,
        plan: dagger.File,
        source: dagger.Directory,
        python_version: str = "3.14",
        weaver_config: str = "ci-weaver.toml",
    ) -> str:
        """Execute plan targets in-container; host-run selected checks/prereqs."""
        import copy
        import tempfile

        from engineering_ci import execution, selection

        raw = await plan.contents()
        try:
            value = json.loads(raw)
        except ValueError:
            return json.dumps(
                execution.plan_rejection(execution.ExecutionError("plan_invalid"))
            )
        try:
            execution.validate_plan(value)
        except execution.ExecutionError as error:
            return json.dumps(execution.plan_rejection(error))
        chosen = value.get("selection") or {}
        targets = chosen.get("tests") or []
        framework = chosen.get("framework", "unittest")
        selected_checks = [
            item
            for item in value.get("check_scope", chosen.get("check_scope", []))
            if item.get("status") in {"selected", "unknown"}
        ]
        if ("check_scope" not in value and "check_scope" not in chosen) and chosen.get(
            "checks"
        ):
            selected_checks = [
                {"name": name, "status": "unknown"} for name in chosen["checks"]
            ]
        prerequisites = list(chosen.get("prerequisites") or [])
        if value.get("git", {}).get("snapshot") == "multi":
            return json.dumps(
                {
                    "status": "not_executed",
                    "reason": "multi_ref_requires_per_ref_execution",
                }
            )
        if not targets and not selected_checks and not prerequisites:
            return json.dumps({"status": "not_executed", "reason": "no_selected_tests"})
        git = value.get("git") or {}
        source_tree_id = git.get("source_tree_id")
        execution_tree = git.get("execution_tree")
        tree_verified = False
        source_tree_state = "not_requested"
        source_digest = None
        input_closure_verified = "not_requested"
        with tempfile.TemporaryDirectory() as directory:
            await source.export(directory)
            exported_content_id = selection.directory_content_id(directory)
            if source_tree_id and execution_tree:
                source_digest = selection.directory_content_id(directory)
                tree_verified = source_digest == source_tree_id
                source_tree_state = "verified" if tree_verified else "failed"
                if not tree_verified:
                    return json.dumps(
                        {
                            "kind": execution.REPORT_KIND,
                            "schema_version": execution.SCHEMA_VERSION,
                            "status": "not_executed",
                            "reason": "source_tree_mismatch",
                            "exit_code": 4,
                            "tree_verified": False,
                            "source_tree_verified": source_tree_state,
                            "input_closure_verified": input_closure_verified,
                            "trusted": False,
                            "plan_execution_tree": execution_tree,
                            "source_tree_id": source_tree_id,
                            "source_digest": source_digest,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
            report = {
                "kind": execution.REPORT_KIND,
                "schema_version": execution.SCHEMA_VERSION,
                "dependency_contract": value.get("dependency_contract"),
                "plan": {"generator": value.get("generator"), "git": git},
                "execution_context": "dagger_export",
                "status": "passed",
                "exit_code": 0,
                "targets": targets,
                "framework": framework,
                # tree_verified is whole-tree Source-byte equivalence only when
                # that check ran. Post-completion uses input_closure_verified.
                # Neither field is #63 Check-evidence admission.
                "tree_verified": tree_verified,
                "source_tree_verified": source_tree_state,
                "input_closure_verified": input_closure_verified,
                "trusted": False,
                "plan_execution_tree": execution_tree,
                "source_tree_id": source_tree_id,
                "source_digest": source_digest,
                "tests": {"status": "not_executed", "exit_code": 3},
                "checks": [],
                "prerequisites": [],
                "source_handoff": {
                    "original_git": git,
                    "exported_content_id": exported_content_id,
                    "host_context": "dagger_export",
                    "original_git_snapshot_preserved": True,
                },
            }
            if targets:
                container = self._uv_container(source, python_version)
                junit = "/tmp/engineering-ci-junit.xml"
                if framework == "pytest":
                    pytest_targets = []
                    for target in targets:
                        if target.endswith(".py") or "/" in target or "::" in target:
                            pytest_targets.append(target)
                        else:
                            pytest_targets.extend(["--pyargs", target])
                    command = [
                        "uv",
                        "run",
                        "pytest",
                        "-q",
                        f"--junitxml={junit}",
                        *pytest_targets,
                    ]
                elif framework == "unittest":
                    command = ["uv", "run", "python", "-m", "unittest", *targets]
                else:
                    return json.dumps(
                        {"status": "not_executed", "reason": "framework_invalid"}
                    )
                result = container.with_exec(command, expect=dagger.ReturnType.ANY)
                exit_code = await result.exit_code()
                report["exit_code"] = exit_code
                report["stdout"] = await result.stdout()
                report["stderr"] = await result.stderr()
                report["tests"] = {
                    "status": "passed" if exit_code == 0 else "failed",
                    "exit_code": exit_code,
                }
                if exit_code != 0:
                    report["status"] = "failed"
                    report["reason"] = "tests_failed"
                if framework == "pytest" and exit_code in {0, 1}:
                    try:
                        report["junit"] = await result.file(junit).contents()
                    except dagger.QueryError:
                        report["junit"] = None
            if selected_checks or prerequisites:
                check_plan = copy.deepcopy(value)
                check_plan.setdefault("selection", {})
                check_plan["selection"] = dict(check_plan["selection"])
                check_plan["selection"]["tests"] = []
                check_plan["git"] = dict(check_plan.get("git") or {})
                # This is only the Host adapter's filesystem view. Preserve the
                # original Git identity in source_handoff, and do not claim an
                # exported Directory has the original ODB or verified full Tree.
                check_plan["git"]["execution_tree"] = None
                check_plan["git"]["index_tree"] = None
                check_plan["git"]["content_binding"] = None
                check_plan["git"]["snapshot"] = "worktree"
                weaver_checks = {"weaver_registry", "weaver_live", "weaver_terminal"}
                selected_weaver = [
                    item
                    for item in selected_checks
                    if item.get("name") in weaver_checks
                ]
                check_plan["check_scope"] = [
                    item
                    for item in selected_checks
                    if item.get("name") not in weaver_checks
                ]
                check_plan["selection"]["checks"] = [
                    name
                    for name in check_plan["selection"].get("checks", [])
                    if name not in weaver_checks
                ]
                host = (
                    execution.execute(
                        check_plan, root=directory, execution_context="dagger_export"
                    )
                    if check_plan["check_scope"] or prerequisites
                    else {
                        "status": "passed",
                        "exit_code": 0,
                        "checks": [],
                        "prerequisites": [],
                    }
                )
                report["checks"] = host.get("checks") or []
                report["prerequisites"] = host.get("prerequisites") or []
                after_host = selection.directory_content_id(directory)
                report["source_handoff"]["after_host_content_id"] = after_host
                if after_host != exported_content_id:
                    report.update(
                        status="not_executed",
                        reason="exported_source_changed",
                        exit_code=4,
                        tree_verified=False,
                        source_tree_verified="failed_after_host_action",
                    )
                    report["source_handoff"]["generated_outputs_delivered"] = False
                    report["checks"].extend(
                        {
                            "name": item["name"],
                            "status": "not_executed",
                            "reason": "exported_source_changed",
                        }
                        for item in selected_weaver
                    )
                    return json.dumps(report, sort_keys=True)
                if report["status"] == "passed":
                    report["status"] = host.get("status", "blocked")
                    report["reason"] = host.get("reason")
                    report["exit_code"] = host.get("exit_code", 5)
                terminal_config = weaver_config
                binding = value.get("post_completion")
                if isinstance(binding, dict):
                    declared = (binding.get("weaver_config") or {}).get("path")
                    if isinstance(declared, str) and declared:
                        terminal_config = declared
                for item in selected_weaver:
                    kind = item["name"].removeprefix("weaver_")
                    if kind == "terminal":
                        gate = selection.validate_post_completion_binding(
                            value, directory
                        )
                        report["input_closure_verified"] = gate.get(
                            "input_closure_verified", "failed"
                        )
                        report["plan_closure_digest"] = gate.get("plan_closure_digest")
                        report["actual_closure_digest"] = gate.get(
                            "actual_closure_digest"
                        )
                        if gate.get("source_tree_verified") != "not_requested":
                            report["source_tree_verified"] = gate[
                                "source_tree_verified"
                            ]
                            report["tree_verified"] = (
                                gate["source_tree_verified"] == "verified"
                            )
                            if gate.get("source_digest"):
                                report["source_digest"] = gate["source_digest"]
                        if not gate.get("ok"):
                            native = {
                                "name": "weaver_terminal",
                                "status": "not_executed",
                                "reason": gate.get("reason")
                                or "post_completion_binding_invalid",
                                "gate_exit_code": 1,
                                "trusted": False,
                                "authenticated": False,
                                "selected": item.get("status"),
                                "input_closure_verified": gate.get(
                                    "input_closure_verified"
                                ),
                                "source_tree_verified": report["source_tree_verified"],
                                "plan_closure_digest": gate.get("plan_closure_digest"),
                                "actual_closure_digest": gate.get(
                                    "actual_closure_digest"
                                ),
                            }
                            report["checks"].append(native)
                            if report["status"] == "passed":
                                report.update(
                                    status="blocked",
                                    exit_code=1,
                                    reason=native["reason"],
                                )
                            continue
                        config_path = terminal_config
                    else:
                        config_path = weaver_config
                    native = json.loads(
                        await self.weaver_report(source, kind, config_path)
                    )
                    native["selected"] = item.get("status")
                    native["source_tree_verified"] = report["source_tree_verified"]
                    if kind == "terminal":
                        native["phase"] = selection.POST_COMPLETION_PHASE
                        native["input_closure_verified"] = report[
                            "input_closure_verified"
                        ]
                        native["plan_closure_digest"] = report.get(
                            "plan_closure_digest"
                        )
                        native["actual_closure_digest"] = report.get(
                            "actual_closure_digest"
                        )
                    report["checks"].append(native)
                    if native["status"] != "passed" and report["status"] == "passed":
                        report.update(
                            status="failed", exit_code=1, reason="checks_failed"
                        )
        return json.dumps(report, ensure_ascii=False, sort_keys=True)

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
    ) -> dagger.Directory:
        """Export the same pytest node used by tests, even when it failed."""
        execution = await self._repository_tests(source)
        bundle = self._pytest_bundle(execution)
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
        if git_token is not None:
            base = prepare_private_git(base)
        container = (
            base.with_env_variable("UV_NO_MANAGED_PYTHON", "1")
            .with_env_variable("UV_PYTHON_DOWNLOADS", "0")
            .with_mounted_cache(
                "/root/.cache/uv",
                dag.cache_volume(f"engineering-ci-uv-python{python_version}"),
            )
            .with_env_variable("UV_LINK_MODE", "copy")
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
        source: dagger.Directory,
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
        source: dagger.Directory,
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

    async def _run_actionlint(self, source: dagger.Directory, policy: dict) -> dict:
        """Execute the pinned Workflow checker once and classify its result."""
        container = (
            dag.container()
            .from_("ghcr.io/astral-sh/uv:python3.14-trixie-slim")
            .with_mounted_cache(
                "/root/.cache/uv", dag.cache_volume("engineering-ci-uv-python3.14")
            )
            .with_env_variable("UV_LINK_MODE", "copy")
            .with_env_variable("UV_PYTHON_DOWNLOADS", "never")
            .with_directory("/workspace", source, exclude=repository.SOURCE_EXCLUDES)
            .with_workdir("/workspace")
        )
        result = container.with_exec(
            script.runner_prefix("actionlint") + script.actionlint_arguments(policy),
            expect=dagger.ReturnType.ANY,
        )
        return script.script_report(
            policy,
            await result.exit_code(),
            await result.stdout(),
            await result.stderr(),
        )

    @function
    async def actionlint_workflows(
        self,
        source: dagger.Directory,
        scan_paths: list[str] | None = None,
        config: str = "",
    ) -> str:
        """Check GitHub Actions Workflows with the pinned actionlint.

        ShellCheck and pyflakes stay explicitly disabled so the checked scope
        never depends on what happens to be installed. Unchecked inline scripts
        are never reported as passing. Returns a JSON report without gating.
        """
        report = await self._run_actionlint(
            source, script.actionlint_policy(scan_paths, config)
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
            elif kind == "psscriptanalyzer":
                report = script.deferred_report(
                    scan_path,
                    "PowerShell scripts are analysed by the Windows runner in "
                    "reusable-python-ci.yml, not by this Linux Dagger check",
                )
            else:
                report = script.unsupported_report(
                    scan_path,
                    "Shell and other script types are out of scope for this check; "
                    "only Workflow files and PowerShell modules are analysed",
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
        )
        if tool == "pyright":
            # The published Pyright distribution downloads a Node runtime on
            # first use; caching it keeps that off every later run.
            container = container.with_mounted_cache(
                "/root/.cache/pyright-python",
                dag.cache_volume("engineering-ci-pyright-node"),
            )
        result = container.with_exec(
            static.runner_prefix(tool, policy["pinned_version"])
            + static.ARGUMENTS[tool](policy, native_output),
            expect=dagger.ReturnType.ANY,
        )
        native_raw = ""
        if native_output:
            native_raw = await result.with_exec(
                ["python", "-c", _READ_OPTIONAL, native_output],
                expect=dagger.ReturnType.ANY,
            ).stdout()
        return static.static_report(
            policy,
            await result.exit_code(),
            await result.stdout(),
            await result.stderr(),
            native_raw,
        )

    @function
    async def python_vulture(
        self,
        source: dagger.Directory,
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
        source: dagger.Directory,
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
            .with_directory("/workspace", source, exclude=list(tooling.SOURCE_NOISE))
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

    @function
    async def lychee_check(
        self,
        source: dagger.Directory,
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
            return json.dumps(
                {
                    "tool": "lychee",
                    "version": tooling.LYCHEE_VERSION,
                    "status": "failed",
                    "exit_code": install_exit,
                    "stdout": await installed.stdout(),
                    "stderr": await installed.stderr(),
                    "reason": "tool_install_failed",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        container = installed.with_directory(
            "/workspace", source, exclude=list(tooling.SOURCE_NOISE)
        ).with_workdir("/workspace")
        result = container.with_exec(
            ["/usr/local/bin/lychee", "--offline", "--no-progress", *targets],
            expect=dagger.ReturnType.ANY,
        )
        exit_code = await result.exit_code()
        return json.dumps(
            {
                "tool": "lychee",
                "version": tooling.LYCHEE_VERSION,
                "status": "passed" if exit_code == 0 else "failed",
                "exit_code": exit_code,
                "stdout": await result.stdout(),
                "stderr": await result.stderr(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    @function
    async def python_pyright(
        self,
        source: dagger.Directory,
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
        source: dagger.Directory,
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
        source: dagger.Directory,
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
            )
        )
        arguments = pytest_arguments(policy, report_path)
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
        source: dagger.Directory,
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
        source: dagger.Directory,
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
        source: dagger.Directory,
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
        source: dagger.Directory,
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
        source: dagger.Directory,
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
        source: dagger.Directory,
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
        container = self._uv_container(source, python_version)
        observed = {}
        for tool, pinned in static.SCANNER_VERSIONS.items():
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
