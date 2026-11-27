"""Probes for ECI repo-local pytest invocation reports (#167 R3/R6).

Each probe runs pytest in a throwaway ECI-shaped workspace with its own report
root, so assertions consume that invocation's exact directory and native files
instead of scanning a shared ``.artifacts/pytest`` for a "latest" entry.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import _eci_pytest_report as report_plugin
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_ROOT / "tests"
REPORT_LINE = re.compile(r"ECI pytest report\b.*?dir=(?P<directory>\S+)")

BODY_OK = (
    "import pytest\n"
    "\n"
    "def test_ok():\n"
    "    assert True\n"
    "\n"
    "def test_skip():\n"
    "    pytest.skip('probe')\n"
)
BODY_FAIL = "def test_fail():\n    assert 1 == 2\n"
BODY_ERROR = (
    "import pytest\n"
    "\n"
    "@pytest.fixture\n"
    "def broken():\n"
    "    raise RuntimeError('boom')\n"
    "\n"
    "def test_error(broken):\n"
    "    assert True\n"
)
BODY_EXIT = "import pytest\n\ndef test_stop():\n    pytest.exit('stop', returncode=2)\n"
BODY_INTERRUPT = "def test_interrupt():\n    raise KeyboardInterrupt\n"
BODY_FILTER = (
    "def test_keep():\n    assert True\n"
    "\n"
    "def test_drop_one():\n    assert True\n"
    "\n"
    "def test_drop_two():\n    assert True\n"
)
BODY_MARKER = (
    "import pytest\n"
    "\n"
    "@pytest.mark.slow\n"
    "def test_slow():\n    assert True\n"
    "\n"
    "def test_fast():\n    assert True\n"
)


def _probe_root(tmp_path: Path) -> Path:
    root = tmp_path / "probe-root"
    (root / "src" / "engineering_ci").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "eci-report-probe"\nversion = "0.0.0"\n'
        'requires-python = ">=3.11"\n'
        "\n"
        "[tool.pytest.ini_options]\n"
        'testpaths = ["tests"]\n',
        encoding="utf-8",
    )
    return root


def _subprocess_env(overrides: dict[str, str] | None = None) -> dict[str, str]:
    ignored = {
        "ECI_PYTEST_NO_AUTO_REPORT",
        "ECI_RUN_LIVE_COLLECTOR_TESTS",
        "ECI_SKIP_LIVE_COLLECTOR_TESTS",
        "PYTEST_ADDOPTS",
    }
    env = {key: value for key, value in os.environ.items() if key not in ignored}
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{PLUGIN_DIR}{os.pathsep}{existing}" if existing else str(PLUGIN_DIR)
    )
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["COLUMNS"] = "200"
    if overrides:
        env.update(overrides)
    return env


def _run_probe(
    root: Path,
    body: str,
    *extra: str,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    (root / "tests" / "test_probe.py").write_text(body, encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "_eci_pytest_report",
        "-p",
        "no:cacheprovider",
        f"--rootdir={root}",
        "tests/test_probe.py",
        *extra,
    ]
    return subprocess.run(
        command,
        cwd=root,
        env=_subprocess_env(env_overrides),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _report_dir(result: subprocess.CompletedProcess) -> Path:
    combined = result.stdout + result.stderr
    match = REPORT_LINE.search(combined)
    assert match is not None, combined
    return Path(match.group("directory").strip())


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _artifacts_root(root: Path) -> Path:
    return root / ".artifacts" / "pytest"


def _reportlog_events(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_serial_invocation_records_native_reports_and_selection(tmp_path):
    root = _probe_root(tmp_path)
    result = _run_probe(root, BODY_OK)
    assert result.returncode == 0, result.stdout + result.stderr

    invocation = _report_dir(result)
    assert invocation.parent == _artifacts_root(root)
    assert str(invocation) in result.stdout + result.stderr
    metadata = _read_json(invocation / "metadata.json")
    summary = _read_json(invocation / "summary.json")
    assert metadata["phase"] == "configured"
    assert summary["phase"] == "finished"
    assert metadata["kind"] == summary["kind"] == "engineering-ci-pytest-invocation"
    assert metadata["schema_version"] == summary["schema_version"] == 1
    assert metadata["invocation_id"] == summary["invocation_id"]
    assert summary["pytest_version"] == pytest.__version__
    assert summary["exitstatus"] == 0
    assert summary["session_finished"] is True
    assert summary["report_complete"] is True
    assert summary["report_delivery"]["status"] == "complete"
    assert summary["source_observation"]["status"] == "unknown"
    assert summary["invocation_binding"]["outer_invocation_ref"] is None

    reportlog = invocation / "pytest-reportlog.jsonl"
    junit = invocation / "junit.xml"
    assert reportlog.is_file()
    assert junit.is_file()
    assert not (invocation / "report.jsonl").exists()
    events = _reportlog_events(reportlog)
    assert events[0]["$report_type"] == "SessionStart"
    assert events[-1]["$report_type"] == "SessionFinish"
    assert events[-1]["exitstatus"] == 0
    assert ET.parse(junit).getroot().tag in {"testsuites", "testsuite"}

    selection = summary["selection"]
    assert selection["mode"] == "serial"
    assert selection["selected_items"] == 2
    assert sorted(selection["selected_node_ids"]) == [
        "tests/test_probe.py::test_ok",
        "tests/test_probe.py::test_skip",
    ]
    assert selection["deselected_items"] == 0
    assert selection["deselection_observed"] is False
    assert selection["complete"] is True
    assert selection["collected_items"] == 2
    assert summary["selected_items"] == 2
    assert summary["deselected_items"] == 0


def test_two_invocations_use_distinct_directories(tmp_path):
    root = _probe_root(tmp_path)
    first = _run_probe(root, BODY_OK)
    second = _run_probe(root, BODY_OK)
    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    first_dir = _report_dir(first)
    second_dir = _report_dir(second)
    assert first_dir != second_dir
    assert {path.name for path in _artifacts_root(root).iterdir()} == {
        first_dir.name,
        second_dir.name,
    }


def test_auto_report_can_be_disabled(tmp_path):
    root = _probe_root(tmp_path)
    flag = _run_probe(root, BODY_OK, "--eci-no-auto-report")
    assert flag.returncode == 0, flag.stdout + flag.stderr
    assert not _artifacts_root(root).exists()

    env = _run_probe(root, BODY_OK, env_overrides={"ECI_PYTEST_NO_AUTO_REPORT": "1"})
    assert env.returncode == 0, env.stdout + env.stderr
    assert not _artifacts_root(root).exists()


def test_explicit_native_report_paths_are_not_overwritten(tmp_path):
    root = _probe_root(tmp_path)
    explicit_log = tmp_path / "explicit" / "custom-report.jsonl"
    explicit_log.parent.mkdir()
    explicit_junit = tmp_path / "explicit" / "custom-junit.xml"
    result = _run_probe(
        root,
        BODY_OK,
        f"--report-log={explicit_log}",
        f"--junitxml={explicit_junit}",
    )
    assert result.returncode == 0, result.stdout + result.stderr

    invocation = _report_dir(result)
    summary = _read_json(invocation / "summary.json")
    assert summary["report_log_explicit"] is True
    assert summary["junit_explicit"] is True
    assert Path(summary["report_log"]) == explicit_log
    assert Path(summary["junit_xml"]) == explicit_junit
    assert explicit_log.is_file()
    assert explicit_junit.is_file()
    assert not (invocation / "pytest-reportlog.jsonl").exists()
    assert not (invocation / "junit.xml").exists()
    assert summary["report_delivery"]["status"] == "complete"


def test_collect_only_records_selection_without_execution(tmp_path):
    root = _probe_root(tmp_path)
    result = _run_probe(root, BODY_OK, "--collect-only")
    assert result.returncode == 0, result.stdout + result.stderr
    invocation = _report_dir(result)
    summary = _read_json(invocation / "summary.json")
    assert summary["collect_only"] is True
    assert summary["run_mode"] == "collect_only"
    selection = summary["selection"]
    assert selection["selected_items"] == 2
    assert sorted(selection["selected_node_ids"]) == [
        "tests/test_probe.py::test_ok",
        "tests/test_probe.py::test_skip",
    ]
    assert summary["report_complete"] is True
    assert summary["report_delivery"]["status"] == "complete"
    assert (invocation / "pytest-reportlog.jsonl").is_file()


def test_no_tests_collected_preserves_native_exit(tmp_path):
    root = _probe_root(tmp_path)
    result = _run_probe(root, "")
    assert result.returncode == 5, result.stdout + result.stderr
    summary = _read_json(_report_dir(result) / "summary.json")
    assert summary["exitstatus"] == 5
    assert summary["session_finished"] is True
    assert summary["selection"]["selected_items"] == 0
    assert summary["selection"]["selected_node_ids"] == []
    assert summary["report_delivery"]["status"] == "complete"


def test_failed_and_errored_sessions_keep_native_exit(tmp_path):
    failed_root = _probe_root(tmp_path / "failed")
    failed = _run_probe(failed_root, BODY_FAIL)
    assert failed.returncode == 1, failed.stdout + failed.stderr
    failed_summary = _read_json(_report_dir(failed) / "summary.json")
    assert failed_summary["exitstatus"] == 1
    assert failed_summary["report_delivery"]["status"] == "complete"

    error_root = _probe_root(tmp_path / "error")
    errored = _run_probe(error_root, BODY_ERROR)
    assert errored.returncode == 1, errored.stdout + errored.stderr
    error_summary = _read_json(_report_dir(errored) / "summary.json")
    assert error_summary["exitstatus"] == 1
    assert error_summary["report_delivery"]["status"] == "complete"


def test_early_exit_preserves_native_exit(tmp_path):
    root = _probe_root(tmp_path)
    result = _run_probe(root, BODY_EXIT)
    assert result.returncode == 2, result.stdout + result.stderr
    invocation = _report_dir(result)
    summary = _read_json(invocation / "summary.json")
    assert summary["exitstatus"] == 2
    assert summary["session_finished"] is True
    events = _reportlog_events(invocation / "pytest-reportlog.jsonl")
    assert events[-1]["$report_type"] == "SessionFinish"
    assert events[-1]["exitstatus"] == 2


def test_keyboard_interrupt_preserves_native_exit(tmp_path):
    root = _probe_root(tmp_path)
    result = _run_probe(root, BODY_INTERRUPT)
    assert result.returncode == 2, result.stdout + result.stderr
    summary = _read_json(_report_dir(result) / "summary.json")
    assert summary["exitstatus"] == 2
    assert summary["session_finished"] is True
    assert summary["report_delivery"]["status"] == "complete"


def test_keyword_selection_and_deselection_are_recorded(tmp_path):
    root = _probe_root(tmp_path)
    result = _run_probe(root, BODY_FILTER, "-k", "keep")
    assert result.returncode == 0, result.stdout + result.stderr
    summary = _read_json(_report_dir(result) / "summary.json")
    selection = summary["selection"]
    assert selection["selected_items"] == 1
    assert selection["selected_node_ids"] == ["tests/test_probe.py::test_keep"]
    assert selection["deselected_items"] == 2
    assert selection["deselection_observed"] is True
    assert selection["complete"] is True
    assert selection["collected_items"] == 3


def test_marker_selection_and_deselection_are_recorded(tmp_path):
    root = _probe_root(tmp_path)
    result = _run_probe(root, BODY_MARKER, "-m", "not slow")
    assert result.returncode == 0, result.stdout + result.stderr
    summary = _read_json(_report_dir(result) / "summary.json")
    selection = summary["selection"]
    assert selection["selected_items"] == 1
    assert selection["selected_node_ids"] == ["tests/test_probe.py::test_fast"]
    assert selection["deselected_items"] == 1
    assert selection["deselection_observed"] is True
    assert selection["complete"] is True
    assert selection["collected_items"] == 2


def test_explicit_durations_options_are_preserved(tmp_path):
    root = _probe_root(tmp_path)
    result = _run_probe(root, BODY_OK, "--durations=0", "--durations-min=0")
    assert result.returncode == 0, result.stdout + result.stderr
    summary = _read_json(_report_dir(result) / "summary.json")
    assert summary["durations"]["effective"] == 0
    assert summary["durations"]["min"] == 0.0


def _stub_config(root: Path, reporter: _Reporter) -> SimpleNamespace:
    return SimpleNamespace(
        option=SimpleNamespace(
            report_log=None,
            xmlpath=None,
            collectonly=False,
            durations=10,
            durations_min=0.05,
            numprocesses=None,
            keyword=None,
            markexpr=None,
            deselect=(),
            lf=False,
            failedfirst=False,
            newfirst=False,
        ),
        rootpath=root,
        invocation_params=SimpleNamespace(args=["probe"]),
        pluginmanager=SimpleNamespace(get_plugin=lambda name: reporter),
    )


class _Reporter:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.separators: list[str] = []

    def write_line(self, message: str, **markup) -> None:
        self.lines.append(message)

    def write_sep(self, sep: str, title: str = "", **markup) -> None:
        self.separators.append(title)


def test_xdist_worker_inventories_are_not_summed():
    plugin = report_plugin._InvocationReportPlugin({})
    plugin.pytest_xdist_node_collection_finished(
        SimpleNamespace(workerid="gw0"), ["tests/a.py::t1", "tests/a.py::t2"]
    )
    plugin.pytest_xdist_node_collection_finished(
        SimpleNamespace(workerid="gw1"), ["tests/a.py::t1", "tests/a.py::t2"]
    )
    selection = plugin._selection(None)
    assert selection["mode"] == "xdist"
    assert selection["selected_items"] is None
    assert selection["deselected_items"] is None
    assert selection["worker_inventories"] == {
        "gw0": {"count": 2, "node_ids": ["tests/a.py::t1", "tests/a.py::t2"]},
        "gw1": {"count": 2, "node_ids": ["tests/a.py::t1", "tests/a.py::t2"]},
    }
    assert selection["complete"] is False


def test_sealing_failure_preserves_native_exit(tmp_path, monkeypatch):
    invocation = tmp_path / "invocation"
    invocation.mkdir()
    reportlog = invocation / "pytest-reportlog.jsonl"
    reportlog.write_text(
        json.dumps({"$report_type": "SessionStart"}) + "\n", encoding="utf-8"
    )
    junit = invocation / "junit.xml"
    junit.write_text("<testsuites/>\n", encoding="utf-8")
    reporter = _Reporter()
    config = _stub_config(tmp_path, reporter)
    meta = report_plugin._build_meta(
        config,
        invocation_id="probe-invocation",
        out_dir=invocation,
        report_log=reportlog,
        junit_xml=junit,
        report_log_explicit=False,
        junit_explicit=False,
    )
    (invocation / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    real_write = report_plugin._write_json_atomic

    def fail_summary(path: Path, payload: dict) -> None:
        if path.name == "summary.json":
            raise OSError("probe sealing failure")
        real_write(path, payload)

    monkeypatch.setattr(report_plugin, "_write_json_atomic", fail_summary)
    plugin = report_plugin._InvocationReportPlugin(meta)
    plugin.collection_observed = True
    plugin.collection_mode = "serial"
    plugin.selected_node_ids = ["tests/test_probe.py::test_ok"]
    session = SimpleNamespace(exitstatus=1, config=config)

    plugin._finalize(session)

    assert session.exitstatus == 1
    assert not (invocation / "summary.json").exists()
    metadata = json.loads((invocation / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["phase"] == "delivery_failed"
    assert metadata["session_finished"] is False
    assert metadata["report_delivery"]["status"] == "failed"
    assert metadata["report_delivery"]["native_exit_preserved"] is True
    assert any("delivery failed" in line for line in reporter.lines)


def test_missing_native_artifact_marks_report_incomplete(tmp_path):
    invocation = tmp_path / "invocation"
    invocation.mkdir()
    reportlog = invocation / "pytest-reportlog.jsonl"
    reportlog.write_text(
        json.dumps({"$report_type": "SessionStart"}) + "\n", encoding="utf-8"
    )
    junit = invocation / "junit.xml"
    config = _stub_config(tmp_path, _Reporter())
    meta = report_plugin._build_meta(
        config,
        invocation_id="probe-invocation",
        out_dir=invocation,
        report_log=reportlog,
        junit_xml=junit,
        report_log_explicit=False,
        junit_explicit=False,
    )
    plugin = report_plugin._InvocationReportPlugin(meta)
    delivery = plugin._delivery()
    assert delivery["status"] == "incomplete"
    assert delivery["missing"] == ["junit_xml"]
    assert delivery["native_exit_preserved"] is True
