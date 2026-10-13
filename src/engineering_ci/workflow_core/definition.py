"""Generate independent definitions and compile finite DAG/data dependencies."""

import copy
import re
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from uuid import uuid4

from .documents import WorkflowError, digest, read, write
from .schemas import PROVENANCE, RECIPE, WORKFLOW, validate


def semantic(value):
    return {
        key: value[key] for key in ("schema_version", "kind", "capabilities", "steps")
    }


def pointer(value, path):
    """Resolve an RFC 6901 pointer without expressions or implicit attribute access."""
    if path == "":
        return value
    if not isinstance(path, str) or not path.startswith("/"):
        raise WorkflowError("Data reference requires a JSON Pointer")
    for part in path[1:].split("/"):
        if re.search(r"~(?![01])", part):
            raise WorkflowError("Invalid JSON Pointer escape")
        key = part.replace("~1", "/").replace("~0", "~")
        try:
            if isinstance(value, list):
                if not re.fullmatch(r"0|[1-9][0-9]*", key):
                    raise ValueError
                value = value[int(key)]
            else:
                value = value[key]
        except (KeyError, IndexError, TypeError, ValueError):
            raise WorkflowError("Data reference output is missing") from None
    return value


def references(value):
    if isinstance(value, dict):
        if "$from" in value:
            if set(value) != {"$from", "pointer"} or not isinstance(
                value["$from"], str
            ):
                raise WorkflowError("Invalid data reference")
            if value["pointer"] != "":
                _pointer_parts(value["pointer"])
            yield value
        else:
            for item in value.values():
                yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)


def _pointer_parts(path):
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or re.search(r"~(?![01])", path)
    ):
        raise WorkflowError("Invalid data reference JSON Pointer")
    return [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]


def output_schema(schema, path):
    for part in [] if path == "" else _pointer_parts(path):
        if schema.get("type") == "object" and part in schema.get("properties", {}):
            if part not in schema.get("required", []):
                raise WorkflowError(
                    "Referenced output must be required by its tool contract"
                )
            schema = schema["properties"][part]
        elif schema.get("type") == "array" and re.fullmatch(r"0|[1-9][0-9]*", part):
            schema = schema.get("items", {})
        else:
            raise WorkflowError("Referenced output cannot be statically resolved")
    return schema


def template_validate(value, schema, steps, catalog):
    """Check literal inputs now and the known types of explicit output references."""
    if isinstance(value, dict) and "$from" in value:
        source = steps[value["$from"]]
        produced = output_schema(
            catalog.resolve(source["tool"])[0].output_schema, value["pointer"]
        )
        actual, expected = produced.get("type"), schema.get("type")
        allowed = expected if isinstance(expected, list) else [expected]
        types = actual if isinstance(actual, list) else [actual]
        if expected and (
            not actual
            or any(
                t not in allowed and not (t == "integer" and "number" in allowed)
                for t in types
            )
        ):
            raise WorkflowError("Data reference type differs from the receiving input")
        return
    refs = list(references(value))
    if not refs:
        validate(value, schema, label="tool input")
        return
    if isinstance(value, dict) and schema.get("type") == "object":
        properties = schema.get("properties", {})
        if not set(schema.get("required", [])) <= value.keys():
            raise WorkflowError("Missing required tool input")
        if (
            schema.get("additionalProperties") is False
            and value.keys() - properties.keys()
        ):
            raise WorkflowError("Unknown tool input field")
        for key, item in value.items():
            template_validate(item, properties.get(key, {}), steps, catalog)
    elif isinstance(value, list) and schema.get("type") == "array":
        for item in value:
            template_validate(item, schema.get("items", {}), steps, catalog)
    else:
        raise WorkflowError("Referenced inputs require explicit object/array schemas")


def compile_workflow(value, catalog):
    """Reject unsupported capabilities, contracts, inputs and graphs before tool calls."""
    validate(value, WORKFLOW, label="workflow")
    if value["definition_id"] != digest(semantic(value)):
        raise WorkflowError("Workflow definition identity differs from its content")
    steps = {step["id"]: step for step in value["steps"]}
    if len(steps) != len(value["steps"]):
        raise WorkflowError("Duplicate step ID")
    if not any(step["required"] for step in steps.values()):
        raise WorkflowError("Workflow must contain at least one required step")
    graph = {}
    for step in value["steps"]:
        spec, _ = catalog.resolve(step["tool"])
        if (
            spec.contract_version != step["contract_version"]
            or spec.sha256 != step["contract_sha256"]
        ):
            raise WorkflowError("Tool contract changed; generate a new definition")
        dependencies = set(step["needs"]) | {
            ref["$from"] for ref in references(step["inputs"])
        }
        if dependencies - steps.keys():
            raise WorkflowError("Step dependency is missing")
        if step["when"] == "failure" and not dependencies:
            raise WorkflowError("Failure conditions require a predecessor")
        graph[step["id"]] = dependencies
        template_validate(step["inputs"], spec.input_schema, steps, catalog)
    try:
        order = list(TopologicalSorter(graph).static_order())
    except CycleError:
        raise WorkflowError("Workflow dependencies contain a cycle") from None
    return {
        "order": order,
        "dependencies": {key: sorted(value) for key, value in graph.items()},
    }


def generate(recipe, catalog, destination, *, source=None, target=None):
    """Create a new generation directory; semantic identity excludes output locations."""
    validate(recipe, RECIPE, label="recipe")
    generation_id = uuid4().hex
    steps = []
    for original in recipe["steps"]:
        step = copy.deepcopy(original)
        spec, _ = catalog.resolve(step["tool"])
        step.update(contract_version=spec.contract_version, contract_sha256=spec.sha256)
        for key, default in (("needs", []), ("when", "success"), ("required", True)):
            step.setdefault(key, default)
        steps.append(step)
    value = {
        "schema_version": 1,
        "kind": "generated-workflow",
        "generation_id": generation_id,
        "capabilities": ["dag", "data-reference", "batch"],
        "steps": steps,
    }
    value["definition_id"] = digest(semantic(value))
    provenance = {
        "schema_version": 1,
        "kind": "workflow-provenance",
        "generation_id": generation_id,
        "definition_id": value["definition_id"],
        "generator": "workflow-generator/1",
        "mapping_basis": "explicit_target_selection"
        if source
        else "manual_composition",
        "source": source,
        "historical_check_identity": "unavailable",
        "optimization_verified": False,
        "llm_used": False,
    }
    if target is not None:
        provenance["target"] = copy.deepcopy(target)
    validate(provenance, PROVENANCE, label="provenance")
    validate_target(value, provenance)
    value["provenance_sha256"] = digest(provenance)
    compile_workflow(value, catalog)
    directory = Path(destination) / generation_id
    directory.mkdir(parents=True, exist_ok=False)
    write(directory / "provenance.json", provenance)
    write(directory / "workflow.json", value)
    return directory / "workflow.json"


def load_generation(path, catalog):
    value, provenance = read(path), read(Path(path).with_name("provenance.json"))
    plan = compile_workflow(value, catalog)
    validate(provenance, PROVENANCE, label="provenance")
    if value["provenance_sha256"] != digest(provenance) or any(
        value[key] != provenance[key] for key in ("generation_id", "definition_id")
    ):
        raise WorkflowError("Generation provenance does not match the definition")
    if bool(provenance["source"]) != (
        provenance["mapping_basis"] == "explicit_target_selection"
    ):
        raise WorkflowError("Generation source and mapping basis differ")
    validate_target(value, provenance)
    return value, provenance, plan


def validate_target(value, provenance):
    """An explicit profile mapping must describe the actual complete definition."""
    if "target" not in provenance:
        return
    mapping = provenance["target"]["mapping"]
    actual = [
        {"step_id": step["id"], "tool_id": step["tool"]} for step in value["steps"]
    ]
    if mapping != actual or any(
        not step["required"] or step["when"] != "success" for step in value["steps"]
    ):
        raise WorkflowError("Target mapping differs from the required workflow steps")


def resolve_inputs(value, outputs):
    if isinstance(value, dict):
        if "$from" in value:
            return copy.deepcopy(pointer(outputs[value["$from"]], value["pointer"]))
        return {key: resolve_inputs(item, outputs) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_inputs(item, outputs) for item in value]
    return value
