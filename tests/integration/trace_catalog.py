"""Explicit PostgreSQL + OTel + Rust4PM evidence-chain acceptance."""

import json

import pytest
import trace_store

from engineering_ci.cli import mine, trace
from engineering_ci.mining.analysis import analyze
from engineering_ci.tracing.catalog import TraceCatalog
from engineering_ci.tracing.projection import standardize
from engineering_ci.tracing.results import canonical, digest, prepare_result
from engineering_ci.tracing.store import ArchiveError, import_source, migration_digest

archive = trace_store.archive
rollout = trace_store.rollout


def projected(archive, tmp_path):
    source = rollout(tmp_path / "source.jsonl")
    captured = import_source(archive, source)
    value = standardize(
        archive.replay(captured["snapshot_id"]), captured["snapshot_id"]
    )
    return source, captured, value


def test_actual_empty_analysis_is_registered_as_unavailable_with_complete_lineage(
    archive, tmp_path
):
    source, captured, projection = projected(archive, tmp_path)
    catalog = TraceCatalog(archive)
    record = prepare_result(projection, captured["capture_digest"])
    catalog.save(record)
    catalog.save(record)  # Idempotent, no duplicate computation.
    result = analyze(projection, digest(canonical(projection)))
    analyzed = prepare_result(result, result["input_sha256"])
    catalog.save(analyzed, projection_input=projection)
    linked = catalog.lineage(analyzed.result_id)
    assert linked["source"]["source"] == source.identity.source
    assert linked["projection"]["result_id"] == record.result_id
    assert linked["analysis"]["summary"]["status"] == "unavailable"
    assert linked["analysis"]["summary"]["engine"] == "rust4pm"
    assert json.loads(catalog.document(analyzed.result_id)) == result
    first = catalog.results(limit=1)
    second = catalog.results(limit=1, after=first["next_after"])
    assert len(first["items"]) == len(second["items"]) == 1
    assert first["items"][0]["result_id"] != second["items"][0]["result_id"]


def test_missing_parent_and_changed_projection_refuse_analysis(archive, tmp_path):
    _, captured, projection = projected(archive, tmp_path)
    catalog = TraceCatalog(archive)
    result = analyze(projection, digest(canonical(projection)))
    record = prepare_result(result, result["input_sha256"])
    with pytest.raises(ArchiveError, match="Register the input projection"):
        catalog.save(record, projection_input=projection)
    catalog.save(prepare_result(projection, captured["capture_digest"]))
    changed = {**projection, "cost": 999}
    with pytest.raises(ArchiveError, match="differs"):
        catalog.save(record, projection_input=changed)
    assert len(catalog.results()["items"]) == 1


def test_same_identity_different_content_and_document_corruption_are_rejected(
    archive, tmp_path
):
    _, captured, projection = projected(archive, tmp_path)
    catalog = TraceCatalog(archive)
    record = prepare_result(projection, captured["capture_digest"])
    catalog.save(record)
    with pytest.raises(ArchiveError, match="different content"):
        catalog.save(
            prepare_result({**projection, "cost": 5}, captured["capture_digest"])
        )
    with archive.transaction() as cursor:
        cursor.execute(
            archive.query(
                "UPDATE {schema}.derived_results SET document = %s WHERE result_id = %s"
            ),
            (b"corrupt", record.result_id),
        )
    with pytest.raises(ArchiveError, match="digest"):
        catalog.document(record.result_id)


def test_metadata_queries_keep_current_cursor_separate_from_immutable_snapshot(
    archive, tmp_path
):
    source, captured, _ = projected(archive, tmp_path)
    with source.path.open("ab") as stream:
        stream.write(b"{}\n")
    import_source(archive, type(source)(source.path))
    catalog = TraceCatalog(archive)
    assert (
        catalog.source(source.identity.source)["cursor"]["offset"]
        > captured["observed_bytes"]
    )
    assert (
        catalog.snapshot(captured["snapshot_id"])["observed_bytes"]
        == captured["observed_bytes"]
    )
    assert (
        catalog.sources(limit=1)["items"][0]["recovery_validation"]
        == "file_prefix_not_checked"
    )
    first = catalog.snapshots(source=source.identity.source, limit=1)
    second = catalog.snapshots(
        source=source.identity.source, limit=1, after=first["next_after"]
    )
    assert second["items"][0]["snapshot_id"] == captured["snapshot_id"]
    with pytest.raises(ArchiveError, match="outside this query"):
        catalog.snapshots(source="other", after=first["next_after"])


def test_v1_queries_work_and_catalog_migration_is_explicit_preserving_raw_bytes(
    archive, tmp_path
):
    _, captured, _ = projected(archive, tmp_path)
    original = b"".join(item.raw for item in archive.replay(captured["snapshot_id"]))
    # Simulate the actual previous schema in this unique component-owned namespace.
    with archive.transaction() as cursor:
        cursor.execute(archive.query("DROP TABLE {schema}.derived_results"))
        cursor.execute(
            archive.query(
                "UPDATE {schema}.archive_meta SET schema_version = 1, migration_digest = %s"
            ),
            (migration_digest(1),),
        )
    catalog = TraceCatalog(archive)
    assert catalog.sources()["items"] and archive.schema_version() == 1
    with pytest.raises(ArchiveError) as error:
        catalog.results()
    assert error.value.status == "migration_required" and archive.schema_version() == 1
    assert not archive.initialize() and archive.schema_version() == 2
    assert (
        b"".join(item.raw for item in archive.replay(captured["snapshot_id"]))
        == original
    )


def config_file(archive, tmp_path):
    path = tmp_path / "archive.toml"
    path.write_text(
        f'schema_version = 1\n[archive]\nschema = "{archive.config.schema}"\n',
        encoding="utf-8",
    )
    return ["--config", str(path), "--json"]


def test_cli_failed_export_retains_registered_result_and_resume_command(
    archive, tmp_path, capsys
):
    _, captured, _ = projected(archive, tmp_path)
    flags = config_file(archive, tmp_path)
    output = tmp_path / "existing.json"
    output.write_bytes(b"unchanged")
    assert (
        trace.main(
            [
                "project",
                captured["snapshot_id"],
                "--record",
                "--out",
                str(output),
                *flags,
            ]
        )
        == 2
    )
    value = json.loads(capsys.readouterr().out)
    identifier = value["registered_result"]["result_id"]
    assert (
        value["status"] == "output_exists" and value["next_actions"][0][2] == "export"
    )
    assert output.read_bytes() == b"unchanged"
    restored = tmp_path / "restored.json"
    assert (
        trace.main(["export", "--result", identifier, "--out", str(restored), *flags])
        == 0
    )
    capsys.readouterr()
    assert json.loads(restored.read_bytes())["projection_id"] == identifier
    assert (
        mine.main(
            [
                "--projection-result",
                identifier,
                "--record",
                "--out",
                str(tmp_path / "analysis.json"),
                *flags,
            ]
        )
        == 3
    )
    result = json.loads(capsys.readouterr().out)
    assert result["association"] == "registered" and result["status"] == "unavailable"
    assert trace.main(["status", "--result", result["analysis_id"], *flags]) == 0
    linked = json.loads(capsys.readouterr().out)
    assert linked["analysis"]["summary"]["status"] == "unavailable"


def test_existing_files_register_without_projection_or_analysis_execution(
    archive, tmp_path, monkeypatch, capsys
):
    _, _, projection = projected(archive, tmp_path)
    flags = config_file(archive, tmp_path)
    path = tmp_path / "projection.json"
    path.write_bytes(canonical(projection))
    result = analyze(projection, digest(path.read_bytes()))
    output = tmp_path / "result.json"
    output.write_bytes(canonical(result))
    import engineering_ci.tracing.projection as module

    monkeypatch.setattr(module, "standardize", lambda *a: pytest.fail("reprojection"))
    monkeypatch.setattr(mine, "analyze", lambda *a, **kw: pytest.fail("reanalysis"))
    assert trace.main(["register", str(path), *flags]) == 0
    capsys.readouterr()
    assert trace.main(["register", str(output), "--input", str(path), *flags]) == 0
    capsys.readouterr()
    assert TraceCatalog(archive).result(result["analysis_id"])["origin"] == "file"


def test_committed_registration_with_simulated_lost_confirmation_is_queryable(
    archive, tmp_path, monkeypatch, capsys
):
    _, captured, _ = projected(archive, tmp_path)
    flags = config_file(archive, tmp_path)
    save = TraceCatalog.save

    def lost_confirmation(self, record, **kwargs):
        save(
            self, record, **kwargs
        )  # Actual PostgreSQL commit, simulated client error.
        raise ArchiveError("network_error", "Registration acknowledgement unavailable")

    monkeypatch.setattr(TraceCatalog, "save", lost_confirmation)
    output = tmp_path / "not-exported.json"
    assert (
        trace.main(
            [
                "project",
                captured["snapshot_id"],
                "--record",
                "--out",
                str(output),
                *flags,
            ]
        )
        == 2
    )
    value = json.loads(capsys.readouterr().out)
    assert not output.exists() and value["registration"]["association"] == "unconfirmed"
    assert value["next_actions"][0][2] == "status"
    assert (
        trace.main(["status", "--result", value["registration"]["result_id"], *flags])
        == 0
    )
    assert (
        json.loads(capsys.readouterr().out)["projection"]["summary"]["status"]
        == "generated"
    )
