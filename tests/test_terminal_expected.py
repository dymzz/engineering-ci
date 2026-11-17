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
PROFILE_REF = {"name": "org/engineering", "version": "1"}
PROFILE_REVISION = "rev-2026-11-17"
ISSUER_REF = {
    "name": "org/phase-witness-issuer",
    "version": "1",
    "sha256": "b" * 64,
}
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
        "profile_ref": copy.deepcopy(PROFILE_REF),
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
    extra_checks=(),
):
    """Frozen #66-B2 Expected v2: declared phase ⇒ partial + unverified."""
    if entry is None:
        entry = make_b2_terminal_entry()
    checks = [entry, *[copy.deepcopy(item) for item in extra_checks]]
    if include_expected_set_ref and expected_set_ref is None:
        expected_set_ref = expected_checks.set_identity(
            checks, schema_version=expected_checks.EXPECTED_V2
        )
    parent = {
        "schema_version": expected_checks.EXPECTED_V2,
        "projection_source": "effective_profile",
        "status": status,
        "reason": (
            "effective_profile_phase_unverified"
            if status == "partial" and phase_status == "unverified"
            else None
        ),
        "scope_id": scope_id,
        "phase": phase,
        "phase_status": phase_status,
        "authority_status": "verified",
        "consumer_completeness": (
            "incomplete_profile"
            if status == "partial" and phase_status == "unverified"
            else None
        ),
        "profile_ref": copy.deepcopy(PROFILE_REF),
        "profile_revision": PROFILE_REVISION,
        "checks": checks,
        "non_check_obligations": [],
        "missing": [],
        "conflicts": [],
    }
    if include_expected_set_ref:
        parent["expected_set_ref"] = expected_set_ref
    return parent


TARGET_TREE = "a" * 40


def matching_run_subject(entry=None, parent=None, **changes):
    """Provider Git subject — distinct from Plan directory content SHA-256."""
    entry = entry or make_b2_terminal_entry()
    source = entry["source_ref"]
    event = entry["event_ref"]
    value = {
        "declared_target_tree": source.get("declared_target_tree"),
        "head_commit": event.get("head_commit"),
        "base_commit": event.get("base_commit"),
        "head_ref": event.get("head_ref"),
        "base_ref": event.get("base_ref"),
        "expected_check_ref": entry.get("expected_check_ref"),
    }
    if parent is not None:
        value["scope_id"] = parent.get("scope_id")
    value.update(changes)
    return value


def completed_run(**changes):
    value = {
        "run_id": "controlled-42/1",
        "attempt": 1,
        "result": "success",
        "status": "completed",
    }
    value.update(changes)
    if "subject" not in value and "omit_subject" not in value:
        value["subject"] = matching_run_subject()
    value.pop("omit_subject", None)
    return value


def accept(_context):
    return {"accepted": True, "status": "authorized"}


def accept_run_with_subject(context):
    """Controlled stand-in: Run terminal + attested Expected subject mapping."""
    assert context.get("expected_subject") is not None
    assert isinstance((context.get("completed_run") or {}).get("subject"), dict)
    return {
        "accepted": True,
        "status": "authorized",
        "subject_verified": True,
        "subject_status": "authorized",
    }


def accept_run_terminal_only(_context):
    """Run terminal only — does not attest subject mapping."""
    return {"accepted": True, "status": "authorized"}


def sealed_phase_witness(parent, *, domain_complete=True, **changes):
    kwargs = {
        "phase": parent["phase"],
        "scope_id": parent["scope_id"],
        "expected_set_ref": parent.get("expected_set_ref"),
        "profile_ref": parent.get("profile_ref"),
        "profile_revision": parent.get("profile_revision"),
        "issuer_ref": ISSUER_REF,
        "validated_at": "2026-11-17T12:00:00+00:00",
        "domain_complete": domain_complete,
    }
    kwargs.update(changes)
    return te.seal_phase_witness(**kwargs)


def self_attested_projection_proof(parent, **changes):
    """Caller dictionary — structurally aligned but untrusted for authorization."""
    value = {
        "accepted": True,
        "status": "authorized",
        "validator": "expected_checks.validate_projection",
        "scope_id": parent["scope_id"],
        "expected_set_ref": parent.get("expected_set_ref"),
    }
    value.update(changes)
    return value


def reject(_context):
    return False


def make_plan(tmp_path, *, source_tree_id=None):
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
        source_tree_id=source_tree_id,
    )
    gate = selection.validate_post_completion_binding(plan, root)
    assert gate["ok"]
    return root, plan


def test_frozen_b2_partial_binds_but_self_attested_proofs_cannot_authorize(tmp_path):
    root, plan = make_plan(tmp_path)
    expected = make_parent_expected()
    binding = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(),
        root=root,
        expected_authority=accept,
        completed_run_authority=accept,
        phase_witness=sealed_phase_witness(expected),
        phase_witness_authority=accept,
        projection_proof=self_attested_projection_proof(expected),
    )
    assert binding["authorized"] is False
    assert binding["projection_verification"] == "untrusted"
    assert "terminal_projection_proof_untrusted" in binding["reasons"]
    assert expected["phase_status"] == "unverified"


def test_forged_sealed_phase_witness_without_authority_cannot_authorize(tmp_path):
    """P0: caller-forged issuer_ref + recomputed witness_ref is not trust."""
    root, plan = make_plan(tmp_path)
    expected = make_parent_expected()
    forged = sealed_phase_witness(
        expected,
        issuer_ref={
            "name": "org/forged-issuer",
            "version": "1",
            "sha256": "f" * 64,
        },
    )
    assert forged["witness_ref"] == te._digest(te._witness_payload(forged))
    binding = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(),
        root=root,
        expected_authority=accept,
        completed_run_authority=accept,
        phase_witness=forged,
        # No phase_witness_authority — seal alone must not authorize.
        projection_proof=self_attested_projection_proof(expected),
    )
    assert binding["authorized"] is False
    assert binding["phase_witness_verified"] is False
    assert binding["phase_witness_status"] == "structurally_aligned"
    assert "terminal_phase_witness_authority_missing" in binding["reasons"]


def test_missing_run_subject_cannot_authorize(tmp_path):
    root, plan = make_plan(tmp_path)
    expected = make_parent_expected()
    binding = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(omit_subject=True),
        root=root,
        expected_authority=accept,
        completed_run_authority=accept,
        phase_witness=sealed_phase_witness(expected),
        phase_witness_authority=accept,
        projection_proof=self_attested_projection_proof(expected),
    )
    assert binding["authorized"] is False
    assert binding["subject_correlated"] is False
    assert "terminal_subject_correlation_missing" in binding["reasons"]


def test_subject_target_mismatch_cannot_authorize(tmp_path):
    root, plan = make_plan(tmp_path)
    expected = make_parent_expected()
    entry = expected["checks"][0]
    binding = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(
            subject=matching_run_subject(entry, expected, declared_target_tree="9" * 40)
        ),
        root=root,
        expected_authority=accept,
        completed_run_authority=accept_run_with_subject,
        phase_witness=sealed_phase_witness(expected),
        phase_witness_authority=accept,
        projection_proof=self_attested_projection_proof(expected),
    )
    assert binding["authorized"] is False
    assert "terminal_subject_declared_target_tree_mismatch" in binding["reasons"]


def test_upstream_unknown_from_other_check_cannot_authorize(tmp_path):
    """P0: unknown caused by another Check must not clear via Terminal evidence."""
    root, plan = make_plan(tmp_path)
    other = make_b2_terminal_entry()
    other["name"] = "docs"
    other["check_id"] = "eci/docs/render"
    other["applicability"] = {"status": "unknown", "reason": "changeset_incomplete"}
    other["expected_check_ref"] = expected_checks.entry_identity(
        {key: value for key, value in other.items() if key != "expected_check_ref"}
    )
    expected = make_parent_expected(extra_checks=(other,))
    expected["status"] = "unknown"
    expected["reason"] = "applicability_or_changeset_incomplete"
    expected["consumer_completeness"] = "unknown"
    terminal = expected["checks"][0]
    terminal["applicability"] = {
        "status": "unknown",
        "reason": "check_scope_missing",
    }
    terminal["expected_check_ref"] = expected_checks.entry_identity(
        {key: value for key, value in terminal.items() if key != "expected_check_ref"}
    )
    expected["expected_set_ref"] = expected_checks.set_identity(
        expected["checks"],
        expected.get("non_check_obligations") or (),
        schema_version=expected_checks.EXPECTED_V2,
    )
    binding = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(),
        root=root,
        expected_authority=accept,
        completed_run_authority=accept_run_with_subject,
        phase_witness=sealed_phase_witness(expected),
        phase_witness_authority=accept,
        projection_proof=self_attested_projection_proof(expected),
    )
    assert binding["authorized"] is False
    assert "terminal_expected_upstream_unknown" in binding["reasons"]


def test_upstream_partial_non_phase_cannot_authorize(tmp_path):
    """P0: partial from policy/rule/authority gaps is not cleared by Phase Witness."""
    root, plan = make_plan(tmp_path)
    expected = make_parent_expected()
    expected["status"] = "partial"
    expected["reason"] = "effective_profile_authority_unverified"
    expected["authority_status"] = "unverified"
    expected["consumer_completeness"] = "incomplete_profile"
    binding = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(),
        root=root,
        expected_authority=accept,
        completed_run_authority=accept_run_with_subject,
        phase_witness=sealed_phase_witness(expected),
        phase_witness_authority=accept,
        projection_proof=self_attested_projection_proof(expected),
    )
    assert binding["authorized"] is False
    assert "terminal_expected_upstream_partial" in binding["reasons"]


def test_phase_only_partial_clears_with_witness(tmp_path):
    """Positive: partial solely from phase_unverified remains bindable with witness."""
    root, plan = make_plan(tmp_path)
    expected = make_parent_expected()
    assert expected["reason"] == "effective_profile_phase_unverified"
    binding = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(),
        root=root,
        expected_authority=accept,
        completed_run_authority=accept_run_with_subject,
        phase_witness=sealed_phase_witness(expected),
        phase_witness_authority=accept,
        projection_proof=self_attested_projection_proof(expected),
    )
    assert "terminal_expected_upstream_partial" not in binding["reasons"]
    assert binding["phase_witness_verified"] is True
    assert binding["authorized"] is False  # still needs live projection
    assert binding["projection_verification"] == "untrusted"


def test_ref_only_subject_without_git_identity_cannot_authorize(tmp_path):
    """P0: copying expected_check_ref/scope_id alone is not source proof."""
    root, plan = make_plan(tmp_path)
    expected = make_parent_expected()
    entry = expected["checks"][0]
    binding = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(
            subject={
                "expected_check_ref": entry["expected_check_ref"],
                "scope_id": expected["scope_id"],
            }
        ),
        root=root,
        expected_authority=accept,
        completed_run_authority=accept_run_terminal_only,
        phase_witness=sealed_phase_witness(expected),
        phase_witness_authority=accept,
        projection_proof=self_attested_projection_proof(expected),
    )
    assert binding["authorized"] is False
    assert binding["subject_correlated"] is False
    assert "terminal_subject_authority_unattested" in binding["reasons"]

    attested_ref_only = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(
            subject={
                "expected_check_ref": entry["expected_check_ref"],
                "scope_id": expected["scope_id"],
            }
        ),
        root=root,
        expected_authority=accept,
        completed_run_authority=accept_run_with_subject,
        phase_witness=sealed_phase_witness(expected),
        phase_witness_authority=accept,
        projection_proof=self_attested_projection_proof(expected),
    )
    assert attested_ref_only["authorized"] is False
    assert "terminal_subject_git_identity_missing" in attested_ref_only["reasons"]


def test_sealed_witness_wrong_profile_revision_rejected_with_authority(tmp_path):
    root, plan = make_plan(tmp_path)
    expected = make_parent_expected()
    binding = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(),
        root=root,
        expected_authority=accept,
        completed_run_authority=accept,
        phase_witness=sealed_phase_witness(
            expected, profile_revision="rev-wrong-profile"
        ),
        phase_witness_authority=accept,
        projection_proof=self_attested_projection_proof(expected),
    )
    assert binding["authorized"] is False
    assert "terminal_phase_witness_profile_revision_mismatch" in binding["reasons"]


def test_phase_authority_accept_still_requires_live_projection(tmp_path):
    root, plan = make_plan(tmp_path)
    expected = make_parent_expected()
    binding = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(),
        root=root,
        expected_authority=accept,
        completed_run_authority=accept,
        phase_witness=sealed_phase_witness(expected),
        phase_witness_authority=accept,
        projection_proof=self_attested_projection_proof(expected),
    )
    assert binding["phase_witness_verified"] is True
    assert binding["phase_witness_status"] == "authority_verified"
    assert binding["authorized"] is False
    assert binding["projection_verification"] == "untrusted"


def test_bare_accepted_phase_witness_is_untrusted(tmp_path):
    root, plan = make_plan(tmp_path)
    expected = make_parent_expected()
    bare = {
        "accepted": True,
        "status": "authorized",
        "phase": expected["phase"],
        "scope_id": expected["scope_id"],
        "expected_set_ref": expected["expected_set_ref"],
        "domain_complete": True,
    }
    binding = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(),
        root=root,
        expected_authority=accept,
        completed_run_authority=accept,
        phase_witness=bare,
        phase_witness_authority=accept,
        projection_proof=self_attested_projection_proof(expected),
    )
    assert binding["authorized"] is False
    assert "terminal_phase_witness_issuer_unproven" in binding["reasons"]


def test_tampered_checks_with_stale_expected_set_ref_cannot_authorize(tmp_path):
    root, plan = make_plan(tmp_path)
    expected = make_parent_expected()
    stale_ref = expected["expected_set_ref"]
    # Keep declared expected_set_ref, but mutate the actual set.
    extra = make_b2_terminal_entry()
    extra["name"] = "weaver_terminal_extra"
    extra["check_id"] = "eci/weaver/live"
    extra["expected_check_ref"] = expected_checks.entry_identity(
        {key: value for key, value in extra.items() if key != "expected_check_ref"}
    )
    expected["checks"].append(extra)
    assert expected["expected_set_ref"] == stale_ref
    binding = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(),
        root=root,
        expected_authority=accept,
        completed_run_authority=accept,
        phase_witness=sealed_phase_witness(expected, expected_set_ref=stale_ref),
        projection_proof=self_attested_projection_proof(
            expected, expected_set_ref=stale_ref
        ),
    )
    assert binding["authorized"] is False
    assert "terminal_expected_set_ref_mismatch" in binding["reasons"]


def test_terminal_plan_is_rejected_as_projection_context(tmp_path):
    root, plan = make_plan(tmp_path)
    expected = make_parent_expected()
    # Terminal post_completion_plan lacks Expected Profile inputs.
    binding = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(),
        root=root,
        expected_authority=accept,
        completed_run_authority=accept,
        phase_witness=sealed_phase_witness(expected),
        phase_witness_authority=accept,
        projection_git=plan.get("git") or {"snapshot": "worktree"},
        expected_plan=plan,
        expected_root=root,
    )
    assert binding["authorized"] is False
    assert binding["projection_verification"] == "failed"
    assert any(
        item.startswith("terminal_projection_validation_")
        for item in binding["reasons"]
    )


def test_parent_phase_status_verified_is_rejected(tmp_path):
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
        phase_witness=sealed_phase_witness(expected, domain_complete=False),
        projection_proof=self_attested_projection_proof(expected),
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
        phase_witness=sealed_phase_witness(
            expected, expected_set_ref="sha256:" + "9" * 64
        ),
        projection_proof=self_attested_projection_proof(expected),
    )
    assert binding["authorized"] is False
    assert "terminal_phase_witness_expected_set_mismatch" in binding["reasons"]


def test_self_reported_completed_run_cannot_authorize(tmp_path):
    root, plan = make_plan(tmp_path)
    expected = make_parent_expected()
    binding = te.bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run(),
        root=root,
        expected_authority=accept,
        phase_witness=sealed_phase_witness(expected),
        projection_proof=self_attested_projection_proof(expected),
    )
    assert binding["authorized"] is False
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
        phase_witness=sealed_phase_witness(expected),
        projection_proof=self_attested_projection_proof(expected),
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
        phase_witness=sealed_phase_witness(expected),
        projection_proof=self_attested_projection_proof(expected),
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
        phase_witness=sealed_phase_witness(expected),
        projection_proof=self_attested_projection_proof(expected),
    )
    assert binding["authorized"] is False
    assert "terminal_expected_authority_missing" in binding["reasons"]


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
        phase_witness=sealed_phase_witness(expected),
        projection_proof=self_attested_projection_proof(expected),
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
        phase_witness=sealed_phase_witness(expected),
        projection_proof=self_attested_projection_proof(expected),
        contract_sample=True,
    )
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
        phase_witness=sealed_phase_witness(expected),
        projection_proof=self_attested_projection_proof(expected),
    )
    forged = copy.deepcopy(binding)
    forged["plan_identity"] = dict(forged["plan_identity"])
    forged["plan_identity"]["run_id"] = "other-run"
    # Keep authorized=False so consistency allows the forged envelope digest.
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
        phase_witness=sealed_phase_witness(expected),
        projection_proof=self_attested_projection_proof(expected),
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
        completed_run_authority=accept,
        phase_witness=sealed_phase_witness(expected),
        projection_proof=self_attested_projection_proof(expected),
    )
    assert binding["authorized"] is False
    assert "terminal_projection_proof_untrusted" in binding["reasons"]


def _attach_phase_profile_policy(path, profile):
    """Minimal [effective_profile] policy binding for live Expected v2 tests."""
    import json

    binding = profile["approval_binding"]
    binding["approval_digest"] = expected_checks._approval_digest(profile)
    profile["profile_sha256"] = expected_checks.profile_digest(profile)
    declared = profile.get("declared_checks") or []
    config_path = path / "ci-selection.toml"
    existing = config_path.read_text(encoding="utf-8")
    existing = existing.split("\n[effective_profile]", 1)[0].rstrip()
    phase_line = (
        f'phase = "{profile["phase"]}"\n'
        if isinstance(profile.get("phase"), str) and profile["phase"]
        else ""
    )
    config_path.write_text(
        existing
        + f"""

[effective_profile]
profile_ref = {{ name = "{profile["profile_ref"]["name"]}", version = "{profile["profile_ref"]["version"]}" }}
profile_revision = "{profile["profile_revision"]}"
approval_source = "{binding["approval_source"]}"
approval_digest = "{binding["approval_digest"]}"
effective_at = "{profile["effective_at"]}"
declared_checks = {json.dumps(declared)}
{phase_line}""",
        encoding="utf-8",
    )
    return selection.load_config(config_path)


def test_live_expected_v2_phase_unverified_and_terminal_plan_rejected(tmp_path):
    """P1: real Effective Profile Expected v2 validates; Terminal Plan context fails."""
    import subprocess

    selection_fixture = ROOT / "tests/fixtures/selection-app"
    if not selection_fixture.is_dir():
        pytest.skip("selection-app fixture unavailable")
    path = tmp_path / "expected-repo"
    shutil.copytree(selection_fixture, path)

    def git(*args):
        return subprocess.run(
            ["git", *args],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout

    git("init", "-q", "-b", "main")
    git("config", "user.email", "terminal@example.invalid")
    git("config", "user.name", "Terminal Test")
    git("add", "-A")
    git("commit", "-q", "-m", "base")

    profile = expected_checks.build_effective_profile(
        profile_ref={"name": "consumer.terminal", "version": "1.0.0"},
        profile_revision=PROFILE_REVISION,
        approval_source="governance-signed",
        authority_verified=True,
        effective_at="2026-11-17T00:00:00Z",
        evaluated_at="2026-11-17T01:00:00Z",
        context="worktree",
        action="worktree",
        target="HEAD",
        phase=check_contract.POST_COMPLETION_PHASE,
        declared_checks=["whitespace", "docs", "weaver_terminal"],
        required_obligations=[
            {
                "obligation_ref": "ob/term",
                "kind": "check",
                "check_names": ["weaver_terminal"],
                "allow_inapplicable": True,
            }
        ],
        advisory_checks=[],
        allowed_inapplicability=[],
        domain_complete=True,
        governed_rules=expected_checks.governed_domain_rules(
            path, config_path=path / "ci-selection.toml"
        ),
    )
    profile.pop("governed_rules", None)
    declared_domain = (profile.get("approval_binding") or {}).get("declared_domain")
    if isinstance(declared_domain, dict):
        declared_domain.pop("governed_rules", None)
    cfg = _attach_phase_profile_policy(path, profile)
    git("add", "ci-selection.toml")
    git("commit", "-q", "-m", "attach phase profile")
    base = git("rev-parse", "HEAD").strip()
    (path / "docs/guide.md").write_text("# docs\n", encoding="utf-8")
    changes = selection.changeset(path, base=base, mode="worktree")
    chosen = selection.select(changes, cfg, root=path)
    expected_plan = selection.plan(
        chosen,
        config=cfg,
        config_path=path / "ci-selection.toml",
        root=path,
        effective_profile=profile,
    )
    parent = expected_plan["expected_checks"]
    assert parent["schema_version"] == expected_checks.EXPECTED_V2
    assert parent["projection_source"] == "effective_profile"
    assert parent["phase"] == check_contract.POST_COMPLETION_PHASE
    assert parent["phase_status"] == "unverified"
    assert any(item.get("name") == "weaver_terminal" for item in parent["checks"])

    ok, basis, reasons = te._bind_projection_proof(
        None,
        scope_id=parent["scope_id"],
        expected_set_ref=parent["expected_set_ref"],
        parent=parent,
        git=expected_plan["git"],
        expected_plan=expected_plan,
        expected_root=path,
    )
    assert ok is True
    assert basis == "validate_projection"
    assert reasons == []

    # Wrong context: Terminal Plan is not Expected Plan for validate_projection.
    if not FIXTURE.is_dir():
        return
    terminal_root = tmp_path / "terminal-src"
    shutil.copytree(FIXTURE, terminal_root)
    terminal_plan = selection.post_completion_plan(
        root=terminal_root,
        config_path=terminal_root / "ci-selection.toml",
        weaver_config="ci-weaver.toml",
        run_id="controlled-42/1",
        attempt=1,
        result="success",
    )
    bad_ok, bad_basis, bad_reasons = te._bind_projection_proof(
        None,
        scope_id=parent["scope_id"],
        expected_set_ref=parent["expected_set_ref"],
        parent=parent,
        git=expected_plan["git"],
        expected_plan=terminal_plan,
        expected_root=path,
    )
    assert bad_ok is False
    assert bad_basis == "failed"
    assert any(
        item.startswith("terminal_projection_validation_") for item in bad_reasons
    )


def test_weaver_terminal_v2_authorizes_when_subjects_correlate(tmp_path):
    """P1: real weaver_terminal Expected v2 → live validate → correlated bind."""
    import subprocess

    selection_fixture = ROOT / "tests/fixtures/selection-app"
    if not selection_fixture.is_dir() or not FIXTURE.is_dir():
        pytest.skip("fixtures unavailable")
    path = tmp_path / "expected-repo"
    shutil.copytree(selection_fixture, path)

    def git(*args):
        return subprocess.run(
            ["git", *args],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout

    git("init", "-q", "-b", "main")
    git("config", "user.email", "terminal@example.invalid")
    git("config", "user.name", "Terminal Test")
    git("add", "-A")
    git("commit", "-q", "-m", "base")

    profile = expected_checks.build_effective_profile(
        profile_ref={"name": "consumer.terminal", "version": "1.0.0"},
        profile_revision=PROFILE_REVISION,
        approval_source="governance-signed",
        authority_verified=True,
        effective_at="2026-11-17T00:00:00Z",
        evaluated_at="2026-11-17T01:00:00Z",
        context="worktree",
        action="worktree",
        target="HEAD",
        phase=check_contract.POST_COMPLETION_PHASE,
        declared_checks=["whitespace", "docs", "weaver_terminal"],
        required_obligations=[
            {
                "obligation_ref": "ob/term",
                "kind": "check",
                "check_names": ["weaver_terminal"],
                "allow_inapplicable": True,
            }
        ],
        advisory_checks=[],
        allowed_inapplicability=[],
        domain_complete=True,
        governed_rules=expected_checks.governed_domain_rules(
            path, config_path=path / "ci-selection.toml"
        ),
    )
    profile.pop("governed_rules", None)
    declared_domain = (profile.get("approval_binding") or {}).get("declared_domain")
    if isinstance(declared_domain, dict):
        declared_domain.pop("governed_rules", None)
    cfg = _attach_phase_profile_policy(path, profile)
    git("add", "ci-selection.toml")
    git("commit", "-q", "-m", "attach phase profile")
    base = git("rev-parse", "HEAD").strip()
    (path / "docs/guide.md").write_text("# docs\n", encoding="utf-8")
    changes = selection.changeset(path, base=base, mode="worktree")
    chosen = selection.select(changes, cfg, root=path)
    expected_plan = selection.plan(
        chosen,
        config=cfg,
        config_path=path / "ci-selection.toml",
        root=path,
        effective_profile=profile,
    )
    parent = expected_plan["expected_checks"]
    entry = next(item for item in parent["checks"] if item["name"] == "weaver_terminal")
    # Freeze identity — do not rewrite expected_check_ref / expected_set_ref.
    frozen_ref = entry["expected_check_ref"]
    frozen_set = parent["expected_set_ref"]
    target_tree = entry["source_ref"]["declared_target_tree"]

    # Binder copy may carry Profile revision for Phase Witness association only.
    binder_parent = copy.deepcopy(parent)
    binder_parent["profile_ref"] = copy.deepcopy(profile["profile_ref"])
    binder_parent["profile_revision"] = profile["profile_revision"]

    terminal_root = tmp_path / "terminal-src"
    shutil.copytree(FIXTURE, terminal_root)
    # Plan source_tree_id is directory content SHA-256 — not Expected Git Tree OID.
    plan_content_id = selection.directory_content_id(terminal_root)
    assert plan_content_id != target_tree
    terminal_plan = selection.post_completion_plan(
        root=terminal_root,
        config_path=terminal_root / "ci-selection.toml",
        weaver_config="ci-weaver.toml",
        run_id="controlled-42/1",
        attempt=1,
        result="success",
        source_tree_id=plan_content_id,
    )
    assert selection.validate_post_completion_binding(terminal_plan, terminal_root)[
        "ok"
    ]

    # Controlled stand-ins: Git subject + same-algorithm content id mapping.
    run = completed_run(
        subject=matching_run_subject(
            entry,
            binder_parent,
            source_content_id=plan_content_id,
        )
    )
    binding = te.bind_terminal_expected(
        binder_parent,
        plan=terminal_plan,
        completed_run=run,
        root=terminal_root,
        expected_authority=accept,
        completed_run_authority=accept_run_with_subject,
        phase_witness=sealed_phase_witness(binder_parent),
        phase_witness_authority=accept,
        projection_git=expected_plan["git"],
        expected_plan=expected_plan,
        expected_root=path,
    )
    assert entry["expected_check_ref"] == frozen_ref
    assert parent["expected_set_ref"] == frozen_set
    assert binding["projection_verification"] == "validate_projection"
    assert binding["subject_correlated"] is True
    assert binding["authorized"] is True
    assert binding["expected_check_ref"] == frozen_ref
    assert binding["plan_identity"]["source_tree_id"] == plan_content_id

    # Individually valid objects with Target/Source mismatch cannot authorize.
    mismatched = te.bind_terminal_expected(
        binder_parent,
        plan=terminal_plan,
        completed_run=completed_run(
            subject=matching_run_subject(
                entry,
                binder_parent,
                declared_target_tree="0" * 40,
                source_content_id=plan_content_id,
            )
        ),
        root=terminal_root,
        expected_authority=accept,
        completed_run_authority=accept_run_with_subject,
        phase_witness=sealed_phase_witness(binder_parent),
        phase_witness_authority=accept,
        projection_git=expected_plan["git"],
        expected_plan=expected_plan,
        expected_root=path,
    )
    assert mismatched["authorized"] is False
    assert "terminal_subject_declared_target_tree_mismatch" in mismatched["reasons"]

    wrong_content = te.bind_terminal_expected(
        binder_parent,
        plan=terminal_plan,
        completed_run=completed_run(
            subject=matching_run_subject(
                entry,
                binder_parent,
                source_content_id="f" * 64,
            )
        ),
        root=terminal_root,
        expected_authority=accept,
        completed_run_authority=accept_run_with_subject,
        phase_witness=sealed_phase_witness(binder_parent),
        phase_witness_authority=accept,
        projection_git=expected_plan["git"],
        expected_plan=expected_plan,
        expected_root=path,
    )
    assert wrong_content["authorized"] is False
    assert "terminal_subject_run_plan_content_id_mismatch" in wrong_content["reasons"]
