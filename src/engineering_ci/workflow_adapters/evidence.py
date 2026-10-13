"""Join an explicit generation, attempt history and original check resources."""

from pathlib import Path

from filelock import FileLock, Timeout

from .. import checks
from ..tracing.retrieval import Query, after_items, page, page_bounds
from ..workflow_core import Runtime, load_generation
from ..workflow_core.documents import WorkflowError, digest, read
from ..workflow_core.observations import history
from ..workflow_core.schemas import EXECUTION, validate
from .candidates import verify_target
from .checks import ChecksAdapter


def check_facts(directory, event, resources):
    """Validate the original attempt receipt and return only content-free check facts."""
    if event["tool_id"] not in resources.names or event["event"] != "returned":
        return {"status": "unavailable", "actual_check_executed": None}
    try:
        path = (
            directory
            / "tool-results"
            / event["step_id"]
            / str(event["attempt"])
            / "result.json"
        )
        resources.resource(path)
        receipt = read(path)
        if digest(receipt) != event["result_sha256"]:
            raise WorkflowError("Attempt receipt identity differs")
        output = receipt["outputs"]
        name = resources.names[event["tool_id"]]
        if output["name"] != name:
            raise WorkflowError("Attempt receipt names a different check")
        for resource in [output["report"], *output["evidence"]]:
            if resources.resource(resources.root / resource["path"]) != resource:
                raise WorkflowError("Original check evidence changed")
        report = checks.read_report(resources.root / output["report"]["path"])
        entry = next(item for item in report["checks"] if item["name"] == name)
        if (
            entry["exit_code"] != receipt["exit_code"]
            or entry["status"] != output["check_status"]
        ):
            raise WorkflowError("Original check result differs from its receipt")
        identity = entry.get("identity")
        return {
            "status": "verified",
            "report": output["report"],
            "resources": output["evidence"],
            "actual_check_executed": entry.get("executed_this_invocation")
            if type(entry.get("executed_this_invocation")) is bool
            else None,
            "evidence_reused": entry.get("reused") is True,
            "identity_sha256": digest(identity) if identity is not None else None,
            "identity_parts": {
                key: digest(identity[key])
                for key in ("inputs", "tools", "environment", "parameters", "executor")
                if identity is not None and key in identity
            },
            "current_input_validity": "not_checked",
            "dagger_cache_hit": None,
        }
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        return {
            "status": "unavailable",
            "actual_check_executed": None,
            "reason": "Original attempt evidence is unavailable or changed",
        }


def evidence(directory, catalog, root, *, limit=20, budget=16384, after=None):
    """Read one named execution; do not mine, run tools or infer an approval."""
    query = Query("evidence", limit=limit, max_bytes=budget, after=after)
    page_bounds(query)
    directory = Path(directory).resolve()
    try:
        with FileLock(directory / "execution.lock", timeout=0):
            return _evidence(
                directory, catalog, root, limit=limit, budget=budget, after=after
            )
    except Timeout:
        raise WorkflowError(
            "Execution is busy; use workflow status or query evidence after it stops"
        ) from None


def _evidence(directory, catalog, root, *, limit, budget, after):
    """Join a stable checkpoint while the existing execution lock is held."""
    state = Runtime(catalog).status(directory)
    workflow, provenance, _ = load_generation(directory / "workflow.json", catalog)
    definitions = {step["id"]: step for step in workflow["steps"]}
    events, completeness, diagnostics = history(directory, state)
    try:
        target_status = verify_target(workflow, provenance)
    except WorkflowError:
        target_status = "stale"
    rows = {}
    for event in events:
        if "step_id" not in event:
            continue
        step = definitions.get(event["step_id"])
        if (
            step is None
            or step["tool"] != event["tool_id"]
            or step["contract_sha256"] != event["contract_sha256"]
        ):
            raise WorkflowError("Observed tool differs from the generated definition")
        if event["event"] not in {"admitted", "invoking", "returned"}:
            continue
        key = (event["step_id"], event["attempt"])
        row = rows.setdefault(
            key,
            {
                "step_id": key[0],
                "tool_id": event["tool_id"],
                "attempt": key[1],
                "events": {},
            },
        )
        if event["event"] in row["events"]:
            raise WorkflowError("Invocation identity has duplicate observation events")
        row["events"][event["event"]] = event
    resources = ChecksAdapter(root)
    prior = None
    if "reuse_source_sha256" in state:
        prior = read(directory / "reuse-source.json")
        validate(prior, EXECUTION, label="reuse source")
        if (
            digest(prior) != state["reuse_source_sha256"]
            or prior["definition_id"] != state["definition_id"]
        ):
            raise WorkflowError(
                "Reuse source checkpoint changed or names another definition"
            )
    entries = []
    for (step, attempt), row in sorted(rows.items()):
        seen = row.pop("events")
        start, returned = seen.get("invoking"), seen.get("returned")
        row.update(
            key=f"{step}:{attempt:08d}",
            invocation_started=True if start is not None else None,
            observation_status="complete"
            if {"admitted", "invoking", "returned"} <= seen.keys()
            else "incomplete",
            result=returned.get("result") if returned else None,
            input_sha256=seen.get("admitted", {}).get("input_sha256"),
            result_sha256=returned.get("result_sha256") if returned else None,
            started_ns=start["observed_ns"] if start else None,
            ended_ns=returned["observed_ns"] if returned else None,
            check=check_facts(directory, returned, resources)
            if returned
            else {"status": "unavailable", "actual_check_executed": None},
            reuse_source=None,
        )
        previous_hash = seen.get("admitted", {}).get("previous_result_sha256")
        reused = (
            returned
            and returned["result"]["reused"]
            and row["check"].get("evidence_reused")
        )
        if reused and previous_hash:
            local = next(
                (
                    event
                    for event in events
                    if event.get("step_id") == step
                    and event["event"] == "returned"
                    and event["attempt"] < attempt
                    and event["result_sha256"] == previous_hash
                ),
                None,
            )
            if local:
                row["reuse_source"] = {
                    "execution_id": state["execution_id"],
                    "generation_id": state["generation_id"],
                    "definition_id": state["definition_id"],
                    "step_id": step,
                    "attempt": local["attempt"],
                    "result_sha256": previous_hash,
                }
        if reused and prior and previous_hash and row["reuse_source"] is None:
            previous = prior["steps"].get(step, {})
            if previous.get("result") and digest(previous["result"]) == previous_hash:
                row["reuse_source"] = {
                    "execution_id": prior["execution_id"],
                    "generation_id": prior["generation_id"],
                    "definition_id": prior["definition_id"],
                    "step_id": step,
                    "attempt": previous["attempt"],
                    "result_sha256": previous_hash,
                }
        entries.append(row)
    query = Query(
        "evidence",
        limit=limit,
        max_bytes=budget,
        after=after,
        candidate=(provenance["source"] or {}).get("candidate_id"),
    )
    scope = {
        "execution_id": state["execution_id"],
        "state_sha256": digest(state),
        "observations_sha256": digest(events),
    }
    entries = after_items(query, scope, entries, lambda row: row["key"])
    result = page(
        query,
        scope,
        entries,
        key=lambda row: row["key"],
        remaining=len(entries),
        metadata={
            "operation": "workflow.evidence",
            "execution_id": state["execution_id"],
            "execution": str(directory),
            "generation_id": state["generation_id"],
            "definition_id": state["definition_id"],
            "source": provenance["source"],
            "mapping_basis": provenance["mapping_basis"],
            "target": provenance.get("target"),
            "target_policy": target_status,
            "historical_check_identity": "unavailable",
            "recorded_result": {
                key: state[key] for key in ("phase", "status", "exit_code")
            },
            "current_input_validity": "not_checked",
            "observation_completeness": completeness,
            "diagnostics": diagnostics,
            "attempts": len(rows),
            "approved": False,
            "optimization_verified": False,
            "llm_used": False,
            "next_actions": [["engineering-ci", "workflow", "status", str(directory)]],
        },
    )
    result["operation"] = "workflow.evidence"
    return result
