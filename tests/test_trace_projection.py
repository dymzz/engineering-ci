"""Authoritative native execution semantics, independent of an installed OTel SDK."""

import json

import pytest

from engineering_ci.tracing.projection import operations
from engineering_ci.tracing.sources.codex_rollout import record_at
from engineering_ci.tracing.store import ArchiveError


def item(kind, payload, offset=0):
    return record_at(
        offset, json.dumps({"type": kind, "payload": payload}).encode() + b"\n"
    )


def header(runtime="0.162.0-alpha.2"):
    return item("session_meta", {"id": "session-1", "cli_version": runtime})


def completed(**changes):
    payload = {
        "type": "item_completed",
        "turn_id": "turn-1",
        "started_at_ms": 1000,
        "completed_at_ms": 2000,
        "item": {
            "id": "item-1",
            "type": "CommandExecution",
            "exit_code": 0,
            "status": "completed",
            "command": ["PRIVATE"],
            "stdout": "SECRET",
        },
    }
    payload.update(changes)
    return item("event_msg", payload, 100)


def test_structured_nonzero_exit_wins_over_completed_and_green_text():
    value = completed(
        item={
            "id": "item-1",
            "type": "CommandExecution",
            "status": "completed",
            "exit_code": 1,
            "stdout": "tests passed",
        }
    )
    result = operations([header(), value])
    operation = result["operations"][0]
    assert operation.outcome == "failed" and operation.exit_code == 1
    assert "tests passed" not in repr(result)


def test_no_raw_content_or_unknown_exit_inferred_from_output():
    value = completed(
        item={
            "id": "item-1",
            "type": "CommandExecution",
            "stdout": "exit code 0 SECRET",
        }
    )
    result = operations([header(), value])
    assert result["operations"][0].outcome == "unknown"
    assert result["operations"][0].exit_code is None
    assert "SECRET" not in repr(result)


@pytest.mark.parametrize(
    "start,end", [(None, 100), (True, 100), (200, 100), ("100", 200)]
)
def test_missing_or_reversed_times_are_not_fabricated(start, end):
    result = operations([header(), completed(started_at_ms=start, completed_at_ms=end)])
    assert result["operations"][0].start_ns is None


def test_wrapper_calls_and_duplicate_delivery_do_not_double_count():
    wrapper = item(
        "response_item",
        {"type": "function_call", "call_id": "wrapper-1", "name": "exec"},
    )
    value = completed()
    result = operations([header(), wrapper, value, value])
    assert (
        len(result["operations"]) == 1 and result["diagnostics"]["duplicate_items"] == 1
    )


def test_conflicting_native_identity_is_not_guessed():
    with pytest.raises(ArchiveError, match="conflicting"):
        operations([header(), completed(), completed(completed_at_ms=3000)])


def test_unknown_dialect_archivable_but_not_projected():
    with pytest.raises(ArchiveError) as error:
        operations([header("99.0.0"), completed()])
    assert error.value.status == "unsupported_projection"


@pytest.mark.parametrize(
    "payload", [{"type": {}}, {"type": "item_completed", "item": {"type": []}}]
)
def test_unknown_non_string_event_types_remain_unmapped(payload):
    assert not operations([header(), item("event_msg", payload)])["operations"]


def test_cancelled_and_open_turn_boundaries_remain_distinct():
    records = [
        header(),
        item("event_msg", {"type": "task_started", "turn_id": "turn-1"}),
        completed(),
        item("event_msg", {"type": "turn_aborted", "turn_id": "turn-1"}),
        item("event_msg", {"type": "task_started", "turn_id": "turn-2"}),
    ]
    result = operations(records)
    assert result["turns"]["turn-1"]["terminal"] == "cancelled"
    assert result["turns"]["turn-2"]["terminal"] is None
