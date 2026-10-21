"""Declared legal events; business code cannot jump between phases."""

from dataclasses import replace
from types import SimpleNamespace

from transitions import Machine, MachineError

from .models import STATES, TaskRecord, timestamp

TRANSITIONS = [
    ("validate", "INIT", "VALIDATED"),
    ("dispatch", "VALIDATED", "DISPATCHING"),
    ("uncertain", "DISPATCHING", "UNKNOWN"),
    ("reject", "DISPATCHING", "REJECTED"),
    ("attach_run", ["INIT", "VALIDATED", "DISPATCHING", "UNKNOWN"], "RUNNING"),
    ("remote_completed", "RUNNING", "COLLECTING"),
    ("pause", ["RUNNING", "COLLECTING"], "PAUSED"),
    ("resume_running", "PAUSED", "RUNNING"),
    ("resume_collecting", "PAUSED", "COLLECTING"),
    ("finish", "COLLECTING", "FINISHED"),
    ("recollect", "FINISHED", "COLLECTING"),
    ("configure", ["INIT", "VALIDATED"], "="),
    ("checkpoint", ["RUNNING", "COLLECTING", "PAUSED", "FINISHED"], "="),
]


class InvalidTransition(ValueError):
    pass


class ExecutionFSM:
    def advance(self, record: TaskRecord, event: str, **updates) -> TaskRecord:
        """Compute a validated immutable checkpoint for one legal event."""
        if "phase" in updates or "schema_version" in updates or "dispatch" in updates:
            raise InvalidTransition("State and dispatch identity cannot be overwritten")
        allowed = {
            "validate": set(),
            "dispatch": set(),
            "uncertain": {"last_error"},
            "reject": {"last_error", "http_status"},
            "attach_run": {"run_id"},
            "remote_completed": {"remote_status", "remote_conclusion"},
            "pause": {"last_error"},
            "resume_running": set(),
            "resume_collecting": set(),
            "finish": {"collection_complete", "last_error"},
            "recollect": {"collection_complete", "collection"},
            "configure": {"collection", "collection_complete"},
            "checkpoint": {"files", "collection", "collection_complete", "last_error"},
        }
        if event in allowed and set(updates) - allowed[event]:
            raise InvalidTransition(
                f"Fields cannot be changed by {event}: {set(updates) - allowed[event]}"
            )
        if (
            event.startswith("resume_")
            and event != f"resume_{(record.paused_from or '').lower()}"
        ):
            raise InvalidTransition("Resume must return to the paused phase")
        if event == "attach_run" and (
            type(updates.get("run_id")) is not int or updates["run_id"] <= 0
        ):
            raise InvalidTransition("attach_run requires a positive Run ID")
        if event == "finish" and updates.get("collection_complete") is not True:
            raise InvalidTransition("Required collection steps are incomplete")
        if event == "pause":
            updates["paused_from"] = record.phase
        elif event.startswith("resume_"):
            updates["paused_from"] = None
            updates["last_error"] = None
        if event == "dispatch":
            updates["dispatch_started_at"] = timestamp()
        model = SimpleNamespace()
        Machine(
            model=model,
            states=list(STATES),
            initial=record.phase,
            transitions=[
                {"trigger": e, "source": s, "dest": d} for e, s, d in TRANSITIONS
            ],
            auto_transitions=False,
            ignore_invalid_triggers=False,
        )
        try:
            if not hasattr(model, event) or event not in {e for e, _, _ in TRANSITIONS}:
                raise InvalidTransition(f"Undefined event: {event}")
            getattr(model, event)()
            result = replace(
                record, phase=model.state, updated_at=timestamp(), **updates
            )
            result.validate()
            return result
        except (MachineError, TypeError, ValueError) as exc:
            raise InvalidTransition(f"{record.phase} / {event}: {exc}") from exc
