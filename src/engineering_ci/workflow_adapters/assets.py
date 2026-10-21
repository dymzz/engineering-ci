"""Export the Provider assets a consumer needs, without shipping a checkout."""

from __future__ import annotations

import hashlib
import json
import os
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

# Everything the Provider needs at runtime that is not the installed Python package:
# the Pipeline policy it owns, and the Dagger module the CLI loads.
POLICY = ("config/pipeline.toml",)
MODULE = (
    "dagger-module.toml",
    "dagger.lock",
    "dagger.toml",
    ".dagger/src",
    ".dagger/sdk",
    ".dagger/pyproject.toml",
    ".dagger/uv.lock",
)
ASSETS = POLICY + MODULE
MANIFEST = "provider-assets.json"
EXCLUDED = {"__pycache__", ".venv", ".ruff_cache", ".pytest_cache"}


class AssetError(Exception):
    """The Provider source does not contain the assets it must deliver."""


def _files(root, relative):
    """Every file under one declared asset path, as repository-relative posix paths."""
    path = root / relative
    if path.is_file():
        return [relative]
    if not path.is_dir():
        raise AssetError(f"Missing Provider asset: {relative}")
    found = []
    for directory, dirs, files in os.walk(path, followlinks=False):
        # Prune excluded directories; os.walk respects in-place mutation of dirs.
        dirs[:] = sorted(name for name in dirs if name not in EXCLUDED)
        # Defense in depth: also drop any path component under an excluded name.
        rel_dir = Path(directory).relative_to(root)
        if any(part in EXCLUDED for part in rel_dir.parts):
            continue
        for name in files:
            if name.endswith((".pyc", ".pyo")):
                continue
            found.append(Path(directory, name).relative_to(root).as_posix())
    if not found:
        raise AssetError(f"Empty Provider asset: {relative}")
    return sorted(found)


def collect(root):
    """List the asset files this Provider source delivers, in a stable order."""
    root = Path(root).resolve()
    listed = []
    for relative in ASSETS:
        listed.extend(_files(root, relative))
    return sorted(set(listed))


def digest(root, files):
    """Hash the delivered bytes so a consumer can verify what they unpacked."""
    root = Path(root).resolve()
    records = {}
    for relative in files:
        payload = (root / relative).read_bytes()
        records[relative] = hashlib.sha256(payload).hexdigest()
    return records


def manifest(root, files=None):
    """Describe the delivery: version, entry points and a hash per asset file."""
    root = Path(root).resolve()
    files = collect(root) if files is None else files
    try:
        installed = version("engineering-ci")
    except PackageNotFoundError:
        installed = None
    records = digest(root, files)
    total = hashlib.sha256(
        json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "schema_version": 1,
        "kind": "provider-assets",
        "package": "engineering-ci",
        "package_version": installed,
        "entry_points": {
            "vesryn.adapters": {
                "engineering-ci": "engineering_ci.workflow_adapters.checks:catalog",
                "engineering-ci-dagger": (
                    "engineering_ci.workflow_adapters.provider:catalog"
                ),
            }
        },
        "policy": list(POLICY),
        "module": list(MODULE),
        "files": records,
        "sha256": total,
    }


def export(root, destination):
    """Write the Provider assets and their manifest into a new directory.

    The result is an install location, not a checkout: it carries no .git, no
    development virtualenv and no test fixtures. The caller unpacks it wherever the
    environment allows and points the Provider at it.
    """
    root, destination = Path(root).resolve(), Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise AssetError("Asset destination must be a new or empty directory")
    files = collect(root)
    destination.mkdir(parents=True, exist_ok=True)
    for relative in files:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / relative).read_bytes())
    document = manifest(root, files)
    (destination / MANIFEST).write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    return document