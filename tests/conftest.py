"""Make the local src package importable without an installation.

Also loads the repo-local #167 R3 invocation report plugin so bare
``pytest`` / ``python -m pytest`` automatically retain reportlog + JUnit
under ``.artifacts/pytest/<invocation-id>/``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

pytest_plugins = ("_eci_pytest_report",)
