"""Native file boundaries, resume identities and real SDK byte preservation."""

import base64
import hashlib
import json
from datetime import datetime

import pytest

from engineering_ci.cli.trace import main
from engineering_ci.tracing.envelope import attributes, structured
from engineering_ci.tracing.sources.codex_rollout import (
    Cursor,
    RolloutSource,
    SourceError,
    inspect_source,
    record_at,
)

SESSION = "01a11ef4-cdaa-7561-ab17-90c2cf23549e"
HEADER = (
    json.dumps(
        {
            "type": "session_meta",
            "timestamp": "2026-10-09T04:38:50.516Z",
            "payload": {"id": SESSION, "cli_version": "0.162.0-alpha.2"},
        }
    )
    + "\n"
).encode()


def source(tmp_path, content, **kwargs):
    path = tmp_path / "rollout.jsonl"
    path.write_bytes(HEADER + content)
    return RolloutSource(path, **kwargs)


def records(value, cursor=None):
    return [record for batch in value.batches(cursor) for record in batch.records]


def test_real_dialect_keeps_unknown_fields_whitespace_and_crlf(tmp_path):
    raw = b'{ "type": "future_event", "secret": "SECRET", "unicode":"\\u4e2d" }\r\n'
    value = source(tmp_path, raw, batch_size=1)
    captured = records(value)
    assert b"".join(item.raw for item in captured) == HEADER + raw
    assert captured[1].kind == "unknown" and captured[1].parse_status == "parsed"
    facts = inspect_source(value)
    assert facts["completeness"] == "unknown"
    assert "SECRET" not in json.dumps(facts) and "SECRET" not in repr(captured)
    assert facts["inspection"] == "head-tail-sample" and facts["sampled_records"] == 2
    assert len(list(value.batches())) == 2


def test_closed_turn_does_not_prove_stream_completeness(tmp_path):
    value = source(
        tmp_path, b'{"type":"event_msg","payload":{"type":"task_complete"}}\n'
    )
    assert inspect_source(value)["completeness"] == "unknown"


def test_fragment_is_preserved_without_consuming_resume_cursor(tmp_path):
    value = source(tmp_path, b'{"type":"event_msg"')
    batch = list(value.batches())[-1]
    assert batch.cursor.offset == len(HEADER)
    assert batch.cursor.digest == hashlib.sha256(HEADER).hexdigest()
    assert batch.records[-1].fragment
    assert inspect_source(value)["completeness"] == "incomplete"
    with value.path.open("ab") as stream:
        stream.write(b"}\n")
    resumed = RolloutSource(value.path)
    captured = records(resumed, batch.cursor)
    assert len(captured) == 1 and not captured[0].fragment
    assert captured[0].offset == len(HEADER)
    assert captured[0].event_id != batch.records[-1].event_id


def test_live_append_does_not_change_frozen_capture(tmp_path):
    value = source(tmp_path, b'{"type":"event_msg"}\n')
    expected = value.path.read_bytes()
    with value.path.open("ab") as stream:
        stream.write(b'{"type":"world_state"}\n')
    assert b"".join(item.raw for item in records(value)) == expected
    assert RolloutSource(value.path).identity == value.identity


@pytest.mark.parametrize(
    "content",
    [b'{"type":"event_msg"}\n', b"broken SECRET\n", b"[]\n", b'{"type":{}}\n'],
)
def test_restart_and_moved_copy_keep_ids(tmp_path, content):
    value = source(tmp_path, content, batch_size=1)
    batches = list(value.batches())
    moved = tmp_path / "moved.jsonl"
    moved.write_bytes(value.path.read_bytes())
    again = RolloutSource(moved)
    assert again.identity == value.identity
    assert records(again)[1].event_id == str(len(HEADER))
    assert not list(again.batches(batches[-1].cursor))[-1].records


@pytest.mark.parametrize("mutation", ["rewrite", "truncate", "header"])
def test_changed_file_rejects_resume_without_payload_diagnostic(tmp_path, mutation):
    value = source(tmp_path, b'"PRIVATE_CONTENT"\n')
    cursor = list(value.batches())[-1].cursor
    original = value.path.read_bytes()
    changed = (
        original.replace(b"PRIVATE_CONTENT", b"ALTERED_CONTENT")
        if mutation == "rewrite"
        else original[:-2]
        if mutation == "truncate"
        else original.replace(b"session_meta", b"session_wrong")
    )
    value.path.write_bytes(changed)
    with pytest.raises(SourceError) as error:
        list(value.batches(cursor))
    assert error.value.status == "source_changed" and "PRIVATE" not in str(error.value)


def test_malformed_record_is_not_discarded_or_false_pass(tmp_path):
    value = source(tmp_path, b"broken SECRET\n")
    assert records(value)[1].raw == b"broken SECRET\n"
    assert inspect_source(value)["sampled_invalid_records"] == 1
    assert inspect_source(value)["completeness"] == "incomplete"


def test_size_limit_does_not_truncate_record(tmp_path):
    value = source(tmp_path, b"x" * 600 + b"\n", max_record_bytes=512)
    with pytest.raises(SourceError) as error:
        list(value.batches())
    assert error.value.status == "payload_limit"


def test_byte_cursor_boundary_is_validated(tmp_path):
    value = source(tmp_path, b"{}\n")
    cursor = Cursor(3, hashlib.sha256(HEADER[:3]).hexdigest())
    with pytest.raises(SourceError, match="boundary"):
        list(value.batches(cursor))


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"{}\n",
        b'{"type":"thread.started"}\n',
        b'{"type":"session_meta","payload":{"id":"SECRET"}}\n',
    ],
)
def test_cli_json_and_incomplete_headers_are_distinct_sources(tmp_path, data):
    path = tmp_path / "invalid.jsonl"
    path.write_bytes(data)
    with pytest.raises(SourceError) as error:
        RolloutSource(path)
    assert error.value.status == "unsupported_source" and "SECRET" not in str(
        error.value
    )


def test_sdk_roundtrip_is_byte_exact_and_has_no_invented_time(tmp_path):
    from cloudevents.core.formats.json import JSONFormat

    value = source(tmp_path, b'  {"type":"event_msg", "private":"SECRET"}  \r\n')
    record = records(value)[1]
    result = structured(value.identity, record)
    decoded = json.loads(result)
    assert "time" not in decoded and "data" not in decoded
    assert base64.b64decode(decoded["data_base64"]) == record.raw
    event = JSONFormat().read(None, result)
    assert event.get_data() == record.raw
    attrs = attributes(value.identity, record)
    assert "SECRET" not in json.dumps(attrs)
    header = records(value)[0]
    assert datetime.fromisoformat(
        json.loads(structured(value.identity, header))["time"]
    ) == datetime.fromisoformat("2026-10-09T04:38:50.516Z")


def test_inspect_cli_does_not_require_db_or_expose_content(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.delenv("VESRYN_DATABASE", raising=False)
    value = source(tmp_path, b' {"private":"SECRET"}\n')
    assert main(["inspect", str(value.path), "--json"]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["operation"] == "trace.inspect"
    assert "SECRET" not in output


def test_bad_timestamp_is_opaque_not_replaced_with_capture_time():
    record = record_at(0, b'{"timestamp":"PRIVATE_TIME"}\n')
    assert record.timestamp is None


def test_format_inspection_never_scans_the_middle(tmp_path):
    value = source(tmp_path, b"{}\n" * 30000 + b"{}\n")
    value.path.write_bytes(
        HEADER + b"{}\n" * 15000 + b"INVALID SECRET\n" + b"{}\n" * 15000
    )
    value = RolloutSource(value.path)
    facts = inspect_source(value)
    assert facts["sampled_invalid_records"] == 0
    assert facts["sampled_records"] < 30001
    assert "SECRET" not in json.dumps(facts)
