#!/usr/bin/env python3
"""Deliver a development PR and, after human merge, validate and publish its version."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from engineering_ci.cli.release import main

if __name__ == "__main__":
    raise SystemExit(main())
