#!/usr/bin/env python3
"""Request, inspect or wait for the Release Workflow; use native Git/gh for PRs."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from engineering_ci.cli.release import main

if __name__ == "__main__":
    raise SystemExit(main())
