"""Load existing analysis evidence for comparison and candidate review."""

import json

from ..mining.comparison import AnalysisEvidence
from ..tracing.results import canonical, digest, prepare_result
from ..tracing.store import ArchiveError
from .trace import load_document


def file_evidence(path, projection_path):
    """Verify analysis against its actual original projection bytes."""
    _, analysis = load_document(path)
    if not isinstance(analysis, dict) or "analysis_id" not in analysis:
        raise ArchiveError("invalid_input", "Expected an existing analysis document")
    prepare_result(analysis, analysis.get("input_sha256"), origin="file")
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
    """Read checked result documents and their registered projection association."""
    metadata = catalog.result(identifier)
    if metadata["kind"] != "analysis":
        raise ArchiveError(
            "invalid_input", "This operation requires a registered analysis"
        )
    analysis = json.loads(catalog.document(identifier))
    projection = json.loads(catalog.document(metadata["parent_result_id"]))
    prepare_result(analysis, metadata["input_digest"], origin="file")
    parent = catalog.result(metadata["parent_result_id"])
    prepared = prepare_result(projection, "0" * 64)
    if (
        (analysis["analysis_id"], analysis["projection_id"], analysis["snapshot_id"])
        != (
            metadata["result_id"],
            metadata["parent_result_id"],
            metadata["snapshot_id"],
        )
        or projection["snapshot_id"] != metadata["snapshot_id"]
        or parent["kind"] != "projection"
        or prepared.kind != "projection"
        or projection["projection_id"] != metadata["parent_result_id"]
        or digest(canonical(projection)) != parent["document_digest"]
    ):
        raise ArchiveError(
            "archive_corrupt", "Registered projection association is unavailable"
        )
    return AnalysisEvidence(analysis, projection, metadata["origin"], True)
