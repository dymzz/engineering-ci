#!/usr/bin/env python3
"""Compatibility entry point for the ghwf PowerShell function."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from engineering_ci.execution.cli import entrypoint, main

if __name__ == '__main__':
    sys.exit(entrypoint())
