"""Produce review-only hypotheses from existing analysis and projection evidence."""

import argparse
import json
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

from ..mining.candidates import candidates, validate_limits
from ..tracing.config import TraceConfigurationError, load_config
from ..tracing.results import canonical
from ..tracing.store import ArchiveError
from .mine_inputs import file_evidence, registered_evidence
from .trace import config_path, write_new_file


def main(argv=None):
    """Read existing evidence, write a new report and preserve actual exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis", nargs="?")
    parser.add_argument("--analysis-result")
    parser.add_argument(
        "--projection", help="Original projection bytes used by file analysis"
    )
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--evidence-limit", type=int, default=3)
    parser.add_argument("--out", required=True)
    parser.add_argument("--config")
    parser.add_argument("--json", action="store_true")
    options = parser.parse_args(argv)
    if (
        bool(options.analysis) == bool(options.analysis_result)
        or options.analysis_result
        and options.projection
    ):
        parser.error(
            "Choose one analysis file (optional --projection) or --analysis-result"
        )
    value = {"schema_version": 1, "operation": "mine.candidates", "exit_code": 2}
    try:
        validate_limits(options.limit, options.evidence_limit)
        if Path(options.out).exists():
            raise ArchiveError(
                "output_exists", "Report output already exists; choose a new path"
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
            report = candidates(
                evidence, limit=options.limit, evidence_limit=options.evidence_limit
            )
        value.update(
            status=report["status"],
            exit_code=0 if report["status"] == "reported" else 3,
            report_id=report["report_id"],
            total_candidates=report["total_candidates"],
            omitted_candidates=report["omitted_candidates"],
            reasons=report["reasons"],
            review_status="not_reviewed",
            optimization_verified=False,
            llm_used=False,
            analysis_id=report["source"]["analysis_id"],
        )
        write_new_file(Path(options.out), [canonical(report)])
        value["output"] = str(Path(options.out).resolve())
    except ArchiveError as error:
        value.update(status=error.status, reason=str(error), exit_code=2)
    except TraceConfigurationError:
        value.update(
            status="invalid_configuration",
            reason="Tracing configuration is invalid",
            exit_code=2,
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        value.update(
            status="invalid_input",
            reason="Cannot read or save the requested candidate evidence",
            exit_code=2,
        )
    if value["exit_code"] == 2 and (
        value["status"] == "output_exists" or "report_id" in value
    ):
        if "report_id" in value:
            value["report_saved"] = False
        output = Path(options.out).resolve()
        retry = output.with_name(
            f"{output.stem}.retry-{uuid4().hex[:8]}{output.suffix}"
        )
        args = ["engineering-ci", "mine", "candidates"]
        if options.analysis_result:
            args += [
                "--analysis-result",
                options.analysis_result,
                "--config",
                str(config_path(options)),
            ]
        else:
            args += [str(Path(options.analysis).resolve())]
            if options.projection:
                args += ["--projection", str(Path(options.projection).resolve())]
        value["next_actions"] = [
            [
                *args,
                "--limit",
                str(options.limit),
                "--evidence-limit",
                str(options.evidence_limit),
                "--out",
                str(retry),
                "--json",
            ]
        ]
    print(
        json.dumps(value, sort_keys=True)
        if options.json
        else f"Candidate review: {json.dumps(value, sort_keys=True)}"
    )
    return value["exit_code"]
