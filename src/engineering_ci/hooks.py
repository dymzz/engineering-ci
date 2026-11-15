"""Git Hook event capture primitives.

This module only captures the native Git event. It does not install hooks,
dispatch jobs, or decide a Check verdict. A native pre-commit/pre-push entry
may call :func:`capture_pre_push` or :func:`capture_pre_commit` once, persist the
returned event, and hand the event reference to a dispatcher.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from engineering_ci import selection

HOOKS = ("pre-commit", "pre-push")
EVENT_SCHEMA_VERSION = 1


class HookEventError(ValueError):
    """The native Git event cannot be captured without loss."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _git(root, *args, env=None):
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=env,
    )


def _git_dir(root):
    result = _git(root, "rev-parse", "--git-dir")
    if result.returncode != 0:
        raise HookEventError("git_directory_unavailable")
    path = Path(result.stdout.strip())
    if not path.is_absolute():
        path = Path(root) / path
    return path.resolve()


def _digest_bytes(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()


def redact_remote_location(location):
    """Return a non-secret remote identity while keeping correlation possible."""
    if not isinstance(location, str) or not location:
        raise HookEventError("remote_location_invalid")
    parts = urlsplit(location)
    if parts.scheme and parts.netloc:
        host = parts.hostname or ""
        port = f":{parts.port}" if parts.port else ""
        netloc = host + port
        if parts.username is not None:
            netloc = "***@" + netloc
        return {
            "kind": "url",
            "value": urlunsplit((parts.scheme, netloc, parts.path, "", "")),
            "sha256": _digest_bytes(location.encode("utf-8")),
            "credentials_redacted": parts.username is not None or bool(parts.query),
        }
    return {
        "kind": "path" if os.path.isabs(location) else "name",
        "value": location
        if len(location) <= 500
        else _digest_bytes(location.encode("utf-8")),
        "sha256": _digest_bytes(location.encode("utf-8")),
        "credentials_redacted": False,
    }


def _ref_action(update):
    after = update["after"]
    before = update["before"]
    if selection._is_zero_oid(after):
        return "delete"
    if selection._is_zero_oid(before):
        return "create"
    return "update"


def capture_pre_push(root, remote_name, remote_location, stdin_text):
    """Capture argv and the complete ordered stdin for one pre-push event."""
    if not isinstance(remote_name, str) or not remote_name:
        raise HookEventError("remote_name_invalid")
    try:
        updates = selection.parse_pre_push_stdin(stdin_text)
    except selection.SelectionError:
        raise HookEventError("pre_push_input_invalid") from None
    refs = []
    for update in updates:
        for key in ("before", "after"):
            if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", update[key]):
                raise HookEventError("pre_push_oid_invalid")
        refs.append({**update, "ref_action": _ref_action(update)})
    return {
        "schema_version": EVENT_SCHEMA_VERSION,
        "hook": "pre-push",
        "remote": {
            "name": remote_name,
            "location": redact_remote_location(remote_location),
        },
        "refs": refs,
    }


def capture_pre_commit(root, *, environ=None):
    """Capture the actual Index used by a pre-commit invocation."""
    environ = os.environ if environ is None else environ
    index_file = environ.get("GIT_INDEX_FILE")
    if index_file:
        index_path = Path(index_file)
        if not index_path.is_absolute():
            index_path = Path(root) / index_path
    else:
        index_path = _git_dir(root) / "index"
    if not index_path.is_file():
        raise HookEventError("index_file_unavailable")
    tree = _git(root, "write-tree", env={**os.environ, **environ})
    if tree.returncode != 0:
        raise HookEventError("index_tree_unavailable")
    git_dir = _git_dir(root)
    return {
        "schema_version": EVENT_SCHEMA_VERSION,
        "hook": "pre-commit",
        "index": {
            "file": _event_path(root, index_path, git_dir),
            "tree": tree.stdout.strip(),
            "sha256": _digest_bytes(index_path.read_bytes()),
        },
    }


def _event_path(root, path, git_dir):
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        pass
    try:
        return resolved.relative_to(git_dir).as_posix()
    except ValueError:
        return str(resolved)


def event_path(root, event):
    """Return the immutable event file under the Git directory."""
    payload = json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(payload).hexdigest()
    return _git_dir(root) / "eci-hooks" / "events" / f"{digest}.json"


def persist_event(root, event):
    """Persist one event atomically and return its path."""
    target = event_path(root, event)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(
        json.dumps(event, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )
    os.replace(temporary, target)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description="Capture Git Hook events")
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture = subparsers.add_parser("capture")
    capture.add_argument("hook", choices=HOOKS)
    capture.add_argument("--root", default=".")
    capture.add_argument("--remote-name")
    capture.add_argument("--remote-location")
    capture.add_argument("--stdin-file")
    capture.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.hook == "pre-push":
            if args.remote_name is None or args.remote_location is None:
                raise HookEventError("remote_arguments_required")
            stdin_text = (
                Path(args.stdin_file).read_text(encoding="utf-8")
                if args.stdin_file
                else sys.stdin.read()
            )
            event = capture_pre_push(
                Path(args.root), args.remote_name, args.remote_location, stdin_text
            )
        else:
            event = capture_pre_commit(Path(args.root))
        path = persist_event(Path(args.root), event)
    except HookEventError as error:
        print(error.code, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"event": event, "path": str(path)}, sort_keys=True))
    else:
        print(path)
    return 0
