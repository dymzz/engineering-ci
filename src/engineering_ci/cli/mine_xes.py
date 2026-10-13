"""Export verified admitted cases for offline Rust4PM/Propel file import."""

import argparse
import hashlib
import json
from contextlib import ExitStack
from pathlib import Path

from ..mining.analysis import cases_xes
from ..mining.candidates import checked_cases
from ..tracing.config import TraceConfigurationError, load_config
from ..tracing.store import ArchiveError
from ..workflow_core.documents import WorkflowError
from .mine_inputs import file_evidence, registered_evidence
from .trace import write_new_file


def export_bytes(evidence):
    """Corroborate existing analysis without discovery or raw trace access."""
    if evidence.projection is None or not evidence.association_verified:
        raise ArchiveError(
            "unavailable", "XES export requires the original verified projection"
        )
    selected, excluded = checked_cases(evidence)
    if not selected:
        raise ArchiveError(
            "unavailable", "No admitted cases are available for XES export"
        )
    return (
        cases_xes(selected, analysis=evidence.analysis, excluded=len(excluded)),
        len(selected),
        len(excluded),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis", nargs="?")
    parser.add_argument(
        "--projection", help="Actual original projection file; required in offline mode"
    )
    parser.add_argument(
        "--analysis-result", help="Optional explicit PostgreSQL analysis ID"
    )
    parser.add_argument("--config")
    parser.add_argument(
        "--execution",
        type=Path,
        help="Registered workflow execution instead of a Codex analysis",
    )
    parser.add_argument(
        "--event-log-id", help="Require unchanged workflow event-log identity"
    )
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--out", required=True)
    parser.add_argument("--json", action="store_true")
    options = parser.parse_args(argv)
    if options.execution:
        if any(
            (
                options.analysis,
                options.analysis_result,
                options.projection,
                options.config,
            )
        ):
            parser.error(
                "--execution cannot be combined with Codex analysis inputs or database configuration"
            )
    elif options.event_log_id or options.allow_incomplete:
        parser.error("--event-log-id and --allow-incomplete require --execution")
    elif bool(options.analysis) == bool(options.analysis_result) or bool(
        options.analysis
    ) != bool(options.projection):
        parser.error("Choose analysis + --projection files, or --analysis-result only")
    value = {
        "schema_version": 1,
        "operation": "mine.export-xes",
        "exit_code": 2,
        "xes_saved": False,
    }
    try:
        output = Path(options.out).resolve()
        if output.exists():
            raise ArchiveError("output_exists", "XES output exists; choose a new path")
        if options.execution:
            from ..workflow_adapters.event_log import export_xes
            from .mine_workflow import execution_log, output_path

            output = output_path(output, options.execution)
            log = execution_log(
                options.execution, allow_incomplete=options.allow_incomplete
            )
            if options.event_log_id and options.event_log_id != log["event_log_id"]:
                raise WorkflowError(
                    "Execution evidence changed; it no longer matches the requested event-log ID"
                )
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
        else:
            value.update(_codex_export(options, output))
    except WorkflowError as error:
        value.update(status="invalid_or_unavailable", reason=str(error))
    except ArchiveError as error:
        value.update(status=error.status, reason=str(error))
    except TraceConfigurationError:
        value.update(
            status="invalid_configuration", reason="Tracing configuration is invalid"
        )
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


def _codex_export(options, output):
    """Preserve the original analysis/projection verification and PostgreSQL option."""
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
        data, selected, excluded = export_bytes(evidence)
    write_new_file(output, [data])
    return {
        "status": "exported",
        "exit_code": 0,
        "xes_saved": True,
        "output": str(output),
        "sha256": hashlib.sha256(data).hexdigest(),
        "cases": selected,
        "excluded_cases": excluded,
        "analysis_id": evidence.analysis["analysis_id"],
        "projection_id": evidence.analysis["projection_id"],
        "completeness": evidence.analysis["completeness"],
        "optimization_verified": False,
        "llm_used": False,
    }
