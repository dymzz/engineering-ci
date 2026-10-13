"""Actual SDK/Rust4PM evidence and optional PostgreSQL candidate readback."""

import json

import trace_store

from engineering_ci.cli import mine
from engineering_ci.cli.mine_inputs import registered_evidence
from engineering_ci.mining.analysis import analyze
from engineering_ci.mining.candidates import candidates
from engineering_ci.mining.comparison import AnalysisEvidence
from engineering_ci.tracing.catalog import TraceCatalog
from engineering_ci.tracing.projection import standardize
from engineering_ci.tracing.results import canonical, digest, prepare_result
from engineering_ci.tracing.sources.codex_rollout import record_at
from engineering_ci.tracing.store import import_source

archive = trace_store.archive


def native():
    """Source fixture has real protocol boundaries, distinct items and failed work."""
    values = [
        {
            "type": "session_meta",
            "payload": {"id": "session-1", "cli_version": "0.162.0-alpha.2"},
        }
    ]
    for turn in ("t1", "t2"):
        values.append(
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": turn}}
        )
        for i, kind in enumerate(
            ("CommandExecution", "CommandExecution", "FileChange")
        ):
            values.append(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "item_completed",
                        "turn_id": turn,
                        "started_at_ms": i * 1000,
                        "completed_at_ms": i * 1000 + 500,
                        "item": {
                            "type": kind,
                            "id": f"{turn}-{i}",
                            "status": "completed",
                            "exit_code": 1 if i == 0 else 0,
                            "command": ["PRIVATE"],
                            "stdout": "SECRET",
                        },
                    },
                }
            )
        values.append(
            {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": turn}}
        )
    return [json.dumps(value).encode() + b"\n" for value in values]


def test_actual_projection_and_mining_feed_review_only_candidates():
    import r4pm

    raw = native()
    projection = standardize(
        [record_at(i, line) for i, line in enumerate(raw)], digest(b"".join(raw))
    )
    analysis = analyze(projection, digest(canonical(projection)))
    before = r4pm.list_items()
    result = candidates(AnalysisEvidence(analysis, projection, "computed", True))
    assert result["status"] == "reported" and result["total_candidates"] == 3
    assert result["coverage"]["selected_outcome_counts"] == {"failed": 2, "passed": 4}
    assert not result["llm_used"] and not result["optimization_verified"]
    assert (
        r4pm.list_items() == before
    )  # Candidate generation does not invoke the Rust registry.
    assert "PRIVATE" not in json.dumps(result) and "SECRET" not in json.dumps(result)


def test_actual_registered_analysis_candidate_cli_is_readonly_and_recoverable(
    archive, tmp_path, capsys
):
    raw = native()
    source = trace_store.rollout(tmp_path / "source.jsonl", b"".join(raw[1:]))
    captured = import_source(archive, source)
    projection = standardize(
        archive.replay(captured["snapshot_id"]), captured["snapshot_id"]
    )
    catalog = TraceCatalog(archive)
    parent = prepare_result(projection, captured["capture_digest"])
    catalog.save(parent)
    analysis = analyze(projection, digest(canonical(projection)))
    record = prepare_result(analysis, analysis["input_sha256"])
    catalog.save(record, projection_input=projection)
    evidence = registered_evidence(catalog, record.result_id)
    assert candidates(evidence)["status"] == "reported"
    config = tmp_path / "tracing.toml"
    config.write_text(
        f'schema_version = 1\n[archive]\nschema = "{archive.config.schema}"\n',
        encoding="utf-8",
    )
    output = tmp_path / "report.json"
    args = [
        "candidates",
        "--analysis-result",
        record.result_id,
        "--config",
        str(config),
        "--out",
        str(output),
        "--json",
    ]
    assert mine.main(args) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["review_status"] == "not_reviewed" and value["total_candidates"] == 3
    stored = json.loads(output.read_bytes())
    assert stored["source"]["analysis_document_digest"] == record.document_digest
    assert catalog.document(record.result_id) == record.document
    assert catalog.document(parent.result_id) == parent.document
    assert (
        len(catalog.results()["items"]) == 2
    )  # No new database result kind/schema or writes.
    original = output.read_bytes()
    assert mine.main(args) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "output_exists"
    assert output.read_bytes() == original
    fresh = tmp_path / "fresh.json"
    args[args.index(str(output))] = str(fresh)
    assert mine.main(args) == 0
    assert json.loads(fresh.read_bytes()) == stored
