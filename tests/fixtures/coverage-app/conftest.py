"""The fixture is also collectable by the repository's independent pytest run."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def pytest_configure(config):
    config.addinivalue_line("markers", "fast: small feedback suite")
    config.addinivalue_line("markers", "slow: full regression example")
