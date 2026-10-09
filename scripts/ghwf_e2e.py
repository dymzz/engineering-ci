#!/usr/bin/env python3
"""Opt-in E2E entry: collect an existing Run, or explicitly authorize one new POST."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from engineering_ci.execution.cli import entrypoint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-dispatch', action='store_true',
                        help='Explicitly permit a real GitHub workflow dispatch')
    args, workflow_args = parser.parse_known_args()
    if not args.allow_dispatch:
        if '--new-run' in workflow_args or not any(
                a == '--resume' or a.startswith('--resume=') for a in workflow_args):
            parser.error('Real E2E requires --resume RUN_ID or explicit --allow-dispatch')
    return entrypoint(workflow_args)


if __name__ == '__main__':
    sys.exit(main())
