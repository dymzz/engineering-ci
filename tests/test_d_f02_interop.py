"""D-F02: native #63 T1-a producer to #61 R1 consumer using the same raw evidence."""

import copy

import pytest
from test_historical_reuse import (
    admit as admit_historical,
    historical_candidate,
    pre_proof_for,
)
from test_required_assessment import (
    ATTEMPT_QUALIFICATION_REF,
    ATTEMPT_REF,
    EVALUATED_AT,
    JOB_ISSUER_REF,
    RULE_SOURCE_REF,
    RULE_TACH,
    RUN_REF,
    RUNNER_REF,
    SOURCE,
    SOURCE_RECEIPT_REF,
    check_obligation,
    check_row,
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


def _real_t1_source(*, status="passed"):
    profile = make_profile()
    entry = make_entry()
    expected = make_projection(profile, [entry])
    universe = make_universe(
        profile,
        expected,
        [check_obligation("o/tach", [entry["expected_check_ref"]])],
    )
    result = native_result(entry, status=status)
    report = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [
            {
                "name": "reconciled",
                "verification_implementation": copy.deepcopy(IMPLEMENTATION),
                "native_results": [result],
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
        "native_results": [result],
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
                "qualification_ref": copy.deepcopy(
                    ATTEMPT_QUALIFICATION_REF
                ),
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
        observations=[observation],
    )
    definition = check_contract.definition_for(entry["name"])
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
            context["issuer_ref"] == JOB_ISSUER_REF
            and context["implementation"] == IMPLEMENTATION
        ),
        resolutions=[resolution],
        resolution_authority=lambda context: (
            context["resolution"]["source_receipt_ref"]
            == SOURCE_RECEIPT_REF
        ),
    )
    return original, universe, expected, entry, receipt, result["result_id"]


def _assess(source, universe, expected, receipt, *, authority):
    return required_assessment.assess_required_obligations(
        universe=universe,
        expected=expected,
        evaluated_at=EVALUATED_AT,
        admission=source,
        receipt=receipt,
        admission_source_authority=authority,
        admission_current_authorization={
            "phase": "pre_publish",
            "action": "publish",
            "target_ref": "refs/heads/main",
            "candidate_digest": None,
        },
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
        authority=lambda ctx: ctx["source_admission"] == source,
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
        authority=lambda ctx: ctx["source_admission"] == source,
    )
    assert outcome["status"] == "blocked"
    assert check_row(outcome, entry)["status"] != "satisfied"


def test_native_t1_source_requires_independent_authority():
    source, universe, expected, _entry, receipt, _ = _real_t1_source()
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="admission_source_authority_unverified",
    ):
        _assess(source, universe, expected, receipt, authority=lambda _: False)


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
    old_result = native_result(entry)
    candidate = historical_candidate(entry, old_result)
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
    outcome = required_assessment.assess_required_obligations(
        universe=universe,
        expected=expected,
        evaluated_at=EVALUATED_AT,
        admission=t2,
        admission_source_authority=lambda ctx: ctx["source_admission"] == t2,
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
    assert row["structural"]["status"] == "not_supplied"

