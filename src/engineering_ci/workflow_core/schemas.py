"""Normative JSON Schema 2020-12 documents, also exported by the host CLI."""

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry
from referencing.exceptions import Unresolvable

from .documents import WorkflowError

DRAFT = "https://json-schema.org/draft/2020-12/schema"
NAME = {"type": "string", "pattern": "^[a-z][a-z0-9-]*$"}
ID = {"type": "string", "pattern": "^[a-f0-9]{32}$"}
HASH = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
TOOL = {"type": "string", "pattern": "^[a-z][a-z0-9-]*\\.[a-z][a-z0-9-]*$"}


def object_schema(properties, required=None):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


STEP = object_schema(
    {
        "id": NAME,
        "tool": TOOL,
        "inputs": {},
        "needs": {"type": "array", "items": NAME, "uniqueItems": True},
        "when": {"enum": ["success", "always", "failure"]},
        "required": {"type": "boolean"},
    },
    ["id", "tool", "inputs"],
)
RECIPE = {
    "$schema": DRAFT,
    **object_schema(
        {
            "schema_version": {"const": 1, "type": "integer"},
            "kind": {"const": "workflow-recipe"},
            "steps": {"type": "array", "items": STEP, "minItems": 1},
        }
    ),
}
CONTRACT_VERSION = {"type": "string", "pattern": "^[1-9][0-9]*(?:\\.[0-9]+){0,2}$"}
DEFINITION_STEP = object_schema(
    {
        **STEP["properties"],
        "contract_version": CONTRACT_VERSION,
        "contract_sha256": HASH,
    }
)
WORKFLOW = {
    "$schema": DRAFT,
    **object_schema(
        {
            "schema_version": {"const": 1, "type": "integer"},
            "kind": {"const": "generated-workflow"},
            "generation_id": ID,
            "definition_id": HASH,
            "provenance_sha256": HASH,
            "capabilities": {
                "type": "array",
                "items": {"enum": ["dag", "data-reference", "batch"]},
                "uniqueItems": True,
            },
            "steps": {"type": "array", "items": DEFINITION_STEP, "minItems": 1},
        }
    ),
}
SOURCE = object_schema(
    {
        "report_id": HASH,
        "candidate_id": HASH,
        "candidate_document_sha256": HASH,
        "analysis_id": HASH,
        "analysis_document_sha256": HASH,
        "projection_id": HASH,
        "projection_document_sha256": HASH,
        "snapshot_id": HASH,
        "completeness": {"enum": ["complete", "incomplete", "unknown"]},
    }
)
PROVENANCE = {
    "$schema": DRAFT,
    **object_schema(
        {
            "schema_version": {"const": 1, "type": "integer"},
            "kind": {"const": "workflow-provenance"},
            "generation_id": ID,
            "definition_id": HASH,
            "generator": {"const": "workflow-generator/1"},
            "mapping_basis": {
                "enum": ["manual_composition", "explicit_target_selection"]
            },
            "source": {"oneOf": [SOURCE, {"type": "null"}]},
            "historical_check_identity": {"const": "unavailable"},
            "optimization_verified": {"const": False},
            "llm_used": {"const": False},
        }
    ),
}
TARGET = object_schema(
    {
        "kind": {"const": "registered-profile"},
        "name": NAME,
        "policy_sha256": HASH,
        "mapping": {
            "type": "array",
            "minItems": 1,
            "items": object_schema({"step_id": NAME, "tool_id": TOOL}),
        },
    }
)
PROVENANCE["properties"]["target"] = TARGET
SCHEMAS = {"recipe": RECIPE, "workflow": WORKFLOW, "provenance": PROVENANCE}

RESULT = object_schema(
    {
        "status": {
            "enum": [
                "passed",
                "failed",
                "incomplete",
                "unavailable",
                "interrupted",
                "unknown",
            ]
        },
        "exit_code": {"type": ["integer", "null"]},
        "outputs": {"type": "object"},
        **{
            key: {"type": "boolean"}
            for key in ("executed", "complete", "current", "reused", "report_generated")
        },
        "reason": {"type": ["string", "null"]},
    }
)
STEP_RESULT = object_schema(
    {
        "status": {
            "enum": [
                "pending",
                "running",
                "skipped",
                "blocked",
                *RESULT["properties"]["status"]["enum"],
            ]
        },
        "attempt": {"type": "integer", "minimum": 0},
        "previous": {"oneOf": [RESULT, {"type": "null"}]},
        "result": RESULT,
        "reason": {"type": "string"},
        "input_sha256": HASH,
        "output_sha256": HASH,
    },
    ["status", "attempt", "previous"],
)
EXECUTION = {
    "$schema": DRAFT,
    **object_schema(
        {
            "schema_version": {"type": "integer", "const": 1},
            "kind": {"const": "workflow-execution"},
            "execution_id": ID,
            "generation_id": ID,
            "definition_id": HASH,
            "phase": {"enum": ["READY", "RUNNING", "PAUSED", "UNKNOWN", "FINISHED"]},
            "status": {
                "enum": [
                    "not_run",
                    "running",
                    "passed",
                    "failed",
                    "interrupted",
                    "unknown",
                    "blocked",
                ]
            },
            "exit_code": {"type": "integer", "minimum": 0},
            "steps": {
                "type": "object",
                "minProperties": 1,
                "additionalProperties": STEP_RESULT,
            },
        }
    ),
}
SCHEMAS.update(tool_result={"$schema": DRAFT, **RESULT}, execution=EXECUTION)
EXECUTION["properties"]["decision_required"] = {"type": "string"}
EXECUTION["properties"]["reuse_source_sha256"] = HASH
EXECUTION["properties"]["observation_protocol"] = {"const": "workflow-observation/1"}
TOOL_DESCRIPTOR = {
    "$schema": DRAFT,
    **object_schema(
        {
            "tool_id": TOOL,
            "contract_version": CONTRACT_VERSION,
            "input_schema": {"type": ["object", "boolean"]},
            "output_schema": {"type": ["object", "boolean"]},
            "effect": {"enum": ["read", "check", "write"]},
            "recovery": {"enum": ["restartable", "manual"]},
            "reuse": {"const": "verified_identity"},
            "environments": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": NAME,
            },
        }
    ),
}
SCHEMAS["tool"] = TOOL_DESCRIPTOR
TOOL_DESCRIPTOR["properties"]["contract_sha256"] = HASH

OBSERVATION = {
    "$schema": DRAFT,
    **object_schema(
        {
            "schema_version": {"const": 1, "type": "integer"},
            "kind": {"const": "workflow-observation"},
            "execution_id": ID,
            "generation_id": ID,
            "definition_id": HASH,
            "session_id": ID,
            "sequence": {"type": "integer", "minimum": 1},
            "observed_ns": {"type": "integer", "minimum": 0},
            "event": {
                "enum": [
                    "session_started",
                    "admitted",
                    "invoking",
                    "returned",
                    "recovered",
                    "skipped",
                    "blocked",
                    "session_finished",
                ]
            },
            "step_id": NAME,
            "tool_id": TOOL,
            "contract_sha256": HASH,
            "attempt": {"type": "integer", "minimum": 0},
            "input_sha256": HASH,
            "output_sha256": HASH,
            "result_sha256": HASH,
            "previous_result_sha256": HASH,
            "result": object_schema(
                {
                    key: RESULT["properties"][key]
                    for key in (
                        "status",
                        "exit_code",
                        "executed",
                        "complete",
                        "current",
                        "reused",
                        "report_generated",
                    )
                }
            ),
            "phase": EXECUTION["properties"]["phase"],
            "status": EXECUTION["properties"]["status"],
            "exit_code": {"type": "integer"},
            "prior_status": STEP_RESULT["properties"]["status"],
            "missing_records": {"type": "integer", "minimum": 0},
        },
        [
            "schema_version",
            "kind",
            "execution_id",
            "generation_id",
            "definition_id",
            "session_id",
            "sequence",
            "observed_ns",
            "event",
        ],
    ),
}
SCHEMAS["observation"] = OBSERVATION
OBSERVATION["allOf"] = [
    {"if": {"properties": {"event": {"enum": events}}}, "then": {"required": fields}}
    for events, fields in (
        (["session_started"], ["phase", "status"]),
        (["session_finished"], ["phase", "status", "exit_code", "missing_records"]),
        (
            ["admitted", "invoking", "returned", "recovered", "skipped", "blocked"],
            ["step_id", "tool_id", "attempt", "contract_sha256"],
        ),
        (["admitted", "invoking", "returned"], ["input_sha256"]),
        (["returned"], ["result", "result_sha256", "output_sha256"]),
    )
]


def validate(value, schema, *, label="document"):
    """Validate offline; diagnostic paths never include invalid instance values."""
    try:
        Draft202012Validator(schema, registry=Registry()).validate(value)
    except (ValidationError, Unresolvable, TypeError, RecursionError) as error:
        path = "/".join(str(item) for item in getattr(error, "absolute_path", ()))
        raise WorkflowError(f"Invalid {label} at /{path}") from None


def check_schema(schema):
    """Accept standard schemas with local references; no network schema retrieval."""

    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"$ref", "$dynamicRef"} and not item.startswith("#"):
                    raise WorkflowError("Tool schemas must use local references")
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    try:
        visit(schema)
        Draft202012Validator.check_schema(schema)
    except WorkflowError:
        raise
    except (SchemaError, TypeError, AttributeError, RecursionError):
        raise WorkflowError("Invalid tool JSON Schema") from None
