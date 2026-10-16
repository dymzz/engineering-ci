"""Raw archive entry points call installed vesryn-log without loading the Harness."""

import builtins
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from engineering_ci.cli import main
from engineering_ci.cli.trace import replay_file
from engineering_ci.tracing.store import ArchiveError


def test_real_inspection_works_without_vesryn(tmp_path):
    source = tmp_path / "rollout.jsonl"
    source.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "timestamp": "2026-10-16T00:00:00Z",
                "payload": {
                    "id": "d3548b41-df50-4249-8bd7-e873b3056ba8",
                    "cli_version": "0.162.0-alpha.2",
                },
            }
        )
        + "\n"
    )
    script = """
import importlib.abc, sys
class NoHarness(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == 'vesryn' or name.startswith('vesryn.'):
            raise AssertionError('Raw logging imported the Harness')
sys.meta_path.insert(0, NoHarness())
from engineering_ci.cli import main
assert main(['trace', 'inspect', sys.argv[1], '--json']) == 0
assert not any(name == 'vesryn' or name.startswith('vesryn.') for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(source)],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert json.loads(result.stdout)["operation"] == "trace.inspect"


def test_sources_uses_logging_store_and_catalog(monkeypatch, capsys):
    from vesryn_log.archive import store as log_store
    from vesryn_log.archive.catalog import TraceCatalog

    from engineering_ci.tracing.store import ArchiveStore

    calls = []

    @contextmanager
    def opened(config, *, environ=None, store_type=None):
        assert store_type is ArchiveStore
        calls.append(config.schema)
        yield SimpleNamespace()

    monkeypatch.setattr(log_store, "open_store", opened)

    def sources(self, *, limit, after):
        calls.append((limit, after))
        return {"items": [], "limit": limit, "next_after": None}

    monkeypatch.setattr(TraceCatalog, "sources", sources)
    assert main(["trace", "sources", "--limit", "3", "--json"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["items"] == [] and value["status"] == "passed"
    assert calls == ["engineering_ci_trace", (3, None)]


def test_missing_recording_api_reports_installation_without_harness(
    monkeypatch, capsys
):
    original = builtins.__import__

    def importing(name, *args, **kwargs):
        if name == "vesryn_log" or name.startswith("vesryn_log."):
            raise ModuleNotFoundError("Logging not installed", name="vesryn_log")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importing)
    assert main(["trace", "sources", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "missing_tool"


def test_archive_failure_stays_nonzero_without_credential_text(monkeypatch, capsys):
    from vesryn_log.archive import store as log_store

    @contextmanager
    def denied(*args, **kwargs):
        raise ArchiveError(
            "connection_failed", "Archive is unavailable", sqlstate="28000"
        )
        yield

    monkeypatch.setattr(log_store, "open_store", denied)
    assert main(["trace", "sources", "--json"]) == 2
    value = json.loads(capsys.readouterr().out)
    assert value["status"] == "connection_failed" and value["sqlstate"] == "28000"


def test_replay_verifies_whole_stream_before_publishing(tmp_path):
    class Broken:
        def replay(self, snapshot):
            yield SimpleNamespace(raw=b"first\n")
            raise ArchiveError("corrupted", "Digest mismatch")

    output = tmp_path / "replay.jsonl"
    with pytest.raises(ArchiveError, match="Digest"):
        replay_file(Broken(), "f" * 64, output)
    assert not output.exists() and list(tmp_path.iterdir()) == []

    class Good:
        def replay(self, snapshot):
            yield SimpleNamespace(raw=b"first\n")
            yield SimpleNamespace(raw=b"second\n")

    replay_file(Good(), "f" * 64, output)
    assert output.read_bytes() == b"first\nsecond\n"
    with pytest.raises(ArchiveError, match="already exists"):
        replay_file(Good(), "f" * 64, output)
    assert output.read_bytes() == b"first\nsecond\n"
