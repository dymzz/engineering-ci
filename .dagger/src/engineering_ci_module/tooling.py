"""Pinned native tool assets used by Dagger checks."""

from engineering_ci import weaver_tool

# Re-export the single engineering_ci.weaver_tool authority for Dagger download.
WEAVER_VERSION = weaver_tool.VERSION
WEAVER_LINUX_SHA256 = weaver_tool.LINUX_SHA256
WEAVER_LINUX_ASSET = weaver_tool.LINUX_ASSET

TACH_VERSION = "0.35.3"
LYCHEE_VERSION = "0.24.2"
LYCHEE_ASSET = (
    "https://github.com/lycheeverse/lychee/releases/download/"
    f"lychee-v{LYCHEE_VERSION}/lychee-x86_64-unknown-linux-gnu.tar.gz"
)
UV_IMAGE = "ghcr.io/astral-sh/uv:python3.14-trixie-slim"

# Check-specific Source construction: remove VCS/virtualenv/cache noise only.
SOURCE_NOISE = [
    ".git",
    ".venv",
    "**/.venv",
    "__pycache__",
    "**/__pycache__",
    ".ruff_cache",
    "**/.ruff_cache",
]
