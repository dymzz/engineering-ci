"""Original CI-specific approval and feedback contracts; serialized identities are unchanged."""

from copy import deepcopy

from vesryn.workflow_core.capabilities import APPROVAL as GENERIC_APPROVAL
from vesryn.workflow_core.capabilities import REFERENCE, SCOPE
from vesryn.workflow_core.schemas import HASH, ID, object_schema

from .feedback_snapshot import SNAPSHOT

APPROVAL = deepcopy(GENERIC_APPROVAL)
APPROVAL["properties"]["verified_scope"] = {"const": "check-submission-reuse"}

FEEDBACK = object_schema(
    {
        "schema_version": {"const": 1, "type": "integer"},
        "kind": {"const": "workflow-capability-feedback"},
        "feedback_id": HASH,
        "capability": REFERENCE,
        "definition_id": HASH,
        "generation_id": ID,
        "catalog_revision": {"type": "integer", "minimum": 0},
        "evidence": SCOPE,
        "recorded_result": object_schema(
            {
                "phase": {
                    "enum": ["READY", "RUNNING", "PAUSED", "UNKNOWN", "FINISHED"]
                },
                "status": {
                    "enum": [
                        "not_run",
                        "running",
                        "passed",
                        "failed",
                        "incomplete",
                        "unavailable",
                        "interrupted",
                        "unknown",
                        "blocked",
                    ]
                },
                "exit_code": {"type": ["integer", "null"]},
            }
        ),
        "observation_completeness": {"enum": ["complete", "incomplete", "unknown"]},
        "metrics": object_schema(
            {
                key: {"type": ["integer", "null"], "minimum": 0}
                for key in (
                    "required_checks",
                    "observed_check_attempts",
                    "check_submissions",
                    "reused_checks",
                    "adapter_batches",
                )
            }
        ),
        "current_input_validity": {"const": "not_checked"},
        "task_outcome_scope": {"const": "original-registered-checks"},
        "optimization_evaluated": {"const": False},
        "llm_used": {"const": False},
    }
)
FEEDBACK["properties"]["schema_version"] = {"enum": [1, 2], "type": "integer"}
FEEDBACK["properties"]["attempt_snapshot"] = SNAPSHOT
FEEDBACK["allOf"] = [
    {
        "if": {"properties": {"schema_version": {"const": 2}}},
        "then": {"required": ["attempt_snapshot"]},
        "else": {"not": {"required": ["attempt_snapshot"]}},
    }
]

SELECTION_BINDING = object_schema(
    {
        "schema_version": {"const": 1, "type": "integer"},
        "kind": {"const": "workflow-selection-binding"},
        "binding_id": HASH,
        "tool_id": {"type": "string"},
        "provider": {"type": "string"},
        "version": {"type": "string"},
        "contract_sha256": HASH,
        "contract_summary": object_schema(
            {
                "tool_id": {"type": "string"},
                "description": {"type": "string"},
                "domain": {"type": "string"},
                "effect": {"type": "string"},
                "contract_version": {"type": "string"},
                "contract_sha256": HASH,
            }
        ),
        "parameters": {"type": "object"},
        "scope": object_schema(
            {
                "repository": {"type": "string"},
                "environment": {"type": "string"},
                "permissions": {"type": "array", "items": {"type": "string"}},
            }
        ),
        "workflow": object_schema(
            {
                "definition_id": HASH,
                "generation_id": ID,
                "workflow_path": {"type": "string"},
            }
        ),
        "execution_id": {"type": ["string", "null"], "pattern": "^[a-f0-9]{32}$"},
    },
    [
        "schema_version",
        "kind",
        "binding_id",
        "tool_id",
        "provider",
        "version",
        "contract_sha256",
        "contract_summary",
        "parameters",
        "scope",
        "workflow",
    ],
)
