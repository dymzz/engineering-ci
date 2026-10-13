"""Admit trace provenance without importing tracing/mining into the workflow core."""

from ..mining.bindings import bind
from ..mining.bindings import target as registered_target
from ..pipeline import PIPELINE
from ..workflow_core.documents import WorkflowError
from .checks import tool_id


def selected_source(evidence, candidate_id):
    """Reuse the verified candidate admission; mapping stays an explicit user choice."""
    return bind(evidence, candidate_id)["source"]


def profile_target(recipe, *, pipeline=PIPELINE):
    """Bind the complete registered PR profile without inferring historical calls."""
    original = registered_target(pipeline)
    expected = [(name, tool_id(name)) for name in original["checks"]]
    actual = [(step["id"], step["tool"]) for step in recipe["steps"]]
    if actual != expected or any(
        not step.get("required", True) or step.get("when", "success") != "success"
        for step in recipe["steps"]
    ):
        raise WorkflowError(
            "Candidate target requires the complete registered PR profile"
        )
    return {
        "kind": "registered-profile",
        "name": "pr",
        "policy_sha256": original["pipeline_sha256"],
        "mapping": [{"step_id": name, "tool_id": tool} for name, tool in actual],
    }


def verify_target(workflow, provenance, *, pipeline=PIPELINE):
    """Reject a changed policy before invocation; legacy definitions stay readable."""
    if "target" not in provenance:
        return "unavailable"
    if provenance["target"] != profile_target(workflow, pipeline=pipeline):
        raise WorkflowError(
            "Target policy changed; generate and review a new definition"
        )
    return "matched"
