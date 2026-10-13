"""Bounded JSON, canonical identities and atomic local documents."""

import hashlib
import json
import os
import tempfile
from pathlib import Path


class WorkflowError(ValueError):
    """A safe diagnostic that contains no raw tool response."""


def canonical(value):
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    except (TypeError, ValueError, RecursionError):
        raise WorkflowError("Document is not serializable finite JSON") from None


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def read(path):
    """Read one bounded JSON object, rejecting duplicate keys and non-finite numbers."""

    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise WorkflowError("Duplicate document field")
            value[key] = item
        return value

    def invalid_constant(_):
        raise WorkflowError("Non-finite JSON numbers are unsupported")

    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise WorkflowError("Workflow document exceeds 2 MiB")
        value = json.loads(
            raw, object_pairs_hook=pairs, parse_constant=invalid_constant
        )
        if not isinstance(value, dict):
            raise WorkflowError("Workflow document must be an object")
        return value
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        if isinstance(error, WorkflowError):
            raise
        raise WorkflowError("Cannot read a supported workflow document") from None


def write(path, value, *, replace=False):
    """Create exclusively, or atomically replace a private execution checkpoint."""
    data = canonical(value) + b"\n"
    if len(data) > 2 * 1024 * 1024:
        raise WorkflowError(
            "Workflow document exceeds 2 MiB; store large outputs as resource references"
        )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
