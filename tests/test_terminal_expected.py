"""#52-D4 Terminal Expected → post_completion binding contract tests."""

from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pytest

from engineering_ci import check_contract, expected_checks, selection
from engineering_ci import terminal_expected as te

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/weaver-cicd"
SCOPE_ID = "sha256:" + "1" * 64
RULE = check_contract.rule_ref("weaver", 1, digest="e" * 64)
SOURCE = check_contract.source_ref(
    "worktree",
    declared_target_tree="a" * 40,
    content_digest="c" * 64,
    verified_scope=["capture.json", "expected.json"],
    scope_kind="check_input_closure",
)


def make_terminal_expected(
    *,
    phase=check_contract.POST_COMPLETION_PHASE,
    run_id="controlled-42/1",
    run_attempt=1,
    **changes,
):
    definition = check_contract.definition_for("weaver_terminal")
    entry = {
        "check_id": definition.check_id,
        "definition_version": definition.definition_version,
        "rule_ref": copy.deepcopy(RULE),
        "source_ref": copy.deepcopy(SOURCE),
        "phase": phase,
        "scope_id": SCOPE_ID,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "name": "weaver_terminal",
        "event_ref": {
            "base_ref": "refs/heads/main",
            "head_ref": "refs/heads/topic",
            "base_commit": "1" * 40,
            "head_commit": "2" * 40,
            "references": [],
        },
        "selection_rule_ref": {
            "name": "engineering-ci.selection",
            "version": "1",
            "sha256": "a" * 64,
        },
        "profile_ref": {"name": "org/engineering", "version": "1"},
        "applicability": {"status": "selected", "reason": "test"},
        "input_refs": [{"kind": "otlp", "path": "capture.json"}],
        "baseline_ref": None,
        "obligation_refs": [{"obligation_ref": "o/terminal", "role": "required"}],
        "required_by_obligations": ["o/terminal"],
        "advisory_for_obligations": [],
    }
    entry.update(changes)
    entry["expected_check_ref"] = expected_checks.entry_identity(
        {key: value for key, value in entry.items() if key != "expected_check_ref"}
    )
    return entry


def accept(_context):
    return {"accepted": True, "status": "authorized"}


def reject(_context):
    return False


def make_plan(tmp_path):
    if not FIXTURE.is_dir():
        pytest.skip("weaver-cicd fixture unavailable")
    root = tmp_path / "src"
    shutil.copytree(FIXTURE, root)
    plan = selection.post_completion_plan(
        root=root,
        config_path=root / "ci-selection.toml",
        weaver_config="ci-weaver.toml",
        run_id="controlled-42/1",
        attempt=1,
        result="success",
    )
    gate = selection.validate_post_completion_binding(plan, root)
    assert gate["ok"]
    return root, plan


def test_authorized_terminal_expected_binds_post_completion_plan(tmp_path):
    root, plan = make_plan(tmp_path)
    expected = make_terminal_expected()
    binding = te.bind_terminal_expected(
        expected, plan=plan, root=root, expected_authority=accept
    )
    assert binding["status"] == "bound"
    assert binding["authorized"] is True
    assert binding["input_closure"] == "verified"
    assert binding["scope_id"] == SCOPE_ID
    assert binding["expected_check_ref"] == expected["expected_check_ref"]
    assert binding["plan_identity"]["run_id"] == "controlled-42/1"
    assert binding["plan_identity"]["closure_sha256"] == plan["post_completion"]["sha256"]
    validated = te.validate_terminal_expected_binding(copy.deepcopy(binding))
    assert validated["binding_ref"] == binding["binding_ref"]


def test_missing_root_marks_input_closure_unverified(tmp_path):
    _root, plan = make_plan(tmp_path)
    binding = te.bind_terminal_expected(
        make_terminal_expected(), plan=plan, expected_authority=accept
    )
    assert binding["authorized"] is False
    assert binding["input_closure"] == "unverified"
    assert "terminal_input_closure_root_missing" in binding["reasons"]


def test_drifted_plan_inputs_fail_closure_validation(tmp_path):
    root, plan = make_plan(tmp_path)
    (root / "advice-policies/cicd-error-type.rego").write_text(
        (root / "advice-policies/cicd-error-type.rego").read_text(encoding="utf-8")
        + "\n# drift\n",
        encoding="utf-8",
    )
    binding = te.bind_terminal_expected(
        make_terminal_expected(), plan=plan, root=root, expected_authority=accept
    )
    assert binding["authorized"] is False
    assert binding["input_closure"] == "failed"
    assert binding["input_closure_gate"]["reason"] == (
        "post_completion_closure_digest_mismatch"
    )


def test_missing_authority_cannot_authorize_terminal(tmp_path):
    root, plan = make_plan(tmp_path)
    binding = te.bind_terminal_expected(
        make_terminal_expected(), plan=plan, root=root
    )
    assert binding["authorized"] is False
    assert binding["status"] == "unverified"
    assert "terminal_expected_authority_missing" in binding["reasons"]


def test_rejected_authority_is_not_authorized(tmp_path):
    root, plan = make_plan(tmp_path)
    binding = te.bind_terminal_expected(
        make_terminal_expected(),
        plan=plan,
        root=root,
        expected_authority=reject,
    )
    assert binding["authorized"] is False
    assert binding["status"] == "rejected"


def test_wrong_phase_is_rejected(tmp_path):
    root, plan = make_plan(tmp_path)
    with pytest.raises(
        te.TerminalExpectedError,
        match="terminal_expected_phase_invalid|terminal_expected_instance_invalid",
    ):
        te.bind_terminal_expected(
            make_terminal_expected(phase=check_contract.EXECUTION_PHASE),
            plan=plan,
            root=root,
            expected_authority=accept,
        )


def test_wrong_attempt_is_rejected(tmp_path):
    root, plan = make_plan(tmp_path)
    binding = te.bind_terminal_expected(
        make_terminal_expected(run_attempt=9),
        plan=plan,
        root=root,
        expected_authority=accept,
    )
    assert binding["authorized"] is False
    assert "terminal_expected_attempt_mismatch" in binding["reasons"]


def test_wrong_check_id_is_rejected(tmp_path):
    root, plan = make_plan(tmp_path)
    expected = make_terminal_expected()
    expected["check_id"] = "eci/weaver/live"
    expected["expected_check_ref"] = expected_checks.entry_identity(
        {key: value for key, value in expected.items() if key != "expected_check_ref"}
    )
    with pytest.raises(te.TerminalExpectedError, match="terminal_expected_"):
        te.bind_terminal_expected(
            expected, plan=plan, root=root, expected_authority=accept
        )


def test_missing_scope_id_is_rejected(tmp_path):
    root, plan = make_plan(tmp_path)
    expected = make_terminal_expected()
    del expected["scope_id"]
    with pytest.raises(
        te.TerminalExpectedError, match="terminal_expected_scope_id_missing"
    ):
        te.bind_terminal_expected(
            expected, plan=plan, root=root, expected_authority=accept
        )


def test_contract_sample_cannot_authorize(tmp_path):
    root, plan = make_plan(tmp_path)
    binding = te.bind_terminal_expected(
        make_terminal_expected(),
        plan=plan,
        root=root,
        expected_authority=accept,
        contract_sample=True,
    )
    assert binding["status"] == "bound"
    assert binding["authorized"] is False
    assert "contract_sample_inputs_not_production_terminal" in binding["reasons"]


def test_validate_rejects_mutated_inner_expected_identity(tmp_path):
    root, plan = make_plan(tmp_path)
    binding = te.bind_terminal_expected(
        make_terminal_expected(), plan=plan, root=root, expected_authority=accept
    )
    forged = copy.deepcopy(binding)
    forged["expected"] = dict(forged["expected"])
    forged["expected"]["input_refs"] = [
        {"kind": "otlp", "path": "capture.json"},
        {"kind": "extra", "path": "x"},
    ]
    # Keep outer expected_check_ref / binding_ref recomputed after mutation.
    payload = {key: value for key, value in forged.items() if key != "binding_ref"}
    forged["binding_ref"] = te._digest(payload)
    with pytest.raises(
        te.TerminalExpectedError,
        match="terminal_expected_check_ref_mismatch|terminal_binding_",
    ):
        te.validate_terminal_expected_binding(forged)


def test_validate_rejects_plan_identity_drift(tmp_path):
    root, plan = make_plan(tmp_path)
    binding = te.bind_terminal_expected(
        make_terminal_expected(), plan=plan, root=root, expected_authority=accept
    )
    forged = copy.deepcopy(binding)
    forged["plan_identity"] = dict(forged["plan_identity"])
    forged["plan_identity"]["run_id"] = "other-run"
    payload = {key: value for key, value in forged.items() if key != "binding_ref"}
    forged["binding_ref"] = te._digest(payload)
    with pytest.raises(
        te.TerminalExpectedError, match="terminal_binding_plan_identity_mismatch"
    ):
        te.validate_terminal_expected_binding(forged)


def test_span_expectations_remain_independent_of_check_expected(tmp_path):
    root, plan = make_plan(tmp_path)
    expected = make_terminal_expected()
    binding = te.bind_terminal_expected(
        expected, plan=plan, root=root, expected_authority=accept
    )
    span_path = root / plan["post_completion"]["expectations"]["path"]
    original = span_path.read_text(encoding="utf-8")
    assert "spans" in original
    assert binding["expected_check_ref"] != plan["post_completion"]["sha256"]
    assert binding["expected"]["check_id"] == te.TERMINAL_CHECK_ID


def test_post_completion_with_expected_helper(tmp_path):
    if not FIXTURE.is_dir():
        pytest.skip("weaver-cicd fixture unavailable")
    root = tmp_path / "src"
    shutil.copytree(FIXTURE, root)
    binding = te.post_completion_with_expected(
        root=root,
        config_path=root / "ci-selection.toml",
        weaver_config="ci-weaver.toml",
        run_id="controlled-42/1",
        attempt=1,
        result="success",
        expected=make_terminal_expected(),
        expected_authority=accept,
    )
    assert binding["authorized"] is True
    assert binding["input_closure"] == "verified"
    assert binding["plan"]["phase"] == selection.POST_COMPLETION_PHASE
