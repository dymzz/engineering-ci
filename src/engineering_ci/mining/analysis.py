"""Select honest observed sequences and delegate discovery to PM4Py."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from importlib.metadata import version
from itertools import pairwise

from ..tracing.projection import ACTIVITIES, PROJECTION_VERSION
from ..tracing.store import ArchiveError

ANALYSIS_VERSION = "observed-turn-variants/1"


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
    import pandas as pd
    import pm4py

    selected, excluded = select_cases(projection, allow_incomplete=allow_incomplete)
    rows = [
        {
            "case:concept:name": case["turn_id"],
            "concept:name": operation["activity"],
            "time:timestamp": pd.to_datetime(operation["end_ns"], unit="ns", utc=True),
        }
        for case in selected
        for operation in case["operations"]
    ]
    variants, dfg, starts, ends = {}, {}, {}, {}
    if rows:
        frame = pd.DataFrame(rows)
        variants = pm4py.get_variants_as_tuples(frame)
        dfg, starts, ends = pm4py.discover_dfg(frame)
    dependencies = {name: version(name) for name in ("pm4py", "pandas", "numpy")}
    identity = {
        "input_sha256": input_digest,
        "snapshot_id": projection.get("snapshot_id"),
        "projection_id": projection.get("projection_id"),
        "analysis_version": ANALYSIS_VERSION,
        "case_scope": "turn",
        "allow_incomplete": allow_incomplete,
        "dependencies": dependencies,
    }
    analysis_id = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    counts = Counter(item["activity"] for item in projection["operations"])
    outcomes = Counter(item["outcome"] for item in projection["operations"])
    return {
        "schema_version": 1,
        "analysis_id": analysis_id,
        **identity,
        "status": "passed" if rows else "unavailable",
        "reason": None
        if rows
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
