"""Plan result contract tests use explicit Dagger doubles, never tool evidence."""

import asyncio
import dataclasses
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

from engineering_ci import execution as host_execution
from engineering_ci import plan_contract

MODULE = Path(__file__).resolve().parents[1] / ".dagger/src/engineering_ci_module"


class MemoryFile:
    def __init__(self, contents=b"", *, _loader=None):
        self._loader = _loader
        if isinstance(contents, bytes):
            self._data = contents
        else:
            self._data = contents.encode("utf-8")

    @property
    def data(self):
        if self._loader is not None:
            loaded = self._loader()
            return loaded if isinstance(loaded, bytes) else loaded.encode("utf-8")
        return self._data

    @property
    def text(self):
        return self.data.decode("utf-8")

    async def sync(self):
        return MemoryFile(self.data)

    async def contents(self):
        return self.text


class MemoryDirectory:
    def __init__(self, files=None, *, dirs=None, _loader=None):
        self._loader = _loader
        raw_files = dict(files or {})
        self.files = dict(raw_files)
        inferred_dirs = set(dirs or ())
        for rel in raw_files:
            parent = Path(rel).parent
            while parent != Path("."):
                inferred_dirs.add(parent.as_posix())
                parent = parent.parent
        self.dirs = frozenset(inferred_dirs)

    def _materialize(self):
        if self._loader is not None:
            loaded_files, loaded_dirs = self._loader()
            return dict(loaded_files), frozenset(loaded_dirs)
        return dict(self.files), frozenset(self.dirs)

    async def sync(self):
        loaded_files, loaded_dirs = self._materialize()
        return MemoryDirectory(loaded_files, dirs=loaded_dirs)

    def with_new_file(self, path, contents):
        cur_files, cur_dirs = self._materialize()
        return MemoryDirectory({**cur_files, path: contents}, dirs=cur_dirs)

    def with_file(self, path, file):
        cur_files, cur_dirs = self._materialize()
        return MemoryDirectory({**cur_files, path: file.data}, dirs=cur_dirs)

    def with_directory(self, prefix, directory):
        cur_files, cur_dirs = self._materialize()
        add_files, add_dirs = directory._materialize()
        clean_prefix = prefix.strip("/")
        merged_files = {
            **cur_files,
            **{
                f"{clean_prefix}/{k}" if clean_prefix else k: v
                for k, v in add_files.items()
            },
        }
        merged_dirs = set(cur_dirs)
        if clean_prefix:
            merged_dirs.add(clean_prefix)
        for item in add_dirs:
            merged_dirs.add(f"{clean_prefix}/{item}" if clean_prefix else item)
        return MemoryDirectory(merged_files, dirs=merged_dirs)

    def file(self, path):
        cur_files, _ = self._materialize()
        return MemoryFile(cur_files[path])

    async def id(self):
        cur_files, cur_dirs = self._materialize()
        return json.dumps(
            {
                "files": sorted((k, repr(v)) for k, v in cur_files.items()),
                "dirs": sorted(cur_dirs),
            },
            sort_keys=True,
        )

    async def export(self, directory):
        cur_files, cur_dirs = self._materialize()
        dest = Path(directory)
        for rel_dir in sorted(cur_dirs):
            (dest / rel_dir).mkdir(parents=True, exist_ok=True)
        for name, value in cur_files.items():
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(value, bytes):
                target.write_bytes(value)
            else:
                target.write_text(value, encoding="utf-8")


@pytest.fixture
def modules(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    def _resolve_workdir(path):
        raw = str(path)
        if not raw or Path(raw).is_absolute():
            raise RuntimeError(f'workdir path "{raw}" escapes workdir')
        root = Path.cwd().resolve()
        candidate = (root / raw).resolve()
        if not candidate.is_relative_to(root):
            raise RuntimeError(f'workdir path "{raw}" escapes workdir')
        return candidate

    def workdir_file(path):
        target = _resolve_workdir(path)

        def load():
            if not target.is_file():
                raise FileNotFoundError(target)
            return target.read_bytes()

        return MemoryFile(_loader=load)

    def workdir(path):
        target = _resolve_workdir(path)

        def load():
            if not target.is_dir():
                raise FileNotFoundError(target)
            loaded_files = {}
            loaded_dirs = set()
            for entry in sorted(target.rglob("*")):
                rel = entry.relative_to(target).as_posix()
                if entry.is_dir():
                    loaded_dirs.add(rel)
                elif entry.is_file():
                    loaded_files[rel] = entry.read_bytes()
            return loaded_files, loaded_dirs

        return MemoryDirectory(_loader=load)

    stub = types.ModuleType("dagger")
    stub.Directory = MemoryDirectory
    stub.File = MemoryFile
    stub.Container = object
    stub.field = dataclasses.field
    stub.function = lambda function: function
    stub.object_type = dataclasses.dataclass
    stub.dag = types.SimpleNamespace(
        directory=MemoryDirectory,
        current_module=lambda: types.SimpleNamespace(
            workdir=workdir,
            workdir_file=workdir_file,
        ),
    )
    stub.ReturnType = types.SimpleNamespace(ANY=object())
    monkeypatch.setitem(sys.modules, "dagger", stub)
    package = types.ModuleType("plan_result_unit")
    package.__path__ = [str(MODULE)]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    loaded = []
    for name in ("plan_result", "plan_execution"):
        spec = importlib.util.spec_from_file_location(
            package.__name__ + "." + name, MODULE / (name + ".py")
        )
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        loaded.append(module)
    return loaded


@pytest.mark.parametrize(
    "status,code", [("failed", 1), ("not_executed", 2), ("blocked", 5)]
)
def test_strict_failure_keeps_the_same_report_and_native_artifacts(
    modules, status, code
):
    result, _ = modules
    original = MemoryDirectory(
        {"tests/junit.xml": "<native/>", "tests/stdout.txt": "witness"}
    )
    execution = result.result(
        {"status": status, "exit_code": code, "reason": "native"}, original
    )

    async def consume():
        before = await execution.report()
        with pytest.raises(RuntimeError, match="Plan " + status):
            await execution.enforce()
        assert await execution.report() == before
        assert (
            await execution.artifacts.file("tests/junit.xml").contents() == "<native/>"
        )
        assert (
            await execution.artifacts.file("tests/stdout.txt").contents() == "witness"
        )

    asyncio.run(consume())
    assert "report.json" not in original.files


@pytest.mark.parametrize("code", [True, False, "0", None])
def test_strict_reader_does_not_accept_non_integer_report_exit(modules, code):
    result, _ = modules
    execution = result.PlanExecution(
        MemoryDirectory(
            {
                "report.json": json.dumps(
                    {
                        "kind": host_execution.REPORT_KIND,
                        "schema_version": 1,
                        "status": "passed",
                        "exit_code": code,
                    }
                ),
                "exit-code.txt": "0\n",
            }
        )
    )
    with pytest.raises(RuntimeError):
        asyncio.run(execution.enforce())


def test_strict_reader_accepts_one_passed_consistent_result(modules):
    result, _ = modules
    execution = result.result({"status": "passed", "exit_code": 0}, MemoryDirectory())
    asyncio.run(execution.enforce())


@pytest.mark.parametrize(
    "report",
    [
        [],
        {
            "kind": host_execution.REPORT_KIND,
            "schema_version": 2,
            "status": "passed",
            "exit_code": 0,
        },
        {
            "kind": host_execution.REPORT_KIND,
            "schema_version": True,
            "status": "passed",
            "exit_code": 0,
        },
    ],
)
def test_strict_reader_refuses_unknown_or_malformed_report_schema(modules, report):
    result, _ = modules
    saved = result.PlanExecution(
        MemoryDirectory(
            {
                "report.json": json.dumps(report),
                "exit-code.txt": "0\n",
            }
        )
    )
    with pytest.raises(RuntimeError, match="plan_result_invalid"):
        asyncio.run(saved.enforce())


class NativeDouble:
    """A barrier proves requests overlap; outputs are explicitly test doubles."""

    def __init__(self, test_code=0, weaver_status="passed", infrastructure_error=False):
        self.test_code = test_code
        self.weaver_status = weaver_status
        self.infrastructure_error = infrastructure_error
        self.calls = []
        self.started = set()
        self.ready = asyncio.Event()

    async def start(self, name):
        self.started.add(name)
        if self.started == {"tests", "weaver"}:
            self.ready.set()
        await asyncio.wait_for(self.ready.wait(), timeout=1)

    def _uv_container(self, source, version):
        self.calls.append("prepare tests")
        return self

    def with_exec(self, command, **kwargs):
        self.calls.append(tuple(command))
        return self

    async def exit_code(self):
        await self.start("tests")
        return self.test_code

    async def stdout(self):
        return "test witness"

    async def stderr(self):
        return "test stderr"

    async def weaver_artifacts(self, source, kind, config):
        self.calls.append("weaver artifacts")
        await self.start("weaver")
        if self.infrastructure_error:
            raise RuntimeError("controlled Engine failure")
        return MemoryDirectory(
            {
                "report.json": json.dumps(
                    {
                        "name": "weaver_registry",
                        "status": self.weaver_status,
                        "exit_code": 17 if self.weaver_status == "failed" else 0,
                    }
                ),
                "native.log": "weaver witness",
            }
        )


@pytest.mark.parametrize(
    "test_code,weaver_status", [(0, "passed"), (1, "passed"), (0, "failed")]
)
def test_independent_native_branches_and_all_readers_share_saved_results(
    modules, test_code, weaver_status
):
    result, collector = modules

    async def run():
        native = NativeDouble(test_code, weaver_status)
        report = {
            "status": "passed",
            "exit_code": 0,
            "framework": "unittest",
            "source_tree_verified": "not_requested",
            "checks": [],
        }
        files = await collector._native_results(
            native,
            MemoryDirectory(),
            {"selection": {"tests": ["tests.controlled"]}},
            ".",
            [{"name": "weaver_registry", "status": "selected"}],
            report,
            MemoryDirectory(),
            "3.14",
            "ci-weaver.toml",
        )
        collector._aggregate(report)
        assert native.started == {"tests", "weaver"}
        assert report["result_sharing"]["origin_execution_attributed"] is False
        assert report["tests"]["exit_code"] == test_code
        assert report["checks"][0]["exit_code"] == (
            17 if weaver_status == "failed" else 0
        )
        saved = result.result(report, files)
        calls = list(native.calls)
        assert json.loads(await saved.report()) == report
        assert (
            await saved.artifacts.file("tests/stdout.txt").contents() == "test witness"
        )
        assert (
            await saved.artifacts.file("checks/weaver_registry/native.log").contents()
            == "weaver witness"
        )
        if test_code or weaver_status != "passed":
            with pytest.raises(RuntimeError):
                await saved.enforce()
        else:
            await saved.enforce()
        assert native.calls == calls
        assert native.calls.count("prepare tests") == 1
        assert native.calls.count("weaver artifacts") == 1

    asyncio.run(run())


def test_infrastructure_error_preserves_the_independent_native_result(modules):
    _, collector = modules

    async def run():
        native = NativeDouble(infrastructure_error=True)
        report = {
            "status": "passed",
            "exit_code": 0,
            "framework": "unittest",
            "source_tree_verified": "not_requested",
            "checks": [],
        }
        await collector._native_results(
            native,
            MemoryDirectory(),
            {"selection": {"tests": ["tests.controlled"]}},
            ".",
            [{"name": "weaver_registry", "status": "selected"}],
            report,
            MemoryDirectory(),
            "3.14",
            "ci-weaver.toml",
        )
        collector._aggregate(report)
        assert report["status"] == "blocked" and report["exit_code"] == 5
        assert report["tests"]["status"] == "passed"
        assert report["checks"][0]["exit_code"] is None
        assert report["checks"][0]["reason"] == "native_execution_error"

    asyncio.run(run())


@pytest.mark.parametrize(
    "fields,reason",
    [
        (
            {
                "requires_tach_check": True,
                "tach_graph_validation": {"status": "failed"},
            },
            "tach_graph_unvalidated",
        ),
        ({"coverage_required": True}, "coverage_evidence_required"),
    ],
)
@pytest.mark.parametrize("weaver_status", ["passed", "failed"])
def test_existing_test_precondition_blocks_only_its_native_consumer(
    modules, fields, reason, weaver_status
):
    result, collector = modules

    class Independent(NativeDouble):
        async def weaver_artifacts(self, source, kind, config):
            self.calls.append("independent native check")
            return MemoryDirectory(
                {
                    "report.json": json.dumps(
                        {
                            "name": "weaver_registry",
                            "status": weaver_status,
                            "exit_code": 17 if weaver_status == "failed" else 0,
                            "gate_exit_code": 9 if weaver_status == "failed" else 0,
                            "reason": "controlled native failure"
                            if weaver_status == "failed"
                            else None,
                        }
                    ),
                    "native.log": "independent native witness",
                }
            )

        def _uv_container(self, *args):
            pytest.fail("dependent test started before its prerequisite passed")

    async def run():
        native = Independent()
        report = {
            "status": "passed",
            "exit_code": 0,
            "framework": "unittest",
            "source_tree_verified": "not_requested",
            "checks": [],
        }
        files = await collector._native_results(
            native,
            MemoryDirectory(),
            {"selection": {"tests": ["tests.controlled"], **fields}},
            ".",
            [{"name": "weaver_registry", "status": "selected"}],
            report,
            MemoryDirectory(),
            "3.14",
            "ci-weaver.toml",
        )
        collector._aggregate(report)
        if weaver_status == "failed":
            assert (report["status"], report["exit_code"], report["reason"]) == (
                "failed",
                9,
                "checks_failed",
            )
            assert report["checks"][0]["exit_code"] == 17
            assert report["checks"][0]["gate_exit_code"] == 9
        else:
            assert (report["status"], report["exit_code"], report["reason"]) == (
                "blocked",
                5,
                reason,
            )
        assert report["tests"]["status"] == "not_executed"
        assert report["tests"]["reason"] == reason
        assert report["tests"]["exit_code"] is None
        assert report["checks"][0]["status"] == weaver_status
        assert "checks/weaver_registry/report.json" in files.files
        saved = result.result(report, files)
        with pytest.raises(RuntimeError):
            await saved.enforce()
        assert (
            await saved.artifacts.file("checks/weaver_registry/native.log").contents()
            == "independent native witness"
        )
        assert native.calls == ["independent native check"]

    asyncio.run(run())


def test_invalid_plan_refuses_before_source_export(modules):
    _, collector = modules
    plan = MemoryFile(
        json.dumps({"kind": plan_contract.PLAN_KIND, "schema_version": 99})
    )

    class Source:
        async def export(self, path):
            pytest.fail("invalid Plan exported Source")

    saved = asyncio.run(
        collector.execute(object(), plan, Source(), "3.14", "ci-weaver.toml")
    )
    assert (
        json.loads(asyncio.run(saved.report()))["reason"] == "plan_schema_unsupported"
    )
    with pytest.raises(RuntimeError):
        asyncio.run(saved.enforce())


@pytest.mark.parametrize("weaver_failed", [False, True])
def test_host_write_invalidates_binding_and_retains_original_result(
    modules, weaver_failed
):
    _, collector = modules
    actions = {
        "write": {
            "type": "command",
            "argv": [
                sys.executable,
                "-c",
                "from pathlib import Path;Path('generated.txt').write_text('new')",
            ],
        }
    }
    plan = {
        "kind": plan_contract.PLAN_KIND,
        "schema_version": 2,
        "generator": {"config_sha256": "a" * 64},
        "git": {"snapshot": "worktree"},
        "selection": {"tests": [], "prerequisites": ["write"]},
        "prerequisite_actions": actions,
        "dependency_contract": plan_contract.prerequisite_contract(
            ["write"],
            actions,
            declaration={"path": "ci-selection.toml", "sha256": "a" * 64},
            selected_by="targeted",
        ),
    }

    class Native:
        async def weaver_artifacts(self, *args):
            return MemoryDirectory(
                {
                    "report.json": json.dumps(
                        {
                            "name": "weaver_registry",
                            "status": "failed",
                            "exit_code": 17,
                        }
                    )
                }
            )

    if weaver_failed:
        plan["check_scope"] = [{"name": "weaver_registry", "status": "selected"}]
    saved = asyncio.run(
        collector.execute(
            Native(),
            MemoryFile(json.dumps(plan)),
            MemoryDirectory({"ci-selection.toml": "# original\n"}),
            "3.14",
            "ci-weaver.toml",
        )
    )
    report = json.loads(asyncio.run(saved.report()))
    assert report["reason"] == "exported_source_changed" and report["exit_code"] == 4
    assert report["prerequisites"][0]["exit_code"] == 0
    assert report["source_handoff"]["generated_outputs_delivered"] is False
    if weaver_failed:
        assert report["checks"][0]["status"] == "failed"
        assert report["checks"][0]["exit_code"] == 17
    with pytest.raises(RuntimeError):
        asyncio.run(saved.enforce())


@pytest.mark.parametrize("old_status", ["passed", "blocked", "failed"])
@pytest.mark.parametrize(
    "group,reason",
    [
        ("checks", "checks_failed"),
        ("prerequisites", "prerequisites_failed"),
    ],
)
def test_aggregate_uses_children_instead_of_the_previous_top_status(
    modules, old_status, group, reason
):
    _, collector = modules
    report = {
        "status": old_status,
        "exit_code": 5,
        "reason": "old summary",
        "targets": ["tests.controlled"],
        "tests": {
            "status": "not_executed",
            "exit_code": None,
            "reason": "tach_graph_unvalidated",
        },
        "checks": [],
        "prerequisites": [],
    }
    report[group] = [
        {
            "name": "actual failure",
            "status": "failed",
            "exit_code": 17,
            "gate_exit_code": 9,
        }
    ]
    before = json.dumps([report["tests"], report["checks"], report["prerequisites"]])
    collector._aggregate(report)
    assert (report["status"], report["exit_code"], report["reason"]) == (
        "failed",
        9,
        reason,
    )
    assert (
        json.dumps([report["tests"], report["checks"], report["prerequisites"]])
        == before
    )


def test_nonexecuted_probe_exit_and_advisory_findings_do_not_become_tool_failures(
    modules,
):
    _, collector = modules
    report = {
        "status": "passed",
        "targets": [],
        "tests": {"status": "not_executed", "exit_code": 3},
        "checks": [{"name": "vulture", "status": "advisory", "exit_code": 3}],
        "prerequisites": [
            {
                "name": "commit",
                "status": "not_executed",
                "exit_code": 128,
                "reason": "git_odb_unavailable_or_unbound",
            }
        ],
    }
    collector._aggregate(report)
    assert report["status"] == "blocked" and report["exit_code"] == 5
    assert report["reason"] == "git_odb_unavailable_or_unbound"


@pytest.mark.parametrize(
    "group,reason",
    [("checks", "checks_failed"), ("prerequisites", "prerequisites_failed")],
)
def test_final_aggregate_includes_host_failures_after_native_collection(
    modules, monkeypatch, group, reason
):
    _, collector = modules

    class Independent:
        async def weaver_artifacts(self, *args):
            return MemoryDirectory(
                {
                    "report.json": json.dumps(
                        {"name": "weaver_registry", "status": "passed", "exit_code": 0}
                    )
                }
            )

        def _uv_container(self, *args):
            pytest.fail("blocked tests started")

    actions = {"policy": {"type": "command", "argv": [sys.executable, "-c", "pass"]}}
    plan = {
        "kind": plan_contract.PLAN_KIND,
        "schema_version": 2,
        "generator": {"config_sha256": "a" * 64},
        "git": {"snapshot": "worktree"},
        "selection": {
            "tests": ["tests.controlled"],
            "requires_tach_check": True,
            "prerequisites": ["policy"],
        },
        "check_scope": [
            {"name": "weaver_registry", "status": "selected"},
            {"name": "source", "status": "selected"},
        ],
        "prerequisite_actions": actions,
        "dependency_contract": plan_contract.prerequisite_contract(
            ["policy"],
            actions,
            declaration={"path": "ci-selection.toml", "sha256": "a" * 64},
            selected_by="targeted",
        ),
    }
    host = {"status": "blocked", "exit_code": 5, "checks": [], "prerequisites": []}
    host[group] = [{"name": "host failure", "status": "failed", "exit_code": 23}]
    monkeypatch.setattr(host_execution, "execute", lambda *a, **k: host)
    saved = asyncio.run(
        collector.execute(
            Independent(),
            MemoryFile(json.dumps(plan)),
            MemoryDirectory({"ci-selection.toml": "# controlled\n"}),
            "3.14",
            "ci-weaver.toml",
        )
    )
    report = json.loads(asyncio.run(saved.report()))
    assert (report["status"], report["exit_code"], report["reason"]) == (
        "failed",
        23,
        reason,
    )
    assert report["tests"]["status"] == "not_executed"
    assert report["checks"][0]["status"] == "passed"
    assert report[group][-1]["exit_code"] == 23
    with pytest.raises(RuntimeError):
        asyncio.run(saved.enforce())


def _v2_dagger_plan(
    *,
    edges,
    prerequisites,
    actions,
    tests=("tests.controlled",),
    checks=("weaver_registry",),
):
    scope = [{"name": name, "status": "selected"} for name in checks]
    return {
        "kind": plan_contract.PLAN_KIND,
        "schema_version": 2,
        "generator": {"config_sha256": "a" * 64},
        "git": {"snapshot": "worktree"},
        "selection": {
            "tests": list(tests),
            "checks": list(checks),
            "check_scope": scope,
            "prerequisites": list(prerequisites),
        },
        "check_scope": scope,
        "prerequisite_actions": actions,
        "dependency_contract": plan_contract.graph_contract(edges),
    }


def _v2_decl(name):
    return {
        "path": "ci-selection.toml",
        "sha256": "a" * 64,
        "section": f"prerequisite_actions.{name}",
    }


def test_v2_invalid_dependency_contract_refuses_before_source_export(modules):
    _, collector = modules
    plan = _v2_dagger_plan(
        edges=[
            {
                "producer_ref": {"kind": "check", "name": "weaver_registry"},
                "consumer_ref": {"kind": "check", "name": "weaver_registry"},
                "dependency_kind": "pre_execution_pass",
                "requires_pass": True,
                "output_ref": None,
                "source_binding_ref": "git",
                "declaration_ref": {
                    "path": "ci-selection.toml",
                    "sha256": "a" * 64,
                    "section": "checks.weaver_registry",
                },
                "execution_phase": "pre_execution",
                "resolution": "resolved",
                "unresolved_reason": None,
            }
        ],
        prerequisites=[],
        actions={},
        tests=[],
        checks=["weaver_registry"],
    )

    class Source:
        async def export(self, path):
            pytest.fail("cyclic v2 plan exported Source")

    saved = asyncio.run(
        collector.execute(
            object(), MemoryFile(json.dumps(plan)), Source(), "3.14", "ci-weaver.toml"
        )
    )
    report = json.loads(asyncio.run(saved.report()))
    assert report["reason"] == "dependency_cycle_detected"
    assert report["plan_declaration_consistent"] is False
    with pytest.raises(RuntimeError):
        asyncio.run(saved.enforce())


@pytest.mark.parametrize("prep_exit,weaver_status", [(2, "passed"), (0, "passed")])
def test_v2_pre_execution_pass_gates_only_dependent_dagger_consumer(
    modules, prep_exit, weaver_status
):
    _, collector = modules
    actions = {
        "prep": {
            "type": "command",
            "argv": [sys.executable, "-c", f"raise SystemExit({prep_exit})"],
        }
    }
    edges = [
        {
            "producer_ref": {"kind": "prerequisite_action", "name": "prep"},
            "consumer_ref": {"kind": "tests"},
            "dependency_kind": "pre_execution_pass",
            "requires_pass": True,
            "output_ref": None,
            "source_binding_ref": "git",
            "declaration_ref": _v2_decl("prep"),
            "execution_phase": "pre_execution",
            "resolution": "resolved",
            "unresolved_reason": None,
        }
    ]
    plan = _v2_dagger_plan(edges=edges, prerequisites=["prep"], actions=actions)

    class Native(NativeDouble):
        def _uv_container(self, source, version):
            if prep_exit != 0:
                pytest.fail(
                    "dependent _uv_container started after failed pre_execution_pass"
                )
            self.calls.append("tests")
            return self

        async def exit_code(self):
            return 0

        async def weaver_artifacts(self, source, kind, config):
            self.calls.append("weaver")
            return MemoryDirectory(
                {
                    "report.json": json.dumps(
                        {
                            "name": "weaver_registry",
                            "status": weaver_status,
                            "exit_code": 0,
                        }
                    ),
                    "native.log": "independent witness",
                }
            )

    native = Native()
    saved = asyncio.run(
        collector.execute(
            native,
            MemoryFile(json.dumps(plan)),
            MemoryDirectory({"ci-selection.toml": "# clean\n"}),
            "3.14",
            "ci-weaver.toml",
        )
    )
    report = json.loads(asyncio.run(saved.report()))
    assert "weaver" in native.calls
    assert report["checks"][0]["status"] == "passed"
    assert (
        asyncio.run(
            saved.artifacts.file("checks/weaver_registry/native.log").contents()
        )
        == "independent witness"
    )
    if prep_exit != 0:
        assert "tests" not in native.calls
        assert report["tests"]["status"] == "not_executed"
        assert report["tests"]["reason"] == "pre_execution_prerequisite_failed"
        assert (
            report["status"] == "failed" and report["reason"] == "prerequisites_failed"
        )
        with pytest.raises(RuntimeError):
            asyncio.run(saved.enforce())
    else:
        assert "tests" in native.calls
        assert report["tests"]["status"] == "passed"
        assert report["status"] == "passed" and report["exit_code"] == 0
        asyncio.run(saved.enforce())


def test_artifact_directory_id_has_unambiguous_boundaries(tmp_path):
    first = tmp_path / "first"
    first.mkdir()
    (first / "a").write_bytes(b"XF\0b\0Y")
    second = tmp_path / "second"
    second.mkdir()
    (second / "a").write_bytes(b"X")
    (second / "b").write_bytes(b"Y")
    assert host_execution.artifact_directory_id(
        first
    ) != host_execution.artifact_directory_id(second)


def test_dagger_aggregate_rejects_unsatisfied_native_check(modules):
    _, collector = modules
    report = {
        "tests": {"status": "not_executed"},
        "checks": [
            {
                "status": "passed",
                "check_contract": {"status": "valid"},
                "native_satisfaction": {"status": "unsatisfied"},
                "native_results": [{"status": "failed"}],
            }
        ],
        "prerequisites": [],
        "artifact_closure": [],
    }
    result = collector._aggregate(report)
    assert result["status"] == "blocked"
    assert result["reason"] == "native_satisfaction_incomplete"


@pytest.mark.parametrize("output_kind", ["file", "directory"])
def test_v2_data_dependency_delivers_isolated_derived_outputs_to_consumer(
    modules, output_kind
):
    _, collector = modules
    import hashlib

    manifest = '{"derived": true}\n'
    writer = (
        "import os; from pathlib import Path; "
        "d = Path(os.environ['ENGINEERING_CI_ARTIFACT_DIR']) / 'generated'; "
        "d.mkdir(parents=True, exist_ok=True); "
        f"(d / 'manifest.json').write_bytes({manifest!r}.encode('utf-8'))"
    )
    output_ref = (
        {
            "kind": "file",
            "path": "generated/manifest.json",
            "sha256": hashlib.sha256(manifest.encode("utf-8")).hexdigest(),
        }
        if output_kind == "file"
        else {"kind": "directory", "path": "generated"}
    )
    actions = {"gen": {"type": "command", "argv": [sys.executable, "-c", writer]}}
    edges = [
        {
            "producer_ref": {"kind": "prerequisite_action", "name": "gen"},
            "consumer_ref": {"kind": "tests"},
            "dependency_kind": "data",
            "requires_pass": True,
            "output_ref": output_ref,
            "source_binding_ref": "git",
            "declaration_ref": _v2_decl("gen"),
            "execution_phase": "pre_execution",
            "resolution": "resolved",
            "unresolved_reason": None,
        }
    ]
    plan = _v2_dagger_plan(edges=edges, prerequisites=["gen"], actions=actions)

    class Native(NativeDouble):
        def __init__(self):
            super().__init__()
            self.test_source = None
            self.weaver_source = None

        def _uv_container(self, source, version):
            self.test_source = source
            return self

        async def exit_code(self):
            return 0

        async def weaver_artifacts(self, source, kind, config):
            self.weaver_source = source
            return MemoryDirectory(
                {
                    "report.json": json.dumps(
                        {"name": "weaver_registry", "status": "passed", "exit_code": 0}
                    )
                }
            )

    native = Native()
    saved = asyncio.run(
        collector.execute(
            native,
            MemoryFile(json.dumps(plan)),
            MemoryDirectory({"ci-selection.toml": "# clean\n"}),
            "3.14",
            "ci-weaver.toml",
        )
    )
    report = json.loads(asyncio.run(saved.report()))
    assert report["status"] == "passed" and report["exit_code"] == 0
    assert report["source_handoff"]["generated_outputs_delivered"] is True
    assert report["artifact_closure"][0]["verified"] is True
    assert "generated/manifest.json" in native.test_source.files
    assert "generated/manifest.json" not in native.weaver_source.files
    assert (
        asyncio.run(
            saved.artifacts.file(
                "produced/prerequisite_action/gen/generated/manifest.json"
            ).contents()
        )
        == manifest
    )
    asyncio.run(saved.enforce())


def test_v2_data_dependency_passed_verdict_without_materialized_file_blocks(modules):
    _, collector = modules
    actions = {"gen": {"type": "command", "argv": [sys.executable, "-c", "pass"]}}
    edges = [
        {
            "producer_ref": {"kind": "prerequisite_action", "name": "gen"},
            "consumer_ref": {"kind": "tests"},
            "dependency_kind": "data",
            "requires_pass": True,
            "output_ref": {"kind": "file", "path": "generated/manifest.json"},
            "source_binding_ref": "git",
            "declaration_ref": _v2_decl("gen"),
            "execution_phase": "pre_execution",
            "resolution": "resolved",
            "unresolved_reason": None,
        }
    ]
    plan = _v2_dagger_plan(edges=edges, prerequisites=["gen"], actions=actions)

    class Native(NativeDouble):
        def _uv_container(self, *args):
            pytest.fail("Verdict passed without File/Directory started consumer")

        async def weaver_artifacts(self, *args):
            return MemoryDirectory(
                {
                    "report.json": json.dumps(
                        {"name": "weaver_registry", "status": "passed", "exit_code": 0}
                    )
                }
            )

    saved = asyncio.run(
        collector.execute(
            Native(),
            MemoryFile(json.dumps(plan)),
            MemoryDirectory({"ci-selection.toml": "# clean\n"}),
            "3.14",
            "ci-weaver.toml",
        )
    )
    report = json.loads(asyncio.run(saved.report()))
    assert report["status"] == "blocked" and report["exit_code"] == 5
    assert report["reason"] == "data_artifact_missing"
    assert report["tests"]["status"] == "not_executed"
    assert report["artifact_closure"][0]["verified"] is False
    assert report["source_handoff"]["generated_outputs_delivered"] is False
    assert report["checks"][0]["status"] == "passed"
    with pytest.raises(RuntimeError):
        asyncio.run(saved.enforce())


def test_v2_host_direct_mutation_of_exported_source_still_fails(modules):
    _, collector = modules
    actions = {
        "mutate": {
            "type": "command",
            "argv": [
                sys.executable,
                "-c",
                "from pathlib import Path; Path('mutated.txt').write_text('bad')",
            ],
        }
    }
    edges = [
        {
            "producer_ref": {"kind": "prerequisite_action", "name": "mutate"},
            "consumer_ref": {"kind": "tests"},
            "dependency_kind": "pre_execution_pass",
            "requires_pass": True,
            "output_ref": None,
            "source_binding_ref": "git",
            "declaration_ref": _v2_decl("mutate"),
            "execution_phase": "pre_execution",
            "resolution": "resolved",
            "unresolved_reason": None,
        }
    ]
    plan = _v2_dagger_plan(
        edges=edges, prerequisites=["mutate"], actions=actions, checks=[]
    )

    class Native(NativeDouble):
        def _uv_container(self, *args):
            return self

        async def exit_code(self):
            return 0

    saved = asyncio.run(
        collector.execute(
            Native(),
            MemoryFile(json.dumps(plan)),
            MemoryDirectory({"ci-selection.toml": "# clean\n"}),
            "3.14",
            "ci-weaver.toml",
        )
    )
    report = json.loads(asyncio.run(saved.report()))
    assert report["reason"] == "exported_source_changed" and report["exit_code"] == 4
    assert report["source_handoff"]["generated_outputs_delivered"] is False
    with pytest.raises(RuntimeError):
        asyncio.run(saved.enforce())


def test_dagger_double_exposes_no_host_and_enforces_workdir_boundaries(modules):
    import sys as _sys

    dagger_stub = _sys.modules["dagger"]
    assert not hasattr(dagger_stub.dag, "host")
    with pytest.raises(RuntimeError, match="escapes workdir"):
        dagger_stub.dag.current_module().workdir("/tmp/outside")
    with pytest.raises(RuntimeError, match="escapes workdir"):
        dagger_stub.dag.current_module().workdir_file("../outside.txt")


def test_v2_data_dependency_preserves_binary_bytes_and_empty_nested_dirs(
    modules, tmp_path
):
    _, collector = modules
    import hashlib

    raw_bytes = b"\x00\xff\x80\x01\xfe\xed\x10binary"
    nested_bytes = b"\x89PNG\r\n\x1a\n"
    writer = (
        "import os; from pathlib import Path; "
        "root = Path(os.environ['ENGINEERING_CI_ARTIFACT_DIR']) / 'generated'; "
        "(root / 'bundle' / 'empty_direct').mkdir(parents=True, exist_ok=True); "
        "(root / 'bundle' / 'nested' / 'empty_child' / 'deep_empty').mkdir(parents=True, exist_ok=True); "
        "mixed = root / 'bundle' / 'mixed'; "
        "(mixed / 'empty_sub').mkdir(parents=True, exist_ok=True); "
        f"(root / 'payload.bin').write_bytes({raw_bytes!r}); "
        f"(mixed / 'data.bin').write_bytes({nested_bytes!r})"
    )
    actions = {"gen": {"type": "command", "argv": [sys.executable, "-c", writer]}}
    edges = [
        {
            "producer_ref": {"kind": "prerequisite_action", "name": "gen"},
            "consumer_ref": {"kind": "tests"},
            "dependency_kind": "data",
            "requires_pass": True,
            "output_ref": {
                "kind": "file",
                "path": "generated/payload.bin",
                "sha256": hashlib.sha256(raw_bytes).hexdigest(),
            },
            "source_binding_ref": "git",
            "declaration_ref": _v2_decl("gen"),
            "execution_phase": "pre_execution",
            "resolution": "resolved",
            "unresolved_reason": None,
        },
        {
            "producer_ref": {"kind": "prerequisite_action", "name": "gen"},
            "consumer_ref": {"kind": "check", "name": "weaver_registry"},
            "dependency_kind": "data",
            "requires_pass": True,
            "output_ref": {"kind": "directory", "path": "generated/bundle"},
            "source_binding_ref": "git",
            "declaration_ref": _v2_decl("gen"),
            "execution_phase": "pre_execution",
            "resolution": "resolved",
            "unresolved_reason": None,
        },
    ]
    plan = _v2_dagger_plan(edges=edges, prerequisites=["gen"], actions=actions)

    class Native(NativeDouble):
        def __init__(self):
            super().__init__()
            self.test_source = None
            self.weaver_source = None

        def _uv_container(self, source, version):
            self.test_source = source
            return self

        async def exit_code(self):
            return 0

        async def weaver_artifacts(self, source, kind, config):
            self.weaver_source = source
            return MemoryDirectory(
                {
                    "report.json": json.dumps(
                        {"name": "weaver_registry", "status": "passed", "exit_code": 0}
                    )
                }
            )

    native = Native()
    saved = asyncio.run(
        collector.execute(
            native,
            MemoryFile(json.dumps(plan)),
            MemoryDirectory({"ci-selection.toml": "# clean\n"}),
            "3.14",
            "ci-weaver.toml",
        )
    )
    report = json.loads(asyncio.run(saved.report()))
    assert report["status"] == "passed" and report["exit_code"] == 0
    assert report["source_handoff"]["generated_outputs_delivered"] is True
    assert report["prerequisites"][0]["execution_location"] == "dagger_module_scratch"
    assert report["prerequisites"][0]["result_origin"] == "module_local_subprocess"
    assert report["prerequisites"][0]["engine_cached"] is False
    assert report["tests"]["generated_input_refs"][0]["path"] == "generated/payload.bin"
    assert report["checks"][0]["generated_input_refs"][0]["path"] == "generated/bundle"
    assert native.test_source.files["generated/payload.bin"] == raw_bytes
    assert "generated/bundle/nested/empty_child/deep_empty" in native.weaver_source.dirs
    exported = tmp_path / "post-cleanup-export"
    asyncio.run(saved.artifacts.export(exported))
    produced_root = exported / "produced" / "prerequisite_action" / "gen" / "generated"
    assert (produced_root / "payload.bin").read_bytes() == raw_bytes
    assert (produced_root / "bundle" / "empty_direct").is_dir()
    assert (produced_root / "bundle" / "mixed" / "empty_sub").is_dir()
    assert (produced_root / "bundle" / "nested" / "empty_child" / "deep_empty").is_dir()
    assert (
        produced_root / "bundle" / "mixed" / "data.bin"
    ).read_bytes() == nested_bytes
    asyncio.run(saved.enforce())


def test_v2_multi_consumer_different_failures_preserved_while_independent_check_runs(
    modules,
):
    _, collector = modules
    writer = (
        "import os; from pathlib import Path; "
        "d = Path(os.environ['ENGINEERING_CI_ARTIFACT_DIR']) / 'generated'; "
        "d.mkdir(parents=True, exist_ok=True); "
        "(d / 'actual.txt').write_text('actual', encoding='utf-8')"
    )
    actions = {
        "missing_gen": {"type": "command", "argv": [sys.executable, "-c", "pass"]},
        "bad_digest": {"type": "command", "argv": [sys.executable, "-c", writer]},
    }
    edges = [
        {
            "producer_ref": {"kind": "prerequisite_action", "name": "missing_gen"},
            "consumer_ref": {"kind": "tests"},
            "dependency_kind": "data",
            "requires_pass": True,
            "output_ref": {"kind": "file", "path": "generated/missing.txt"},
            "source_binding_ref": "git",
            "declaration_ref": _v2_decl("missing_gen"),
            "execution_phase": "pre_execution",
            "resolution": "resolved",
            "unresolved_reason": None,
        },
        {
            "producer_ref": {"kind": "prerequisite_action", "name": "bad_digest"},
            "consumer_ref": {"kind": "execution_report", "name": "artifacts"},
            "dependency_kind": "data",
            "requires_pass": True,
            "output_ref": {
                "kind": "file",
                "path": "generated/actual.txt",
                "sha256": "0" * 64,
            },
            "source_binding_ref": "git",
            "declaration_ref": _v2_decl("bad_digest"),
            "execution_phase": "pre_execution",
            "resolution": "resolved",
            "unresolved_reason": None,
        },
    ]
    plan = _v2_dagger_plan(
        edges=edges,
        prerequisites=["missing_gen", "bad_digest"],
        actions=actions,
        checks=["weaver_registry"],
    )

    class Native(NativeDouble):
        async def weaver_artifacts(self, source, kind, config):
            return MemoryDirectory(
                {
                    "report.json": json.dumps(
                        {"name": "weaver_registry", "status": "passed", "exit_code": 0}
                    )
                }
            )

    saved = asyncio.run(
        collector.execute(
            Native(),
            MemoryFile(json.dumps(plan)),
            MemoryDirectory({"ci-selection.toml": "# clean\n"}),
            "3.14",
            "ci-weaver.toml",
        )
    )
    report = json.loads(asyncio.run(saved.report()))
    assert report["status"] == "blocked" and report["exit_code"] == 5
    assert report["checks"][0]["name"] == "weaver_registry"
    assert report["checks"][0]["status"] == "passed"
    assert report["source_handoff"]["generated_outputs_delivered"] is False
    consumers = {
        (c["consumer_ref"]["kind"], c["consumer_ref"].get("name")): [
            f["reason"] for f in c["failures"]
        ]
        for c in report["artifact_delivery"]["consumers"]
    }
    assert consumers == {
        ("execution_report", "artifacts"): ["data_artifact_digest_mismatch"],
        ("tests", None): ["data_artifact_missing"],
    }


@pytest.mark.parametrize("has_git", [True, False])
def test_docs_check_bridges_lychee_and_git_containers_honestly(
    modules, monkeypatch, has_git
):
    _, collector = modules
    import hashlib
    import shutil

    content = "# Generated Doc\n\nSee [anchor](README.md).\n"
    writer = (
        "import os; from pathlib import Path; "
        "d = Path(os.environ['ENGINEERING_CI_ARTIFACT_DIR']) / 'docs'; "
        "d.mkdir(parents=True, exist_ok=True); "
        f"(d / 'generated.md').write_bytes({content!r}.encode('utf-8'))"
    )
    actions = {"gen_docs": {"type": "command", "argv": [sys.executable, "-c", writer]}}
    edges = [
        {
            "producer_ref": {"kind": "prerequisite_action", "name": "gen_docs"},
            "consumer_ref": {"kind": "check", "name": "docs"},
            "dependency_kind": "data",
            "requires_pass": True,
            "output_ref": {
                "kind": "file",
                "path": "docs/generated.md",
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            },
            "source_binding_ref": "git",
            "declaration_ref": _v2_decl("gen_docs"),
            "execution_phase": "pre_execution",
            "resolution": "resolved",
            "unresolved_reason": None,
        }
    ]
    plan = _v2_dagger_plan(
        edges=edges,
        prerequisites=["gen_docs"],
        actions=actions,
        tests=[],
        checks=["docs"],
    )
    plan["git"] = {
        "snapshot": "worktree",
        "base_commit": "0" * 40,
        "changed_paths": [],
    }
    hygiene_toml = (
        "schema_version = 1\n"
        'checks = ["docs"]\n'
        "[lychee]\n"
        'executable = "lychee"\n'
        'arguments = ["--offline", "--no-progress"]\n'
    )
    source_files = {
        "ci-selection.toml": "# clean\n",
        "ci-hygiene.toml": hygiene_toml,
        "README.md": "# Root\n",
    }
    if has_git:
        source_files[".git/HEAD"] = "ref: refs/heads/main\n"

    real_which = shutil.which
    monkeypatch.setattr(
        shutil,
        "which",
        lambda cmd, *a, **kw: (
            None if cmd in {"lychee", "git"} else real_which(cmd, *a, **kw)
        ),
    )

    class DocsNative(NativeDouble):
        def __init__(self):
            super().__init__()
            self.git_calls = []
            self.lychee_calls = []

        async def _run_git(self, source, argv_tail):
            self.git_calls.append(list(argv_tail))
            if ".git/HEAD" not in source.files:
                return {
                    "exit_code": 128,
                    "stdout": "",
                    "stderr": "fatal: not a git repository",
                }
            return {
                "exit_code": 0,
                "stdout": "README.md\0",
                "stderr": "",
            }

        async def _run_lychee(self, source, argv_tail):
            self.lychee_calls.append((set(source.files), list(argv_tail)))
            assert "docs/generated.md" in source.files
            return {"exit_code": 0, "stdout": "1 OK\n", "stderr": ""}

    native = DocsNative()
    saved = asyncio.run(
        collector.execute(
            native,
            MemoryFile(json.dumps(plan)),
            MemoryDirectory(source_files),
            "3.14",
            "ci-weaver.toml",
        )
    )
    report = json.loads(asyncio.run(saved.report()))
    assert native.git_calls == [["ls-files", "-z"]]
    docs_check = report["checks"][0]
    assert docs_check["name"] == "docs"
    if has_git:
        assert native.lychee_calls == [
            (
                set(source_files) | {"docs/generated.md"},
                ["--offline", "--no-progress", "docs/generated.md"],
            )
        ]
        assert report["status"] == "passed" and report["exit_code"] == 0
        assert docs_check["status"] == "passed"
        assert any(
            ref.get("kind") == "generated_input"
            and ref.get("path") == "docs/generated.md"
            for ref in docs_check["native_results"][1]["input_refs"]
        )
        asyncio.run(saved.enforce())
    else:
        docs_native_ids = {
            r["tool"]["access_id"]: r for r in docs_check["native_results"]
        }
        assert docs_native_ids["git/ls-files"]["status"] == "failed"
        assert docs_check["native_satisfaction"]["status"] == "unsatisfied"
        assert report["status"] == "blocked"
        assert report["reason"] == "native_satisfaction_incomplete"
        with pytest.raises(RuntimeError):
            asyncio.run(saved.enforce())

        # When targets do not expand onto disk without Git inventory, Lychee never runs.
        no_target_plan = _v2_dagger_plan(
            edges=[],
            prerequisites=[],
            actions={},
            tests=[],
            checks=["docs"],
        )
        no_target_plan["git"] = {
            "snapshot": "worktree",
            "base_commit": "0" * 40,
            "changed_paths": ["docs/deleted.md"],
        }
        native_empty = DocsNative()
        saved_empty = asyncio.run(
            collector.execute(
                native_empty,
                MemoryFile(json.dumps(no_target_plan)),
                MemoryDirectory(
                    {
                        "ci-selection.toml": "# clean\n",
                        "ci-hygiene.toml": hygiene_toml,
                    }
                ),
                "3.14",
                "ci-weaver.toml",
            )
        )
        empty_report = json.loads(asyncio.run(saved_empty.report()))
        assert native_empty.lychee_calls == []
        assert empty_report["status"] == "failed"
        assert empty_report["checks"][0]["reason"] == "git_inventory_failed"
        with pytest.raises(RuntimeError):
            asyncio.run(saved_empty.enforce())
