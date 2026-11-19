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
