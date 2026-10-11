"""Invoke existing checks and release operations from GitHub Actions.

Workflow YAML owns the DAG. This module executes only the requested operation.
"""

import argparse
import os
import sys


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation", choices=("checks", "release", "manifest", "security")
    )
    if not args or args[0] in {"-h", "--help"}:
        parser.print_help()
        return 0
    operation = parser.parse_args(args[:1]).operation
    if operation == "checks":
        from .checks import main as invoke

        check_args = args[1:]
        if "--coverage-from-env" in check_args:
            # workflow_call has a boolean input; push/PR supply an empty value.
            if (
                check_args.count("--coverage-from-env") != 1
                or "--coverage" in check_args
            ):
                parser.error("Use one explicit coverage source")
            coverage = os.environ.get("EXPORT_COVERAGE", "")
            if coverage not in ("", "false", "true"):
                parser.error("EXPORT_COVERAGE must be a GitHub boolean or empty")
            check_args = [
                value for value in check_args if value != "--coverage-from-env"
            ]
            if coverage == "true":
                check_args.append("--coverage")
        # YAML has already selected Linux and installed the native CI toolchain.
        return invoke(check_args, executor="native")
    elif operation == "release":
        from .release.publishing import main as invoke
    elif operation == "manifest":
        from .release.manifest import main as invoke
    else:
        from .tools.security import main as invoke
    return invoke(args[1:])


if __name__ == "__main__":
    raise SystemExit(main())
