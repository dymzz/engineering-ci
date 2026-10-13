"""Bind explicit approval and future CLI calls to original PR comparison evidence."""

from pathlib import Path

from ..workflow_core import Runtime, load_generation
from ..workflow_core.capabilities import CALL
from ..workflow_core.documents import WorkflowError, digest, read, write
from ..workflow_core.schemas import validate
from .candidates import verify_target
from .comparison import compare
from .evidence import execution_pair


def inside(path, root):
    """Keep capability documents and evidence in the actual primary checkout."""
    path = Path(path)
    path = (Path(root) / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise WorkflowError(
            "Capability inputs and store must stay in the primary checkout"
        )
    return path


def approve(store, report, baseline, candidate, name, version, catalog, root):
    """Recompute the original controlled comparison before recording a reviewed version."""
    report, baseline, candidate = [
        inside(path, root) for path in (report, baseline, candidate)
    ]
    if any(store.directory.is_relative_to(path) for path in (baseline, candidate)):
        raise WorkflowError("Capability store must stay outside original executions")
    original = read(report)
    left, right = execution_pair(baseline, candidate, catalog, root)
    verified = compare(left, right)
    if (
        original != verified
        or not verified["optimization_verified"]
        or verified["exit_code"] != 0
    ):
        raise WorkflowError(
            "Approval requires unchanged comparable evidence and a verified passed optimization scope"
        )
    workflow, provenance, _ = load_generation(candidate / "workflow.json", catalog)
    verify_target(workflow, provenance)
    if provenance["source"] is None:
        raise WorkflowError(
            "Capability approval requires an explicitly linked candidate source"
        )
    value = {
        "schema_version": 1,
        "kind": "workflow-capability-approval",
        "name": name,
        "version": version,
        "decision": "explicit-cli-approval",
        "definition_id": workflow["definition_id"],
        "workflow_sha256": digest(workflow),
        "provenance_sha256": digest(provenance),
        "comparison_id": verified["comparison_id"],
        "comparison_sha256": digest(verified),
        "comparison_version": verified["comparison_version"],
        "verified_scope": verified["verified_scope"],
        "baseline": {
            "path": baseline.relative_to(root).as_posix(),
            "scope": left["scope"],
        },
        "candidate": {
            "path": candidate.relative_to(root).as_posix(),
            "scope": right["scope"],
        },
    }
    value["approval_id"] = digest(value)
    return store.publish(value, workflow, provenance, verified)


def verified_comparison(approval, directory, catalog, root):
    """Historical evidence admits this capability; each original tool still checks current inputs."""
    workflow, provenance, _ = load_generation(directory / "workflow.json", catalog)
    verify_target(workflow, provenance)
    inputs = [inside(approval[key]["path"], root) for key in ("baseline", "candidate")]
    left, right = execution_pair(*inputs, catalog, root)
    result = compare(left, right)
    if (
        digest(result) != approval["comparison_sha256"]
        or digest(right["workflow"]) != approval["workflow_sha256"]
        or result["comparison_id"] != approval["comparison_id"]
        or not result["optimization_verified"]
        or result["exit_code"] != 0
        or any(
            view["scope"] != approval[key]["scope"]
            for key, view in zip(("baseline", "candidate"), (left, right), strict=True)
        )
    ):
        raise WorkflowError(
            "Approved evidence is changed, unavailable or no longer admitted; review a new trial"
        )
    return result


def admission(approval, directory, catalog, root):
    """Retain the existing call admission contract using the same comparison verification."""
    verified_comparison(approval, directory, catalog, root)


def create_call(store, name, version, destination, catalog, root):
    """Freeze selected approval before executing; deactivation only blocks later calls."""
    destination = inside(destination, root)
    if destination.is_relative_to(store.directory):
        raise WorkflowError("Execution output must stay outside the capability store")
    with store.locked():
        approval, directory, revision = store.select(name, version, catalog)
        admission(approval, directory, catalog, root)
        execution = Runtime(catalog).create(
            directory / "workflow.json",
            destination,
            reuse_from=inside(approval["candidate"]["path"], root),
        )
        if (
            digest(read(execution / "reuse-source.json"))
            != approval["candidate"]["scope"]["state_sha256"]
        ):
            raise WorkflowError(
                f"Approved reuse source changed during call admission; no tools invoked. Unstarted checkpoint: {execution}"
            )
        binding = {
            "schema_version": 1,
            "kind": "workflow-capability-call",
            "name": name,
            "version": approval["version"],
            "approval_id": approval["approval_id"],
            "catalog_revision": revision,
            "store": store.directory.relative_to(root).as_posix(),
            "execution_id": execution.name,
            "generation_id": Runtime(catalog).status(execution)["generation_id"],
            "definition_id": approval["definition_id"],
        }
        write(execution / "capability.json", binding)
    return execution, binding


def call_reference(directory, state):
    """Expose frozen approval separately from raw generation/result facts."""
    path = Path(directory) / "capability.json"
    if not path.exists():
        return None
    try:
        value = read(path)
        validate(value, CALL, label="capability call")
        if any(
            value[key] != state[key]
            for key in ("execution_id", "definition_id", "generation_id")
        ):
            raise WorkflowError("Capability context differs")
        return {
            "status": "bound",
            "capability": {
                key: value[key] for key in ("name", "version", "approval_id")
            },
            "catalog_revision": value["catalog_revision"],
            "selection_state_now": "not_checked",
        }
    except (WorkflowError, OSError):
        return {"status": "unavailable"}


def discover(store, catalog, root, *, name=None, limit=20, budget=16384, after=None):
    """Page the explicit index; discovery never executes or infers current CI success."""
    from ..tracing.retrieval import Query, after_items, page, page_bounds

    query = Query("candidate", limit=limit, max_bytes=budget, after=after)
    page_bounds(query)
    with store.locked():
        index = store.index()
        entries = [
            {
                **item,
                "key": item["name"] + "/" + item["version"],
                "enabled": index["active"].get(item["name"]) == item["approval_id"],
                "admission": "not_checked",
                "current_input_validity": "not_checked",
            }
            for item in sorted(
                index["versions"], key=lambda value: (value["name"], value["version"])
            )
            if name is None or item["name"] == name
        ]
    scope = {"catalog_sha256": digest(index), "name": name}
    entries = after_items(query, scope, entries, lambda row: row["key"])
    result = page(
        query,
        scope,
        entries,
        key=lambda row: row["key"],
        remaining=len(entries),
        metadata={
            "operation": "workflow.capabilities",
            "selection": "explicit activation; no automatic latest-version selection",
        },
    )
    if result["exit_code"] != 0:
        result["operation"] = "workflow.capabilities"
    return result
