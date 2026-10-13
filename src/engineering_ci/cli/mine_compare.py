"""Compare analysis files or registered IDs without rerunning the mining backend."""

import argparse
import json
from contextlib import ExitStack
from pathlib import Path

from ..mining.comparison import compare
from ..tracing.config import TraceConfigurationError, load_config
from ..tracing.results import canonical
from ..tracing.store import ArchiveError
from .mine_inputs import file_evidence, registered_evidence
from .trace import write_new_file


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", nargs="?")
    parser.add_argument("candidate", nargs="?")
    parser.add_argument("--baseline-result")
    parser.add_argument("--candidate-result")
    parser.add_argument("--baseline-projection")
    parser.add_argument("--candidate-projection")
    parser.add_argument("--out", required=True)
    parser.add_argument("--config")
    parser.add_argument("--json", action="store_true")
    options = parser.parse_args(argv)
    files = options.baseline and options.candidate
    ids = options.baseline_result and options.candidate_result
    if (
        bool(files) == bool(ids)
        or (
            ids
            and (
                options.baseline
                or options.candidate
                or options.baseline_projection
                or options.candidate_projection
            )
        )
        or (files and (options.baseline_result or options.candidate_result))
    ):
        parser.error(
            "Choose two analysis files or two result IDs, exactly one input mode"
        )
    value = {"schema_version": 1, "operation": "mine.compare", "exit_code": 2}
    try:
        with ExitStack() as stack:
            if ids:
                from ..tracing.catalog import TraceCatalog
                from ..tracing.store import open_store

                catalog = TraceCatalog(
                    stack.enter_context(open_store(load_config(options.config)))
                )
                baseline, candidate = [
                    registered_evidence(catalog, identifier)
                    for identifier in (
                        options.baseline_result,
                        options.candidate_result,
                    )
                ]
            else:
                baseline = file_evidence(options.baseline, options.baseline_projection)
                candidate = file_evidence(
                    options.candidate, options.candidate_projection
                )
            result = compare(baseline, candidate)
        write_new_file(Path(options.out), [canonical(result)])
        value.update(
            status=result["status"],
            exit_code=0 if result["status"] == "compared" else 3,
            comparison_id=result["comparison_id"],
            results_equivalent=result["results_equivalent"],
            reasons=result["reasons"],
            optimization_verified=False,
            output=str(Path(options.out).resolve()),
        )
    except ArchiveError as error:
        value.update(status=error.status, reason=str(error))
    except TraceConfigurationError:
        value.update(
            status="invalid_configuration", reason="Tracing configuration is invalid"
        )
    except (OSError, ValueError, TypeError, KeyError):
        value.update(
            status="invalid_input",
            reason="Cannot read or save the requested comparison",
        )
    print(
        json.dumps(value, sort_keys=True)
        if options.json
        else f"Result comparison: {json.dumps(value, sort_keys=True)}"
    )
    return value["exit_code"]
