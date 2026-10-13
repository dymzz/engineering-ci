"""Safe actual-call feedback, durable locally and explicitly archived to PostgreSQL."""

from ..workflow_core.capabilities import CALL, FEEDBACK, CapabilityStore
from ..workflow_core.documents import WorkflowError, digest, read, write
from ..workflow_core.feedback_snapshot import checked_snapshot, from_view
from ..workflow_core.schemas import validate
from .capabilities import inside
from .comparison import metrics
from .evidence import execution_evidence


def prepare(execution, catalog, root):
    """Join the frozen selection to an actual execution; never infer task or optimization success."""
    execution = inside(execution, root)
    binding = read(execution / "capability.json")
    validate(binding, CALL, label="capability call")
    store = CapabilityStore(inside(binding["store"], root))
    with store.locked():
        approval, _ = store.load(binding["name"], binding["version"], catalog)
    view = execution_evidence(execution, catalog, root)
    if (
        binding["approval_id"] != approval["approval_id"]
        or binding["execution_id"] != view["scope"]["execution_id"]
        or binding["generation_id"] != view["metadata"]["generation_id"]
        or digest(view["workflow"]) != approval["workflow_sha256"]
        or binding["definition_id"] != approval["definition_id"]
        or binding["definition_id"] != view["metadata"]["definition_id"]
    ):
        raise WorkflowError("Execution does not match its frozen approved capability")
    value = {
        "schema_version": 2,
        "kind": "workflow-capability-feedback",
        "capability": {key: binding[key] for key in ("name", "version", "approval_id")},
        "definition_id": view["metadata"]["definition_id"],
        "generation_id": view["metadata"]["generation_id"],
        "catalog_revision": binding["catalog_revision"],
        "evidence": view["scope"],
        "recorded_result": view["metadata"]["recorded_result"],
        "observation_completeness": view["metadata"]["observation_completeness"],
        "metrics": {
            key: item
            for key, item in metrics(view).items()
            if key in FEEDBACK["properties"]["metrics"]["properties"]
        },
        "attempt_snapshot": from_view(view),
        "current_input_validity": "not_checked",
        "task_outcome_scope": "original-registered-checks",
        "optimization_evaluated": False,
        "llm_used": False,
    }
    value["feedback_id"] = digest(value)
    validate(value, FEEDBACK, label="capability feedback")
    checked_snapshot(value)
    path = store.directory / "feedback" / (value["feedback_id"] + ".json")
    if path.exists():
        if read(path) != value:
            raise WorkflowError("Feedback identity conflicts with saved content")
    else:
        try:
            write(path, value)
        except FileExistsError:
            if read(path) != value:
                raise WorkflowError("Concurrent feedback identity conflicts") from None
    return value, path


def archive(value, config=None):
    """Explicit archive only; migration and execution are separate user operations."""
    from ..tracing.config import load_config
    from ..tracing.store import open_store

    with open_store(load_config(config)) as store:
        return store.register_feedback(value)
