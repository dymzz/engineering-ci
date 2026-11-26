"""Probe that normal ECI pytest invocations auto-retain reportlog + JUnit (#167 R3)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_probe(
    *extra: str, env: dict | None = None
) -> tuple[subprocess.CompletedProcess, Path]:
    """Run an isolated probe under tests/ so repo conftest + report plugin load."""
    probe_dir = Path(tempfile.mkdtemp(prefix="eci_report_probe_", dir=ROOT / "tests"))
    try:
        (probe_dir / "test_probe.py").write_text(
            "import pytest\n"
            "def test_ok():\n"
            "    assert 1 == 1\n"
            "def test_skip():\n"
            "    pytest.skip('probe')\n",
            encoding="utf-8",
        )
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:tach",
            str(probe_dir),
            *extra,
        ]
        merged = os.environ.copy()
        if env:
            merged.update(env)
        result = subprocess.run(
            cmd,
            cwd=ROOT,
            env=merged,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return result, probe_dir
    except Exception:
        shutil.rmtree(probe_dir, ignore_errors=True)
        raise


def _cleanup(probe_dir: Path) -> None:
    shutil.rmtree(probe_dir, ignore_errors=True)


def _latest_invocation_dirs(before: set[Path]) -> list[Path]:
    base = ROOT / ".artifacts" / "pytest"
    if not base.is_dir():
        return []
    return sorted(
        path for path in base.iterdir() if path.is_dir() and path not in before
    )


def test_two_invocations_create_distinct_report_dirs():
    base = ROOT / ".artifacts" / "pytest"
    before = set(base.iterdir()) if base.is_dir() else set()

    first, first_probe = _run_probe()
    try:
        assert first.returncode == 0, first.stdout + first.stderr
        after_first = _latest_invocation_dirs(before)
        assert after_first, first.stdout + first.stderr
        first_dir = after_first[-1]
        assert (first_dir / "report.jsonl").is_file()
        assert (first_dir / "junit.xml").is_file()
        assert (first_dir / "summary.json").is_file()
        summary = json.loads((first_dir / "summary.json").read_text(encoding="utf-8"))
        assert summary["session_finished"] is True
        assert summary["selected_items"] >= 1
        assert "wall_time_seconds" in summary
        combined = first.stdout + first.stderr
        assert "ECI pytest report" in combined
        assert str(first_dir) in combined
    finally:
        _cleanup(first_probe)

    second, second_probe = _run_probe()
    try:
        assert second.returncode == 0, second.stdout + second.stderr
        after_second = _latest_invocation_dirs(before)
        assert len(after_second) >= 2
        second_dir = after_second[-1]
        assert second_dir != first_dir
        assert (second_dir / "report.jsonl").is_file()
        assert (second_dir / "junit.xml").is_file()
    finally:
        _cleanup(second_probe)


def test_collect_only_marks_metadata_and_disable_flag():
    base = ROOT / ".artifacts" / "pytest"
    before = set(base.iterdir()) if base.is_dir() else set()

    collected, probe = _run_probe("--collect-only")
    try:
        assert collected.returncode == 0, collected.stdout + collected.stderr
        dirs = _latest_invocation_dirs(before)
        assert dirs
        meta = json.loads((dirs[-1] / "summary.json").read_text(encoding="utf-8"))
        assert meta["collect_only"] is True
    finally:
        _cleanup(probe)

    before_disable = set(base.iterdir()) if base.is_dir() else set()
    disabled, probe2 = _run_probe(env={"ECI_PYTEST_NO_AUTO_REPORT": "1"})
    try:
        assert disabled.returncode == 0, disabled.stdout + disabled.stderr
        assert _latest_invocation_dirs(before_disable) == []
    finally:
        _cleanup(probe2)
