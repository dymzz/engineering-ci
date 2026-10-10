"""Unified entry point; Actions and noninteractive agents use the same commands."""

from __future__ import annotations

import argparse
import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        description="Engineering CI: local checks, release evidence and GitHub workflow outputs"
    )
    parser.add_argument(
        "command", choices=("checks", "release", "manifest", "version", "workflow")
    )
    if not argv or argv[0] in {"-h", "--help"}:
        parser.print_help()
        return 0
    namespace = parser.parse_args(argv[:1])
    args = argv[1:]
    if namespace.command == "checks":
        from ..checks import main as invoke
    elif namespace.command == "release":
        from ..release.publishing import main as invoke
    elif namespace.command == "manifest":
        from ..release.manifest import main as invoke
    elif namespace.command == "version":
        from ..release.version import main as invoke
    else:
        from .workflow import main as invoke
    return invoke(args)
