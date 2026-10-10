#!/usr/bin/env python3
"""Keep engineering-ci package metadata aligned with a release tag.

Usage:
  python scripts/release_version.py set v0.4.3
  python scripts/release_version.py check
  python scripts/release_version.py show
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECTS = (ROOT, ROOT / ".dagger")
VERSION_RE = re.compile(r"^v?(\d+\.\d+\.\d+)$")


def normalize(raw: str) -> str:
    match = VERSION_RE.fullmatch(raw)
    if not match:
        raise ValueError(f"Expected vMAJOR.MINOR.PATCH, got {raw!r}")
    return match.group(1)


def versions() -> dict[str, str]:
    found: dict[str, str] = {}
    for project in PROJECTS:
        label = str(project.relative_to(ROOT)) or "."
        for name in ("pyproject.toml", "uv.lock"):
            data = tomllib.loads((project / name).read_text(encoding="utf-8"))
            if name == "pyproject.toml":
                if data["project"]["name"] != "engineering-ci":
                    raise ValueError(f"Unexpected project name in {label}/{name}")
                version = data["project"]["version"]
            else:
                matches = [
                    pkg["version"]
                    for pkg in data["package"]
                    if pkg["name"] == "engineering-ci"
                ]
                if len(matches) != 1:
                    raise ValueError(
                        f"Expected one engineering-ci package in {label}/{name}"
                    )
                version = matches[0]
            found[f"{label}/{name}"] = version
    return found


def checked_version() -> str:
    found = versions()
    unique = set(found.values())
    if len(unique) != 1:
        raise ValueError(f"Release version mismatch: {found}")
    return next(iter(unique))


def set_version(raw: str) -> None:
    version = normalize(raw)
    # The Dagger SDK is generated only by `dagger develop`; running
    # `uv lock` against .dagger independently fails without its local sdk/.
    # Update only the first-party version metadata in all four files.
    for project in PROJECTS:
        project_file = project / "pyproject.toml"
        lock_file = project / "uv.lock"
        project_text = project_file.read_text(encoding="utf-8")
        new_project, count = re.subn(
            r'(?m)^(version\s*=\s*")[^"]+(")$',
            lambda m: m.group(1) + version + m.group(2),
            project_text,
            count=1,
        )
        if count != 1:
            raise ValueError(f"Could not locate project version in {project_file}")
        lock_text = lock_file.read_text(encoding="utf-8")
        new_lock, count = re.subn(
            r'(?m)^(name = "engineering-ci"\nversion = ")[^"]+(")$',
            lambda m: m.group(1) + version + m.group(2),
            lock_text,
            count=1,
        )
        if count != 1:
            raise ValueError(f"Could not locate lock version in {lock_file}")
        project_file.write_text(new_project, encoding="utf-8", newline="\n")
        lock_file.write_text(new_lock, encoding="utf-8", newline="\n")
    actual = checked_version()
    if actual != version:
        raise ValueError(f"Expected {version}, got {actual}")
    print(f"Aligned both pyproject.toml and uv.lock files to v{version}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("set", "check", "show"))
    parser.add_argument("version", nargs="?")
    args = parser.parse_args(argv)
    try:
        if args.command == "set":
            if args.version is None:
                parser.error("set requires a version")
            set_version(args.version)
        else:
            if args.version is not None:
                parser.error(f"{args.command} takes no version")
            version = checked_version()
            if args.command == "show":
                print(version)
            else:
                print(f"PASS: all four project version fields match v{version}")
        return 0
    except (ValueError, KeyError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
