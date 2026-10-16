"""Explicit PostgreSQL acceptance of the raw CLI without the optional Harness."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import trace_store

archive = trace_store.archive
rollout = trace_store.rollout

NO_HARNESS = """
import importlib.abc, sys
class NoHarness(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'vesryn' or fullname.startswith('vesryn.'):
            raise AssertionError('Raw archive operation imported the Harness')
sys.meta_path.insert(0, NoHarness())
from engineering_ci.cli import main
raise SystemExit(main(sys.argv[1:]))
"""


def test_real_raw_cli_roundtrip_without_vesryn(archive, tmp_path):
    """Use real CLI subprocesses, Psycopg and vesryn-log for every archive step."""
    config = tmp_path / "tracing.toml"
    config.write_text(
        f'schema_version = 1\n[archive]\nschema = "{archive.config.schema}"\n',
        encoding="utf-8",
    )
    source = rollout(tmp_path / "source.jsonl")

    def invoke(*args, expected=0):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                NO_HARNESS,
                "trace",
                *args,
                "--config",
                str(config),
                "--json",
            ],
            cwd=tmp_path,
            env={
                **os.environ,
                "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
            },
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        # Never emit raw driver stderr or connection parameters on failure.
        assert result.returncode == expected, "Archive CLI returned an unexpected exit"
        value = json.loads(result.stdout)
        assert value["exit_code"] == expected
        return value

    assert invoke("status")["status"] == "passed"
    initialized = invoke("init")
    assert not initialized["created"] and initialized["archive_schema_version"] == 3
    assert invoke("inspect", str(source.path))["status"] == "passed"
    captured = invoke("import", str(source.path))
    repeated = invoke("import", str(source.path))
    assert repeated["snapshot_id"] == captured["snapshot_id"]
    listed = invoke("sources", "--limit", "1")
    assert listed["items"][0]["source"] == source.identity.source
    snapshots = invoke("snapshots", "--source", source.identity.source)
    assert len(snapshots["items"]) == 1
    assert invoke("status", "--snapshot", captured["snapshot_id"])["status"] == "passed"
    output = tmp_path / "replay.jsonl"
    invoke("replay", captured["snapshot_id"], "--out", str(output))
    assert output.read_bytes() == source.path.read_bytes()
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    conflict = invoke(
        "replay", captured["snapshot_id"], "--out", str(output), expected=2
    )
    assert conflict["status"] == "output_exists"
    assert hashlib.sha256(output.read_bytes()).hexdigest() == checksum
