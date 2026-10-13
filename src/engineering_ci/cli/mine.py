"""Analyze a fixed projection locally; never call models or execute candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from contextlib import ExitStack, redirect_stdout
from pathlib import Path

from ..mining.analysis import analyze
from ..tracing.config import TraceConfigurationError
from ..tracing.store import ArchiveError
from .trace import config_path, load_document, write_new_file


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == "compare":
        from .mine_compare import main as comparison_command

        return comparison_command(args[1:])
    if args and args[0] == "candidates":
        from .mine_candidates import main as candidate_command

        return candidate_command(args[1:])
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Existing-result operations: mine compare --help; mine candidates --help",
    )
    parser.add_argument(
        "projection", nargs="?", help="Content-free JSON produced by trace project"
    )
    parser.add_argument(
        "--projection-result", help="Registered projection ID instead of a file"
    )
    parser.add_argument(
        "--record", action="store_true", help="Register the result before exporting it"
    )
    parser.add_argument(
        "--config", help="Tracing configuration for explicit database operations"
    )
    parser.add_argument("--out", required=True, help="New analysis result file")
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--json", action="store_true")
    options = parser.parse_args(argv)
    if bool(options.projection) == bool(options.projection_result):
        parser.error("Choose a projection file or --projection-result, exactly one")
    registered = None
    attempted = None
    try:
        with ExitStack() as stack:
            catalog = None
            if options.record or options.projection_result:
                from ..tracing.catalog import TraceCatalog
                from ..tracing.config import load_config
                from ..tracing.store import open_store

                catalog = TraceCatalog(
                    stack.enter_context(open_store(load_config(options.config)))
                )
            if options.projection_result:
                if catalog.result(options.projection_result)["kind"] != "projection":
                    raise ArchiveError(
                        "invalid_input",
                        "--projection-result must identify a registered projection",
                    )
                raw = catalog.document(options.projection_result)
                projection = json.loads(raw)
            else:
                raw, projection = load_document(options.projection)
            # Keep third-party warnings separate from the single JSON operation result.
            with redirect_stdout(sys.stderr):
                result = analyze(
                    projection,
                    hashlib.sha256(raw).hexdigest(),
                    allow_incomplete=options.allow_incomplete,
                )
            if options.record:
                from ..tracing.results import prepare_result

                record = prepare_result(result, hashlib.sha256(raw).hexdigest())
                attempted = record.result_id
                registered = catalog.save(record, projection_input=projection)
        write_new_file(
            Path(options.out), [json.dumps(result, sort_keys=True).encode() + b"\n"]
        )
        value = {
            "schema_version": 1,
            "operation": "mine",
            "status": result["status"],
            "exit_code": 0 if result["status"] == "passed" else 3,
            "analysis_id": result["analysis_id"],
            "cases": len(result["selected_cases"]),
            "excluded_cases": len(result["excluded_cases"]),
            "variants": len(result["variants"]),
            "llm_used": False,
            "output": str(Path(options.out).resolve()),
            "engine": result.get("engine", "rust4pm"),
            "association": "registered" if options.record else "file_only",
        }
    except ArchiveError as error:
        value = {
            "schema_version": 1,
            "operation": "mine",
            "status": error.status,
            "reason": str(error),
            "exit_code": 2,
        }
    except TraceConfigurationError:
        value = {
            "schema_version": 1,
            "operation": "mine",
            "status": "invalid_configuration",
            "reason": "Tracing configuration is invalid",
            "exit_code": 2,
        }
    except ImportError:
        value = {
            "schema_version": 1,
            "operation": "mine",
            "status": "missing_tool",
            "reason": "Install the mining extra to enable Rust4PM (r4pm)",
            "exit_code": 2,
        }
    except (OSError, ValueError, TypeError, RecursionError):
        value = {
            "schema_version": 1,
            "operation": "mine",
            "status": "invalid_input",
            "reason": "Cannot read, validate or write the requested analysis data",
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
        else f"Workflow mining: {json.dumps(value, sort_keys=True)}"
    )
    return value["exit_code"]
