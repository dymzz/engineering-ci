"""Portable core, actual graph parameters, generation integrity and crash recovery."""

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from filelock import FileLock

from engineering_ci.workflow_core import (
    Catalog,
    Runtime,
    ToolResult,
    ToolSpec,
    generate,
    load_generation,
)
from engineering_ci.workflow_core.definition import pointer
from engineering_ci.workflow_core.documents import WorkflowError, digest, read, write
from engineering_ci.workflow_core.schemas import SCHEMAS, object_schema

NUMBER = object_schema({"value": {"type": "integer"}})


class Arithmetic:
    def __init__(self, *, fail=False, interrupt=False, malformed=False):
        self.calls, self.fail, self.interrupt, self.malformed = (
            [],
            fail,
            interrupt,
            malformed,
        )

    def invoke(self, calls, context):
        assert context.execution_id == context.run_dir.name
        self.calls.extend(
            (call.step_id, call.inputs, context.execution_id) for call in calls
        )
        if self.interrupt:
            raise KeyboardInterrupt
        return {
            call.step_id: ToolResult(
                "failed" if self.fail else "passed",
                7 if self.fail else 0,
                outputs={}
                if self.malformed
                else {
                    "value": call.inputs["value"]
                    + (1 if call.tool_id == "math.increment" else 10)
                },
                executed=True,
                complete=True,
                current=True,
                report_generated=True,
            )
            for call in calls
        }


def tools(adapter=None, *, effect="check"):
    adapter = adapter or Arithmetic()
    result = Catalog()
    for name in ("math.increment", "math.add-ten"):
        result.register(
            ToolSpec(
                name,
                NUMBER,
                NUMBER,
                effect=effect,
                recovery="manual" if effect == "write" else "restartable",
            ),
            adapter,
        )
    return result, adapter


def recipe():
    return {
        "schema_version": 1,
        "kind": "workflow-recipe",
        "steps": [
            {"id": "first", "tool": "math.increment", "inputs": {"value": 2}},
            {
                "id": "second",
                "tool": "math.add-ten",
                "inputs": {"value": {"$from": "first", "pointer": "/value"}},
            },
        ],
    }


def execution(tmp_path, adapter=None, **kwargs):
    registered, adapter = tools(adapter, **kwargs)
    path = generate(recipe(), registered, tmp_path / "generated")
    runtime = Runtime(registered)
    return runtime, runtime.create(path, tmp_path / "runs"), adapter


def test_each_generation_is_independent_and_semantic_identity_is_stable(tmp_path):
    registered, _ = tools()
    first = generate(recipe(), registered, tmp_path)
    original = first.read_bytes()
    second = generate(recipe(), registered, tmp_path)
    a, _, plan = load_generation(first, registered)
    b, _, _ = load_generation(second, registered)
    assert (
        a["definition_id"] == b["definition_id"]
        and a["generation_id"] != b["generation_id"]
    )
    assert first != second and first.read_bytes() == original
    assert plan["dependencies"] == {"first": [], "second": ["first"]}
    assert set(SCHEMAS) >= {"recipe", "workflow", "provenance"}


def test_actual_tools_parameters_and_predecessor_outputs_drive_execution(tmp_path):
    runtime, directory, adapter = execution(tmp_path)
    result = runtime.resume(directory)
    assert result["status"] == "passed" and result["exit_code"] == 0
    assert [(name, inputs) for name, inputs, _ in adapter.calls] == [
        ("first", {"value": 2}),
        ("second", {"value": 3}),
    ]
    assert result["steps"]["second"]["result"]["outputs"] == {"value": 13}
    assert result["steps"]["second"]["input_sha256"] == digest({"value": 3})
    assert len({context for _, _, context in adapter.calls}) == 1
    altered = recipe()
    altered["steps"][0]["inputs"]["value"] = 20
    path = generate(altered, runtime.catalog, tmp_path / "generated")
    next_run = runtime.create(path, tmp_path / "runs")
    assert runtime.resume(next_run)["steps"]["second"]["result"]["outputs"] == {
        "value": 31
    }
    with pytest.raises(WorkflowError, match="same semantic"):
        runtime.create(path, tmp_path / "runs", reuse_from=directory)


@pytest.mark.parametrize(
    "change",
    [
        lambda x: x["steps"].append(copy.deepcopy(x["steps"][0])),
        lambda x: x["steps"][0].update(needs=["missing"]),
        lambda x: x["steps"][0].update(needs=["second"]),
        lambda x: x["steps"][0].update(tool="unknown.tool"),
        lambda x: x["steps"][0]["inputs"].update(value="SECRET"),
        lambda x: x["steps"][1]["inputs"]["value"].update(pointer="/missing"),
        lambda x: x["steps"][1].update(shell="echo SECRET"),
        lambda x: x.update(schema_version=True),
        lambda x: x.update(steps=[]),
        lambda x: [step.update(required=False) for step in x["steps"]],
    ],
)
def test_invalid_configuration_is_rejected_before_any_tool(tmp_path, change):
    registered, adapter = tools()
    proposed = recipe()
    change(proposed)
    with pytest.raises(WorkflowError) as error:
        generate(proposed, registered, tmp_path)
    assert not adapter.calls and "SECRET" not in str(error.value)
    assert list(tmp_path.iterdir()) == []


def test_static_type_mismatch_and_output_requiredness(tmp_path):
    registered, adapter = tools()
    registered.register(
        ToolSpec("text.consume", object_schema({"value": {"type": "string"}}), NUMBER),
        adapter,
    )
    proposed = recipe()
    proposed["steps"][1]["tool"] = "text.consume"
    with pytest.raises(WorkflowError, match="type differs"):
        generate(proposed, registered, tmp_path)


def test_provenance_and_contract_cannot_be_changed_under_old_identity(tmp_path):
    registered, _ = tools()
    path = generate(recipe(), registered, tmp_path)
    value = read(path)
    value["capabilities"].append("loop")
    write(path, value, replace=True)
    with pytest.raises(WorkflowError):
        load_generation(path, registered)
    path = generate(recipe(), registered, tmp_path)
    provenance = read(path.with_name("provenance.json"))
    provenance["llm_used"] = True
    write(path.with_name("provenance.json"), provenance, replace=True)
    with pytest.raises(WorkflowError):
        load_generation(path, registered)
    changed = Catalog()
    changed.register(
        ToolSpec(
            "math.increment", object_schema({"value": {"type": "number"}}), NUMBER
        ),
        Arithmetic(),
    )
    with pytest.raises(WorkflowError, match="contract changed"):
        load_generation(path, changed)


@pytest.mark.parametrize(
    "fail,malformed,status,code",
    [(True, False, "failed", 7), (False, True, "failed", 3), (True, True, "failed", 7)],
)
def test_report_success_and_output_validation_cannot_hide_tool_failure(
    tmp_path, fail, malformed, status, code
):
    runtime, directory, _ = execution(
        tmp_path, Arithmetic(fail=fail, malformed=malformed)
    )
    result = runtime.resume(directory)
    assert result["status"] == status and result["exit_code"] == code
    assert result["steps"]["second"]["status"] == "skipped"


def test_explicit_failure_diagnostic_runs_but_does_not_erase_failure(tmp_path):
    registered, adapter = tools(Arithmetic(fail=True))
    proposed = recipe()
    proposed["steps"][1].update(
        inputs={"value": 8}, needs=["first"], when="failure", required=False
    )
    path = generate(proposed, registered, tmp_path / "generated")
    runtime = Runtime(registered)
    result = runtime.resume(runtime.create(path, tmp_path / "runs"))
    assert [name for name, _, _ in adapter.calls] == ["first", "second"]
    assert result["exit_code"] == 7


def test_interruption_resume_and_read_only_status(tmp_path):
    runtime, directory, adapter = execution(tmp_path, Arithmetic(interrupt=True))
    result = runtime.resume(directory)
    assert result["phase"] == "PAUSED" and result["exit_code"] == 130
    calls = len(adapter.calls)
    assert runtime.status(directory) == result and len(adapter.calls) == calls
    adapter.interrupt = False
    result = Runtime(runtime.catalog).resume(directory)
    assert result["status"] == "passed" and result["steps"]["first"]["attempt"] == 2
    with (
        FileLock(directory / "execution.lock"),
        pytest.raises(WorkflowError, match="owned"),
    ):
        runtime.resume(directory)


def test_uncertain_write_is_never_invoked_again(tmp_path):
    class LostResponse(Arithmetic):
        def invoke(self, calls, context):
            self.calls.extend(calls)
            raise ConnectionError("Token SECRET and response")

    runtime, directory, adapter = execution(tmp_path, LostResponse(), effect="write")
    result = runtime.resume(directory)
    assert result["phase"] == "UNKNOWN" and len(adapter.calls) == 1
    assert "SECRET" not in json.dumps(result)
    assert Runtime(runtime.catalog).resume(directory)["phase"] == "UNKNOWN"
    assert len(adapter.calls) == 1


@pytest.mark.parametrize("effect", ["check", "write"])
def test_actual_process_death_retains_intent_and_obeys_recovery(tmp_path, effect):
    registered, adapter = tools(effect=effect)
    path = generate(recipe(), registered, tmp_path / "generated")
    directory = Runtime(registered).create(path, tmp_path / "runs")
    script = """
import os, sys
from engineering_ci.workflow_core import Catalog, Runtime, ToolSpec
from engineering_ci.workflow_core.schemas import object_schema
schema=object_schema({"value":{"type":"integer"}})
class Exit:
    def invoke(self, calls, context): os._exit(91)
c=Catalog()
for name in ("math.increment","math.add-ten"):
    c.register(ToolSpec(name,schema,schema,effect=sys.argv[2],recovery="manual" if sys.argv[2]=="write" else "restartable"),Exit())
Runtime(c).resume(sys.argv[1])
"""
    process = subprocess.run(
        [sys.executable, "-c", script, str(directory), effect],
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
        check=False,
    )
    assert process.returncode == 91
    assert read(directory / "state.json")["steps"]["first"]["status"] == "running"
    result = Runtime(registered).resume(directory)
    assert result["phase"] == ("UNKNOWN" if effect == "write" else "FINISHED")
    assert len(adapter.calls) == (0 if effect == "write" else 2)


def test_core_can_be_extracted_without_project_imports(tmp_path):
    from engineering_ci import workflow_core

    source = Path(workflow_core.__file__).parent
    shutil.copytree(
        source, tmp_path / "workflow_core", ignore=shutil.ignore_patterns("__pycache__")
    )
    script = 'import sys; import workflow_core; assert not any(name.startswith("engineering_ci") or name.startswith("dagger") or name.startswith("psycopg") for name in sys.modules)'
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "raw",
    [b'{"a":1,"a":2}', b'{"a":NaN}', b"[]", b"{", b" " * (2 * 1024 * 1024 + 1)],
    ids=["duplicates", "nonfinite", "array", "broken", "oversized"],
)
def test_bounded_documents_reject_ambiguous_json(tmp_path, raw):
    path = tmp_path / "input.json"
    path.write_bytes(raw)
    with pytest.raises(WorkflowError):
        read(path)


def test_json_pointer_escapes_and_array_indices():
    assert pointer({"a/b": {"~": [3]}}, "/a~1b/~0/0") == 3
    for invalid in ("/a~2b", "/a~1b/~0/00", "/a~1b/~0/-"):
        with pytest.raises(WorkflowError):
            pointer({"a/b": {"~": [3]}}, invalid)


def test_exported_descriptors_and_all_schemas_match_actual_payloads():
    from jsonschema import Draft202012Validator

    from engineering_ci.workflow_core.schemas import validate

    registered, _ = tools()
    for schema in SCHEMAS.values():
        Draft202012Validator.check_schema(schema)
    for descriptor in registered.documents():
        validate(descriptor, SCHEMAS["tool"])


def test_adapter_replacement_uses_same_contract_without_core_edits(tmp_path):
    _, directory, original = execution(tmp_path)
    replacement_catalog, replacement = tools()
    result = Runtime(replacement_catalog).resume(directory)
    assert (
        result["exit_code"] == 0 and not original.calls and len(replacement.calls) == 2
    )


def test_write_size_failure_preserves_original_checkpoint(tmp_path):
    path = tmp_path / "state.json"
    write(path, {"value": "original"})
    before = path.read_bytes()
    with pytest.raises(WorkflowError, match="2 MiB"):
        write(path, {"value": "x" * (2 * 1024 * 1024)}, replace=True)
    assert path.read_bytes() == before


def test_known_write_rejection_is_preserved_not_reclassified_as_unknown(tmp_path):
    runtime, directory, adapter = execution(
        tmp_path, Arithmetic(fail=True), effect="write"
    )
    first = runtime.resume(directory)
    assert first["status"] == "failed" and first["exit_code"] == 7
    result = runtime.resume(directory)
    assert (
        result["phase"] == "PAUSED"
        and result["status"] == "blocked"
        and result["exit_code"] == 7
    )
    assert result["steps"]["first"]["status"] == "failed" and len(adapter.calls) == 1
    assert "Known write outcome retained" in result["decision_required"]


def test_fsm_and_each_adapter_share_one_context_with_isolated_predecessors(
    tmp_path, monkeypatch
):
    from engineering_ci.workflow_core import runtime as implementation

    contexts = []
    original = implementation.ExecutionFSM

    class ObservedFSM(original):
        def __init__(self, state, context):
            contexts.append(context)
            super().__init__(state, context)

    monkeypatch.setattr(implementation, "ExecutionFSM", ObservedFSM)

    class Observe(Arithmetic):
        def invoke(self, calls, context):
            assert context is contexts[0]
            if calls[0].step_id == "second":
                assert context.predecessors["first"]["result"]["outputs"] == {
                    "value": 3
                }
                context.predecessors["first"]["result"]["outputs"]["value"] = 999
            with pytest.raises(TypeError):
                context.predecessors["injected"] = {}
            return super().invoke(calls, context)

    runtime, directory, _ = execution(tmp_path, Observe())
    result = runtime.resume(directory)
    assert result["steps"]["first"]["result"]["outputs"] == {"value": 3}
    assert result["steps"]["second"]["result"]["outputs"] == {"value": 13}


def test_recovery_drops_old_skipped_reason_and_input_output_stamps(tmp_path):
    runtime, directory, adapter = execution(tmp_path, Arithmetic(malformed=True))
    assert runtime.resume(directory)["steps"]["second"]["status"] == "skipped"
    adapter.malformed = False
    result = runtime.resume(directory)
    assert result["status"] == "passed" and "reason" not in result["steps"]["second"]
    assert result["steps"]["second"]["input_sha256"] == digest({"value": 3})
