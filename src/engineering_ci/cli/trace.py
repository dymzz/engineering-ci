"""Trace operations; status performs only a read-only database preflight."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from ..tracing.config import TraceConfigurationError, load_config
from ..tracing.preflight import PreflightResult, preflight
from ..tracing.sources.codex_rollout import RolloutSource, SourceError, inspect_source


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser(
        "status", help="Inspect PostgreSQL access without migrations or archive writes"
    )
    status.add_argument(
        "--json",
        action="store_true",
        help="Print a safe machine-readable preflight result",
    )
    status.add_argument(
        "--config", help="Tracing TOML path; relative paths start at engineering-ci"
    )
    selector = status.add_mutually_exclusive_group()
    for name in ("source", "file", "snapshot", "result"):
        selector.add_argument(f"--{name}")
    inspect = commands.add_parser(
        "inspect", help="Inspect one native rollout file without archiving content"
    )
    inspect.add_argument("file")
    inspect.add_argument("--config")
    inspect.add_argument("--json", action="store_true")
    init = commands.add_parser(
        "init", help="Initialize only the dedicated PostgreSQL archive schema"
    )
    ingest = commands.add_parser(
        "import",
        help="Archive one explicit rollout file; resume from its committed cursor",
    )
    ingest.add_argument("file")
    replay = commands.add_parser(
        "replay", help="Write a fixed snapshot's original bytes; never reexecute tools"
    )
    replay.add_argument("snapshot")
    replay.add_argument(
        "--out", required=True, help="New output file; raw archive content is private"
    )
    project = commands.add_parser(
        "project", help="Derive content-free OTel spans from a fixed snapshot"
    )
    project.add_argument("snapshot")
    project.add_argument("--out", required=True)
    project.add_argument(
        "--record",
        action="store_true",
        help="Register the projection before exporting it",
    )
    queries = []
    for name in ("sources", "snapshots", "results"):
        command = commands.add_parser(
            name, help="List bounded archive metadata without reading source payloads"
        )
        command.add_argument("--limit", type=int, default=20)
        command.add_argument("--after", help="next_after from the previous page")
        queries.append(command)
    queries[1].add_argument("--source")
    queries[2].add_argument("--snapshot")
    queries[2].add_argument("--kind", choices=("projection", "analysis"))
    register = commands.add_parser(
        "register", help="Associate an existing result file without recomputing it"
    )
    register.add_argument("file")
    register.add_argument(
        "--input", help="Original projection JSON required for analysis registration"
    )
    export = commands.add_parser(
        "export", help="Export a checked registered result without recomputing it"
    )
    export.add_argument("--result", required=True)
    export.add_argument("--out", required=True)
    for command in (*queries, register, export):
        command.add_argument("--config")
        command.add_argument("--json", action="store_true")
    for command in (init, ingest, replay, project):
        command.add_argument("--config")
        command.add_argument("--json", action="store_true")
    options = parser.parse_args(argv)
    if options.command in {
        "init",
        "import",
        "replay",
        "project",
        "sources",
        "snapshots",
        "results",
        "register",
        "export",
    } or (
        options.command == "status"
        and any(
            getattr(options, name) for name in ("source", "file", "snapshot", "result")
        )
    ):
        return archive_command(options)
    if options.command == "inspect":
        try:
            config = load_config(options.config)
            facts = inspect_source(
                RolloutSource(
                    options.file,
                    max_record_bytes=config.max_record_bytes,
                    batch_size=config.batch_size,
                )
            )
            value = {
                "schema_version": 1,
                "operation": "trace.inspect",
                "status": "passed",
                "exit_code": 0,
                **facts,
            }
        except (SourceError, TraceConfigurationError) as error:
            value = {
                "schema_version": 1,
                "operation": "trace.inspect",
                "status": getattr(error, "status", "invalid_configuration"),
                "reason": str(error),
                "exit_code": 2,
            }
        print(
            json.dumps(value, sort_keys=True)
            if options.json
            else f"Trace source: {value['status']}; {json.dumps(value, sort_keys=True)}"
        )
        return value["exit_code"]
    try:
        result = preflight(load_config(options.config))
    except TraceConfigurationError:
        result = PreflightResult(
            "invalid_configuration", "Tracing configuration is invalid"
        )
    if options.json:
        print(json.dumps(result.to_dict(), sort_keys=True))
    else:
        print(f"Trace preflight: {result.status}; {result.reason}")
        if result.facts:
            print(
                f"Database: {result.facts.database}; server: {result.facts.server_version_num}; schema: {result.schema}"
            )
        print(
            "Archive initialization and ownership have not been verified by this read-only command."
        )
    return result.exit_code


def archive_command(options):
    """Keep archive writes explicit and publish replay files only after verification."""
    from ..tracing.store import ArchiveError, import_source, open_store

    operation = f"trace.{options.command}"
    registered = None
    attempted = None
    try:
        config = load_config(options.config)
        source = (
            RolloutSource(
                options.file,
                max_record_bytes=config.max_record_bytes,
                batch_size=config.batch_size,
            )
            if options.command == "import"
            else None
        )
        with open_store(config) as store:
            from ..tracing.catalog import TraceCatalog

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
            elif options.command == "project":
                from ..tracing.projection import standardize
                from ..tracing.results import prepare_result

                projection = standardize(
                    store.replay(options.snapshot), options.snapshot
                )
                if options.record:
                    snapshot = catalog.snapshot(options.snapshot)
                    record = prepare_result(projection, snapshot["capture_digest"])
                    attempted = record.result_id
                    registered = catalog.save(record)
                write_new_file(
                    Path(options.out),
                    [json.dumps(projection, sort_keys=True).encode() + b"\n"],
                )
                data = {
                    "snapshot_id": options.snapshot,
                    "projection_id": projection["projection_id"],
                    "spans": len(projection["spans"]),
                    "completeness": projection["completeness"],
                    "output": str(Path(options.out).resolve()),
                    "association": "registered" if options.record else "file_only",
                }
            elif options.command == "status":
                if options.result:
                    data = catalog.lineage(options.result)
                elif options.snapshot:
                    snapshot = catalog.snapshot(options.snapshot)
                    data = {
                        "snapshot": snapshot,
                        "source": catalog.source(snapshot["source"]),
                    }
                else:
                    identifier = options.source
                    if options.file:
                        identifier = RolloutSource(
                            options.file, max_record_bytes=config.max_record_bytes
                        ).identity.source
                    data = {
                        "source": catalog.source(identifier),
                        "snapshots": catalog.snapshots(source=identifier),
                    }
                    if options.file:
                        data["next_actions"] = [
                            [
                                "engineering-ci",
                                "trace",
                                "import",
                                str(Path(options.file).resolve()),
                                "--config",
                                str(config_path(options)),
                                "--json",
                            ]
                        ]
            elif options.command == "sources":
                data = catalog.sources(limit=options.limit, after=options.after)
            elif options.command == "snapshots":
                data = catalog.snapshots(
                    source=options.source, limit=options.limit, after=options.after
                )
            elif options.command == "results":
                data = catalog.results(
                    snapshot=options.snapshot,
                    kind=options.kind,
                    limit=options.limit,
                    after=options.after,
                )
            elif options.command == "register":
                from ..tracing.results import digest, prepare_result

                _, document = load_document(options.file)
                projection_input = None
                if "analysis_id" in document:
                    if not options.input:
                        raise ArchiveError(
                            "invalid_input",
                            "Analysis registration requires its original --input projection file",
                        )
                    raw, projection_input = load_document(options.input)
                    input_digest = digest(raw)
                else:
                    if options.input:
                        raise ArchiveError(
                            "invalid_input",
                            "Projection registration uses the archived snapshot, not --input",
                        )
                    input_digest = catalog.snapshot(document.get("snapshot_id"))[
                        "capture_digest"
                    ]
                record = prepare_result(document, input_digest, origin="file")
                attempted = record.result_id
                data = catalog.save(record, projection_input=projection_input)
                attempted = None
            elif options.command == "export":
                write_new_file(Path(options.out), [catalog.document(options.result)])
                data = {
                    "result_id": options.result,
                    "output": str(Path(options.out).resolve()),
                }
            else:
                data = replay_file(store, options.snapshot, Path(options.out))
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
            "reason": "Optional dependency or output file is unavailable",
            "exit_code": 2,
        }
    if attempted is not None and registered is None:
        value["registration"] = {"result_id": attempted, "association": "unconfirmed"}
        value["next_actions"] = [
            [
                "engineering-ci",
                "trace",
                "status",
                "--result",
                attempted,
                "--config",
                str(config_path(options)),
                "--json",
            ]
        ]
    if registered is not None:
        value["registered_result"] = registered
        value["next_actions"] = [
            [
                "engineering-ci",
                "trace",
                "export",
                "--result",
                registered["result_id"],
                "--out",
                str(Path(options.out).resolve()),
                "--config",
                str(config_path(options)),
                "--json",
            ]
        ]
    print(
        json.dumps(value, sort_keys=True)
        if options.json
        else f"{operation}: {json.dumps(value, sort_keys=True)}"
    )
    return value["exit_code"]


def config_path(options):
    """Retain the actual configuration in recovery commands."""
    from .._paths import ROOT

    path = Path(options.config) if options.config else Path("config/tracing.toml")
    return path if path.is_absolute() else ROOT / path


def load_document(path):
    """Load derived JSON safely without quoting its contents in diagnostics."""
    from ..tracing.store import ArchiveError

    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise TypeError
        return raw, value
    except (OSError, ValueError, TypeError, RecursionError):
        raise ArchiveError(
            "invalid_input", "Cannot read the requested derived JSON document"
        ) from None


def replay_file(store, snapshot, destination):
    """Use a private temporary file and refuse to overwrite any existing output."""
    write_new_file(destination, (record.raw for record in store.replay(snapshot)))
    return {"snapshot_id": snapshot, "output": str(destination.resolve())}


def write_new_file(destination, chunks):
    """Publish a fully verified stream atomically without replacing existing data."""
    if destination.exists():
        from ..tracing.store import ArchiveError

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
        # Hard-link creation is atomic and cannot overwrite a path created meanwhile.
        os.link(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
