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
    def __init__(self, contents):
        self.text = contents

    async def contents(self):
        return self.text


class MemoryDirectory:
    def __init__(self, files=None):
        self.files = dict(files or {})

    def with_new_file(self, path, contents):
        return MemoryDirectory({**self.files, path: contents})

    def with_directory(self, prefix, directory):
        return MemoryDirectory(
            {**self.files, **{prefix + "/" + k: v for k, v in directory.files.items()}}
        )

    def file(self, path):
        return MemoryFile(self.files[path])

    async def export(self, directory):
        for name, value in self.files.items():
            target = Path(directory) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(value, encoding="utf-8")


@pytest.fixture
def modules(monkeypatch):
    stub = types.ModuleType("dagger")
    stub.Directory = MemoryDirectory
    stub.field = dataclasses.field
    stub.function = lambda function: function
    stub.object_type = dataclasses.dataclass
    stub.dag = types.SimpleNamespace(directory=MemoryDirectory)
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
def test_existing_test_precondition_blocks_only_its_native_consumer(
    modules, fields, reason
):
    _, collector = modules

    class Independent(NativeDouble):
        async def weaver_artifacts(self, source, kind, config):
            self.calls.append("independent native check")
            return MemoryDirectory(
                {
                    "report.json": json.dumps(
                        {
                            "name": "weaver_registry",
                            "status": "passed",
                            "exit_code": 0,
                        }
                    )
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
        assert report["status"] == "blocked" and report["reason"] == reason
        assert report["tests"]["status"] == "not_executed"
        assert report["checks"][0]["status"] == "passed"
        assert "checks/weaver_registry/report.json" in files.files
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


def test_host_write_invalidates_binding_and_retains_original_result(modules):
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
    saved = asyncio.run(
        collector.execute(
            object(),
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
    with pytest.raises(RuntimeError):
        asyncio.run(saved.enforce())
