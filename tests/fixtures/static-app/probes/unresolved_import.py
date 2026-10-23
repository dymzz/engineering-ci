"""Module whose import cannot resolve in the check environment.

Pyright reports this as a diagnostic instead of silently skipping the module;
the report must carry it as an explicit limitation rather than a clean result.
"""

import engineering_ci_absent_dependency_xyz  # noqa: F401


def probe() -> int:
    """Return a constant; the import above is the actual subject of the probe."""
    return 1
