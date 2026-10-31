"""Invoke existing checks and release operations from GitHub Actions.

Workflow YAML owns the DAG. This module executes only the requested operation.
"""

import argparse
import sys


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation", choices=("release", "manifest", "security", "powershell")
    )
    if not args or args[0] in {"-h", "--help"}:
        parser.print_help()
        return 0
    operation = parser.parse_args(args[:1]).operation
    if operation == "release":
        from .release.publishing import main as invoke
    elif operation == "manifest":
        from .release.manifest import main as invoke
    elif operation == "powershell":
        from .powershell_analysis import main as invoke
    else:
        from .tools.security import main as invoke
    return invoke(args[1:])


if __name__ == "__main__":
    raise SystemExit(main())
