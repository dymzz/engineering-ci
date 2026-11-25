"""Pinned native tool assets used by Dagger checks."""

from engineering_ci import semconv_registry, weaver_tool

# Re-export the single engineering_ci.weaver_tool authority for Dagger download.
WEAVER_VERSION = weaver_tool.VERSION
WEAVER_LINUX_SHA256 = weaver_tool.LINUX_SHA256
WEAVER_LINUX_ASSET = weaver_tool.LINUX_ASSET

# Re-export the pinned official SemConv Registry identity. The Weaver container
# receives it as ECI_SEMCONV_OFFICIAL_SOURCE and refuses to report
# official_registry_verified unless the Registry it read reproduces this pin.
SEMCONV_OFFICIAL_SOURCE = dict(semconv_registry.RECORD)

TACH_VERSION = "0.35.3"
LYCHEE_VERSION = "0.24.2"
LYCHEE_ASSET = (
    "https://github.com/lycheeverse/lychee/releases/download/"
    f"lychee-v{LYCHEE_VERSION}/lychee-x86_64-unknown-linux-gnu.tar.gz"
)
UV_IMAGE = "ghcr.io/astral-sh/uv:python3.14-trixie-slim"

# Check-specific Source construction: remove historical VCS/virtualenv/cache noise only.
# Host secret/local/scratch/cache excludes belong in repository.py so public
# standalone entrypoints (e.g. python_test, python_lint, python_pyright, tach_check,
# lychee_check, weaver_report) never drop declared consumer inputs by directory name.
SOURCE_NOISE = [
    ".git",
    ".venv",
    "**/.venv",
    "__pycache__",
    "**/__pycache__",
    ".ruff_cache",
    "**/.ruff_cache",
]

# Root Git Context (controlled ODB/Index attached beside a verified Git Tree for
# Git-native checks). Source Content ID never covers it, so ordinary consumers of
# a verified tree receive every tree member without this root entry.
GIT_CONTEXT_EXCLUDES = [".git"]
