"""Bind one observed candidate to the existing complete PR check operation."""

import json
from pathlib import Path

from ..check_operations import OPERATIONS
from ..pipeline import PIPELINE
from ..tracing._compat import archive_errors
from ..tracing.results import canonical, digest, result_id
from ..tracing.store import ArchiveError
from .candidates import candidates

BINDING_VERSION = "registered-pr-check-reuse/1"
SOURCE_FIELDS = (
    "report_id",
    "candidate_id",
    "candidate_document_sha256",
    "analysis_id",
    "analysis_document_sha256",
    "projection_id",
    "projection_document_sha256",
    "snapshot_id",
)


def target(pipeline):
    """Use the registered profile without copying commands or weakening its gates."""
    names = pipeline["profiles"]["pr"]
    if set(names) != set(OPERATIONS) or len(names) != len(OPERATIONS):
        raise ArchiveError(
            "invalid_binding", "The complete registered PR profile is required"
        )
    return {
        "handler": "checks.pr",
        "checks": list(names),
        "pipeline_sha256": digest(canonical(pipeline)),
        "reuse_policy": "matching_execution_identity",
    }


@archive_errors
def bind(evidence, candidate_id, *, pipeline=PIPELINE):
    """Select a real candidate; the explicit target is not inferred historical work."""
    result_id(candidate_id)
    report = candidates(evidence, limit=100)
    if report["status"] != "reported":
        raise ArchiveError(
            "unavailable", "Binding requires verified analysis and projection evidence"
        )
    candidate = next(
        (item for item in report["candidates"] if item["candidate_id"] == candidate_id),
        None,
    )
    if candidate is None:
        raise ArchiveError(
            "invalid_binding", "Candidate is not present in this verified analysis"
        )
    if candidate["kind"] != "adjacent_activity_repeat" or candidate["pattern"][
        "activities"
    ] != ["command_execution", "command_execution"]:
        raise ArchiveError(
            "unsupported_candidate",
            "PR reuse supports adjacent command activity investigation only",
        )
    source = report["source"]
    value = {
        "schema_version": 1,
        "binding_version": BINDING_VERSION,
        "source": {
            "report_id": report["report_id"],
            "candidate_id": candidate_id,
            "candidate_document_sha256": digest(canonical(candidate)),
            "analysis_id": source["analysis_id"],
            "analysis_document_sha256": source["analysis_document_digest"],
            "projection_id": source["projection_id"],
            "projection_document_sha256": source["projection_document_digest"],
            "snapshot_id": source["snapshot_id"],
            "completeness": report["completeness"],
        },
        "target": target(pipeline),
        "mapping_basis": "explicit_target_selection",
        "historical_check_identity": "unavailable",
        "optimization_verified": False,
        "llm_used": False,
    }
    value["binding_id"] = digest(canonical(value))
    return value


def validate(value, *, pipeline=PIPELINE):
    """Reject changed contracts and unsupported actions before invoking any executor."""
    fields = {
        "schema_version",
        "binding_version",
        "binding_id",
        "source",
        "target",
        "mapping_basis",
        "historical_check_identity",
        "optimization_verified",
        "llm_used",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ArchiveError("invalid_binding", "Unsupported binding fields")
    source = value["source"]
    if not isinstance(source, dict) or set(source) != {*SOURCE_FIELDS, "completeness"}:
        raise ArchiveError("invalid_binding", "Unsupported binding source")
    for name in SOURCE_FIELDS:
        result_id(source[name])
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["binding_version"] != BINDING_VERSION
        or source["completeness"] not in ("complete", "incomplete", "unknown")
        or value["mapping_basis"] != "explicit_target_selection"
        or value["historical_check_identity"] != "unavailable"
        or value["optimization_verified"] is not False
        or value["llm_used"] is not False
    ):
        raise ArchiveError("invalid_binding", "Unsupported binding semantics")
    if value["binding_id"] != digest(
        canonical({k: v for k, v in value.items() if k != "binding_id"})
    ):
        raise ArchiveError(
            "identity_conflict", "Binding content differs from its identity"
        )
    if value["target"] != target(pipeline):
        raise ArchiveError(
            "stale_binding", "Pipeline contract changed; bind the candidate again"
        )
    return value


def read(path, *, pipeline=PIPELINE):
    """Read a bounded local artifact without connecting to the archive or mining engine."""
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            raise ValueError("Binding exceeds 64 KiB")
        return validate(json.loads(raw), pipeline=pipeline)
    except (OSError, ValueError, TypeError, RecursionError):
        raise ArchiveError(
            "invalid_binding", "Cannot read a supported Handler binding"
        ) from None
