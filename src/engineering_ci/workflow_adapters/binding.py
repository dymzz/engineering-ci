"""Persisted tool selection binding over unified Registry and Dagger execution graph."""

import copy
from pathlib import Path

from vesryn.workflow_core import DiscoveryScope, Runtime, generate
from vesryn.workflow_core.documents import WorkflowError, digest, read, write
from vesryn.workflow_core.schemas import validate

from .contracts import SELECTION_BINDING


def canonical_binding(binding):
    """Deterministic canonical representation excluding computed binding_id."""
    return {k: v for k, v in binding.items() if k != "binding_id"}


def create_binding(
    catalog,
    tool_id,
    *,
    provider="engineering-ci-dagger",
    parameters=None,
    scope=None,
    out_dir=None,
    root=None,
):
    """Freeze an explicit tool selection from Registry into an immutable binding document."""
    parameters = {} if parameters is None else copy.deepcopy(parameters)
    effective_scope = scope or DiscoveryScope(
        repository="engineering-ci",
        environment="dagger",
        permissions=frozenset(["check"]),
    )
    spec, _ = catalog.resolve(tool_id, scope=effective_scope)
    validate(parameters, spec.input_schema, label="tool parameters")

    out_dir = Path(out_dir or ".test-tools/workflow-bindings").resolve()
    step_id = tool_id.split(".")[-1]
    recipe = {
        "schema_version": 1,
        "kind": "workflow-recipe",
        "steps": [{"id": step_id, "tool": tool_id, "inputs": parameters}],
    }
    workflow_path = generate(recipe, catalog, out_dir)
    workflow_doc = read(workflow_path)

    binding = {
        "schema_version": 1,
        "kind": "workflow-selection-binding",
        "tool_id": spec.tool_id,
        "provider": provider,
        "version": spec.contract_version,
        "contract_sha256": spec.sha256,
        "contract_summary": spec.summary(),
        "parameters": parameters,
        "scope": {
            "repository": effective_scope.repository or "",
            "environment": effective_scope.environment or "",
            "permissions": (
                sorted(effective_scope.permissions)
                if effective_scope.permissions
                else []
            ),
        },
        "workflow": {
            "definition_id": workflow_doc["definition_id"],
            "generation_id": workflow_doc["generation_id"],
            "workflow_path": (
                workflow_path.relative_to(root).as_posix()
                if root and workflow_path.is_relative_to(Path(root).resolve())
                else str(workflow_path)
            ),
        },
        "execution_id": None,
    }
    binding["binding_id"] = digest(canonical_binding(binding))
    validate(binding, SELECTION_BINDING, label="selection binding")
    binding_path = workflow_path.parent / "binding.json"
    write(binding_path, binding)
    return binding, binding_path


def verify_binding(binding, catalog, *, current_scope=None):
    """Verify selection binding integrity and reject stale or altered contracts."""
    validate(binding, SELECTION_BINDING, label="selection binding")
    expected_id = digest(canonical_binding(binding))
    if binding["binding_id"] != expected_id:
        raise WorkflowError(
            "Selection binding integrity check failed (tampered binding)"
        )

    if current_scope is not None:
        if (
            current_scope.repository
            and binding["scope"]["repository"]
            and binding["scope"]["repository"] != current_scope.repository
        ):
            raise WorkflowError(
                f"Selection binding repository scope mismatch: expected {current_scope.repository}, got {binding['scope']['repository']}"
            )
        if (
            current_scope.environment
            and binding["scope"]["environment"]
            and binding["scope"]["environment"] != current_scope.environment
        ):
            raise WorkflowError(
                f"Selection binding environment scope mismatch: expected {current_scope.environment}, got {binding['scope']['environment']}"
            )
        if current_scope.permissions is not None:
            bound_perms = set(binding["scope"]["permissions"])
            if not bound_perms.issubset(current_scope.permissions):
                raise WorkflowError(
                    f"Selection binding permission scope mismatch: {bound_perms} not in {current_scope.permissions}"
                )

    spec, adapter = catalog.resolve(binding["tool_id"], scope=current_scope)

    if spec.contract_version != binding["version"]:
        raise WorkflowError(
            f"Tool contract version changed from {binding['version']} to {spec.contract_version}; "
            "refuse to use stale binding; do not auto-select latest"
        )

    if spec.sha256 != binding["contract_sha256"]:
        raise WorkflowError(
            "Tool contract sha256 changed; refuse to use stale binding; do not auto-select latest"
        )

    validate(binding["parameters"], spec.input_schema, label="tool parameters")
    return spec, adapter


def call_binding(binding, catalog, runs_dir, *, reuse_from=None, root=None):
    """Execute bound tool through Dagger execution graph and correlate raw evidence."""
    verify_binding(binding, catalog)

    workflow_path = binding["workflow"]["workflow_path"]
    resolved_workflow = (
        (Path(root) / workflow_path).resolve()
        if root and not Path(workflow_path).is_absolute()
        else Path(workflow_path).resolve()
    )

    runs_dir = Path(runs_dir).resolve()
    reuse_dir = (
        (Path(root) / reuse_from).resolve()
        if root and reuse_from and not Path(reuse_from).is_absolute()
        else Path(reuse_from).resolve()
        if reuse_from
        else None
    )

    runtime = Runtime(catalog)
    execution_dir = runtime.create(resolved_workflow, runs_dir, reuse_from=reuse_dir)

    frozen_binding = copy.deepcopy(binding)
    frozen_binding["execution_id"] = execution_dir.name
    frozen_binding["binding_id"] = digest(canonical_binding(frozen_binding))
    validate(frozen_binding, SELECTION_BINDING, label="selection binding")
    write(execution_dir / "binding.json", frozen_binding)

    state = runtime.resume(execution_dir)

    step_results = {}
    for step_id, step_info in state["steps"].items():
        res = step_info.get("result", {})
        outputs = res.get("outputs", {})
        step_results[step_id] = {
            "status": step_info["status"],
            "exit_code": res.get("exit_code"),
            "executed": res.get("executed", False),
            "reused": res.get("reused", False),
            "report": outputs.get("report"),
            "evidence": outputs.get("evidence", []),
        }

    return {
        "execution": str(execution_dir.resolve()),
        "execution_id": execution_dir.name,
        "binding": frozen_binding,
        "state": state,
        "status": state["status"],
        "exit_code": state["exit_code"],
        "step_results": step_results,
    }


def restore_binding(execution_dir, catalog, *, current_scope=None):
    """Restore execution checkpoint and verify bound tool contract explicitly."""
    execution_dir = Path(execution_dir).resolve()
    binding_path = execution_dir / "binding.json"
    if not binding_path.is_file():
        raise WorkflowError("Execution directory does not contain selection binding")

    binding = read(binding_path)
    verify_binding(binding, catalog, current_scope=current_scope)

    state_path = execution_dir / "state.json"
    if not state_path.is_file():
        raise WorkflowError("Execution state is unavailable")
    state = read(state_path)

    if (
        binding.get("execution_id")
        and state.get("execution_id")
        and state["execution_id"] != binding["execution_id"]
    ):
        raise WorkflowError("Execution ID mismatch between state and binding")
    if state.get("definition_id") != binding["workflow"]["definition_id"]:
        raise WorkflowError("Workflow definition ID mismatch between state and binding")

    step_results = {}
    for step_id, step_info in state["steps"].items():
        res = step_info.get("result", {})
        outputs = res.get("outputs", {})
        step_results[step_id] = {
            "status": step_info["status"],
            "exit_code": res.get("exit_code"),
            "executed": res.get("executed", False),
            "reused": res.get("reused", False),
            "report": outputs.get("report"),
            "evidence": outputs.get("evidence", []),
        }

    return {
        "execution": str(execution_dir),
        "execution_id": state["execution_id"],
        "binding": binding,
        "state": state,
        "status": state["status"],
        "exit_code": state["exit_code"],
        "step_results": step_results,
    }


def resume_binding(execution_dir, catalog, *, current_scope=None):
    """Resume execution after verification; preserves failure and reuse without duplicating pytest."""
    restored = restore_binding(execution_dir, catalog, current_scope=current_scope)
    runtime = Runtime(catalog)
    state = runtime.resume(execution_dir)
    restored["state"] = state
    restored["status"] = state["status"]
    restored["exit_code"] = state["exit_code"]

    step_results = {}
    for step_id, step_info in state["steps"].items():
        res = step_info.get("result", {})
        outputs = res.get("outputs", {})
        step_results[step_id] = {
            "status": step_info["status"],
            "exit_code": res.get("exit_code"),
            "executed": res.get("executed", False),
            "reused": res.get("reused", False),
            "report": outputs.get("report"),
            "evidence": outputs.get("evidence", []),
        }
    restored["step_results"] = step_results
    return restored
