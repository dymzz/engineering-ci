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

    def with_file(self, path, file):
        return MemoryDirectory({**self.files, path: file.text})

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
    stub.dag = types.SimpleNamespace(
        directory=MemoryDirectory,
        host=lambda: types.SimpleNamespace(
            file=lambda path: MemoryFile(Path(path).read_text(encoding="utf-8"))
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
