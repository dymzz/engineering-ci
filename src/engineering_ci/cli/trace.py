"""Keep legacy trace syntax while routing raw archive operations to vesryn-log."""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from .._paths import ROOT

ARCHIVE_COMMANDS = {
    "status",
    "inspect",
    "init",
    "import",
    "replay",
    "sources",
    "snapshots",
}


def needs_harness(args):
    """Projection, mining interpretation and derived queries still belong to Vesryn."""
    return bool(args and args[0] not in ARCHIVE_COMMANDS | {"-h", "--help"})


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if needs_harness(args):
        from vesryn.cli.trace import main as analysis_main

        if not any(item == "--config" or item.startswith("--config=") for item in args):
            args += ["--config", str(ROOT / "config/tracing.toml")]
        return analysis_main(args)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser(
        "status", help="Read-only archive preflight or selected metadata"
    )
    selector = status.add_mutually_exclusive_group()
    for name in ("file", "source", "snapshot", "result"):
        selector.add_argument("--" + name)
    inspect = commands.add_parser(
        "inspect", help="Bounded native source metadata; no database"
    )
    inspect.add_argument("file")
    init = commands.add_parser("init", help="Explicit archive initialization/migration")
    ingest = commands.add_parser(
        "import", help="Import one explicitly selected native source"
    )
    ingest.add_argument("file")
    replay = commands.add_parser(
        "replay", help="Export verified original bytes; never execute tools"
    )
    replay.add_argument("snapshot")
    replay.add_argument("--out", required=True)
    sources = commands.add_parser("sources")
    snapshots = commands.add_parser("snapshots")
    snapshots.add_argument("--source")
    for command in (sources, snapshots):
        command.add_argument("--limit", type=int, default=20)
        command.add_argument("--after")
    for command in (status, inspect, init, ingest, replay, sources, snapshots):
        command.add_argument("--config")
        command.add_argument("--json", action="store_true")
    parser.epilog = "Vesryn analysis commands remain available: project, results, register, export, query, send."
    options = parser.parse_args(args)
    try:
        return archive_command(options)
    except ModuleNotFoundError as error:
        if not (
            error.name == "vesryn_log" or str(error.name).startswith("vesryn_log.")
        ):
            raise
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "operation": "trace." + options.command,
                    "status": "missing_tool",
                    "exit_code": 2,
                    "reason": "Install the logging API with uv sync --locked --extra trace",
                }
            )
        )
        return 2


def archive_command(options):
    """Call installed archive APIs directly; preserve original JSON/exit conventions."""
    from vesryn_log.archive.config import TraceConfigurationError
    from vesryn_log.sources.codex_rollout import (
        RolloutSource,
        SourceError,
        inspect_source,
    )

    from ..tracing.catalog import TraceCatalog
    from ..tracing.config import load_config
    from ..tracing.preflight import preflight
    from ..tracing.store import ArchiveError, import_source, open_store

    operation = "trace." + options.command
    try:
        path = options.config
        if path is not None and not Path(path).is_absolute():
            path = ROOT / path
        config = load_config(path)
        source = None
        if options.command in {"inspect", "import"}:
            source = RolloutSource(
                options.file,
                batch_size=config.batch_size,
                max_record_bytes=config.max_record_bytes,
            )
        if options.command == "inspect":
            data = inspect_source(source)
        elif options.command == "status" and not any(
            getattr(options, name) for name in ("file", "source", "snapshot", "result")
        ):
            value = preflight(config).to_dict()
            print(
                json.dumps(value, sort_keys=True)
                if options.json
                else f"Trace preflight: {value['status']}; {value['reason']}"
            )
            return value["exit_code"]
        else:
            with open_store(config) as store:
                catalog = TraceCatalog(store)
                if options.command == "init":
                    data = {
                        "created": store.initialize(),
                        "schema": config.schema,
                        "archive_ready": True,
                        "archive_schema_version": store.schema_version(),
                    }
                elif options.command == "import":
                    data = import_source(store, source)
                elif options.command == "sources":
                    data = catalog.sources(limit=options.limit, after=options.after)
                elif options.command == "snapshots":
                    data = catalog.snapshots(
                        source=options.source, limit=options.limit, after=options.after
                    )
                elif options.command == "replay":
                    replay_file(store, options.snapshot, Path(options.out))
                    data = {
                        "snapshot_id": options.snapshot,
                        "output": str(Path(options.out).resolve()),
                    }
                elif options.result:
                    data = catalog.lineage(options.result)
                elif options.snapshot:
                    snapshot = catalog.snapshot(options.snapshot)
                    data = {
                        "snapshot": snapshot,
                        "source": catalog.source(snapshot["source"]),
                    }
                else:
                    identity = (
                        options.source
                        or RolloutSource(
                            options.file, max_record_bytes=config.max_record_bytes
                        ).identity.source
                    )
                    data = {
                        "source": catalog.source(identity),
                        "snapshots": catalog.snapshots(source=identity),
                    }
        value = {
            "schema_version": 1,
            "operation": operation,
            "status": "passed",
            "exit_code": 0,
            **data,
        }
    except (TraceConfigurationError, SourceError, ArchiveError) as error:
        value = {
            "schema_version": 1,
            "operation": operation,
            "status": getattr(error, "status", "invalid_configuration"),
            "reason": str(error),
            "sqlstate": getattr(error, "sqlstate", None),
            "exit_code": 2,
        }
    except (ImportError, OSError):
        value = {
            "schema_version": 1,
            "operation": operation,
            "status": "environment_error",
            "reason": "Archive dependency or selected file is unavailable",
            "exit_code": 2,
        }
    print(
        json.dumps(value, sort_keys=True)
        if options.json
        else f"{operation}: {json.dumps(value, sort_keys=True)}"
    )
    return value["exit_code"]


def replay_file(store, snapshot, destination):
    """Publish original bytes only after full replay verification, without overwriting."""
    write_new_file(destination, (record.raw for record in store.replay(snapshot)))
    return {"snapshot_id": snapshot, "output": str(destination.resolve())}


def write_new_file(destination, chunks):
    """Retain the host's atomic export helper used by mining and archive commands."""
    from ..tracing.store import ArchiveError

    if destination.exists():
        raise ArchiveError(
            "output_exists", "Replay output already exists; choose a new file"
        )
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=".trace-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            for chunk in chunks:
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
