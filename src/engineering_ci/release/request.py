"""One concrete release dispatch: one POST, with a narrow duplicate-request guard."""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from ..github_cli import ApiError
from . import RELEASE_CONFIG


class UncertainDispatch(RuntimeError):
    def __init__(self, reason, status=None, *, run_id=None, exit_code=3):
        self.reason, self.status = reason, status
        self.run_id, self.exit_code = run_id, exit_code
        super().__init__(reason)


class DispatchRejected(ApiError):
    """GitHub explicitly rejected this POST; correcting it permits a new request."""


def request_path(version, mode):
    return Path(".test-tools") / f"release-{mode}-v{version}.json"


def read_request(path):
    """An unreadable intent blocks another POST; no legacy state is migrated."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise TypeError
        return value
    except (OSError, ValueError, TypeError) as error:
        raise UncertainDispatch("dispatch_intent_unreadable") from error


def save_run(path, value):
    """Atomically attach the returned Run ID to this specific request."""
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def dispatch(api, identity, mode, scenario="pass", *, path=None):
    """Persist intent before sending, preserve uncertainty, never retry a POST."""
    path = path or request_path(identity["version"], mode)
    intent = {
        "repository": api.repository,
        **{key: identity[key] for key in ("version", "tag", "commit_sha", "ref")},
        "mode": mode,
        "scenario": scenario,
        "run_id": None,
    }
    if path.exists():
        old = read_request(path)
        if set(old) != set(intent) or {**old, "run_id": None} != intent:
            raise UncertainDispatch("dispatch_intent_identity_mismatch")
        if type(old["run_id"]) is int and old["run_id"] > 0:
            return old["run_id"]
        raise UncertainDispatch("dispatch_result_unknown")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(intent, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise UncertainDispatch("dispatch_already_in_progress") from error
    body = {
        "ref": identity["ref"],
        "return_run_details": True,
        "inputs": {
            "version": identity["tag"],
            "publish": str(mode == "publish").lower(),
            "pytest_scenario": scenario,
        },
    }
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                "--include",
                "--method",
                "POST",
                api.prefix
                + f"actions/workflows/{RELEASE_CONFIG['workflow']}/dispatches",
                "--input",
                "-",
            ],
            input=json.dumps(body),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=60,
        )
    except (FileNotFoundError, PermissionError):
        path.unlink()  # the process never started
        raise
    except (OSError, subprocess.TimeoutExpired, KeyboardInterrupt) as error:
        if isinstance(error, KeyboardInterrupt):
            raise
        raise UncertainDispatch("dispatch_transport_interrupted") from error
    headers, separator, content = result.stdout.replace("\r\n", "\n").partition("\n\n")
    match = re.match(r"HTTP/\S+\s+(\d{3})(?:\s|$)", headers)
    if match is None:
        match = re.search(r"\(HTTP (\d{3})\)", result.stderr)
    status = int(match[1]) if match else None
    if status is not None and 400 <= status < 500 and status != 408:
        path.unlink()
        raise DispatchRejected(status, exit_code=result.returncode or 1)
    if status == 204:
        raise UncertainDispatch("accepted_without_run_id", status)
    if status != 200 or not separator:
        raise UncertainDispatch("dispatch_response_unavailable", status)
    try:
        value = json.loads(content)
        run_id = value.get("workflow_run_id") if isinstance(value, dict) else None
    except ValueError:
        run_id = None
    if type(run_id) is not int or run_id <= 0:
        raise UncertainDispatch("dispatch_response_invalid", status)
    try:
        save_run(path, {**intent, "run_id": run_id})
    except OSError as error:
        raise UncertainDispatch(
            "dispatch_run_persistence_failed", status, run_id=run_id, exit_code=2
        ) from error
    if result.returncode:
        code = result.returncode if result.returncode >= 0 else 128 - result.returncode
        raise UncertainDispatch(
            "dispatch_command_failed", status, run_id=run_id, exit_code=code
        )
    return run_id
