"""Read PostgreSQL capabilities without initializing or mutating the archive."""

from __future__ import annotations

import importlib
import os
import re
from dataclasses import asdict, dataclass

from .config import DATABASE_ENV, TraceConfig, TraceConfigurationError, load_config


@dataclass(frozen=True)
class DatabaseFacts:
    database: str
    server_version_num: int
    fsync: str
    synchronous_commit: str
    schema_exists: bool
    can_create_schema: bool
    schema_usage: bool | None
    schema_create: bool | None


@dataclass(frozen=True)
class PreflightResult:
    status: str
    reason: str
    schema: str | None = None
    facts: DatabaseFacts | None = None
    sqlstate: str | None = None

    @property
    def exit_code(self):
        return 0 if self.status == "passed" else 2

    def to_dict(self):
        """Expose facts, never connection parameters or raw driver exceptions."""
        return {
            "schema_version": 1,
            "operation": "trace.status",
            **asdict(self),
            "exit_code": self.exit_code,
            "archive_ready": False,
            "ready_for_migration": bool(
                self.status == "passed"
                and self.facts
                and not self.facts.schema_exists
                and self.facts.can_create_schema
            ),
        }


def failure(exc, schema):
    """Classify failures while keeping libpq diagnostics private."""
    raw_state = getattr(exc, "sqlstate", None)
    state = (
        raw_state
        if isinstance(raw_state, str) and re.fullmatch(r"[A-Z0-9]{5}", raw_state)
        else None
    )
    detail = str(exc).lower()[:8192]
    if (
        state
        and state.startswith("28")
        or any(
            text in detail
            for text in (
                "password authentication failed",
                "no password supplied",
                "sasl authentication failed",
            )
        )
    ):
        status, reason = "authentication_failed", "PostgreSQL authentication failed"
    elif state == "42501" or "no pg_hba.conf entry" in detail:
        status, reason = "permission_denied", "PostgreSQL access was denied"
    elif (
        state == "57014"
        or isinstance(exc, TimeoutError)
        or any(text in detail for text in ("timeout expired", "timed out"))
    ):
        status, reason = "timeout", "PostgreSQL preflight timed out"
    elif (
        state
        and state.startswith("08")
        or isinstance(exc, (ConnectionError, OSError))
        or any(
            text in detail
            for text in (
                "connection refused",
                "could not translate host name",
                "could not resolve",
                "certificate verify failed",
                "server closed the connection",
            )
        )
    ):
        status, reason = "network_error", "PostgreSQL connection failed"
    else:
        status, reason = "database_error", "PostgreSQL preflight failed"
    return PreflightResult(status, reason, schema, sqlstate=state)


def preflight(config=None, *, environ=None, driver=None) -> PreflightResult:
    """Perform a bounded read-only probe; driver injection is only a test seam."""
    try:
        settings = load_config() if config is None else config
        if not isinstance(settings, TraceConfig):
            raise TraceConfigurationError("Invalid tracing configuration object")
    except TraceConfigurationError:
        return PreflightResult(
            "invalid_configuration", "Tracing configuration is invalid"
        )
    env = os.environ if environ is None else environ
    conninfo = env.get(DATABASE_ENV)
    if not isinstance(conninfo, str) or not conninfo.strip():
        return PreflightResult(
            "missing_configuration",
            f"{DATABASE_ENV} is not configured",
            settings.schema,
        )
    if driver is None:
        try:
            driver = importlib.import_module("psycopg")
        except (ImportError, OSError):
            return PreflightResult(
                "missing_tool",
                "Install the trace extra to enable Psycopg 3",
                settings.schema,
            )
    try:
        driver.conninfo.conninfo_to_dict(conninfo)
    except driver.ProgrammingError:
        return PreflightResult(
            "invalid_configuration",
            f"{DATABASE_ENV} is not valid PostgreSQL conninfo",
            settings.schema,
        )
    try:
        with (
            driver.connect(
                conninfo,
                connect_timeout=settings.connect_timeout,
                application_name="engineering-ci-trace-preflight",
                autocommit=True,
            ) as connection,
            connection.transaction(),
            connection.cursor() as cursor,
        ):
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            cursor.execute(
                "SELECT set_config('statement_timeout', %s, true), set_config('lock_timeout', %s, true)",
                (
                    f"{settings.statement_timeout * 1000}ms",
                    f"{settings.lock_timeout * 1000}ms",
                ),
            )
            cursor.execute(
                "SELECT current_database(), current_setting('server_version_num')::integer, "
                "current_setting('fsync'), current_setting('synchronous_commit'), "
                "to_regnamespace(%s) IS NOT NULL, has_database_privilege(current_database(), 'CREATE'), "
                "CASE WHEN to_regnamespace(%s) IS NULL THEN NULL ELSE has_schema_privilege(%s, 'USAGE') END, "
                "CASE WHEN to_regnamespace(%s) IS NULL THEN NULL ELSE has_schema_privilege(%s, 'CREATE') END",
                (settings.schema,) * 5,
            )
            row = cursor.fetchone()
            if row is None or len(row) != 8:
                return PreflightResult(
                    "database_error",
                    "PostgreSQL capability result is unavailable",
                    settings.schema,
                )
            facts = DatabaseFacts(*row)
    except (driver.Error, OSError) as exc:
        return failure(exc, settings.schema)
    if (
        not facts.schema_exists
        and not facts.can_create_schema
        or facts.schema_exists
        and not facts.schema_usage
    ):
        return PreflightResult(
            "permission_denied",
            "Archive schema access requires an explicit permission decision",
            settings.schema,
            facts,
        )
    return PreflightResult(
        "passed",
        "Read-only database probe passed; archive ownership and initialization are separate",
        settings.schema,
        facts,
    )
