"""Select honest observed sequences and delegate discovery to Rust4PM."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from importlib.metadata import version
from itertools import pairwise
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree as ET

from ..tracing.projection import ACTIVITIES, PROJECTION_VERSION
from ..tracing.store import ArchiveError

LEGACY_ANALYSIS_VERSION = "observed-turn-variants/1"
ANALYSIS_VERSION = "observed-turn-variants/2"


def analysis_identity(value):
    """Keep historical PM4Py identities distinct from the Rust4PM backend."""
    names = (
        "input_sha256",
        "snapshot_id",
        "projection_id",
        "analysis_version",
        "case_scope",
        "allow_incomplete",
        "dependencies",
    )
    if value["analysis_version"] == ANALYSIS_VERSION:
        names += ("engine",)
    return {name: value[name] for name in names}


def cases_xes(selected, *, analysis=None, excluded=0):
    """Serialize admitted cases; export details only from verified projected fields."""
    log = ET.Element(
        "log", {"xes.version": "1.0", "xmlns": "http://www.xes-standard.org/"}
    )
    if analysis is not None:
        for name in (
            "analysis_id",
            "projection_id",
            "snapshot_id",
            "input_sha256",
            "completeness",
        ):
            ET.SubElement(
                log, "string", key="engineering_ci:" + name, value=analysis[name]
            )
        ET.SubElement(
            log, "int", key="engineering_ci:selected_cases", value=str(len(selected))
        )
        ET.SubElement(
            log, "int", key="engineering_ci:excluded_cases", value=str(excluded)
        )
        ET.SubElement(log, "boolean", key="engineering_ci:llm_used", value="false")
    for case in selected:
        trace = ET.SubElement(log, "trace")
        ET.SubElement(trace, "string", key="concept:name", value=case["turn_id"])
        if analysis is not None:
            ET.SubElement(
                trace, "string", key="engineering_ci:boundary", value=case["boundary"]
            )
        for operation in case["operations"]:
            event = ET.SubElement(trace, "event")
            ET.SubElement(
                event, "string", key="concept:name", value=operation["activity"]
            )
            if analysis is not None:
                for key in ("event_id", "outcome"):
                    ET.SubElement(
                        event,
                        "string",
                        key="engineering_ci:" + key,
                        value=operation[key],
                    )
                for key in ("start_ns", "end_ns"):
                    ET.SubElement(
                        event,
                        "int",
                        key="engineering_ci:" + key,
                        value=str(operation[key]),
                    )
                end = operation["end_ns"]
                timestamp = datetime.fromtimestamp(end // 1_000_000_000, UTC).strftime(
                    "%Y-%m-%dT%H:%M:%S"
                )
                ET.SubElement(
                    event,
                    "date",
                    key="time:timestamp",
                    value=f"{timestamp}.{end % 1_000_000_000:09d}Z",
                )
    return ET.tostring(log, encoding="utf-8", xml_declaration=True)


def discover(selected):
    """Reuse the same admitted-case serializer; Rust4PM owns discovery algorithms."""
    import r4pm
    from r4pm import bindings

    if not selected:
        return {}, {}, {}, {}
    identifiers = []
    try:
        with TemporaryDirectory(prefix="engineering-ci-mining-") as directory:
            path = Path(directory) / "cases.xes"
            path.write_bytes(cases_xes(selected))
            log_id = r4pm.import_item("EventLog", str(path))
            identifiers.append(log_id)
            projection_id = bindings.log_to_activity_projection(log_id)
            identifiers.append(projection_id)
            variants = {
                tuple(item["activities"]): item["count"]
                for item in bindings.get_variants(projection_id)
            }
            graph = bindings.discover_dfg(log_id)
            edges = {
                tuple(edge): count
                for edge, count in graph["directly_follows_relations"]
            }
            # Rust4PM returns boundary sets; preserve the existing frequency fields.
            starts, ends = Counter(), Counter()
            for activities, count in variants.items():
                starts[activities[0]] += count
                ends[activities[-1]] += count
            return variants, edges, starts, ends
    except (ValueError, RuntimeError, KeyError, TypeError):
        raise ArchiveError(
            "analysis_failed", "Rust4PM could not analyze the admitted event log"
        ) from None
    finally:
        for identifier in reversed(identifiers):
            r4pm.remove_item(identifier)


def select_cases(projection, *, allow_incomplete=False):
    """Exclude ambiguous order without treating omitted data as a successful case."""
    if (
        not isinstance(projection, dict)
        or projection.get("projection_version") != PROJECTION_VERSION
        or not isinstance(projection.get("operations"), list)
        or not isinstance(projection.get("turns"), dict)
    ):
        raise ArchiveError(
            "invalid_projection", "Unsupported projection structure or version"
        )
    completeness = projection.get("completeness")
    if completeness not in {"complete", "incomplete", "unknown"}:
        raise ArchiveError(
            "invalid_projection", "Projection completeness is unavailable"
        )
    if completeness == "incomplete" and not allow_incomplete:
        raise ArchiveError(
            "incomplete_input", "Incomplete input requires explicit --allow-incomplete"
        )
    grouped, excluded, selected = defaultdict(list), [], []
    for operation in projection["operations"]:
        if (
            not isinstance(operation, dict)
            or operation.get("activity") not in ACTIVITIES.values()
            or not isinstance(operation.get("turn_id"), str)
            or not isinstance(operation.get("event_id"), str)
            or operation.get("outcome") not in {"passed", "failed", "unknown"}
        ):
            raise ArchiveError("invalid_projection", "Invalid projected operation")
        grouped[operation["turn_id"]].append(operation)
    for turn, members in sorted(grouped.items()):
        boundary = projection["turns"].get(turn, {})
        if not isinstance(boundary, dict):
            raise ArchiveError("invalid_projection", "Invalid projected turn boundary")
        reason = None
        if not boundary.get("started") or boundary.get("terminal") not in {
            "completed",
            "cancelled",
        }:
            reason = "open_or_missing_turn_boundary"
        elif any(
            type(item.get("start_ns")) is not int
            or type(item.get("end_ns")) is not int
            or not 0 <= item["start_ns"] <= item["end_ns"] < 2**63
            for item in members
        ):
            reason = "missing_or_invalid_time"
        else:
            members = sorted(members, key=lambda item: item["start_ns"])
            if any(
                right["start_ns"] < left["end_ns"]
                or right["start_ns"] <= left["start_ns"]
                or right["end_ns"] <= left["end_ns"]
                for left, right in pairwise(members)
            ):
                reason = "overlap_or_timestamp_tie"
        if reason:
            excluded.append(
                {"turn_id": turn, "reason": reason, "operations": len(members)}
            )
        else:
            selected.append(
                {
                    "turn_id": turn,
                    "boundary": boundary["terminal"],
                    "operations": members,
                }
            )
    return selected, excluded


def analyze(projection, input_digest, *, allow_incomplete=False):
    """Use variants and DFG APIs only; models and production execution are absent."""
    selected, excluded = select_cases(projection, allow_incomplete=allow_incomplete)
    variants, dfg, starts, ends = discover(selected)
    dependencies = {"r4pm": version("r4pm")}
    identity = {
        "input_sha256": input_digest,
        "snapshot_id": projection.get("snapshot_id"),
        "projection_id": projection.get("projection_id"),
        "analysis_version": ANALYSIS_VERSION,
        "case_scope": "turn",
        "allow_incomplete": allow_incomplete,
        "dependencies": dependencies,
        "engine": "rust4pm",
    }
    analysis_id = hashlib.sha256(
        json.dumps(
            analysis_identity(identity), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    counts = Counter(item["activity"] for item in projection["operations"])
    outcomes = Counter(item["outcome"] for item in projection["operations"])
    return {
        "schema_version": 1,
        "analysis_id": analysis_id,
        **identity,
        "status": "passed" if selected else "unavailable",
        "reason": None
        if selected
        else "No closed observed case has unambiguous operation times",
        "llm_used": False,
        "completeness": projection["completeness"],
        "scope": "Observed completed native items; unknown completeness is included and labelled. DFG describes observed order, not causal dependencies.",
        "activity_counts": dict(sorted(counts.items())),
        "outcome_counts": dict(sorted(outcomes.items())),
        "variants": [
            {"activities": list(key), "cases": int(count)}
            for key, count in sorted(variants.items())
        ],
        "dfg": [
            {"from": left, "to": right, "count": int(count)}
            for (left, right), count in sorted(dfg.items())
        ],
        "start_activities": {key: int(value) for key, value in sorted(starts.items())},
        "end_activities": {key: int(value) for key, value in sorted(ends.items())},
        "selected_cases": [
            {
                "turn_id": case["turn_id"],
                "boundary": case["boundary"],
                "event_ids": [item["event_id"] for item in case["operations"]],
            }
            for case in selected
        ],
        "excluded_cases": excluded,
        "cost": None,
        "limitations": [
            "Activities classify native item types, not individual pytest/check identities",
            "Repeated activities are observations, not proof of removable work",
            "No full-workflow completeness or optimization improvement is asserted",
        ],
    }
