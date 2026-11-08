"""User commands; Workflow operations have a separate module entry point."""

from __future__ import annotations

import argparse
import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        description="Engineering CI: Release Workflow, version metadata and Run downloads; checks use dagger check"
    )
    parser.add_argument(
        "command",
        choices=(
            "release",
            "download",
            "version",
            "policy",
            "select",
            "hygiene",
            "sources",
        ),
    )
    if not argv or argv[0] in {"-h", "--help"}:
        parser.print_help()
        return 0
    namespace = parser.parse_args(argv[:1])
    args = argv[1:]
    if namespace.command == "release":
        from .release import main as invoke
    elif namespace.command == "version":
        from ..release.version import main as invoke
    elif namespace.command == "policy":
        from ..security_policy import main as invoke
    elif namespace.command == "select":
        from eci_core.selection import main as invoke
    elif namespace.command == "hygiene":
        from eci_core.hygiene import main as invoke
    elif namespace.command == "sources":
        from eci_core.sources import main as invoke
    else:
        from .download import main as invoke
    return invoke(args)
