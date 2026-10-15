"""Export CI execution evidence; ordinary analysis export belongs to Vesryn."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from vesryn.cli.mine_xes import export_bytes
from vesryn.cli.mine_xes import main as portable_main
from vesryn.workflow_core.documents import WorkflowError

from ..tracing.store import ArchiveError
from .mine_workflow import execution_log, output_path
from .trace import write_new_file

__all__ = ["export_bytes", "main"]


def main(argv=None):
    """Keep domain admission and source protection for the existing execution option."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not any(
        item == "--execution" or item.startswith("--execution=") for item in args
    ):
        return portable_main(args)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution", type=Path, required=True)
    parser.add_argument("--event-log-id")
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    options = parser.parse_args(args)
    value = {
        "schema_version": 1,
        "operation": "mine.export-xes",
        "exit_code": 2,
        "xes_saved": False,
    }
    try:
        output = output_path(options.out, options.execution)
        if output.exists():
            raise ArchiveError("output_exists", "XES output exists; choose a new path")
        log = execution_log(
            options.execution, allow_incomplete=options.allow_incomplete
        )
        if options.event_log_id and options.event_log_id != log["event_log_id"]:
            raise WorkflowError(
                "Execution evidence changed; it no longer matches the requested event-log ID"
            )
        from ..workflow_adapters.event_log import export_xes

        data = export_xes(log)
        write_new_file(output, [data])
        value.update(
            status="exported",
            exit_code=0,
            xes_saved=True,
            output=str(output),
            sha256=hashlib.sha256(data).hexdigest(),
            cases=len(log["cases"]),
            excluded_cases=len(log["excluded_cases"]),
            event_log_id=log["event_log_id"],
            execution_id=log["execution_id"],
            case_scope=log["case_scope"],
            completeness=log["completeness"],
            recorded_result=log["recorded_result"],
            current_input_validity="not_checked",
            optimization_verified=False,
            llm_used=False,
        )
    except WorkflowError as error:
        value.update(status="invalid_or_unavailable", reason=str(error))
    except ArchiveError as error:
        value.update(status=error.status, reason=str(error))
    except (OSError, ValueError, KeyError, TypeError):
        value.update(
            status="invalid_input",
            reason="Cannot read or export verified XES case data",
        )
    print(
        json.dumps(value, sort_keys=True)
        if options.json
        else f"XES export: {json.dumps(value, sort_keys=True)}"
    )
    return value["exit_code"]
