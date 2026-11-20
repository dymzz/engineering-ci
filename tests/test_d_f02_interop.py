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


def _real_t1_with_extra_failure(*, failed_attempt_ref, shared_report=False):
    """Build real T1-a evidence with a selected PASS plus a verified FAIL."""
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

    if shared_report:
        pass_report = {
            "kind": reconciliation.EXECUTION_REPORT_KIND,
            "schema_version": 1,
            "checks": [
                {
                    "name": "reconciled",
                    "verification_implementation": copy.deepcopy(IMPLEMENTATION),
                    "native_results": [passed_res, failed_res],
                }
            ],
        }
        fail_report = pass_report
    else:
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
    fail_job_receipt = (
        pass_job_receipt
        if shared_report
        else {
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
    )
    pass_obs = {
        "job_ref": pass_job_receipt["job_ref"],
        "run_ref": pass_job_receipt["run_ref"],
        "attempt_ref": pass_job_receipt["attempt_ref"],
        "runner_ref": pass_job_receipt["runner_ref"],
        "report": pass_report,
        "report_ref": copy.deepcopy(pass_report_ref),
        "native_results": [passed_res, failed_res] if shared_report else [passed_res],
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
    updated_receipt = copy.deepcopy(receipt)
    if not shared_report:
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
        updated_receipt["entries"][0]["observed_result_ids"].append(
            failed_res["result_id"]
        )
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
    reports = [pass_report] if shared_report else [pass_report, fail_report]
    job_receipts = (
        [pass_job_receipt] if shared_report else [pass_job_receipt, fail_job_receipt]
    )
    t1 = admission.admit_evidence(
        expected,
        updated_receipt,
        reports=reports,
        job_receipts=job_receipts,
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


def test_new014_same_attempt_independent_report_does_not_contaminate_witness_or_r1():
    t1, universe, expected, entry, receipt, failed_id = _real_t1_with_extra_failure(
        failed_attempt_ref=ATTEMPT_REF, shared_report=False
    )
    assert t1["entries"][0]["admitted"] is True
    assert t1["remaining_work"] == []

    outcome = _assess(
        t1,
        universe,
        expected,
        receipt,
        authority=df02_source_authority(t1, universe),
    )
    assert outcome["status"] == "passed"
    row = check_row(outcome, entry)
    assert row["status"] == "satisfied"
    assert row["authenticated_failures"] == []
    assert failed_id in row["admission"]["nonselected_origins"]
    required_assessment.validate_assessment(outcome)


def test_new014_shared_report_verified_failure_blocks_t1_and_r1():
    t1, universe, expected, entry, receipt, failed_id = _real_t1_with_extra_failure(
        failed_attempt_ref=ATTEMPT_REF, shared_report=True
    )
    assert t1["entries"][0]["admitted"] is False
    assert "shared_report_authenticated_failure" in t1["entries"][0]["reasons"]
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


def test_new014_same_job_attempt_independent_checks_isolate_in_r1():
    rule_vulture = check_contract.rule_ref("vulture", 1, digest="f" * 64)
    profile = make_profile(
        declared_checks=["tach", "vulture"],
        obligations=[
            {"obligation_ref": "o/tach", "check_names": ["tach"]},
            {"obligation_ref": "o/vulture", "check_names": ["vulture"]},
        ],
    )
    tach_entry = make_entry("tach", required_by=("o/tach",), rule_ref=RULE_TACH)
    vulture_entry = make_entry(
        "vulture", required_by=("o/vulture",), rule_ref=rule_vulture
    )
    expected = make_projection(profile, [tach_entry, vulture_entry])
    universe = make_universe(
        profile,
        expected,
        [
            check_obligation("o/tach", [tach_entry["expected_check_ref"]]),
            {
                **check_obligation("o/vulture", [vulture_entry["expected_check_ref"]]),
                "check_names": ["vulture"],
            },
        ],
    )
    tach_pass = native_result(tach_entry, status="passed")
    vulture_def = check_contract.definition_for("vulture")
    vulture_fail = check_contract.native_result(
        vulture_def,
        source=copy.deepcopy(SOURCE),
        status="failed",
        result_origin="host",
        result_instance="run-1",
        native_exit_code=1,
        evidence={"stdout": "unused"},
        rule_ref=copy.deepcopy(rule_vulture),
        baseline_ref=None,
        input_refs=[dict(item) for item in vulture_entry.get("input_refs", ())],
        coverage=None,
        phase=None,
        runner_ref=RUNNER_REF,
        profile_ref=copy.deepcopy(PROFILE_REF),
        tool=check_contract.tool_ref("vulture/dead-code"),
    )
    report = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [
            {
                "name": "tach",
                "verification_implementation": copy.deepcopy(IMPLEMENTATION),
                "native_results": [tach_pass],
            },
            {
                "name": "vulture",
                "verification_implementation": copy.deepcopy(IMPLEMENTATION),
                "native_results": [vulture_fail],
            },
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
    routing = [
        {
            "expected_check_ref": item["expected_check_ref"],
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
        for item in (tach_entry, vulture_entry)
    ]
    resolutions = [
        {
            "expected_check_ref": tach_entry["expected_check_ref"],
            "rule_ref": copy.deepcopy(RULE_TACH),
            "source_ref": copy.deepcopy(SOURCE),
            "baseline_ref": None,
            "rule_source_ref": copy.deepcopy(RULE_SOURCE_REF),
            "source_receipt_ref": copy.deepcopy(SOURCE_RECEIPT_REF),
        },
        {
            "expected_check_ref": vulture_entry["expected_check_ref"],
            "rule_ref": copy.deepcopy(rule_vulture),
            "source_ref": copy.deepcopy(SOURCE),
            "baseline_ref": None,
            "rule_source_ref": copy.deepcopy(RULE_SOURCE_REF),
            "source_receipt_ref": copy.deepcopy(SOURCE_RECEIPT_REF),
        },
    ]
    receipt = reconciliation.reconcile_expected_results(
        expected,
        routing=routing,
        resolutions=resolutions,
        observations=[
            {
                "job_ref": "job/release",
                "run_ref": RUN_REF,
                "attempt_ref": ATTEMPT_REF,
                "runner_ref": RUNNER_REF,
                "report": report,
                "report_ref": copy.deepcopy(report_ref),
                "native_results": [tach_pass, vulture_fail],
                "job_receipt": copy.deepcopy(job_receipt),
            }
        ],
    )
    policy = {
        "trusted_issuers": [copy.deepcopy(JOB_ISSUER_REF)],
        "approved_implementations": [
            {
                "check_id": tach_entry["check_id"],
                "definition_version": tach_entry["definition_version"],
                **IMPLEMENTATION,
            },
            {
                "check_id": vulture_entry["check_id"],
                "definition_version": vulture_entry["definition_version"],
                **IMPLEMENTATION,
            },
        ],
    }
    t1 = admission.admit_evidence(
        expected,
        receipt,
        reports=[report],
        job_receipts=[job_receipt],
        policy=policy,
        authority=lambda ctx: (
            ctx["issuer_ref"] == JOB_ISSUER_REF
            and ctx["implementation"] == IMPLEMENTATION
        ),
        resolutions=resolutions,
        resolution_authority=lambda ctx: (
            ctx["resolution"]["source_receipt_ref"] == SOURCE_RECEIPT_REF
        ),
    )
    assert t1["status"] == "partial"
    outcome = _assess(
        t1,
        universe,
        expected,
        receipt,
        authority=df02_source_authority(t1, universe),
    )
    assert outcome["status"] == "blocked"
    tach_row = check_row(outcome, tach_entry)
    vulture_row = check_row(outcome, vulture_entry)
    assert tach_row["status"] == "satisfied"
    assert tach_row["authenticated_failures"] == []
    assert vulture_row["status"] == "unmet"
    assert [f["result_id"] for f in vulture_row["authenticated_failures"]] == [
        vulture_fail["result_id"]
    ]
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


def test_dg01_non_sample_requires_universe_authority_and_closed_governance():
    profile = make_profile(
        declared_checks=["tach"],
        obligations=[{"obligation_ref": "o/tach", "check_names": ["tach"]}],
    )
    entry = make_entry("tach", required_by=("o/tach",), rule_ref=RULE_TACH)
    expected = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(
        profile,
        expected,
        [obligation],
        contract_sample=False,
    )
    adm = make_admission(
        expected,
        coverage_entries=[(entry, historical_coverage(expected, entry))],
        contract_sample=False,
    )

    def r1_universe_authority(ctx):
        return {
            "status": "verified",
            "universe_ref": ctx["universe_ref"],
            "profile_ref": ctx["profile"]["profile_ref"],
            "profile_revision": ctx["profile"]["profile_revision"],
            "approval_source": ctx["profile"]["approval_source"],
            "profile_sha256": ctx["profile_sha256"],
            "approval_digest": ctx["profile"]["approval_digest"],
            "authority_source_ref": ctx["authority"]["source_ref"],
            "closure_ref": ctx["governance_domain"]["closure_ref"],
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

    # 1. Non-sample self-reported complete/verified Universe WITHOUT universe_authority must block.
    missing_auth = required_assessment.assess_required_obligations(
        universe=universe,
        expected=expected,
        evaluated_at=EVALUATED_AT,
        admission=adm,
        universe_authority=None,
    )
    assert missing_auth["status"] == "blocked"
    assert missing_auth["inputs"]["contract_sample"] is False
    assert "universe_source_verification" not in missing_auth["inputs"]
    assert missing_auth["verification"]["authority"]["status"] == "unverified"
    assert (
        "universe_issuer_authority_missing"
        in missing_auth["verification"]["authority"]["reasons"]
    )
    assert (
        missing_auth["verification"]["obligations_universe"]["status"] == "unverified"
    )
    assert (
        "universe_issuer_authority_missing"
        in missing_auth["verification"]["obligations_universe"]["reasons"]
    )
    required_assessment.validate_assessment(missing_auth)

    # 2. Legitimate single closed Profile + controlled universe_authority passes non-sample R1.
    proof = required_assessment.authenticate_obligations_universe(
        universe,
        expected=expected,
        universe_authority=r1_universe_authority,
        evaluated_at=EVALUATED_AT,
    )
    assert proof["status"] == "verified"
    assert proof["profile_sha256"] == profile["profile_sha256"]
    assert proof["profile_revision"] == profile["profile_revision"]

    passed = required_assessment.assess_required_obligations(
        universe=universe,
        expected=expected,
        evaluated_at=EVALUATED_AT,
        admission=adm,
        universe_authority=r1_universe_authority,
    )
    assert passed["status"] == "passed"
    assert passed["inputs"]["contract_sample"] is False
    assert passed["inputs"]["universe_source_verification"] == proof
    required_assessment.validate_assessment(passed)

    # 3. Forged non-sample PASS without universe_source_verification is rejected by validate_assessment.
    forged_no_proof = copy.deepcopy(passed)
    forged_no_proof["inputs"].pop("universe_source_verification")
    forged_no_proof.pop("assessment_ref")
    forged_no_proof["assessment_ref"] = required_assessment._digest(forged_no_proof)
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="assessment_universe_source_missing",
    ):
        required_assessment.validate_assessment(forged_no_proof)

    # 4. Unclosed governance domain, omitted delegation domain, revoked profile,
    #    profile revision drift, and out-of-scope obligation target must all block
    #    even when universe_authority is provided.
    unclosed = make_universe(
        profile,
        expected,
        [obligation],
        contract_sample=False,
        closure_status="incomplete",
    )
    unclosed_res = required_assessment.assess_required_obligations(
        universe=unclosed,
        expected=expected,
        evaluated_at=EVALUATED_AT,
        admission=adm,
        universe_authority=r1_universe_authority,
    )
    assert unclosed_res["status"] == "blocked"
    assert (
        "governance_domain_not_closed"
        in unclosed_res["verification"]["obligations_universe"]["reasons"]
    )
    assert "universe_source_verification" not in unclosed_res["inputs"]
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="governance_domain_not_closed",
    ):
        required_assessment.authenticate_obligations_universe(
            unclosed,
            expected=expected,
            universe_authority=r1_universe_authority,
            evaluated_at=EVALUATED_AT,
        )

    omitted_delegation = make_universe(
        profile,
        expected,
        [obligation],
        contract_sample=False,
        delegation_domains=[
            {
                "domain_ref": {
                    "name": "delegated-sec",
                    "version": "1",
                    "sha256": "7" * 64,
                },
                "authority_ref": {
                    "name": "sec-authority",
                    "version": "1",
                    "sha256": "8" * 64,
                },
                "included": False,
                "closure_status": "incomplete",
            }
        ],
    )
    delegation_res = required_assessment.assess_required_obligations(
        universe=omitted_delegation,
        expected=expected,
        evaluated_at=EVALUATED_AT,
        admission=adm,
        universe_authority=r1_universe_authority,
    )
    assert delegation_res["status"] == "blocked"
    assert (
        "governance_domain_delegation_incomplete"
        in delegation_res["verification"]["obligations_universe"]["reasons"]
    )
    assert "universe_source_verification" not in delegation_res["inputs"]

    revoked = make_universe(
        profile,
        expected,
        [obligation],
        contract_sample=False,
        revoked_at="2026-11-16T00:20:00Z",
    )
    revoked_res = required_assessment.assess_required_obligations(
        universe=revoked,
        expected=expected,
        evaluated_at=EVALUATED_AT,
        admission=adm,
        universe_authority=r1_universe_authority,
    )
    assert revoked_res["status"] == "blocked"
    assert "profile_revoked" in revoked_res["verification"]["authority"]["reasons"]
    assert "universe_source_verification" not in revoked_res["inputs"]

    drifted = copy.deepcopy(universe)
    drifted["profile"]["profile_revision"] = "99"
    drifted.pop("universe_ref")
    drifted["universe_ref"] = required_assessment._digest(drifted)
    drifted_res = required_assessment.assess_required_obligations(
        universe=drifted,
        expected=expected,
        evaluated_at=EVALUATED_AT,
        admission=adm,
        universe_authority=r1_universe_authority,
    )
    assert drifted_res["status"] == "blocked"
    assert (
        "profile_binding_mismatch"
        in drifted_res["verification"]["obligations_universe"]["reasons"]
    )
    assert "universe_source_verification" not in drifted_res["inputs"]

    out_of_scope_ob = check_obligation(
        "o/tach",
        [entry["expected_check_ref"]],
        target_ref="refs/heads/unauthorized",
    )
    out_of_scope = make_universe(
        profile,
        expected,
        [out_of_scope_ob],
        contract_sample=False,
    )
    out_of_scope_res = required_assessment.assess_required_obligations(
        universe=out_of_scope,
        expected=expected,
        evaluated_at=EVALUATED_AT,
        admission=adm,
        universe_authority=r1_universe_authority,
    )
    assert out_of_scope_res["status"] == "blocked"
    assert (
        "obligation_target_out_of_scope"
        in out_of_scope_res["verification"]["obligations_universe"]["reasons"]
    )
    assert "universe_source_verification" not in out_of_scope_res["inputs"]


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


def _prerequisite_e2e_chain(
    *,
    prereq_status="passed",
    with_prereq_receipt=True,
    prereq_issuer=JOB_ISSUER_REF,
    prereq_attempt=ATTEMPT_REF,
    consumed_id=None,
    sibling_required=False,
    sibling_routed=False,
):
    """Build real #66-B2 -> #66-C -> #63 T1-a -> Projection -> #61 R1 prerequisite chain."""
    checks_decl = ["tach", "docs"] if sibling_required else ["tach"]
    obs_decl = [{"obligation_ref": "o/tach", "check_names": ["tach"]}]
    if sibling_required:
        obs_decl.append({"obligation_ref": "o/docs", "check_names": ["docs"]})
    profile = make_profile(declared_checks=checks_decl, obligations=obs_decl)
    prereq_rule = check_contract.rule_ref("docs", 1, digest="a" * 64)
    prereq_res = check_contract.native_result(
        check_contract.definition_for("docs"),
        source=copy.deepcopy(SOURCE),
        status=prereq_status,
        result_origin="host",
        result_instance="prereq-inv",
        native_exit_code=0 if prereq_status == "passed" else 1,
        evidence={"stdout": "docs/guide.md"},
        rule_ref=copy.deepcopy(prereq_rule),
        runner_ref=RUNNER_REF,
        profile_ref=copy.deepcopy(PROFILE_REF),
        tool=check_contract.tool_ref("git/ls-files"),
    )
    prereq_report = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [
            {
                "name": "docs-inventory",
                "status": prereq_status,
                "verification_implementation": copy.deepcopy(IMPLEMENTATION),
                "native_results": [prereq_res],
            }
        ],
    }
    prereq_report_ref = report_ref_for(prereq_report)
    prereq_job_receipt = {
        "kind": reconciliation.JOB_RECEIPT_KIND,
        "schema_version": reconciliation.JOB_RECEIPT_SCHEMA_VERSION,
        "job_ref": "job/prereq",
        "run_ref": RUN_REF,
        "attempt_ref": prereq_attempt,
        "runner_ref": RUNNER_REF,
        "report_ref": copy.deepcopy(prereq_report_ref),
        "issuer_ref": copy.deepcopy(prereq_issuer),
        "verification_status": "upstream_verified",
    }
    prereq_input = [
        {
            "kind": "check_result",
            "result_id": consumed_id or prereq_res["result_id"],
        }
    ]
    tach_entry = make_entry(
        "tach",
        required_by=("o/tach",),
        rule_ref=RULE_TACH,
    )
    tach_entry["input_refs"] = prereq_input
    tach_entry["expected_check_ref"] = expected_checks.entry_identity(tach_entry)
    entries = [tach_entry]
    obligations = [check_obligation("o/tach", [tach_entry["expected_check_ref"]])]
    docs_entry = None
    if sibling_required:
        docs_entry = make_entry("docs", required_by=("o/docs",), rule_ref=prereq_rule)
        entries.append(docs_entry)
        obligations.append(
            {
                **check_obligation("o/docs", [docs_entry["expected_check_ref"]]),
                "check_names": ["docs"],
            }
        )
    expected = make_projection(profile, entries)
    universe = make_universe(profile, expected, obligations)
    tach_res = check_contract.native_result(
        check_contract.definition_for("tach"),
        source=copy.deepcopy(SOURCE),
        status="passed",
        result_origin="host",
        result_instance="run-1",
        native_exit_code=0,
        evidence={"stdout": ""},
        rule_ref=copy.deepcopy(RULE_TACH),
        input_refs=copy.deepcopy(prereq_input),
        runner_ref=RUNNER_REF,
        profile_ref=copy.deepcopy(PROFILE_REF),
        tool=check_contract.tool_ref("tach/check"),
    )
    tach_report = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [
            {
                "name": "tach",
                "verification_implementation": copy.deepcopy(IMPLEMENTATION),
                "native_results": [tach_res],
            }
        ],
    }
    tach_report_ref = report_ref_for(tach_report)
    tach_job_receipt = {
        "kind": reconciliation.JOB_RECEIPT_KIND,
        "schema_version": reconciliation.JOB_RECEIPT_SCHEMA_VERSION,
        "job_ref": "job/release",
        "run_ref": RUN_REF,
        "attempt_ref": ATTEMPT_REF,
        "runner_ref": RUNNER_REF,
        "report_ref": copy.deepcopy(tach_report_ref),
        "issuer_ref": copy.deepcopy(JOB_ISSUER_REF),
        "verification_status": "upstream_verified",
    }
    routing = [
        {
            "expected_check_ref": tach_entry["expected_check_ref"],
            "job_refs": ["job/release"],
            "job_instances": [
                {
                    "job_ref": "job/release",
                    "run_ref": RUN_REF,
                    "attempt_ref": ATTEMPT_REF,
                    "runner_ref": RUNNER_REF,
                    "attempt_eligibility": "eligible",
                    "qualification_ref": copy.deepcopy(ATTEMPT_QUALIFICATION_REF),
                    "report_ref": copy.deepcopy(tach_report_ref),
                }
            ],
            "mapping_status": "routed",
            "scheduler_ref": {"name": "controlled", "version": "1"},
            "effective_config_ref": {"name": "fixture", "version": "1"},
            "reason": None,
        }
    ]
    resolutions = [
        {
            "expected_check_ref": tach_entry["expected_check_ref"],
            "rule_ref": copy.deepcopy(RULE_TACH),
            "source_ref": copy.deepcopy(SOURCE),
            "baseline_ref": None,
            "rule_source_ref": copy.deepcopy(RULE_SOURCE_REF),
            "source_receipt_ref": copy.deepcopy(SOURCE_RECEIPT_REF),
            "input_refs": copy.deepcopy(prereq_input),
        }
    ]
    observations = [
        {
            "job_ref": "job/release",
            "run_ref": RUN_REF,
            "attempt_ref": ATTEMPT_REF,
            "runner_ref": RUNNER_REF,
            "report": tach_report,
            "report_ref": copy.deepcopy(tach_report_ref),
            "native_results": [tach_res],
            "job_receipt": copy.deepcopy(tach_job_receipt),
        }
    ]
    if sibling_required and docs_entry is not None and sibling_routed:
        routing.append(
            {
                "expected_check_ref": docs_entry["expected_check_ref"],
                "job_refs": ["job/prereq"],
                "job_instances": [
                    {
                        "job_ref": "job/prereq",
                        "run_ref": RUN_REF,
                        "attempt_ref": prereq_attempt,
                        "runner_ref": RUNNER_REF,
                        "attempt_eligibility": "eligible",
                        "qualification_ref": copy.deepcopy(ATTEMPT_QUALIFICATION_REF),
                        "report_ref": copy.deepcopy(prereq_report_ref),
                    }
                ],
                "mapping_status": "routed",
                "scheduler_ref": {"name": "controlled", "version": "1"},
                "effective_config_ref": {"name": "fixture", "version": "1"},
                "reason": None,
            }
        )
        resolutions.append(
            {
                "expected_check_ref": docs_entry["expected_check_ref"],
                "rule_ref": copy.deepcopy(prereq_rule),
                "source_ref": copy.deepcopy(SOURCE),
                "baseline_ref": None,
                "rule_source_ref": copy.deepcopy(RULE_SOURCE_REF),
                "source_receipt_ref": copy.deepcopy(SOURCE_RECEIPT_REF),
            }
        )
    receipt = reconciliation.reconcile_expected_results(
        expected,
        routing=routing,
        resolutions=resolutions,
        observations=observations,
    )
    policy = {
        "trusted_issuers": [copy.deepcopy(JOB_ISSUER_REF)],
        "approved_implementations": [
            {
                "check_id": tach_entry["check_id"],
                "definition_version": tach_entry["definition_version"],
                **IMPLEMENTATION,
            },
            {
                "check_id": check_contract.definition_for("docs").check_id,
                "definition_version": check_contract.definition_for(
                    "docs"
                ).definition_version,
                **IMPLEMENTATION,
            },
        ],
    }
    job_receipts = [tach_job_receipt]
    if with_prereq_receipt:
        job_receipts.append(prereq_job_receipt)
    t1 = admission.admit_evidence(
        expected,
        receipt,
        reports=[tach_report, prereq_report],
        job_receipts=job_receipts,
        policy=policy,
        authority=lambda ctx: (
            ctx["issuer_ref"] == JOB_ISSUER_REF
            and ctx["implementation"] == IMPLEMENTATION
        ),
        resolutions=resolutions,
        resolution_authority=lambda ctx: (
            ctx["resolution"]["source_receipt_ref"] == SOURCE_RECEIPT_REF
        ),
    )
    proj = admission.project_t1_t2_gate_admission(
        t1,
        expected=expected,
        evaluation=required_assessment._projection_evaluation(
            required_assessment._validate_universe(universe)
        ),
        current_authorization=_df02_current_authorization(),
        source_authority=df02_source_authority(t1, universe),
        receipt=receipt,
        contract_sample=True,
    )
    r1 = _assess(
        t1,
        universe,
        expected,
        receipt,
        authority=df02_source_authority(t1, universe),
    )
    return t1, proj, r1, tach_entry, docs_entry, prereq_res["result_id"]


def test_new014_prerequisite_trust_closure_across_t1_projection_and_r1():
    # 1. Authenticated prerequisite PASS -> T1-a, Projection, and R1 all pass.
    t1, proj, r1, tach_entry, _, prereq_id = _prerequisite_e2e_chain(
        prereq_status="passed"
    )
    assert t1["status"] == "admitted"
    assert t1["remaining_work"] == []
    key = f"{proj['scope_id']}/{tach_entry['expected_check_ref']}"
    assert proj["accepted_coverage_by_instance"][key]["admission_status"] == "admitted"
    assert (
        prereq_id in proj["accepted_coverage_by_instance"][key]["nonselected_origins"]
    )
    assert proj["authenticated_failures_by_instance"] == {}
    assert r1["status"] == "passed"
    assert check_row(r1, tach_entry)["status"] == "satisfied"
    required_assessment.validate_assessment(r1)

    # 2. Unauthenticated prerequisite PASS (no job_receipt, or untrusted issuer)
    #    cannot satisfy dependency -> blocked across T1-a, Projection, R1.
    for scenario, expected_reason in (
        ({"with_prereq_receipt": False}, "prerequisite_job_receipt_missing"),
        (
            {
                "prereq_issuer": {
                    "name": "untrusted-observer",
                    "version": "1",
                    "sha256": "9" * 64,
                }
            },
            "prerequisite_authority_rejected",
        ),
    ):
        t1_u, proj_u, r1_u, tach_u, _, _ = _prerequisite_e2e_chain(
            prereq_status="passed", **scenario
        )
        key_u = f"{proj_u['scope_id']}/{tach_u['expected_check_ref']}"
        assert t1_u["entries"][0]["admitted"] is False
        assert expected_reason in t1_u["entries"][0]["reasons"]
        assert (
            proj_u["accepted_coverage_by_instance"][key_u]["admission_status"]
            == "unverified"
        )
        assert proj_u["remaining_work"][0]["reason"] == expected_reason
        assert r1_u["status"] == "blocked"
        assert check_row(r1_u, tach_u)["status"] == "unverified"
        required_assessment.validate_assessment(r1_u)

    # 3. Authenticated prerequisite FAIL -> T1-a unsatisfied, Projection emits failure, R1 unmet.
    t1_f, proj_f, r1_f, tach_f, _, failed_prereq_id = _prerequisite_e2e_chain(
        prereq_status="failed"
    )
    key_f = f"{proj_f['scope_id']}/{tach_f['expected_check_ref']}"
    assert t1_f["entries"][0]["admitted"] is False
    assert t1_f["entries"][0]["status"] == "unsatisfied"
    assert "prerequisite_result_failed" in t1_f["entries"][0]["reasons"]
    assert any(
        item["result_id"] == failed_prereq_id
        for item in proj_f["authenticated_failures_by_instance"].get(key_f, [])
    )
    assert r1_f["status"] == "blocked"
    f_row = check_row(r1_f, tach_f)
    assert f_row["status"] == "unmet"
    assert any(
        item["result_id"] == failed_prereq_id
        for item in f_row["authenticated_failures"]
    )
    required_assessment.validate_assessment(r1_f)

    # 4. Result ID / Attempt scope mismatch -> T1-a, Projection, R1 stay blocked.
    for scenario, expected_reason in (
        ({"consumed_id": "sha256:" + "0" * 64}, "prerequisite_result_missing"),
        ({"prereq_attempt": "attempt/0"}, "prerequisite_result_scope_mismatch"),
    ):
        t1_m, proj_m, r1_m, tach_m, _, _ = _prerequisite_e2e_chain(**scenario)
        key_m = f"{proj_m['scope_id']}/{tach_m['expected_check_ref']}"
        assert t1_m["entries"][0]["admitted"] is False
        assert expected_reason in t1_m["entries"][0]["reasons"]
        assert (
            proj_m["accepted_coverage_by_instance"][key_m]["admission_status"]
            == "unverified"
        )
        assert r1_m["status"] == "blocked"
        assert check_row(r1_m, tach_m)["status"] == "unverified"
        required_assessment.validate_assessment(r1_m)

    # 5. Independent Required sibling check (`docs`) without routing/reconciliation
    #    cannot be silently satisfied via attached Report: tach is satisfied while
    #    docs remains unverified / remaining_work and R1 blocks.
    t1_s, proj_s, r1_s, tach_s, docs_s, _ = _prerequisite_e2e_chain(
        sibling_required=True, sibling_routed=False
    )
    assert docs_s is not None
    by_name = {item["name"]: item for item in t1_s["entries"]}
    assert by_name["tach"]["admitted"] is True
    assert by_name["docs"]["admitted"] is False
    assert [r["expected_check_ref"] for r in t1_s["remaining_work"]] == [
        docs_s["expected_check_ref"]
    ]
    docs_key = f"{proj_s['scope_id']}/{docs_s['expected_check_ref']}"
    assert (
        proj_s["accepted_coverage_by_instance"][docs_key]["admission_status"]
        == "unverified"
    )
    assert r1_s["status"] == "blocked"
    assert check_row(r1_s, tach_s)["status"] == "satisfied"
    assert check_row(r1_s, docs_s)["status"] == "unverified"
    required_assessment.validate_assessment(r1_s)


def test_new014_p2a_p2b_multi_receipt_conflict_and_cross_source_across_r1():
    profile = make_profile(
        declared_checks=["tach"],
        obligations=[{"obligation_ref": "o/tach", "check_names": ["tach"]}],
    )
    prereq_rule = check_contract.rule_ref("docs", 1, digest="a" * 64)
    prereq_src = check_contract.source_ref(
        "worktree",
        declared_target_tree="3" * 40,
        actual_execution_source="worktree",
        head_commit="2" * 40,
        content_digest="a" * 64,
        verified_scope=["docs/guide.md"],
        scope_kind="check_input_closure",
    )
    dep_src = check_contract.source_ref(
        "worktree",
        declared_target_tree="3" * 40,
        actual_execution_source="dagger_directory",
        head_commit="2" * 40,
        content_digest="b" * 64,
        verified_scope=["src/app.py", "tach.toml"],
        scope_kind="check_input_closure",
    )
    prereq_res = check_contract.native_result(
        check_contract.definition_for("docs"),
        source=prereq_src,
        status="passed",
        result_origin="host",
        result_instance="prereq-inv",
        native_exit_code=0,
        evidence={"stdout": "docs/guide.md"},
        rule_ref=copy.deepcopy(prereq_rule),
        runner_ref=RUNNER_REF,
        profile_ref=copy.deepcopy(PROFILE_REF),
        tool=check_contract.tool_ref("git/ls-files"),
    )
    prereq_report = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [
            {
                "name": "docs-inventory",
                "status": "passed",
                "verification_implementation": copy.deepcopy(IMPLEMENTATION),
                "native_results": [prereq_res],
            }
        ],
    }
    prereq_report_ref = report_ref_for(prereq_report)
    prereq_input = [{"kind": "check_result", "result_id": prereq_res["result_id"]}]
    tach_entry = make_entry(
        "tach", required_by=("o/tach",), rule_ref=RULE_TACH, source=dep_src
    )
    tach_entry["input_refs"] = prereq_input
    tach_entry["expected_check_ref"] = expected_checks.entry_identity(tach_entry)
    expected = make_projection(profile, [tach_entry], source=dep_src)
    obligations = [check_obligation("o/tach", [tach_entry["expected_check_ref"]])]
    universe = make_universe(profile, expected, obligations)
    tach_res = check_contract.native_result(
        check_contract.definition_for("tach"),
        source=copy.deepcopy(dep_src),
        status="passed",
        result_origin="host",
        result_instance="run-1",
        native_exit_code=0,
        evidence={"stdout": ""},
        rule_ref=copy.deepcopy(RULE_TACH),
        input_refs=copy.deepcopy(prereq_input),
        runner_ref=RUNNER_REF,
        profile_ref=copy.deepcopy(PROFILE_REF),
        tool=check_contract.tool_ref("tach/check"),
    )
    tach_report = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [
            {
                "name": "tach",
                "verification_implementation": copy.deepcopy(IMPLEMENTATION),
                "native_results": [tach_res],
            }
        ],
    }
    tach_report_ref = report_ref_for(tach_report)
    tach_jr = {
        "kind": reconciliation.JOB_RECEIPT_KIND,
        "schema_version": reconciliation.JOB_RECEIPT_SCHEMA_VERSION,
        "job_ref": "job/release",
        "run_ref": RUN_REF,
        "attempt_ref": ATTEMPT_REF,
        "runner_ref": RUNNER_REF,
        "report_ref": copy.deepcopy(tach_report_ref),
        "issuer_ref": copy.deepcopy(JOB_ISSUER_REF),
        "verification_status": "upstream_verified",
    }
    routing = [
        {
            "expected_check_ref": tach_entry["expected_check_ref"],
            "job_refs": ["job/release"],
            "job_instances": [
                {
                    "job_ref": "job/release",
                    "run_ref": RUN_REF,
                    "attempt_ref": ATTEMPT_REF,
                    "runner_ref": RUNNER_REF,
                    "attempt_eligibility": "eligible",
                    "qualification_ref": copy.deepcopy(ATTEMPT_QUALIFICATION_REF),
                    "report_ref": copy.deepcopy(tach_report_ref),
                }
            ],
            "mapping_status": "routed",
            "scheduler_ref": {"name": "controlled", "version": "1"},
            "effective_config_ref": {"name": "fixture", "version": "1"},
            "reason": None,
        }
    ]
    resolutions = [
        {
            "expected_check_ref": tach_entry["expected_check_ref"],
            "rule_ref": copy.deepcopy(RULE_TACH),
            "source_ref": copy.deepcopy(dep_src),
            "baseline_ref": None,
            "rule_source_ref": copy.deepcopy(RULE_SOURCE_REF),
            "source_receipt_ref": copy.deepcopy(SOURCE_RECEIPT_REF),
            "input_refs": copy.deepcopy(prereq_input),
        }
    ]
    receipt = reconciliation.reconcile_expected_results(
        expected,
        routing=routing,
        resolutions=resolutions,
        observations=[
            {
                "job_ref": "job/release",
                "run_ref": RUN_REF,
                "attempt_ref": ATTEMPT_REF,
                "runner_ref": RUNNER_REF,
                "report": tach_report,
                "report_ref": copy.deepcopy(tach_report_ref),
                "native_results": [tach_res],
                "job_receipt": copy.deepcopy(tach_jr),
            }
        ],
    )
    policy = {
        "trusted_issuers": [copy.deepcopy(JOB_ISSUER_REF)],
        "approved_implementations": [
            {
                "check_id": tach_entry["check_id"],
                "definition_version": tach_entry["definition_version"],
                **IMPLEMENTATION,
            },
            {
                "check_id": check_contract.definition_for("docs").check_id,
                "definition_version": check_contract.definition_for(
                    "docs"
                ).definition_version,
                **IMPLEMENTATION,
            },
        ],
    }

    def run_with(extra_receipts):
        t1 = admission.admit_evidence(
            expected,
            receipt,
            reports=[tach_report, prereq_report],
            job_receipts=[tach_jr, *extra_receipts],
            policy=policy,
            authority=lambda ctx: (
                ctx["issuer_ref"] == JOB_ISSUER_REF
                and ctx["implementation"] == IMPLEMENTATION
            ),
            resolutions=resolutions,
            resolution_authority=lambda ctx: (
                ctx["resolution"]["source_receipt_ref"] == SOURCE_RECEIPT_REF
            ),
        )
        proj = admission.project_t1_t2_gate_admission(
            t1,
            expected=expected,
            evaluation=required_assessment._projection_evaluation(
                required_assessment._validate_universe(universe)
            ),
            current_authorization=_df02_current_authorization(),
            source_authority=df02_source_authority(t1, universe),
            receipt=receipt,
            contract_sample=True,
        )
        r1 = _assess(
            t1,
            universe,
            expected,
            receipt,
            authority=df02_source_authority(t1, universe),
        )
        return t1, proj, r1

    base_prereq_jr = {
        "kind": reconciliation.JOB_RECEIPT_KIND,
        "schema_version": reconciliation.JOB_RECEIPT_SCHEMA_VERSION,
        "job_ref": "job/prereq-match",
        "run_ref": RUN_REF,
        "attempt_ref": ATTEMPT_REF,
        "runner_ref": RUNNER_REF,
        "report_ref": copy.deepcopy(prereq_report_ref),
        "issuer_ref": copy.deepcopy(JOB_ISSUER_REF),
        "verification_status": "upstream_verified",
        "scope_id": expected["scope_id"],
    }
    wrong_scope_jr = {
        **base_prereq_jr,
        "job_ref": "job/prereq-000",
        "scope_id": "sha256:" + "0" * 64,
    }
    # Swapped order with wrong-scope receipt + valid cross-Check source_ref -> PASSES end-to-end.
    key = f"{expected['scope_id']}/{tach_entry['expected_check_ref']}"
    for ordered in ([wrong_scope_jr, base_prereq_jr], [base_prereq_jr, wrong_scope_jr]):
        t1_ok, proj_ok, r1_ok = run_with(ordered)
        assert t1_ok["status"] == "admitted"
        assert (
            proj_ok["accepted_coverage_by_instance"][key]["admission_status"]
            == "admitted"
        )
        assert r1_ok["status"] == "passed"
        required_assessment.validate_assessment(r1_ok)

    # #66-C-compatible duplicate observation (two distinct job_refs observing
    # the exact same Report, Issuer, Run, Attempt, Runner, and attribution)
    # is allowed with preserved multi-job origin tracing regardless of order.
    dup_jr_a = {
        **base_prereq_jr,
        "job_ref": "job/prereq-alpha",
        "invocation_ref": "inv/1",
    }
    dup_jr_b = {
        **base_prereq_jr,
        "job_ref": "job/prereq-beta",
        "invocation_ref": "inv/1",
    }
    expected_dup_refs = sorted(
        reconciliation._digest(doc) for doc in (dup_jr_a, dup_jr_b)
    )
    for ordered in ([dup_jr_a, dup_jr_b], [dup_jr_b, dup_jr_a]):
        t1_d, proj_d, r1_d = run_with(ordered)
        assert t1_d["status"] == "admitted"
        assert (
            proj_d["accepted_coverage_by_instance"][key]["admission_status"]
            == "admitted"
        )
        origin = proj_d["accepted_coverage_by_instance"][key]["nonselected_origins"][
            prereq_res["result_id"]
        ]
        assert origin["duplicate_observation"] is True
        assert origin["observed_job_receipt_refs"] == expected_dup_refs
        assert origin["observed_job_refs"] == ["job/prereq-alpha", "job/prereq-beta"]
        assert r1_d["status"] == "passed"
        required_assessment.validate_assessment(r1_d)

    # Genuine attribution conflict (e.g. different invocation_ref) stays blocked
    # across T1-a / Projection / R1 regardless of input ordering.
    conflict_jr_a = {
        **base_prereq_jr,
        "job_ref": "job/prereq-one",
        "invocation_ref": "inv/1",
    }
    conflict_jr_b = {
        **base_prereq_jr,
        "job_ref": "job/prereq-two",
        "invocation_ref": "inv/2",
    }
    for ordered in ([conflict_jr_a, conflict_jr_b], [conflict_jr_b, conflict_jr_a]):
        t1_c, proj_c, r1_c = run_with(ordered)
        assert t1_c["entries"][0]["admitted"] is False
        assert "prerequisite_result_conflict" in t1_c["entries"][0]["reasons"]
        assert (
            proj_c["accepted_coverage_by_instance"][key]["admission_status"]
            == "unverified"
        )
        assert proj_c["remaining_work"][0]["reason"] == "prerequisite_result_conflict"
        assert r1_c["status"] == "blocked"
        assert check_row(r1_c, tach_entry)["status"] == "unverified"
        required_assessment.validate_assessment(r1_c)


def _overlap_e2e_chain(*, prereq_paths, dep_paths, dep_extra_inputs=()):
    profile = make_profile(
        declared_checks=["tach"],
        obligations=[{"obligation_ref": "o/tach", "check_names": ["tach"]}],
    )

    def make_bound(paths_dict, *, actual):
        canonical = {k: v for k, v in sorted(paths_dict.items())}
        digest = reconciliation._digest(canonical).removeprefix("sha256:")
        src = check_contract.source_ref(
            "worktree",
            declared_target_tree="3" * 40,
            actual_execution_source=actual,
            head_commit="2" * 40,
            content_digest=digest,
            verified_scope=sorted(canonical),
            scope_kind="check_input_closure",
        )
        bind = {"algorithm": "sha256-paths", "digest": digest, "paths": canonical}
        return src, bind

    prereq_src, prereq_bind = make_bound(prereq_paths, actual="worktree")
    dep_src, dep_bind = make_bound(dep_paths, actual="dagger_directory")
    prereq_rule = check_contract.rule_ref("docs", 1, digest="a" * 64)
    prereq_res = check_contract.native_result(
        check_contract.definition_for("docs"),
        source=prereq_src,
        status="passed",
        result_origin="host",
        result_instance="prereq-inv",
        native_exit_code=0,
        evidence={"stdout": "docs/shared.md"},
        rule_ref=copy.deepcopy(prereq_rule),
        runner_ref=RUNNER_REF,
        profile_ref=copy.deepcopy(PROFILE_REF),
        tool=check_contract.tool_ref("git/ls-files"),
    )
    prereq_report = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [
            {
                "name": "docs-inventory",
                "status": "passed",
                "verification_implementation": copy.deepcopy(IMPLEMENTATION),
                "source_content_binding": prereq_bind,
                "native_results": [prereq_res],
            }
        ],
    }
    prereq_report_ref = report_ref_for(prereq_report)
    prereq_jr = {
        "kind": reconciliation.JOB_RECEIPT_KIND,
        "schema_version": reconciliation.JOB_RECEIPT_SCHEMA_VERSION,
        "job_ref": "job/prereq",
        "run_ref": RUN_REF,
        "attempt_ref": ATTEMPT_REF,
        "runner_ref": RUNNER_REF,
        "report_ref": copy.deepcopy(prereq_report_ref),
        "issuer_ref": copy.deepcopy(JOB_ISSUER_REF),
        "verification_status": "upstream_verified",
    }
    prereq_input = [
        {"kind": "check_result", "result_id": prereq_res["result_id"]},
        *[copy.deepcopy(item) for item in dep_extra_inputs],
    ]
    tach_entry = make_entry(
        "tach", required_by=("o/tach",), rule_ref=RULE_TACH, source=dep_src
    )
    tach_entry["input_refs"] = prereq_input
    tach_entry["expected_check_ref"] = expected_checks.entry_identity(tach_entry)
    expected = make_projection(profile, [tach_entry], source=dep_src)
    obligations = [check_obligation("o/tach", [tach_entry["expected_check_ref"]])]
    universe = make_universe(profile, expected, obligations)
    tach_res = check_contract.native_result(
        check_contract.definition_for("tach"),
        source=copy.deepcopy(dep_src),
        status="passed",
        result_origin="host",
        result_instance="run-1",
        native_exit_code=0,
        evidence={"stdout": ""},
        rule_ref=copy.deepcopy(RULE_TACH),
        input_refs=copy.deepcopy(prereq_input),
        runner_ref=RUNNER_REF,
        profile_ref=copy.deepcopy(PROFILE_REF),
        tool=check_contract.tool_ref("tach/check"),
    )
    tach_report = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [
            {
                "name": "tach",
                "verification_implementation": copy.deepcopy(IMPLEMENTATION),
                "source_content_binding": dep_bind,
                "native_results": [tach_res],
            }
        ],
    }
    tach_report_ref = report_ref_for(tach_report)
    tach_jr = {
        "kind": reconciliation.JOB_RECEIPT_KIND,
        "schema_version": reconciliation.JOB_RECEIPT_SCHEMA_VERSION,
        "job_ref": "job/release",
        "run_ref": RUN_REF,
        "attempt_ref": ATTEMPT_REF,
        "runner_ref": RUNNER_REF,
        "report_ref": copy.deepcopy(tach_report_ref),
        "issuer_ref": copy.deepcopy(JOB_ISSUER_REF),
        "verification_status": "upstream_verified",
    }
    resolutions = [
        {
            "expected_check_ref": tach_entry["expected_check_ref"],
            "rule_ref": copy.deepcopy(RULE_TACH),
            "source_ref": copy.deepcopy(dep_src),
            "baseline_ref": None,
            "rule_source_ref": copy.deepcopy(RULE_SOURCE_REF),
            "source_receipt_ref": copy.deepcopy(SOURCE_RECEIPT_REF),
            "input_refs": copy.deepcopy(prereq_input),
        }
    ]
    receipt = reconciliation.reconcile_expected_results(
        expected,
        routing=[
            {
                "expected_check_ref": tach_entry["expected_check_ref"],
                "job_refs": ["job/release"],
                "job_instances": [
                    {
                        "job_ref": "job/release",
                        "run_ref": RUN_REF,
                        "attempt_ref": ATTEMPT_REF,
                        "runner_ref": RUNNER_REF,
                        "attempt_eligibility": "eligible",
                        "qualification_ref": copy.deepcopy(ATTEMPT_QUALIFICATION_REF),
                        "report_ref": copy.deepcopy(tach_report_ref),
                    }
                ],
                "mapping_status": "routed",
                "scheduler_ref": {"name": "controlled", "version": "1"},
                "effective_config_ref": {"name": "fixture", "version": "1"},
                "reason": None,
            }
        ],
        resolutions=resolutions,
        observations=[
            {
                "job_ref": "job/release",
                "run_ref": RUN_REF,
                "attempt_ref": ATTEMPT_REF,
                "runner_ref": RUNNER_REF,
                "report": tach_report,
                "report_ref": copy.deepcopy(tach_report_ref),
                "native_results": [tach_res],
                "job_receipt": copy.deepcopy(tach_jr),
            }
        ],
    )
    policy = {
        "trusted_issuers": [copy.deepcopy(JOB_ISSUER_REF)],
        "approved_implementations": [
            {
                "check_id": tach_entry["check_id"],
                "definition_version": tach_entry["definition_version"],
                **IMPLEMENTATION,
            },
            {
                "check_id": check_contract.definition_for("docs").check_id,
                "definition_version": check_contract.definition_for(
                    "docs"
                ).definition_version,
                **IMPLEMENTATION,
            },
        ],
    }
    t1 = admission.admit_evidence(
        expected,
        receipt,
        reports=[tach_report, prereq_report],
        job_receipts=[tach_jr, prereq_jr],
        policy=policy,
        authority=lambda ctx: (
            ctx["issuer_ref"] == JOB_ISSUER_REF
            and ctx["implementation"] == IMPLEMENTATION
        ),
        resolutions=resolutions,
        resolution_authority=lambda ctx: (
            ctx["resolution"]["source_receipt_ref"] == SOURCE_RECEIPT_REF
        ),
    )
    proj = admission.project_t1_t2_gate_admission(
        t1,
        expected=expected,
        evaluation=required_assessment._projection_evaluation(
            required_assessment._validate_universe(universe)
        ),
        current_authorization=_df02_current_authorization(),
        source_authority=df02_source_authority(t1, universe),
        receipt=receipt,
        contract_sample=True,
    )
    r1 = _assess(
        t1,
        universe,
        expected,
        receipt,
        authority=df02_source_authority(t1, universe),
    )
    return t1, proj, r1, expected["scope_id"], tach_entry


def test_new014_p1_overlapping_path_consistency_and_generated_overlay_across_r1():
    shared = "4" * 64
    drifted = "5" * 64
    gen_old = "6" * 64
    gen_new = "7" * 64

    # 1. Overlapping path matches + unrelated non-overlapping differs -> PASS across T1-a/Proj/R1.
    t1_ok, proj_ok, r1_ok, sid_ok, e_ok = _overlap_e2e_chain(
        prereq_paths={
            "ci-hygiene.toml": "1" * 64,
            "docs/shared.md": shared,
            "docs/only.md": "a" * 64,
        },
        dep_paths={
            "ci-hygiene.toml": "1" * 64,
            "docs/shared.md": shared,
            "src/app.py": "b" * 64,
        },
    )
    key_ok = f"{sid_ok}/{e_ok['expected_check_ref']}"
    assert t1_ok["status"] == "admitted"
    assert (
        proj_ok["accepted_coverage_by_instance"][key_ok]["admission_status"]
        == "admitted"
    )
    assert r1_ok["status"] == "passed"

    # 2. Overlapping `docs/shared.md` drifted (even with unrelated generated_input) -> BLOCKED across R1.
    for extra in (
        (),
        (
            {
                "kind": "generated_input",
                "input_kind": "file",
                "path": "docs/unrelated.md",
                "sha256": gen_new,
            },
        ),
    ):
        t1_b, proj_b, r1_b, sid_b, e_b = _overlap_e2e_chain(
            prereq_paths={
                "ci-hygiene.toml": "1" * 64,
                "docs/shared.md": shared,
                "docs/only.md": "a" * 64,
            },
            dep_paths={
                "ci-hygiene.toml": "1" * 64,
                "docs/shared.md": drifted,
                "src/app.py": "b" * 64,
            },
            dep_extra_inputs=extra,
        )
        key_b = f"{sid_b}/{e_b['expected_check_ref']}"
        assert t1_b["entries"][0]["admitted"] is False
        assert "prerequisite_result_scope_mismatch" in t1_b["entries"][0]["reasons"]
        assert (
            proj_b["accepted_coverage_by_instance"][key_b]["admission_status"]
            == "unverified"
        )
        assert (
            proj_b["remaining_work"][0]["reason"]
            == "prerequisite_result_scope_mismatch"
        )
        assert r1_b["status"] == "blocked"
        assert check_row(r1_b, e_b)["status"] == "unverified"

    # 3. Genuine Generated Overlay covering exact divergent overlapping file -> PASS across R1.
    t1_g, proj_g, r1_g, sid_g, e_g = _overlap_e2e_chain(
        prereq_paths={
            "ci-hygiene.toml": "1" * 64,
            "docs/shared.md": shared,
            "docs/gen.md": gen_old,
        },
        dep_paths={
            "ci-hygiene.toml": "1" * 64,
            "docs/shared.md": shared,
            "docs/gen.md": gen_new,
            "src/app.py": "b" * 64,
        },
        dep_extra_inputs=(
            {
                "kind": "generated_input",
                "input_kind": "file",
                "path": "docs/gen.md",
                "sha256": gen_new,
            },
        ),
    )
    key_g = f"{sid_g}/{e_g['expected_check_ref']}"
    assert t1_g["status"] == "admitted"
    assert (
        proj_g["accepted_coverage_by_instance"][key_g]["admission_status"] == "admitted"
    )
    assert r1_g["status"] == "passed"
    required_assessment.validate_assessment(r1_g)
