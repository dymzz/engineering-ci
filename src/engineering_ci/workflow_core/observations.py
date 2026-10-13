"""Private attempt observations; failures never replace the original tool outcome."""

from time import time_ns
from uuid import uuid4

from .documents import WorkflowError, digest, read, write
from .schemas import OBSERVATION, validate


class ObservationJournal:
    def __init__(self, context):
        self.context = context
        self.session_id = uuid4().hex
        self.sequence = 0
        self.errors = 0

    def record(self, event, *, step=None, item=None, spec=None, **facts):
        """Save only identities and execution facts, never parameters or diagnostics."""
        self.sequence += 1
        value = {
            "schema_version": 1,
            "kind": "workflow-observation",
            "execution_id": self.context.execution_id,
            "generation_id": self.context.generation_id,
            "definition_id": self.context.definition_id,
            "session_id": self.session_id,
            "sequence": self.sequence,
            "observed_ns": time_ns(),
            "event": event,
            **facts,
        }
        if step is not None:
            value.update(
                step_id=step["id"],
                tool_id=step["tool"],
                contract_sha256=spec.sha256,
                attempt=item["attempt"],
            )
            for key in ("input_sha256", "output_sha256"):
                if key in item:
                    value[key] = item[key]
            if item.get("result"):
                result = item["result"]
                value["result_sha256"] = digest(result)
                value["result"] = {
                    key: result[key]
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
            if item.get("previous"):
                value["previous_result_sha256"] = digest(item["previous"])
        try:
            validate(value, OBSERVATION, label="operation observation")
            write(
                self.context.run_dir
                / "observations"
                / self.session_id
                / f"{self.sequence:08d}.json",
                value,
            )
        except (OSError, ValueError):
            self.errors += 1

    def finish(self, state):
        """Record the observed stopping point separately from workflow success."""
        self.record(
            "session_finished",
            phase=state["phase"],
            status=state["status"],
            exit_code=state["exit_code"],
            missing_records=self.errors,
        )


def history(directory, state):
    """Read bounded ordered sessions; missing observations are never completion."""
    base = directory / "observations"
    events, sessions, diagnostics = [], [], []
    if not base.exists():
        expected = state.get("observation_protocol") and any(
            item["attempt"] for item in state["steps"].values()
        )
        return (
            [],
            "incomplete" if expected else "unknown",
            ["No operation observations are available"],
        )
    if base.is_symlink():
        raise WorkflowError("Observation directory cannot be a symlink")
    paths = sorted(base.glob("*/*.json"))
    if (
        len(paths) > 10000
        or sum(path.stat().st_size for path in paths) > 16 * 1024 * 1024
    ):
        raise WorkflowError("Observation history exceeds 10000 records or 16 MiB")
    for path in paths:
        if path.is_symlink() or path.parent.is_symlink():
            raise WorkflowError("Observation records cannot be symlinks")
        value = read(path)
        validate(value, OBSERVATION, label="operation observation")
        if (
            any(
                value[key] != state[key]
                for key in ("execution_id", "generation_id", "definition_id")
            )
            or path.parent.name != value["session_id"]
            or path.name != f"{value['sequence']:08d}.json"
        ):
            raise WorkflowError("Observation identity differs from its execution")
        events.append(value)
    for session in sorted({event["session_id"] for event in events}):
        items = [event for event in events if event["session_id"] == session]
        sequences = [event["sequence"] for event in items]
        complete = (
            items[0]["event"] == "session_started"
            and items[-1]["event"] == "session_finished"
            and sequences == list(range(1, sequences[-1] + 1))
            and items[-1].get("missing_records") == 0
        )
        sessions.append(complete)
    if not events or not all(sessions):
        diagnostics.append(
            "An observed session is missing records or its final boundary"
        )
    if state.get("observation_protocol"):
        for step, item in state["steps"].items():
            admitted = {
                event["attempt"]
                for event in events
                if event["event"] == "admitted" and event["step_id"] == step
            }
            if admitted != set(range(1, item["attempt"] + 1)):
                diagnostics.append("An expected attempt admission is missing")
                break
    else:
        diagnostics.append(
            "Legacy checkpoint cannot prove pre-observation history coverage"
        )
        return events, "unknown", diagnostics
    return (
        events,
        "complete" if events and not diagnostics else "incomplete",
        diagnostics,
    )
