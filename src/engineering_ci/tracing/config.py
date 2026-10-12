"""Non-secret archive settings anchored to the engineering-ci checkout."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .._paths import ROOT

DATABASE_ENV = "VESRYN_DATABASE"


class TraceConfigurationError(ValueError):
    """Reject configuration without including its raw values."""


@dataclass(frozen=True)
class TraceConfig:
    schema: str = "engineering_ci_trace"
    connect_timeout: int = 10
    statement_timeout: int = 15
    lock_timeout: int = 5
    batch_size: int = 100
    max_record_bytes: int = 4 * 1024 * 1024

    def __post_init__(self):
        if (
            not isinstance(self.schema, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", self.schema)
            or self.schema.startswith("pg_")
            or self.schema in {"public", "information_schema"}
        ):
            raise TraceConfigurationError(
                "Archive schema must be a dedicated SQL identifier"
            )
        for name in (
            "connect_timeout",
            "statement_timeout",
            "lock_timeout",
            "batch_size",
            "max_record_bytes",
        ):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise TraceConfigurationError(
                    f"Archive {name} must be a positive integer"
                )


def load_config(path=None, *, root=None) -> TraceConfig:
    """Load only implemented, non-secret settings before any database operation."""
    base = ROOT if root is None else Path(root)
    location = Path(path) if path is not None else Path("config/tracing.toml")
    if not location.is_absolute():
        location = base / location
    try:
        with location.open("rb") as source:
            data = tomllib.load(source)
    except (OSError, ValueError):
        raise TraceConfigurationError("Cannot read a valid tracing TOML file") from None
    if (
        set(data) != {"schema_version", "archive"}
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
        or not isinstance(data["archive"], dict)
        or set(data["archive"]) - set(TraceConfig.__dataclass_fields__)
    ):
        raise TraceConfigurationError(
            "Unsupported tracing configuration fields or version"
        )
    return TraceConfig(**data["archive"])
