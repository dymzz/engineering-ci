"""Configuration, safe diagnostics and the read-only PostgreSQL boundary."""

import json
from contextlib import nullcontext
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from engineering_ci.cli import main, trace
from engineering_ci.tracing.config import (
    TraceConfig,
    TraceConfigurationError,
    load_config,
)
from engineering_ci.tracing.preflight import preflight

SECRET = "postgresql://private:SECRET_PASSWORD@private-host/private-db"
FACTS = ("archive-db", 170006, "on", "on", False, True, None, None)


class ProbeError(Exception):
    def __init__(self, text=SECRET, state=None):
        super().__init__(text)
        self.sqlstate = state


class DriverFake:
    Error = ProbeError
    ProgrammingError = ProbeError

    def __init__(
        self, row=FACTS, *, parse_error=None, connect_error=None, query_error=None
    ):
        self.row = row
        self.parse_error = parse_error
        self.connect_error = connect_error
        self.query_error = query_error
        self.calls = []
        self.queries = []
        self.closed = False
        self.conninfo = SimpleNamespace(conninfo_to_dict=self.parse)

    def parse(self, value):
        if self.parse_error:
            raise self.parse_error
        return {"dbname": "archive-db"}

    def connect(self, value, **kwargs):
        self.calls.append(kwargs)
        if self.connect_error:
            raise self.connect_error
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def transaction(self):
        return nullcontext()

    def cursor(self):
        return nullcontext(self)

    def execute(self, query, parameters=None):
        self.queries.append((query, parameters))
        if self.query_error:
            raise self.query_error

    def fetchone(self):
        return self.row


def test_config_is_anchored_to_project_and_contains_no_credentials(
    tmp_path, monkeypatch
):
    config = tmp_path / "config"
    config.mkdir()
    (config / "tracing.toml").write_text(
        'schema_version = 1\n[archive]\nschema = "engineering_ci_trace_test"\nconnect_timeout = 6\n'
    )
    elsewhere = tmp_path / "consumer"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    result = load_config(root=tmp_path)
    assert result.schema == "engineering_ci_trace_test" and result.connect_timeout == 6
    assert "VESRYN_DATABASE" not in json.dumps(asdict(result))


@pytest.mark.parametrize(
    "schema",
    [
        "public",
        "information_schema",
        "pg_catalog",
        "pg_trace",
        "a" * 64,
        "trace; DROP SCHEMA other",
        "UPPER",
        "",
    ],
)
def test_dedicated_schema_validation(schema):
    with pytest.raises(TraceConfigurationError) as error:
        TraceConfig(schema=schema)
    assert schema not in str(error.value) if schema else True


@pytest.mark.parametrize(
    "name",
    [
        "connect_timeout",
        "statement_timeout",
        "lock_timeout",
        "batch_size",
        "max_record_bytes",
    ],
)
@pytest.mark.parametrize("value", [0, -1, True, "SECRET_TIMEOUT", 1.5])
def test_timeout_types_are_explicit(name, value):
    with pytest.raises(TraceConfigurationError) as error:
        TraceConfig(**{name: value})
    assert "SECRET_TIMEOUT" not in str(error.value)


@pytest.mark.parametrize(
    "contents",
    [
        "schema_version = 2\n[archive]\n",
        "schema_version = true\n[archive]\n",
        'schema_version = 1\n[archive]\ndsn = "SECRET_PASSWORD"\n',
        'schema_version = 1\n[archive]\n[unknown]\nvalue = "SECRET_PASSWORD"\n',
        'schema_version = 1\narchive = "SECRET_PASSWORD"\n',
        'schema_version = 1\n[archive]\ncommand = "SECRET_PASSWORD"\n',
        'schema_version = "SECRET_BROKEN',
    ],
)
def test_unknown_or_invalid_toml_cannot_expose_values(tmp_path, contents):
    path = tmp_path / "trace.toml"
    path.write_text(contents)
    with pytest.raises(TraceConfigurationError) as error:
        load_config(path)
    assert "SECRET" not in str(error.value)


def test_missing_config_has_a_safe_error(tmp_path):
    with pytest.raises(TraceConfigurationError, match="Cannot read"):
        load_config(tmp_path / "missing.toml")


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_env_never_loads_the_optional_driver(monkeypatch, value):
    import engineering_ci.tracing.preflight as module

    monkeypatch.setattr(
        module.importlib,
        "import_module",
        lambda _: pytest.fail("Driver must remain optional"),
    )
    result = preflight(TraceConfig(), environ={"VESRYN_DATABASE": value})
    assert result.status == "missing_configuration" and result.exit_code == 2
    assert not result.to_dict()["archive_ready"]


def test_driver_missing_is_not_a_network_error(monkeypatch):
    import engineering_ci.tracing.preflight as module

    def missing(_):
        raise ImportError(SECRET)

    monkeypatch.setattr(module.importlib, "import_module", missing)
    result = preflight(TraceConfig(), environ={"VESRYN_DATABASE": SECRET})
    assert result.status == "missing_tool"
    assert "SECRET" not in json.dumps(result.to_dict())


def test_invalid_connection_string_is_rejected_before_connecting():
    driver = DriverFake(parse_error=ProbeError())
    result = preflight(
        TraceConfig(), environ={"VESRYN_DATABASE": SECRET}, driver=driver
    )
    assert result.status == "invalid_configuration" and not driver.calls
    assert "SECRET" not in json.dumps(result.to_dict())


def test_probe_is_read_only_bounded_and_does_not_claim_archive_initialization():
    driver = DriverFake()
    settings = TraceConfig(connect_timeout=4, statement_timeout=8, lock_timeout=2)
    result = preflight(settings, environ={"VESRYN_DATABASE": SECRET}, driver=driver)
    assert result.status == "passed" and result.exit_code == 0 and driver.closed
    assert driver.calls == [
        {
            "connect_timeout": 4,
            "application_name": "engineering-ci-trace-preflight",
            "autocommit": True,
        }
    ]
    assert "READ ONLY" in driver.queries[0][0]
    assert all(
        query.startswith(("SELECT", "SET TRANSACTION")) for query, _ in driver.queries
    )
    assert driver.queries[1][1] == ("8000ms", "2000ms")
    assert driver.queries[-1][1] == (settings.schema,) * 5
    view = result.to_dict()
    assert view["ready_for_migration"] and not view["archive_ready"]
    assert "SECRET" not in json.dumps(view) and "private-host" not in repr(result)


@pytest.mark.parametrize(
    "state,detail,expected",
    [
        ("28P01", SECRET, "authentication_failed"),
        ("28000", SECRET, "authentication_failed"),
        (None, "password authentication failed " + SECRET, "authentication_failed"),
        ("42501", SECRET, "permission_denied"),
        (None, "no pg_hba.conf entry " + SECRET, "permission_denied"),
        ("08006", SECRET, "network_error"),
        (None, "connection refused " + SECRET, "network_error"),
        (None, "certificate verify failed " + SECRET, "network_error"),
        ("57014", SECRET, "timeout"),
        (None, "timeout expired " + SECRET, "timeout"),
        ("SECRET_STATE", SECRET, "database_error"),
    ],
)
@pytest.mark.parametrize("stage", ["connect", "query"])
def test_failure_classification_never_serializes_driver_diagnostics(
    state, detail, expected, stage
):
    driver = DriverFake(**{f"{stage}_error": ProbeError(detail, state)})
    result = preflight(
        TraceConfig(), environ={"VESRYN_DATABASE": SECRET}, driver=driver
    )
    assert result.status == expected and result.exit_code == 2
    view = json.dumps(result.to_dict())
    assert "SECRET" not in view and "private-host" not in view
    assert result.sqlstate == (state if state and len(state) == 5 else None)
    if stage == "query":
        assert driver.closed


@pytest.mark.parametrize("row", [None, FACTS[:3]])
def test_missing_capability_result_is_not_passed(row):
    result = preflight(
        TraceConfig(), environ={"VESRYN_DATABASE": SECRET}, driver=DriverFake(row)
    )
    assert result.status == "database_error"


@pytest.mark.parametrize(
    "exists,create,usage", [(False, False, None), (True, False, False)]
)
def test_actual_schema_permission_failure_is_explicit(exists, create, usage):
    row = (*FACTS[:4], exists, create, usage, False if exists else None)
    result = preflight(
        TraceConfig(), environ={"VESRYN_DATABASE": SECRET}, driver=DriverFake(row)
    )
    assert result.status == "permission_denied" and result.facts
    assert not result.to_dict()["ready_for_migration"]


def test_existing_schema_is_not_assumed_owned_or_initialized():
    row = (*FACTS[:4], True, True, True, True)
    result = preflight(
        TraceConfig(), environ={"VESRYN_DATABASE": SECRET}, driver=DriverFake(row)
    )
    assert result.status == "passed"
    assert (
        not result.to_dict()["ready_for_migration"]
        and not result.to_dict()["archive_ready"]
    )


def test_cli_returns_the_actual_preflight_exit_status_and_valid_json(
    monkeypatch, capsys
):
    result = preflight(TraceConfig(), environ={})
    monkeypatch.setattr(trace, "preflight", lambda _: result)
    assert main(["trace", "status", "--json"]) == 2
    value = json.loads(capsys.readouterr().out)
    assert value["status"] == "missing_configuration" and value["exit_code"] == 2


def test_invalid_cli_config_never_opens_a_connection(tmp_path, monkeypatch, capsys):
    path = tmp_path / "bad.toml"
    path.write_text('schema_version = 1\n[archive]\ndsn = "SECRET"\n')
    monkeypatch.setattr(
        trace, "preflight", lambda _: pytest.fail("No connection may run")
    )
    assert trace.main(["status", "--config", str(path), "--json"]) == 2
    value = json.loads(capsys.readouterr().out)
    assert value["status"] == "invalid_configuration" and "SECRET" not in json.dumps(
        value
    )
