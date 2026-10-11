"""Linux execution of the repository's shared PR profile, separate from fixture checks."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib

import dagger
from dagger import dag

SOURCE_EXCLUDES = [
    ".git",
    ".test-tools",
    ".test-deps",
    ".venv",
    "**/.venv",
    ".dagger/sdk",
    "**/__pycache__",
    "**/.pytest_cache",
    "**/.ruff_cache",
    ".env",
    ".env.*",
    ".secrets",
    ".aws",
    ".ssh",
    ".docker",
    ".codex",
    ".agents",
    "config/ghwf.local.toml",
    "core-junit.xml",
    "core-coverage.xml",
    "core-coverage.json",
    ".coverage",
    ".coverage.*",
]


async def container(source, executable_paths):
    """Prepare pinned uv/Python plus the native process dependencies used by core tests."""
    data = tomllib.loads(await source.file("config/pipeline.toml").contents())
    uv, python = data["toolchain"]["uv"], data["toolchain"]["python"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", uv) or not re.fullmatch(r"3\.\d+", python):
        raise ValueError("Invalid container toolchain")
    image = dag.container().from_(f"python:{python}-slim")
    base = (
        image.with_exec(["apt-get", "update", "-qq"], disable_dagger_in_dagger=True)
        .with_exec(
            [
                "apt-get",
                "install",
                "-y",
                "-qq",
                "--no-install-recommends",
                "git",
                "gh",
                "bash",
                "aria2",
                "ca-certificates",
            ],
            disable_dagger_in_dagger=True,
        )
        .with_exec(
            ["python", "-m", "pip", "install", "--no-cache-dir", f"uv=={uv}"],
            disable_dagger_in_dagger=True,
        )
        .with_env_variable("UV_PYTHON_DOWNLOADS", "never")
        .with_env_variable("UV_NO_MANAGED_PYTHON", "1")
        .with_env_variable("PYTHONPATH", "/workspace/src")
        .with_env_variable("UV_CACHE_DIR", "/root/.cache/engineering-ci-pr-uv")
        .with_mounted_cache(
            "/root/.cache/engineering-ci-pr-uv",
            dag.cache_volume(f"engineering-ci-pr-uv-{uv}-python{python}"),
        )
    )
    runtime = {
        "engine_version": await dag.version(),
        "image_ref": await image.image_ref(),
        "container_sha256": hashlib.sha256(
            (await base.rootfs().digest()).encode()
        ).hexdigest(),
    }
    result = base.with_directory(
        "/workspace", source, exclude=SOURCE_EXCLUDES
    ).with_workdir("/workspace")
    paths = json.loads(executable_paths)
    if not isinstance(paths, list) or any(
        not isinstance(path, str) or path.startswith("/") or ".." in path.split("/")
        for path in paths
    ):
        raise ValueError("Invalid executable input paths")
    # Windows does not expose POSIX execute bits; preserve the repository's Git modes.
    if paths:
        result = result.with_exec(
            ["chmod", "+x", "--", *paths], disable_dagger_in_dagger=True
        )
    return result, json.dumps(runtime, sort_keys=True)


async def environment(source, request, executable_paths):
    prepared, runtime = await container(source, executable_paths)
    result = prepared.with_exec(
        [
            "python",
            "-m",
            "engineering_ci.checks_container",
            "prepare",
            "--request",
            request,
            "--runtime",
            runtime,
        ],
        expect=dagger.ReturnType.ANY,
        disable_dagger_in_dagger=True,
    )
    return await result.file("/evidence/environment.json").contents()


async def artifacts(source, request, executable_paths):
    prepared, runtime = await container(source, executable_paths)
    result = prepared.with_directory("/evidence", dag.directory()).with_exec(
        [
            "python",
            "-m",
            "engineering_ci.checks_container",
            "execute",
            "--request",
            request,
            "--runtime",
            runtime,
        ],
        expect=dagger.ReturnType.ANY,
        disable_dagger_in_dagger=True,
        redirect_stdout="/evidence/stdout.log",
        redirect_stderr="/evidence/stderr.log",
    )
    return result.directory("/evidence")
