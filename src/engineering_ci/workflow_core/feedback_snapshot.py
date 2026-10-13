"""Content-only attempt indices for cumulative feedback; independent of storage and tools."""

from .documents import WorkflowError, digest
from .schemas import HASH, ID, NAME, RESULT, TOOL, object_schema

NULL_HASH = {"anyOf": [HASH, {"type": "null"}]}
ATTEMPT = object_schema(
    {
        "step_id": NAME,
        "attempt": {"type": "integer", "minimum": 1},
        "tool_id": TOOL,
        "contract_sha256": HASH,
        "session_id": ID,
        "batch_id": {"anyOf": [ID, {"type": "null"}]},
        "input_sha256": NULL_HASH,
        "result_sha256": NULL_HASH,
        "source_row_sha256": HASH,
        "observation_status": {"enum": ["complete", "incomplete"]},
        "check_status": {"enum": ["verified", "unavailable"]},
        "check_submitted": {"type": ["boolean", "null"]},
        "evidence_reused": {"type": ["boolean", "null"]},
        "result_status": {"anyOf": [RESULT["properties"]["status"], {"type": "null"}]},
        "exit_code": {"type": ["integer", "null"]},
    }
)
SNAPSHOT = object_schema(
    {
        "protocol": {"const": "workflow-feedback-attempts/1"},
        "required_step_ids": {
            "type": "array",
            "items": NAME,
            "uniqueItems": True,
            "maxItems": 256,
        },
        "attempts": {"type": "array", "items": ATTEMPT, "maxItems": 10000},
    }
)


def measured(snapshot, complete):
    """Use original submission markers; unknown measurements never become zero."""
    rows = snapshot["attempts"]
    submissions = [row["check_submitted"] for row in rows]
    reused = [row["evidence_reused"] for row in rows]
    return {
        "required_checks": len(snapshot["required_step_ids"]),
        "observed_check_attempts": len(rows),
        "check_submissions": sum(submissions)
        if complete and all(type(item) is bool for item in submissions)
        else None,
        "reused_checks": sum(reused)
        if complete and all(type(item) is bool for item in reused)
        else None,
        "adapter_batches": len({(row["session_id"], row["batch_id"]) for row in rows})
        if complete and all(row["batch_id"] is not None for row in rows)
        else None,
    }


def checked_snapshot(feedback):
    """Check semantic identities and metrics after the original feedback Schema validation."""
    snapshot = feedback.get("attempt_snapshot")
    if snapshot is None:
        return None
    keys = [(row["step_id"], row["attempt"]) for row in snapshot["attempts"]]
    if len(keys) != len(set(keys)) or any(
        step not in snapshot["required_step_ids"] for step, _ in keys
    ):
        raise WorkflowError(
            "Feedback attempt index has duplicate or unknown step identities"
        )
    complete = feedback["observation_completeness"] == "complete"
    if complete:
        for step in snapshot["required_step_ids"]:
            attempts = {number for key, number in keys if key == step}
            if sorted(attempts) != list(range(1, len(attempts) + 1)):
                raise WorkflowError(
                    "Complete feedback attempt index has an admission gap"
                )
    for row in snapshot["attempts"]:
        if row["check_status"] != "verified" and any(
            row[key] is not None for key in ("check_submitted", "evidence_reused")
        ):
            raise WorkflowError(
                "Unavailable check evidence cannot supply submission measurements"
            )
        if row["observation_status"] == "complete" and any(
            row[key] is None
            for key in ("input_sha256", "result_sha256", "result_status")
        ):
            raise WorkflowError(
                "Complete attempt is missing its original boundary facts"
            )
    if measured(snapshot, complete) != feedback["metrics"]:
        raise WorkflowError(
            "Feedback cumulative metrics do not match its attempt index"
        )
    return snapshot


def from_view(view):
    """Project existing verified evidence rows without commands, paths, logs or new execution."""
    attempts = []
    for row in view["items"]:
        result = row["result"] or {}
        attempts.append(
            {
                **{
                    key: row[key]
                    for key in (
                        "step_id",
                        "attempt",
                        "tool_id",
                        "contract_sha256",
                        "session_id",
                        "input_sha256",
                        "result_sha256",
                        "observation_status",
                    )
                },
                "batch_id": row["batch"]["id"] if row["batch"] else None,
                "source_row_sha256": digest(row),
                "check_status": row["check"]["status"],
                "check_submitted": row["check"].get("actual_check_executed"),
                "evidence_reused": row["check"].get("evidence_reused"),
                "result_status": result.get("status"),
                "exit_code": result.get("exit_code"),
            }
        )
    return {
        "protocol": "workflow-feedback-attempts/1",
        "required_step_ids": sorted(view["dependencies"]),
        "attempts": attempts,
    }
