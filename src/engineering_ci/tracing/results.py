"""Small typed references to existing projection/analysis JSON, not an Agent schema."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field

from .store import ArchiveError


def digest(value):
    """Return the SHA256 of actual bytes."""
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    """Normalize derived JSON formatting; original archive bytes are untouched."""
    try:
        return (
            json.dumps(
                value, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
            + b"\n"
        )
    except (TypeError, ValueError, RecursionError):
        raise ArchiveError("invalid_result", "Result is not valid JSON data") from None


def result_id(value):
    """Reject missing IDs before any SQL or file operation."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ArchiveError(
            "invalid_result", "Expected a complete SHA256 result or snapshot ID"
        )
    return value


@dataclass(frozen=True)
class ResultRecord:
    result_id: str
    kind: str
    snapshot_id: str
    parent_result_id: str | None
    input_digest: str
    document_digest: str
    document: bytes = field(repr=False)
    summary: dict
    origin: str


def prepare_result(value, input_digest, *, origin="computed"):
    """Check declared execution identity and retain only safe query summary fields."""
    from ..mining.analysis import (
        ANALYSIS_VERSION,
        LEGACY_ANALYSIS_VERSION,
        analysis_identity,
    )
    from .projection import ACTIVITIES, PROJECTION_VERSION, projection_identity

    if (
        not isinstance(value, dict)
        or type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
        or origin not in {"computed", "file"}
    ):
        raise ArchiveError("invalid_result", "Unsupported result document or origin")
    snapshot = result_id(value.get("snapshot_id"))
    input_digest = result_id(input_digest)
    completeness = value.get("completeness")
    if (
        not isinstance(completeness, str)
        or completeness not in {"complete", "incomplete", "unknown"}
        or value.get("llm_used") is not False
    ):
        raise ArchiveError(
            "invalid_result", "Unsupported completeness or LLM result scope"
        )
    dependencies = value.get("dependencies")
    if not isinstance(dependencies, dict) or any(
        not isinstance(name, str) or not isinstance(version, str)
        for name, version in dependencies.items()
    ):
        raise ArchiveError(
            "invalid_result", "Result dependency versions are unavailable"
        )
    if "projection_id" in value and "analysis_id" not in value:
        kind, parent = "projection", None
        if (
            value.get("projection_version") != PROJECTION_VERSION
            or value.get("mapping") != ACTIVITIES
            or value.get("format") != "opentelemetry-sdk-span-json"
            or not all(
                isinstance(value.get(name), expected)
                for name, expected in (
                    ("operations", list),
                    ("spans", list),
                    ("diagnostics", dict),
                    ("turns", dict),
                )
            )
        ):
            raise ArchiveError("invalid_result", "Unsupported projection contract")
        identity = projection_identity(value)
        identity_id = result_id(value["projection_id"])
        allowed = {
            "invalid_records",
            "invalid_operations",
            "duplicate_items",
            "unmapped_records",
            "operations_without_valid_time",
        }
        diagnostics = value["diagnostics"]
        if set(diagnostics) - allowed or any(
            type(count) is not int or count < 0 for count in diagnostics.values()
        ):
            raise ArchiveError("invalid_result", "Unsupported projection diagnostics")
        expected_dependencies = {
            "opentelemetry-sdk",
            "opentelemetry-semantic-conventions",
        }
        summary = {
            "status": "generated",
            "version": value["projection_version"],
            "mapping": ACTIVITIES,
            "operations": len(value["operations"]),
            "spans": len(value["spans"]),
            "diagnostics": diagnostics,
        }
    elif "analysis_id" in value:
        kind, parent = "analysis", result_id(value.get("projection_id"))
        if (
            not isinstance(value.get("analysis_version"), str)
            or value.get("analysis_version")
            not in {ANALYSIS_VERSION, LEGACY_ANALYSIS_VERSION}
            or value.get("case_scope") != "turn"
            or type(value.get("allow_incomplete")) is not bool
            or value.get("input_sha256") != input_digest
            or not isinstance(value.get("status"), str)
            or value.get("status") not in {"passed", "unavailable"}
            or not all(
                isinstance(value.get(name), list)
                for name in ("selected_cases", "excluded_cases", "variants", "dfg")
            )
        ):
            raise ArchiveError(
                "invalid_result", "Unsupported analysis contract or input digest"
            )
        if (
            value["analysis_version"] == ANALYSIS_VERSION
            and value.get("engine") != "rust4pm"
        ):
            raise ArchiveError("invalid_result", "Unsupported analysis engine")
        if (
            value["analysis_version"] == LEGACY_ANALYSIS_VERSION
            and value.get("engine", "pm4py") != "pm4py"
        ):
            raise ArchiveError(
                "invalid_result", "Unsupported historical analysis engine"
            )
        expected_dependencies = (
            {"r4pm"}
            if value["analysis_version"] == ANALYSIS_VERSION
            else {"pm4py", "pandas", "numpy"}
        )
        identity = analysis_identity(value)
        identity_id = result_id(value["analysis_id"])
        if any(
            not isinstance(item, dict) or not isinstance(item.get("reason"), str)
            for item in value["excluded_cases"]
        ):
            raise ArchiveError("invalid_result", "Invalid excluded case metadata")
        reasons = Counter(item["reason"] for item in value["excluded_cases"])
        if set(reasons) - {
            "open_or_missing_turn_boundary",
            "missing_or_invalid_time",
            "overlap_or_timestamp_tie",
        }:
            raise ArchiveError("invalid_result", "Unsupported exclusion reason")
        summary = {
            "status": value["status"],
            "version": value["analysis_version"],
            "engine": value.get("engine", "pm4py"),
            "case_scope": "turn",
            "allow_incomplete": value["allow_incomplete"],
            "selected_cases": len(value["selected_cases"]),
            "excluded_cases": len(value["excluded_cases"]),
            "exclusion_counts": dict(sorted(reasons.items())),
            "variants": len(value["variants"]),
            "dfg_edges": len(value["dfg"]),
        }
    else:
        raise ArchiveError(
            "invalid_result", "Expected an existing projection or analysis document"
        )
    if set(dependencies) != expected_dependencies or any(
        not re.fullmatch(r"[0-9][0-9A-Za-z.+-]{0,63}", ver)
        for ver in dependencies.values()
    ):
        raise ArchiveError("invalid_result", "Unsupported dependency names or versions")
    if digest(canonical(identity).rstrip(b"\n")) != identity_id:
        raise ArchiveError(
            "identity_conflict",
            "Result ID does not match its declared input, mapping or tool versions",
        )
    document = canonical(value)
    return ResultRecord(
        identity_id,
        kind,
        snapshot,
        parent,
        input_digest,
        digest(document),
        document,
        {
            **summary,
            "dependencies": dependencies,
            "completeness": completeness,
            "llm_used": False,
        },
        origin,
    )
