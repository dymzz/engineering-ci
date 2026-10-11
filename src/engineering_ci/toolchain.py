"""Tool versions used by the local checks and Actions bootstrap."""

UV_VERSION = "0.12.23"
RUFF_VERSION = "0.16.10"
PYTHON_VERSION = "3.14"


def ruff_command(*arguments):
    # Tool bootstrap ignores machine-specific uv config; root lock checks retain theirs.
    return ["uvx", "--no-config", f"ruff=={RUFF_VERSION}", *arguments]
