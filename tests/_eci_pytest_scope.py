"""Require bounded targets or an explicit full-suite intent in this checkout."""

from pathlib import Path

import pytest
from _eci_pytest_report import _eci_repo_root

_PENDING_SCOPE = pytest.StashKey[bool]()
_DESELECTED = pytest.StashKey[bool]()
_MESSAGE = (
    "engineering-ci requires a test file, node, subdirectory, or effective "
    "selection. For an intentional full run use --full-suite."
)


def pytest_addoption(parser):
    parser.getgroup("eci-scope", "engineering-ci test scope").addoption(
        "--full-suite",
        action="store_true",
        help="Explicitly request the complete engineering-ci suite.",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session):
    config = session.config
    root = _eci_repo_root(config)
    if root is None or config.option.collectonly or config.option.full_suite:
        return
    broad = {root, root / "tests"}
    targets = config.args
    if targets and not any(
        "::" not in target and Path(target).resolve() in broad for target in targets
    ):
        return
    option = config.option
    if not (
        option.keyword
        or option.markexpr
        or getattr(option, "lf", False)
        or getattr(option, "testmon", False)
    ):
        raise pytest.UsageError(_MESSAGE)
    if getattr(option, "numprocesses", 0):
        raise pytest.UsageError(
            "engineering-ci parallel selection requires an explicit test target "
            "or --full-suite."
        )
    config.stash[_PENDING_SCOPE] = True


def pytest_deselected(items):
    if items:
        items[0].config.stash[_DESELECTED] = True


@pytest.hookimpl(trylast=True)
def pytest_collection_finish(session):
    config = session.config
    if (
        not config.stash.get(_PENDING_SCOPE, False)
        or not session.items
        or config.stash.get(_DESELECTED, False)
    ):
        return
    # Pinned pytest may skip files before producing items for --lf.
    last_failed = config.pluginmanager.get_plugin("lfplugin")
    if getattr(config.option, "lf", False) and getattr(
        last_failed, "_skipped_files", 0
    ):
        return
    raise pytest.UsageError(f"Selection did not narrow collection. {_MESSAGE}")
