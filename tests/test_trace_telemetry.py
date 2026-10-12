"""Telemetry admission/configuration and failures without optional network dependencies."""

import json

import pytest
from test_trace_results import projection

from engineering_ci.cli import trace
from engineering_ci.tracing import telemetry
from engineering_ci.tracing.config import (
    TelemetryConfig,
    TraceConfigurationError,
    load_config,
)
from engineering_ci.tracing.results import canonical
from engineering_ci.tracing.store import ArchiveError


def projected_spans(count=3):
    value = projection()
    value["spans"] = [
        {
            "name": "execute_tool command_execution",
            "kind": "SpanKind.INTERNAL",
            "context": {"trace_id": "0x1", "span_id": hex(i + 1)},
            "start_time": "2026-10-09T04:39:25.000000Z",
            "end_time": "2026-10-09T04:39:25.010000Z",
            "parent_id": None,
            "events": [],
            "links": [],
            "resource": {
                "attributes": {
                    "service.name": "codex",
                    "service.version": "0.162.0-alpha.2",
                }
            },
            "status": {"status_code": "ERROR"},
            "attributes": {
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "command_execution",
                "gen_ai.conversation.id": "session-1",
                "engineering_ci.source.item_id": f"item-{i}",
                "engineering_ci.source.turn_id": "turn-1",
                "engineering_ci.archive.snapshot_id": value["snapshot_id"],
                "engineering_ci.archive.event_id": str(i),
                "engineering_ci.projection.version": value["projection_version"],
                "engineering_ci.operation.outcome": "failed",
                "process.exit.code": 1,
                "error.type": "process_exit_nonzero",
            },
        }
        for i in range(count)
    ]
    return value


@pytest.mark.parametrize(
    "url",
    [
        "file:///secret",
        "https://user:SECRET@server/v1/traces",
        "http://localhost/v1/traces?token=SECRET",
        "http://localhost:99999/v1/traces",
        "http://localhost/",
        "http://localhost/v1/traces#secret",
        "http://localhost/v1/ traces",
    ],
)
def test_endpoint_errors_do_not_expose_values(url):
    with pytest.raises(TraceConfigurationError) as error:
        TelemetryConfig(endpoint=url)
    assert "SECRET" not in str(error.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("timeout", True),
        ("timeout", 0),
        ("timeout", 301),
        ("batch_size", 0),
        ("batch_size", 1025),
    ],
)
def test_export_bounds(field, value):
    with pytest.raises(TraceConfigurationError):
        TelemetryConfig(**{field: value})


def test_optional_config_is_backwards_compatible_and_anchored(tmp_path):
    (tmp_path / "config").mkdir()
    path = tmp_path / "config/tracing.toml"
    path.write_text(
        'schema_version=1\n[archive]\nschema="test_trace"\n', encoding="utf-8"
    )
    assert load_config(root=tmp_path).telemetry == TelemetryConfig()
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            '[telemetry]\nendpoint="http://127.0.0.1:4318/v1/traces"\nbatch_size=2\n'
        )
    assert load_config(root=tmp_path).telemetry.batch_size == 2


@pytest.mark.parametrize("change", ["secret", "time", "context", "snapshot"])
def test_unsafe_spans_are_rejected_before_sdk_or_network(change):
    value = projected_spans()
    span = value["spans"][0]
    if change == "secret":
        span["attributes"]["prompt"] = "SECRET"
    elif change == "time":
        span["end_time"] = "unknown"
    elif change == "context":
        span["context"]["trace_id"] = "0x0"
    else:
        span["attributes"]["engineering_ci.archive.snapshot_id"] = "wrong"
    with pytest.raises(ArchiveError) as error:
        telemetry.span_fields(value)
    assert error.value.status == "invalid_projection" and "SECRET" not in str(
        error.value
    )


def test_exact_clock_and_original_failure_are_preserved():
    fields = telemetry.span_fields(projected_spans())
    assert fields[0]["end"] - fields[0]["start"] == 10_000_000
    assert fields[0]["failed"] and fields[0]["attributes"]["process.exit.code"] == 1


@pytest.mark.parametrize(
    "status,rejected",
    [
        ("accepted", 0),
        ("partial", 1),
        ("network_error", None),
        ("rejected", None),
        ("response_invalid", None),
    ],
)
def test_batched_delivery_stops_on_failure_and_never_claims_backend_verification(
    monkeypatch, status, rejected
):
    monkeypatch.setattr(telemetry, "restore_spans", lambda x: x)
    monkeypatch.setattr(telemetry, "version", lambda _: "1.45.1")
    batches = []

    def send(batch):
        batches.append(batch)
        return telemetry.ExportAck(
            status, 200 if rejected is not None else None, rejected, 1
        )

    settings = TelemetryConfig(endpoint="http://127.0.0.1:4318/v1/traces", batch_size=2)
    result = telemetry.deliver(projected_spans(), "0" * 64, settings, sender=send)
    assert (
        not result["backend_persistence_verified"]
        and result["completeness"] == "unknown"
    )
    assert len(batches) == (2 if status == "accepted" else 1)
    assert result["trace_ids"] == ["0" * 31 + "1"] and result["trace_count"] == 1
    assert result["acknowledged_spans"] == (
        3 if status == "accepted" else 1 if status == "partial" else 0
    )
    assert result["remaining_spans"] == (0 if status == "accepted" else 1)


def test_empty_projection_does_not_fabricate_export_success(monkeypatch):
    monkeypatch.setattr(telemetry, "restore_spans", lambda x: x)
    monkeypatch.setattr(telemetry, "version", lambda _: "1.45.1")
    result = telemetry.deliver(
        projection(),
        "0" * 64,
        TelemetryConfig(endpoint="http://localhost/v1/traces"),
        sender=lambda _: pytest.fail("sent empty spans"),
    )
    assert result["status"] == "unavailable"


def test_cli_rejects_existing_receipt_before_delivery(tmp_path, monkeypatch, capsys):
    from engineering_ci.cli import trace_send

    output = tmp_path / "receipt.json"
    output.write_bytes(b"old")
    monkeypatch.setattr(
        trace_send, "deliver", lambda *a: pytest.fail("network operation")
    )
    assert trace.main(["send", "unused.json", "--out", str(output), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "output_exists"


def test_failed_cli_saves_receiver_facts_and_usable_retry_path(
    tmp_path, monkeypatch, capsys
):
    from engineering_ci.cli import trace_send

    source, output = tmp_path / "projection.json", tmp_path / "receipt.json"
    source.write_bytes(canonical(projection()))
    monkeypatch.setattr(
        trace_send,
        "deliver",
        lambda *a: {
            "status": "network_error",
            "acknowledged_spans": 0,
            "backend_persistence_verified": False,
        },
    )
    assert (
        trace.main(
            [
                "send",
                str(source),
                "--out",
                str(output),
                "--endpoint",
                "http://localhost/v1/traces",
                "--json",
            ]
        )
        == 3
    )
    value = json.loads(capsys.readouterr().out)
    assert json.loads(output.read_bytes())["receipt"]["status"] == "network_error"
    args = value["next_actions"][0]
    assert (
        args[args.index("--out") + 1] != str(output)
        and not value["receipt"]["backend_persistence_verified"]
    )


def test_send_uses_cli_override_and_original_business_failure(
    tmp_path, monkeypatch, capsys
):
    from engineering_ci.cli import trace_send

    source = tmp_path / "projection.json"
    source.write_bytes(canonical(projected_spans(1)))

    def sent(value, digest, settings):
        assert (
            settings.endpoint == "http://localhost:9876/v1/traces"
            and settings.batch_size == 2
        )
        assert value["spans"][0]["attributes"]["process.exit.code"] == 1
        return {"status": "accepted", "backend_persistence_verified": False}

    monkeypatch.setattr(trace_send, "deliver", sent)
    assert (
        trace.main(
            [
                "send",
                str(source),
                "--out",
                str(tmp_path / "receipt.json"),
                "--endpoint",
                "http://localhost:9876/v1/traces",
                "--batch-size",
                "2",
                "--json",
            ]
        )
        == 0
    )
    assert not json.loads(capsys.readouterr().out)["next_actions"]
