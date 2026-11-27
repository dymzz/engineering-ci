"""Authoritative input contract for this repository's five native CI checks.

The Dagger module builds its container source excludes from this module and
``engineering_ci.ci_applicability`` decides which check a candidate change set
must run from the same declarations. This is this repository's own check-input
contract: it is not a generic consumer interface, not a test selector (#66/#165
remain the owners of test selection) and not evidence admission (#63).

Ruff 0.16.10 was measured for the actual discovery contract (2026-11-27):
``ruff check .`` resolves ``.py``/``.pyi``/``.ipynb`` plus ``pyproject.toml``/
``ruff.toml``/``.ruff.toml`` and honours ``.gitignore``; it does not lint
Markdown. ``ruff format --check .`` additionally formats Markdown code blocks.
"""

from __future__ import annotations

from dataclasses import dataclass

CONTRACT_VERSION = 1
KIND = "engineering-ci-ci-check-inputs"

SOURCE_NOISE = [
    ".git",
    ".venv",
    "**/.venv",
    "__pycache__",
    "**/__pycache__",
    ".ruff_cache",
    "**/.ruff_cache",
]

WORKSPACE_SCRATCH = [
    ".pytest_cache",
    "**/.pytest_cache",
    ".mypy_cache",
    "**/.mypy_cache",
    ".pyright",
    "**/.pyright",
    ".tach",
    "**/.tach",
    ".lycheecache",
    "**/.lycheecache",
    ".DS_Store",
    "Thumbs.db",
    ".test-tools",
    "**/.test-tools",
    ".test-deps",
    "**/.test-deps",
    ".tmp",
    "**/.tmp",
    ".log",
    "**/.log",
    ".work",
    "**/.work",
    ".tools",
    "**/.tools",
    ".weaver-74-probe",
    "**/.weaver-74-probe",
    ".artifacts",
    "**/.artifacts",
    "dist",
    "build",
    "*.egg-info",
    "**/*.egg-info",
    "htmlcov",
    ".coverage*",
    "core-junit.xml",
    "core-coverage.xml",
    "core-coverage.json",
    ".codex",
    ".claude",
    ".agents",
    ".idea",
    ".vscode",
]

HOST_SECRET_EXCLUDES = [
    ".env",
    ".env.*",
    ".secrets",
    ".aws",
    ".ssh",
]

HOST_LOCAL_EXCLUDES = [
    ".docker",
    "**/*.local.toml",
]

WEAVER_DEFAULT_EXCLUDES = [
    *SOURCE_NOISE,
    ".env",
    ".secrets",
    ".aws",
    ".ssh",
]

SOURCE_EXCLUDES = [
    *SOURCE_NOISE,
    *HOST_SECRET_EXCLUDES,
    *HOST_LOCAL_EXCLUDES,
    *WORKSPACE_SCRATCH,
    "get_vesryn-log_docs.ps1",
    "guides",
    "design",
    "docs/**/*.md",
    "AGENTS.md",
    ".dagger/AGENTS.md",
    ".agent",
    "scripts/*.md",
]

# Paths that may not be read from the Dagger repository source. The predicate
# stays a deliberate subset of SOURCE_EXCLUDES: every entry is asserted against
# that list by the contract tests, while an excluded-but-unlisted path (for
# example ``.env``) merely runs the checks instead of being skipped.
DOCUMENTATION_FILES = (
    "AGENTS.md",
    ".dagger/AGENTS.md",
    "scripts/README.md",
    "scripts/rules.md",
)
DOCUMENTATION_PREFIXES = (
    ".agent/",
    "design/",
    "guides/",
)
DOCUMENTATION_MARKDOWN_PREFIXES = ("docs/",)

# Toolchain, dependency and workflow inputs invalidate the tool, environment or
# orchestration of every check, so they escalate to all of them.
TOOLCHAIN_FILES = (
    ".gitattributes",
    ".gitignore",
    ".dagger/pyproject.toml",
    ".dagger/uv.lock",
    "dagger-module.toml",
    "dagger.lock",
    "dagger.toml",
    "pyproject.toml",
    "uv.lock",
)
TOOLCHAIN_PREFIXES = (
    ".github/workflows/",
    ".dagger/sdk/",
    ".dagger/src/engineering_ci_module/",
)

# Changing this contract, the CI decision or its verification consumer also
# changes how every candidate is classified. The workflow hardcodes the same
# paths so a candidate's own classification rules cannot drop them.
SELF_VERIFICATION_FILES = (
    ".github/workflows/ci.yml",
    "src/engineering_ci/ci_applicability.py",
    "src/engineering_ci/ci_check_inputs.py",
    "tests/test_ci_applicability.py",
)
SELF_VERIFICATION_PREFIXES = (".dagger/src/engineering_ci_module/",)


@dataclass(frozen=True)
class CheckInputs:
    """One existing native check and the repository inputs it actually reads."""

    name: str
    job: str
    dagger_check: str
    suffixes: tuple[str, ...] = ()
    names: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    prefixes: tuple[str, ...] = ()
    source_scope: bool = False

    @property
    def github_output(self):
        return self.name.replace("-", "_")


CI_CHECKS = (
    CheckInputs(
        name="lint",
        job="python-ci",
        dagger_check="lint",
        suffixes=(".py", ".pyi", ".ipynb"),
        names=("pyproject.toml", "ruff.toml", ".ruff.toml", ".gitignore"),
    ),
    CheckInputs(
        name="format-check",
        job="python-ci",
        dagger_check="format-check",
        # Ruff 0.16.10 formats Markdown code blocks but does not lint them.
        suffixes=(".py", ".pyi", ".ipynb", ".md"),
        names=("pyproject.toml", "ruff.toml", ".ruff.toml", ".gitignore"),
    ),
    CheckInputs(
        name="tests",
        job="sync-tool-tests",
        dagger_check="tests",
        source_scope=True,
    ),
    CheckInputs(
        name="version",
        job="version-metadata",
        dagger_check="version",
        files=(
            ".dagger/pyproject.toml",
            ".dagger/uv.lock",
            "pyproject.toml",
            "src/engineering_ci/__init__.py",
            "src/engineering_ci/_paths.py",
            "uv.lock",
        ),
        prefixes=("src/engineering_ci/release/",),
    ),
    CheckInputs(
        name="lock",
        job="version-metadata",
        dagger_check="lock",
        names=("pyproject.toml",),
        files=(
            "uv.lock",
            ".dagger/uv.lock",
            "pyproject.toml",
            ".dagger/pyproject.toml",
        ),
    ),
)


def _is_under(path, prefixes):
    return any(path.startswith(prefix) for prefix in prefixes)


def is_source_excluded_path(path):
    """Whether the Dagger checks cannot read the path inside their container."""
    return (
        path in DOCUMENTATION_FILES
        or _is_under(path, DOCUMENTATION_PREFIXES)
        or (path.endswith(".md") and _is_under(path, DOCUMENTATION_MARKDOWN_PREFIXES))
    )


def is_toolchain_path(path):
    return path in TOOLCHAIN_FILES or _is_under(path, TOOLCHAIN_PREFIXES)


def is_self_verification_path(path):
    return path in SELF_VERIFICATION_FILES or _is_under(
        path, SELF_VERIFICATION_PREFIXES
    )


def matches_check(path, check):
    """Whether the path is an input the check's native tool actually reads."""
    if check.source_scope:
        return True
    name = path.rsplit("/", 1)[-1]
    return (
        path.endswith(check.suffixes)
        or name in check.names
        or path in check.files
        or (check.prefixes and path.startswith(check.prefixes))
    )
