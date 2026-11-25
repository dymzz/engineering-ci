"""Plan result contract tests use explicit Dagger doubles, never tool evidence."""

import asyncio
import dataclasses
import hashlib
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

from engineering_ci import check_contract, plan_contract, weaver_tool
from engineering_ci import execution as host_execution

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

    async def digest(self):
        return hashlib.sha256((await self.id()).encode()).hexdigest()

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


def test_preparation_operation_exit_is_preserved_without_becoming_a_test_exit(modules):
    _, collector = modules

    class PreparationFailure(RuntimeError):
        exit_code = 2
        stdout = "original preparation stdout"
        stderr = "lockfile missing"

        def __init__(self, message):
            super().__init__(message)
            self.command = ["uv", "sync", "--locked"]

    item = collector._native_failure("tests", PreparationFailure("operation failed"))
    assert item["status"] == "not_executed" and item["exit_code"] is None
    assert item["failed_operation"]["exit_code"] == 2
    assert item["failed_operation"]["stderr"] == "lockfile missing"
    assert "native_result" not in item


def test_artifact_identity_query_failure_keeps_completed_native_test_exit(modules):
    _, collector = modules
    adapter = __import__(
        collector.__package__ + ".native_results", fromlist=["test_result"]
    )

    class Probe:
        def with_exec(self, command):
            return self

        async def stdout(self):
            return json.dumps({"executable": "python", "tool_version": "3.14"})

    class UnavailableIdentity(MemoryDirectory):
        async def digest(self):
            raise RuntimeError("controlled metadata transport failure")

    item = asyncio.run(
        adapter.test_result(
            {"status": "failed", "exit_code": 1},
            Probe(),
            MemoryDirectory(),
            "unittest",
            ["tests.test_native"],
            artifacts=UnavailableIdentity(),
        )
    )
    assert item["status"] == "failed" and item["exit_code"] == 1
    assert item["native_result"]["native_exit_code"] == 1
    assert (
        item["native_result"]["evidence"]["observation"]["artifact_identity"]["status"]
        == "unavailable"
    )


def test_export_handoff_links_directory_bytes_parent_and_original_host_report(
    modules, monkeypatch
):
    from engineering_ci import check_contract, selection

    _, collector = modules
    source = MemoryDirectory({"input.txt": "original source bytes"})
    plan = {
        "kind": plan_contract.PLAN_KIND,
        "schema_version": 1,
        "generator": {"name": "engineering-ci", "version": "0.7.0"},
        "git": {"snapshot": "worktree"},
        "selection": {"tests": []},
        "check_scope": [{"name": "source", "status": "selected"}],
    }
    observed = {}

    def host(plan, *, root, run_instance, **kwargs):
        observed["content_id"] = selection.directory_content_id(root)
        observed["instance"] = run_instance
        native = check_contract.native_result(
            check_contract.definition_for("source"),
            source=check_contract.source_ref("worktree"),
            status="passed",
            result_origin="host",
            result_instance=run_instance + ":host:source",
            evidence={"unit_double": True},
            runner_ref="execution:dagger_export",
        )
        return {
            "kind": host_execution.REPORT_KIND,
            "schema_version": 1,
            "status": "passed",
            "exit_code": 0,
            "checks": [
                {
                    "name": "source",
                    "status": "passed",
                    "native_results": [native],
                    "check_contract": {"status": "valid"},
                }
            ],
            "prerequisites": [],
        }

    monkeypatch.setattr(host_execution, "execute", host)

    async def run():
        saved = await collector.execute(
            object(),
            MemoryFile(json.dumps(plan)),
            source,
            "3.14",
            "ci-weaver.toml",
            run_ref="run/123",
            attempt_ref="attempt/2",
        )
        report = json.loads(await saved.report())
        handoff = report["source_handoff"]
        assert (
            handoff["before_host_content_id"]
            == observed["content_id"]
            == handoff["exported_content_id"]
        )
        assert handoff["after_host_content_id"] == handoff["exported_content_id"]
        assert handoff["directory"]["digest"] == await source.digest()
        assert handoff["parent_execution_ref"] == observed["instance"]
        assert handoff["run_ref"] == "run/123" and handoff["attempt_ref"] == "attempt/2"
        raw = await saved.artifacts.file("host/report.json").contents()
        assert hashlib.sha256(raw.encode()).hexdigest() == handoff["host_report_sha256"]
        result = json.loads(raw)["checks"][0]["native_results"][0]
        assert handoff["host_result_ids"] == [result["result_id"]]
        assert result["result_origin"] == "host"
        assert report["source_binding_verified"] == "unknown"
        assert report["execution_identity"]["run_attempt_verification"] == "unverified"

    asyncio.run(run())


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
    assert report["status"] == "passed", json.dumps(report, indent=2)
    assert report["exit_code"] == 0, json.dumps(report, indent=2)
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


@pytest.mark.parametrize("native_exit", [0, 23])
def test_native_prerequisite_uses_existing_v2_closure_and_preserves_failure(
    modules, monkeypatch, native_exit
):
    _, collector = modules
    payload = b"native-derived\x00\xff"
    action = {
        "type": "dagger_command",
        "argv": ["python", "build.py"],
        "image": "example.invalid/python@sha256:" + "a" * 64,
        "platform": "linux/amd64",
        "cache": "source",
    }
    edge = {
        "producer_ref": {"kind": "prerequisite_action", "name": "gen"},
        "consumer_ref": {"kind": "execution_report", "name": "artifacts"},
        "dependency_kind": "data",
        "requires_pass": True,
        "output_ref": {
            "kind": "file",
            "path": "generated/data.bin",
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
        "source_binding_ref": "git",
        "declaration_ref": _v2_decl("gen"),
        "execution_phase": "pre_execution",
        "resolution": "resolved",
        "unresolved_reason": None,
    }
    plan = _v2_dagger_plan(
        edges=[edge],
        prerequisites=["gen"],
        actions={"gen": action},
        tests=[],
        checks=[],
    )
    # An unselected declaration must not become a producer or path operation.
    plan["prerequisite_actions"]["unselected/action"] = {
        "type": "command",
        "argv": ["never-executed"],
    }

    class Native:
        calls = 0

        async def _run_dagger_prerequisite(self, source, name, spec):
            self.calls += 1
            assert spec == action
            return (
                {
                    "name": name,
                    "type": "dagger_command",
                    "status": "passed" if native_exit == 0 else "failed",
                    "exit_code": native_exit,
                    "stdout": "original out",
                    "stderr": "original diagnostic",
                    "execution_location": "dagger_container",
                    "result_origin": "dagger",
                },
                MemoryDirectory({"generated/data.bin": payload}),
            )

    def no_scratch_import():
        pytest.fail("native output was replaced with a Module scratch import")

    monkeypatch.setattr(collector.dag, "current_module", no_scratch_import)
    native = Native()
    saved = asyncio.run(
        collector.execute(
            native,
            MemoryFile(json.dumps(plan)),
            MemoryDirectory({"ci-selection.toml": "# stable\n"}),
            "3.14",
            "ci-weaver.toml",
        )
    )
    report = json.loads(asyncio.run(saved.report()))
    assert report["prerequisites"][0]["exit_code"] == native_exit
    assert report["prerequisites"][0]["stderr"] == "original diagnostic"
    assert saved.artifacts.file("prerequisites/gen/generated/data.bin").data == payload
    if native_exit == 0:
        assert report["status"] == "passed", report
        assert report["artifact_closure"][0]["verified"]
        assert (
            saved.artifacts.file(
                "produced/prerequisite_action/gen/generated/data.bin"
            ).data
            == payload
        )
        asyncio.run(saved.enforce())
    else:
        assert report["status"] != "passed"
        assert not any(item["verified"] for item in report.get("artifact_closure", ()))
        with pytest.raises(RuntimeError):
            asyncio.run(saved.enforce())
    assert native.calls == 1


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
    assert report["status"] == "passed", json.dumps(report, indent=2)
    assert report["exit_code"] == 0, json.dumps(report, indent=2)
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
                "observation": {"execution_location": "dagger_container"},
            }

        async def _run_lychee(self, source, argv_tail):
            self.lychee_calls.append((set(source.files), list(argv_tail)))
            assert "docs/generated.md" in source.files
            return {
                "exit_code": 0,
                "stdout": "1 OK\n",
                "stderr": "",
                "observation": {
                    "execution_location": "dagger_container",
                    "freshness_policy": "native_exec_cache",
                    "process_started_at": "controlled-process-time",
                    "origin_execution_attributed": False,
                },
            }

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
        assert docs_check["result_origin"] == "dagger"
        assert all(
            item["result_origin"] == "dagger" for item in docs_check["native_results"]
        )
        assert all(
            item["runner_ref"] == "execution:dagger"
            for item in docs_check["native_results"]
        )
        assert (
            docs_check["observation"]["process_started_at"] == "controlled-process-time"
        )
        assert (
            docs_check["native_results"][1]["evidence"]["observation"]
            == docs_check["observation"]
        )
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


def test_policy_only_selection_plan_runs_host_check_and_propagates_refusal(
    tmp_path, modules, monkeypatch
):
    import shutil

    from engineering_ci import execution as real_execution
    from engineering_ci import selection as real_selection

    _, collector = modules
    fixture = Path(__file__).resolve().parents[1] / "tests/fixtures/selection-app"
    hygiene_toml = (fixture / "ci-hygiene.toml").read_text(encoding="utf-8")
    assert "--files-from" not in hygiene_toml

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "ci-selection.toml").write_text(
        (fixture / "ci-selection.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (repo / "ci-hygiene.toml").write_text(hygiene_toml, encoding="utf-8")
    tach_toml = (fixture / "tach.toml").read_text(encoding="utf-8")
    (repo / "tach.toml").write_text(tach_toml, encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")

    changes = real_selection.ChangeSet(
        root=repo,
        base_ref="base",
        head_ref="head",
        base_commit="1" * 40,
        head_commit="2" * 40,
        tree="a" * 40,
        paths=("docs/guide.md",),
        worktree_dirty=False,
        complete=True,
    )
    cfg = real_selection.load_config(repo / "ci-selection.toml")
    sel = real_selection.select(changes, cfg, root=repo)
    plan = real_selection.plan(
        sel,
        config=cfg,
        config_path=repo / "ci-selection.toml",
        root=repo,
    )
    assert "expected_checks" in plan
    assert plan["dependency_contract"]["schema_version"] == 1

    class DocsOnlyNative(NativeDouble):
        def __init__(self):
            super().__init__()
            self.lychee_argvs = []

        async def _run_git(self, source, argv_tail):
            return {"exit_code": 0, "stdout": "docs/guide.md\0", "stderr": ""}

        async def _run_lychee(self, source, argv_tail):
            self.lychee_argvs.append(list(argv_tail))
            return {"exit_code": 0, "stdout": "ok", "stderr": ""}

    real_which = shutil.which
    monkeypatch.setattr(
        shutil,
        "which",
        lambda cmd, *a, **kw: (
            None if cmd in {"lychee", "git"} else real_which(cmd, *a, **kw)
        ),
    )
    native = DocsOnlyNative()
    source_dir = MemoryDirectory(
        {
            "ci-selection.toml": (repo / "ci-selection.toml").read_text(
                encoding="utf-8"
            ),
            "ci-hygiene.toml": hygiene_toml,
            "tach.toml": tach_toml,
            "docs/guide.md": "# Guide\n",
        }
    )
    saved = asyncio.run(
        collector.execute(
            native,
            MemoryFile(json.dumps(plan)),
            source_dir,
            "3.14",
            "ci-weaver.toml",
        )
    )
    report = json.loads(asyncio.run(saved.report()))
    assert report["status"] == "passed", json.dumps(report, indent=2)
    assert [c["name"] for c in report["checks"]] == ["whitespace", "docs"]
    docs_check = next(c for c in report["checks"] if c["name"] == "docs")
    assert docs_check["status"] == "passed"
    assert native.lychee_argvs == [["--offline", "--no-progress", "docs/guide.md"]]
    asyncio.run(saved.enforce())

    # If the Host adapter ever rejects or blocks with no child checks, never pass silently.
    monkeypatch.setattr(
        real_execution,
        "execute",
        lambda *_args, **_kwargs: {
            "status": "not_executed",
            "reason": "expected_scope_mismatch",
            "exit_code": 2,
            "checks": [],
            "prerequisites": [],
        },
    )
    blocked = asyncio.run(
        collector.execute(
            DocsOnlyNative(),
            MemoryFile(json.dumps(plan)),
            source_dir,
            "3.14",
            "ci-weaver.toml",
        )
    )
    blocked_report = json.loads(asyncio.run(blocked.report()))
    assert blocked_report["status"] == "blocked"
    assert blocked_report["reason"] == "expected_scope_mismatch"
    assert blocked_report["checks"][0]["status"] == "not_executed"
    assert blocked_report["checks"][0]["reason"] == "expected_scope_mismatch"
    with pytest.raises(RuntimeError):
        asyncio.run(blocked.enforce())


def test_external_hygiene_scope_drives_dag_producer_and_dagger_consumption(
    tmp_path, modules, monkeypatch
):
    import shutil

    from engineering_ci import hygiene as real_hygiene
    from engineering_ci import selection as real_selection

    _, collector = modules
    fixture = Path(__file__).resolve().parents[1] / "tests/fixtures/selection-app"
    tach_toml = (fixture / "tach.toml").read_text(encoding="utf-8")
    base_selection = (fixture / "ci-selection.toml").read_text(encoding="utf-8")

    marker_file = tmp_path / "gen_docs_launches.log"
    generated_payload = "# Generated Guide\n"
    gen_script = (
        "import os; from pathlib import Path; "
        f"m = Path({str(marker_file)!r}); "
        "m.write_text((m.read_text(encoding='utf-8') if m.exists() else '') + '1\\n', encoding='utf-8'); "
        "out = Path(os.environ['ENGINEERING_CI_ARTIFACT_DIR']) / 'docs/generated.md'; "
        "out.parent.mkdir(parents=True, exist_ok=True); "
        f"out.write_text({generated_payload!r}, encoding='utf-8')"
    )

    repo = tmp_path / "repo"
    repo.mkdir()
    selection_text = (
        base_selection
        + "\n[prerequisite_actions.gen_docs]\n"
        + 'type = "command"\n'
        + f"argv = {json.dumps([sys.executable, '-c', gen_script])}\n\n"
        + "[[dependency_edges]]\n"
        + 'producer_ref = { kind = "prerequisite_action", name = "gen_docs" }\n'
        + 'consumer_ref = { kind = "check", name = "docs" }\n'
        + 'dependency_kind = "data"\n'
        + "requires_pass = true\n"
        + 'output_ref = { kind = "file", path = "docs/generated.md" }\n\n'
        + "[[dependency_edges]]\n"
        + 'producer_ref = { kind = "prerequisite_action", name = "gen_docs" }\n'
        + 'consumer_ref = { kind = "execution_report", name = "artifacts" }\n'
        + 'dependency_kind = "data"\n'
        + "requires_pass = true\n"
        + 'output_ref = { kind = "file", path = "docs/generated.md" }\n'
    )
    (repo / "ci-selection.toml").write_text(selection_text, encoding="utf-8")
    (repo / "tach.toml").write_text(tach_toml, encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")

    # Scenario 1: repo default enables docs -> select() includes dependency-only gen_docs;
    # external policy disables docs -> Plan retracts gen_docs and Dagger never launches it.
    hygiene_on_text = (
        'schema_version = 1\nchecks = ["whitespace", "docs"]\n'
        '[lychee]\nexecutable = "lychee"\narguments = ["--offline", "--no-progress"]\n'
    )
    hygiene_off_text = (
        'schema_version = 1\nchecks = ["whitespace"]\n'
        '[lychee]\nexecutable = "lychee"\narguments = ["--offline", "--no-progress"]\n'
    )
    (repo / "ci-hygiene.toml").write_text(hygiene_on_text, encoding="utf-8")
    ext_off = tmp_path / "external-off.toml"
    ext_off.write_text(hygiene_off_text, encoding="utf-8")

    changes = real_selection.ChangeSet(
        root=repo,
        base_ref="base",
        head_ref="head",
        base_commit="1" * 40,
        head_commit="2" * 40,
        tree="a" * 40,
        paths=("docs/guide.md",),
        worktree_dirty=False,
        complete=True,
    )
    cfg = real_selection.load_config(repo / "ci-selection.toml")
    sel_default_on = real_selection.select(changes, cfg, root=repo)
    assert sel_default_on.prerequisites == frozenset({"gen_docs"})
    assert sel_default_on.base_prerequisites == frozenset()

    plan_off = real_selection.plan(
        sel_default_on,
        config=cfg,
        config_path=repo / "ci-selection.toml",
        root=repo,
        hygiene_config=real_hygiene.load_config(ext_off),
        hygiene_config_path=ext_off,
    )
    assert plan_off["selection"]["prerequisites"] == []
    assert plan_off["dependency_contract"]["edges"] == []

    class DocsNative(NativeDouble):
        def __init__(self):
            super().__init__()
            self.lychee_argvs = []

        async def _run_git(self, source, argv_tail):
            return {"exit_code": 0, "stdout": "docs/guide.md\0", "stderr": ""}

        async def _run_lychee(self, source, argv_tail):
            self.lychee_argvs.append(list(argv_tail))
            return {"exit_code": 0, "stdout": "ok", "stderr": ""}

    real_which = shutil.which
    monkeypatch.setattr(
        shutil,
        "which",
        lambda cmd, *a, **kw: (
            None if cmd in {"lychee", "git"} else real_which(cmd, *a, **kw)
        ),
    )

    source_off = MemoryDirectory(
        {
            "ci-selection.toml": selection_text,
            "ci-hygiene.toml": hygiene_on_text,
            "tach.toml": tach_toml,
            "docs/guide.md": "# Guide\n",
        }
    )
    native_off = DocsNative()
    saved_off = asyncio.run(
        collector.execute(
            native_off,
            MemoryFile(json.dumps(plan_off)),
            source_off,
            "3.14",
            "ci-weaver.toml",
        )
    )
    report_off = json.loads(asyncio.run(saved_off.report()))
    assert report_off["status"] == "passed", json.dumps(report_off, indent=2)
    assert report_off["prerequisites"] == []
    assert [c["name"] for c in report_off["checks"]] == ["whitespace"]
    assert not marker_file.exists()
    assert native_off.lychee_argvs == []
    asyncio.run(saved_off.enforce())

    # Scenario 2: repo default disables docs -> select() omits gen_docs;
    # external policy enables docs -> Plan activates gen_docs + data edges, and Dagger
    # launches gen_docs once before docs and delivers docs/generated.md.
    (repo / "ci-hygiene.toml").write_text(hygiene_off_text, encoding="utf-8")
    ext_on = tmp_path / "external-on.toml"
    ext_on.write_text(hygiene_on_text, encoding="utf-8")

    sel_default_off = real_selection.select(changes, cfg, root=repo)
    assert sel_default_off.prerequisites == frozenset()
    assert sel_default_off.base_prerequisites == frozenset()

    plan_on = real_selection.plan(
        sel_default_off,
        config=cfg,
        config_path=repo / "ci-selection.toml",
        root=repo,
        hygiene_config=real_hygiene.load_config(ext_on),
        hygiene_config_path=ext_on,
    )
    assert plan_on["selection"]["prerequisites"] == ["gen_docs"]
    assert plan_on["dependency_contract"]["schema_version"] == 2
    assert {
        (e["producer_ref"]["name"], e["consumer_ref"]["kind"])
        for e in plan_on["dependency_contract"]["edges"]
    } == {("gen_docs", "check"), ("gen_docs", "execution_report")}

    source_on = MemoryDirectory(
        {
            "ci-selection.toml": selection_text,
            "ci-hygiene.toml": hygiene_off_text,
            "tach.toml": tach_toml,
            "docs/guide.md": "# Guide\n",
        }
    )
    native_on = DocsNative()
    saved_on = asyncio.run(
        collector.execute(
            native_on,
            MemoryFile(json.dumps(plan_on)),
            source_on,
            "3.14",
            "ci-weaver.toml",
        )
    )
    report_on = json.loads(asyncio.run(saved_on.report()))
    assert report_on["status"] == "passed", json.dumps(report_on, indent=2)
    assert marker_file.read_text(encoding="utf-8").splitlines() == ["1"]
    assert [p["name"] for p in report_on["prerequisites"]] == ["gen_docs"]
    assert report_on["prerequisites"][0]["status"] == "passed"
    assert report_on["source_handoff"]["generated_outputs_delivered"] is True
    docs_check = next(c for c in report_on["checks"] if c["name"] == "docs")
    assert docs_check["status"] == "passed"
    assert [ref["path"] for ref in docs_check["generated_input_refs"]] == [
        "docs/generated.md"
    ]
    assert native_on.lychee_argvs == [
        ["--offline", "--no-progress", "docs/generated.md", "docs/guide.md"]
    ]
    asyncio.run(saved_on.enforce())


def test_pr103_pr108_converged_dagger_profile_chain(modules, tmp_path):
    import shutil

    from test_execution import ROOT, FakeRunner, run
    from test_reconciliation import _attach_effective_profile_policy

    from engineering_ci import check_contract, execution, expected_checks, selection

    _result, collector = modules
    fixture = ROOT / "tests/fixtures/selection-app"
    repo = tmp_path / "repo"
    shutil.copytree(fixture, repo)
    run(repo, "init", "-q", "-b", "main")
    run(repo, "config", "user.email", "dagger@example.invalid")
    run(repo, "config", "user.name", "Dagger Test")
    run(repo, "config", "core.autocrlf", "false")
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", "init")
    base = run(repo, "rev-parse", "HEAD").strip()

    declared = ["whitespace", "forwarding"]
    prof_ref = check_contract.profile_ref("eci/default", "1.0.0")
    cfg_path = repo / "ci-selection.toml"
    rules = expected_checks.governed_domain_rules(repo, config_path=cfg_path)
    profile = expected_checks.build_effective_profile(
        profile_ref=prof_ref,
        profile_revision="rev-pr103-108",
        approval_source="governance-signed",
        effective_at="2026-11-16T00:00:00Z",
        evaluated_at="2026-11-16T00:00:00Z",
        context="commit",
        action="commit",
        target="HEAD",
        declared_checks=declared,
        required_obligations=[
            {"obligation_ref": f"req-{n}", "check_names": [n]} for n in declared
        ],
        governed_rules=rules,
        domain_complete=True,
        authority_verified=True,
    )
    cfg = _attach_effective_profile_policy(cfg_path, profile)
    (repo / "docs/guide.md").write_text("# updated\n", encoding="utf-8")
    (repo / "src/pkg/mod.py").write_text("VALUE = 77\n", encoding="utf-8")
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", "change")

    cs = selection.changeset(repo, base=base, head="HEAD", mode="commit")
    sel = selection.select(cs, cfg, root=repo)
    plan = selection.plan(
        sel,
        config=cfg,
        config_path=cfg_path,
        root=repo,
        effective_profile=profile,
    )
    # Simulate exporting the target tree into an isolated Dagger scratch directory
    # where the original absolute repo_root path is outside exported_dir:
    exported_dir = tmp_path / "dagger_export"
    shutil.copytree(repo, exported_dir, ignore=shutil.ignore_patterns(".git"))

    rel_plan = collector._plan_with_repo_relative_configs(plan)
    assert rel_plan["inputs"]["config"]["path"] == "ci-selection.toml"
    verified = execution._profile_reference(
        rel_plan, root=exported_dir, execution_context="dagger_export"
    )
    assert verified == prof_ref

    selected_checks = [
        c for c in plan["check_scope"] if c.get("status") in {"selected", "unknown"}
    ]
    assert selected_checks
    host_plan = collector._host_adapter_plan(plan, selected_checks)
    assert "expected_checks" not in host_plan
    assert (
        execution._profile_reference(
            host_plan, root=exported_dir, execution_context="dagger_export"
        )
        is None
    )
    entry = execution.run_host_check(
        host_plan,
        selected_checks[0],
        root=exported_dir,
        runner=FakeRunner(),
        execution_context="dagger_export",
        profile_ref=verified,
    )
    assert entry["native_result"]["check"]["profile_ref"] == prof_ref
    assert entry["plan_authorized"] is True

    # Tampering config on exported_dir or dropping source_tree_id fails closed:
    tampered_dir = tmp_path / "dagger_tampered"
    shutil.copytree(exported_dir, tampered_dir)
    (tampered_dir / "ci-selection.toml").write_text(
        (tampered_dir / "ci-selection.toml").read_text(encoding="utf-8")
        + "\n# tampered\n",
        encoding="utf-8",
    )
    assert (
        execution._profile_reference(
            rel_plan, root=tampered_dir, execution_context="dagger_export"
        )
        is None
    )
    no_tree_plan = json.loads(json.dumps(rel_plan))
    no_tree_plan["git"]["source_tree_id"] = None
    assert (
        execution._profile_reference(
            no_tree_plan, root=exported_dir, execution_context="dagger_export"
        )
        is None
    )


class _ContractTestContainer:
    """A labelled double; only the real adapter/contract runs in these tests."""

    def __init__(self, owner, *, metadata=False):
        self.owner = owner
        self.metadata = metadata

    def with_exec(self, command, **kwargs):
        if "unittest" in command:
            self.owner.test_invocations += 1
        return _ContractTestContainer(self.owner, metadata="-c" in command)

    async def exit_code(self):
        return 0

    async def stdout(self):
        if self.metadata:
            return json.dumps({"executable": "python", "tool_version": "3.14"})
        return "controlled test output"

    async def stderr(self):
        return ""


class _ContractNativeDouble:
    def __init__(self, collector):
        self.adapter = __import__(
            collector.__package__ + ".native_results", fromlist=["weaver_result"]
        )
        self.test_invocations = 0
        self.weaver_invocations = 0
        self.consumer_sources = {}

    def _uv_container(self, source, version):
        self.consumer_sources["tests"] = source
        return _ContractTestContainer(self)

    async def _run_pytest(self, source, version, target, *, _targets):
        self.test_invocations += 1
        self.consumer_sources["tests"] = source
        return types.SimpleNamespace(
            exit_code=0,
            output="controlled pytest output",
            xml="<testsuite/>",
            container=_ContractTestContainer(self),
        )

    def _pytest_bundle(self, execution):
        return MemoryDirectory({"junit.xml": execution.xml, "exit-code.txt": "0\n"})

    async def _weaver_execution(self, source, kind, config, **kwargs):
        self.weaver_invocations += 1
        self.consumer_sources["weaver"] = source
        stages = ("registry-check", "registry-package", "registry-local-check")
        raw = {
            "name": "weaver_" + kind,
            "status": "passed",
            "exit_code": 0,
            "gate_exit_code": 0,
            "baseline_required": False,
            "version": weaver_tool.VERSION,
            "tool_asset_sha256": weaver_tool.LINUX_SHA256,
            "source_digest": await source.digest(),
            "inputs": {"config_sha256": "c" * 64},
            "commands": [
                {
                    "argv": ["weaver", "registry", "check"],
                    "exit_code": 0,
                    "stdout": stage + ".stdout",
                    "stderr": stage + ".stderr",
                }
                for stage in stages
            ],
        }
        files = MemoryDirectory(
            {
                "report.json": json.dumps(raw),
                **{
                    stage + suffix: "controlled native output"
                    for stage in stages
                    for suffix in (".stdout", ".stderr")
                },
            }
        )
        return await self.adapter.weaver_result(files, source, kind, **kwargs)


def _plan_git_identity(snapshot):
    return {
        "snapshot": snapshot,
        **(
            {
                "execution_tree": "a" * 40,
                "head_commit": "b" * 40,
                "index_tree": "a" * 40,
            }
            if snapshot != "worktree"
            else {}
        ),
    }


def _assert_native_source_identity(report, git):
    for check in report["checks"]:
        for result in check.get("native_results") or ():
            check_contract.validate_result(result)
            source = result["source_ref"]
            assert source["snapshot"] == git["snapshot"]
            assert source.get("declared_target_tree") == git.get("execution_tree")
            assert source.get("head_commit") == git.get("head_commit")
            assert source.get("index_tree") == git.get("index_tree")
            assert source["actual_execution_source"] == "dagger_directory"
            assert source["binding_status"] == (
                "identity_only" if git.get("execution_tree") else "unknown"
            )
            assert source["scope_kind"] == "changed_paths"
            assert source["verified_input_scope"] == []
            assert "content_digest" not in source
            assert result["result_origin"] == "dagger"
    assert report["source_binding_verified"] == "unknown"
    assert report["trusted"] is False


@pytest.mark.parametrize("snapshot", ["worktree", "index", "commit"])
@pytest.mark.parametrize("framework", ["unittest", "pytest"])
def test_policy_only_native_results_keep_plan_git_identity(
    modules, snapshot, framework
):
    _, collector = modules
    plan = _v2_dagger_plan(edges=[], prerequisites=[], actions={})
    plan["git"] = _plan_git_identity(snapshot)
    plan["selection"]["framework"] = framework
    native = _ContractNativeDouble(collector)
    saved = asyncio.run(
        collector.execute(
            native,
            MemoryFile(json.dumps(plan)),
            MemoryDirectory(),
            "3.14",
            "ci-weaver.toml",
        )
    )
    report = json.loads(asyncio.run(saved.report()))
    assert report["status"] == "passed", report
    _assert_native_source_identity(report, plan["git"])
    assert native.test_invocations == native.weaver_invocations == 1
    asyncio.run(saved.enforce())
    assert native.test_invocations == native.weaver_invocations == 1


@pytest.mark.parametrize(
    "snapshot,bad_consumer",
    [
        ("worktree", None),
        ("index", None),
        ("commit", None),
        ("index", "tests"),
        ("index", "weaver"),
    ],
)
@pytest.mark.parametrize("framework", ["unittest", "pytest"])
def test_dag_generated_refs_are_bound_before_native_result_identity(
    modules, snapshot, bad_consumer, framework
):
    _, collector = modules
    data = b"controlled generated input"
    paths = {
        "tests": "generated/test-input.txt",
        "weaver": "generated/registry-input.txt",
    }
    writer = (
        "import os; from pathlib import Path; r=Path(os.environ['ENGINEERING_CI_ARTIFACT_DIR']); "
        "(r/'generated').mkdir(); "
        + "; ".join(f"(r/{path!r}).write_bytes({data!r})" for path in paths.values())
    )
    actions = {"generate": {"type": "command", "argv": [sys.executable, "-c", writer]}}
    edges = [
        {
            "producer_ref": {"kind": "prerequisite_action", "name": "generate"},
            "consumer_ref": {"kind": "tests"}
            if consumer == "tests"
            else {"kind": "check", "name": "weaver_registry"},
            "dependency_kind": "data",
            "requires_pass": True,
            "output_ref": {
                "kind": "file",
                "path": path,
                "sha256": "f" * 64
                if consumer == bad_consumer
                else hashlib.sha256(data).hexdigest(),
            },
            "source_binding_ref": "git",
            "declaration_ref": _v2_decl("generate"),
            "execution_phase": "pre_execution",
            "resolution": "resolved",
            "unresolved_reason": None,
        }
        for consumer, path in paths.items()
    ]
    plan = _v2_dagger_plan(edges=edges, prerequisites=["generate"], actions=actions)
    plan["git"] = _plan_git_identity(snapshot)
    plan["selection"]["framework"] = framework
    native = _ContractNativeDouble(collector)
    saved = asyncio.run(
        collector.execute(
            native,
            MemoryFile(json.dumps(plan)),
            MemoryDirectory(),
            "3.14",
            "ci-weaver.toml",
        )
    )
    report = json.loads(asyncio.run(saved.report()))
    _assert_native_source_identity(report, plan["git"])
    consumers = {"tests": report["tests"], "weaver": report["checks"][0]}
    for consumer, item in consumers.items():
        if consumer == bad_consumer:
            assert item["status"] == "not_executed"
            assert item["reason"] == "data_artifact_digest_mismatch"
            assert not item.get("native_results")
            continue
        expected = {
            "kind": "generated_input",
            "input_kind": "file",
            "producer_ref": {"kind": "prerequisite_action", "name": "generate"},
            "path": paths[consumer],
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        assert native.consumer_sources[consumer].files[paths[consumer]] == data
        assert item["generated_input_refs"] == [expected]
        for result in item["native_results"]:
            assert [
                ref for ref in result["input_refs"] if ref["kind"] == "generated_input"
            ] == [expected]
            check_contract.validate_result(result)
    assert native.test_invocations == int(bad_consumer != "tests")
    assert native.weaver_invocations == int(bad_consumer != "weaver")
    if bad_consumer is None:
        assert report["status"] == "passed", report
        asyncio.run(saved.enforce())
    else:
        assert report["status"] == "blocked", report
        with pytest.raises(RuntimeError):
            asyncio.run(saved.enforce())


@pytest.mark.parametrize("snapshot", ["worktree", "commit"])
def test_dagger_plan_execution_producer_artifact_handoff_reaches_resolution(
    modules, tmp_path, snapshot
):
    from test_execution import run
    from test_reconciliation import make_observation, make_routing

    from engineering_ci import execution, hygiene, reconciliation, selection

    _result, collector = modules
    gen_file = "scripts/generated.sh"
    gen_file_bytes = b"#!/bin/sh\necho dagger-file\n"
    gen_file_sha = hashlib.sha256(gen_file_bytes).hexdigest()

    gen_dir = "scripts/generated_dir"
    dir_stage = tmp_path / "dir_stage"
    (dir_stage / "sub").mkdir(parents=True)
    (dir_stage / "empty_subdir").mkdir()
    (dir_stage / "sub/helper.sh").write_bytes(b"#!/bin/sh\necho dagger-dir\n")
    gen_dir_sha = execution.artifact_directory_id(dir_stage)

    emit_script = tmp_path / "emit.py"
    emit_script.write_text(
        f"""\
import os
from pathlib import Path

root = Path(os.environ["ENGINEERING_CI_ARTIFACT_DIR"])
(root / {gen_file!r}).parent.mkdir(parents=True, exist_ok=True)
(root / {gen_file!r}).write_bytes({gen_file_bytes!r})
(root / {gen_dir!r} / "sub").mkdir(parents=True, exist_ok=True)
(root / {gen_dir!r} / "empty_subdir").mkdir(parents=True, exist_ok=True)
(root / {gen_dir!r} / "sub/helper.sh").write_bytes(b"#!/bin/sh\\necho dagger-dir\\n")
""",
        encoding="utf-8",
    )

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "scripts").mkdir()
    (repo / "tach.toml").write_text(
        'source_roots = ["src"]\n[[modules]]\npath = "src"\ndepends_on = []\n',
        encoding="utf-8",
        newline="\n",
    )
    (repo / "ci-selection.toml").write_text(
        f"""\
schema_version = 1
checks = ["shellcheck"]

[[non_code_rules]]
prefix = "scripts/"
tests = []
reason = "shell scripts"

[prerequisite_actions.generate]
type = "command"
argv = {json.dumps([sys.executable, str(emit_script)])}

[[dependency_edges]]
producer_ref = {{ kind = "prerequisite_action", name = "generate" }}
consumer_ref = {{ kind = "check", name = "shellcheck" }}
dependency_kind = "data"
requires_pass = true
output_ref = {{ kind = "file", path = "{gen_file}", sha256 = "{gen_file_sha}" }}

[[dependency_edges]]
producer_ref = {{ kind = "prerequisite_action", name = "generate" }}
consumer_ref = {{ kind = "check", name = "shellcheck" }}
dependency_kind = "data"
requires_pass = true
output_ref = {{ kind = "directory", path = "{gen_dir}", sha256 = "{gen_dir_sha}" }}
""",
        encoding="utf-8",
        newline="\n",
    )
    (repo / "ci-hygiene.toml").write_text(
        'schema_version = 1\nchecks = ["forwarding"]\n',
        encoding="utf-8",
        newline="\n",
    )
    (repo / "scripts/check.sh").write_text(
        "#!/bin/sh\necho repo-script\n", encoding="utf-8", newline="\n"
    )
    (repo / "src/mod.py").write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
    run(repo, "init", "-q", "-b", "main")
    run(repo, "config", "user.email", "dagger86@example.invalid")
    run(repo, "config", "user.name", "Dagger 86 Test")
    run(repo, "config", "core.autocrlf", "false")
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", "init")
    head = run(repo, "rev-parse", "HEAD").strip()

    cfg = selection.load_config(repo / "ci-selection.toml")
    hyg = hygiene.load_config(repo / "ci-hygiene.toml")
    cs = selection.ChangeSet(
        root=repo,
        base_ref="origin/main",
        head_ref="HEAD",
        base_commit=head,
        head_commit=head,
        tree=run(repo, "rev-parse", "HEAD^{tree}").strip(),
        paths=("scripts/check.sh",),
        worktree_dirty=False,
        complete=True,
        snapshot=snapshot,
    )
    sel = selection.select(cs, cfg, root=repo)
    plan_doc = selection.plan(
        sel,
        config=cfg,
        config_path=repo / "ci-selection.toml",
        hygiene_config=hyg,
        hygiene_config_path=repo / "ci-hygiene.toml",
        root=repo,
    )

    source_tree = MemoryDirectory(
        {
            "ci-selection.toml": (repo / "ci-selection.toml").read_bytes(),
            "ci-hygiene.toml": (repo / "ci-hygiene.toml").read_bytes(),
            "tach.toml": (repo / "tach.toml").read_bytes(),
            "scripts/check.sh": (repo / "scripts/check.sh").read_bytes(),
            "src/mod.py": (repo / "src/mod.py").read_bytes(),
        }
    )
    saved = asyncio.run(
        collector.execute(
            NativeDouble(),
            MemoryFile(json.dumps(plan_doc)),
            source_tree,
            "3.14",
            "ci-weaver.toml",
            run_ref="dag-run",
            attempt_ref="3",
        )
    )
    report = json.loads(asyncio.run(saved.report()))
    assert report["status"] == "passed", json.dumps(
        [
            (c.get("name"), c.get("status"), c.get("reason"), c.get("stderr"))
            for c in report["checks"]
        ]
    )
    handoff = report["producer_artifact_handoff"]
    assert handoff["execution_context"] == "dagger_export"
    assert handoff["execution_identity"]["run_ref"] == "dag-run"
    assert handoff["execution_identity"]["attempt_ref"] == "3"
    assert len(handoff["artifacts"]) == 2

    exported = tmp_path / "exported_dagger_artifacts"
    asyncio.run(saved.artifacts.export(exported))
    assert (exported / "produced/prerequisite_action/generate" / gen_file).is_file()
    assert (
        exported / "produced/prerequisite_action/generate" / gen_dir / "empty_subdir"
    ).is_dir()

    authority = execution.producer_artifact_authority_from_execution(
        plan_doc,
        report,
        bundle_root=exported,
        allowed_checks={"shellcheck"},
        expected_run_ref="dag-run",
        expected_attempt_ref="3",
    )

    resolution = execution.build_source_resolution(
        plan_doc,
        "shellcheck",
        root=repo,
        producer_artifact_handoff=handoff,
        producer_artifacts_dir=exported,
        producer_artifact_authority=authority,
        expected_run_ref="dag-run",
        expected_attempt_ref="3",
    )
    assert resolution["producer_artifact_handoff_id"] == handoff["handoff_digest"]
    assert resolution["producer_execution_identity"]["run_ref"] == "dag-run"
    assert resolution["producer_execution_identity"]["attempt_ref"] == "3"
    assert {
        (ref["kind"], ref["input_kind"], ref["path"])
        for ref in resolution["input_refs"]
        if ref.get("kind") == "generated_input"
    } == {
        ("generated_input", "file", gen_file),
        ("generated_input", "directory", gen_dir),
    }
    native = next(
        row["native_result"] for row in report["checks"] if row["name"] == "shellcheck"
    )
    assert resolution["source_ref"] == native["source_ref"]
    expected = plan_doc["expected_checks"]
    exp_entry = next(
        item for item in expected["checks"] if item["name"] == "shellcheck"
    )
    rec = reconciliation.reconcile_expected_results(
        expected,
        routing=[
            make_routing(
                exp_entry,
                run_ref="dag-run",
                attempt_ref="3",
                runner_ref=native["runner_ref"],
            )
        ],
        resolutions=[resolution],
        observations=[
            make_observation(
                "job-1",
                [native],
                run_ref="dag-run",
                attempt_ref="3",
                runner_ref=native["runner_ref"],
            )
        ],
    )
    rec_entry = next(item for item in rec["entries"] if item["name"] == "shellcheck")
    if snapshot == "commit":
        assert resolution["source_ref"]["scope_kind"] == "check_input_closure"
        assert resolution["source_ref"]["binding_status"] == "content_digest"
        assert rec_entry["binding_status"] == "matched"
        assert rec_entry["native_status"] == "passed"
        assert rec_entry["native_satisfaction"]["status"] == "satisfied"
    else:
        assert resolution["source_ref"]["scope_kind"] == "changed_paths"
        assert rec_entry["binding_status"] == "unknown"
        assert rec_entry["reason"] == "expected_source_scope_insufficient"

    omitted_attempt = execution.evaluate_source_resolution(
        plan_doc,
        "shellcheck",
        root=repo,
        producer_artifact_handoff=handoff,
        producer_artifacts_dir=exported,
        producer_artifact_authority=authority,
    )
    assert omitted_attempt["resolution_status"] == "unverified"
    assert omitted_attempt["reason"] == "producer_handoff_attempt_unverified"

    (
        exported / "produced/prerequisite_action/generate" / gen_dir / "empty_subdir"
    ).rmdir()
    truncated = execution.evaluate_source_resolution(
        plan_doc,
        "shellcheck",
        root=repo,
        producer_artifact_handoff=handoff,
        producer_artifacts_dir=exported,
        producer_artifact_authority=authority,
        expected_run_ref="dag-run",
        expected_attempt_ref="3",
    )
    assert truncated["resolution_status"] == "contradicted"
    assert truncated["reason"] == "producer_directory_truncated"
    rec_trunc = reconciliation.reconcile_expected_results(
        expected,
        routing=[
            make_routing(
                exp_entry,
                run_ref="dag-run",
                attempt_ref="3",
                runner_ref=native["runner_ref"],
            )
        ],
        resolutions=[truncated],
        observations=[
            make_observation(
                "job-1",
                [native],
                run_ref="dag-run",
                attempt_ref="3",
                runner_ref=native["runner_ref"],
            )
        ],
    )
    trunc_entry = next(
        item for item in rec_trunc["entries"] if item["name"] == "shellcheck"
    )
    assert trunc_entry["binding_status"] == "insufficient"
    assert trunc_entry["reason"] == "producer_directory_truncated"
    assert trunc_entry["native_status"] == "passed"


def test_dagger_index_separates_identity_only_from_source_bound_host_checks(
    modules, tmp_path
):
    from engineering_ci import plan_contract
    from engineering_ci import selection as real_selection

    _, collector = modules
    sources_text = (
        'schema_version = 1\nmanifest = "data/catalog/manifest.json"\n'
        'acquired_roots = ["data/acquired"]\n'
        "exempt_paths = []\nenvironment_prefixes = []\n"
        "require_text_unset = false\n"
    )
    acquired_bytes = b"v1\n"
    manifest_text = (
        json.dumps(
            [
                {
                    "path": "data/acquired/a.bin",
                    "sha256": hashlib.sha256(acquired_bytes).hexdigest(),
                    "size_bytes": len(acquired_bytes),
                }
            ]
        )
        + "\n"
    )
    src = tmp_path / "src"
    raw_files = {
        "ci-sources.toml": sources_text.encode("utf-8"),
        "data/acquired/a.bin": acquired_bytes,
        "data/catalog/manifest.json": manifest_text.encode("utf-8"),
    }
    for rel, raw in raw_files.items():
        target = src / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)

    verified_source_tree_id = real_selection.directory_content_id(src)
    tree_hex = "a" * 40
    base_plan = _v2_dagger_plan(
        edges=[],
        prerequisites=[],
        actions={},
        tests=("tests.controlled",),
        checks=("source",),
    )
    base_plan["git"] = {
        "base_ref": "HEAD",
        "head_ref": "HEAD",
        "base_commit": "1" * 40,
        "head_commit": "1" * 40,
        "snapshot": "index",
        "execution_tree": tree_hex,
        "index_tree": tree_hex,
        "changed_paths": ["data/acquired/a.bin"],
        "source_tree_id": None,
    }
    base_plan["check_scope"] = [
        {
            "name": "source",
            "status": "selected",
            "inputs": ["data/acquired/a.bin"],
            "reason": "source inputs changed",
        }
    ]
    base_plan["selection"]["check_scope"] = list(base_plan["check_scope"])

    dag_plan = json.loads(json.dumps(base_plan))
    dag_plan["selection"]["prerequisites"] = ["prep"]
    dag_plan["prerequisite_actions"] = {
        "prep": {
            "type": "command",
            "argv": [sys.executable, "-c", "pass"],
        }
    }
    dag_plan["dependency_contract"] = plan_contract.graph_contract(
        [
            {
                "producer_ref": {"kind": "prerequisite_action", "name": "prep"},
                "consumer_ref": {"kind": "check", "name": "source"},
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
    )

    class SimpleNative(NativeDouble):
        async def exit_code(self):
            self.calls.append("tests")
            return 0

    for variant in (base_plan, dag_plan):
        native = SimpleNative()
        saved = asyncio.run(
            collector.execute(
                native,
                MemoryFile(json.dumps(variant)),
                MemoryDirectory(raw_files),
                "3.14",
                "ci-weaver.toml",
            )
        )
        report = json.loads(asyncio.run(saved.report()))
        assert report["status"] == "blocked", (report.get("reason"), report)
        assert report["exit_code"] == 5, report
        assert report["reason"] == "dagger_export_index_source_unverified", report
        assert report["source_tree_verified"] == "not_requested"
        src_check = next(c for c in report["checks"] if c["name"] == "source")
        assert src_check["status"] == "not_executed"
        assert src_check["reason"] == "dagger_export_index_source_unverified"

        verified = json.loads(json.dumps(variant))
        verified["git"]["source_tree_id"] = verified_source_tree_id
        native_ok = SimpleNative()
        saved_ok = asyncio.run(
            collector.execute(
                native_ok,
                MemoryFile(json.dumps(verified)),
                MemoryDirectory(raw_files),
                "3.14",
                "ci-weaver.toml",
            )
        )
        ok_report = json.loads(asyncio.run(saved_ok.report()))
        assert ok_report["status"] == "passed", (
            ok_report.get("reason"),
            ok_report.get("checks"),
        )
        assert ok_report["exit_code"] == 0, ok_report
        assert ok_report["source_tree_verified"] == "verified"
        src_ok = next(c for c in ok_report["checks"] if c["name"] == "source")
        assert src_ok["status"] == "passed"


def test_dagger_index_identity_conflicts_still_refuse_early(modules, tmp_path):
    from engineering_ci import plan_contract
    from engineering_ci import selection as real_selection

    _, collector = modules
    base = {
        "kind": plan_contract.PLAN_KIND,
        "schema_version": 2,
        "generator": {"config_sha256": "a" * 64},
        "git": {
            "snapshot": "index",
            "execution_tree": "a" * 40,
            "index_tree": "b" * 40,
            "source_tree_id": None,
        },
        "selection": {"tests": ["tests.controlled"]},
        "dependency_contract": plan_contract.graph_contract([]),
    }
    native = NativeDouble()
    saved = asyncio.run(
        collector.execute(
            native,
            MemoryFile(json.dumps(base)),
            MemoryDirectory({"ci-selection.toml": "# clean\n"}),
            "3.14",
            "ci-weaver.toml",
        )
    )
    report = json.loads(asyncio.run(saved.report()))
    assert report["status"] == "not_executed"
    assert report["reason"] == "index_source_identity_mismatch"
    assert report["exit_code"] == 2
    assert native.calls == []

    missing_exec = json.loads(json.dumps(base))
    missing_exec["git"] = {
        "snapshot": "index",
        "execution_tree": None,
        "index_tree": None,
        "source_tree_id": "c" * 64,
    }
    saved_missing = asyncio.run(
        collector.execute(
            native,
            MemoryFile(json.dumps(missing_exec)),
            MemoryDirectory({"ci-selection.toml": "# clean\n"}),
            "3.14",
            "ci-weaver.toml",
        )
    )
    rep_missing = json.loads(asyncio.run(saved_missing.report()))
    assert rep_missing["status"] == "not_executed"
    assert rep_missing["reason"] == "dagger_export_index_source_unverified"
    assert rep_missing["exit_code"] == 4

    src = tmp_path / "src"
    src.mkdir()
    (src / "ci-selection.toml").write_bytes(b"# clean\n")
    wrong_id = json.loads(json.dumps(base))
    wrong_id["git"] = {
        "snapshot": "index",
        "execution_tree": "a" * 40,
        "index_tree": "a" * 40,
        "source_tree_id": "sha256:" + "0" * 64,
    }
    assert wrong_id["git"]["source_tree_id"] != real_selection.directory_content_id(src)
    saved_wrong = asyncio.run(
        collector.execute(
            native,
            MemoryFile(json.dumps(wrong_id)),
            MemoryDirectory({"ci-selection.toml": "# clean\n"}),
            "3.14",
            "ci-weaver.toml",
        )
    )
    rep_wrong = json.loads(asyncio.run(saved_wrong.report()))
    assert rep_wrong["status"] == "not_executed"
    assert rep_wrong["reason"] == "source_tree_mismatch"
    assert rep_wrong["exit_code"] == 4


def test_v2_build_data_artifact_reaches_consumer_and_records_filtered_view(modules):
    _, collector = modules
    manifest_bytes = b'{"built":true}\n'
    writer = (
        "import os; from pathlib import Path; "
        "target = Path(os.environ['ENGINEERING_CI_ARTIFACT_DIR']) / 'build' / 'generated' / 'manifest.json'; "
        "target.parent.mkdir(parents=True, exist_ok=True); "
        "target.write_bytes(b'{\"built\":true}\\n')"
    )
    action = {"type": "command", "argv": [sys.executable, "-c", writer]}
    edge = {
        "producer_ref": {"kind": "prerequisite_action", "name": "gen"},
        "consumer_ref": {"kind": "tests"},
        "dependency_kind": "data",
        "requires_pass": True,
        "output_ref": {"kind": "file", "path": "build/generated/manifest.json"},
        "source_binding_ref": "git",
        "declaration_ref": _v2_decl("gen"),
        "execution_phase": "pre_execution",
        "resolution": "resolved",
        "unresolved_reason": None,
    }
    plan = _v2_dagger_plan(
        edges=[edge],
        prerequisites=["gen"],
        actions={"gen": action},
        checks=["weaver_registry"],
    )

    class Native(NativeDouble):
        def __init__(self):
            super().__init__()
            self.test_source = None

        def _bound_public_source(self, source):
            files, dirs = source._materialize()
            return MemoryDirectory(
                {k: v for k, v in files.items() if not k.startswith(".git/")},
                dirs={d for d in dirs if d != ".git" and not d.startswith(".git/")},
            )

        def _uv_container(self, source, version):
            self.test_source = source
            return self

        async def stdout(self):
            return json.dumps(
                {
                    "executable": sys.executable,
                    "python": "3.14.0",
                    "platform": "linux",
                    "tool_version": "3.14.0",
                }
            )

    native = Native()
    saved = asyncio.run(
        collector.execute(
            native,
            MemoryFile(json.dumps(plan)),
            MemoryDirectory(
                {
                    ".git/HEAD": "ref: refs/heads/main\n",
                    "ci-selection.toml": "# clean\n",
                }
            ),
            "3.14",
            "ci-weaver.toml",
        )
    )
    report = json.loads(asyncio.run(saved.report()))
    assert report["status"] == "passed", report
    assert native.test_source.files["build/generated/manifest.json"] == manifest_bytes
    obs = report["tests"]["native_result"]["evidence"]["observation"]
    assert obs["container_source_filtered"] is True
    assert (
        obs["source_directory"]["digest"] != obs["consumed_source_directory"]["digest"]
    )
    assert report["tests"]["native_result"]["evidence"]["source_limit"] == (
        "input_closure_not_proven"
    )


def _filter_source_with_patterns(source_dir, patterns):
    """Apply Dagger-style exclude patterns to a MemoryDirectory."""
    import fnmatch

    files, dirs = source_dir._materialize()

    def excluded(rel):
        parts = rel.split("/")
        for pat in patterns:
            if pat.startswith("**/"):
                sub = pat[3:]
                if any(fnmatch.fnmatchcase(part, sub) for part in parts):
                    return True
                if fnmatch.fnmatchcase(rel, pat) or fnmatch.fnmatchcase(parts[-1], sub):
                    return True
            elif fnmatch.fnmatchcase(parts[0], pat) or fnmatch.fnmatchcase(rel, pat):
                return True
        return False

    return MemoryDirectory(
        {k: v for k, v in files.items() if not excluded(k)},
        dirs={d for d in dirs if not excluded(d)},
    )


def _write_memdir(source_dir, root):
    """Write a MemoryDirectory synchronously onto a temp Path for container probes."""
    files, dirs = source_dir._materialize()
    for rel in sorted(dirs):
        (root / rel).mkdir(parents=True, exist_ok=True)
    for rel, raw in sorted(files.items()):
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)


def _make_runtime_unittest_container_class(workspace_root, *, mutate_during_test=None):
    """Run real `python -m unittest` pre-mounted on `workspace_root` without post-exec hashing."""
    import subprocess

    class RuntimeContainer:
        def __init__(
            self,
            *,
            command=None,
            exit_code_val=0,
            stdout_text="",
            stderr_text="",
        ):
            self.command = command
            self.exit_code_val = exit_code_val
            self.stdout_text = stdout_text
            self.stderr_text = stderr_text

        def with_exec(self, command, **kwargs):
            cmd = list(command)
            if "unittest" in cmd:
                target_args = cmd[cmd.index("unittest") + 1 :]
                proc = subprocess.run(
                    [sys.executable, "-m", "unittest", *target_args],
                    cwd=workspace_root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if mutate_during_test is not None:
                    mutate_during_test(workspace_root)
                return RuntimeContainer(
                    command=cmd,
                    exit_code_val=proc.returncode,
                    stdout_text=proc.stdout,
                    stderr_text=proc.stderr,
                )
            if len(cmd) >= 2 and cmd[-2] == "-c" and "importlib.metadata" in cmd[-1]:
                return RuntimeContainer(
                    command=cmd,
                    exit_code_val=0,
                    stdout_text=json.dumps(
                        {
                            "executable": sys.executable,
                            "python": "3.14.0",
                            "platform": "linux",
                            "tool_version": "3.14.0",
                        }
                    ),
                )
            return RuntimeContainer(command=cmd)

        async def exit_code(self):
            return self.exit_code_val

        async def stdout(self):
            return self.stdout_text

        async def stderr(self):
            return self.stderr_text

    return RuntimeContainer


def test_pre_execution_closure_proof_preserves_verified_and_downgrades_deviations(
    modules, tmp_path
):
    import importlib
    import shutil

    from engineering_ci import selection as real_selection

    _, collector = modules
    tooling = importlib.import_module(collector.__package__ + ".tooling")

    raw_files = {
        ".git/HEAD": b"ref: refs/heads/main\n",
        "ci-selection.toml": (
            b'schema_version = 1\ntests_dir = "tests"\nsrc_root = "src"\nframework = "unittest"\n'
        ),
        "ci-weaver.toml": b"# weaver\n",
        ".env.test": b"APP_ENV=testing\n",
        "config/test.local.toml": b"[service]\nport = 8080\n",
        ".docker/deploy.sh": b"#!/bin/sh\necho ok\n",
        "tests/test_controlled.py": (
            b"import unittest\n"
            b"from pathlib import Path\n\n"
            b"class ControlledTest(unittest.TestCase):\n"
            b"    def test_ok(self):\n"
            b"        self.assertEqual(Path('.env.test').read_bytes(), b'APP_ENV=testing\\n')\n"
        ),
    }
    src = tmp_path / "src"
    for rel, raw in raw_files.items():
        target = src / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)

    tree_hex = "a" * 40
    plan = _v2_dagger_plan(
        edges=[],
        prerequisites=[],
        actions={},
        tests=("tests.test_controlled",),
        checks=("weaver_registry",),
    )
    plan["git"] = {
        "base_ref": "HEAD",
        "head_ref": "HEAD",
        "base_commit": "1" * 40,
        "head_commit": "1" * 40,
        "snapshot": "index",
        "execution_tree": tree_hex,
        "index_tree": tree_hex,
        "changed_paths": [".env.test", "config/test.local.toml", ".docker/deploy.sh"],
        "source_tree_id": real_selection.directory_content_id(src),
    }

    def run_case(
        case_dir,
        *,
        bind_src=src,
        mutate_during_test=None,
        bound_transform=None,
    ):
        ws_root = case_dir / "container_ws"
        ws_root.mkdir(parents=True, exist_ok=True)
        bound_kwarg_seen = []

        class Native:
            def _bound_public_source(self, source):
                filtered = _filter_source_with_patterns(source, tooling.SOURCE_NOISE)
                if bound_transform is not None:
                    return bound_transform(filtered)
                return filtered

            def _uv_container(self, source, version, _bound_source=None):
                bound_kwarg_seen.append(_bound_source is not None)
                mount = (
                    _bound_source
                    if _bound_source is not None
                    else self._bound_public_source(source)
                )
                _write_memdir(mount, ws_root)
                return _make_runtime_unittest_container_class(
                    ws_root, mutate_during_test=mutate_during_test
                )()

            async def weaver_artifacts(self, source, kind, config):
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

        case_files = {
            rel: path.read_bytes()
            for path in bind_src.rglob("*")
            if path.is_file()
            for rel in [path.relative_to(bind_src).as_posix()]
        }
        plan_case = json.loads(json.dumps(plan))
        plan_case["git"]["source_tree_id"] = real_selection.directory_content_id(
            bind_src
        )
        saved = asyncio.run(
            collector.execute(
                Native(),
                MemoryFile(json.dumps(plan_case)),
                MemoryDirectory(case_files),
                "3.14",
                "ci-weaver.toml",
            )
        )
        return json.loads(asyncio.run(saved.report())), bound_kwarg_seen

    # 1. Pre-exec proof succeeds on immutable consumed_source despite .git filtering;
    # runtime test writes/edits in workspace AFTER start do not falsify pre-exec binding.
    def _runtime_side_effects(ws):
        (ws / "runtime_created.log").write_text("runtime output\n", encoding="utf-8")
        (ws / ".env.test").write_bytes(b"APP_ENV=mutated_by_test_after_start\n")

    ok_report, ok_kwargs = run_case(
        tmp_path / "ok", mutate_during_test=_runtime_side_effects
    )
    assert ok_kwargs == [True]
    assert ok_report["status"] == "passed", ok_report
    ok_native = ok_report["tests"]["native_result"]
    ok_obs = ok_native["evidence"]["observation"]
    assert ok_obs["container_source_filtered"] is True
    assert ok_obs["equivalence_verified_at"] == "pre_execution"
    assert ok_obs["source_equivalence_status"] == "verified_equivalent"
    assert ok_native["source_ref"]["binding_status"] == "byte_tree"
    assert ok_native["source_ref"]["scope_kind"] == "full_input_closure"
    assert "source_limit" not in ok_native["evidence"]

    # 2. Unfiltered identical immutable Directory digest -> skip redundant re-hash.
    same_src = tmp_path / "same_src"
    shutil.copytree(src, same_src, ignore=shutil.ignore_patterns(".git"))
    same_report, _ = run_case(tmp_path / "identical", bind_src=same_src)
    same_native = same_report["tests"]["native_result"]
    assert same_native["evidence"]["observation"]["container_source_filtered"] is False
    assert (
        same_native["evidence"]["observation"]["source_equivalence_status"]
        == "unfiltered_identical"
    )
    assert same_native["source_ref"]["binding_status"] == "byte_tree"
    assert "source_limit" not in same_native["evidence"]

    # 3. Missing bound input (.ruff_cache/bound.txt) stripped by SOURCE_NOISE pre-execution ->
    # retain native exit=0 and downgrade binding with source_limit.
    miss_src = tmp_path / "miss_src"
    shutil.copytree(src, miss_src)
    (miss_src / ".ruff_cache").mkdir()
    (miss_src / ".ruff_cache/bound.txt").write_bytes(b"bound-cache\n")
    miss_report, _ = run_case(tmp_path / "missing", bind_src=miss_src)
    assert miss_report["tests"]["exit_code"] == 0
    miss_native = miss_report["tests"]["native_result"]
    assert (
        miss_native["evidence"]["observation"]["source_equivalence_status"]
        == "missing_bound_inputs"
    )
    assert miss_native["evidence"]["observation"]["missing_bound_paths"] == [
        ".ruff_cache/bound.txt"
    ]
    assert miss_native["evidence"]["source_limit"] == "input_closure_not_proven"
    assert miss_native["source_ref"]["binding_status"] == "identity_only"
    assert miss_native["source_ref"]["scope_kind"] == "changed_paths"

    # 4. Unexpected extra file pre-execution in consumed_source ->
    # caught by full-tree closure check (not expected-paths-only), downgraded cleanly.
    extra_report, _ = run_case(
        tmp_path / "extra",
        bound_transform=lambda d: d.with_new_file(
            "unexpected_extra.txt", b"extra pre-exec\n"
        ),
    )
    assert extra_report["tests"]["exit_code"] == 0
    extra_native = extra_report["tests"]["native_result"]
    assert (
        extra_native["evidence"]["observation"]["source_equivalence_status"]
        == "unexpected_bound_inputs"
    )
    assert extra_native["evidence"]["observation"]["unexpected_bound_paths"] == [
        "unexpected_extra.txt"
    ]
    assert extra_native["evidence"]["source_limit"] == "input_closure_not_proven"
    assert extra_native["source_ref"]["binding_status"] == "identity_only"


def test_v2_data_overlay_applies_after_source_noise_filtering_and_preserves_env_and_local_toml(
    modules, tmp_path
):
    import importlib

    _, collector = modules
    tooling = importlib.import_module(collector.__package__ + ".tooling")
    env_bytes = b"APP_ENV=overlay\n"
    local_bytes = b"[overlay]\nport = 9090\n"
    cache_overlay_bytes = b"overlay-must-survive\n"

    writer = (
        "import os; from pathlib import Path; "
        "root = Path(os.environ['ENGINEERING_CI_ARTIFACT_DIR']); "
        "(root / '.env.test').write_bytes(b'APP_ENV=overlay\\n'); "
        "(root / 'config').mkdir(parents=True, exist_ok=True); "
        "(root / 'config' / 'generated.local.toml').write_bytes(b'[overlay]\\nport = 9090\\n'); "
        "(root / '.ruff_cache').mkdir(parents=True, exist_ok=True); "
        "(root / '.ruff_cache' / 'generated-data.txt').write_bytes(b'overlay-must-survive\\n')"
    )
    action = {"type": "command", "argv": [sys.executable, "-c", writer]}
    edges = [
        {
            "producer_ref": {"kind": "prerequisite_action", "name": "gen"},
            "consumer_ref": {"kind": "tests"},
            "dependency_kind": "data",
            "requires_pass": True,
            "output_ref": {"kind": "file", "path": rel},
            "source_binding_ref": "git",
            "declaration_ref": _v2_decl("gen"),
            "execution_phase": "pre_execution",
            "resolution": "resolved",
            "unresolved_reason": None,
        }
        for rel in (
            ".env.test",
            "config/generated.local.toml",
            ".ruff_cache/generated-data.txt",
        )
    ]
    plan = _v2_dagger_plan(
        edges=edges,
        prerequisites=["gen"],
        actions={"gen": action},
        tests=("tests.test_overlay_unit",),
        checks=("weaver_registry",),
    )

    ws_root = tmp_path / "unittest_ws"
    ws_root.mkdir(parents=True, exist_ok=True)

    class Native:
        def __init__(self):
            self.test_source = None
            self.bound_kwarg_passed = False

        def _bound_public_source(self, source):
            return _filter_source_with_patterns(source, tooling.SOURCE_NOISE)

        # Mirrors real EngineeringCi._uv_container: omits _bound_source -> second filter runs!
        def _uv_container(self, source, version, _bound_source=None):
            self.bound_kwarg_passed = _bound_source is not None
            mounted = (
                _bound_source
                if _bound_source is not None
                else self._bound_public_source(source)
            )
            self.test_source = mounted
            _write_memdir(mounted, ws_root)
            return _make_runtime_unittest_container_class(ws_root)()

        async def weaver_artifacts(self, source, kind, config):
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

    native = Native()
    saved = asyncio.run(
        collector.execute(
            native,
            MemoryFile(json.dumps(plan)),
            MemoryDirectory(
                {
                    ".git/HEAD": b"ref: refs/heads/main\n",
                    "ci-selection.toml": b"# clean\n",
                    "tests/test_overlay_unit.py": (
                        b"import unittest\n"
                        b"from pathlib import Path\n\n"
                        b"class OverlayTest(unittest.TestCase):\n"
                        b"    def test_reads_all_overlays(self):\n"
                        b"        self.assertEqual(Path('.env.test').read_bytes(), b'APP_ENV=overlay\\n')\n"
                        b"        self.assertEqual(Path('config/generated.local.toml').read_bytes(), b'[overlay]\\nport = 9090\\n')\n"
                        b"        self.assertEqual(Path('.ruff_cache/generated-data.txt').read_bytes(), b'overlay-must-survive\\n')\n"
                    ),
                }
            ),
            "3.14",
            "ci-weaver.toml",
        )
    )
    report = json.loads(asyncio.run(saved.report()))
    assert report["status"] == "passed", report
    assert report["tests"]["exit_code"] == 0
    assert native.bound_kwarg_passed is True
    assert ".git/HEAD" not in native.test_source.files
    assert native.test_source.files[".env.test"] == env_bytes
    assert native.test_source.files["config/generated.local.toml"] == local_bytes
    assert (
        native.test_source.files[".ruff_cache/generated-data.txt"]
        == cache_overlay_bytes
    )
    assert report["tests"]["native_result"]["evidence"]["source_limit"] == (
        "input_closure_not_proven"
    )
    assert len(report["tests"]["generated_input_refs"]) == 3


def test_repository_script_scan_keeps_docker_targets_and_refuses_filtered_targets(
    monkeypatch, modules, tmp_path
):
    import importlib

    from engineering_ci import workflow_analysis

    _, collector = modules
    tooling = importlib.import_module(collector.__package__ + ".tooling")
    dagger_mod = sys.modules["dagger"]

    class FakeDagDir(MemoryDirectory):
        def with_directory(self, prefix, directory, *, exclude=()):
            filtered = _filter_source_with_patterns(directory, exclude)
            merged = super().with_directory(prefix, filtered)
            return FakeDagDir(merged.files, dirs=merged.dirs)

    def identity(*args, **kwargs):
        if args and callable(args[0]) and not kwargs:
            return args[0]
        return lambda fn: fn

    monkeypatch.setattr(dagger_mod, "function", identity, raising=False)
    monkeypatch.setattr(dagger_mod, "check", identity, raising=False)
    monkeypatch.setattr(dagger_mod, "object_type", identity, raising=False)
    monkeypatch.setattr(dagger_mod, "DefaultPath", lambda *a, **kw: None, raising=False)
    monkeypatch.setattr(dagger_mod, "Ignore", lambda *a, **kw: None, raising=False)
    monkeypatch.setattr(
        dagger_mod,
        "dag",
        types.SimpleNamespace(directory=lambda: FakeDagDir()),
        raising=False,
    )
    for attr in ("Directory", "Container", "Secret"):
        if not hasattr(dagger_mod, attr):
            monkeypatch.setattr(dagger_mod, attr, object, raising=False)
    spec = importlib.util.spec_from_file_location(
        collector.__package__ + ".main", MODULE / "main.py"
    )
    main_mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, main_mod)
    spec.loader.exec_module(main_mod)

    engine = main_mod.EngineeringCi()
    scan_calls = []

    class ScanContainer:
        def __init__(self, source_dir, *, stdout_text="[]", code=0):
            self.source_dir = source_dir
            self.stdout_text = stdout_text
            self.code = code

        def with_new_file(self, path, contents):
            return ScanContainer(
                self.source_dir, stdout_text=self.stdout_text, code=self.code
            )

        def with_exec(self, command, **kwargs):
            if (
                len(command) >= 3
                and command[0] == "python"
                and "repository_scan_targets" in command[2]
            ):
                ws = tmp_path / (
                    f"scan_{len(scan_calls)}_"
                    + hashlib.sha256(
                        json.dumps(
                            {
                                k: v.decode("latin1")
                                for k, v in self.source_dir.files.items()
                            },
                            sort_keys=True,
                        ).encode("utf-8")
                    ).hexdigest()[:12]
                )
                ws.mkdir(parents=True, exist_ok=True)
                _write_memdir(self.source_dir, ws)
                policy = json.loads(command[3])
                observed = workflow_analysis.repository_scan_targets(ws, policy)
                scan_calls.append(list(self.source_dir.files))
                return ScanContainer(
                    self.source_dir,
                    stdout_text=json.dumps(observed),
                    code=0,
                )
            return ScanContainer(
                self.source_dir, stdout_text='{"comments": []}', code=0
            )

        async def stdout(self):
            return self.stdout_text

        async def stderr(self):
            return ""

        async def exit_code(self):
            return self.code

    monkeypatch.setattr(
        engine,
        "_uv_tool_container",
        lambda source, _bound_source=None: ScanContainer(
            _bound_source if _bound_source is not None else source
        ),
    )

    # Case A: .git/HEAD + .docker/deploy.sh + scripts/ok.sh -> both scanned, NO duplicate raw scan!
    scan_calls.clear()
    ok_source = MemoryDirectory(
        {
            ".git/HEAD": b"ref: refs/heads/main\n",
            "scripts/ok.sh": b"#!/bin/sh\necho ok\n",
            ".docker/deploy.sh": b"#!/bin/sh\necho deploy\n",
        }
    )
    ok_report = asyncio.run(
        engine._run_shellcheck(
            ok_source, workflow_analysis.shellcheck_policy(scan_paths=["."])
        )
    )
    assert ok_report["outcome"] == "clean", ok_report
    assert {item["path"] for item in ok_report["target_sources"]} == {
        ".docker/deploy.sh",
        "scripts/ok.sh",
    }
    assert len(scan_calls) == 1

    # Case B: scripts/ok.sh + .ruff_cache/hidden.sh -> refuse, never pass on subset!
    hidden_source = MemoryDirectory(
        {
            "scripts/ok.sh": b"#!/bin/sh\necho ok\n",
            ".ruff_cache/hidden.sh": b"#!/bin/sh\necho $unquoted\n",
        }
    )
    refused = asyncio.run(
        engine._run_shellcheck(
            hidden_source, workflow_analysis.shellcheck_policy(scan_paths=["."])
        )
    )
    assert refused["outcome"] == "not_executed"
    assert refused["exit_code"] is None
    assert refused["reason"] == "source_filter_excluded_scan_targets"
    assert [t["path"] for t in refused["excluded_targets"]] == [".ruff_cache/hidden.sh"]

    # Case C: ONLY .ruff_cache/hidden.sh -> source_filter_excluded_scan_targets, not no_applicable_scripts.
    only_hidden = MemoryDirectory({".ruff_cache/hidden.sh": b"#!/bin/sh\necho hi\n"})
    refused_only = asyncio.run(
        engine._run_shellcheck(
            only_hidden, workflow_analysis.shellcheck_policy(scan_paths=["."])
        )
    )
    assert refused_only["outcome"] == "not_executed"
    assert refused_only["reason"] == "source_filter_excluded_scan_targets"

    # Case D: Comparator detects problems & config_problem using repository_scan_targets's exact keys!
    anomaly_only = workflow_analysis.repository_scan_filter_refusal(
        workflow_analysis.shellcheck_policy(scan_paths=["."]),
        {
            "targets": [{"path": "scripts/ok.sh"}],
            "problems": {},
            "config_problem": None,
        },
        {
            "targets": [{"path": "scripts/ok.sh"}],
            "problems": {".ruff_cache/escape.sh": "workflow_target_outside_root"},
            "config_problem": None,
        },
    )
    assert anomaly_only is not None
    assert anomaly_only["outcome"] == "not_executed"
    assert anomaly_only["reason"] == "source_filter_excluded_scan_targets"
    assert anomaly_only["target_problems"] == {
        ".ruff_cache/escape.sh": "workflow_target_outside_root"
    }
    assert anomaly_only["excluded_problems"] == {
        ".ruff_cache/escape.sh": "workflow_target_outside_root"
    }
    assert (
        tooling.SOURCE_NOISE
    )  # keeps import explicitly used and checked alongside main_mod
