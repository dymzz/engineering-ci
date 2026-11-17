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
        "scope_id": scope_id,
        "phase": phase,
        "phase_status": phase_status,
        "profile_ref": copy.deepcopy(PROFILE_REF),
        "profile_revision": PROFILE_REVISION,
        "checks": checks,
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
        projection_proof=self_attested_projection_proof(expected),
    )
    assert binding["authorized"] is False
    assert binding["projection_verification"] == "untrusted"
    assert "terminal_projection_proof_untrusted" in binding["reasons"]
    assert expected["phase_status"] == "unverified"


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


def test_live_validate_projection_accepts_expected_plan_not_terminal_plan(tmp_path):
    """P1: live validator needs the original Expected Plan context."""
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
    base = git("rev-parse", "HEAD").strip()
    config = selection.load_config(path / "ci-selection.toml")
    changes = selection.changeset(path, base=base, mode="worktree")
    chosen = selection.select(changes, config, root=path)
    expected_plan = selection.plan(
        chosen, config=config, config_path=path / "ci-selection.toml", root=path
    )
    parent = expected_plan["expected_checks"]
    assert expected_plan.get("check_scope") is not None
    # Positive: original Expected Plan + Git + Root → live validate_projection.
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

    # Wrong context: Terminal post_completion Plan is not an Expected Plan.
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
