"""Explicit telemetry delivery without dispatching workflows or executing archived tools."""

import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from ..tracing.config import TraceConfigurationError, load_config
from ..tracing.results import digest
from ..tracing.store import ArchiveError
from ..tracing.telemetry import deliver
from .trace import config_path, load_document, write_new_file


def main(options):
    """Always expose actual receiver facts, even when the receipt file cannot be saved."""
    value = {
        "schema_version": 1,
        "operation": "trace.send",
        "status": "invalid_input",
        "exit_code": 2,
    }
    try:
        if Path(options.out).exists():
            raise ArchiveError(
                "output_exists",
                "Receipt output already exists; choose a new path before sending",
            )
        config = load_config(options.config)
        overrides = {
            key: getattr(options, key)
            for key in ("endpoint", "timeout", "batch_size")
            if getattr(options, key) is not None
        }
        settings = replace(config.telemetry, **overrides)
        if options.projection_result:
            from ..tracing.catalog import TraceCatalog
            from ..tracing.store import open_store

            with open_store(config) as store:
                catalog = TraceCatalog(store)
                if catalog.result(options.projection_result)["kind"] != "projection":
                    raise ArchiveError(
                        "invalid_input",
                        "--projection-result must identify a projection",
                    )
                raw = catalog.document(options.projection_result)
                projection = json.loads(raw)
        else:
            raw, projection = load_document(options.file)
        receipt = deliver(projection, digest(raw), settings)
        value.update(
            status=receipt["status"],
            exit_code=0 if receipt["status"] == "accepted" else 3,
            receipt=receipt,
        )
        args = ["engineering-ci", "trace", "send"]
        args += (
            ["--projection-result", options.projection_result]
            if options.projection_result
            else [str(Path(options.file).resolve())]
        )
        output = Path(options.out).resolve()
        retry = output.with_name(
            f"{output.stem}.retry-{uuid4().hex[:8]}{output.suffix}"
        )
        value["next_actions"] = (
            []
            if receipt["status"] == "accepted"
            else [
                [
                    *args,
                    "--endpoint",
                    settings.endpoint,
                    "--timeout",
                    str(settings.timeout),
                    "--batch-size",
                    str(settings.batch_size),
                    "--out",
                    str(retry),
                    "--config",
                    str(config_path(options)),
                    "--json",
                ]
            ]
        )
        # A failed network operation still publishes its actual, safe receipt.
        write_new_file(
            Path(options.out), [json.dumps(value, sort_keys=True).encode() + b"\n"]
        )
        value["output"] = str(Path(options.out).resolve())
    except ArchiveError as error:
        value.update(status=error.status, reason=str(error), exit_code=2)
    except TraceConfigurationError:
        value.update(
            status="invalid_configuration",
            reason="Tracing configuration is invalid",
            exit_code=2,
        )
    except ImportError:
        value.update(
            status="missing_tool",
            reason="Install the telemetry extra to enable the official OTLP exporter",
            exit_code=2,
        )
    except (OSError, ValueError, TypeError):
        value.update(
            status="environment_error",
            reason="Cannot read or save the requested telemetry result",
            exit_code=2,
        )
    if "receipt" in value and "output" not in value:
        value["receipt_saved"] = False
    print(
        json.dumps(value, sort_keys=True)
        if options.json
        else f"Telemetry: {json.dumps(value, sort_keys=True)}"
    )
    return value["exit_code"]
