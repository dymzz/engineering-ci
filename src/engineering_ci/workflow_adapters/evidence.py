"""Join an explicit generation, attempt history and original check resources."""

from contextlib import ExitStack
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


def check_details(entry):
    """Expose allowlisted original facts; paths, commands and environment values stay private."""
    identity = entry.get("identity") or {}
    if not isinstance(identity, dict):
        raise WorkflowError("Original check identity is malformed")
    parameters = identity.get("parameters", {})
    tools = identity.get("tools", {})
    environment = identity.get("environment", {})
    executor = identity.get("executor", {})
    if not all(
        isinstance(value, dict) for value in (parameters, tools, environment, executor)
    ):
        raise WorkflowError("Original check identity fields are malformed")
    allowed_tools = {
        "bootstrap_python",
        "python",
        "pytest",
        "pytest-cov",
        "pyyaml",
        "ruff",
    }
    for spec in checks.PIPELINE["checks"].values():
        allowed_tools.update(spec["required_tools"] + spec["linux_tools"])
    allowed_tools.update(
        spec["tool"] for spec in checks.PIPELINE["optional_backends"].values()
    )
    versions = {}
    for name in sorted(allowed_tools & tools.keys()):
        if not isinstance(tools[name], dict):
            continue
        value = tools[name].get("version")
        if (
            isinstance(value, str)
            and len(value) <= 64
            and checks.VERSION.fullmatch(value)
        ):
            versions[name] = value
    safe_parameters = {}
    if type(parameters.get("coverage")) is bool:
        safe_parameters["coverage"] = parameters["coverage"]
    junit = parameters.get("junit")
    if isinstance(junit, str):
        from .checks import INPUT

        try:
            validate({"junit": junit}, INPUT, label="observed check parameter")
            safe_parameters["junit"] = junit
        except WorkflowError:
            pass
    return {
        "parameters": safe_parameters,
        "tool_versions": versions,
        "environment": {
            "platform": environment.get("platform")
            if environment.get("platform") in {"Linux", "Windows", "Darwin"}
            else None,
            "machine": environment.get("machine")
            if environment.get("machine")
            in {"AMD64", "x86_64", "arm64", "aarch64", "x86", "i686"}
            else None,
            "backend": executor.get("backend")
            if executor.get("backend") in {"dagger", "native"}
            else None,
        },
        "counts": {
            key: value
            for key, value in (entry.get("counts") or {}).items()
            if key in {"tests", "failures", "errors", "skipped", "passed"}
            and type(value) is int
            and value >= 0
        },
    }


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
            "details": check_details(entry),
            "recorded_gate": {
                "status": entry["status"],
                "exit_code": entry["exit_code"],
                "executed": entry.get("executed") is True,
                "input_stable": entry.get("input_stable") is True,
                "passed_for_recorded_input": entry.get("passed_for_current_input")
                is True,
                "pytest_complete": entry.get("completeness") == "complete"
                if name == "pytest"
                else None,
            },
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
    view = execution_evidence(directory, catalog, root)
    query = Query(
        "evidence",
        limit=limit,
        max_bytes=budget,
        after=after,
        candidate=(view["metadata"]["source"] or {}).get("candidate_id"),
    )
    entries = after_items(query, view["scope"], view["items"], lambda row: row["key"])
    result = page(
        query,
        view["scope"],
        entries,
        key=lambda row: row["key"],
        remaining=len(entries),
        metadata=view["metadata"],
    )
    result["operation"] = "workflow.evidence"
    return result


def execution_evidence(directory, catalog, root):
    """Join one bounded execution under its lock, also for offline event-log export."""
    directory = Path(directory).resolve()
    try:
        with FileLock(directory / "execution.lock", timeout=0):
            return _snapshot(directory, catalog, root)
    except Timeout:
        raise WorkflowError(
            "Execution is busy; use workflow status or query evidence after it stops"
        ) from None


def execution_pair(baseline, candidate, catalog, root):
    """Hold both original execution locks in stable order while joining comparison inputs."""
    directories = [Path(path).resolve() for path in (baseline, candidate)]
    try:
        with ExitStack() as stack:
            for directory in sorted(set(directories)):
                stack.enter_context(FileLock(directory / "execution.lock", timeout=0))
            views = tuple(
                _snapshot(directory, catalog, root) for directory in directories
            )
            for view in views:
                for row in view["items"]:
                    row["check"]["recorded_gate"] = view["recorded_gates"][row["key"]]
            return views
    except Timeout:
        raise WorkflowError(
            "Comparison input is busy; query status after it stops"
        ) from None


def _snapshot(directory, catalog, root):
    """Join a stable checkpoint while the existing execution lock is held."""
    state = Runtime(catalog).status(directory)
    workflow, provenance, plan = load_generation(directory / "workflow.json", catalog)
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
    entries, recorded_gates = [], {}
    for (step, attempt), row in sorted(rows.items()):
        seen = row.pop("events")
        start, returned = seen.get("invoking"), seen.get("returned")
        if (
            start
            and returned
            and any(
                start.get(key) != returned.get(key)
                for key in ("batch_id", "batch_size")
            )
        ):
            raise WorkflowError("Observed return names a different adapter batch")
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
            session_id=(start or seen.get("admitted") or returned)["session_id"],
            batch={"id": start["batch_id"], "size": start["batch_size"]}
            if start and "batch_id" in start
            else None,
            time_scope="adapter_invocation",
            contract_version=catalog.resolve(row["tool_id"])[0].contract_version,
            contract_sha256=definitions[step]["contract_sha256"],
            registered_check=row["tool_id"] in resources.names,
            check=check_facts(directory, returned, resources)
            if returned
            else {"status": "unavailable", "actual_check_executed": None},
            reuse_source=None,
        )
        recorded_gates[row["key"]] = row["check"].pop("recorded_gate", None)
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
    scope = {
        "execution_id": state["execution_id"],
        "state_sha256": digest(state),
        "observations_sha256": digest(events),
        "provenance_sha256": digest(provenance),
        "evidence_sha256": digest(entries),
    }
    return {
        "scope": scope,
        "items": entries,
        "dependencies": plan["dependencies"],
        "workflow": workflow,
        "recorded_gates": recorded_gates,
        "metadata": {
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
            "reuse_source": {
                "execution_id": prior["execution_id"],
                "generation_id": prior["generation_id"],
                "definition_id": prior["definition_id"],
                "state_sha256": digest(prior),
            }
            if prior
            else None,
            "approved": False,
            "optimization_verified": False,
            "llm_used": False,
            "next_actions": [["engineering-ci", "workflow", "status", str(directory)]],
        },
    }
