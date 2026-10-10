#!/usr/bin/env python3
"""Standalone stdlib entry point; scanning does not start a Dagger Engine."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from engineering_ci.dependency_security import scan, write_bundle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path("."))
    parser.add_argument("--mode", default="off")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--exceptions", type=Path)
    parser.add_argument("--scanner", type=Path)
    parser.add_argument("--uv", default="uv")
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--max-findings", type=int, default=100)
    parser.add_argument("--workspace-root", type=Path)
    parser.add_argument("--public-index", action="append", default=[])
    args = parser.parse_args(argv)
    report, native = scan(
        args.project,
        mode=args.mode,
        scanner=args.scanner,
        uv=args.uv,
        exceptions=args.exceptions,
        cache=args.cache,
        timeout=args.timeout,
        max_findings=args.max_findings,
        workspace_root=args.workspace_root,
        public_indexes=args.public_index,
    )
    write_bundle(args.out, report, native)
    print(
        f"Lock integrity: {report['integrity']['status']}; dependency security: {report['status']}; mode: {report['gate']['mode']}"
    )
    return report["gate"]["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
