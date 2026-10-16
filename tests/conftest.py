"""Make the local src package importable without an installation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


import pytest


@pytest.fixture(autouse=True)
def isolate_task_observation_transport(monkeypatch):
    """General tests do not write task files into the primary checkout.

    Dedicated recording tests restore installed vesryn-log writers in their own
    temporary project and assert the actual host-entry/file contract.
    """
    from engineering_ci import task_observation

    monkeypatch.setattr(task_observation, "deliver", lambda task: None)
    monkeypatch.setattr(task_observation, "persist_delivery", lambda task: None)
