#!/usr/bin/env python3
"""Select an existing Workflow Run and download its logs and artifacts."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from engineering_ci.cli.download import main

if __name__ == "__main__":
    raise SystemExit(main())
