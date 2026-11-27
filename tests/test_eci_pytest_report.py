"""Probes for ECI repo-local pytest invocation reports (#167 R3/R6).

Each probe runs pytest in a throwaway ECI-shaped workspace with its own report
root, so assertions consume that invocation's exact directory and native files
instead of scanning a shared ``.artifacts/pytest`` for a "latest" entry.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import _eci_pytest_report as report_plugin
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_ROOT / "tests"

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
BODY_FAIL_FIRST = (
    "def test_fail_first():\n    assert 1 == 2\n\ndef test_second():\n    assert True\n"
)
BODY_COLLECTION_ERROR = "import definitely_missing_module\n"
BODY_TEARDOWN_ERROR = (
    "import pytest\n"
    "\n"
    "@pytest.fixture\n"
    "def teardown_broken():\n"
    "    yield\n"
    "    raise RuntimeError('teardown boom')\n"
    "\n"
    "def test_teardown_error(teardown_broken):\n"
    "    assert True\n"
)
BODY_SETUP_SKIP = (
    "import pytest\n"
    "\n"
    "@pytest.fixture\n"
    "def skipping():\n"
    "    pytest.skip('setup skip')\n"
    "\n"
    "def test_setup_skip(skipping):\n"
    "    assert True\n"
)
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
BODY_SETUP_INTERRUPT = (
    "import pytest\n"
    "\n"
    "@pytest.fixture\n"
    "def interrupt_setup():\n"
    "    raise KeyboardInterrupt\n"
    "\n"
    "def test_first(interrupt_setup):\n"
    "    assert True\n"
    "\n"
    "def test_second():\n"
    "    assert True\n"
)
BODY_COLLECTION_INTERRUPT = "raise KeyboardInterrupt\n"
BODY_DUPLICATES = (
    "def test_first():\n    assert True\n\ndef test_second():\n    assert True\n"
)
BODY_WRITER_FAILURE_CONFTEST = (
    "from pathlib import Path\n"
    "\n"
    "def pytest_sessionstart(session):\n"
    "    target = session.config.option.xmlpath\n"
    "    if target:\n"
    "        path = Path(target)\n"
    "        if path.exists():\n"
    "            path.unlink()\n"
    "        path.mkdir(parents=True, exist_ok=True)\n"
)
BODY_NON_WRITER_SESSIONFINISH_CONFTEST = (
    "import pytest\n"
    "\n"
    "@pytest.hookimpl(trylast=True)\n"
    "def pytest_sessionfinish(session, exitstatus):\n"
    "    raise RuntimeError('non-writer sessionfinish failure')\n"
)
BODY_METADATA_FAILURE_CONFTEST = (
    "import _eci_pytest_report\n"
    "\n"
    "_real_write = _eci_pytest_report._write_json_atomic\n"
    "\n"
    "def _fail_metadata(path, payload):\n"
    "    if path.name == 'metadata.json':\n"
    "        raise OSError('injected metadata write failure')\n"
    "    return _real_write(path, payload)\n"
    "\n"
    "_eci_pytest_report._write_json_atomic = _fail_metadata\n"
)
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
    extra_files: dict[str, str] | None = None,
    extra_targets: tuple[str, ...] = (),
) -> subprocess.CompletedProcess:
    files = {"test_probe.py": body}
    if extra_files:
        files.update(extra_files)
    for name, content in files.items():
        path = root / "tests" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    targets = [
        "tests/test_probe.py",
        *(f"tests/{name}" for name in extra_targets),
    ]
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
        *targets,
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


def _run_pytest(
    root: Path,
    *args: str,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
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
        *args,
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
    for line in combined.splitlines():
        if "ECI pytest report" not in line or "dir=" not in line:
            continue
        _, _, tail = line.partition("dir=")
        candidate = tail.strip().rstrip("=").strip()
        if candidate:
            return Path(candidate)
    raise AssertionError(combined)


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
    assert summary["report_sealed"] is True
    delivery = summary["report_delivery"]
    assert delivery["status"] == "limited"
    assert delivery["native_artifacts_available"] is True
    assert delivery["limited_checks_passed"] is True
    assert delivery["attribution_verified"] is True
    assert delivery["content_completeness"] == "unverified"
    assert delivery["report_sealed"] is True
    assert "report_complete" not in delivery
    assert summary["native_exit_observation"]["status"] == "unknown_in_process"
    assert summary["native_exit_observation"]["outer_process_exit"] is None
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
    assert summary["collection"]["complete"] is True
    assert summary["collection"]["hook_returned"] is True
    assert summary["collection"]["hook_exception"] is None
    execution = summary["execution"]
    assert execution["mode"] == "run"
    assert execution["status"] == "complete"
    assert execution["started_items"] == 2
    assert execution["completed_items"] == 2
    assert execution["not_started_node_ids"] == []
    assert execution["incomplete_node_ids"] == []
    phase_counts = execution["phase_counts"]
    assert phase_counts["setup"] == {"passed": 2}
    assert phase_counts["call"] == {"passed": 1, "skipped": 1}
    assert phase_counts["teardown"] == {"passed": 2}


def test_report_directory_with_spaces_is_parsed(tmp_path):
    root = _probe_root(tmp_path / "space dir")
    result = _run_probe(root, BODY_OK)
    assert result.returncode == 0, result.stdout + result.stderr
    invocation = _report_dir(result)
    assert "space dir" in str(invocation)
    assert (invocation / "summary.json").is_file()
    assert (invocation / "pytest-reportlog.jsonl").is_file()


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
    delivery = summary["report_delivery"]
    assert delivery["status"] == "unknown"
    assert summary["report_sealed"] is True
    assert delivery["native_artifacts_available"] is True
    assert delivery["limited_checks_passed"] is True
    assert delivery["attribution_verified"] is False
    assert delivery["content_completeness"] == "unverified"
    assert "report_complete" not in delivery
    assert delivery["artifacts"]["report_log"]["attribution"] == (
        "explicit_path_unverified"
    )
    assert delivery["artifacts"]["junit_xml"]["attribution"] == (
        "explicit_path_unverified"
    )
    assert delivery["artifacts"]["report_log"]["status"] == "unknown"
    assert delivery["artifacts"]["junit_xml"]["status"] == "unknown"


def test_explicit_junit_path_matches_native_expansion(tmp_path):
    root = _probe_root(tmp_path)
    target = tmp_path / "expanded junit.xml"
    token = "%ECI_JUNIT_TARGET%" if os.name == "nt" else "${ECI_JUNIT_TARGET}"
    result = _run_probe(
        root,
        BODY_OK,
        f"--junitxml={token}",
        env_overrides={"ECI_JUNIT_TARGET": str(target)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    summary = _read_json(_report_dir(result) / "summary.json")
    assert Path(summary["junit_xml"]) == target
    assert target.is_file()
    delivery = summary["report_delivery"]
    assert delivery["status"] == "unknown"
    assert delivery["native_artifacts_available"] is True
    assert delivery["artifacts"]["junit_xml"]["exists"] is True
    assert "junit_xml" not in delivery["missing"]


def test_explicit_junit_writer_failure_records_outcome_and_native_exit(tmp_path):
    root = _probe_root(tmp_path)
    target = tmp_path / "explicit-junit.xml"
    (root / "conftest.py").write_text(BODY_WRITER_FAILURE_CONFTEST, encoding="utf-8")
    result = _run_probe(root, BODY_OK, f"--junitxml={target}")
    assert result.returncode == 1, result.stdout + result.stderr
    invocation = _report_dir(result)
    summary = _read_json(invocation / "summary.json")
    metadata = _read_json(invocation / "metadata.json")
    assert metadata["phase"] == "configured"
    assert summary["phase"] == "finished"
    assert Path(summary["junit_xml"]) == target
    assert summary["exitstatus"] == 0
    assert summary["exitstatus_scope"] == "pytest_sessionfinish_hook_stage"
    assert summary["session_exitstatus"] == 0
    sessionfinish_hook_exception = summary["sessionfinish_hook_exception"]
    assert sessionfinish_hook_exception is not None
    assert sessionfinish_hook_exception["type"] in {
        "IsADirectoryError",
        "PermissionError",
        "OSError",
    }
    assert summary["native_exit_observation"]["status"] == "unknown_in_process"
    assert summary["native_exit_observation"]["outer_process_exit"] is None
    assert (
        summary["native_exit_observation"]["sessionfinish_hook_exception"]
        == sessionfinish_hook_exception
    )
    assert summary["report_sealed"] is True
    delivery = summary["report_delivery"]
    assert delivery["sessionfinish_hook_exception"] == sessionfinish_hook_exception
    assert delivery["native_artifacts_available"] is False
    assert "junit_xml" in delivery["missing"]
    assert delivery["status"] == "incomplete"


def test_auto_junit_writer_failure_records_outcome_and_native_exit(tmp_path):
    root = _probe_root(tmp_path)
    (root / "conftest.py").write_text(BODY_WRITER_FAILURE_CONFTEST, encoding="utf-8")
    result = _run_probe(root, BODY_OK)
    assert result.returncode == 1, result.stdout + result.stderr
    invocation = _report_dir(result)
    summary = _read_json(invocation / "summary.json")
    metadata = _read_json(invocation / "metadata.json")
    assert metadata["phase"] == "configured"
    assert summary["phase"] == "finished"
    assert summary["exitstatus"] == 0
    sessionfinish_hook_exception = summary["sessionfinish_hook_exception"]
    assert sessionfinish_hook_exception is not None
    assert sessionfinish_hook_exception["type"] in {
        "IsADirectoryError",
        "PermissionError",
        "OSError",
    }
    assert summary["native_exit_observation"]["outer_process_exit"] is None
    assert (
        summary["native_exit_observation"]["sessionfinish_hook_exception"]
        == sessionfinish_hook_exception
    )
    assert summary["report_sealed"] is True
    delivery = summary["report_delivery"]
    assert delivery["sessionfinish_hook_exception"] == sessionfinish_hook_exception
    assert delivery["native_artifacts_available"] is False
    assert "junit_xml" in delivery["missing"]
    assert delivery["status"] == "incomplete"


def test_non_writer_sessionfinish_hook_exception_keeps_limited_delivery(tmp_path):
    root = _probe_root(tmp_path)
    (root / "conftest.py").write_text(
        BODY_NON_WRITER_SESSIONFINISH_CONFTEST, encoding="utf-8"
    )
    result = _run_probe(root, BODY_OK)
    assert result.returncode == 1, result.stdout + result.stderr
    invocation = _report_dir(result)
    summary = _read_json(invocation / "summary.json")
    assert summary["exitstatus"] == 0
    exception = summary["sessionfinish_hook_exception"]
    assert exception is not None
    assert exception["type"] == "RuntimeError"
    assert summary["native_exit_observation"]["outer_process_exit"] is None
    assert summary["report_sealed"] is True
    assert (invocation / "junit.xml").is_file()
    assert (invocation / "pytest-reportlog.jsonl").is_file()
    delivery = summary["report_delivery"]
    assert delivery["status"] == "limited"
    assert delivery["native_artifacts_available"] is True
    assert delivery["limited_checks_passed"] is True
    assert delivery["attribution_verified"] is True
    assert delivery["content_completeness"] == "unverified"
    assert delivery["sessionfinish_hook_exception"] == exception


def test_metadata_initialization_failure_does_not_leave_auto_options(tmp_path):
    root = _probe_root(tmp_path)
    (root / "conftest.py").write_text(BODY_METADATA_FAILURE_CONFTEST, encoding="utf-8")
    result = _run_probe(root, BODY_OK)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ECI pytest report disabled" in result.stdout + result.stderr
    if _artifacts_root(root).exists():
        for invocation in _artifacts_root(root).iterdir():
            assert not (invocation / "metadata.json").exists()
            assert not (invocation / "summary.json").exists()
            assert not (invocation / "pytest-reportlog.jsonl").exists()
            assert not (invocation / "junit.xml").exists()


def test_metadata_initialization_failure_preserves_explicit_junit(tmp_path):
    root = _probe_root(tmp_path)
    (root / "conftest.py").write_text(BODY_METADATA_FAILURE_CONFTEST, encoding="utf-8")
    target = tmp_path / "explicit-metadata-failure.xml"
    result = _run_probe(root, BODY_OK, f"--junitxml={target}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert target.is_file()
    if _artifacts_root(root).exists():
        for invocation in _artifacts_root(root).iterdir():
            assert not (invocation / "metadata.json").exists()
            assert not (invocation / "summary.json").exists()
            assert not (invocation / "pytest-reportlog.jsonl").exists()
            assert not (invocation / "junit.xml").exists()


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
    assert summary["report_sealed"] is True
    assert summary["report_delivery"]["status"] == "limited"
    assert (invocation / "pytest-reportlog.jsonl").is_file()
    execution = summary["execution"]
    assert execution["mode"] == "collect_only"
    assert execution["status"] == "not_run"
    assert execution["started_items"] == 0
    assert execution["completed_items"] == 0
    assert execution["complete"] is False
    assert execution["unknown_reasons"] == ["collect_only_no_execution_intent"]


def test_no_tests_collected_preserves_native_exit(tmp_path):
    root = _probe_root(tmp_path)
    result = _run_probe(root, "")
    assert result.returncode == 5, result.stdout + result.stderr
    summary = _read_json(_report_dir(result) / "summary.json")
    assert summary["exitstatus"] == 5
    assert summary["session_finished"] is True
    assert summary["selection"]["selected_items"] == 0
    assert summary["selection"]["selected_node_ids"] == []
    assert summary["report_delivery"]["status"] == "limited"
    execution = summary["execution"]
    assert execution["status"] == "empty"
    assert execution["unknown_reasons"] == ["no_selected_items"]
    assert execution["started_items"] == 0


def test_failed_and_errored_sessions_keep_native_exit(tmp_path):
    failed_root = _probe_root(tmp_path / "failed")
    failed = _run_probe(failed_root, BODY_FAIL)
    assert failed.returncode == 1, failed.stdout + failed.stderr
    failed_summary = _read_json(_report_dir(failed) / "summary.json")
    assert failed_summary["exitstatus"] == 1
    assert failed_summary["report_delivery"]["status"] == "limited"
    assert failed_summary["execution"]["status"] == "complete"
    assert failed_summary["execution"]["started_items"] == 1
    assert failed_summary["execution"]["completed_items"] == 1

    error_root = _probe_root(tmp_path / "error")
    errored = _run_probe(error_root, BODY_ERROR)
    assert errored.returncode == 1, errored.stdout + errored.stderr
    error_summary = _read_json(_report_dir(errored) / "summary.json")
    assert error_summary["exitstatus"] == 1
    assert error_summary["report_delivery"]["status"] == "limited"
    assert error_summary["execution"]["status"] == "complete"
    assert error_summary["execution"]["started_items"] == 1
    assert error_summary["execution"]["completed_items"] == 1


def test_stop_first_preserves_selected_but_not_started(tmp_path):
    root = _probe_root(tmp_path)
    result = _run_probe(root, BODY_FAIL_FIRST, "-x")
    assert result.returncode == 1, result.stdout + result.stderr
    summary = _read_json(_report_dir(result) / "summary.json")
    selection = summary["selection"]
    assert selection["selected_items"] == 2
    assert sorted(selection["selected_node_ids"]) == [
        "tests/test_probe.py::test_fail_first",
        "tests/test_probe.py::test_second",
    ]
    assert selection["deselected_items"] == 0
    execution = summary["execution"]
    assert execution["status"] == "incomplete"
    assert execution["complete"] is False
    assert execution["started_node_ids"] == ["tests/test_probe.py::test_fail_first"]
    assert execution["completed_node_ids"] == ["tests/test_probe.py::test_fail_first"]
    assert execution["not_started_node_ids"] == ["tests/test_probe.py::test_second"]
    assert execution["incomplete_node_ids"] == []


def test_collection_error_is_not_declared_complete(tmp_path):
    root = _probe_root(tmp_path)
    result = _run_probe(
        root,
        BODY_OK,
        extra_files={"test_broken.py": BODY_COLLECTION_ERROR},
        extra_targets=("test_broken.py",),
    )
    assert result.returncode == 2, result.stdout + result.stderr
    summary = _read_json(_report_dir(result) / "summary.json")
    assert summary["exitstatus"] == 2
    assert summary["selection"]["selected_items"] == 2
    assert summary["selection"]["complete"] is False
    assert "collection_incomplete" in summary["selection"]["unknown_reasons"]
    collection = summary["collection"]
    assert collection["complete"] is False
    assert collection["errors"]
    assert collection["errors"][0]["nodeid"].endswith("test_broken.py")
    execution = summary["execution"]
    assert execution["status"] == "unknown"
    assert execution["complete"] is False
    assert execution["started_items"] == 0
    assert "collection_incomplete" in execution["unknown_reasons"]


def test_teardown_error_and_setup_skip_are_completed(tmp_path):
    teardown_root = _probe_root(tmp_path / "teardown")
    teardown = _run_probe(teardown_root, BODY_TEARDOWN_ERROR)
    assert teardown.returncode == 1, teardown.stdout + teardown.stderr
    teardown_summary = _read_json(_report_dir(teardown) / "summary.json")
    assert teardown_summary["execution"]["status"] == "complete"
    assert teardown_summary["execution"]["started_items"] == 1
    assert teardown_summary["execution"]["completed_items"] == 1

    skip_root = _probe_root(tmp_path / "skip")
    skipped = _run_probe(skip_root, BODY_SETUP_SKIP)
    assert skipped.returncode == 0, skipped.stdout + skipped.stderr
    skip_summary = _read_json(_report_dir(skipped) / "summary.json")
    assert skip_summary["execution"]["status"] == "complete"
    assert skip_summary["execution"]["started_items"] == 1
    assert skip_summary["execution"]["completed_items"] == 1


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
    execution = summary["execution"]
    assert execution["status"] == "incomplete"
    assert execution["started_items"] == 1
    assert execution["completed_items"] == 0
    assert execution["incomplete_node_ids"] == ["tests/test_probe.py::test_stop"]


def test_keyboard_interrupt_preserves_native_exit(tmp_path):
    root = _probe_root(tmp_path)
    result = _run_probe(root, BODY_INTERRUPT)
    assert result.returncode == 2, result.stdout + result.stderr
    summary = _read_json(_report_dir(result) / "summary.json")
    assert summary["exitstatus"] == 2
    assert summary["session_finished"] is True
    assert summary["report_delivery"]["status"] == "limited"
    execution = summary["execution"]
    assert execution["status"] == "incomplete"
    assert execution["started_items"] == 1
    assert execution["completed_items"] == 0
    assert execution["incomplete_node_ids"] == ["tests/test_probe.py::test_interrupt"]


def test_setup_interrupt_uses_native_logstart(tmp_path):
    root = _probe_root(tmp_path)
    result = _run_probe(root, BODY_SETUP_INTERRUPT)
    assert result.returncode == 2, result.stdout + result.stderr
    summary = _read_json(_report_dir(result) / "summary.json")
    assert summary["exitstatus"] == 2
    assert summary["collection"]["complete"] is True
    execution = summary["execution"]
    assert execution["status"] == "incomplete"
    assert execution["started_items"] == 1
    assert execution["started_node_ids"] == ["tests/test_probe.py::test_first"]
    assert execution["completed_items"] == 0
    assert execution["incomplete_node_ids"] == ["tests/test_probe.py::test_first"]
    assert execution["not_started_node_ids"] == ["tests/test_probe.py::test_second"]


def test_collection_interrupt_and_invalid_target_are_incomplete(tmp_path):
    interrupt_root = _probe_root(tmp_path / "interrupt")
    interrupted = _run_probe(interrupt_root, BODY_COLLECTION_INTERRUPT)
    assert interrupted.returncode == 2, interrupted.stdout + interrupted.stderr
    interrupt_summary = _read_json(_report_dir(interrupted) / "summary.json")
    assert interrupt_summary["collection"]["complete"] is False
    assert interrupt_summary["collection"]["hook_returned"] is False
    assert interrupt_summary["collection"]["hook_exception"] == "KeyboardInterrupt"
    assert (
        "collection_interrupted_before_execution"
        in interrupt_summary["collection"]["unknown_reasons"]
    )
    assert interrupt_summary["selection"]["complete"] is False
    assert interrupt_summary["execution"]["status"] == "unknown"

    invalid_root = _probe_root(tmp_path / "invalid")
    (invalid_root / "tests" / "test_probe.py").write_text(BODY_OK, encoding="utf-8")
    invalid = _run_pytest(invalid_root, "tests/does_not_exist.py")
    assert invalid.returncode == 4, invalid.stdout + invalid.stderr
    invalid_summary = _read_json(_report_dir(invalid) / "summary.json")
    assert invalid_summary["exitstatus"] == 4
    assert invalid_summary["collection"]["complete"] is False
    assert invalid_summary["collection"]["hook_returned"] is False
    assert invalid_summary["collection"]["hook_exception"] == "UsageError"
    assert invalid_summary["collection"]["usage_error"] is True
    assert invalid_summary["execution"]["status"] == "unknown"


def test_duplicate_selection_preserves_instances(tmp_path):
    root = _probe_root(tmp_path)
    (root / "tests" / "test_probe.py").write_text(BODY_DUPLICATES, encoding="utf-8")
    result = _run_pytest(
        root,
        "--keep-duplicates",
        "tests/test_probe.py",
        "tests/test_probe.py",
        "-k",
        "second",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    summary = _read_json(_report_dir(result) / "summary.json")
    selection = summary["selection"]
    assert selection["selected_items"] == 2
    assert selection["selected_node_ids"] == [
        "tests/test_probe.py::test_second",
        "tests/test_probe.py::test_second",
    ]
    assert selection["deselected_items"] == 2
    assert selection["deselected_node_ids"] == [
        "tests/test_probe.py::test_first",
        "tests/test_probe.py::test_first",
    ]
    assert selection["collected_items"] == 4
    assert selection["complete"] is True
    execution = summary["execution"]
    assert execution["status"] == "complete"
    assert execution["started_items"] == 2
    assert execution["completed_items"] == 2
    assert execution["duplicate_node_ids"] == ["tests/test_probe.py::test_second"]
    assert summary["report_delivery"]["status"] == "limited"


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
    assert summary["execution"]["status"] == "complete"
    assert summary["execution"]["not_started_node_ids"] == []


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
    assert summary["execution"]["status"] == "complete"
    assert summary["execution"]["not_started_node_ids"] == []


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


def test_sealing_failure_keeps_native_session_and_separates_delivery(
    tmp_path, monkeypatch
):
    invocation = tmp_path / "invocation"
    invocation.mkdir()
    reportlog = invocation / "pytest-reportlog.jsonl"
    reportlog.write_text(
        json.dumps({"$report_type": "SessionStart"})
        + "\n"
        + json.dumps({"$report_type": "SessionFinish", "exitstatus": 1})
        + "\n",
        encoding="utf-8",
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
    plugin.collection_hook_returned = True
    plugin.collection_mode = "serial"
    plugin.selected_node_ids = ["tests/test_probe.py::test_ok"]
    plugin.started_counts["tests/test_probe.py::test_ok"] = 1
    plugin.finished_counts["tests/test_probe.py::test_ok"] = 1
    plugin.started_order.append("tests/test_probe.py::test_ok")
    plugin.finished_order.append("tests/test_probe.py::test_ok")
    session = SimpleNamespace(exitstatus=1, config=config)

    plugin._finalize(session, hook_exitstatus=1, outcome=SimpleNamespace(excinfo=None))

    assert session.exitstatus == 1
    assert not (invocation / "summary.json").exists()
    metadata = json.loads((invocation / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["phase"] == "delivery_failed"
    assert metadata["session_finished"] is True
    assert metadata["exitstatus"] == 1
    assert metadata["report_sealed"] is False
    assert metadata["sessionfinish_hook_exception"] is None
    assert metadata["native_exit_observation"]["status"] == "unknown_in_process"
    assert metadata["report_delivery"]["status"] == "failed"
    assert metadata["report_delivery"]["native_artifacts_available"] is True
    assert metadata["report_delivery"]["limited_checks_passed"] is True
    assert metadata["report_delivery"]["content_completeness"] == "unverified"
    assert metadata["report_delivery"]["summary_seal"] == "failed"
    assert metadata["report_delivery"]["report_sealed"] is False
    assert "report_complete" not in metadata["report_delivery"]
    assert metadata["execution"]["status"] == "complete"
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
    delivery = plugin._delivery(0)
    assert delivery["status"] == "incomplete"
    assert delivery["native_artifacts_available"] is False
    assert delivery["limited_checks_passed"] is False
    assert delivery["content_completeness"] == "unverified"
    assert delivery["missing"] == ["junit_xml"]
    assert delivery["unknown"] == ["report_log"]
    assert delivery["artifacts"]["report_log"]["session_finish_observed"] is False
    assert "native_exit_preserved" not in delivery


def test_invalid_native_artifacts_are_not_complete(tmp_path):
    invocation = tmp_path / "invocation"
    invocation.mkdir()
    reportlog = invocation / "pytest-reportlog.jsonl"
    reportlog.write_text("not json\n", encoding="utf-8")
    junit = invocation / "junit.xml"
    junit.write_text("not xml\n", encoding="utf-8")
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
    delivery = plugin._delivery(0)
    assert delivery["status"] == "incomplete"
    assert delivery["native_artifacts_available"] is True
    assert delivery["limited_checks_passed"] is False
    assert delivery["content_completeness"] == "unverified"
    assert set(delivery["invalid"]) == {"report_log", "junit_xml"}
    assert delivery["artifacts"]["report_log"]["status"] == "invalid"
    assert delivery["artifacts"]["junit_xml"]["status"] == "invalid"


def test_corrupt_prefix_is_only_a_limited_check(tmp_path):
    invocation = tmp_path / "invocation"
    invocation.mkdir()
    reportlog = invocation / "pytest-reportlog.jsonl"
    reportlog.write_text(
        "not-json\n"
        + json.dumps({"$report_type": "SessionFinish", "exitstatus": 0})
        + "\n",
        encoding="utf-8",
    )
    junit = invocation / "junit.xml"
    junit.write_text("<testsuites/>\n", encoding="utf-8")
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
    delivery = plugin._delivery(0)
    assert delivery["status"] == "limited"
    assert delivery["native_artifacts_available"] is True
    assert delivery["limited_checks_passed"] is True
    assert delivery["attribution_verified"] is True
    assert delivery["content_completeness"] == "unverified"
    assert "report_complete" not in delivery


def test_duplicate_node_ids_use_multiset_lifecycle(tmp_path):
    config = _stub_config(tmp_path, _Reporter())
    plugin = report_plugin._InvocationReportPlugin({})
    plugin.collection_observed = True
    plugin.collection_hook_returned = True
    plugin.collection_mode = "serial"
    plugin.selected_node_ids = ["tests/a.py::t1", "tests/a.py::t1"]
    plugin.started_counts["tests/a.py::t1"] = 1
    plugin.started_order.append("tests/a.py::t1")
    plugin.finished_counts["tests/a.py::t1"] = 1
    plugin.finished_order.append("tests/a.py::t1")
    execution = plugin._execution(config)
    assert execution["status"] == "incomplete"
    assert execution["not_started_node_ids"] == ["tests/a.py::t1"]
    assert execution["duplicate_node_ids"] == ["tests/a.py::t1"]


def _repo_subprocess_env(overrides: dict[str, str] | None = None) -> dict[str, str]:
    ignored = {
        "ECI_PYTEST_NO_AUTO_REPORT",
        "PYTEST_ADDOPTS",
        "PYTEST_CURRENT_TEST",
    }
    env = {key: value for key, value in os.environ.items() if key not in ignored}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if overrides:
        env.update(overrides)
    return env


def test_repo_conftest_loads_plugin_under_both_import_modes():
    target = (
        "tests/test_eci_pytest_report.py::"
        "test_missing_native_artifact_marks_report_incomplete"
    )
    cases = (
        (
            "prepend",
            ["--import-mode=prepend", "--eci-no-auto-report"],
            None,
        ),
        (
            "importlib",
            ["--import-mode=importlib", "--eci-no-auto-report"],
            None,
        ),
        (
            "importlib env disable",
            ["--import-mode=importlib"],
            {"ECI_PYTEST_NO_AUTO_REPORT": "1"},
        ),
    )
    for label, args, env in cases:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                *args,
                target,
            ],
            cwd=REPO_ROOT,
            env=_repo_subprocess_env(env),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        assert result.returncode == 0, f"{label}: {result.stdout}{result.stderr}"
