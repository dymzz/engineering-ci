"""Select a lock-specific native uv package cache; never cache the project venv."""

import hashlib
import os
from pathlib import Path
import re
import sys


def cache_environment(python_version, lockfile=Path("uv.lock"), *, environment=None):
    if not re.fullmatch(r"3\.(10|11|12|13|14)", python_version):
        raise ValueError("Unsupported cache Python version")
    try:
        digest = hashlib.sha256(lockfile.read_bytes()).hexdigest()
    except FileNotFoundError:
        # Let uv itself reject the missing lock with its original exit status.
        digest = "missing-lock"
    env = dict(os.environ if environment is None else environment)
    env["UV_CACHE_DIR"] = (
        f"/root/.cache/engineering-ci-uv/python{python_version}/{digest}"
    )
    env["UV_LINK_MODE"] = "copy"
    return env


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        raise ValueError("Expected one validated Python version")
    version = args[0]
    # Replace this process so uv's exit code/signals remain execution facts.
    # Refresh metadata, including dynamic local builds, when source changed but
    # Dagger imports retained the same filesystem timestamps. Native uv still
    # reuses valid downloaded archives and handles cache refresh failures.
    os.execvpe(
        "uv",
        ["uv", "sync", "--locked", "--refresh", "--python", version],
        cache_environment(version),
    )


if __name__ == "__main__":
    main()
