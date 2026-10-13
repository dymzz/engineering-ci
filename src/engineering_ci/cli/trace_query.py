"""Decision-sized archive queries for humans and agents, without a context service."""

import sys
from contextlib import ExitStack

from ..tracing.config import TraceConfigurationError, load_config
from ..tracing.results import result_id
from ..tracing.retrieval import (
    LEVELS,
    Query,
    analysis_query,
    encode,
    raw_query,
    summary_query,
)
from ..tracing.store import ArchiveError
from .mine_inputs import file_evidence, registered_evidence


def add_command(commands):
    """Attach the bounded query surface to the existing trace CLI."""
    command = commands.add_parser(
        "query", help="Read bounded summaries, patterns and specific evidence"
    )
    command.add_argument("level", choices=LEVELS)
    source = command.add_mutually_exclusive_group()
    source.add_argument(
        "--analysis", help="Existing analysis JSON; requires --projection"
    )
    source.add_argument(
        "--analysis-result", help="Registered analysis ID; requires trace extra"
    )
    command.add_argument(
        "--projection", help="Actual original projection JSON used by --analysis"
    )
    command.add_argument("--snapshot", help="Raw snapshot or unscoped summary filter")
    command.add_argument(
        "--candidate", help="Candidate ID from the pattern/summary query"
    )
    command.add_argument("--kind")
    command.add_argument("--activity")
    command.add_argument("--outcome")
    command.add_argument("--min-cases", type=int, default=1)
    command.add_argument("--limit", type=int, default=20)
    command.add_argument("--max-bytes", type=int, default=16384)
    command.add_argument(
        "--after", help="Opaque next_after bookmark from the same query"
    )
    command.add_argument(
        "--allow-raw",
        action="store_true",
        help="Explicit acknowledgement to return private original bytes",
    )
    command.add_argument("--config")
    command.add_argument(
        "--json",
        action="store_true",
        help="Queries always return compact, budgeted JSON",
    )


def run(options):
    """Validate before I/O and return safe errors without raw driver messages."""
    query = Query(
        options.level,
        options.limit,
        options.max_bytes,
        options.after,
        options.candidate,
        options.kind,
        options.activity,
        options.outcome,
        options.min_cases,
        options.allow_raw,
    )
    try:
        query.validate()
        if options.analysis_result:
            result_id(options.analysis_result)
        if options.snapshot:
            result_id(options.snapshot)
        if (
            bool(options.analysis) != bool(options.projection)
            or options.analysis_result
            and options.projection
        ):
            raise ArchiveError(
                "invalid_query",
                "File analysis requires its --projection; registered analysis uses its stored parent",
            )
        has_analysis = bool(options.analysis or options.analysis_result)
        if (
            options.level in {"pattern", "candidate", "evidence"}
            and not has_analysis
            or has_analysis
            and options.snapshot
            or options.level == "raw"
            and (has_analysis or not options.snapshot)
        ):
            raise ArchiveError(
                "invalid_query",
                "Choose an analysis for derived views, or one snapshot for raw",
            )
        with ExitStack() as stack:
            if options.analysis:
                value = analysis_query(
                    file_evidence(options.analysis, options.projection), query
                )
            else:
                from ..tracing.catalog import TraceCatalog

                # Reject a malformed token even before opening PostgreSQL.
                from ..tracing.retrieval import cursor
                from ..tracing.store import open_store

                if options.level == "summary" and not has_analysis:
                    if (
                        query.kind
                        or query.activity
                        or query.outcome
                        or query.min_cases != 1
                    ):
                        raise ArchiveError(
                            "invalid_query",
                            "Unscoped summary accepts only --snapshot, pagination and budget",
                        )
                    cursor(
                        query,
                        query.identity(
                            {
                                "kind": "registered_analysis_metadata",
                                "snapshot_id": options.snapshot,
                            }
                        ),
                    )
                elif options.level == "raw":
                    cursor(query, query.identity({"snapshot_id": options.snapshot}))
                catalog = TraceCatalog(
                    stack.enter_context(open_store(load_config(options.config)))
                )
                if options.analysis_result:
                    value = analysis_query(
                        registered_evidence(catalog, options.analysis_result), query
                    )
                elif options.level == "raw":
                    value = raw_query(catalog, query, options.snapshot)
                else:
                    value = summary_query(catalog, query, snapshot=options.snapshot)
    except ArchiveError as error:
        value = {
            "schema_version": 1,
            "operation": "trace.query",
            "level": query.level,
            "status": error.status,
            "exit_code": 3 if error.status == "unavailable" else 2,
            "reason": str(error),
        }
    except TraceConfigurationError:
        value = {
            "schema_version": 1,
            "operation": "trace.query",
            "level": query.level,
            "status": "invalid_configuration",
            "exit_code": 2,
            "reason": "Tracing configuration is invalid",
        }
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        value = {
            "schema_version": 1,
            "operation": "trace.query",
            "level": query.level,
            "status": "invalid_evidence",
            "exit_code": 2,
            "reason": "Cannot read the requested evidence; no private content returned",
        }
    sys.stdout.buffer.write(encode(value))
    sys.stdout.buffer.flush()
    return value["exit_code"]
