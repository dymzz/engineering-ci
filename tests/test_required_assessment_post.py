"""#61 R5 post-stage Required Assessment contract tests."""

from __future__ import annotations

import copy

import pytest
from test_required_assessment import (
    BASIS_REF,
    PHASE_ORDER,
    assess,
    check_obligation,
    evidence_entry,
    historical_coverage,
    inapplicability_authorization,
    make_admission,
    make_entry,
    make_evidence,
    make_profile,
    make_projection,
    make_universe,
    non_check_expected,
    non_check_obligation,
)
from test_required_assessment import (
    EVALUATED_AT as PRIOR_EVALUATED_AT,
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


def accept(_context):
    return {"accepted": True, "status": "authorized"}


def accept_due_with_late(context):
    outcome = {"accepted": True, "status": "authorized"}
    if context.get("due_state") == "overdue":
        outcome["late_evidence_accepted"] = True
    return outcome


def reconcile(prior, post, **changes):
    kwargs = {
        "trigger_event": trigger(),
        "due_window": window(),
        "evaluated_at": EVALUATED_AT,
        "phase_order": copy.deepcopy(PHASE_ORDER),
        "phase_order_authority": accept,
        "trigger_authority": accept,
        "due_window_authority": accept,
    }
    kwargs.update(changes)
    return rap.reconcile_post_stage(prior, post, **kwargs)


def prior_passed(
    *,
    contract_sample=False,
    candidate_commit="2" * 40,
    phase="pre_publish",
    evaluated_at=PRIOR_EVALUATED_AT,
):
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(
        profile,
        projection,
        [obligation],
        contract_sample=contract_sample,
        phase=phase,
        candidate={"commit": candidate_commit},
    )
    admission = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
        contract_sample=contract_sample,
    )
    return assess(universe, projection, admission=admission, evaluated_at=evaluated_at)


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


def post_assessment(
    *,
    passed=True,
    contract_sample=False,
    candidate_commit="2" * 40,
    applicability="required",
    phase="post_publish",
    evaluated_at=EVALUATED_AT,
):
    profile = make_profile(
        obligations=[{"obligation_ref": "o/sbom", "kind": "non_check"}],
        declared_checks=[],
    )
    projection = make_projection(profile, [], non_check=[non_check_expected("o/sbom")])
    if applicability == "not_applicable":
        obligation = non_check_obligation(
            "o/sbom",
            phase=phase,
            applicability="not_applicable",
            basis_ref=BASIS_REF,
            applicability_authorization=inapplicability_authorization(
                obligation_ref="o/sbom",
                phase=phase,
            ),
        )
        evidence = None
    else:
        obligation = non_check_obligation("o/sbom", phase=phase)
        evidence = (
            make_evidence(
                [
                    evidence_entry(
                        "o/sbom", phase=phase, subject_ref={"commit": candidate_commit}
                    )
                ],
                contract_sample=contract_sample,
            )
            if passed
            else make_evidence([], contract_sample=contract_sample)
        )
    universe = make_universe(
        profile,
        projection,
        [obligation],
        phase=phase,
        contract_sample=contract_sample,
        candidate={"commit": candidate_commit},
    )
    kwargs = {
        "admission": make_admission(projection, contract_sample=contract_sample),
        "evaluated_at": evaluated_at,
    }
    if evidence is not None:
        kwargs["non_check_evidence"] = evidence
    return assess(universe, projection, **kwargs)


def accept_disposition(_context):
    return {"accepted": True, "status": "authorized", "disposition": "await_repair"}


def test_prior_pass_and_in_window_post_pass_is_verified():
    prior = prior_passed(contract_sample=False)
    post = post_assessment(passed=True, contract_sample=False)
    result = reconcile(prior, post)
    assert result["status"] == "verified"
    assert result["prior_assessment_status"] == "passed"
    assert result["post_assessment_status"] == "passed"
    assert result["due_state"] == "within_window"
    assert result["prior_phase"] == "pre_publish"
    assert result["post_phase"] == "post_publish"
    assert result["lifecycle_identity"]["candidate"] == {"commit": "2" * 40}
    assert result["auto_revoke_published"] is False
    assert result["lifecycle_all_phases_passed"] is False
    assert result["lifecycle_coverage"] == "unverified"
    assert (
        rap.validate_post_stage(copy.deepcopy(result))["post_stage_ref"]
        == (result["post_stage_ref"])
    )


def test_before_earliest_is_pending_not_success():
    prior = prior_passed(contract_sample=False)
    post = post_assessment(passed=True, contract_sample=False, evaluated_at=BEFORE)
    result = reconcile(
        prior,
        post,
        trigger_event=trigger(observed_at=BEFORE),
        evaluated_at=BEFORE,
    )
    assert result["status"] == "pending"
    assert result["due_state"] == "not_yet_due"
    assert result["lifecycle_all_phases_passed"] is False


def test_overdue_without_passing_evidence_is_not_success():
    prior = prior_passed(contract_sample=False)
    post = post_assessment(passed=False, contract_sample=False, evaluated_at=AFTER)
    result = reconcile(
        prior,
        post,
        trigger_event=trigger(observed_at=AFTER),
        evaluated_at=AFTER,
        due_window_authority=accept_due_with_late,
    )
    assert result["status"] != "verified"
    assert result["due_state"] == "overdue"
    assert result["prior_assessment_status"] == "passed"


def test_overdue_pass_requires_authorized_late_evidence():
    prior = prior_passed(contract_sample=False)
    post = post_assessment(passed=True, contract_sample=False, evaluated_at=AFTER)
    denied = reconcile(
        prior,
        post,
        trigger_event=trigger(observed_at=AFTER),
        evaluated_at=AFTER,
        due_window_authority=accept,
    )
    assert denied["status"] != "verified"
    assert "post_overdue_late_evidence_unproven" in denied["reasons"]

    accepted = reconcile(
        prior,
        post,
        trigger_event=trigger(observed_at=AFTER),
        evaluated_at=AFTER,
        due_window_authority=accept_due_with_late,
    )
    assert accepted["status"] == "verified"
    assert accepted["due_state"] == "overdue"


def test_post_smoke_fail_preserves_prior_pass():
    prior = prior_passed(contract_sample=False)
    post = post_assessment(passed=False, contract_sample=False)
    assert post["status"] == "blocked"
    result = reconcile(prior, post, disposition_authority=accept_disposition)
    assert result["status"] != "verified"
    assert result["prior_assessment_status"] == "passed"
    assert result["post_assessment_status"] == "blocked"
    assert result["disposition"] == "await_repair"
    assert result["auto_revoke_published"] is False


def test_prior_blocked_cannot_open_verified_post_stage():
    prior = prior_blocked()
    post = post_assessment(passed=True, contract_sample=False)
    result = reconcile(prior, post)
    assert result["status"] == "rejected"
    assert result["prior_assessment_status"] == "blocked"


def test_authorized_not_applicable_post_can_verify():
    prior = prior_passed(contract_sample=False)
    post = post_assessment(
        passed=True, contract_sample=False, applicability="not_applicable"
    )
    assert post["status"] == "passed"
    result = reconcile(prior, post)
    assert result["due_state"] == "not_applicable"
    assert result["status"] == "verified"
    assert result["lifecycle_all_phases_passed"] is False


def test_blocked_post_cannot_become_verified_via_not_applicable_claim():
    prior = prior_passed(contract_sample=False)
    post = post_assessment(passed=False, contract_sample=False)
    assert post["status"] == "blocked"
    result = reconcile(prior, post)
    assert result["status"] != "verified"
    assert result["due_state"] != "not_applicable"


def test_different_candidate_is_rejected():
    prior = prior_passed(contract_sample=False, candidate_commit="2" * 40)
    post = post_assessment(
        passed=True, contract_sample=False, candidate_commit="3" * 40
    )
    result = reconcile(prior, post)
    assert result["status"] != "verified"
    assert "post_lifecycle_candidate_mismatch" in result["reasons"]


def test_trigger_target_mismatch_is_rejected():
    prior = prior_passed(contract_sample=False)
    post = post_assessment(passed=True, contract_sample=False)
    result = reconcile(prior, post, trigger_event=trigger(target="other-target"))
    assert result["status"] != "verified"
    assert "post_trigger_target_mismatch" in result["reasons"]


def test_same_phase_is_not_post_stage_progression():
    prior = prior_passed(contract_sample=False, phase="pre_publish")
    post = post_assessment(passed=True, contract_sample=False, phase="pre_publish")
    result = reconcile(prior, post)
    assert result["status"] != "verified"
    assert "post_stage_same_phase_not_progression" in result["reasons"]


def test_unknown_phase_relation_is_rejected():
    prior = prior_passed(contract_sample=False)
    post = post_assessment(passed=True, contract_sample=False, phase="post_publish")
    order = {
        "ref": copy.deepcopy(PHASE_ORDER["ref"]),
        "phases": ["pre_publish", "publish"],
    }
    result = reconcile(prior, post, phase_order=order)
    assert result["status"] != "verified"
    assert "post_phase_order_unknown_phase" in result["reasons"]


def test_incomplete_run_trigger_must_not_force_wait():
    prior = prior_passed(contract_sample=False)
    post = post_assessment(passed=True, contract_sample=False)
    result = reconcile(prior, post, trigger_event=trigger(run_completed=False))
    assert result["status"] != "verified"
    assert "post_trigger_run_not_completed" in result["reasons"]


def test_future_trigger_is_rejected():
    prior = prior_passed(contract_sample=False)
    post = post_assessment(passed=True, contract_sample=False)
    result = reconcile(
        prior,
        post,
        trigger_event=trigger(observed_at="2026-11-17T20:00:00+00:00"),
        evaluated_at=EVALUATED_AT,
    )
    assert result["status"] != "verified"
    assert "post_trigger_observed_in_future" in result["reasons"]


def test_post_assessment_before_trigger_cannot_verify():
    prior = prior_passed(contract_sample=False)
    # Stale post PASS evaluated before the claimed run_completed event.
    post = post_assessment(
        passed=True,
        contract_sample=False,
        evaluated_at="2026-11-16T01:00:00+00:00",
    )
    result = reconcile(prior, post)
    assert result["status"] != "verified"
    assert "post_assessment_before_trigger_event" in result["reasons"]


def test_forged_phase_order_without_authority_cannot_verify():
    prior = prior_passed(contract_sample=False)
    post = post_assessment(passed=True, contract_sample=False)
    forged = {
        "ref": {
            "name": "org/forged-phase-order",
            "version": "9",
            "sha256": "f" * 64,
        },
        "phases": ["pre_publish", "post_publish"],
    }
    result = reconcile(prior, post, phase_order=forged, phase_order_authority=None)
    assert result["status"] != "verified"
    assert "post_phase_order_authority_missing" in result["reasons"]

    rejected = reconcile(
        prior, post, phase_order=forged, phase_order_authority=lambda _ctx: False
    )
    assert rejected["status"] != "verified"
    assert "post_phase_order_authority_rejected" in rejected["reasons"]


def test_missing_trigger_authority_cannot_verify():
    prior = prior_passed(contract_sample=False)
    post = post_assessment(passed=True, contract_sample=False)
    result = reconcile(prior, post, trigger_authority=None)
    assert result["status"] == "unverified"
    assert "post_trigger_authority_missing" in result["reasons"]


def test_missing_run_completed_flag_is_rejected():
    prior = prior_passed(contract_sample=False)
    post = post_assessment(passed=True, contract_sample=False)
    bad = trigger()
    del bad["run_completed"]
    with pytest.raises(
        rap.RequiredAssessmentPostError, match="post_trigger_run_completed_missing"
    ):
        reconcile(prior, post, trigger_event=bad)


def test_authorized_reevaluation_requires_same_phase():
    prior = prior_passed(contract_sample=False, phase="pre_publish")
    mismatched = post_assessment(
        passed=True, contract_sample=False, phase="post_publish"
    )
    bad = reconcile(
        prior,
        mismatched,
        trigger_event=trigger(kind="authorized_reevaluation"),
        disposition_authority=lambda _ctx: {
            "accepted": True,
            "status": "authorized",
            "disposition": "reevaluate",
        },
    )
    assert bad["status"] != "verified"
    assert "post_reevaluation_phase_mismatch" in bad["reasons"]

    same = post_assessment(passed=True, contract_sample=False, phase="pre_publish")
    result = reconcile(
        prior,
        same,
        trigger_event=trigger(kind="authorized_reevaluation"),
        disposition_authority=lambda _ctx: {
            "accepted": True,
            "status": "authorized",
            "disposition": "reevaluate",
        },
    )
    assert result["status"] == "verified"
    assert result["trigger_event"]["kind"] == "authorized_reevaluation"
    assert result["disposition"] == "reevaluate"
    assert result["lifecycle_all_phases_passed"] is False


@pytest.mark.parametrize(
    "prior_sample,post_sample,explicit_sample",
    [(True, False, False), (False, True, False), (True, True, True)],
)
def test_contract_sample_cannot_claim_verified_lifecycle(
    prior_sample, post_sample, explicit_sample
):
    prior = prior_passed(contract_sample=prior_sample)
    post = post_assessment(contract_sample=post_sample)
    assert prior["status"] == post["status"] == "passed"
    result = reconcile(prior, post, contract_sample=explicit_sample)
    assert result["status"] != "verified"
    assert result["contract_sample"] is True
    assert "contract_sample_inputs_not_production_post_stage" in result["reasons"]


@pytest.mark.parametrize(
    "prior_at,post_at,reason",
    [
        (AFTER, BEFORE, "post_prior_assessment_evaluated_in_future"),
        (PRIOR_EVALUATED_AT, AFTER, "post_assessment_evaluated_in_future"),
    ],
)
def test_reevaluation_rejects_future_assessment(prior_at, post_at, reason):
    prior = prior_passed(evaluated_at=prior_at)
    post = post_assessment(phase="pre_publish", evaluated_at=post_at)
    assert prior["status"] == post["status"] == "passed"
    result = reconcile(
        prior, post, trigger_event=trigger(kind="authorized_reevaluation")
    )
    assert result["status"] != "verified"
    assert reason in result["reasons"]


@pytest.mark.parametrize("kind", ["run_completed", "authorized_reevaluation"])
def test_post_before_prior_requires_authorized_historical_reevaluation(kind):
    prior = prior_passed(evaluated_at=EVALUATED_AT)
    phase = "pre_publish" if kind == "authorized_reevaluation" else "post_publish"
    post = post_assessment(phase=phase, evaluated_at=BEFORE)
    result = reconcile(prior, post, trigger_event=trigger(kind=kind))
    assert result["status"] != "verified"
    assert "post_assessment_before_prior_assessment" in result["reasons"]


def test_reevaluation_post_before_event_requires_explicit_historical_authorization():
    prior = prior_passed()
    post = post_assessment(phase="pre_publish", evaluated_at=BEFORE)
    result = reconcile(
        prior, post, trigger_event=trigger(kind="authorized_reevaluation")
    )
    assert result["status"] != "verified"
    assert "post_assessment_before_trigger_event" in result["reasons"]


def test_historical_reevaluation_binds_trigger_and_assessment_identities():
    prior = prior_passed(evaluated_at=EVALUATED_AT)
    post = post_assessment(phase="pre_publish", evaluated_at=BEFORE)
    event = trigger(kind="authorized_reevaluation", historical_as_of=BEFORE)

    def authorize_history(context):
        if (
            context["trigger_event"] != event
            or context["prior_assessment_ref"] != prior["assessment_ref"]
            or context["post_assessment_ref"] != post["assessment_ref"]
            or context["prior_phase"] != "pre_publish"
            or context["post_phase"] != "pre_publish"
            or context["evaluated_at"] != EVALUATED_AT
        ):
            return False
        return {**accept(context), "historical_reevaluation_accepted": True}

    result = reconcile(
        prior, post, trigger_event=event, trigger_authority=authorize_history
    )
    assert result["status"] == "verified"
    assert result["trigger_event"]["historical_as_of"] == BEFORE
    assert result["lifecycle_all_phases_passed"] is False
    assert rap.validate_post_stage(result) is result

    generic_acceptance = reconcile(prior, post, trigger_event=event)
    assert generic_acceptance["status"] != "verified"
    assert "post_historical_reevaluation_unproven" in generic_acceptance["reasons"]

    wrong_attempt = reconcile(
        prior,
        post,
        trigger_event={**event, "run_attempt": 2},
        trigger_authority=authorize_history,
    )
    assert wrong_attempt["status"] != "verified"
    assert "post_trigger_authority_rejected" in wrong_attempt["reasons"]

    wrong_time = reconcile(
        prior,
        post,
        trigger_event={**event, "historical_as_of": EARLIEST},
        trigger_authority=lambda context: {
            **accept(context),
            "historical_reevaluation_accepted": True,
        },
    )
    assert wrong_time["status"] != "verified"
    assert "post_historical_reevaluation_time_mismatch" in wrong_time["reasons"]


def test_validate_rejects_forged_lifecycle_all_phases_true():
    prior = prior_passed(contract_sample=False)
    post = post_assessment(passed=True, contract_sample=False)
    result = reconcile(prior, post)
    forged = copy.deepcopy(result)
    forged["lifecycle_all_phases_passed"] = True
    forged["lifecycle_coverage"] = "complete"
    payload = {key: value for key, value in forged.items() if key != "post_stage_ref"}
    forged["post_stage_ref"] = rap._digest(payload)
    with pytest.raises(
        rap.RequiredAssessmentPostError,
        match="post_lifecycle_all_phases_requires_complete_coverage",
    ):
        rap.validate_post_stage(forged)


def test_validate_rejects_auto_revoke_flag():
    prior = prior_passed(contract_sample=False)
    post = post_assessment(passed=True, contract_sample=False)
    result = reconcile(prior, post)
    forged = copy.deepcopy(result)
    forged["auto_revoke_published"] = True
    payload = {key: value for key, value in forged.items() if key != "post_stage_ref"}
    forged["post_stage_ref"] = rap._digest(payload)
    with pytest.raises(
        rap.RequiredAssessmentPostError, match="post_auto_revoke_forbidden"
    ):
        rap.validate_post_stage(forged)


def test_missing_phase_order_is_rejected():
    prior = prior_passed(contract_sample=False)
    post = post_assessment(passed=True, contract_sample=False)
    with pytest.raises(
        rap.RequiredAssessmentPostError, match="post_phase_order_missing"
    ):
        rap.reconcile_post_stage(
            prior,
            post,
            trigger_event=trigger(),
            due_window=window(),
            evaluated_at=EVALUATED_AT,
            phase_order=None,
            trigger_authority=accept,
            due_window_authority=accept,
        )
