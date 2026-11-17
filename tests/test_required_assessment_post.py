"""#61 R5 post-stage Required Assessment contract tests."""

from __future__ import annotations

import copy

import pytest
from test_required_assessment import (
    assess,
    check_obligation,
    evidence_entry,
    historical_coverage,
    make_admission,
    make_entry,
    make_evidence,
    make_profile,
    make_projection,
    make_universe,
    non_check_expected,
    non_check_obligation,
)

from engineering_ci import required_assessment_post as rap

EVALUATED_AT = "2026-11-17T12:00:00+00:00"
EARLIEST = "2026-11-17T10:00:00+00:00"
DUE = "2026-11-17T18:00:00+00:00"
BEFORE = "2026-11-17T09:00:00+00:00"
AFTER = "2026-11-17T19:00:00+00:00"


def trigger(**changes):
    value = {
        "kind": "run_completed",
        "observed_at": EVALUATED_AT,
        "run_id": "run/123",
        "run_attempt": 1,
        "target": "candidate",
        "run_completed": True,
    }
    value.update(changes)
    return value


def window(**changes):
    value = {"earliest_at": EARLIEST, "due_at": DUE}
    value.update(changes)
    return value


def prior_passed(*, contract_sample=False):
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(
        profile,
        projection,
        [obligation],
        contract_sample=contract_sample,
        phase="pre_publish",
    )
    admission = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
        contract_sample=contract_sample,
    )
    return assess(universe, projection, admission=admission)


def prior_blocked():
    profile = make_profile(authority_verified=False)
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(
        profile,
        projection,
        [obligation],
        authority_status="unverified",
        contract_sample=False,
        phase="pre_publish",
    )
    admission = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
        contract_sample=False,
    )
    return assess(universe, projection, admission=admission)


def post_assessment(*, passed=True, contract_sample=False):
    profile = make_profile(
        obligations=[{"obligation_ref": "o/sbom", "kind": "non_check"}],
        declared_checks=[],
    )
    projection = make_projection(
        profile, [], non_check=[non_check_expected("o/sbom")]
    )
    obligation = non_check_obligation("o/sbom", phase="post_publish")
    universe = make_universe(
        profile,
        projection,
        [obligation],
        phase="post_publish",
        contract_sample=contract_sample,
    )
    evidence = (
        make_evidence(
            [evidence_entry("o/sbom", phase="post_publish")],
            contract_sample=contract_sample,
        )
        if passed
        else make_evidence([], contract_sample=contract_sample)
    )
    return assess(
        universe,
        projection,
        admission=make_admission(projection, contract_sample=contract_sample),
        non_check_evidence=evidence,
    )


def accept_disposition(_context):
    return {"accepted": True, "status": "authorized", "disposition": "await_repair"}


def test_prior_pass_and_in_window_post_pass_is_verified():
    prior = prior_passed(contract_sample=False)
    post = post_assessment(passed=True, contract_sample=False)
    result = rap.reconcile_post_stage(
        prior,
        post,
        trigger_event=trigger(),
        due_window=window(),
        evaluated_at=EVALUATED_AT,
    )
    assert result["status"] == "verified"
    assert result["prior_assessment_status"] == "passed"
    assert result["post_assessment_status"] == "passed"
    assert result["due_state"] == "within_window"
    assert result["auto_revoke_published"] is False
    assert result["lifecycle_all_phases_passed"] is True
    assert rap.validate_post_stage(copy.deepcopy(result))["post_stage_ref"] == (
        result["post_stage_ref"]
    )


def test_before_earliest_is_pending_not_success():
    prior = prior_passed(contract_sample=False)
    post = post_assessment(passed=True, contract_sample=False)
    result = rap.reconcile_post_stage(
        prior,
        post,
        trigger_event=trigger(observed_at=BEFORE),
        due_window=window(),
        evaluated_at=BEFORE,
    )
    assert result["status"] == "pending"
    assert result["due_state"] == "not_yet_due"
    assert result["lifecycle_all_phases_passed"] is False


def test_overdue_without_passing_evidence_is_not_success():
    prior = prior_passed(contract_sample=False)
    post = post_assessment(passed=False, contract_sample=False)
    result = rap.reconcile_post_stage(
        prior,
        post,
        trigger_event=trigger(observed_at=AFTER),
        due_window=window(),
        evaluated_at=AFTER,
    )
    assert result["status"] in {"rejected", "unverified"}
    assert result["status"] != "verified"
    assert result["due_state"] == "overdue"
    assert result["prior_assessment_status"] == "passed"


def test_post_smoke_fail_preserves_prior_pass():
    prior = prior_passed(contract_sample=False)
    post = post_assessment(passed=False, contract_sample=False)
    assert post["status"] == "blocked"
    result = rap.reconcile_post_stage(
        prior,
        post,
        trigger_event=trigger(),
        due_window=window(),
        evaluated_at=EVALUATED_AT,
        disposition_authority=accept_disposition,
    )
    assert result["status"] != "verified"
    assert result["prior_assessment_status"] == "passed"
    assert result["post_assessment_status"] == "blocked"
    assert result["disposition"] == "await_repair"
    assert result["auto_revoke_published"] is False


def test_prior_blocked_cannot_open_verified_post_stage():
    prior = prior_blocked()
    post = post_assessment(passed=True, contract_sample=False)
    result = rap.reconcile_post_stage(
        prior,
        post,
        trigger_event=trigger(),
        due_window=window(),
        evaluated_at=EVALUATED_AT,
    )
    assert result["status"] == "rejected"
    assert result["prior_assessment_status"] == "blocked"


def test_profile_not_applicable_can_verify_without_post_required():
    prior = prior_passed(contract_sample=False)
    post = post_assessment(passed=True, contract_sample=False)
    result = rap.reconcile_post_stage(
        prior,
        post,
        trigger_event=trigger(),
        due_window=window(),
        evaluated_at=EVALUATED_AT,
        profile_applicable=False,
    )
    assert result["due_state"] == "not_applicable"
    assert result["status"] == "verified"
    assert result["disposition"] == "none"


def test_incomplete_run_trigger_must_not_force_wait():
    prior = prior_passed(contract_sample=False)
    post = post_assessment(passed=True, contract_sample=False)
    result = rap.reconcile_post_stage(
        prior,
        post,
        trigger_event=trigger(run_completed=False),
        due_window=window(),
        evaluated_at=EVALUATED_AT,
    )
    assert result["status"] != "verified"
    assert "post_trigger_run_not_completed" in result["reasons"]
    assert any(
        gap["reason"] == "incomplete_run_must_not_force_workflow_wait"
        for gap in result["gaps"]
    )


def test_authorized_reevaluation_trigger():
    prior = prior_passed(contract_sample=False)
    post = post_assessment(passed=True, contract_sample=False)
    result = rap.reconcile_post_stage(
        prior,
        post,
        trigger_event=trigger(kind="authorized_reevaluation"),
        due_window=window(),
        evaluated_at=EVALUATED_AT,
        disposition_authority=lambda _ctx: {
            "accepted": True,
            "status": "authorized",
            "disposition": "reevaluate",
        },
    )
    assert result["status"] == "verified"
    assert result["trigger_event"]["kind"] == "authorized_reevaluation"
    assert result["disposition"] == "reevaluate"


def test_contract_sample_cannot_claim_verified_lifecycle():
    prior = prior_passed(contract_sample=True)
    post = post_assessment(passed=True, contract_sample=True)
    result = rap.reconcile_post_stage(
        prior,
        post,
        trigger_event=trigger(),
        due_window=window(),
        evaluated_at=EVALUATED_AT,
        contract_sample=True,
    )
    assert result["status"] != "verified"
    assert result["contract_sample"] is True
    assert "contract_sample_inputs_not_production_post_stage" in result["reasons"]


def test_validate_rejects_tampered_lifecycle_claim():
    prior = prior_passed(contract_sample=False)
    post = post_assessment(passed=False, contract_sample=False)
    result = rap.reconcile_post_stage(
        prior,
        post,
        trigger_event=trigger(),
        due_window=window(),
        evaluated_at=EVALUATED_AT,
    )
    forged = copy.deepcopy(result)
    forged["lifecycle_all_phases_passed"] = True
    payload = {
        key: value for key, value in forged.items() if key != "post_stage_ref"
    }
    forged["post_stage_ref"] = rap._digest(payload)
    with pytest.raises(
        rap.RequiredAssessmentPostError, match="post_lifecycle_requires_verified"
    ):
        rap.validate_post_stage(forged)


def test_validate_rejects_auto_revoke_flag():
    prior = prior_passed(contract_sample=False)
    post = post_assessment(passed=True, contract_sample=False)
    result = rap.reconcile_post_stage(
        prior,
        post,
        trigger_event=trigger(),
        due_window=window(),
        evaluated_at=EVALUATED_AT,
    )
    forged = copy.deepcopy(result)
    forged["auto_revoke_published"] = True
    payload = {
        key: value for key, value in forged.items() if key != "post_stage_ref"
    }
    forged["post_stage_ref"] = rap._digest(payload)
    with pytest.raises(
        rap.RequiredAssessmentPostError, match="post_auto_revoke_forbidden"
    ):
        rap.validate_post_stage(forged)
