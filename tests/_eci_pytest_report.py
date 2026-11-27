"""ECI repo-local pytest invocation reports (#167 R3/R6).

Prepares a unique ``.artifacts/pytest/<invocation-id>/`` directory for each
controller session and points native pytest-reportlog / JUnit at it. The
plugin records only what the running session actually observed: it does not
rewrite explicit native reporting options, does not observe Git/Dagger/source
identity, and does not claim an outer ECI Invocation or Receipt binding.

Consumers' generic ``_run_pytest`` never loads this module. The repository's
own Dagger self-test does load ``tests/conftest.py``, so container-local report
generation can occur; exporting those files from Dagger/CI remains #167 R3/#86
work, not this plugin.
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from collections import Counter
from importlib import metadata
from pathlib import Path
from typing import Any

import pytest

_REPORT_PLUGIN = "eci_invocation_report"
_KIND = "engineering-ci-pytest-invocation"
_SCHEMA_VERSION = 1
_REPORT_LOG_NAME = "pytest-reportlog.jsonl"
_JUNIT_NAME = "junit.xml"
_METADATA_NAME = "metadata.json"
_SUMMARY_NAME = "summary.json"
_DELIVERY_BOUNDARY = (
    "pytest_configure → pytest_sessionfinish after inner sessionfinish hooks"
)


def _package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _plugin_versions() -> dict[str, str | None]:
    return {
        "pytest": pytest.__version__,
        "pytest-reportlog": _package_version("pytest-reportlog"),
        "pytest-xdist": _package_version("pytest-xdist"),
    }


def _is_xdist_worker(config) -> bool:
    return hasattr(config, "workerinput")


def _parallel_workers(config) -> int | str | None:
    value = getattr(config.option, "numprocesses", None)
    return None if value in (None, 0) else value


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


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Seal a JSON document without leaving a partial completion receipt."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except OSError:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise


def _emit(config, message: str, *, error: bool = False) -> None:
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(message, red=error)
    else:
        print(message, file=sys.stderr)


def _selection_filters_present(config) -> bool:
    option = config.option
    return any(
        (
            getattr(option, "keyword", None),
            getattr(option, "markexpr", None),
            getattr(option, "deselect", None),
            getattr(option, "lf", False),
            getattr(option, "failedfirst", False),
            getattr(option, "newfirst", False),
        )
    )


_MAX_TAIL_BYTES = 256 * 1024


def _tail_text(path: Path, limit: int = _MAX_TAIL_BYTES) -> str:
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - limit))
        return stream.read().decode("utf-8", errors="replace")


def _last_json_line(text: str) -> dict[str, Any] | None:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None
    return None


def _colocated(path: Path, out_dir: Path) -> bool:
    try:
        if path.is_symlink():
            return False
        return path.resolve().is_relative_to(out_dir.resolve())
    except OSError:
        return False


def _attribution(path: Path, out_dir: Path, *, explicit: bool) -> str:
    if _colocated(path, out_dir):
        return "colocated_invocation_dir"
    if explicit:
        return "explicit_path_unverified"
    return "outside_invocation_dir"


def _inspect_report_log(
    path: Path, out_dir: Path, *, explicit: bool, exitstatus: int
) -> dict[str, Any]:
    info: dict[str, Any] = {
        "artifact": "report_log",
        "path": str(path),
        "explicit": explicit,
        "attribution": _attribution(path, out_dir, explicit=explicit),
        "exists": False,
        "non_empty": False,
        "bytes": None,
        "format": "unknown",
        "session_finish_observed": False,
        "exitstatus_match": None,
        "status": "missing",
        "reasons": ["missing"],
    }
    try:
        if not path.is_file():
            return info
        size = path.stat().st_size
    except OSError as exc:
        info["status"] = "invalid"
        info["reasons"] = [f"unreadable: {type(exc).__name__}"]
        return info
    info["exists"] = True
    info["non_empty"] = size > 0
    info["bytes"] = size
    if not info["non_empty"]:
        info["status"] = "empty"
        info["reasons"] = ["empty"]
        return info
    try:
        payload = _last_json_line(_tail_text(path))
    except OSError as exc:
        info["status"] = "invalid"
        info["reasons"] = [f"unreadable: {type(exc).__name__}"]
        return info
    if payload is None:
        info["status"] = "invalid"
        info["reasons"] = ["last_line_not_json_object"]
        return info
    info["format"] = "reportlog_jsonl"
    if payload.get("$report_type") != "SessionFinish":
        info["status"] = "unknown"
        info["reasons"] = ["session_finish_not_observed"]
        return info
    info["session_finish_observed"] = True
    report_exit = payload.get("exitstatus")
    if type(report_exit) is not int:
        info["status"] = "unknown"
        info["reasons"] = ["reportlog_exitstatus_not_observed"]
        return info
    info["exitstatus_match"] = report_exit == exitstatus
    if report_exit != exitstatus:
        info["status"] = "conflict"
        info["reasons"] = ["exitstatus_mismatch"]
        return info
    if info["attribution"] != "colocated_invocation_dir":
        info["status"] = "unknown"
        info["reasons"] = ["attribution_unverified"]
        return info
    info["status"] = "complete"
    info["reasons"] = []
    return info


def _inspect_junit(path: Path, out_dir: Path, *, explicit: bool) -> dict[str, Any]:
    info: dict[str, Any] = {
        "artifact": "junit_xml",
        "path": str(path),
        "explicit": explicit,
        "attribution": _attribution(path, out_dir, explicit=explicit),
        "exists": False,
        "non_empty": False,
        "bytes": None,
        "format": "unknown",
        "root_tag": None,
        "status": "missing",
        "reasons": ["missing"],
    }
    try:
        if not path.is_file():
            return info
        size = path.stat().st_size
    except OSError as exc:
        info["status"] = "invalid"
        info["reasons"] = [f"unreadable: {type(exc).__name__}"]
        return info
    info["exists"] = True
    info["non_empty"] = size > 0
    info["bytes"] = size
    if not info["non_empty"]:
        info["status"] = "empty"
        info["reasons"] = ["empty"]
        return info
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        info["status"] = "invalid"
        info["reasons"] = [f"invalid_junit: {type(exc).__name__}"]
        return info
    info["format"] = "junit_xml"
    info["root_tag"] = root.tag
    if root.tag not in {"testsuites", "testsuite"}:
        info["status"] = "invalid"
        info["reasons"] = ["unexpected_junit_root"]
        return info
    if info["attribution"] != "colocated_invocation_dir":
        info["status"] = "unknown"
        info["reasons"] = ["attribution_unverified"]
        return info
    info["status"] = "complete"
    info["reasons"] = []
    return info


def _overall_delivery(
    report_log: dict[str, Any], junit: dict[str, Any]
) -> dict[str, Any]:
    artifacts = {"report_log": report_log, "junit_xml": junit}
    missing = [
        name
        for name, info in artifacts.items()
        if info["status"] in {"missing", "empty"}
    ]
    invalid = [
        name
        for name, info in artifacts.items()
        if info["status"] in {"invalid", "conflict"}
    ]
    unknown = [name for name, info in artifacts.items() if info["status"] == "unknown"]
    if missing or invalid:
        status = "incomplete"
    elif unknown:
        status = "unknown"
    else:
        status = "complete"
    errors = [
        f"{name}: {reason}"
        for name, info in artifacts.items()
        for reason in info.get("reasons", [])
    ]
    return {
        "status": status,
        "report_complete": status == "complete",
        "missing": missing,
        "invalid": invalid,
        "unknown": unknown,
        "artifacts": artifacts,
        "errors": errors,
        "native_exit_preserved": True,
        "summary_seal": "ok",
    }


def _expand_counts(counts: Counter[str], order: list[str]) -> list[str]:
    expanded: list[str] = []
    seen: set[str] = set()
    for nodeid in order:
        if nodeid in counts and nodeid not in seen:
            expanded.extend([nodeid] * counts[nodeid])
            seen.add(nodeid)
    for nodeid, count in counts.items():
        if nodeid not in seen:
            expanded.extend([nodeid] * count)
    return expanded


def _build_meta(
    config,
    *,
    invocation_id: str,
    out_dir: Path,
    report_log: Path,
    junit_xml: Path,
    report_log_explicit: bool,
    junit_explicit: bool,
) -> dict[str, Any]:
    collect_only = bool(getattr(config.option, "collectonly", False))
    started_at_unix = time.time()
    started_mono = time.perf_counter()
    invocation_params = getattr(config, "invocation_params", None)
    argv = list(getattr(invocation_params, "args", ()) or ())
    return {
        "kind": _KIND,
        "schema_version": _SCHEMA_VERSION,
        "phase": "configured",
        "invocation_id": invocation_id,
        "out_dir": str(out_dir),
        "report_log": str(report_log),
        "junit_xml": str(junit_xml),
        "report_log_explicit": report_log_explicit,
        "junit_explicit": junit_explicit,
        "collect_only": collect_only,
        "run_mode": "collect_only" if collect_only else "normal",
        "cwd": str(Path.cwd()),
        "rootpath": str(Path(config.rootpath).resolve()),
        "pytest_version": pytest.__version__,
        "python_version": platform.python_version(),
        "platform": sys.platform,
        "plugin_versions": _plugin_versions(),
        "argv": argv,
        "started_at_unix": started_at_unix,
        "started_mono": started_mono,
        "timing": {
            "started_at_unix": started_at_unix,
            "started_mono": started_mono,
            "phase_boundary": _DELIVERY_BOUNDARY,
        },
        "source_observation": {
            "status": "unknown",
            "reason": "not_observed_by_report_plugin",
        },
        "invocation_binding": {
            "scope": "process_local",
            "outer_invocation_ref": None,
            "note": (
                "local invocation id and colocated paths do not prove an outer "
                "ECI Invocation or Receipt"
            ),
        },
        "report_delivery": {"status": "pending"},
    }


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
    invocation_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    out_dir = root / ".artifacts" / "pytest" / invocation_id
    report_log = (
        Path(existing_log).resolve() if existing_log else out_dir / _REPORT_LOG_NAME
    )
    junit_xml = (
        Path(existing_junit).resolve() if existing_junit else out_dir / _JUNIT_NAME
    )

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _emit(config, f"ECI pytest report disabled: {exc}", error=True)
        return
    if not existing_log:
        config.option.report_log = str(report_log)
    if not existing_junit:
        config.option.xmlpath = str(junit_xml)

    meta = _build_meta(
        config,
        invocation_id=invocation_id,
        out_dir=out_dir,
        report_log=report_log,
        junit_xml=junit_xml,
        report_log_explicit=bool(existing_log),
        junit_explicit=bool(existing_junit),
    )
    try:
        _write_json_atomic(out_dir / _METADATA_NAME, meta)
    except OSError as exc:
        _emit(config, f"ECI pytest report disabled: {exc}", error=True)
        return

    config._eci_invocation_report = meta
    if not config.pluginmanager.has_plugin(_REPORT_PLUGIN):
        config.pluginmanager.register(_InvocationReportPlugin(meta), _REPORT_PLUGIN)


class _InvocationReportPlugin:
    def __init__(self, meta: dict[str, Any]):
        self.meta = meta
        self.selected_node_ids: list[str] | None = None
        self.deselected_node_ids: list[str] = []
        self.collection_mode = "unknown"
        self.collection_observed = False
        self.collection_errors: list[dict[str, str]] = []
        self.deselection_observed = False
        self.worker_collections: dict[str, list[str]] = {}
        self.started_counts: Counter[str] = Counter()
        self.finished_counts: Counter[str] = Counter()
        self.started_order: list[str] = []
        self.finished_order: list[str] = []

    def pytest_collection_finish(self, session) -> None:
        self.collection_observed = True
        if _parallel_workers(session.config) is not None:
            self.collection_mode = "xdist"
            return
        self.collection_mode = "serial"
        self.selected_node_ids = [item.nodeid for item in session.items]

    def pytest_collectreport(self, report) -> None:
        if report.failed:
            self.collection_errors.append(
                {"nodeid": str(report.nodeid), "outcome": str(report.outcome)}
            )

    def pytest_runtest_logreport(self, report) -> None:
        if report.when == "setup":
            self.started_counts[report.nodeid] += 1
            self.started_order.append(report.nodeid)
        elif report.when == "teardown":
            self.finished_counts[report.nodeid] += 1
            self.finished_order.append(report.nodeid)

    def pytest_deselected(self, items) -> None:
        self.deselection_observed = True
        self.deselected_node_ids.extend(str(item.nodeid) for item in items)

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node, ids) -> None:
        self.collection_mode = "xdist"
        self.worker_collections[str(node.workerid)] = [str(nodeid) for nodeid in ids]

    @pytest.hookimpl(hookwrapper=True)
    def pytest_sessionfinish(self, session, exitstatus) -> None:
        yield
        try:
            self._finalize(session)
        except Exception as exc:  # noqa: BLE001 - never mask the native exit
            _emit(
                session.config,
                f"ECI pytest report finalization failed: {type(exc).__name__}: {exc}",
                error=True,
            )

    def _selection(self, config) -> dict[str, Any]:
        if self.collection_mode == "xdist":
            return {
                "mode": "xdist",
                "selected_items": None,
                "selected_node_ids": None,
                "deselected_items": None,
                "deselected_node_ids": None,
                "collected_items": None,
                "complete": False,
                "deselection_observed": self.deselection_observed,
                "worker_inventories": {
                    worker: {
                        "count": len(node_ids),
                        "node_ids": node_ids,
                    }
                    for worker, node_ids in sorted(self.worker_collections.items())
                },
                "unknown_reasons": ["xdist_worker_collections_are_per_worker"],
            }
        if not self.collection_observed or self.selected_node_ids is None:
            return {
                "mode": self.collection_mode,
                "selected_items": None,
                "selected_node_ids": None,
                "deselected_items": None,
                "deselected_node_ids": None,
                "collected_items": None,
                "complete": False,
                "deselection_observed": self.deselection_observed,
                "unknown_reasons": ["collection_not_observed"],
            }

        filters_present = _selection_filters_present(config)
        deselection_known = self.deselection_observed or not filters_present
        deselected_node_ids = (
            list(dict.fromkeys(self.deselected_node_ids)) if deselection_known else None
        )
        selected_items = len(self.selected_node_ids)
        deselected_items = (
            len(deselected_node_ids) if deselected_node_ids is not None else None
        )
        unknown_reasons: list[str] = []
        if not deselection_known:
            unknown_reasons.append("deselection_not_observed")
        if self.collection_errors:
            unknown_reasons.append("collection_incomplete")
        return {
            "mode": "serial",
            "selected_items": selected_items,
            "selected_node_ids": self.selected_node_ids,
            "deselected_items": deselected_items,
            "deselected_node_ids": deselected_node_ids,
            "collected_items": (
                selected_items + deselected_items
                if deselected_items is not None
                else None
            ),
            "complete": not unknown_reasons,
            "deselection_observed": self.deselection_observed,
            "unknown_reasons": unknown_reasons,
        }

    def _collection(self) -> dict[str, Any]:
        if self.collection_mode == "xdist":
            return {
                "observed": False,
                "complete": False,
                "errors": self.collection_errors,
                "unknown_reasons": ["xdist_controller_collection_not_observed"],
            }
        return {
            "observed": self.collection_observed,
            "complete": self.collection_observed and not self.collection_errors,
            "errors": self.collection_errors,
            "unknown_reasons": (
                ["collection_incomplete"] if self.collection_errors else []
            ),
        }

    def _execution(self, config) -> dict[str, Any]:
        started_items = sum(self.started_counts.values())
        completed_items = sum(self.finished_counts.values())
        collect_only = bool(getattr(config.option, "collectonly", False))
        if collect_only:
            return {
                "mode": "collect_only",
                "status": "not_run",
                "complete": False,
                "started_items": started_items,
                "completed_items": completed_items,
                "started_node_ids": list(self.started_order),
                "completed_node_ids": list(self.finished_order),
                "not_started_node_ids": None,
                "incomplete_node_ids": None,
                "duplicate_node_ids": [],
                "unknown_reasons": ["collect_only_no_execution_intent"],
            }
        unknown_reasons: list[str] = []
        if not self.collection_observed or self.collection_errors:
            unknown_reasons.append("collection_incomplete")
        if self.selected_node_ids is None or self.collection_mode == "xdist":
            unknown_reasons.append("selection_unknown")
        if unknown_reasons:
            return {
                "mode": "run",
                "status": "unknown",
                "complete": False,
                "started_items": started_items,
                "completed_items": completed_items,
                "started_node_ids": list(self.started_order),
                "completed_node_ids": list(self.finished_order),
                "not_started_node_ids": None,
                "incomplete_node_ids": None,
                "duplicate_node_ids": [],
                "unknown_reasons": unknown_reasons,
            }

        selected_counts = Counter(self.selected_node_ids)
        duplicate_node_ids = sorted(
            nodeid for nodeid, count in selected_counts.items() if count > 1
        )
        not_started_node_ids = _expand_counts(
            selected_counts - self.started_counts, self.selected_node_ids
        )
        incomplete_node_ids = _expand_counts(
            self.started_counts - self.finished_counts, self.started_order
        )
        if not self.selected_node_ids:
            status = "empty"
            reasons = ["no_selected_items"]
        elif not_started_node_ids or incomplete_node_ids:
            status = "incomplete"
            reasons = []
        else:
            status = "complete"
            reasons = []
        return {
            "mode": "run",
            "status": status,
            "complete": status == "complete",
            "started_items": started_items,
            "completed_items": completed_items,
            "started_node_ids": list(self.started_order),
            "completed_node_ids": list(self.finished_order),
            "not_started_node_ids": not_started_node_ids,
            "incomplete_node_ids": incomplete_node_ids,
            "duplicate_node_ids": duplicate_node_ids,
            "unknown_reasons": reasons,
        }

    def _delivery(self, exitstatus: int) -> dict[str, Any]:
        meta = self.meta
        out_dir = Path(meta["out_dir"])
        report_log = _inspect_report_log(
            Path(meta["report_log"]),
            out_dir,
            explicit=bool(meta["report_log_explicit"]),
            exitstatus=exitstatus,
        )
        junit_xml = _inspect_junit(
            Path(meta["junit_xml"]),
            out_dir,
            explicit=bool(meta["junit_explicit"]),
        )
        return _overall_delivery(report_log, junit_xml)

    def _finalize(self, session) -> None:
        config = session.config
        meta = self.meta
        finished_mono = time.perf_counter()
        wall_time = finished_mono - float(meta["started_mono"])
        exitstatus = int(session.exitstatus)
        selection = self._selection(config)
        collection = self._collection()
        execution = self._execution(config)
        delivery = self._delivery(exitstatus)
        finished_at_unix = time.time()
        summary = {
            **meta,
            "phase": "finished",
            "session_finished": True,
            "report_complete": delivery["report_complete"],
            "finished_at_unix": finished_at_unix,
            "wall_time_seconds": round(wall_time, 3),
            "exitstatus": exitstatus,
            "selection": selection,
            "collection": collection,
            "execution": execution,
            "selected_items": selection["selected_items"],
            "deselected_items": selection["deselected_items"],
            "durations": {
                "effective": getattr(config.option, "durations", None),
                "min": getattr(config.option, "durations_min", None),
                "source": "native CLI/config (not rewritten by this plugin)",
            },
            "workers": _parallel_workers(config),
            "report_delivery": delivery,
            "timing": {
                **meta["timing"],
                "finished_at_unix": finished_at_unix,
                "finished_mono": finished_mono,
                "wall_time_seconds": round(wall_time, 3),
            },
        }
        try:
            _write_json_atomic(Path(meta["out_dir"]) / _SUMMARY_NAME, summary)
        except OSError as exc:
            self._record_delivery_failure(
                config,
                delivery,
                exitstatus,
                exc,
                selection=selection,
                collection=collection,
                execution=execution,
            )
            return

        selected = selection["selected_items"]
        deselected = selection["deselected_items"]
        detail = (
            f"ECI pytest report  wall={wall_time:.2f}s  "
            f"selected={selected if selected is not None else 'unknown'} "
            f"deselected={deselected if deselected is not None else 'unknown'}  "
            f"dir={meta['out_dir']}"
        )
        reporter = config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.write_sep("=", detail)
        else:
            print(detail)
        _emit(config, f"reportlog: {meta['report_log']}")
        _emit(config, f"junit:     {meta['junit_xml']}")
        if delivery["status"] != "complete":
            details: list[str] = []
            if delivery["missing"]:
                details.append("missing " + ", ".join(delivery["missing"]))
            if delivery["invalid"]:
                details.append("invalid " + ", ".join(delivery["invalid"]))
            if delivery["unknown"]:
                details.append("unverified " + ", ".join(delivery["unknown"]))
            _emit(
                config,
                f"report delivery {delivery['status']}: "
                + "; ".join(details)
                + " (native exit is preserved)",
                error=True,
            )

    def _record_delivery_failure(
        self,
        config,
        delivery,
        exitstatus: int,
        exc: OSError,
        *,
        selection,
        collection,
        execution,
    ) -> None:
        meta = self.meta
        failure = {
            **meta,
            "phase": "delivery_failed",
            "session_finished": True,
            "report_complete": False,
            "exitstatus": exitstatus,
            "failed_at_unix": time.time(),
            "selection": selection,
            "collection": collection,
            "execution": execution,
            "report_delivery": {
                **delivery,
                "status": "failed",
                "summary_seal": "failed",
                "errors": [
                    *(delivery.get("errors") or []),
                    f"summary_seal: {type(exc).__name__}: {exc}",
                ],
            },
        }
        with contextlib.suppress(OSError):
            _write_json_atomic(Path(meta["out_dir"]) / _METADATA_NAME, failure)
        _emit(
            config,
            "ECI pytest report delivery failed: "
            f"{type(exc).__name__}: {exc} (native exit is preserved)",
            error=True,
        )
