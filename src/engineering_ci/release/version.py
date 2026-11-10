"""Keep engineering-ci package metadata aligned with a release tag.

Usage:
  python -m engineering_ci.release.version set v0.4.11
  python -m engineering_ci.release.version check
  python -m engineering_ci.release.version show
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib

from .._paths import ROOT

PROJECTS = (ROOT, ROOT / ".dagger")
PROJECT_NAMES = {ROOT: "engineering-ci", ROOT / ".dagger": "engineering-ci-module"}
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
        expected = PROJECT_NAMES[project]
        for name in ("pyproject.toml", "uv.lock"):
            data = tomllib.loads((project / name).read_text(encoding="utf-8"))
            if name == "pyproject.toml":
                if data["project"]["name"] != expected:
                    raise ValueError(f"Unexpected project name in {label}/{name}")
                version = data["project"]["version"]
            else:
                matches = [
                    pkg["version"] for pkg in data["package"] if pkg["name"] == expected
                ]
                if len(matches) != 1:
                    raise ValueError(
                        f"Expected one {expected} package in {label}/{name}"
                    )
                version = matches[0]
                if project != ROOT:
                    shared = [
                        pkg["version"]
                        for pkg in data["package"]
                        if pkg["name"] == PROJECT_NAMES[ROOT]
                    ]
                    if len(shared) != 1:
                        raise ValueError(
                            f"Expected one shared {PROJECT_NAMES[ROOT]} package in {label}/{name}"
                        )
                    found[f"{label}/{name}:{PROJECT_NAMES[ROOT]}"] = shared[0]
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
    # SDK/lock generation uses the native Dagger API; independently resolving
    # .dagger requires that generated local sdk/ to be present.
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
        package_names = {PROJECT_NAMES[project]}
        if project != ROOT:
            package_names.add(PROJECT_NAMES[ROOT])
        new_lock = lock_text
        for package_name in sorted(package_names):
            new_lock, count = re.subn(
                rf'(?m)^(name = "{re.escape(package_name)}"\nversion = ")[^"]+(")$',
                lambda m: m.group(1) + version + m.group(2),
                new_lock,
                count=1,
            )
            if count != 1:
                raise ValueError(
                    f"Could not locate {package_name} lock version in {lock_file}"
                )
        project_file.write_text(new_project, encoding="utf-8", newline="\n")
        lock_file.write_text(new_lock, encoding="utf-8", newline="\n")
    actual = checked_version()
    if actual != version:
        raise ValueError(f"Expected {version}, got {actual}")
    print(f"Aligned project metadata and first-party lock entries to v{version}")


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
                print(f"PASS: all project version fields match v{version}")
        return 0
    except (ValueError, KeyError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
