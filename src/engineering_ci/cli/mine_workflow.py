"""Analyze existing registered workflow attempts without executing tools again."""

import argparse
import hashlib
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

from .._paths import ROOT
from ..checks import report_path
from ..tracing.store import ArchiveError
from ..workflow_adapters.checks import catalog
from ..workflow_adapters.event_log import event_log, export_xes
from ..workflow_adapters.evidence import execution_evidence
from ..workflow_core.documents import WorkflowError
from .trace import write_new_file


def execution_log(directory, *, allow_incomplete=False):
    """Read one explicit stable execution; no SDK, database or source trace access."""
    return event_log(
        execution_evidence(directory, catalog(ROOT), ROOT),
        allow_incomplete=allow_incomplete,
    )


def output_path(path, execution):
    """Protect source inputs and original execution evidence from export destinations."""
    output = report_path(Path(path), ROOT)
    if not output.is_relative_to(ROOT.resolve()) or output.is_relative_to(
        Path(execution).resolve()
    ):
        raise WorkflowError(
            "Workflow export must stay in the checkout outside its execution directory"
        )
    if output.exists():
        raise ArchiveError("output_exists", "Output exists; choose a new path")
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("execution", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--xes-out", type=Path)
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--json", action="store_true")
    options = parser.parse_args(argv)
    saved = False
    value = {
        "schema_version": 1,
        "operation": "mine.workflow",
        "exit_code": 2,
        "analysis_saved": False,
        "xes_saved": False,
    }
    try:
        output = output_path(options.out, options.execution)
        xes = (
            output_path(options.xes_out, options.execution) if options.xes_out else None
        )
        if output == xes:
            raise WorkflowError("Analysis and XES outputs must have different paths")
        log = execution_log(
            options.execution, allow_incomplete=options.allow_incomplete
        )
        from ..mining.workflow import analyze

        with redirect_stdout(sys.stderr):
            result = analyze(log)
        write_new_file(output, [json.dumps(result, sort_keys=True).encode() + b"\n"])
        saved = True
        value.update(
            status=result["status"],
            exit_code=0 if result["status"] == "passed" else 3,
            analysis_saved=True,
            output=str(output),
            analysis_id=result["analysis_id"],
            event_log_id=log["event_log_id"],
            case_scope=log["case_scope"],
            cases=len(log["cases"]),
            excluded_cases=len(log["excluded_cases"]),
            completeness=log["completeness"],
            engine=result["engine"],
            recorded_result=log["recorded_result"],
            current_input_validity="not_checked",
            optimization_verified=False,
            llm_used=False,
        )
        if xes:
            data = export_xes(log)
            write_new_file(xes, [data])
            value.update(
                xes_saved=True,
                xes={"output": str(xes), "sha256": hashlib.sha256(data).hexdigest()},
            )
    except (WorkflowError, ArchiveError) as error:
        value.update(
            status=getattr(error, "status", "invalid_or_unavailable"), reason=str(error)
        )
        if value["exit_code"] == 0:
            value["exit_code"] = 2
    except ImportError:
        value.update(
            status="missing_tool",
            reason="Install the locked mining extra for Rust4PM (r4pm)",
            exit_code=value["exit_code"] or 2,
        )
    except (OSError, ValueError, KeyError, TypeError):
        value.update(
            status="invalid_input",
            reason="Cannot read, analyze or save workflow evidence",
        )
        if value["exit_code"] == 0:
            value["exit_code"] = 2
    if saved and not value["xes_saved"] and options.xes_out:
        value["analysis_saved"] = True
        value["analysis_status"] = result["status"]
        value["next_actions"] = [
            [
                "engineering-ci",
                "mine",
                "export-xes",
                "--execution",
                str(options.execution.resolve()),
                "--event-log-id",
                log["event_log_id"],
                "--out",
                str(options.xes_out.resolve()),
                *(["--allow-incomplete"] if options.allow_incomplete else []),
                "--json",
            ]
        ]
    print(
        json.dumps(value, sort_keys=True)
        if options.json
        else "Workflow mining: " + json.dumps(value, sort_keys=True)
    )
    return value["exit_code"]
