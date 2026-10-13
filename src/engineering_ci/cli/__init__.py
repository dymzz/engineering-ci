"""User commands; Workflow operations have a separate module entry point."""

from __future__ import annotations

import argparse
import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        description="Engineering CI: local checks, Release Workflow, Run downloads, trace archive and offline mining"
    )
    parser.add_argument(
        "command",
        choices=(
            "checks",
            "release",
            "download",
            "version",
            "trace",
            "mine",
            "workflow",
        ),
    )
    if not argv or argv[0] in {"-h", "--help"}:
        parser.print_help()
        return 0
    namespace = parser.parse_args(argv[:1])
    args = argv[1:]
    if namespace.command == "checks":
        if any(arg == "--binding" or arg.startswith("--binding=") for arg in args):
            from .checks_binding import main as invoke
        else:
            from ..checks import main as invoke
    elif namespace.command == "release":
        from .release import main as invoke
    elif namespace.command == "version":
        from ..release.version import main as invoke
    elif namespace.command == "trace":
        from .trace import main as invoke
    elif namespace.command == "mine":
        from .mine import main as invoke
    elif namespace.command == "workflow":
        from .workflow import main as invoke
    else:
        from .download import main as invoke
    return invoke(args)
