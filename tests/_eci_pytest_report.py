"""ECI repo-local pytest invocation reports (#167 R3).

Prepares a unique `.artifacts/pytest/<invocation-id>/` directory for each
controller session and points native pytest-reportlog / JUnit / durations at it.
Does not invent a second report serializer; consumers' generic `_run_pytest`
never loads this module.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import pytest

_REPORT_PLUGIN = "eci_invocation_report"


def _is_xdist_worker(config) -> bool:
    return hasattr(config, "workerinput")


def _help_or_version(config) -> bool:
    return bool(getattr(config.option, "help", False)) or bool(
        getattr(config.option, "version", False)
    )


def _auto_report_disabled(config) -> bool:
    if os.environ.get("ECI_PYTEST_NO_AUTO_REPORT") == "1":
        return True
    return bool(getattr(config.option, "eci_no_auto_report", False))


def _eci_repo_root(config) -> Path | None:
    root = Path(config.rootpath).resolve()
    if not (root / "src" / "engineering_ci").is_dir():
        return None
    if not (root / "pyproject.toml").is_file():
        return None
    return root


def pytest_addoption(parser):
    group = parser.getgroup("eci-report", "engineering-ci invocation reports")
    group.addoption(
        "--eci-no-auto-report",
        action="store_true",
        default=False,
        help="Disable automatic per-invocation reportlog/JUnit under .artifacts/pytest/",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    if (
        _help_or_version(config)
        or _is_xdist_worker(config)
        or _auto_report_disabled(config)
    ):
        return
    root = _eci_repo_root(config)
    if root is None:
        return

    existing_log = getattr(config.option, "report_log", None)
    existing_junit = getattr(config.option, "xmlpath", None)
    collect_only = bool(getattr(config.option, "collectonly", False))

    invocation_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    out_dir = root / ".artifacts" / "pytest" / invocation_id
    out_dir.mkdir(parents=True, exist_ok=True)

    report_log = Path(existing_log) if existing_log else out_dir / "report.jsonl"
    junit_path = Path(existing_junit) if existing_junit else out_dir / "junit.xml"
    if not existing_log:
        config.option.report_log = str(report_log)
    if not existing_junit:
        config.option.xmlpath = str(junit_path)

    durations = getattr(config.option, "durations", None)
    if durations in (None, 0):
        config.option.durations = 10
    if getattr(config.option, "durations_min", None) in (None, 0.0):
        config.option.durations_min = 0.05

    meta = {
        "kind": "engineering-ci-pytest-invocation",
        "schema_version": 1,
        "invocation_id": invocation_id,
        "out_dir": str(out_dir),
        "report_log": str(report_log),
        "junit_xml": str(junit_path),
        "collect_only": collect_only,
        "cwd": str(Path.cwd()),
        "rootpath": str(root),
        "pytest_version": pytest.__version__,
        "started_at_unix": time.time(),
        "started_mono": time.perf_counter(),
        "argv": list(getattr(config, "invocation_params", None).args or ()),
    }
    (out_dir / "metadata.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    config._eci_invocation_report = meta
    if not config.pluginmanager.has_plugin(_REPORT_PLUGIN):
        config.pluginmanager.register(_InvocationReportPlugin(meta), _REPORT_PLUGIN)


class _InvocationReportPlugin:
    def __init__(self, meta: dict):
        self.meta = meta
        self.selected = 0
        self.deselected = 0
        self.exitstatus = None

    def pytest_collection_modifyitems(self, session, config, items):
        self.selected = len(items)

    def pytest_deselected(self, items):
        self.deselected += len(items)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_sessionfinish(self, session, exitstatus):
        yield
        self.exitstatus = int(exitstatus)
        ended_mono = time.perf_counter()
        wall = ended_mono - float(self.meta["started_mono"])
        summary = {
            **self.meta,
            "finished_at_unix": time.time(),
            "wall_time_seconds": round(wall, 3),
            "exitstatus": self.exitstatus,
            "selected_items": self.selected,
            "deselected_items": self.deselected,
            "session_finished": True,
        }
        out_dir = Path(self.meta["out_dir"])
        (out_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (out_dir / "metadata.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.write_sep(
                "=",
                (
                    f"ECI pytest report  wall={wall:.2f}s  "
                    f"selected={self.selected} deselected={self.deselected}  "
                    f"dir={out_dir}"
                ),
            )
            reporter.write_line(f"reportlog: {self.meta['report_log']}")
            reporter.write_line(f"junit:     {self.meta['junit_xml']}")
            reporter.write_line(
                "durations: use terminal Top-N above; full node phases are in reportlog"
            )
