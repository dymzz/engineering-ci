"""Native containers for this repository's Checks; Dagger owns their graph/cache."""

import dagger
from dagger import dag

SOURCE_EXCLUDES = [
    ".git",
    ".venv",
    "**/.venv",
    ".test-tools",
    ".test-deps",
    ".log",
    ".artifacts",
    "**/__pycache__",
    "**/.pytest_cache",
    "**/.ruff_cache",
    ".coverage*",
    "core-junit.xml",
    "core-coverage.xml",
    "core-coverage.json",
    ".env",
    ".env.*",
    ".secrets",
    ".aws",
    ".ssh",
    ".docker",
    ".codex",
    ".claude",
    ".agents",
    "config/ghwf.local.toml",
    "get_vesryn-log_docs.ps1",
    "guides",
    "docs/**/*.md",
    "AGENTS.md",
    ".dagger/AGENTS.md",
    ".agent",
    "scripts/*.md",
]


def environment(source: dagger.Directory, *, tests: bool = False) -> dagger.Container:
    """Install from manifest/lock before adding source; test-only OS tools stay separate."""
    base = dag.container().from_("ghcr.io/astral-sh/uv:python3.14-trixie-slim")
    if tests:
        base = base.with_exec(["apt-get", "update", "-qq"]).with_exec(
            [
                "apt-get",
                "install",
                "-y",
                "-qq",
                "--no-install-recommends",
                "git",
                "aria2",
                "gh",
            ]
        )
    manifests = (
        dag.directory()
        .with_file("pyproject.toml", source.file("pyproject.toml"))
        .with_file("uv.lock", source.file("uv.lock"))
    )
    return (
        base.with_mounted_cache(
            "/root/.cache/uv", dag.cache_volume("engineering-ci-uv-python3.14")
        )
        .with_env_variable("UV_LINK_MODE", "copy")
        .with_env_variable("UV_PYTHON_DOWNLOADS", "never")
        .with_env_variable("PYTHONPATH", "/workspace/src")
        .with_directory("/workspace", manifests)
        .with_workdir("/workspace")
        .with_exec(["uv", "sync", "--locked", "--no-install-project", "--group", "dev"])
        .with_directory("/workspace", source, exclude=SOURCE_EXCLUDES)
    )
