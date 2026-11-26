"""Git Hook event capture primitives.

The native hook captures and persists one Git event per invocation. Lefthook
owns installation and scheduling. Hygiene supplies the native whitespace
outcome for the captured pre-commit tree.

Security boundaries:

- Git pre-push ``remote_name`` and ``remote_location`` are both untrusted native
  argv values. Either can be a direct URL containing credentials. Raw values
  are only used inside this controlled capture step; the returned/persisted
  Event contains a redacted, correlatable identity only.
- The content-addressed Event path is an ``event_payload_ref``, not an execution
  identity. Every :func:`persist_event` call creates a distinct
  ``invocation_ref`` and invocation record for the jobs consuming the event.
  No #104 strict receipt field is extended here.
- Event files are written with a unique temporary name, flushed, and atomically
  published with owner-only permissions. Capture publication and pruning share
  a small per-repository lock; pruning keeps Events referenced by current
  Invocations. Pre-commit captures verify that the Index bytes did not change
  while ``git write-tree`` ran.
- The caller owns the raw remote files handed to the CLI. The caller creates
  them with owner-only access and deletes them on success and failure. The
  capture CLI only reads these files.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from engineering_ci import hygiene, selection

HOOKS = ("pre-commit", "pre-push")
EVENT_SCHEMA_VERSION = 2
INVOCATION_SCHEMA_VERSION = 1
INVOCATION_KIND = "engineering-ci-hook-invocation"
EVENT_RETENTION_SECONDS = 24 * 60 * 60
INDEX_LOCATOR_KINDS = frozenset({"git_dir", "worktree", "absolute"})
_INVOCATION_REF = re.compile(r"invocation:([0-9a-f]{32})")
_TREE_OID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_SAFE_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")
_SCP_LIKE = re.compile(r"^(?:(?P<user>[^/@:]+)@)?(?P<host>[^@/:]+):(?P<path>.*)$")
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


@dataclass(frozen=True)
class InvocationBinding:
    """Verified Invocation identity and the content-addressed Event it names."""

    invocation_ref: str
    event_payload_ref: str
    event: dict
    hook: str
    invocation_path: Path
    event_path: Path


def _git(root, *args, env=None, data=None):
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=env,
        input=data,
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


def _hook_storage(git_dir, *, create=False):
    """Return the controlled Events/Invocations directories for one repository.

    The physical Git directory is the trust anchor. A symlink/junction for
    ``eci-hooks``, ``events`` or ``invocations`` that resolves outside that
    Git directory is rejected before any write or prune.
    """
    git_dir = Path(git_dir).resolve()
    eci_dir = git_dir / "eci-hooks"
    events_dir = eci_dir / "events"
    invocations_dir = eci_dir / "invocations"
    directories = (eci_dir, events_dir, invocations_dir)
    for directory in directories:
        if directory.is_symlink():
            raise HookEventError("hook_storage_outside_repository")
        if directory.exists():
            try:
                directory.resolve().relative_to(git_dir)
            except (OSError, ValueError):
                raise HookEventError("hook_storage_outside_repository") from None
    if create:
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    for directory in directories:
        if not directory.exists():
            continue
        try:
            directory.resolve().relative_to(git_dir)
        except (OSError, ValueError):
            raise HookEventError("hook_storage_outside_repository") from None
    return events_dir, invocations_dir


@contextmanager
def _capture_lock(git_dir):
    """Serialize Event publication and pruning for one repository.

    A one-byte lock file avoids an Event database while ensuring a prune cannot
    delete an Event between the content write and the Invocation publication.
    """
    _hook_storage(git_dir, create=True)
    lock_dir = Path(git_dir) / "eci-hooks"
    lock_path = lock_dir / "capture.lock"
    if lock_path.is_symlink():
        raise HookEventError("hook_storage_outside_repository")
    with open(lock_path, "a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            if os.name == "nt":
                import msvcrt

                with suppress(OSError):
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                with suppress(OSError):
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _identity_value(value, *, field):
    if not isinstance(value, str) or not value:
        raise HookEventError(f"{field}_invalid")
    return value


def _safe_host(host):
    if not host:
        return None
    if _SAFE_HOST.fullmatch(host):
        return host
    if ":" in host and re.fullmatch(r"[0-9A-Fa-f:]+", host):
        return host
    return None


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
    try:
        parts = urlsplit(value)
    except ValueError:
        parts = None
    if parts is not None and parts.scheme and parts.netloc:
        try:
            hostname = parts.hostname or ""
        except ValueError:
            hostname = ""
        host = _safe_host(hostname)
        if host is None:
            return {
                "kind": "opaque",
                "sha256": digest,
                "credentials_redacted": True,
            }
        identity = {
            "kind": "url",
            "host": host,
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
    if (
        scp_like
        and "://" not in value
        and not os.path.isabs(value)
        and not _WINDOWS_ABS.match(value)
    ):
        host = _safe_host(scp_like.group("host"))
        if host is None:
            return {
                "kind": "opaque",
                "sha256": digest,
                "credentials_redacted": True,
            }
        return {
            "kind": "scp",
            "host": host,
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


def _active_index_path(root, environ, *, git_dir=None):
    index_file = environ.get("GIT_INDEX_FILE")
    if not index_file:
        return (git_dir or _git_dir(root)) / "index"
    return Path(root) / index_file


def capture_pre_commit(root, *, environ=None):
    """Capture the actual Index used by a pre-commit invocation.

    Read Index bytes before and after ``git write-tree`` and check that the
    baseline Tree stayed fixed. The native check verifies the recorded Source
    identity again when it consumes this Event.
    """
    environ = os.environ if environ is None else environ
    base_tree = _pre_commit_base_tree(root)
    head_commit = selection._resolve(root, "HEAD")
    git_dir = _git_dir(root)
    index_path = _active_index_path(root, environ, git_dir=git_dir)
    captured_path = index_path.resolve()
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
    if index_path.resolve() != captured_path:
        raise HookEventError("index_changed_during_capture")
    if (
        _pre_commit_base_tree(root) != base_tree
        or selection._resolve(root, "HEAD") != head_commit
    ):
        raise HookEventError("pre_commit_base_changed_during_capture")
    locator = _index_locator(root, captured_path, git_dir)
    return {
        "schema_version": EVENT_SCHEMA_VERSION,
        "hook": "pre-commit",
        "base_tree": base_tree,
        "head_commit": head_commit,
        "index": {
            # Authoritative namespaced locator; consumers must not re-guess bases.
            "locator": locator,
            # Stable relative/absolute display string derived from the locator.
            "file": locator["path"],
            "tree": tree_id,
            "sha256": index_digest,
        },
    }


def _index_locator(root, index_path, git_dir):
    """Encode Index path with an unambiguous namespace for Event consumers.

    Preference order:
    - ``git_dir`` when under the physical Git Directory (default Index);
    - ``worktree`` when under the Worktree but outside the Git Directory;
    - ``absolute`` for explicitly external Index files.
    """
    resolved = Path(index_path).resolve()
    git_dir = Path(git_dir).resolve()
    root = Path(root).resolve()
    try:
        rel = resolved.relative_to(git_dir).as_posix()
        if not rel or rel.startswith(".."):
            raise ValueError("outside git_dir")
        return {"kind": "git_dir", "path": rel}
    except ValueError:
        pass
    try:
        rel = resolved.relative_to(root).as_posix()
        if not rel or rel.startswith(".."):
            raise ValueError("outside worktree")
        return {"kind": "worktree", "path": rel}
    except ValueError:
        pass
    return {"kind": "absolute", "path": str(resolved)}


def resolve_index_path(event, *, root):
    """Resolve the captured Index file from an Event's authoritative locator.

    Fail closed when the Event lacks a namespaced locator or the path is missing.
    Does not probe alternate bases when a relative path is absent.
    """
    if not isinstance(event, dict) or event.get("hook") != "pre-commit":
        raise HookEventError("index_locator_unavailable")
    if event.get("schema_version") != EVENT_SCHEMA_VERSION:
        raise HookEventError("index_locator_unavailable")
    index = event.get("index")
    if not isinstance(index, dict):
        raise HookEventError("index_locator_unavailable")
    locator = index.get("locator")
    if not isinstance(locator, dict):
        raise HookEventError("index_locator_unavailable")
    kind = locator.get("kind")
    path = locator.get("path")
    if kind not in INDEX_LOCATOR_KINDS or not isinstance(path, str) or not path:
        raise HookEventError("index_locator_invalid")
    root = Path(root).resolve()
    if kind == "absolute":
        candidate = Path(path)
        if not candidate.is_absolute():
            raise HookEventError("index_locator_invalid")
        resolved = candidate.resolve()
    elif kind == "git_dir":
        git_dir = _git_dir(root)
        resolved = (Path(git_dir) / path).resolve()
        try:
            resolved.relative_to(Path(git_dir).resolve())
        except ValueError as error:
            raise HookEventError("index_locator_invalid") from error
    else:  # worktree
        resolved = (root / path).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as error:
            raise HookEventError("index_locator_invalid") from error
    if not resolved.is_file():
        raise HookEventError("index_file_unavailable")
    return resolved


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
    """Read an Event and verify canonical bytes plus its filename identity."""
    path = Path(path)
    raw = path.read_bytes()
    try:
        event = json.loads(raw)
    except ValueError:
        raise HookEventError("event_payload_invalid") from None
    if not isinstance(event, dict) or canonical_event_bytes(event) != raw:
        raise HookEventError("event_payload_mismatch")
    if not re.fullmatch(r"[0-9a-f]{64}", path.stem):
        raise HookEventError("event_path_invalid")
    if hashlib.sha256(raw).hexdigest() != path.stem:
        raise HookEventError("event_payload_mismatch")
    return event


def _invocation_git_dir(path):
    path = Path(path).resolve()
    if path.parent.name != "invocations" or path.parent.parent.name != "eci-hooks":
        raise HookEventError("invocation_path_invalid")
    git_dir = path.parent.parent.parent
    if not (git_dir / "HEAD").is_file():
        raise HookEventError("invocation_path_invalid")
    return git_dir


def _controlled_event_path(git_dir, event_file):
    if not isinstance(event_file, str) or not event_file:
        raise HookEventError("invocation_payload_invalid")
    candidate_file = Path(event_file)
    if candidate_file.is_absolute() or ".." in candidate_file.parts:
        raise HookEventError("invocation_event_outside_store")
    git_dir = Path(git_dir).resolve()
    try:
        events_dir, _invocations_dir = _hook_storage(git_dir, create=False)
    except HookEventError:
        raise HookEventError("invocation_event_outside_store") from None
    event_root = events_dir.resolve()
    candidate = (git_dir / candidate_file).resolve()
    try:
        candidate.relative_to(event_root)
    except ValueError:
        raise HookEventError("invocation_event_outside_store") from None
    if candidate.parent != event_root or not re.fullmatch(
        r"[0-9a-f]{64}\.json", candidate.name
    ):
        raise HookEventError("invocation_event_outside_store")
    return candidate


def load_invocation_binding(path):
    """Read an Invocation and verify its Event content/reference linkage.

    The referenced Event must be a direct child of this repository's
    ``eci-hooks/events`` namespace; absolute paths, ``..`` escapes and
    cross-repository references are rejected.
    """
    path = Path(path)
    raw = path.read_bytes()
    try:
        invocation = json.loads(raw)
    except ValueError:
        raise HookEventError("invocation_payload_invalid") from None
    if not isinstance(invocation, dict) or canonical_event_bytes(invocation) != raw:
        raise HookEventError("invocation_payload_invalid")
    if (
        invocation.get("kind") != INVOCATION_KIND
        or invocation.get("schema_version") != INVOCATION_SCHEMA_VERSION
    ):
        raise HookEventError("invocation_payload_invalid")
    expected_ref = f"invocation:{path.stem}"
    if invocation.get("invocation_ref") != expected_ref:
        raise HookEventError("invocation_ref_mismatch")
    git_dir = _invocation_git_dir(path)
    event_path = _controlled_event_path(git_dir, invocation.get("event_file"))
    event = load_event(event_path)
    hook = invocation.get("hook")
    if hook not in HOOKS or event.get("hook") != hook:
        raise HookEventError("invocation_hook_mismatch")
    payload_ref = invocation.get("event_payload_ref")
    if event_payload_ref(event) != payload_ref:
        raise HookEventError("invocation_event_mismatch")
    return InvocationBinding(
        invocation_ref=expected_ref,
        event_payload_ref=payload_ref,
        event=event,
        hook=hook,
        invocation_path=path.resolve(),
        event_path=event_path,
    )


def load_invocation(path):
    """Read the verified Event named by an Invocation."""
    return load_invocation_binding(path).event


def _load_invocation_ref(root, invocation_ref):
    match = (
        _INVOCATION_REF.fullmatch(invocation_ref)
        if isinstance(invocation_ref, str)
        else None
    )
    if match is None:
        raise HookEventError("invocation_ref_invalid")
    git_dir = _git_dir(root)
    _hook_storage(git_dir)
    path = _invocation_path(git_dir, match[1])
    if _invocation_git_dir(path) != git_dir:
        raise HookEventError("invocation_repository_mismatch")
    try:
        return load_invocation_binding(path)
    except OSError:
        raise HookEventError("invocation_file_unavailable") from None


def _write_private_file(path, payload):
    """Atomically publish canonical bytes with owner-only permissions.

    Concurrent writers use unique temporary names. Windows may transiently
    refuse a replace while another writer publishes the same content-addressed
    target; validate and reuse identical bytes instead of treating that as
    corruption.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with suppress(OSError):
        os.chmod(path.parent, 0o700)
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
            with suppress(OSError):
                os.chmod(temporary, 0o600)
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
            with suppress(OSError):
                os.chmod(path, 0o600)
            return path
        finally:
            if temporary is not None and temporary.exists():
                with suppress(OSError):
                    temporary.unlink()
    raise HookEventError("event_publish_failed")


def _invocation_path(git_dir, invocation_id):
    return git_dir / "eci-hooks" / "invocations" / f"{invocation_id}.json"


def persist_event(root, event):
    """Persist one Event and create an independent Invocation record.

    The Event path is content-addressed; each call has a unique
    ``invocation_ref`` and a separate invocation file. A pre-existing target
    is reused only when its stored bytes equal the canonical payload.
    """
    git_dir = _git_dir(root)
    with _capture_lock(git_dir):
        payload = canonical_event_bytes(event)
        target = event_path(root, event)
        if target.exists():
            if target.read_bytes() != payload:
                raise HookEventError("event_payload_conflict")
            with suppress(OSError):
                os.chmod(target, 0o600)
        else:
            _write_private_file(target, payload)
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


def _record_stage_timing(
    invocation_path,
    *,
    invocation_ref,
    event_payload_ref,
    hook_name,
    stage,
    started_ns,
    completed_ns,
    elapsed_seconds,
    status,
    job_ref=None,
    suffix=None,
    extra=None,
):
    """Record a bounded monotonic stage timing sidecar inside the invocation dir."""
    stage_suffix = suffix or f".timing-{stage}"
    path = invocation_path.with_suffix(stage_suffix)
    if path.is_symlink() or path.resolve().parent != invocation_path.parent:
        raise HookEventError("hook_artifact_outside_invocation")
    wall_clock_monotonic = (
        started_ns is not None
        and completed_ns is not None
        and completed_ns >= started_ns
    )
    payload = {
        "schema_version": 1,
        "invocation_ref": invocation_ref,
        "event_payload_ref": event_payload_ref,
        "hook_name": hook_name,
        "stage": stage,
        "timing_clock": "monotonic_perf_counter",
        "started_at_ns": started_ns,
        "completed_at_ns": completed_ns if wall_clock_monotonic else None,
        "time_source_verified": False,
        "elapsed_seconds": elapsed_seconds,
        "status": status,
    }
    if job_ref is not None:
        payload["job_ref"] = job_ref
    if extra:
        payload.update(extra)
    git_dir = _invocation_git_dir(invocation_path)
    with _capture_lock(git_dir):
        _write_private_file(path, canonical_event_bytes(payload))
    return path


def _invocation_event_path(path, git_dir):
    try:
        invocation = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(invocation, dict):
        return None
    event_file = invocation.get("event_file")
    if not isinstance(event_file, str) or not event_file:
        return None
    event_path = Path(event_file)
    if not event_path.is_absolute():
        event_path = git_dir / event_path
    try:
        return event_path.resolve()
    except OSError:
        return None


def prune_events(root, *, now=None, retention_seconds=EVENT_RETENTION_SECONDS):
    """Prune expired Invocations and unreferenced expired Events.

    Current Invocations protect their referenced Event even when the Event
    itself is old (content-addressed Events are reused by newer Invocations).
    The per-repository capture lock prevents a concurrent persist from
    publishing a new reference between the protection scan and deletion.
    """
    if type(retention_seconds) not in (int, float) or retention_seconds < 0:
        raise HookEventError("retention_invalid")
    cutoff = (time.time() if now is None else now) - retention_seconds
    git_dir = _git_dir(root)
    removed = 0
    with _capture_lock(git_dir):
        protected = set()
        invocation_dir = git_dir / "eci-hooks" / "invocations"
        if invocation_dir.is_dir():
            for path in invocation_dir.glob("*.json"):
                try:
                    expired = path.stat().st_mtime < cutoff
                except OSError:
                    continue
                if expired:
                    if _INVOCATION_REF.fullmatch(f"invocation:{path.stem}"):
                        sidecars = [
                            path.with_suffix(suffix)
                            for suffix in (
                                ".plan",
                                ".activation",
                                ".execution",
                                ".receipt",
                                ".push-source",
                            )
                        ]
                        sidecars.extend(
                            path.parent.glob(f"{path.stem}.execution-[0-9]*")
                        )
                        for suffix in ("execution", "activation"):
                            sidecars.extend(
                                path.parent.glob(f"{path.stem}.{suffix}-check-*")
                            )
                        sidecars.extend(path.parent.glob(f"{path.stem}.timing-*"))
                        for sidecar in sidecars:
                            with suppress(OSError):
                                sidecar.unlink(missing_ok=True)
                    try:
                        path.unlink()
                        removed += 1
                    except OSError:
                        continue
                    continue
                event_path = _invocation_event_path(path, git_dir)
                if event_path is not None:
                    protected.add(event_path)
        event_dir = git_dir / "eci-hooks" / "events"
        if event_dir.is_dir():
            for path in event_dir.glob("*.json"):
                try:
                    resolved = path.resolve()
                    if resolved in protected:
                        continue
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


def _read_pre_push_frame(stream):
    """Read a Lefthook input frame without logging the original argv."""
    try:
        marker, name, location, stdin = stream.read().split(b":", 3)
        if marker != b"ECI-PRE-PUSH":
            raise ValueError
        return (
            base64.b64decode(name, validate=True).decode("utf-8"),
            base64.b64decode(location, validate=True).decode("utf-8"),
            stdin.decode("utf-8"),
        )
    except ValueError:
        raise HookEventError("pre_push_argument_frame_invalid") from None


def _pre_commit_base_tree(root):
    head = _git(root, "rev-parse", "--verify", "--quiet", "HEAD^{tree}")
    if head.returncode == 0:
        return head.stdout.strip()
    empty = selection.unborn_index_base(root)
    if empty is None:
        raise HookEventError("pre_commit_base_unavailable")
    return empty


def _capture_details(event, reference):
    return {
        "event": event,
        "path": str(reference.event_path),
        "invocation_ref": reference.invocation_ref,
        "event_payload_ref": reference.event_payload_ref,
        "invocation_path": str(reference.invocation_path),
    }


def _active_index_matches(root, path, captured):
    try:
        if _active_index_path(root, os.environ).resolve() != path or _digest_bytes(
            path.read_bytes()
        ) != captured.get("sha256"):
            return False
        current_tree = _git(root, "write-tree")
        return (
            current_tree.returncode == 0
            and current_tree.stdout.strip() == captured["tree"]
            and _active_index_path(root, os.environ).resolve() == path
            and _digest_bytes(path.read_bytes()) == captured["sha256"]
        )
    except (OSError, HookEventError):
        return False


def check_pre_commit(root, *, invocation_ref=None):
    """Check the exact staged tree and retain the native result for its event."""
    if invocation_ref is None:
        event = capture_pre_commit(root)
        reference = persist_event(root, event)
    else:
        reference = _load_invocation_ref(root, invocation_ref)
        if reference.hook != "pre-commit":
            raise HookEventError("invocation_hook_mismatch")
        event = reference.event
    base = event.get("base_tree")
    if not isinstance(base, str) or not _TREE_OID.fullmatch(base):
        raise HookEventError("pre_commit_base_unavailable")
    index = resolve_index_path(event, root=root)
    captured = event["index"]
    tree = captured.get("tree")
    if not isinstance(tree, str) or not _TREE_OID.fullmatch(tree):
        raise HookEventError("index_tree_unavailable")
    native = hygiene.check_git_whitespace(root, base=base, tree=tree)
    index_matches = _active_index_matches(root, index, captured)
    try:
        base_matches = _pre_commit_base_tree(root) == base
    except HookEventError:
        base_matches = False
    reason = None
    if not index_matches:
        reason = "index_changed_during_check"
    elif not base_matches:
        reason = "pre_commit_base_changed_during_check"
    return {
        **_capture_details(event, reference),
        "base_tree": base,
        "check": native,
        "source_status": "matched" if reason is None else "stale",
        "reason": reason or native["reason"],
        "exit_code": native["exit_code"] or (1 if reason is not None else 0),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Capture and check Git Hook inputs")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check")
    check.add_argument("hook", choices=("pre-commit",))
    check.add_argument("--root", type=Path, default=Path("."))
    check.add_argument("--invocation-ref", help="Consume an existing pre-commit Event.")
    check.add_argument("--json", action="store_true")
    for command in ("prepare", "run", "reconcile", "consume", "skip"):
        job = subparsers.add_parser(command)
        job.add_argument(
            "hook", choices=("pre-commit",) if command == "skip" else HOOKS
        )
        job.add_argument("--root", type=Path, default=Path("."))
        job.add_argument("--invocation-ref", required=True)
        job.add_argument("--json", action="store_true")
        if command in ("skip", "run"):
            job.add_argument("--job")
        if command == "prepare":
            job.add_argument("--split-checks", action="store_true")
            job.add_argument(
                "--dagger-module",
                help="Use . in the ECI checkout or pin the ECI Dagger module to a full Git commit.",
            )
            job.add_argument("--new-ref-baseline")
            job.add_argument(
                "--require-plan",
                action="store_true",
                help="Require each pre-push Ref to have a Plan and matching EffectiveProfile.",
            )
            job.add_argument(
                "--effective-profile",
                action="append",
                default=[],
                help="Repository-relative push profile in the Ref's actual Git tree.",
            )
    capture = subparsers.add_parser("capture")
    capture.add_argument("hook", choices=HOOKS)
    capture.add_argument("--root", default=".")
    # Raw remote argv is deliberately not accepted as a CLI argument: process
    # arguments are observable by other processes. The caller supplies the
    # paths of controlled files containing the two values.
    capture.add_argument(
        "--remote-name-file",
        help="Caller-owned 0600 file; capture reads it and never deletes it.",
    )
    capture.add_argument(
        "--remote-location-file",
        help="Caller-owned 0600 file; capture reads it and never deletes it.",
    )
    capture.add_argument("--stdin-file")
    capture.add_argument("--framed-stdin", action="store_true")
    capture.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command in ("prepare", "run", "reconcile", "consume", "skip"):
            from . import hook_checks

            binding = _load_invocation_ref(args.root, args.invocation_ref)
            if args.command == "skip":
                if args.job is None:
                    raise HookEventError("hook_index_job_required")
                return int(not hook_checks.skip_index_job(binding, args.job))
            if args.hook == "pre-push":
                if args.command == "prepare":
                    if args.split_checks:
                        raise HookEventError("pre_push_split_checks_unsupported")
                    outcome = hook_checks.prepare_push(
                        args.root,
                        binding,
                        new_ref_baseline=args.new_ref_baseline,
                        effective_profiles=args.effective_profile,
                        require_plan=args.require_plan,
                        dagger_module=args.dagger_module,
                    )
                else:
                    if args.command == "run" and args.job is not None:
                        raise HookEventError("pre_push_index_job_unsupported")
                    handlers = {
                        "run": hook_checks.run_push_checks,
                        "reconcile": hook_checks.reconcile_push,
                        "consume": hook_checks.consume_push_result,
                    }
                    outcome = handlers[args.command](args.root, binding)
            else:
                if args.command == "prepare" and (
                    args.effective_profile or args.new_ref_baseline or args.require_plan
                ):
                    raise HookEventError("pre_commit_push_options_unsupported")
                handlers = {
                    "prepare": hook_checks.prepare,
                    "run": hook_checks.run_checks,
                    "reconcile": hook_checks.reconcile,
                    "consume": hook_checks.consume_index_result,
                }
                options = {}
                if args.command == "prepare":
                    options = {
                        "dagger_module": args.dagger_module,
                        "split_checks": args.split_checks,
                    }
                elif args.command == "run" and args.job is not None:
                    options = {"job_ref": args.job}
                outcome = handlers[args.command](args.root, binding, **options)
            if args.json:
                print(json.dumps(outcome, sort_keys=True))
            elif args.command not in ("prepare", "consume") or outcome["exit_code"]:
                hook_checks.print_result(outcome, binding.invocation_ref)
            return outcome["exit_code"]
        if args.command == "check":
            report = check_pre_commit(args.root, invocation_ref=args.invocation_ref)
            if args.json:
                print(json.dumps(report, sort_keys=True))
            else:
                print(report["invocation_ref"])
                sys.stdout.write(report["check"]["stdout"])
                sys.stderr.write(report["check"]["stderr"])
                if report["reason"] is not None:
                    print(report["reason"], file=sys.stderr)
            return report["exit_code"]
        capture_started_mono = time.perf_counter()
        capture_started_ns = time.time_ns()
        if args.hook == "pre-push":
            if args.framed_stdin:
                if (
                    args.remote_name_file
                    or args.remote_location_file
                    or args.stdin_file
                ):
                    raise HookEventError("pre_push_argument_sources_conflict")
                remote_name, remote_location, stdin_text = _read_pre_push_frame(
                    sys.stdin.buffer
                )
            else:
                remote_name = _read_remote_file(
                    args.remote_name_file, field="remote_name"
                )
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
        with suppress(OSError, HookEventError):
            _record_stage_timing(
                reference.invocation_path,
                invocation_ref=reference.invocation_ref,
                event_payload_ref=reference.event_payload_ref,
                hook_name=event.get("hook"),
                stage="capture",
                job_ref="capture",
                started_ns=capture_started_ns,
                completed_ns=time.time_ns(),
                elapsed_seconds=time.perf_counter() - capture_started_mono,
                status="ok",
            )
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(_capture_details(event, reference), sort_keys=True))
    else:
        # The default handoff identifies this invocation, not merely its
        # reusable payload content. The Event path is available in --json.
        print(reference.invocation_ref)
    return 0
