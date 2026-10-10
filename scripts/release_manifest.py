#!/usr/bin/env python3
"""Compatibility entry point; business logic lives in engineering_ci."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from engineering_ci.release import manifest as _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())
else:
    sys.modules[__name__] = _implementation
