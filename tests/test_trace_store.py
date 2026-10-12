"""Archive boundary and command tests without assuming a host database in core CI."""

import json
from types import SimpleNamespace

import pytest

from engineering_ci.cli import trace
from engineering_ci.tracing import store
from engineering_ci.tracing.config import TraceConfig
from engineering_ci.tracing.sources.codex_rollout import record_at


def test_missing_database_does_not_load_driver(monkeypatch):
    monkeypatch.setattr(
        store.importlib, "import_module", lambda *_: pytest.fail("driver loaded")
    )
    with (
        pytest.raises(store.ArchiveError) as error,
        store.open_store(TraceConfig(), environ={}),
    ):
        pass
    assert error.value.status == "missing_configuration"


def test_missing_driver_is_not_network_failure(monkeypatch):
    def missing(*_):
        raise ImportError("SECRET")

    monkeypatch.setattr(store.importlib, "import_module", missing)
    with (
        pytest.raises(store.ArchiveError) as error,
        store.open_store(TraceConfig(), environ={"VESRYN_DATABASE": "SECRET"}),
    ):
        pass
    assert error.value.status == "missing_tool" and "SECRET" not in str(error.value)


def test_database_error_is_safe_and_preserves_classification(monkeypatch):
    class DriverError(Exception):
        sqlstate = "42501"

    def denied(*_, **__):
        raise DriverError("postgresql://SECRET@private-host/db")

    driver = SimpleNamespace(
        Error=DriverError,
        ProgrammingError=DriverError,
        conninfo=SimpleNamespace(conninfo_to_dict=lambda _: {}),
        connect=denied,
    )
    monkeypatch.setattr(store.importlib, "import_module", lambda _: driver)
    with (
        pytest.raises(store.ArchiveError) as error,
        store.open_store(TraceConfig(), environ={"VESRYN_DATABASE": "SECRET"}),
    ):
        pass
    assert error.value.status == "permission_denied" and error.value.sqlstate == "42501"
    assert "SECRET" not in str(error.value)


def test_migration_hash_is_independent_of_checkout_line_endings(tmp_path, monkeypatch):
    path = tmp_path / "migration.sql"
    monkeypatch.setattr(store, "MIGRATION", path)
    path.write_bytes(b"CREATE TABLE a;\n")
    expected = store.migration_digest()
    path.write_bytes(b"CREATE TABLE a;\r\n")
    assert store.migration_digest() == expected


def test_replay_failure_never_publishes_partial_output(tmp_path):
    def broken(_):
        yield record_at(0, b"PRIVATE\n")
        raise store.ArchiveError("archive_corrupt", "Bad digest")

    destination = tmp_path / "replay.jsonl"
    with pytest.raises(store.ArchiveError):
        trace.replay_file(SimpleNamespace(replay=broken), "snapshot", destination)
    assert not destination.exists() and not list(tmp_path.iterdir())


def test_replay_success_is_byte_exact_and_refuses_overwrite(tmp_path):
    original = b"PRIVATE\r\n"
    destination = tmp_path / "replay.jsonl"
    fake = SimpleNamespace(replay=lambda _: [record_at(0, original)])
    trace.replay_file(fake, "snapshot", destination)
    assert destination.read_bytes() == original
    with pytest.raises(store.ArchiveError, match="already exists"):
        trace.replay_file(fake, "snapshot", destination)
    assert destination.read_bytes() == original


def test_archive_cli_missing_env_returns_nonzero_and_safe_json(monkeypatch, capsys):
    monkeypatch.delenv("VESRYN_DATABASE", raising=False)
    assert trace.main(["init", "--json"]) == 2
    value = json.loads(capsys.readouterr().out)
    assert value["status"] == "missing_configuration" and not value.get("archive_ready")
