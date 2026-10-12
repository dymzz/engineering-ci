"""Explicit actual OTel SDK acceptance, without database or remote telemetry."""

import json

from engineering_ci.tracing.projection import standardize
from engineering_ci.tracing.sources.codex_rollout import record_at


def records():
    values = [
        {
            "type": "session_meta",
            "payload": {"id": "session-1", "cli_version": "0.162.0-alpha.2"},
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "turn_id": "turn-1",
                "started_at_ms": 1000,
                "completed_at_ms": 2000,
                "item": {
                    "type": "CommandExecution",
                    "id": "item-1",
                    "exit_code": 1,
                    "status": "completed",
                    "stdout": "SECRET",
                    "command": ["PRIVATE"],
                },
            },
        },
    ]
    return [
        record_at(i, json.dumps(value).encode() + b"\n")
        for i, value in enumerate(values)
    ]


def test_sdk_projection_has_real_times_status_and_deterministic_ids():
    result = standardize(records(), "snapshot-1")
    again = standardize(records(), "snapshot-1")
    assert result == again and result["cost"] is None and result["llm_used"] is False
    assert "SECRET" not in json.dumps(result) and "PRIVATE" not in json.dumps(result)
    span = result["spans"][0]
    assert span["status"]["status_code"] == "ERROR"
    assert span["attributes"]["process.exit.code"] == 1
    assert span["start_time"] == "1970-01-01T00:00:01.000000Z"
    assert span["end_time"] == "1970-01-01T00:00:02.000000Z"
    assert "gen_ai.tool.call.id" not in span["attributes"]
    assert span["parent_id"] is None


def test_sdk_projection_identity_changes_with_snapshot():
    assert (
        standardize(records(), "snapshot-1")["projection_id"]
        != standardize(records(), "snapshot-2")["projection_id"]
    )
