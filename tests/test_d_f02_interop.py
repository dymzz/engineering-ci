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
