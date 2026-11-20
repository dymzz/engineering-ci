"""D-F02: native #63 T1-a producer to #61 R1 consumer using the same raw evidence."""

import copy

import pytest
from test_historical_reuse import (
    admit as admit_historical,
)
from test_historical_reuse import (
    historical_candidate,
    pre_proof_for,
)
from test_required_assessment import (
    ATTEMPT_QUALIFICATION_REF,
    ATTEMPT_REF,
    EVALUATED_AT,
    JOB_ISSUER_REF,
    PROFILE_REF,
    RULE_SOURCE_REF,
    RULE_TACH,
    RUN_REF,
    RUNNER_REF,
    SOURCE,
    SOURCE_RECEIPT_REF,
    _df02_current_authorization,
    check_obligation,
    check_row,
    coverage,
    df02_source_authority,
    historical_coverage,
    make_admission,
    make_entry,
    make_profile,
    make_projection,
    make_universe,
    native_result,
    report_ref_for,
)

from engineering_ci import (
    admission,
    check_contract,
    expected_checks,
    local_actions_handoff,
    reconciliation,
    required_assessment,
)

IMPLEMENTATION = {
    "implementation_id": "eci.test.tach",
    "implementation_version": "1",
    "implementation_sha256": "d" * 64,
}


def _real_t1_source(*, status="passed", multiple=False, issuer_approved=True):
    check = "docs" if multiple else "tach"
    obligation = f"o/{check}"
    profile = make_profile(
        declared_checks=[check],
        obligations=[{"obligation_ref": obligation, "check_names": [check]}],
    )
    rule = (
        check_contract.rule_ref("docs", 1, digest="a" * 64) if multiple else RULE_TACH
    )
    entry = make_entry(check, required_by=(obligation,), rule_ref=rule)
    expected = make_projection(profile, [entry])
    universe = make_universe(
        profile,
        expected,
        [check_obligation(obligation, [entry["expected_check_ref"]])],
    )
    definition = check_contract.definition_for(check)
    if multiple:
        common = {
            "source": SOURCE,
            "status": "passed",
            "result_origin": "host",
            "native_exit_code": 0,
            "rule_ref": rule,
            "runner_ref": RUNNER_REF,
            "profile_ref": PROFILE_REF,
        }
        inventory = check_contract.native_result(
            definition,
            **common,
            result_instance="inventory",
            evidence={"stdout": "docs/guide.md"},
            tool=check_contract.tool_ref("git/ls-files"),
        )
        links = check_contract.native_result(
            definition,
            **common,
            result_instance="links",
            evidence={"stdout": "ok"},
            tool=check_contract.tool_ref("lychee/docs"),
            input_refs=[
                {"kind": "check_result", "result_id": inventory["result_id"]},
                *entry["input_refs"],
            ],
        )
        results = [inventory, links]
    else:
        results = [native_result(entry, status=status)]
    report = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [
            {
                "name": "reconciled",
                "verification_implementation": copy.deepcopy(IMPLEMENTATION),
                "native_results": results,
            }
        ],
    }
    report_ref = report_ref_for(report)
    job_receipt = {
        "kind": reconciliation.JOB_RECEIPT_KIND,
        "schema_version": reconciliation.JOB_RECEIPT_SCHEMA_VERSION,
        "job_ref": "job/release",
        "run_ref": RUN_REF,
        "attempt_ref": ATTEMPT_REF,
        "runner_ref": RUNNER_REF,
        "report_ref": copy.deepcopy(report_ref),
        "issuer_ref": copy.deepcopy(JOB_ISSUER_REF),
        "verification_status": "upstream_verified",
    }
    observation = {
        "job_ref": job_receipt["job_ref"],
        "run_ref": job_receipt["run_ref"],
        "attempt_ref": job_receipt["attempt_ref"],
        "runner_ref": job_receipt["runner_ref"],
        "report": report,
        "report_ref": copy.deepcopy(report_ref),
        "native_results": results,
        "job_receipt": copy.deepcopy(job_receipt),
    }
    route = {
        "expected_check_ref": entry["expected_check_ref"],
        "job_refs": ["job/release"],
        "job_instances": [
            {
                "job_ref": "job/release",
                "run_ref": RUN_REF,
                "attempt_ref": ATTEMPT_REF,
                "runner_ref": RUNNER_REF,
                "attempt_eligibility": "eligible",
                "qualification_ref": copy.deepcopy(ATTEMPT_QUALIFICATION_REF),
                "report_ref": copy.deepcopy(report_ref),
            }
        ],
        "mapping_status": "routed",
        "scheduler_ref": {"name": "controlled", "version": "1"},
        "effective_config_ref": {"name": "fixture", "version": "1"},
        "reason": None,
    }
    resolution = {
        "expected_check_ref": entry["expected_check_ref"],
        "rule_ref": copy.deepcopy(rule),
        "source_ref": copy.deepcopy(SOURCE),
        "baseline_ref": None,
        "rule_source_ref": copy.deepcopy(RULE_SOURCE_REF),
        "source_receipt_ref": copy.deepcopy(SOURCE_RECEIPT_REF),
    }
    receipt = reconciliation.reconcile_expected_results(
        expected,
        routing=[route],
        resolutions=[resolution],
        observations=[observation],
    )
    policy = {
        "trusted_issuers": [copy.deepcopy(JOB_ISSUER_REF)],
        "approved_implementations": [
            {
                "check_id": entry["check_id"],
                "definition_version": definition.definition_version,
                **IMPLEMENTATION,
            }
        ],
    }
    original = admission.admit_evidence(
        expected,
        receipt,
        reports=[report],
        job_receipts=[job_receipt],
        policy=policy,
        authority=lambda context: (
            issuer_approved
            and context["issuer_ref"] == JOB_ISSUER_REF
            and context["implementation"] == IMPLEMENTATION
        ),
        resolutions=[resolution],
        resolution_authority=lambda context: (
            context["resolution"]["source_receipt_ref"] == SOURCE_RECEIPT_REF
        ),
    )
    return original, universe, expected, entry, receipt, results[0]["result_id"]


def _assess(source, universe, expected, receipt, *, authority, authorization=None):
    return required_assessment.assess_required_obligations(
        universe=universe,
        expected=expected,
        evaluated_at=EVALUATED_AT,
        admission=source,
        receipt=receipt,
        admission_source_authority=authority,
        admission_current_authorization=authorization or _df02_current_authorization(),
        admission_contract_sample=True,
    )


def test_native_t1_producer_pass_reaches_r1_without_forged_gate_fields():
    source, universe, expected, entry, receipt, result_id = _real_t1_source()
    assert source["status"] == "admitted"
    assert source.get("phase") is None
    outcome = _assess(
        source,
        universe,
        expected,
        receipt,
        authority=df02_source_authority(source, universe),
    )
    assert outcome["status"] == "passed"
    row = check_row(outcome, entry)
    assert row["status"] == "satisfied"
    assert row["structural"]["selected_result_ids"] == [result_id]
    assert row["admission"]["source_kind"] == "current_run"
    assert outcome["inputs"]["contract_sample"] is True


def test_native_t1_producer_failure_still_blocks_r1():
    source, universe, expected, entry, receipt, _result_id = _real_t1_source(
        status="failed"
    )
    assert source["status"] != "admitted"
    outcome = _assess(
        source,
        universe,
        expected,
        receipt,
        authority=df02_source_authority(source, universe),
    )
    assert outcome["status"] == "blocked"
    row = check_row(outcome, entry)
    assert row["status"] != "satisfied"
    assert row["admission"]["source_disposition"]["status"] == "unsatisfied"
    assert row["admission"]["source_disposition"]["native_outcome"] == "failed"


def test_native_t1_preserves_an_issuer_denial():
    source, universe, expected, entry, receipt, _ = _real_t1_source(
        issuer_approved=False
    )
    outcome = _assess(
        source,
        universe,
        expected,
        receipt,
        authority=df02_source_authority(source, universe),
    )
    assert outcome["status"] == "blocked"
    disposition = check_row(outcome, entry)["admission"]["source_disposition"]
    assert disposition["status"] == "rejected"
    assert "authority_rejected" in disposition["reasons"]


def test_native_t1_source_requires_independent_authority():
    source, universe, expected, _entry, receipt, _ = _real_t1_source()
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="admission_source_authority_unverified",
    ):
        _assess(source, universe, expected, receipt, authority=lambda _: False)


def test_native_t1_requires_every_selected_receipt_result():
    source, universe, expected, entry, receipt, _ = _real_t1_source(multiple=True)
    selected = receipt["entries"][0]["selected_result_ids"]
    assert len(selected) == 2
    outcome = _assess(
        source,
        universe,
        expected,
        receipt,
        authority=df02_source_authority(source, universe),
    )
    assert outcome["status"] == "passed"
    assert set(check_row(outcome, entry)["evidence_refs"]) == set(selected)

    truncated = copy.deepcopy(source)
    original_entry = truncated["entries"][0]
    dropped = original_entry["result_ids"].pop()
    original_entry["facts"] = [
        fact for fact in original_entry["facts"] if fact["result_id"] != dropped
    ]
    record = next(iter(truncated["accepted_coverage_by_instance"].values()))
    record["result_ids"] = original_entry["result_ids"]
    outcome = _assess(
        truncated,
        universe,
        expected,
        receipt,
        authority=df02_source_authority(truncated, universe),
    )
    assert outcome["status"] == "blocked"
    assert "receipt_witness_missing" in check_row(outcome, entry)["reasons"]

    legacy = make_admission(
        expected,
        coverage_entries=[
            (entry, coverage(expected, entry, selected[0], rule_ref=entry["rule_ref"])),
        ],
    )
    outcome = required_assessment.assess_required_obligations(
        universe=universe,
        expected=expected,
        evaluated_at=EVALUATED_AT,
        admission=legacy,
        receipt=receipt,
    )
    assert outcome["status"] == "passed"


@pytest.mark.parametrize("changed", ["phase", "target_ref", "candidate_digest"])
def test_native_t1_verification_cannot_move_to_a_different_evaluation(changed):
    source, universe, expected, _entry, receipt, _ = _real_t1_source()
    authority = df02_source_authority(source, universe)
    alternate = copy.deepcopy(universe)
    authorization = _df02_current_authorization()
    if changed == "phase":
        authorization[changed] = "post_publish"
        alternate["evaluation"]["phase"] = authorization[changed]
    elif changed == "target_ref":
        authorization[changed] = "refs/heads/other"
        alternate["evaluation"]["refs"][0]["target_ref"] = authorization[changed]
    else:
        authorization[changed] = "sha256:" + "8" * 64
        alternate["evaluation"]["candidate"]["artifact_digest"] = authorization[changed]
    alternate.pop("universe_ref")
    alternate["universe_ref"] = required_assessment._digest(alternate)
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="admission_source_authority_unverified",
    ):
        _assess(
            source,
            alternate,
            expected,
            receipt,
            authority=authority,
            authorization=authorization,
        )


def test_native_t2_producer_reuse_reaches_r1_with_no_current_receipt():
    """Test the actual historical pre-execution comparator and current authority."""
    profile = make_profile()
    entry = make_entry()
    expected = make_projection(profile, [entry])
    universe = make_universe(
        profile,
        expected,
        [check_obligation("o/tach", [entry["expected_check_ref"]])],
    )
    pre_proof = pre_proof_for(entry, source=SOURCE)
    pre_proof["scope_id"] = expected["scope_id"]
    old_results = [native_result(entry), native_result(entry, result_instance="run-2")]
    candidate = historical_candidate(entry, old_results)
    authorization = {
        "phase": "pre_publish",
        "action": "publish",
        "target_ref": "refs/heads/main",
        "candidate_digest": None,
        "profile_sha256": expected["effective_profile"]["profile_sha256"],
    }
    t2 = admit_historical(
        expected,
        pre_proof,
        candidate,
        current_authorization=authorization,
    )
    assert t2["kind"] == admission.HISTORICAL_REUSE_KIND
    assert t2["status"] == "admitted"
    assert t2["receipt_ref"] is None
    admitted = t2["entries"][0]
    selected = admitted["equivalence_basis"]["selected_result_ids"]
    assert len(selected) == 1
    observed = set(admitted["result_ids"])
    assert set(selected) < observed
    outcome = required_assessment.assess_required_obligations(
        universe=universe,
        expected=expected,
        evaluated_at=EVALUATED_AT,
        admission=t2,
        admission_source_authority=df02_source_authority(t2, universe),
        admission_current_authorization={
            "phase": "pre_publish",
            "action": "publish",
            "target_ref": "refs/heads/main",
            "candidate_digest": None,
        },
        admission_contract_sample=True,
    )
    assert outcome["status"] == "passed"
    row = check_row(outcome, entry)
    assert row["admission"]["source_kind"] == "historical_reuse"
    assert row["evidence_refs"] == selected
    assert set(row["admission"]["witness_origins"]) == set(selected)
    assert set(row["admission"]["nonselected_origins"]) == observed - set(selected)
    assert row["structural"]["status"] == "not_supplied"
    assert outcome["inputs"]["admission_observations_reconciled"] is None
    required_assessment.validate_assessment(outcome)

    invalid = copy.deepcopy(t2)
    invalid["entries"][0]["equivalence_basis"]["selected_result_ids"] = [
        "sha256:" + "f" * 64
    ]
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="admission_projection_selected_results_invalid",
    ):
        _assess(
            invalid,
            universe,
            expected,
            None,
            authority=df02_source_authority(invalid, universe),
        )


def _issued_projection(source, universe, expected, receipt, authorization=None):
    authorization = authorization or _df02_current_authorization()
    normalized = required_assessment._validate_universe(universe)
    return admission.project_t1_t2_gate_admission(
        source,
        expected=expected,
        evaluation=required_assessment._projection_evaluation(normalized),
        current_authorization=authorization,
        source_authority=df02_source_authority(source, universe, authorization),
        receipt=receipt,
        contract_sample=True,
    )


def _issued_consumer(source, universe, issued_ref, authorization=None):
    """Confirm the exact projection the issuer recorded, not the request hash."""
    authorization = authorization or _df02_current_authorization()
    source_ref = admission._digest(source)

    def verify(request):
        if (
            request.get("issued_gate_projection_ref") != issued_ref
            or request.get("source_admission_ref") != source_ref
            or request.get("issuer_ref") != JOB_ISSUER_REF
            or request.get("verification_ref") != SOURCE_RECEIPT_REF
            or request["current_evaluation"]["universe_ref"] != universe["universe_ref"]
            or request["current_authorization"] != authorization
        ):
            return None
        return {
            "status": "verified",
            "issued_gate_projection_ref": issued_ref,
            "source_admission_ref": source_ref,
            "current_authorization_ref": request["current_authorization_ref"],
            "issuer_ref": copy.deepcopy(JOB_ISSUER_REF),
            "verification_ref": copy.deepcopy(SOURCE_RECEIPT_REF),
        }

    return verify


def _consume(
    projection,
    universe,
    expected,
    receipt,
    *,
    authority,
    authorization=None,
    evaluated_at=EVALUATED_AT,
):
    return required_assessment.assess_required_obligations(
        universe=universe,
        expected=expected,
        evaluated_at=evaluated_at,
        admission=projection,
        receipt=receipt,
        admission_source_authority=authority,
        admission_current_authorization=authorization or _df02_current_authorization(),
        admission_contract_sample=True,
    )


def test_issued_gate_projection_is_consumed_without_inline_reprojection():
    source, universe, expected, entry, receipt, result_id = _real_t1_source()
    projection = _issued_projection(source, universe, expected, receipt)
    assert projection["phase"] == admission.GATE_PROJECTION_PHASE
    assert projection["source_verification"][
        "source_admission_ref"
    ] == admission._digest(source)
    outcome = _consume(
        projection,
        universe,
        expected,
        receipt,
        authority=_issued_consumer(source, universe, projection["admission_ref"]),
    )
    assert outcome["status"] == "passed"
    proof = outcome["inputs"]["admission_source_verification"]
    assert proof["source_admission_ref"] == admission._digest(source)
    assert proof["issuer_ref"] == JOB_ISSUER_REF
    row = check_row(outcome, entry)
    assert row["status"] == "satisfied"
    assert row["structural"]["selected_result_ids"] == [result_id]
    required_assessment.validate_assessment(outcome)

    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="admission_source_authority_unverified",
    ):
        _consume(
            projection, universe, expected, receipt, authority=lambda _request: True
        )

    def wrong_issuer(request):
        decision = _issued_consumer(source, universe, projection["admission_ref"])(
            request
        )
        if isinstance(decision, dict):
            decision["issuer_ref"] = {
                "name": "other-issuer",
                "version": "1",
                "sha256": "e" * 64,
            }
        return decision

    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="admission_source_authority_unverified",
    ):
        _consume(projection, universe, expected, receipt, authority=wrong_issuer)

    forged = copy.deepcopy(projection)
    forged["source_verification"]["source_admission_ref"] = "sha256:" + "ab" * 32
    forged.pop("admission_ref")
    forged["admission_ref"] = admission._digest(forged)
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="admission_source_authority_unverified",
    ):
        _consume(
            forged,
            universe,
            expected,
            receipt,
            authority=_issued_consumer(source, universe, projection["admission_ref"]),
        )

    tampered = copy.deepcopy(projection)
    tampered["remaining_work"] = [
        *tampered["remaining_work"],
        {
            "scope_id": expected["scope_id"],
            "expected_check_ref": entry["expected_check_ref"],
            "reason": "tampered",
        },
    ]
    with pytest.raises(
        required_assessment.RequiredAssessmentError, match="admission_ref_mismatch"
    ):
        _consume(
            tampered,
            universe,
            expected,
            receipt,
            authority=_issued_consumer(source, universe, projection["admission_ref"]),
        )

    rehashed = copy.deepcopy(projection)
    rehashed["remaining_work"] = [
        *rehashed["remaining_work"],
        {
            "scope_id": expected["scope_id"],
            "expected_check_ref": entry["expected_check_ref"],
            "reason": "rehashed",
        },
    ]
    rehashed.pop("admission_ref")
    rehashed["admission_ref"] = admission._digest(rehashed)
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="admission_source_authority_unverified",
    ):
        _consume(
            rehashed,
            universe,
            expected,
            receipt,
            authority=_issued_consumer(source, universe, projection["admission_ref"]),
        )

    moved = copy.deepcopy(universe)
    moved["evaluation"]["refs"][0]["target_ref"] = "refs/heads/other"
    moved.pop("universe_ref")
    moved["universe_ref"] = required_assessment._digest(
        {key: value for key, value in moved.items() if key != "universe_ref"}
    )
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="admission_scope_mismatch",
    ):
        _consume(
            projection,
            moved,
            expected,
            receipt,
            authority=_issued_consumer(source, moved, projection["admission_ref"]),
            authorization={
                **_df02_current_authorization(),
                "target_ref": "refs/heads/other",
            },
        )
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="admission_authorization_mismatch",
    ):
        _consume(
            projection,
            universe,
            expected,
            receipt,
            authority=_issued_consumer(source, universe, projection["admission_ref"]),
            authorization={
                **_df02_current_authorization(),
                "candidate_digest": "sha256:" + "8" * 64,
            },
        )

    later = _consume(
        projection,
        universe,
        expected,
        receipt,
        authority=_issued_consumer(source, universe, projection["admission_ref"]),
        evaluated_at="2026-11-16T03:00:00Z",
    )
    assert later["status"] == "passed"
    assert later["evaluated_at"].startswith("2026-11-16T03:00:00")

    early = _consume(
        projection,
        universe,
        expected,
        receipt,
        authority=_issued_consumer(source, universe, projection["admission_ref"]),
        evaluated_at="2026-11-15T00:00:00Z",
    )
    assert early["status"] == "blocked"
    assert any(
        "admission_before_authorized_evaluation" in reason
        for reason in early["blocked_reasons"]
    )

    expired_universe = copy.deepcopy(universe)
    expired_universe["authority"]["valid_until"] = "2026-11-16T00:30:00Z"
    expired_universe.pop("universe_ref")
    expired_universe["universe_ref"] = required_assessment._digest(
        {key: value for key, value in expired_universe.items() if key != "universe_ref"}
    )
    expired_projection = _issued_projection(source, expired_universe, expected, receipt)
    expired = _consume(
        expired_projection,
        expired_universe,
        expected,
        receipt,
        authority=_issued_consumer(
            source, expired_universe, expired_projection["admission_ref"]
        ),
    )
    assert expired["status"] == "blocked"
    assert any("profile_expired" in reason for reason in expired["blocked_reasons"])


def _alternate_same_selection_receipt(receipt):
    """A second valid Receipt: same scope and selected results, different identity."""
    alternate = copy.deepcopy(receipt)
    alternate["limitations"] = [
        *alternate.get("limitations", []),
        "alternate_receipt_identity",
    ]
    alternate.pop("receipt_ref", None)
    alternate["receipt_ref"] = reconciliation._digest(alternate)
    reconciliation.validate_receipt(alternate)
    assert alternate["receipt_ref"] != receipt["receipt_ref"]
    assert alternate["scope_id"] == receipt["scope_id"]
    assert (
        alternate["entries"][0]["selected_result_ids"]
        == receipt["entries"][0]["selected_result_ids"]
    )
    return alternate


def test_missing_t1_receipt_stays_a_blocked_assessment():
    source, universe, expected, entry, receipt, _result_id = _real_t1_source()
    projection = _issued_projection(source, universe, expected, receipt)

    def absent(admission, authority):
        outcome = required_assessment.assess_required_obligations(
            universe=universe,
            expected=expected,
            evaluated_at=EVALUATED_AT,
            admission=admission,
            receipt=None,
            admission_source_authority=authority,
            admission_current_authorization=_df02_current_authorization(),
            admission_contract_sample=True,
        )
        assert outcome["status"] == "blocked"
        assert "receipt_missing_for_current_run" in check_row(outcome, entry)["reasons"]
        required_assessment.validate_assessment(outcome)
        return outcome

    absent(source, df02_source_authority(source, universe))
    absent(
        projection,
        _issued_consumer(source, universe, projection["admission_ref"]),
    )


def test_t1_assessment_rejects_a_different_valid_receipt():
    source, universe, expected, entry, receipt, result_id = _real_t1_source()
    alternate = _alternate_same_selection_receipt(receipt)
    projection = _issued_projection(source, universe, expected, receipt)
    issued = _consume(
        projection,
        universe,
        expected,
        receipt,
        authority=_issued_consumer(source, universe, projection["admission_ref"]),
    )
    assert issued["status"] == "passed"
    assert issued["inputs"]["receipt_ref"] == receipt["receipt_ref"]
    assert (
        issued["inputs"]["admission_source_verification"]["source_receipt_ref"]
        == receipt["receipt_ref"]
    )
    assert check_row(issued, entry)["structural"]["selected_result_ids"] == [result_id]
    required_assessment.validate_assessment(issued)

    with pytest.raises(
        required_assessment.RequiredAssessmentError, match="admission_receipt_mismatch"
    ):
        _consume(
            projection,
            universe,
            expected,
            alternate,
            authority=_issued_consumer(source, universe, projection["admission_ref"]),
        )
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="admission_receipt_scope_mismatch",
    ):
        _assess(
            source,
            universe,
            expected,
            alternate,
            authority=df02_source_authority(source, universe),
        )


def test_issued_t2_projection_keeps_unknown_receipt_closure():
    profile = make_profile()
    entry = make_entry()
    expected = make_projection(profile, [entry])
    universe = make_universe(
        profile,
        expected,
        [check_obligation("o/tach", [entry["expected_check_ref"]])],
    )
    pre_proof = pre_proof_for(entry, source=SOURCE)
    pre_proof["scope_id"] = expected["scope_id"]
    old_results = [native_result(entry), native_result(entry, result_instance="run-2")]
    candidate = historical_candidate(entry, old_results)
    authorization = {
        "phase": "pre_publish",
        "action": "publish",
        "target_ref": "refs/heads/main",
        "candidate_digest": None,
        "profile_sha256": expected["effective_profile"]["profile_sha256"],
    }
    t2 = admit_historical(
        expected,
        pre_proof,
        candidate,
        current_authorization=authorization,
    )
    current = {
        "phase": "pre_publish",
        "action": "publish",
        "target_ref": "refs/heads/main",
        "candidate_digest": None,
    }
    projection = _issued_projection(t2, universe, expected, None, current)
    assert projection["all_observations_reconciled"] is None
    assert projection["source_verification"]["source_receipt_ref"] is None
    outcome = _consume(
        projection,
        universe,
        expected,
        None,
        authority=_issued_consumer(t2, universe, projection["admission_ref"], current),
        authorization=current,
    )
    assert outcome["status"] == "passed"
    assert outcome["inputs"]["admission_observations_reconciled"] is None
    assert outcome["inputs"]["receipt_ref"] is None
    row = check_row(outcome, entry)
    assert (
        row["evidence_refs"]
        == t2["entries"][0]["equivalence_basis"]["selected_result_ids"]
    )
    assert set(row["admission"]["nonselected_origins"])
    required_assessment.validate_assessment(outcome)

    altered = copy.deepcopy(projection)
    altered["remaining_work"] = [
        *altered["remaining_work"],
        {
            "scope_id": expected["scope_id"],
            "expected_check_ref": entry["expected_check_ref"],
            "reason": "altered-t2",
        },
    ]
    altered.pop("admission_ref")
    altered["admission_ref"] = admission._digest(altered)
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="admission_source_authority_unverified",
    ):
        _consume(
            altered,
            universe,
            expected,
            None,
            authority=_issued_consumer(
                t2, universe, projection["admission_ref"], current
            ),
            authorization=current,
        )


def _real_t1_with_extra_failure(*, failed_attempt_ref):
    """Build real T1-a evidence with a selected PASS plus an independent verified FAIL."""
    check = "tach"
    obligation = "o/tach"
    profile = make_profile(
        declared_checks=[check],
        obligations=[{"obligation_ref": obligation, "check_names": [check]}],
    )
    entry = make_entry(check, required_by=(obligation,), rule_ref=RULE_TACH)
    expected = make_projection(profile, [entry])
    universe = make_universe(
        profile,
        expected,
        [check_obligation(obligation, [entry["expected_check_ref"]])],
    )
    definition = check_contract.definition_for(check)
    passed_res = native_result(entry, status="passed")
    failed_res = native_result(entry, status="failed", result_instance="failed-inst")

    pass_report = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [
            {
                "name": "reconciled",
                "verification_implementation": copy.deepcopy(IMPLEMENTATION),
                "native_results": [passed_res],
            }
        ],
    }
    fail_report = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [
            {
                "name": "reconciled",
                "verification_implementation": copy.deepcopy(IMPLEMENTATION),
                "native_results": [failed_res],
            }
        ],
    }
    pass_report_ref = report_ref_for(pass_report)
    fail_report_ref = report_ref_for(fail_report)
    pass_job_receipt = {
        "kind": reconciliation.JOB_RECEIPT_KIND,
        "schema_version": reconciliation.JOB_RECEIPT_SCHEMA_VERSION,
        "job_ref": "job/release",
        "run_ref": RUN_REF,
        "attempt_ref": ATTEMPT_REF,
        "runner_ref": RUNNER_REF,
        "report_ref": copy.deepcopy(pass_report_ref),
        "issuer_ref": copy.deepcopy(JOB_ISSUER_REF),
        "verification_status": "upstream_verified",
    }
    fail_job_receipt = {
        "kind": reconciliation.JOB_RECEIPT_KIND,
        "schema_version": reconciliation.JOB_RECEIPT_SCHEMA_VERSION,
        "job_ref": "job/release",
        "run_ref": RUN_REF,
        "attempt_ref": failed_attempt_ref,
        "runner_ref": RUNNER_REF,
        "report_ref": copy.deepcopy(fail_report_ref),
        "issuer_ref": copy.deepcopy(JOB_ISSUER_REF),
        "verification_status": "upstream_verified",
    }
    pass_obs = {
        "job_ref": pass_job_receipt["job_ref"],
        "run_ref": pass_job_receipt["run_ref"],
        "attempt_ref": pass_job_receipt["attempt_ref"],
        "runner_ref": pass_job_receipt["runner_ref"],
        "report": pass_report,
        "report_ref": copy.deepcopy(pass_report_ref),
        "native_results": [passed_res],
        "job_receipt": copy.deepcopy(pass_job_receipt),
    }
    route = {
        "expected_check_ref": entry["expected_check_ref"],
        "job_refs": ["job/release"],
        "job_instances": [
            {
                "job_ref": "job/release",
                "run_ref": RUN_REF,
                "attempt_ref": ATTEMPT_REF,
                "runner_ref": RUNNER_REF,
                "attempt_eligibility": "eligible",
                "qualification_ref": copy.deepcopy(ATTEMPT_QUALIFICATION_REF),
                "report_ref": copy.deepcopy(pass_report_ref),
            }
        ],
        "mapping_status": "routed",
        "scheduler_ref": {"name": "controlled", "version": "1"},
        "effective_config_ref": {"name": "fixture", "version": "1"},
        "reason": None,
    }
    resolution = {
        "expected_check_ref": entry["expected_check_ref"],
        "rule_ref": copy.deepcopy(RULE_TACH),
        "source_ref": copy.deepcopy(SOURCE),
        "baseline_ref": None,
        "rule_source_ref": copy.deepcopy(RULE_SOURCE_REF),
        "source_receipt_ref": copy.deepcopy(SOURCE_RECEIPT_REF),
    }
    receipt = reconciliation.reconcile_expected_results(
        expected,
        routing=[route],
        resolutions=[resolution],
        observations=[pass_obs],
    )
    # Append the independent verified failed observation into receipt observed_results
    # and rehash receipt_ref, mirroring NEW-014's multi-report observation shape.
    updated_receipt = copy.deepcopy(receipt)
    fail_evidence = {
        "result_id": failed_res["result_id"],
        "status": failed_res["status"],
        "result_origin": failed_res["result_origin"],
        "native_exit_code": failed_res.get("native_exit_code"),
        "job_ref": fail_job_receipt["job_ref"],
        "run_ref": fail_job_receipt["run_ref"],
        "attempt_ref": fail_job_receipt["attempt_ref"],
        "runner_ref": fail_job_receipt["runner_ref"],
        "report_ref": copy.deepcopy(fail_report_ref),
        "job_receipt_ref": reconciliation._digest(fail_job_receipt),
        "report_status": None,
        "report_reason": None,
        "report_admissible": True,
    }
    updated_receipt["entries"][0]["observed_result_ids"].append(failed_res["result_id"])
    updated_receipt["entries"][0]["observed_results"].append(fail_evidence)
    updated_receipt.pop("receipt_ref")
    updated_receipt["receipt_ref"] = reconciliation._digest(updated_receipt)

    policy = {
        "trusted_issuers": [copy.deepcopy(JOB_ISSUER_REF)],
        "approved_implementations": [
            {
                "check_id": entry["check_id"],
                "definition_version": definition.definition_version,
                **IMPLEMENTATION,
            }
        ],
    }
    t1 = admission.admit_evidence(
        expected,
        updated_receipt,
        reports=[pass_report, fail_report],
        job_receipts=[pass_job_receipt, fail_job_receipt],
        policy=policy,
        authority=lambda ctx: (
            ctx["issuer_ref"] == JOB_ISSUER_REF
            and ctx["implementation"] == IMPLEMENTATION
        ),
        resolutions=[resolution],
        resolution_authority=lambda ctx: (
            ctx["resolution"]["source_receipt_ref"] == SOURCE_RECEIPT_REF
        ),
    )
    return t1, universe, expected, entry, updated_receipt, failed_res["result_id"]


def test_new014_same_attempt_verified_failure_blocks_t1_and_r1():
    t1, universe, expected, entry, receipt, failed_id = _real_t1_with_extra_failure(
        failed_attempt_ref=ATTEMPT_REF
    )
    assert t1["entries"][0]["admitted"] is False
    assert "same_attempt_authenticated_failure" in t1["entries"][0]["reasons"]
    assert len(t1["remaining_work"]) == 1

    outcome = _assess(
        t1,
        universe,
        expected,
        receipt,
        authority=df02_source_authority(t1, universe),
    )
    assert outcome["status"] == "blocked"
    row = check_row(outcome, entry)
    assert row["status"] == "unmet"
    assert any(item["result_id"] == failed_id for item in row["authenticated_failures"])
    required_assessment.validate_assessment(outcome)


def test_new014_cross_attempt_verified_failure_requires_adjudication_for_r1():
    t1, universe, expected, entry, receipt, failed_id = _real_t1_with_extra_failure(
        failed_attempt_ref="attempt/0"
    )
    assert t1["entries"][0]["admitted"] is True

    blocked = _assess(
        t1,
        universe,
        expected,
        receipt,
        authority=df02_source_authority(t1, universe),
    )
    assert blocked["status"] == "blocked"
    row = check_row(blocked, entry)
    assert row["status"] == "unmet"
    assert any(item["result_id"] == failed_id for item in row["authenticated_failures"])
    required_assessment.validate_assessment(blocked)

    adjudication = {
        "scope_id": expected["scope_id"],
        "expected_check_ref": entry["expected_check_ref"],
        "applies_to_result_ids": [failed_id],
        "disposition": "failure_superseded",
        "authority_ref": {
            "name": "retry-adjudication-authority",
            "version": "1",
            "sha256": "a" * 64,
        },
        "policy_ref": {
            "name": "retry-policy",
            "version": "1",
            "sha256": "b" * 64,
        },
        "decided_at": "2026-11-16T00:50:00Z",
        "valid_until": "2026-11-16T05:00:00Z",
        "revoked_at": None,
        "reason": "superseded_by_attempt_1_pass",
    }

    # Unauthenticated adjudication (authority does not echo adjudications_ref) is rejected.
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="admission_source_authority_unverified",
    ):
        required_assessment.assess_required_obligations(
            universe=universe,
            expected=expected,
            evaluated_at=EVALUATED_AT,
            admission=t1,
            receipt=receipt,
            admission_source_authority=df02_source_authority(t1, universe),
            admission_current_authorization=_df02_current_authorization(),
            admission_adjudications=[adjudication],
            admission_contract_sample=True,
        )

    def authority_with_adjudication(context):
        base = df02_source_authority(t1, universe)(context)
        return {**base, "adjudications_ref": context["adjudications_ref"]}

    passed = required_assessment.assess_required_obligations(
        universe=universe,
        expected=expected,
        evaluated_at=EVALUATED_AT,
        admission=t1,
        receipt=receipt,
        admission_source_authority=authority_with_adjudication,
        admission_current_authorization=_df02_current_authorization(),
        admission_adjudications=[adjudication],
        admission_contract_sample=True,
    )
    assert passed["status"] == "passed"
    assert check_row(passed, entry)["status"] == "satisfied"
    required_assessment.validate_assessment(passed)


def test_t1b_to_t1a_to_r1_controlled_handoff_chain():
    profile = make_profile(
        declared_checks=["tach"],
        obligations=[{"obligation_ref": "o/tach", "check_names": ["tach"]}],
    )
    entry = make_entry("tach", required_by=("o/tach",), rule_ref=RULE_TACH)
    expected = make_projection(profile, [entry])
    universe = make_universe(
        profile,
        expected,
        [check_obligation("o/tach", [entry["expected_check_ref"]])],
    )
    definition = check_contract.definition_for("tach")
    passed_res = native_result(entry, status="passed")
    report = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [
            {
                "name": "reconciled",
                "verification_implementation": copy.deepcopy(IMPLEMENTATION),
                "native_results": [passed_res],
            }
        ],
    }
    report_ref = report_ref_for(report)
    job_receipt = {
        "kind": reconciliation.JOB_RECEIPT_KIND,
        "schema_version": reconciliation.JOB_RECEIPT_SCHEMA_VERSION,
        "job_ref": "job/local",
        "run_ref": RUN_REF,
        "attempt_ref": ATTEMPT_REF,
        "runner_ref": RUNNER_REF,
        "scope_id": expected["scope_id"],
        "expected_set_ref": expected["expected_set_ref"],
        "report_ref": copy.deepcopy(report_ref),
        "issuer_ref": copy.deepcopy(JOB_ISSUER_REF),
        "verification_status": "upstream_verified",
    }
    package = local_actions_handoff.build_local_handoff_package(
        payload={
            "reports": [report],
            "job_receipts": [job_receipt],
            "result_ids": [passed_res["result_id"]],
            "source_digests": [SOURCE["content_digest"]],
            "invocation_ref": "inv/local-1",
            "event_payload_ref": "evt/local-1",
        },
        issuer_ref=JOB_ISSUER_REF,
        execution_boundary={
            "kind": "controlled_local_environment",
            "collector_ref": {"name": "collector", "version": "1", "sha256": "c" * 64},
            "controlled": True,
            "self_reported": False,
            "proof_ref": {"name": "attest", "version": "1", "sha256": "1" * 64},
        },
        producer_authorization={
            "authority_ref": {"name": "grant", "version": "1", "sha256": "e" * 64},
            "producer_ref": {"name": "local-cli", "version": "1", "sha256": "d" * 64},
            "scope": {
                "action": "publish",
                "target_ref": "refs/heads/main",
                "phase": "pre_publish",
                "check_ids": [entry["check_id"]],
                "scope_id": expected["scope_id"],
                "expected_set_ref": expected["expected_set_ref"],
            },
            "valid_from": "2026-11-01T00:00:00Z",
            "valid_until": "2026-12-01T00:00:00Z",
            "revoked_at": None,
        },
        produced_at="2026-11-16T00:30:00Z",
    )
    verified = local_actions_handoff.verify_local_handoff(
        package,
        handoff_policy={
            "allowed_issuers": [copy.deepcopy(JOB_ISSUER_REF)],
            "allowed_execution_boundaries": ["controlled_local_environment"],
        },
        consumer_scope={
            "action": "publish",
            "target_ref": "refs/heads/main",
            "phase": "pre_publish",
            "check_ids": [entry["check_id"]],
            "scope_id": expected["scope_id"],
            "expected_set_ref": expected["expected_set_ref"],
        },
        issuer_authority=lambda _pkg: True,
        execution_authority=lambda pkg: {
            "accepted": True,
            "executed_at": pkg["produced_at"],
        },
        authorization_authority=lambda ctx: {
            "accepted": True,
            "handoff_ref": ctx["handoff_ref"],
            "consumer_scope_ref": ctx["consumer_scope_ref"],
        },
        evaluated_at=EVALUATED_AT,
    )
    assert verified["status"] == "verified"
    assert verified["admitted_for_t1a"] is True
    assert verified["remaining_work"] == []

    # Feed verified T1-b raw materials through #66-C -> T1-a -> #61 R1.
    handed_report = verified["materials"]["reports"][0]
    handed_job_receipt = verified["materials"]["job_receipts"][0]
    receipt = reconciliation.reconcile_expected_results(
        expected,
        routing=[
            {
                "expected_check_ref": entry["expected_check_ref"],
                "job_refs": ["job/local"],
                "job_instances": [
                    {
                        "job_ref": "job/local",
                        "run_ref": RUN_REF,
                        "attempt_ref": ATTEMPT_REF,
                        "runner_ref": RUNNER_REF,
                        "attempt_eligibility": "eligible",
                        "qualification_ref": copy.deepcopy(ATTEMPT_QUALIFICATION_REF),
                        "report_ref": copy.deepcopy(report_ref),
                    }
                ],
                "mapping_status": "routed",
                "scheduler_ref": {"name": "controlled", "version": "1"},
                "effective_config_ref": {"name": "fixture", "version": "1"},
                "reason": None,
            }
        ],
        resolutions=[
            {
                "expected_check_ref": entry["expected_check_ref"],
                "rule_ref": copy.deepcopy(RULE_TACH),
                "source_ref": copy.deepcopy(SOURCE),
                "baseline_ref": None,
                "rule_source_ref": copy.deepcopy(RULE_SOURCE_REF),
                "source_receipt_ref": copy.deepcopy(SOURCE_RECEIPT_REF),
            }
        ],
        observations=[
            {
                "job_ref": "job/local",
                "run_ref": RUN_REF,
                "attempt_ref": ATTEMPT_REF,
                "runner_ref": RUNNER_REF,
                "report": handed_report,
                "report_ref": copy.deepcopy(report_ref),
                "native_results": [passed_res],
                "job_receipt": handed_job_receipt,
            }
        ],
    )
    t1 = admission.admit_evidence(
        expected,
        receipt,
        reports=verified["materials"]["reports"],
        job_receipts=verified["materials"]["job_receipts"],
        policy={
            "trusted_issuers": [copy.deepcopy(JOB_ISSUER_REF)],
            "approved_implementations": [
                {
                    "check_id": entry["check_id"],
                    "definition_version": definition.definition_version,
                    **IMPLEMENTATION,
                }
            ],
        },
        authority=lambda ctx: (
            ctx["issuer_ref"] == JOB_ISSUER_REF
            and ctx["implementation"] == IMPLEMENTATION
        ),
        resolutions=[
            {
                "expected_check_ref": entry["expected_check_ref"],
                "rule_ref": copy.deepcopy(RULE_TACH),
                "source_ref": copy.deepcopy(SOURCE),
                "baseline_ref": None,
                "rule_source_ref": copy.deepcopy(RULE_SOURCE_REF),
                "source_receipt_ref": copy.deepcopy(SOURCE_RECEIPT_REF),
            }
        ],
        resolution_authority=lambda ctx: (
            ctx["resolution"]["source_receipt_ref"] == SOURCE_RECEIPT_REF
        ),
    )
    outcome = _assess(
        t1,
        universe,
        expected,
        receipt,
        authority=df02_source_authority(t1, universe),
    )
    assert outcome["status"] == "passed"
    assert check_row(outcome, entry)["status"] == "satisfied"
    required_assessment.validate_assessment(outcome)


def test_dg01_authenticated_phase_and_universe_issuer_reach_r1_pass():
    profile = make_profile(
        phase="pre_publish",
        declared_checks=["tach"],
        obligations=[{"obligation_ref": "o/tach", "check_names": ["tach"]}],
    )
    entry = make_entry("tach", required_by=("o/tach",), rule_ref=RULE_TACH)
    raw_expected = make_projection(profile, [entry])
    # Simulate #66-B2 unverified phase projection (status=partial, reason=effective_profile_phase_unverified).
    raw_expected["status"] = "partial"
    raw_expected["reason"] = "effective_profile_phase_unverified"
    raw_expected["consumer_completeness"] = "incomplete_profile"
    raw_expected["definitions_status"] = "domain_complete"
    raw_expected["applicability_status"] = "domain_complete"

    # D-F01 invariant: incomplete domain must NOT be promoted to complete by phase authentication.
    incomplete_profile = make_profile(
        phase="pre_publish",
        domain_complete=False,
        declared_checks=["tach"],
        obligations=[{"obligation_ref": "o/tach", "check_names": ["tach"]}],
    )
    incomplete_expected = make_projection(incomplete_profile, [entry])
    incomplete_expected["status"] = "partial"
    incomplete_expected["reason"] = "effective_profile_phase_unverified"
    incomplete_expected["consumer_completeness"] = "incomplete_profile"

    def b2_phase_issuer(ctx):
        return {
            "status": "verified",
            "scope_id": ctx["scope_id"],
            "expected_set_ref": ctx["expected_set_ref"],
            "phase": ctx["phase"],
            "profile_sha256": ctx["profile_sha256"],
            "issuer_ref": {"name": "phase-issuer", "version": "1", "sha256": "a" * 64},
            "verification_ref": {
                "name": "phase-proof",
                "version": "1",
                "sha256": "b" * 64,
            },
        }

    still_partial = expected_checks.authenticate_phase_projection(
        incomplete_expected, phase_authority=b2_phase_issuer
    )
    assert still_partial["phase_status"] == "verified"
    assert still_partial["status"] == "partial"

    # Complete domain + authenticated phase promotes cleanly to complete.
    expected = expected_checks.authenticate_phase_projection(
        raw_expected, phase_authority=b2_phase_issuer
    )
    assert expected["phase_status"] == "verified"
    assert expected["status"] == "complete"
    assert expected["consumer_completeness"] == "expected_domain_complete"

    universe = make_universe(
        profile,
        expected,
        [check_obligation("o/tach", [entry["expected_check_ref"]])],
        phase="pre_publish",
    )
    adm = make_admission(
        expected,
        coverage_entries=[(entry, historical_coverage(expected, entry))],
    )

    # Without phase_authority in R1, even a phase_proof-bearing projection fails closed.
    without_verifier = required_assessment.assess_required_obligations(
        universe=universe,
        expected=expected,
        evaluated_at=EVALUATED_AT,
        admission=adm,
    )
    assert without_verifier["status"] == "blocked"
    assert (
        "expected_phase_proof_unverified"
        in without_verifier["verification"]["check_domain"]["reasons"]
    )

    def r1_phase_authority(ctx):
        return {
            "status": "verified",
            "scope_id": ctx["scope_id"],
            "expected_set_ref": ctx["expected_set_ref"],
            "phase": ctx["phase"],
            "profile_sha256": ctx["profile_sha256"],
            "universe_ref": ctx["universe_ref"],
            "proof_ref": ctx["proof_ref"],
            "issuer_ref": {"name": "phase-issuer", "version": "1", "sha256": "a" * 64},
            "verification_ref": {
                "name": "r1-phase-check",
                "version": "1",
                "sha256": "c" * 64,
            },
        }

    def r1_universe_authority(ctx):
        return {
            "status": "verified",
            "universe_ref": ctx["universe_ref"],
            "profile_sha256": ctx["profile_sha256"],
            "scope_id": ctx["scope_id"],
            "expected_set_ref": ctx["expected_set_ref"],
            "issuer_ref": {
                "name": "governance-issuer",
                "version": "1",
                "sha256": "d" * 64,
            },
            "verification_ref": {
                "name": "closure-attest",
                "version": "1",
                "sha256": "e" * 64,
            },
        }

    passed = required_assessment.assess_required_obligations(
        universe=universe,
        expected=expected,
        evaluated_at=EVALUATED_AT,
        admission=adm,
        phase_authority=r1_phase_authority,
        universe_authority=r1_universe_authority,
    )
    assert passed["status"] == "passed"
    assert passed["inputs"]["phase_verification"]["status"] == "verified"
    assert passed["inputs"]["universe_source_verification"]["status"] == "verified"
    required_assessment.validate_assessment(passed)

    # Rejected universe_authority blocks both authority and obligations_universe.
    rejected_universe = required_assessment.assess_required_obligations(
        universe=universe,
        expected=expected,
        evaluated_at=EVALUATED_AT,
        admission=adm,
        phase_authority=r1_phase_authority,
        universe_authority=lambda _ctx: {"status": "rejected"},
    )
    assert rejected_universe["status"] == "blocked"
    assert (
        "universe_issuer_unverified"
        in rejected_universe["verification"]["authority"]["reasons"]
    )
    assert (
        "universe_issuer_unverified"
        in rejected_universe["verification"]["obligations_universe"]["reasons"]
    )


def test_new031_new032_new026_r1_temporal_causality_and_serialized_validation():
    profile = make_profile(
        declared_checks=["tach"],
        obligations=[{"obligation_ref": "o/tach", "check_names": ["tach"]}],
    )
    entry = make_entry("tach", required_by=("o/tach",), rule_ref=RULE_TACH)
    expected = make_projection(profile, [entry])
    adm = make_admission(
        expected,
        coverage_entries=[(entry, historical_coverage(expected, entry))],
    )

    # NEW-031: Universe evaluated after assessment time blocks.
    future_eval_universe = make_universe(
        profile,
        expected,
        [check_obligation("o/tach", [entry["expected_check_ref"]])],
    )
    future_eval_universe["evaluation"]["evaluated_at"] = "2026-11-16T02:00:00Z"
    future_eval_universe.pop("universe_ref")
    future_eval_universe["universe_ref"] = required_assessment._digest(
        future_eval_universe
    )
    new031 = required_assessment.assess_required_obligations(
        universe=future_eval_universe,
        expected=expected,
        evaluated_at=EVALUATED_AT,
        admission=adm,
    )
    assert new031["status"] == "blocked"
    assert (
        "universe_evaluated_after_assessment"
        in new031["verification"]["authority"]["reasons"]
    )

    # NEW-032: Profile approved_at after assessment time blocks.
    future_approved_universe = make_universe(
        profile,
        expected,
        [check_obligation("o/tach", [entry["expected_check_ref"]])],
    )
    future_approved_universe["authority"]["approved_at"] = "2026-11-16T02:00:00Z"
    future_approved_universe.pop("universe_ref")
    future_approved_universe["universe_ref"] = required_assessment._digest(
        future_approved_universe
    )
    new032 = required_assessment.assess_required_obligations(
        universe=future_approved_universe,
        expected=expected,
        evaluated_at=EVALUATED_AT,
        admission=adm,
    )
    assert new032["status"] == "blocked"
    assert "profile_not_yet_approved" in new032["verification"]["authority"]["reasons"]

    # NEW-026: Serialized passed assessment with tampered future authority time fails validate_assessment.
    valid_universe = make_universe(
        profile,
        expected,
        [check_obligation("o/tach", [entry["expected_check_ref"]])],
    )
    passed = required_assessment.assess_required_obligations(
        universe=valid_universe,
        expected=expected,
        evaluated_at=EVALUATED_AT,
        admission=adm,
    )
    assert passed["status"] == "passed"
    for field in ("approved_at", "effective_at", "evaluated_at"):
        tampered = copy.deepcopy(passed)
        tampered["inputs"]["authority"][field] = "2026-11-16T09:00:00+00:00"
        tampered.pop("assessment_ref")
        tampered["assessment_ref"] = required_assessment._digest(tampered)
        with pytest.raises(
            required_assessment.RequiredAssessmentError,
            match="assessment_verification_inconsistent",
        ):
            required_assessment.validate_assessment(tampered)
