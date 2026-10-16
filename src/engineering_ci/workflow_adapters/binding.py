"""Persisted tool selection binding over unified Registry and Dagger execution graph."""

import copy
from pathlib import Path

from vesryn.workflow_core import DiscoveryScope, Runtime, generate, load_generation
from vesryn.workflow_core.documents import WorkflowError, digest, read, write
from vesryn.workflow_core.schemas import validate

from .contracts import SELECTION_BINDING

HOST_SCOPE = DiscoveryScope(
    repository="engineering-ci", environment="dagger", permissions=frozenset({"check"})
)


def binding_catalog(provider, root):
    """Load the explicit installed Provider through Vesryn's existing registry."""
    from vesryn.registry import provider as load_provider

    tools = load_provider(provider, root)
    tools.provider_name = provider
    return tools


def binding_document(path):
    """Reject altered metadata before loading its named Provider."""
    binding = read(path)
    _verify_document(binding)
    return binding


def _verify_document(binding):
    validate(binding, SELECTION_BINDING, label="selection binding")
    if binding["binding_id"] != digest(canonical_binding(binding)):
        raise WorkflowError(
            "Selection binding integrity check failed (tampered binding)"
        )


def _provider_matches(provider, catalog):
    if provider != getattr(catalog, "provider_name", None):
        raise WorkflowError(
            "Selection binding Provider does not match the loaded catalog"
        )


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
    root = Path(root).resolve() if root is not None else None
    parameters = {} if parameters is None else copy.deepcopy(parameters)
    effective_scope = scope or HOST_SCOPE
    _provider_matches(provider, catalog)
    spec, _ = catalog.resolve(tool_id, scope=effective_scope)
    validate(parameters, spec.input_schema, label="tool parameters")

    out_dir = _path(out_dir or ".test-tools/workflow-bindings", root)
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
    _verify_document(binding)
    current_scope = current_scope or HOST_SCOPE
    for key in ("repository", "environment"):
        current = getattr(current_scope, key)
        if current and binding["scope"][key] != current:
            raise WorkflowError(f"Selection binding {key} scope mismatch")
    if current_scope.permissions is not None:
        bound_perms = set(binding["scope"]["permissions"])
        if not bound_perms.issubset(current_scope.permissions):
            raise WorkflowError("Selection binding permission scope mismatch")

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

    _provider_matches(binding["provider"], catalog)
    bound_scope = DiscoveryScope(
        repository=binding["scope"]["repository"],
        environment=binding["scope"]["environment"],
        permissions=frozenset(binding["scope"]["permissions"]),
    )
    catalog.resolve(binding["tool_id"], scope=bound_scope)
    summary = binding["contract_summary"]
    if any(
        summary[key] != spec.summary()[key]
        for key in ("tool_id", "effect", "contract_version", "contract_sha256")
    ):
        raise WorkflowError("Selection binding contract summary differs from its tool")
    validate(binding["parameters"], spec.input_schema, label="tool parameters")
    return spec, adapter


def _path(path, root=None):
    path = Path(path)
    if root:
        from .capabilities import inside

        return inside(path, root)
    return path.resolve()


def verify_workflow(binding, catalog, path=None, *, root=None, current_scope=None):
    """Admit the actual generation, including its sole tool and literal parameters."""
    verify_binding(binding, catalog, current_scope=current_scope)
    path = _path(path or binding["workflow"]["workflow_path"], root)
    value, _, _ = load_generation(path, catalog)
    if any(
        value[key] != binding["workflow"][key]
        for key in ("definition_id", "generation_id")
    ):
        raise WorkflowError("Workflow identity differs from the selection binding")
    expected = {
        "id": binding["tool_id"].split(".")[-1],
        "tool": binding["tool_id"],
        "inputs": binding["parameters"],
        "contract_version": binding["version"],
        "contract_sha256": binding["contract_sha256"],
        "needs": [],
        "when": "success",
        "required": True,
    }
    if value["steps"] != [expected]:
        raise WorkflowError(
            "Workflow steps differ from the selected tool and parameters"
        )
    return path


class BindingRuntime(Runtime):
    """Use Runtime's existing execution lock and recheck admission before mutation."""

    def __init__(self, catalog, *, current_scope=None):
        super().__init__(catalog)
        self.current_scope = current_scope

    def status(self, directory):
        directory = Path(directory).resolve()
        binding = binding_document(directory / "binding.json")
        verify_workflow(
            binding,
            self.catalog,
            directory / "workflow.json",
            current_scope=self.current_scope,
        )
        state = super().status(directory)
        if binding.get("execution_id") != state["execution_id"]:
            raise WorkflowError("Execution ID mismatch between state and binding")
        return state


def create_bound_execution(
    binding, catalog, runs_dir, *, reuse_from=None, root=None, current_scope=None
):
    """Freeze an admitted selection; no tool executes while creating the checkpoint."""
    workflow = verify_workflow(binding, catalog, root=root, current_scope=current_scope)
    runtime = BindingRuntime(catalog, current_scope=current_scope)
    execution_dir = runtime.create(
        workflow,
        _path(runs_dir, root),
        reuse_from=_path(reuse_from, root) if reuse_from else None,
    )

    frozen_binding = copy.deepcopy(binding)
    frozen_binding["execution_id"] = execution_dir.name
    frozen_binding["binding_id"] = digest(canonical_binding(frozen_binding))
    validate(frozen_binding, SELECTION_BINDING, label="selection binding")
    write(execution_dir / "binding.json", frozen_binding)

    return execution_dir


def restore_binding(execution_dir, catalog, *, current_scope=None):
    """Restore execution checkpoint and verify bound tool contract explicitly."""
    execution_dir = Path(execution_dir).resolve()
    state = BindingRuntime(catalog, current_scope=current_scope).status(execution_dir)
    return _result(execution_dir, state)


def _result(execution_dir, state):
    binding = binding_document(execution_dir / "binding.json")
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
    execution_dir = Path(execution_dir).resolve()
    runtime = BindingRuntime(catalog, current_scope=current_scope)
    state = runtime.resume(execution_dir)
    return _result(execution_dir, state)


def call_binding(
    binding, catalog, runs_dir, *, reuse_from=None, root=None, current_scope=None
):
    """Execute only the admitted selection, retaining the original result and evidence."""
    directory = create_bound_execution(
        binding,
        catalog,
        runs_dir,
        reuse_from=reuse_from,
        root=root,
        current_scope=current_scope,
    )
    return resume_binding(directory, catalog, current_scope=current_scope)
