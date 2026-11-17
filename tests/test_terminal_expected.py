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


def make_b2_terminal_entry(**changes):
    """Real #66-B2 Check Entry shape: no run_id / run_attempt / phase / scope_id."""
    definition = check_contract.definition_for("weaver_terminal")
    entry = {
        "check_id": definition.check_id,
        "definition_version": definition.definition_version,
        "rule_ref": copy.deepcopy(RULE),
        "source_ref": copy.deepcopy(SOURCE),
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
        "definition_status": "resolved",
        "rule_status": "resolved",
        "policy_status": "resolved",
    }
    entry.update(changes)
    entry["expected_check_ref"] = expected_checks.entry_identity(
        {key: value for key, value in entry.items() if key != "expected_check_ref"}
    )
    return entry


def make_parent_expected(
    entry=None,
    *,
    phase=check_contract.POST_COMPLETION_PHASE,
    phase_status="unverified",
    status="partial",
    scope_id=SCOPE_ID,
    expected_set_ref=None,
    include_expected_set_ref=True,
):
    """Frozen #66-B2 Expected v2: declared phase ⇒ partial + unverified."""
    if entry is None:
        entry = make_b2_terminal_entry()
    if include_expected_set_ref and expected_set_ref is None:
        expected_set_ref = expected_checks.set_identity(
            [entry], schema_version=expected_checks.EXPECTED_V2
        )
    parent = {
        "schema_version": expected_checks.EXPECTED_V2,
        "projection_source": "effective_profile",
        "status": status,
        "scope_id": scope_id,
        "phase": phase,
        "phase_status": phase_status,
        "checks": [entry],
        "non_check_obligations": [],
    }
    if include_expected_set_ref:
        parent["expected_set_ref"] = expected_set_ref
    return parent


def completed_run(**changes):
    value = {
        "run_id": "controlled-42/1",
        "attempt": 1,
        "result": "success",
        "status": "completed",
    }
    value.update(changes)
    return value


def phase_witness_for(parent, *, domain_complete=True, **changes):
    """External Phase/domain witness — does not rewrite the parent Schema."""
    value = {
        "accepted": True,
        "status": "authorized",
        "phase": parent["phase"],
        "scope_id": parent["scope_id"],
        "expected_set_ref": parent.get("expected_set_ref"),
        "domain_complete": domain_complete,
        "note": "controlled external attestation — not a current B2 production PASS",
    }
    value.update(changes)
    return value


def projection_proof_for(parent, **changes):
    """Controlled receipt that validate_projection accepted this frozen parent."""
    value = {
        "accepted": True,
        "status": "authorized",
        "validator": "expected_checks.validate_projection",
        "scope_id": parent["scope_id"],
        "expected_set_ref": parent.get("expected_set_ref"),
        "note": "controlled external attestation — not a live Producer PASS",
    }
    value.update(changes)
    return value


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


def test_frozen_b2_partial_binds_but_cannot_authorize_without_witnesses(tmp_path):
    root, plan = make_plan(tmp_path)
    expected = make_parent_expected()
    assert expected["status"] == "partial"
    assert expected["phase_status"] == "unverified"
    binding = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(),
        root=root,
        expected_authority=accept,
        completed_run_authority=accept,
    )
    assert binding["authorized"] is False
    assert "terminal_expected_phase_unverified" in binding["reasons"]
    assert "terminal_phase_witness_missing" in binding["reasons"]
    assert "terminal_projection_proof_missing" in binding["reasons"]
    assert "run_id" not in binding["expected"]


def test_authorized_requires_independent_witnesses_on_frozen_parent(tmp_path):
    root, plan = make_plan(tmp_path)
    expected = make_parent_expected()
    binding = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(),
        root=root,
        expected_authority=accept,
        completed_run_authority=accept,
        phase_witness=phase_witness_for(expected),
        projection_proof=projection_proof_for(expected),
    )
    assert binding["status"] == "bound"
    assert binding["authorized"] is True
    assert binding["phase_witness_verified"] is True
    assert binding["projection_verified"] is True
    assert binding["completed_run_verified"] is True
    assert expected["phase_status"] == "unverified"  # parent never rewritten
    validated = te.validate_terminal_expected_binding(copy.deepcopy(binding))
    assert validated["binding_ref"] == binding["binding_ref"]


def test_parent_phase_status_verified_is_rejected_by_current_b2_contract(tmp_path):
    root, plan = make_plan(tmp_path)
    with pytest.raises(
        te.TerminalExpectedError,
        match="terminal_expected_parent_phase_status_unsupported",
    ):
        te.bind_terminal_expected(
            make_parent_expected(status="complete", phase_status="verified"),
            plan=plan,
            completed_run=completed_run(),
            root=root,
            expected_authority=accept,
            completed_run_authority=accept,
        )


def test_complete_with_unverified_phase_is_rejected(tmp_path):
    root, plan = make_plan(tmp_path)
    with pytest.raises(
        te.TerminalExpectedError, match="terminal_expected_phase_complete_unproven"
    ):
        te.bind_terminal_expected(
            make_parent_expected(status="complete", phase_status="unverified"),
            plan=plan,
            completed_run=completed_run(),
            root=root,
            expected_authority=accept,
            completed_run_authority=accept,
        )


def test_phase_witness_without_domain_complete_cannot_authorize(tmp_path):
    root, plan = make_plan(tmp_path)
    expected = make_parent_expected()
    binding = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(),
        root=root,
        expected_authority=accept,
        completed_run_authority=accept,
        phase_witness=phase_witness_for(expected, domain_complete=False),
        projection_proof=projection_proof_for(expected),
    )
    assert binding["authorized"] is False
    assert "terminal_phase_witness_domain_incomplete" in binding["reasons"]


def test_phase_witness_wrong_expected_set_cannot_authorize(tmp_path):
    root, plan = make_plan(tmp_path)
    expected = make_parent_expected()
    binding = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(),
        root=root,
        expected_authority=accept,
        completed_run_authority=accept,
        phase_witness=phase_witness_for(
            expected, expected_set_ref="sha256:" + "9" * 64
        ),
        projection_proof=projection_proof_for(expected),
    )
    assert binding["authorized"] is False
    assert "terminal_phase_witness_expected_set_mismatch" in binding["reasons"]


def test_missing_expected_set_ref_cannot_authorize_even_with_phase_witness(tmp_path):
    root, plan = make_plan(tmp_path)
    expected = make_parent_expected(include_expected_set_ref=False)
    witness = {
        "accepted": True,
        "status": "authorized",
        "phase": expected["phase"],
        "scope_id": expected["scope_id"],
        "expected_set_ref": None,
        "domain_complete": True,
    }
    binding = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(),
        root=root,
        expected_authority=accept,
        completed_run_authority=accept,
        phase_witness=witness,
        projection_proof={
            "accepted": True,
            "status": "authorized",
            "validator": "expected_checks.validate_projection",
            "scope_id": expected["scope_id"],
            "expected_set_ref": None,
        },
    )
    assert binding["authorized"] is False
    assert "terminal_expected_set_ref_missing" in binding["reasons"]


def test_self_reported_completed_run_cannot_authorize(tmp_path):
    root, plan = make_plan(tmp_path)
    expected = make_parent_expected()
    binding = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(),
        root=root,
        expected_authority=accept,
        phase_witness=phase_witness_for(expected),
        projection_proof=projection_proof_for(expected),
    )
    assert binding["authorized"] is False
    assert binding["completed_run_verified"] is False
    assert "terminal_completed_run_source_unproven" in binding["reasons"]


def test_missing_root_marks_input_closure_unverified(tmp_path):
    _root, plan = make_plan(tmp_path)
    expected = make_parent_expected()
    binding = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(),
        expected_authority=accept,
        completed_run_authority=accept,
        phase_witness=phase_witness_for(expected),
        projection_proof=projection_proof_for(expected),
    )
    assert binding["authorized"] is False
    assert binding["input_closure"] == "unverified"


def test_drifted_plan_inputs_fail_closure_validation(tmp_path):
    root, plan = make_plan(tmp_path)
    (root / "advice-policies/cicd-error-type.rego").write_text(
        (root / "advice-policies/cicd-error-type.rego").read_text(encoding="utf-8")
        + "\n# drift\n",
        encoding="utf-8",
    )
    expected = make_parent_expected()
    binding = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(),
        root=root,
        expected_authority=accept,
        completed_run_authority=accept,
        phase_witness=phase_witness_for(expected),
        projection_proof=projection_proof_for(expected),
    )
    assert binding["authorized"] is False
    assert binding["input_closure"] == "failed"


def test_missing_authority_cannot_authorize_terminal(tmp_path):
    root, plan = make_plan(tmp_path)
    expected = make_parent_expected()
    binding = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(),
        root=root,
        completed_run_authority=accept,
        phase_witness=phase_witness_for(expected),
        projection_proof=projection_proof_for(expected),
    )
    assert binding["authorized"] is False
    assert "terminal_expected_authority_missing" in binding["reasons"]


def test_rejected_authority_is_not_authorized(tmp_path):
    root, plan = make_plan(tmp_path)
    expected = make_parent_expected()
    binding = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(),
        root=root,
        expected_authority=reject,
        completed_run_authority=accept,
        phase_witness=phase_witness_for(expected),
        projection_proof=projection_proof_for(expected),
    )
    assert binding["authorized"] is False
    assert binding["status"] == "rejected"


def test_wrong_parent_phase_is_rejected(tmp_path):
    root, plan = make_plan(tmp_path)
    with pytest.raises(
        te.TerminalExpectedError, match="terminal_expected_phase_invalid"
    ):
        te.bind_terminal_expected(
            make_parent_expected(phase=check_contract.EXECUTION_PHASE),
            plan=plan,
            completed_run=completed_run(),
            root=root,
            expected_authority=accept,
            completed_run_authority=accept,
        )


def test_wrong_completed_attempt_is_rejected(tmp_path):
    root, plan = make_plan(tmp_path)
    expected = make_parent_expected()
    binding = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(attempt=9),
        root=root,
        expected_authority=accept,
        completed_run_authority=accept,
        phase_witness=phase_witness_for(expected),
        projection_proof=projection_proof_for(expected),
    )
    assert binding["authorized"] is False
    assert "terminal_completed_attempt_mismatch" in binding["reasons"]


def test_post_hoc_run_fields_on_expected_are_rejected(tmp_path):
    root, plan = make_plan(tmp_path)
    entry = make_b2_terminal_entry(run_id="controlled-42/1", run_attempt=1)
    with pytest.raises(
        te.TerminalExpectedError, match="terminal_expected_post_hoc_run_forbidden"
    ):
        te.bind_terminal_expected(
            make_parent_expected(entry),
            plan=plan,
            completed_run=completed_run(),
            root=root,
            expected_authority=accept,
            completed_run_authority=accept,
        )


def test_missing_expected_check_ref_is_rejected(tmp_path):
    root, plan = make_plan(tmp_path)
    entry = make_b2_terminal_entry()
    del entry["expected_check_ref"]
    with pytest.raises(
        te.TerminalExpectedError, match="terminal_expected_check_ref_missing"
    ):
        te.bind_terminal_expected(
            make_parent_expected(entry),
            plan=plan,
            completed_run=completed_run(),
            root=root,
            expected_authority=accept,
            completed_run_authority=accept,
        )


def test_contract_sample_cannot_authorize(tmp_path):
    root, plan = make_plan(tmp_path)
    expected = make_parent_expected()
    binding = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(),
        root=root,
        expected_authority=accept,
        completed_run_authority=accept,
        phase_witness=phase_witness_for(expected),
        projection_proof=projection_proof_for(expected),
        contract_sample=True,
    )
    assert binding["status"] == "bound"
    assert binding["authorized"] is False


def test_validate_rejects_plan_identity_drift(tmp_path):
    root, plan = make_plan(tmp_path)
    expected = make_parent_expected()
    binding = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(),
        root=root,
        expected_authority=accept,
        completed_run_authority=accept,
        phase_witness=phase_witness_for(expected),
        projection_proof=projection_proof_for(expected),
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
    expected = make_parent_expected()
    binding = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(),
        root=root,
        expected_authority=accept,
        completed_run_authority=accept,
        phase_witness=phase_witness_for(expected),
        projection_proof=projection_proof_for(expected),
    )
    span_path = root / plan["post_completion"]["expectations"]["path"]
    assert "spans" in span_path.read_text(encoding="utf-8")
    assert binding["expected_check_ref"] != plan["post_completion"]["sha256"]


def test_post_completion_helper_does_not_self_authorize_from_plan_params(tmp_path):
    if not FIXTURE.is_dir():
        pytest.skip("weaver-cicd fixture unavailable")
    root = tmp_path / "src"
    shutil.copytree(FIXTURE, root)
    expected = make_parent_expected()
    binding = te.post_completion_with_expected(
        root=root,
        config_path=root / "ci-selection.toml",
        weaver_config="ci-weaver.toml",
        run_id="controlled-42/1",
        attempt=1,
        result="success",
        expected=expected,
        expected_authority=accept,
        phase_witness=phase_witness_for(expected),
        projection_proof=projection_proof_for(expected),
    )
    assert binding["authorized"] is False
    assert binding["completed_run_verified"] is False

    authorized = te.post_completion_with_expected(
        root=root,
        config_path=root / "ci-selection.toml",
        weaver_config="ci-weaver.toml",
        run_id="controlled-42/1",
        attempt=1,
        result="success",
        expected=expected,
        expected_authority=accept,
        completed_run_authority=accept,
        phase_witness=phase_witness_for(expected),
        projection_proof=projection_proof_for(expected),
    )
    assert authorized["authorized"] is True
