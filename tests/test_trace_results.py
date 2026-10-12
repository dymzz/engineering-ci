"""Result identity and CLI boundaries without optional mining or a host database."""

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from engineering_ci.cli import mine, trace
from engineering_ci.mining.analysis import ANALYSIS_VERSION, analysis_identity
from engineering_ci.tracing.catalog import TraceCatalog, page_limit
from engineering_ci.tracing.projection import (
    ACTIVITIES,
    PROJECTION_VERSION,
    projection_identity,
)
from engineering_ci.tracing.results import canonical, digest, prepare_result
from engineering_ci.tracing.store import ArchiveError


def projection():
    value = {
        "schema_version": 1,
        "snapshot_id": "a" * 64,
        "projection_version": PROJECTION_VERSION,
        "mapping": ACTIVITIES,
        "dependencies": {
            "opentelemetry-sdk": "1.45.1",
            "opentelemetry-semantic-conventions": "0.66b1",
        },
        "format": "opentelemetry-sdk-span-json",
        "completeness": "unknown",
        "llm_used": False,
        "operations": [],
        "turns": {},
        "spans": [],
        "diagnostics": {"invalid_records": 0},
    }
    value["projection_id"] = digest(canonical(projection_identity(value)).rstrip(b"\n"))
    return value


def analysis(parent, *, legacy=False):
    value = {
        "schema_version": 1,
        "snapshot_id": parent["snapshot_id"],
        "projection_id": parent["projection_id"],
        "input_sha256": digest(canonical(parent)),
        "analysis_version": "observed-turn-variants/1" if legacy else ANALYSIS_VERSION,
        "dependencies": {"pm4py": "2.7.23.9", "pandas": "3.0.6", "numpy": "2.5.4"}
        if legacy
        else {"r4pm": "0.6.2"},
        "case_scope": "turn",
        "allow_incomplete": False,
        "status": "unavailable",
        "completeness": "unknown",
        "llm_used": False,
        "selected_cases": [],
        "excluded_cases": [],
        "variants": [],
        "dfg": [],
    }
    if not legacy:
        value["engine"] = "rust4pm"
    value["analysis_id"] = digest(canonical(analysis_identity(value)).rstrip(b"\n"))
    return value


@pytest.mark.parametrize("legacy", [False, True])
def test_rust_and_historical_pm4py_results_keep_their_own_identities(legacy):
    value = analysis(projection(), legacy=legacy)
    record = prepare_result(value, value["input_sha256"], origin="file")
    assert record.summary["engine"] == ("pm4py" if legacy else "rust4pm")
    assert record.summary["status"] == "unavailable" and record.origin == "file"
    value["dependencies"][next(iter(value["dependencies"]))] = "99.0"
    with pytest.raises(ArchiveError, match="Result ID"):
        prepare_result(value, value["input_sha256"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", True),
        ("snapshot_id", "bad"),
        ("llm_used", True),
        ("completeness", "passed"),
    ],
)
def test_invalid_result_is_not_registered(field, value):
    document = projection()
    document[field] = value
    with pytest.raises(ArchiveError):
        prepare_result(document, "b" * 64)


@pytest.mark.parametrize("field", ["diagnostics", "dependencies"])
def test_query_summary_does_not_admit_credentials(field):
    value = projection()
    value[field]["token"] = "SECRET"
    with pytest.raises(ArchiveError) as error:
        prepare_result(value, "b" * 64)
    assert "SECRET" not in str(error.value)


def test_projection_metadata_and_canonical_bytes_are_stable():
    value = projection()
    record = prepare_result(value, "b" * 64)
    assert record.summary["status"] == "generated"
    assert record.document_digest == digest(record.document)
    assert record == prepare_result(json.loads(json.dumps(value, indent=2)), "b" * 64)


def test_analysis_wrong_actual_input_is_rejected():
    value = analysis(projection())
    with pytest.raises(ArchiveError, match="input digest"):
        prepare_result(value, "c" * 64)


@pytest.mark.parametrize(
    "field,value",
    [
        ("completeness", {}),
        ("analysis_version", []),
        ("status", {}),
        ("excluded_cases", [{"reason": {}}]),
        ("engine", "SECRET"),
    ],
)
def test_bad_historical_metadata_is_safe_and_structured(field, value):
    document = analysis(projection(), legacy=True)
    document[field] = value
    with pytest.raises(ArchiveError) as error:
        prepare_result(document, document["input_sha256"])
    assert error.value.status == "invalid_result" and "SECRET" not in str(error.value)


@pytest.mark.parametrize("limit", [0, 101, True, "2"])
def test_query_limit_is_bounded(limit):
    with pytest.raises(ArchiveError):
        page_limit(limit)


def test_sources_are_readonly_metadata_with_page_cursor():
    calls = []
    cursor = SimpleNamespace(
        execute=lambda sql, args: calls.append((sql, args)),
        fetchall=lambda: [
            (name, "session", "codex-rollout", "0.162.0-alpha.2", 10, "b" * 64)
            for name in ("one", "two")
        ],
    )

    @contextmanager
    def transaction(*, readonly):
        assert readonly
        yield cursor

    store = SimpleNamespace(
        transaction=transaction, _owned=lambda _: None, query=lambda sql: sql
    )
    result = TraceCatalog(store).sources(limit=1)
    assert len(result["items"]) == 1 and result["next_after"] == "one"
    assert result["items"][0]["recovery_validation"] == "file_prefix_not_checked"
    assert "events" not in calls[0][0] and calls[0][1][-1] == 2


def test_query_status_does_not_initialize_or_import(monkeypatch, capsys):
    import engineering_ci.tracing.store as module

    @contextmanager
    def opened(*_):
        yield object()

    monkeypatch.setattr(module, "open_store", opened)
    monkeypatch.setattr(
        TraceCatalog,
        "source",
        lambda _, source: {"source": source, "cursor": {"offset": 9}},
    )
    monkeypatch.setattr(TraceCatalog, "snapshots", lambda *a, **kw: {"items": []})
    assert trace.main(["status", "--source", "chosen", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["source"]["cursor"]["offset"] == 9


def test_mine_file_mode_never_opens_database(tmp_path, monkeypatch, capsys):
    import engineering_ci.tracing.store as module

    path = tmp_path / "input.json"
    path.write_bytes(canonical(projection()))
    monkeypatch.setattr(
        module, "open_store", lambda *_: pytest.fail("implicit database access")
    )
    monkeypatch.setattr(mine, "analyze", lambda *a, **kw: analysis(projection()))
    assert mine.main([str(path), "--out", str(tmp_path / "out.json"), "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["association"] == "file_only"


def test_mine_requires_exactly_one_input():
    with pytest.raises(SystemExit) as error:
        mine.main(["a.json", "--projection-result", "a" * 64, "--out", "x"])
    assert error.value.code == 2


def test_new_query_and_export_missing_database_are_explicit(monkeypatch, capsys):
    monkeypatch.delenv("VESRYN_DATABASE", raising=False)
    for args in (
        ["sources"],
        ["results"],
        ["export", "--result", "a" * 64, "--out", "unused"],
    ):
        assert trace.main([*args, "--json"]) == 2
        assert json.loads(capsys.readouterr().out)["status"] == "missing_configuration"
