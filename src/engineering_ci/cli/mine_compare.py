"""Compare analysis files or registered IDs without rerunning the mining backend."""

import argparse
import json
from contextlib import ExitStack
from pathlib import Path

from ..mining.comparison import AnalysisEvidence, compare
from ..tracing.config import TraceConfigurationError, load_config
from ..tracing.results import canonical, digest, prepare_result
from ..tracing.store import ArchiveError
from .trace import load_document, write_new_file


def file_evidence(path, projection_path):
    _, analysis = load_document(path)
    projection = None
    if projection_path:
        raw, projection = load_document(projection_path)
        prepare_result(projection, "0" * 64)
        if digest(raw) != analysis.get("input_sha256") or (
            projection["projection_id"],
            projection["snapshot_id"],
        ) != (analysis.get("projection_id"), analysis.get("snapshot_id")):
            raise ArchiveError(
                "identity_conflict",
                "Analysis does not match its original projection bytes",
            )
    return AnalysisEvidence(analysis, projection, "file", projection is not None)


def registered_evidence(catalog, identifier):
    metadata = catalog.result(identifier)
    if metadata["kind"] != "analysis":
        raise ArchiveError(
            "invalid_input", "Comparison requires registered analysis results"
        )
    analysis = json.loads(catalog.document(identifier))
    projection = json.loads(catalog.document(metadata["parent_result_id"]))
    prepare_result(analysis, metadata["input_digest"], origin="file")
    prepare_result(projection, "0" * 64)
    if (
        (analysis["analysis_id"], analysis["projection_id"], analysis["snapshot_id"])
        != (
            metadata["result_id"],
            metadata["parent_result_id"],
            metadata["snapshot_id"],
        )
        or projection["snapshot_id"] != metadata["snapshot_id"]
        or digest(canonical(projection))
        != catalog.result(metadata["parent_result_id"])["document_digest"]
    ):
        raise ArchiveError(
            "archive_corrupt", "Registered projection association is unavailable"
        )
    return AnalysisEvidence(analysis, projection, metadata["origin"], True)


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
