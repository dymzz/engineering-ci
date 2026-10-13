"""Persistent finite workflow execution; domain adapters retain their recovery rules."""

import copy
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4

from filelock import FileLock, Timeout
from transitions import Machine

from .contracts import ExecutionContext, Invocation, ToolResult
from .definition import load_generation, resolve_inputs
from .documents import WorkflowError, canonical, digest, read, write
from .observations import ObservationJournal
from .schemas import EXECUTION, RESULT, validate

OUTCOMES = {"passed", "failed", "incomplete", "unavailable", "interrupted", "unknown"}


class ExecutionFSM:
    def __init__(self, state, context):
        self.context = context
        Machine(
            model=self,
            states=["READY", "RUNNING", "PAUSED", "UNKNOWN", "FINISHED"],
            initial=state,
            auto_transitions=False,
            transitions=[
                {
                    "trigger": "begin",
                    "source": ["READY", "PAUSED", "FINISHED"],
                    "dest": "RUNNING",
                },
                {"trigger": "pause", "source": "RUNNING", "dest": "PAUSED"},
                {"trigger": "uncertain", "source": "RUNNING", "dest": "UNKNOWN"},
                {"trigger": "finish", "source": "RUNNING", "dest": "FINISHED"},
            ],
        )


def checked_result(result, spec):
    """Keep execution failure authoritative even when output validation also fails."""
    if not isinstance(result, ToolResult) or result.status not in OUTCOMES:
        raise WorkflowError("Adapter returned an unsupported result")
    validate(result.document(), RESULT, label="tool result")
    canonical(result.document())
    if type(result.exit_code) not in (int, type(None)):
        raise WorkflowError("Adapter returned an invalid exit status")
    if any(
        type(getattr(result, name)) is not bool
        for name in ("executed", "complete", "current", "reused", "report_generated")
    ):
        raise WorkflowError("Adapter returned invalid execution facts")
    validate(result.outputs, spec.output_schema, label="tool output")
    if result.status == "passed" and not (
        result.exit_code == 0 and result.executed and result.complete and result.current
    ):
        raise WorkflowError("Passed result lacks current complete execution evidence")
    if result.status == "failed" and not result.exit_code:
        raise WorkflowError("Failed result lacks a nonzero exit status")
    return result.document()


class Runtime:
    def __init__(self, catalog):
        self.catalog = catalog
        self.observation_errors = 0

    def create(self, workflow, destination, *, reuse_from=None):
        value, provenance, _ = load_generation(workflow, self.catalog)
        previous = {}
        if reuse_from:
            saved = self.status(reuse_from)
            if saved["definition_id"] != value["definition_id"]:
                raise WorkflowError(
                    "Reuse requires the same semantic workflow definition"
                )
            previous = saved["steps"]
        execution_id = uuid4().hex
        directory = Path(destination).resolve() / execution_id
        directory.mkdir(parents=True, exist_ok=False)
        write(directory / "workflow.json", value)
        write(directory / "provenance.json", provenance)
        state = {
            "schema_version": 1,
            "kind": "workflow-execution",
            "observation_protocol": "workflow-observation/1",
            "execution_id": execution_id,
            "generation_id": value["generation_id"],
            "definition_id": value["definition_id"],
            "phase": "READY",
            "status": "not_run",
            "exit_code": 3,
            "steps": {
                step["id"]: {
                    "status": "pending",
                    "attempt": 0,
                    "previous": previous.get(step["id"], {}).get("result"),
                }
                for step in value["steps"]
            },
        }
        if reuse_from:
            write(directory / "reuse-source.json", saved)
            state["reuse_source_sha256"] = digest(saved)
        write(directory / "state.json", state)
        return directory

    def status(self, directory):
        """Read recorded facts only; this does not claim that inputs remain current."""
        directory = Path(directory).resolve()
        value, _, _ = load_generation(directory / "workflow.json", self.catalog)
        state = read(directory / "state.json")
        validate(state, EXECUTION, label="execution checkpoint")
        if (
            state.get("schema_version") != 1
            or state.get("kind") != "workflow-execution"
            or state.get("execution_id") != directory.name
            or any(
                state.get(key) != value[key]
                for key in ("generation_id", "definition_id")
            )
            or state.get("phase")
            not in {"READY", "RUNNING", "PAUSED", "UNKNOWN", "FINISHED"}
            or set(state.get("steps", {})) != {step["id"] for step in value["steps"]}
        ):
            raise WorkflowError("Invalid execution checkpoint identity")
        for item in state["steps"].values():
            if (
                not isinstance(item, dict)
                or type(item.get("attempt")) is not int
                or item["attempt"] < 0
                or item.get("status")
                not in {"pending", "running", "skipped", "blocked", *OUTCOMES}
            ):
                raise WorkflowError("Invalid step checkpoint")
        return state

    def resume(self, directory):
        """Resume the same execution under a lock, rechecking previous tool evidence."""
        directory = Path(directory).resolve()
        try:
            with FileLock(directory / "execution.lock", timeout=0):
                return self._execute(directory)
        except Timeout:
            raise WorkflowError(
                "Execution is already owned by another process"
            ) from None

    def _execute(self, directory):
        state = self.status(directory)
        value, _, plan = load_generation(directory / "workflow.json", self.catalog)
        definitions = {step["id"]: step for step in value["steps"]}
        predecessor_view = {}
        context = ExecutionContext(
            state["execution_id"],
            state["generation_id"],
            state["definition_id"],
            directory,
            MappingProxyType(predecessor_view),
        )
        fsm = ExecutionFSM(state["phase"], context)
        journal = ObservationJournal(context)
        self.observation_errors = 0
        journal.record("session_started", phase=fsm.state, status=state["status"])

        def observe(event, key, **facts):
            journal.record(
                event,
                step=definitions[key],
                item=state["steps"][key],
                spec=self.catalog.resolve(definitions[key]["tool"])[0],
                **facts,
            )

        def finished():
            journal.finish(state)
            self.observation_errors = journal.errors
            return state

        def save():
            state["phase"] = fsm.state
            validate(state, EXECUTION, label="execution checkpoint")
            write(directory / "state.json", state, replace=True)
            write(directory / "result.json", state, replace=True)

        if fsm.state == "UNKNOWN" or any(
            item["status"] == "running"
            and self.catalog.resolve(definitions[key]["tool"])[0].recovery == "manual"
            for key, item in state["steps"].items()
        ):
            if fsm.state != "UNKNOWN":
                if fsm.state != "RUNNING":
                    fsm.begin()
                fsm.uncertain()
            state.update(status="unknown", exit_code=3)
            save()
            return finished()
        if fsm.state == "RUNNING":
            fsm.pause()
        # All earlier outcomes are offered to adapters, never blindly trusted by the core.
        old = {
            key: item.get("result", item.get("previous"))
            for key, item in state["steps"].items()
        }
        for key, item in state["steps"].items():
            if item["status"] != "pending":
                journal.record(
                    "recovered",
                    step=definitions[key],
                    item=item,
                    spec=self.catalog.resolve(definitions[key]["tool"])[0],
                    prior_status=item["status"],
                )
            item["previous"] = old[key]
            item["status"] = "pending"
            for field in ("result", "reason", "input_sha256", "output_sha256"):
                item.pop(field, None)
        fsm.begin()
        state.update(status="running", exit_code=3)
        save()
        pending = set(definitions)
        while pending:
            ready = [
                key
                for key in plan["order"]
                if key in pending and not (set(plan["dependencies"][key]) & pending)
            ]
            groups = {}
            for key in ready:
                step, item = definitions[key], state["steps"][key]
                predecessors = [
                    state["steps"][dep] for dep in plan["dependencies"][key]
                ]
                successful = all(
                    previous["status"] == "passed" for previous in predecessors
                )
                failed = any(
                    previous["status"] != "passed" for previous in predecessors
                )
                admitted = (
                    step["when"] == "always"
                    or (step["when"] == "success" and successful)
                    or (step["when"] == "failure" and failed)
                )
                if not admitted:
                    item.update(
                        status="skipped", reason="Predecessor condition did not hold"
                    )
                    pending.remove(key)
                    observe("skipped", key)
                    continue
                spec, adapter = self.catalog.resolve(step["tool"])
                try:
                    outputs = {
                        dep: state["steps"][dep].get("result", {}).get("outputs", {})
                        for dep in plan["dependencies"][key]
                    }
                    inputs = resolve_inputs(step["inputs"], outputs)
                    validate(inputs, spec.input_schema, label="resolved tool input")
                except WorkflowError:
                    item.update(
                        status="blocked",
                        reason="Required predecessor output is unavailable or invalid",
                    )
                    pending.remove(key)
                    observe("blocked", key)
                    continue
                if spec.recovery == "manual" and old[key] is not None:
                    previous = old[key]
                    item.update(status=previous["status"], result=previous)
                    code = previous.get("exit_code") or 3
                    state.update(
                        status="blocked",
                        exit_code=code if code >= 0 else 128 - code,
                        decision_required="Known write outcome retained; domain verification is required before continuation",
                    )
                    fsm.pause()
                    save()
                    observe("blocked", key)
                    return finished()
                item.update(
                    status="running",
                    attempt=item["attempt"] + 1,
                    input_sha256=digest(inputs),
                )
                groups.setdefault(id(adapter), (adapter, []))[1].append(
                    Invocation(key, step["tool"], inputs, item["attempt"], old[key])
                )
            save()  # Intent for every admitted call is durable before invoking a tool.
            for _, calls in groups.values():
                for call in calls:
                    observe("admitted", call.step_id)
            for adapter, calls in groups.values():
                predecessor_view.clear()
                predecessor_view.update(
                    copy.deepcopy(
                        {
                            dep: state["steps"][dep]
                            for call in calls
                            for dep in plan["dependencies"][call.step_id]
                        }
                    )
                )
                interrupted = False
                batch = {"batch_id": uuid4().hex, "batch_size": len(calls)}
                for call in calls:
                    observe("invoking", call.step_id, **batch)
                try:
                    results = adapter.invoke(tuple(calls), context)
                    if set(results) != {call.step_id for call in calls}:
                        raise WorkflowError("Adapter batch omitted a requested result")
                except KeyboardInterrupt:
                    results, interrupted = {}, True
                except Exception:  # noqa: BLE001 -- preserve UNKNOWN on arbitrary adapter/SDK failure
                    # SDK/subprocess errors may contain credentials; never persist their text.
                    results = {}
                for call in calls:
                    spec, _ = self.catalog.resolve(call.tool_id)
                    original = results.get(call.step_id)
                    try:
                        if original is None:
                            raise WorkflowError(
                                "Tool invocation did not return a result"
                            )
                        result = checked_result(original, spec)
                    except WorkflowError:
                        uncertain = spec.recovery == "manual"
                        failure = (
                            isinstance(original, ToolResult)
                            and original.status == "failed"
                            and type(original.exit_code) is int
                            and original.exit_code
                        )
                        result = ToolResult(
                            "unknown"
                            if uncertain
                            else (
                                "failed"
                                if failure
                                else ("interrupted" if interrupted else "unavailable")
                            ),
                            (
                                original.exit_code
                                if isinstance(original, ToolResult)
                                and type(original.exit_code) is int
                                and original.exit_code
                                else (130 if interrupted else 3)
                            ),
                            reason="Tool result unavailable or invalid; inspect original tool evidence",
                        ).document()
                    state["steps"][call.step_id].update(
                        status=result["status"],
                        result=result,
                        output_sha256=digest(result["outputs"]),
                    )
                    pending.remove(call.step_id)
                save()
                for call in calls:
                    observe("returned", call.step_id, **batch)
                if any(
                    state["steps"][call.step_id]["status"] == "unknown"
                    for call in calls
                ):
                    fsm.uncertain()
                    state.update(status="unknown", exit_code=3)
                    save()
                    return finished()
                if interrupted or any(
                    state["steps"][call.step_id]["status"] == "interrupted"
                    for call in calls
                ):
                    fsm.pause()
                    state.update(status="interrupted", exit_code=130)
                    save()
                    return finished()
        bad = [
            item
            for key, item in state["steps"].items()
            if item["status"] != "passed"
            and (definitions[key]["required"] or item["status"] == "failed")
        ]
        actual_failures = [item for item in bad if item["status"] == "failed"]
        code = next(
            (
                item.get("result", {}).get("exit_code")
                for item in (actual_failures or bad)
                if item.get("result", {}).get("exit_code")
            ),
            3 if bad else 0,
        )
        state.update(
            status="failed" if bad else "passed",
            exit_code=code if code >= 0 else 128 - code,
        )
        fsm.finish()
        save()
        return finished()
