"""Contract regressions for #66-P0 pre-execution Source proof."""

from engineering_ci import execution


def _plan(path, name="whitespace"):
    return {
        "selection": {
            "check_scope": [{"name": name, "status": "selected"}],
        },
        "git": {
            "snapshot": "worktree",
            "changed_paths": [path],
        },
    }


def test_pre_execution_proof_binds_host_check_inputs_without_native_result(tmp_path):
    (tmp_path / "file.txt").write_text("first\n", encoding="utf-8")
    plan = _plan("file.txt")
    first = execution.build_pre_execution_source_proof(
        plan, "whitespace", root=tmp_path
    )
    assert first["phase"] == "pre_execution"
    assert first["execution_started"] is False
    assert first["status"] == "verified_candidate_inputs"
    source = first["source_ref"]
    assert source["scope_kind"] == "check_input_closure"
    assert source["binding_status"] == "content_digest"
    assert source["actual_execution_source"] == "planned_host_worktree"
    assert "file.txt" in source["verified_input_scope"]

    (tmp_path / "file.txt").write_text("changed\n", encoding="utf-8")
    second = execution.build_pre_execution_source_proof(
        plan, "whitespace", root=tmp_path
    )
    assert second["source_ref"]["content_digest"] != source["content_digest"]


def test_pre_execution_unknown_dynamic_tool_does_not_claim_trusted_execution(tmp_path):
    (tmp_path / "file.txt").write_text("source\n", encoding="utf-8")
    proof = execution.build_pre_execution_source_proof(
        _plan("file.txt", name="source"), "source", root=tmp_path
    )
    assert proof["status"] == "unverified"
    assert proof["source_ref"] is None
    assert proof["execution_started"] is False
