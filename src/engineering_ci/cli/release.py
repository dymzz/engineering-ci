"""Request verification or publication through the existing Release Workflow."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Also support IDE/direct-file execution without losing the package context.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = "engineering_ci.cli"

from ..release.version import normalize
from ._execution import (
    add_wait_arguments,
    dependency_error,
    invoke_execution,
    runtime_arguments,
)


def entrypoint(args):
    return invoke_execution(args, collect_outputs=False)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Examples: release.py verify --version v0.4.11 --ref BRANCH; "
        "release.py publish --version v0.4.11. Downloads: download.py --run RUN_ID --save-log.",
    )
    parser.add_argument("command", choices=("verify", "publish"))
    parser.add_argument("--version", required=True, help="Exact version, e.g. v0.4.11")
    parser.add_argument("--repo", default="dymzz/engineering-ci")
    parser.add_argument("--ref", default="main")
    parser.add_argument("--scenario", choices=("pass", "assertion"), default="pass")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--resume", nargs="?", const="last", metavar="RUN_ID")
    group.add_argument("--new-run", action="store_true")
    add_wait_arguments(parser)
    if not argv:
        parser.print_help()
        return 0
    options = parser.parse_args(argv)
    try:
        from ..execution.repository import REPO_PATTERN
    except ModuleNotFoundError as error:
        return dependency_error(error)
    try:
        version = normalize(options.version)
    except ValueError as error:
        parser.error(str(error))
    if not REPO_PATTERN.fullmatch(options.repo) or not options.ref:
        parser.error("A valid OWNER/REPO and nonempty --ref are required")
    publish = options.command == "publish"
    if publish and (options.ref != "main" or options.scenario != "pass"):
        parser.error("Publication requires --ref main and --scenario pass")
    args = [
        "--repo",
        options.repo,
        "--workflow",
        "release.yml",
        "--ref",
        options.ref,
        "--input",
        f"version=v{version}",
        "--input",
        f"publish={str(publish).lower()}",
        "--input",
        f"pytest_scenario={options.scenario}",
        *runtime_arguments(options),
    ]
    if options.resume is not None:
        args += ["--resume", options.resume]
    elif options.new_run:
        args.append("--new-run")
    print(
        f"Release Workflow: {options.command} v{version} on {options.repo}@{options.ref}; "
        "GitHub Actions determines the verification and publication result.",
        flush=True,
    )
    return entrypoint(args)


if __name__ == "__main__":
    raise SystemExit(main())
