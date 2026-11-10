"""Pinned native tool assets used by Dagger checks."""

TACH_VERSION = "0.35.3"
LYCHEE_VERSION = "0.24.2"
WEAVER_VERSION = "0.28.0"
WEAVER_LINUX_SHA256 = "9878e2d56ad82ae441b0b527377a4f6001ae19f4af362fb0a4d951884f59ab61"
WEAVER_LINUX_ASSET = (
    "https://github.com/open-telemetry/weaver/releases/download/"
    f"v{WEAVER_VERSION}/weaver-x86_64-unknown-linux-gnu.tar.xz"
)
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
