"""Request verification or publication through the existing Release Workflow."""

from __future__ import annotations

import argparse

from ..execution.cli import entrypoint
from ..execution.repository import REPO_PATTERN
from ..release.version import normalize
from ._execution import add_runtime_arguments, runtime_arguments


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify", "publish"))
    parser.add_argument("--version", required=True, help="Exact version, e.g. v0.4.11")
    parser.add_argument("--repo", default="dymzz/engineering-ci")
    parser.add_argument("--ref", default="main")
    parser.add_argument("--scenario", choices=("pass", "assertion"), default="pass")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--resume", nargs="?", const="last", metavar="RUN_ID")
    group.add_argument("--new-run", action="store_true")
    add_runtime_arguments(parser)
    parser.set_defaults(out="release-artifacts", save_log=True)
    options = parser.parse_args(argv)
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
