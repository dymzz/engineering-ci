"""Compatibility bridge for the unreleased PR #25 binding prototype."""

import argparse
import sys
from pathlib import Path

from .. import checks
from ..mining.bindings import read
from ..tracing.store import ArchiveError


def main(argv=None):
    """Keep the old host CLI usable without coupling the original checks library."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checks", nargs="*")
    parser.add_argument("--binding", type=Path, required=True)
    parser.add_argument("--reuse", action="store_true")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--junit", default="core-junit.xml")
    parser.add_argument("--coverage", action="store_true")
    parser.add_argument("--executor", choices=("auto", "native", "dagger"))
    args = parser.parse_args(argv)
    try:
        if args.checks or not args.json or not args.reuse:
            raise ValueError(
                "--binding requires --reuse and --json, without other selectors"
            )
        output = checks.report_path(args.json, checks.ROOT)
        if args.binding.resolve() in {
            output,
            (checks.ROOT / args.junit).resolve(),
            checks.ROOT / "core-coverage.xml",
            checks.ROOT / "core-coverage.json",
        }:
            raise ValueError("Check outputs must not overwrite the Handler binding")
        binding = read(args.binding, pipeline=checks.PIPELINE)
        print(
            "Prototype binding compatibility; use engineering-ci workflow generate for new definitions"
        )
        report, code = checks.run_checks(
            ["pr"],
            executor=args.executor,
            coverage=args.coverage,
            junit=args.junit,
            output=output,
            reuse=True,
        )
        report["handler_binding"] = binding
        try:
            checks.atomic_json(output, report)
        except OSError:
            return code or 3
        return code
    except (OSError, ValueError, KeyError, TypeError, ArchiveError):
        print("checks: invalid or unavailable prototype binding", file=sys.stderr)
        return 3
