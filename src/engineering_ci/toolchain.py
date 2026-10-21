"""Tool versions used by the local checks and Actions bootstrap."""

import re

from .check_operations import ruff_command as registered_ruff_command
from .pipeline import PIPELINE

# Overwritten by publish(); declared so the names exist before a policy is bound.
UV_VERSION: str | None = None
RUFF_VERSION: str | None = None
PYTHON_VERSION: str | None = None


def publish(policy):
    """Publish the pinned toolchain for one validated Pipeline, or clear it."""
    global PIPELINE, UV_VERSION, RUFF_VERSION, PYTHON_VERSION

    PIPELINE = policy
    if policy is None:
        values = (None, None, None)
    else:
        toolchain = policy["toolchain"]
        values = (toolchain["uv"], toolchain["ruff"], toolchain["python"])
    UV_VERSION, RUFF_VERSION, PYTHON_VERSION = values


publish(PIPELINE)


def ruff_command(*arguments):
    # Tool bootstrap ignores machine-specific uv config; root lock checks retain theirs.
    return registered_ruff_command(PIPELINE, *arguments)


def dagger_cli_version(output):
    """Read only the version from legacy or 1.0 CLI output."""
    version = r"[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?"
    legacy = re.fullmatch(rf"dagger v({version})(?: [^\r\n]*)?", output.strip())
    if legacy:
        return legacy[1]
    fields = [
        line.strip()
        for line in output.splitlines()
        if line.strip().startswith("version:")
    ]
    match = (
        re.fullmatch(rf"version:[ \t]+v({version})", fields[0])
        if len(fields) == 1
        else None
    )
    if not match:
        raise ValueError("Dagger CLI version unavailable")
    return match[1]
