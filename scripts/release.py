#!/usr/bin/env python3
"""Request Release Workflow verification, or explicitly publish from main."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from engineering_ci.cli.release import main

if __name__ == "__main__":
    raise SystemExit(main())
