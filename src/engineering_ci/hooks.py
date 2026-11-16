"""Git Hook event capture primitives.

This module only captures the native Git event. It does not install hooks,
dispatch jobs, or decide a Check verdict. A native pre-commit/pre-push entry
may call :func:`capture_pre_push` or :func:`capture_pre_commit` once, persist the
returned event, and hand the invocation reference to a dispatcher.

Security boundaries:

- Git pre-push ``remote_name`` and ``remote_location`` are both untrusted native
  argv values. Either can be a direct URL containing credentials. Raw values
  are only used inside this controlled capture step; the returned/persisted
  Event contains a redacted, correlatable identity only.
- The content-addressed Event path is an ``event_payload_ref``, not an execution
  identity. Every :func:`persist_event` call creates a distinct
  ``invocation_ref`` and invocation record; a later Coordinator maps that to
  Run/Attempt. No #104 strict receipt field is extended here.
- Event files are written with a unique temporary name, flushed, and atomically
  published with owner-only permissions. Pre-commit captures verify that the
  Index bytes did not change while ``git write-tree`` ran.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from engineering_ci import selection

HOOKS = ("pre-commit", "pre-push")
EVENT_SCHEMA_VERSION = 1
INVOCATION_SCHEMA_VERSION = 1
INVOCATION_KIND = "engineering-ci-hook-invocation"
EVENT_RETENTION_SECONDS = 24 * 60 * 60
_SAFE_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SCP_LIKE = re.compile(r"^(?:(?P<user>[^/@:]+)@)?(?P<host>[^/:]+):(?P<path>.*)$")
_WINDOWS_ABS = re.compile(r"^[A-Za-z]:[\\/]")


class HookEventError(ValueError):
    """The native Git event cannot be captured without loss."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class HookInvocation:
    """One capture invocation and its content-addressed native Event."""

    invocation_ref: str
    event_payload_ref: str
    event_path: Path
    invocation_path: Path


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


def _identity_value(value, *, field):
    if not isinstance(value, str) or not value:
        raise HookEventError(f"{field}_invalid")
    return value


def redact_remote(value, *, field="remote"):
    """Return a safe correlatable identity for one native remote argv value.

    Raw URLs, paths, userinfo, query strings and scp-like path fragments are
    never retained. The full original value is represented by a digest so the
    same remote can still be correlated without exposing secrets.
    """
    value = _identity_value(value, field=field)
    encoded = value.encode("utf-8")
    digest = _digest_bytes(encoded)
    if _SAFE_ALIAS.fullmatch(value):
        return {
            "kind": "alias",
            "value": value,
            "sha256": digest,
            "credentials_redacted": False,
        }
    parts = urlsplit(value)
    if parts.scheme and parts.netloc:
        identity = {
            "kind": "url",
            "host": parts.hostname or "",
            "sha256": digest,
            "credentials_redacted": bool(
                parts.username is not None
                or parts.password is not None
                or parts.query
                or parts.fragment
            ),
        }
        try:
            port = parts.port
        except ValueError:
            port = None
        if port:
            identity["port"] = port
        return identity
    scp_like = _SCP_LIKE.fullmatch(value)
    if scp_like and not os.path.isabs(value) and not _WINDOWS_ABS.match(value):
        return {
            "kind": "scp",
            "host": scp_like.group("host"),
            "sha256": digest,
            "credentials_redacted": scp_like.group("user") is not None,
        }
    if os.path.isabs(value) or value.startswith(("./", "../")):
        return {
            "kind": "path",
            "sha256": digest,
            "credentials_redacted": False,
        }
    return {
        "kind": "opaque",
        "sha256": digest,
        "credentials_redacted": False,
    }


def redact_remote_location(location):
    """Backward-compatible wrapper for the location-specific identity."""
    return redact_remote(location, field="remote_location")


def _remote_identity(name, location):
    return _digest_bytes(f"{name}\0{location}".encode())


def _ref_action(update):
    before = update["before"]
    after = update["after"]
    if selection._is_zero_oid(after):
        return "delete"
    if selection._is_zero_oid(before):
        return "create"
    return "update"


def capture_pre_push(root, remote_name, remote_location, stdin_text):
    """Capture argv and the complete ordered stdin for one pre-push event.

    Both native argv values are untrusted and may contain credentials. The
    returned Event only contains redacted identities plus their correlation
    digest; raw values never enter the Event.
    """
    remote_name = _identity_value(remote_name, field="remote_name")
    remote_location = _identity_value(remote_location, field="remote_location")
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
            "name": redact_remote(remote_name, field="remote_name"),
            "location": redact_remote(remote_location, field="remote_location"),
            "identity": _remote_identity(remote_name, remote_location),
        },
        "refs": refs,
    }


def capture_pre_commit(root, *, environ=None):
    """Capture the actual Index used by a pre-commit invocation.

    The Index bytes are read immediately before and after ``git write-tree``.
    A change during the window is refused rather than combining a Tree ID and
    Index digest from different moments. This still does not claim that native
    tools read those bytes; Source materialization/Check execution must verify
    the recorded identity.
    """
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
    tree_id = None
    index_digest = None
    previous_tree = None
    # ``git write-tree`` may refresh the index cache on its first call. Repeat
    # until the bytes are unchanged across one complete write-tree invocation;
    # if the resulting Tree changes while the bytes keep moving, the Index was
    # mutated concurrently and must be refused.
    for _attempt in range(3):
        before = index_path.read_bytes()
        tree = _git(root, "write-tree", env={**os.environ, **environ})
        if tree.returncode != 0:
            raise HookEventError("index_tree_unavailable")
        after = index_path.read_bytes()
        current_tree = tree.stdout.strip()
        if previous_tree is not None and current_tree != previous_tree:
            raise HookEventError("index_changed_during_capture")
        if before == after:
            tree_id = current_tree
            index_digest = _digest_bytes(after)
            break
        previous_tree = current_tree
    if tree_id is None:
        raise HookEventError("index_changed_during_capture")
    git_dir = _git_dir(root)
    return {
        "schema_version": EVENT_SCHEMA_VERSION,
        "hook": "pre-commit",
        "index": {
            "file": _event_path(root, index_path, git_dir),
            "tree": tree_id,
            "sha256": index_digest,
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


def canonical_event_bytes(event):
    """Canonical bytes shared by event publication and digest verification."""
    return json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")


def event_payload_ref(event):
    """Content identity of one Event payload; not an execution identity."""
    return _digest_bytes(canonical_event_bytes(event))


def event_path(root, event):
    """Return the content-addressed Event file under the Git directory."""
    digest = hashlib.sha256(canonical_event_bytes(event)).hexdigest()
    return _git_dir(root) / "eci-hooks" / "events" / f"{digest}.json"


def load_event(path):
    """Read an Event and verify that its bytes are the canonical payload."""
    raw = Path(path).read_bytes()
    try:
        event = json.loads(raw)
    except ValueError:
        raise HookEventError("event_payload_invalid") from None
    if not isinstance(event, dict) or canonical_event_bytes(event) != raw:
        raise HookEventError("event_payload_mismatch")
    return event


def _write_private_file(path, payload):
    """Atomically publish canonical bytes with owner-only permissions.

    Concurrent writers use unique temporary names. Windows may transiently
    refuse a replace while another writer publishes the same content-addressed
    target; validate and reuse identical bytes instead of treating that as
    corruption.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    for attempt in range(5):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            try:
                os.replace(temporary, path)
            except OSError as error:
                if path.exists():
                    try:
                        if path.read_bytes() == payload:
                            return path
                    except OSError:
                        pass
                if attempt == 4:
                    raise HookEventError("event_publish_failed") from error
                time.sleep(0.02 * (attempt + 1))
                continue
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            return path
        finally:
            if temporary is not None and temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass
    raise HookEventError("event_publish_failed")


def _invocation_path(git_dir, invocation_id):
    return git_dir / "eci-hooks" / "invocations" / f"{invocation_id}.json"


def persist_event(root, event):
    """Persist one Event and create an independent Invocation record.

    The Event path is content-addressed; each call has a unique
    ``invocation_ref`` and a separate invocation file. A pre-existing target
    is reused only when its stored bytes equal the canonical payload.
    """
    payload = canonical_event_bytes(event)
    target = event_path(root, event)
    if target.exists():
        if target.read_bytes() != payload:
            raise HookEventError("event_payload_conflict")
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
    else:
        _write_private_file(target, payload)
    git_dir = _git_dir(root)
    invocation_id = uuid.uuid4().hex
    invocation_ref = f"invocation:{invocation_id}"
    try:
        event_file = target.relative_to(git_dir).as_posix()
    except ValueError:
        event_file = str(target)
    invocation_path = _invocation_path(git_dir, invocation_id)
    invocation = {
        "schema_version": INVOCATION_SCHEMA_VERSION,
        "kind": INVOCATION_KIND,
        "invocation_ref": invocation_ref,
        "event_payload_ref": event_payload_ref(event),
        "event_file": event_file,
        "hook": event.get("hook"),
    }
    _write_private_file(invocation_path, canonical_event_bytes(invocation))
    return HookInvocation(
        invocation_ref=invocation_ref,
        event_payload_ref=invocation["event_payload_ref"],
        event_path=target,
        invocation_path=invocation_path,
    )


def prune_events(root, *, now=None, retention_seconds=EVENT_RETENTION_SECONDS):
    """Delete Event/Invocation files older than the explicit retention boundary.

    The caller runs this outside an active capture. This is a bounded lifecycle
    helper, not an Event database.
    """
    if type(retention_seconds) not in (int, float) or retention_seconds < 0:
        raise HookEventError("retention_invalid")
    cutoff = (time.time() if now is None else now) - retention_seconds
    git_dir = _git_dir(root)
    removed = 0
    for directory in ("events", "invocations"):
        parent = git_dir / "eci-hooks" / directory
        if not parent.is_dir():
            continue
        for path in parent.glob("*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
    return removed


def _read_remote_file(path, *, field):
    if path is None:
        raise HookEventError("remote_arguments_required")
    try:
        value = Path(path).read_text(encoding="utf-8")
    except OSError:
        raise HookEventError(f"{field}_file_unavailable") from None
    return value.rstrip("\r\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Capture Git Hook events")
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture = subparsers.add_parser("capture")
    capture.add_argument("hook", choices=HOOKS)
    capture.add_argument("--root", default=".")
    # Raw remote argv is deliberately not accepted as a CLI argument: process
    # arguments are observable by other processes. The native hook writes the
    # two values to controlled files and passes only those paths here.
    capture.add_argument("--remote-name-file")
    capture.add_argument("--remote-location-file")
    capture.add_argument("--stdin-file")
    capture.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.hook == "pre-push":
            remote_name = _read_remote_file(args.remote_name_file, field="remote_name")
            remote_location = _read_remote_file(
                args.remote_location_file, field="remote_location"
            )
            stdin_text = (
                Path(args.stdin_file).read_text(encoding="utf-8")
                if args.stdin_file
                else sys.stdin.read()
            )
            event = capture_pre_push(
                Path(args.root), remote_name, remote_location, stdin_text
            )
        else:
            event = capture_pre_commit(Path(args.root))
        reference = persist_event(Path(args.root), event)
    except HookEventError as error:
        print(error.code, file=sys.stderr)
        return 2
    if args.json:
        print(
            json.dumps(
                {
                    "event": event,
                    "path": str(reference.event_path),
                    "invocation_ref": reference.invocation_ref,
                    "event_payload_ref": reference.event_payload_ref,
                    "invocation_path": str(reference.invocation_path),
                },
                sort_keys=True,
            )
        )
    else:
        print(reference.event_path)
    return 0
