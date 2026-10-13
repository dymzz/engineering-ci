"""Bounded query output, immutable bookmarks and explicit private-content access."""

import base64
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_trace_candidates import evidence, refreshed

from engineering_ci.cli import trace
from engineering_ci.mining.candidates import candidates
from engineering_ci.tracing.results import canonical
from engineering_ci.tracing.retrieval import (
    Query,
    analysis_query,
    encode,
    raw_query,
    summary_query,
)
from engineering_ci.tracing.store import ArchiveError


def selected_candidate(value):
    return candidates(value)["candidates"][0]["candidate_id"]


def test_levels_preserve_existing_candidate_identity_and_failure_facts(monkeypatch):
    import engineering_ci.mining.analysis as module

    monkeypatch.setattr(
        module, "discover", lambda *_: pytest.fail("query must not mine")
    )
    value = evidence()
    summary = analysis_query(value, Query("summary"))
    pattern = analysis_query(value, Query("pattern"))
    assert [item["candidate_id"] for item in pattern["items"]] == [
        item["candidate_id"] for item in candidates(value)["candidates"]
    ]
    assert summary["statistics"]["selected_outcomes"] == {
        "failed": 2,
        "passed": 6,
        "unknown": 1,
    }
    assert summary["statistics"]["excluded_cases"] == 2
    assert summary["completeness"] == "unknown"
    repeat = pattern["items"][0]
    assert repeat["support"] == {"cases": 3, "selected_cases": 3, "ratio": 1.0}
    assert repeat["occurrence_member_outcomes"]["failed"] == 2
    detail = analysis_query(
        value, Query("candidate", candidate=repeat["candidate_id"])
    )["items"][0]
    assert detail["parameters"]["status"] == "unavailable_in_activity_projection"
    assert detail["tool_coverage"] == {"status": "unmapped", "handler": None}
    assert detail["validation"]["quality_gate"] == "not_evaluated"
    assert not detail["validation"]["optimization_verified"]


def test_pattern_pages_are_complete_and_budget_can_change_between_pages():
    value = evidence()
    first = analysis_query(value, Query("pattern", limit=1))
    second = analysis_query(
        value,
        Query("pattern", limit=2, after=first["page"]["next_after"], max_bytes=10000),
    )
    assert len(first["items"]) == 1 and len(second["items"]) == 2
    assert second["page"]["next_after"] is None
    assert first["page"]["remaining_items"] == 2
    assert {item["candidate_id"] for item in first["items"] + second["items"]} == {
        item["candidate_id"] for item in candidates(value)["candidates"]
    }


@pytest.mark.parametrize(
    "change",
    [
        {"level": "summary"},
        {"activity": "command_execution"},
        {"kind": "repeated_variant"},
        {"min_cases": 2},
        {"outcome": "failed"},
    ],
)
def test_cursor_cannot_cross_levels_or_filters(change):
    value = evidence()
    query = Query("pattern", limit=1)
    first = analysis_query(value, query)
    with pytest.raises(ArchiveError, match="Continuation token"):
        analysis_query(
            value, replace(query, after=first["page"]["next_after"], **change)
        )


def test_cursor_cannot_cross_changed_source_content():
    value = evidence()
    first = analysis_query(value, Query("pattern", limit=1))
    value.projection["diagnostics"]["invalid_records"] = 1
    refreshed(value)
    with pytest.raises(ArchiveError, match="Continuation token"):
        analysis_query(value, Query("pattern", after=first["page"]["next_after"]))


@pytest.mark.parametrize(
    "after",
    [
        "not-base64!",
        base64.b64encode(b"[]").decode(),
        base64.b64encode(b'{"query":"other","key":true}').decode(),
    ],
)
def test_malformed_cursor_is_safe(after):
    with pytest.raises(ArchiveError, match="Continuation token"):
        analysis_query(evidence(), Query("pattern", after=after))


def test_filters_and_evidence_pages_preserve_relevant_outcomes():
    value = evidence()
    identifier = selected_candidate(value)
    first = analysis_query(
        value, Query("evidence", candidate=identifier, limit=1, outcome="failed")
    )
    second = analysis_query(
        value,
        Query(
            "evidence",
            candidate=identifier,
            limit=1,
            outcome="failed",
            after=first["page"]["next_after"],
        ),
    )
    assert [item["turn_id"] for item in first["items"] + second["items"]] == [
        "t1",
        "t2",
    ]
    assert second["page"]["next_after"] is None
    cancelled = analysis_query(
        value, Query("evidence", candidate=identifier, outcome="unknown")
    )
    assert cancelled["items"][0]["boundary"] == "cancelled"
    assert (
        cancelled["verification"] == "projected_member_references; raw_payload_not_read"
    )
    filtered = analysis_query(
        value, Query("pattern", min_cases=3, kind="repeated_variant")
    )
    assert filtered["items"] == []


def test_output_budget_returns_whole_items_with_continuation():
    value = evidence()
    full = analysis_query(value, Query("pattern"))
    # Find a budget allowing some, but not all, complete items including their bookmark.
    for budget in range(1024, len(encode(full)), 100):
        page = analysis_query(value, Query("pattern", max_bytes=budget))
        assert len(encode(page)) <= budget
        assert json.loads(encode(page)) == page
        if page["status"] == "returned" and page["budget"]["limited"]:
            assert page["items"] and page["page"]["next_after"]
            rest = analysis_query(
                value, Query("pattern", after=page["page"]["next_after"])
            )
            assert page["items"] + rest["items"] == full["items"]
            break
    else:
        pytest.fail("No complete-item budget page found")


def test_empty_or_oversized_first_item_never_claims_success():
    result = analysis_query(
        evidence(),
        Query("candidate", candidate=selected_candidate(evidence()), max_bytes=1024),
    )
    assert result["status"] == "budget_exceeded" and result["exit_code"] == 3
    assert len(encode(result)) <= 1024 and result["items"] == []


def test_unverified_or_inconsistent_evidence_is_rejected():
    value = evidence()
    with pytest.raises(ArchiveError, match="association"):
        analysis_query(replace(value, association_verified=False), Query("summary"))
    value.analysis["variants"][0]["cases"] = 999
    with pytest.raises(ArchiveError, match="model or measurements"):
        analysis_query(value, Query("summary"))


def test_secret_extra_fields_are_not_returned():
    value = evidence()
    value.projection.update(
        prompt="PRIVATE_PROMPT", tool_output="PRIVATE_OUTPUT", diff="PRIVATE_DIFF"
    )
    refreshed(value)
    for level in ("summary", "pattern", "candidate", "evidence"):
        query = Query(
            level,
            candidate=selected_candidate(value)
            if level in {"candidate", "evidence"}
            else None,
        )
        output = encode(analysis_query(value, query))
        assert b"PRIVATE" not in output


@pytest.mark.parametrize(
    "query",
    [
        Query("raw"),
        Query("pattern", max_bytes=1),
        Query("pattern", limit=True),
        Query("pattern", activity="SECRET"),
        Query("candidate"),
        Query("summary", candidate="a" * 64),
        Query("pattern", min_cases=0),
        Query("pattern", allow_raw=True),
    ],
)
def test_bad_request_rejected_before_database(query):
    with pytest.raises(ArchiveError):
        query.validate()


def test_raw_budget_does_not_read_unadmitted_payloads():
    calls = []
    items = [
        {
            "position": index,
            "event_id": str(index),
            "offset": index,
            "bytes": size,
            "sha256": "a" * 64,
            "fragment": False,
        }
        for index, size in enumerate((3, 1000000))
    ]
    catalog = SimpleNamespace(
        raw_members=lambda *a, **kw: {
            "items": items,
            "has_more": False,
            "snapshot": {"completeness": "unknown", "records": 2},
        },
        raw_payload=lambda _, item: calls.append(item["position"]) or "YWJj",
    )
    result = raw_query(catalog, Query("raw", allow_raw=True, max_bytes=2048), "a" * 64)
    assert calls == [0] and len(encode(result)) <= 2048
    assert result["page"]["next_after"] and result["budget"]["limited"]
    assert base64.b64decode(result["items"][0]["payload_base64"]) == b"abc"
    calls.clear()
    catalog.raw_members = lambda *a, **kw: {
        "items": items[1:],
        "has_more": False,
        "snapshot": {"completeness": "unknown", "records": 2},
    }
    blocked = raw_query(
        catalog,
        Query(
            "raw", allow_raw=True, max_bytes=2048, after=result["page"]["next_after"]
        ),
        "a" * 64,
    )
    assert (
        not calls
        and blocked["status"] == "budget_exceeded"
        and blocked["blocked_reference"] == 1
    )


def test_raw_requires_acknowledgement_without_catalog_access():
    with pytest.raises(ArchiveError) as error:
        raw_query(None, Query("raw"), "a" * 64)
    assert error.value.status == "authorization_required"


def test_summary_metadata_never_reads_documents_and_filters_snapshot():
    requests = []
    item = {
        name: "a" * 64
        for name in (
            "result_id",
            "snapshot_id",
            "parent_result_id",
            "input_digest",
            "document_digest",
        )
    }
    item.update(
        origin="file",
        prompt="PRIVATE",
        summary={
            "status": "passed",
            "engine": "rust4pm",
            "completeness": "unknown",
            "selected_cases": 3,
            "excluded_cases": 2,
            "variants": 2,
            "dfg_edges": 3,
            "tool_output": "PRIVATE",
        },
    )
    catalog = SimpleNamespace(
        results=lambda **kw: (
            requests.append(kw) or {"items": [item], "next_after": None}
        )
    )
    result = summary_query(catalog, Query("summary"), snapshot="b" * 64)
    assert requests[0]["snapshot"] == "b" * 64 and requests[0]["kind"] == "analysis"
    assert b"PRIVATE" not in encode(result)
    assert result["items"][0]["origin"] == "file"
    assert "documents_and_raw_payload_not_read" in result["verification"]


def test_cli_file_mode_returns_budgeted_json_without_database(
    tmp_path, monkeypatch, capsys
):
    import engineering_ci.tracing.store as module

    value = evidence()
    analysis = tmp_path / "analysis.json"
    projection = tmp_path / "projection.json"
    analysis.write_bytes(canonical(value.analysis))
    projection.write_bytes(canonical(value.projection))
    monkeypatch.setattr(
        module, "open_store", lambda *_: pytest.fail("file query opened database")
    )
    assert (
        trace.main(
            [
                "query",
                "pattern",
                "--analysis",
                str(analysis),
                "--projection",
                str(projection),
                "--limit",
                "1",
                "--max-bytes",
                "4096",
                "--json",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    result = json.loads(output)
    assert len(output.encode()) <= 4096 and result["page"]["next_after"]


def test_cli_bad_request_and_raw_ack_are_rejected_before_io(monkeypatch, capsys):
    import engineering_ci.tracing.store as module

    monkeypatch.setattr(
        module, "open_store", lambda *_: pytest.fail("invalid query opened database")
    )
    for args, status in (
        (["raw", "--snapshot", "a" * 64], "authorization_required"),
        (["pattern"], "invalid_query"),
        (["summary", "--max-bytes", "0"], "invalid_query"),
    ):
        assert trace.main(["query", *args]) == 2
        assert json.loads(capsys.readouterr().out)["status"] == status


def test_unavailable_analysis_is_visible_even_when_empty_query_completes():
    from test_trace_results import analysis, projection

    from engineering_ci.mining.comparison import AnalysisEvidence

    parent = projection()
    document = analysis(parent)
    document.update(
        start_activities={}, end_activities={}, activity_counts={}, outcome_counts={}
    )
    value = analysis_query(
        AnalysisEvidence(document, parent, "file", True), Query("summary")
    )
    assert value["status"] == "returned" and value["analysis_status"] == "unavailable"
    assert value["items"] == [] and value["statistics"]["selected_cases"] == 0


def test_pattern_pagination_is_not_capped_by_old_report_limit():
    from collections import Counter

    from test_trace_results import analysis, projection

    from engineering_ci.mining.comparison import AnalysisEvidence

    parent = projection()
    cases, variants, edges = [], Counter(), Counter()
    for length in range(1, 106):
        activities = ["command_execution"] * length
        variants[tuple(activities)] = 2
        edges.update({("command_execution", "command_execution"): 2 * (length - 1)})
        for repeat in range(2):
            turn = f"t{length}_{repeat}"
            parent["turns"][turn] = {"started": True, "terminal": "completed"}
            members = []
            for index in range(length):
                event = str(len(parent["operations"]))
                parent["operations"].append(
                    {
                        "turn_id": turn,
                        "event_id": event,
                        "activity": activities[index],
                        "start_ns": index * 100,
                        "end_ns": index * 100 + 50,
                        "outcome": "passed",
                    }
                )
                members.append(event)
            cases.append(
                {"turn_id": turn, "boundary": "completed", "event_ids": members}
            )
    # Case selection sorts by opaque turn ID; reflect the real selection order.
    cases.sort(key=lambda item: item["turn_id"])
    value = analysis(parent)
    total = len(parent["operations"])
    value.update(
        status="passed",
        selected_cases=cases,
        variants=[
            {"activities": list(seq), "cases": count}
            for seq, count in sorted(variants.items())
        ],
        dfg=[
            {"from": left, "to": right, "count": count}
            for (left, right), count in sorted(edges.items())
            if count
        ],
        start_activities={"command_execution": 210},
        end_activities={"command_execution": 210},
        activity_counts={"command_execution": total},
        outcome_counts={"passed": total},
    )
    reference = AnalysisEvidence(value, parent, "file", True)
    first = analysis_query(
        reference,
        Query("pattern", kind="repeated_variant", limit=100, max_bytes=1048576),
    )
    second = analysis_query(
        reference,
        Query(
            "pattern",
            kind="repeated_variant",
            limit=100,
            max_bytes=1048576,
            after=first["page"]["next_after"],
        ),
    )
    assert len(first["items"]) == 100 and len(second["items"]) == 5
    assert second["page"]["next_after"] is None
    assert (
        len({item["candidate_id"] for item in first["items"] + second["items"]}) == 105
    )
