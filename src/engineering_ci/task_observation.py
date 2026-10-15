"""Capture host execution facts; installed vesryn-log owns physical log writes."""

import importlib.util
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from threading import RLock
from time import time_ns
from uuid import uuid4

from ._paths import ROOT

_LOCK = RLock()
_ACTIVE = None
_MAX_BYTES = 1800 * 1024


def _inside(root, path):
    path = path.resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("Observation delivery must remain inside the project")
    return path


def _name(args, kwargs):
    command = args[0] if args else kwargs.get("args")
    first = command[0] if isinstance(command, (list, tuple)) and command else None
    if not isinstance(first, (str, Path)):
        return "native-process"
    name = str(first).replace("\\", "/").rsplit("/", 1)[-1]
    return name if re.fullmatch(r"[\w.+-]{1,120}", name) else "native-process"


class Capture:
    """Capture safe process facts in memory, then record through vesryn-log.

    Only this Python process and its worker threads are covered. Child internals,
    other terminals, argv, output, environment and exception text are not captured.
    """

    def __init__(self, root, purpose):
        self.root = Path(root).resolve()
        self.task_id = uuid4().hex
        now = time_ns()
        self.context = {
            "schema_version": 1,
            "kind": "task-observation-context",
            "task_id": self.task_id,
            "goal": purpose,
            "started_ns": now,
            "capture_scope": "python-subprocess-and-linked-tools/1",
        }
        self.relative = (
            f"tasks/{datetime.fromtimestamp(now / 1e9, UTC):%Y-%m-%d}/{self.task_id}"
        )
        self.events, self.calls, self.methods = [], {}, {}
        self.errors = self.sequence = self.size = 0
        self.saturated = False
        self.status, self.exit_code, self.closed = "returned", None, False
        self.lock = RLock()
        self.receipt = {"task_id": self.task_id, "observation_delivery": "unavailable"}

    def event(self, event, call=None):
        self.sequence += 1
        value = {
            "schema_version": 1,
            "kind": "task-operation-observation",
            "task_id": self.task_id,
            "sequence": self.sequence,
            "observed_ns": time_ns(),
            "event": event,
        }
        if call is not None:
            value["call"] = dict(call)
        size = len(json.dumps(value).encode("utf-8"))
        if len(self.events) >= 9999 or self.size + size > _MAX_BYTES:
            self.errors += 1
            self.saturated = True
            return
        self.size += size
        self.events.append(value)

    def begin(self, name):
        with self.lock:
            if self.closed:
                return None
            call = {
                "call_id": uuid4().hex,
                "parent_id": None,
                "category": "process",
                "name": name,
                "purpose": None,
                "backend": "dagger"
                if name.casefold() in {"dagger", "dagger.exe"}
                else "host",
                "started_ns": time_ns(),
                "finished_ns": None,
                "status": "requested",
                "exit_code": None,
                "pid": None,
                "execution_id": None,
                "tool_id": None,
                "contract_sha256": None,
                "summary": None,
            }
            # Stop retaining new calls after the bounded envelope is full.
            if self.saturated or self.size >= _MAX_BYTES or len(self.calls) >= 3000:
                self.errors += 1
                return None
            self.calls[call["call_id"]] = call
            self.event("requested", call)
            return call

    def update(self, call, *, done=False, **facts):
        with self.lock:
            if self.closed or call["finished_ns"] is not None:
                return
            call.update(facts)
            if done:
                call["finished_ns"] = time_ns()
            self.event("returned" if done else "started", call)

    def __enter__(self):
        global _ACTIVE
        with _LOCK:
            if _ACTIVE is not None:
                raise ValueError("A task already owns this process")
            _ACTIVE = self
            self.event("task_started")
            for name in ("__init__", "wait", "poll", "communicate"):
                original = getattr(subprocess.Popen, name)
                wrapped = self._wrap(original, launch=name == "__init__")
                self.methods[name] = original, wrapped
                setattr(subprocess.Popen, name, wrapped)
        return self

    def _wrap(self, original, *, launch):
        @wraps(original)
        def wrapped(process, *args, **kwargs):
            if launch:
                call = self.begin(_name(args, kwargs))
                try:
                    result = original(process, *args, **kwargs)
                except BaseException as error:
                    if call:
                        self.update(
                            call,
                            done=True,
                            status="launch_failed"
                            if isinstance(error, (OSError, ValueError, TypeError))
                            else "unknown",
                        )
                    raise
                if call:
                    process._engineering_ci_capture = self, call
                    self.update(call, status="running", pid=process.pid)
                return result
            try:
                return original(process, *args, **kwargs)
            finally:
                observed = getattr(process, "_engineering_ci_capture", None)
                if observed and getattr(process, "returncode", None) is not None:
                    owner, call = observed
                    owner.update(
                        call, done=True, status="exited", exit_code=process.returncode
                    )

        return wrapped

    def __exit__(self, error_type, error, traceback):
        global _ACTIVE
        with _LOCK, self.lock:
            for name, (original, wrapped) in self.methods.items():
                if getattr(subprocess.Popen, name) is wrapped:
                    setattr(subprocess.Popen, name, original)
            _ACTIVE = None
            if error_type is not None:
                self.status = (
                    "interrupted"
                    if issubclass(error_type, (KeyboardInterrupt, SystemExit))
                    else "raised"
                )
                self.exit_code = (
                    error.code
                    if isinstance(error, SystemExit) and type(error.code) is int
                    else None
                )
            self.event("task_finished")
            self.closed = True
        try:
            deliver(self)
        except (ImportError, OSError, ValueError, KeyError, TypeError):
            # Recording diagnostics cannot leak content or mask task facts.
            self.receipt.update(
                observation_delivery="unavailable",
                failure_reason="log_recording_unavailable",
            )
        try:
            persist_delivery(self)
        except (ImportError, OSError, ValueError):
            self.receipt["delivery_receipt_status"] = "unavailable"
        try:
            print(
                f"Task observation {self.receipt['observation_delivery']}: {self.task_id}",
                file=sys.stderr,
            )
        except (OSError, ValueError):
            self.receipt["diagnostic_delivery"] = "unavailable"
        return False

    def envelope(self):
        return {
            "schema_version": 1,
            "kind": "task-observation-capture",
            "context": self.context,
            "events": self.events,
            "observation_errors": self.errors,
            "status": self.status,
            "exit_code": self.exit_code,
        }


def persist_delivery(task):
    """Keep recording status separate from the original task result."""
    from vesryn_log import JsonFileSink

    folder = _inside(task.root, task.root / ".log" / task.relative)
    JsonFileSink(_inside(task.root, folder / "delivery.json")).emit(
        {
            **task.receipt,
            "task_goal": task.context["goal"],
            "task_status": task.status,
            "task_exit_code": task.exit_code,
        }
    )


def deliver(task):
    """Record this invocation locally; logging never launches another tool or Runtime."""
    from vesryn_log import JsonFileSink, JsonlSink

    target = _inside(task.root, task.root / ".log" / task.relative)
    if target.exists():
        raise ValueError("Task log identity already exists")
    JsonFileSink(_inside(task.root, target / "capture.json")).emit(task.envelope())
    journal = JsonlSink(_inside(task.root, target / "events.jsonl"))
    for event in task.events:
        journal.emit(event)
    JsonFileSink(
        _inside(task.root, task.root / ".log/latest-task.json"), replace=True
    ).emit(
        {
            "schema_version": 1,
            "kind": "task-latest",
            "task_id": task.task_id,
            "run_path": task.relative,
        }
    )
    task.receipt.update(
        observation_delivery="available", directory=str(target), recorder="vesryn-log"
    )


def task_entry(purpose, *, operations=(), read_only=(), prerequisites=()):
    """Wrap a task dispatcher once; queries/help retain their read-only behavior."""

    def decorate(function):
        @wraps(function)
        def wrapped(argv=None, *args, **kwargs):
            values = list(sys.argv[1:] if argv is None else argv)
            if any(importlib.util.find_spec(name) is None for name in prerequisites):
                return function(argv, *args, **kwargs)
            if (
                _ACTIVE is not None
                or not values
                or any(value in {"-h", "--help"} for value in values)
                or values[0] in read_only
                or (operations and values[0] not in operations)
            ):
                return function(argv, *args, **kwargs)
            with Capture(ROOT, purpose) as task:
                result = function(argv, *args, **kwargs)
                if type(result) is int:
                    task.exit_code = result
                return result

        return wrapped

    return decorate
