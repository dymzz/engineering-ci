"""Explicitly bind a verified repeat candidate to the existing complete PR profile."""

import argparse
import json
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

from ..mining.bindings import bind
from ..tracing.config import TraceConfigurationError, load_config
from ..tracing.results import canonical, result_id
from ..tracing.store import ArchiveError
from .mine_inputs import file_evidence, registered_evidence
from .trace import write_new_file


def main(argv=None):
    """Create a reviewable binding; no check, database write or remote action is run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis", nargs="?")
    parser.add_argument("--analysis-result")
    parser.add_argument(
        "--projection", help="Original projection bytes for file analysis"
    )
    parser.add_argument(
        "--candidate", required=True, help="Selected candidate SHA256 ID"
    )
    parser.add_argument(
        "--profile",
        required=True,
        choices=("pr",),
        help="Explicit existing Handler target",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="New binding JSON; existing files are not overwritten",
    )
    parser.add_argument("--config")
    parser.add_argument("--json", action="store_true")
    options = parser.parse_args(argv)
    if (
        bool(options.analysis) == bool(options.analysis_result)
        or options.analysis_result
        and options.projection
    ):
        parser.error("Choose one analysis file with --projection, or --analysis-result")
    if options.analysis and not options.projection:
        parser.error("File binding requires the actual --projection input")
    value = {"schema_version": 1, "operation": "mine.bind", "exit_code": 2}
    try:
        result_id(options.candidate)
        if Path(options.out).exists():
            raise ArchiveError(
                "output_exists", "Binding output already exists; choose a new path"
            )
        with ExitStack() as stack:
            if options.analysis_result:
                from ..tracing.catalog import TraceCatalog
                from ..tracing.store import open_store

                catalog = TraceCatalog(
                    stack.enter_context(open_store(load_config(options.config)))
                )
                evidence = registered_evidence(catalog, options.analysis_result)
            else:
                evidence = file_evidence(options.analysis, options.projection)
            binding = bind(evidence, options.candidate)
        value["binding_id"] = binding["binding_id"]
        write_new_file(Path(options.out), [canonical(binding)])
        value.update(
            status="bound",
            exit_code=0,
            output=str(Path(options.out).resolve()),
            candidate_id=options.candidate,
            historical_check_identity="unavailable",
            optimization_verified=False,
            llm_used=False,
            next_actions=[
                [
                    "engineering-ci",
                    "checks",
                    "--binding",
                    str(Path(options.out).resolve()),
                    "--reuse",
                    "--json",
                    ".test-tools/checks/pr.json",
                ]
            ],
        )
    except ArchiveError as error:
        value.update(status=error.status, reason=str(error), exit_code=2)
    except TraceConfigurationError:
        value.update(
            status="invalid_configuration", reason="Tracing configuration is invalid"
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        value.update(
            status="invalid_input",
            reason="Cannot read or save the requested binding evidence",
        )
    if value["exit_code"]:
        value["binding_saved"] = False
        if value["status"] == "output_exists" or "binding_id" in value:
            retry = Path(options.out).resolve()
            retry = retry.with_name(
                f"{retry.stem}.retry-{uuid4().hex[:8]}{retry.suffix}"
            )
            command = ["engineering-ci", "mine", "bind"]
            if options.analysis_result:
                command += ["--analysis-result", options.analysis_result]
            else:
                command += [
                    str(Path(options.analysis).resolve()),
                    "--projection",
                    str(Path(options.projection).resolve()),
                ]
            if options.config:
                command += ["--config", str(Path(options.config).resolve())]
            value["next_actions"] = [
                [
                    *command,
                    "--candidate",
                    options.candidate,
                    "--profile",
                    options.profile,
                    "--out",
                    str(retry),
                    "--json",
                ]
            ]
    print(
        json.dumps(value, sort_keys=True)
        if options.json
        else f"Handler binding: {json.dumps(value, sort_keys=True)}"
    )
    return value["exit_code"]
