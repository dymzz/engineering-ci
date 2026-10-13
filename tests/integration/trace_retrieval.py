"""Explicit host PostgreSQL query acceptance using only synthetic private payloads."""

import base64
import json

import pytest
import trace_candidates
import trace_store

from engineering_ci.cli import trace
from engineering_ci.cli.mine_inputs import registered_evidence
from engineering_ci.mining.analysis import analyze
from engineering_ci.tracing.catalog import TraceCatalog
from engineering_ci.tracing.projection import standardize
from engineering_ci.tracing.results import canonical, digest, prepare_result
from engineering_ci.tracing.retrieval import (
    Query,
    analysis_query,
    encode,
    raw_query,
    summary_query,
)
from engineering_ci.tracing.store import ArchiveError, import_source

archive = trace_store.archive


def test_actual_raw_pages_preserve_bytes_and_member_identity(archive, tmp_path):
    source = trace_store.rollout(
        tmp_path / "synthetic.jsonl", b' {"private":"SECRET"}\r\n' + b"\xff incomplete"
    )
    snapshot = import_source(archive, source)["snapshot_id"]
    catalog = TraceCatalog(archive)
    after, chunks = None, []
    while True:
        result = raw_query(
            catalog,
            Query("raw", limit=1, max_bytes=2048, after=after, allow_raw=True),
            snapshot,
        )
        assert len(encode(result)) <= 2048 and result["status"] == "returned"
        for item in result["items"]:
            raw = base64.b64decode(item["payload_base64"])
            assert digest(raw) == item["sha256"]
            chunks.append(raw)
        after = result["page"]["next_after"]
        if after is None:
            break
    assert b"".join(chunks) == source.path.read_bytes()
    assert result["items"][0]["fragment"] is True
    assert (
        result["verification"] == "selected_member_digests; full_snapshot_not_verified"
    )


def test_actual_raw_corruption_never_returns_a_success_page(archive, tmp_path):
    source = trace_store.rollout(tmp_path / "synthetic.jsonl")
    snapshot = import_source(archive, source)["snapshot_id"]
    with archive.transaction() as cursor:
        cursor.execute(
            archive.query(
                "UPDATE {schema}.events SET payload = %s WHERE source = %s AND record_offset = 0"
            ),
            (b"CORRUPTED", source.identity.source),
        )
    with pytest.raises(ArchiveError, match="digest or identity"):
        raw_query(TraceCatalog(archive), Query("raw", allow_raw=True), snapshot)


def test_actual_raw_cursor_refuses_another_snapshot_and_invalid_position(
    archive, tmp_path
):
    one = import_source(archive, trace_store.rollout(tmp_path / "one.jsonl"))[
        "snapshot_id"
    ]
    two = import_source(archive, trace_store.rollout(tmp_path / "two.jsonl"))[
        "snapshot_id"
    ]
    catalog = TraceCatalog(archive)
    first = raw_query(catalog, Query("raw", limit=1, allow_raw=True), one)
    with pytest.raises(ArchiveError, match="Continuation token"):
        raw_query(
            catalog,
            Query("raw", after=first["page"]["next_after"], allow_raw=True),
            two,
        )
    with pytest.raises(ArchiveError, match="outside this snapshot"):
        catalog.raw_members(one, after=999)


def test_actual_registered_queries_do_not_recompute_or_write(
    archive, tmp_path, monkeypatch, capsys
):
    import engineering_ci.mining.analysis as module

    source = trace_store.rollout(
        tmp_path / "synthetic.jsonl", b"".join(trace_candidates.native()[1:])
    )
    captured = import_source(archive, source)
    projection = standardize(
        archive.replay(captured["snapshot_id"]), captured["snapshot_id"]
    )
    analysis = analyze(projection, digest(canonical(projection)))
    catalog = TraceCatalog(archive)
    catalog.save(prepare_result(projection, captured["capture_digest"]))
    record = prepare_result(analysis, analysis["input_sha256"])
    catalog.save(record, projection_input=projection)
    evidence = registered_evidence(catalog, record.result_id)
    monkeypatch.setattr(
        module, "discover", lambda *_: pytest.fail("query repeated Rust4PM")
    )
    before = catalog.results()
    summary = summary_query(catalog, Query("summary"), snapshot=captured["snapshot_id"])
    assert summary["items"][0]["statistics"]["selected_cases"] == 2
    pattern = analysis_query(evidence, Query("pattern", limit=1))
    candidate = pattern["items"][0]["candidate_id"]
    for level in ("candidate", "evidence"):
        result = analysis_query(evidence, Query(level, candidate=candidate))
        assert b"SECRET" not in encode(result) and b"PRIVATE" not in encode(result)
        assert result["origin"] == "computed" and not result["optimization_verified"]
    config = tmp_path / "tracing.toml"
    config.write_text(
        f'schema_version = 1\n[archive]\nschema = "{archive.config.schema}"\n',
        encoding="utf-8",
    )
    assert (
        trace.main(
            [
                "query",
                "evidence",
                "--analysis-result",
                record.result_id,
                "--candidate",
                candidate,
                "--limit",
                "1",
                "--config",
                str(config),
                "--max-bytes",
                "4096",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert len(output.encode()) <= 4096 and json.loads(output)["items"]
    assert before == catalog.results()
    assert catalog.document(record.result_id) == record.document
