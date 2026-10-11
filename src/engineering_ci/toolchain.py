"""Tool versions used by the local checks and Actions bootstrap."""

from .pipeline import PIPELINE

UV_VERSION = PIPELINE["toolchain"]["uv"]
RUFF_VERSION = PIPELINE["toolchain"]["ruff"]
PYTHON_VERSION = PIPELINE["toolchain"]["python"]


def ruff_command(*arguments):
    # Tool bootstrap ignores machine-specific uv config; root lock checks retain theirs.
    return ["uvx", "--no-config", f"ruff=={RUFF_VERSION}", *arguments]
