"""Make the local src package and repo-local pytest plugin importable.

Also loads the repo-local #167 R3 invocation report plugin so bare
``pytest`` / ``python -m pytest`` automatically retain reportlog + JUnit
under ``.artifacts/pytest/<invocation-id>/``. The plugin directory is added to
``sys.path`` explicitly so this also works under ``--import-mode=importlib``
without installing the test helper.
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent / "src"))
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

pytest_plugins = ("_eci_pytest_report", "_eci_pytest_scope")
