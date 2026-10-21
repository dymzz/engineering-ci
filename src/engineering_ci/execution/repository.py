"""Existing origin detection and repository cache rules."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from .github import gh
from .store import atomic_json

REPO_PATTERN = re.compile(r"[\w.-]+/[\w.-]+\Z")


def git_value(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
        )
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def repo_from_origin(origin: str) -> str | None:
    scp = re.fullmatch("(?:[^@]+@)?github\\.com:(.+)", origin)
    if scp:
        path = scp.group(1)
    else:
        parsed = urlsplit(origin)
        if parsed.hostname != "github.com":
            return None
        path = parsed.path
    path = path.strip("/")
    path = path.removesuffix(".git")
    return path if REPO_PATTERN.fullmatch(path) else None


def default_cache_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "gh-workflow-artifacts"


def default_repo_cache() -> Path:
    return default_cache_dir() / "repo-cache.json"


def read_repo_cache(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (
            isinstance(data, dict)
            and data.get("version") == 1
            and isinstance(data.get("repos"), dict)
        ):
            return data["repos"]
    except (OSError, ValueError):
        pass
    return {}


def write_repo_cache(path: Path, entries: dict) -> None:
    try:
        atomic_json(path, {"version": 1, "repos": entries})
    except OSError as exc:
        print(f"Warning: cannot save repository cache: {exc}", file=sys.stderr)


def resolve_repo(
    *, refresh: bool = False, cache_path: Path | None = None
) -> tuple[str, str]:
    root = git_value("rev-parse", "--show-toplevel")
    if root is None:
        raise RuntimeError("Not in a Git repository; specify --repo OWNER/REPO.")
    origin = git_value("remote", "get-url", "origin") or ""
    fingerprint = hashlib.sha256(origin.encode("utf-8")).hexdigest()
    cache_path = cache_path or default_repo_cache()
    cache = read_repo_cache(cache_path)
    key = str(Path(root).resolve())
    record = cache.get(key)
    if (
        not refresh
        and isinstance(record, dict)
        and (record.get("origin_sha256") == fingerprint)
        and isinstance(record.get("repo"), str)
        and REPO_PATTERN.fullmatch(record["repo"])
    ):
        return (record["repo"], "cache")
    repo = repo_from_origin(origin)
    source = "git origin"
    if repo is None:
        try:
            response = json.loads(gh("repo", "view", "--json", "nameWithOwner"))
            repo = response.get("nameWithOwner")
        except (RuntimeError, ValueError) as exc:
            raise RuntimeError(
                "Cannot resolve repository; specify --repo OWNER/REPO."
            ) from exc
        if not isinstance(repo, str) or not REPO_PATTERN.fullmatch(repo):
            raise RuntimeError("`gh repo view` did not return a valid OWNER/REPO")
        source = "gh repo view"
    cache[key] = {"repo": repo, "origin_sha256": fingerprint}
    write_repo_cache(cache_path, cache)
    return (repo, source)
