"""T1-a same-execution admission tests."""

import copy
import json

import pytest

from engineering_ci import admission, check_contract, expected_checks, reconciliation

EVENT = {
    "base_ref": "refs/heads/main",
    "head_ref": "refs/heads/topic",
    "base_commit": "1" * 40,
    "head_commit": "2" * 40,
    "references": [],
}
WEAK_SOURCE = {
    "snapshot": "worktree",
    "declared_target_tree": None,
    "actual_execution_source": "unknown",
    "verified_input_scope": ["docs/guide.md"],
    "scope_kind": "changed_paths",
    "binding_status": "unknown",
}
STRONG_SOURCE = check_contract.source_ref(
    "worktree",
    declared_target_tree="3" * 40,
    actual_execution_source="worktree",
    content_digest="c" * 64,
    verified_scope=["docs/guide.md"],
    scope_kind="check_input_closure",
)
OTHER_SOURCE = check_contract.source_ref(
    "worktree",
    declared_target_tree="3" * 40,
    actual_execution_source="worktree",
    content_digest="e" * 64,
    verified_scope=["docs/guide.md"],
    scope_kind="check_input_closure",
)
SELECTION_RULE = {
    "name": "engineering-ci.selection",
    "version": "1",
    "sha256": "a" * 64,
}
RULE_TACH = check_contract.rule_ref("tach", 1, digest="b" * 64)
RULE_VULTURE = check_contract.rule_ref("vulture", 1, digest="f" * 64)
RULE_SOURCE_REF = {"name": "hygiene-rule", "version": "1", "sha256": "4" * 64}
SOURCE_RECEIPT_REF = {
    "name": "p0-source-receipt",
    "version": "1",
    "sha256": "5" * 64,
}
PROFILE_REF = {"name": "org/engineering", "version": "1"}
PROFILE_SOURCE = "org-governance"
RUN_REF = "run/123"
ATTEMPT_REF = "attempt/1"
RUNNER_REF = "execution:host"
ATTEMPT_QUALIFICATION_REF = {
    "name": "scheduler-attempt",
    "version": "1",
    "sha256": "b" * 64,
}
JOB_ISSUER = {"name": "controlled-host", "version": "1", "sha256": "c" * 64}
IMPLEMENTATION = {
    "implementation_id": "eci.test.tach-checker",
    "implementation_version": "1",
    "implementation_sha256": "d" * 64,
}


def make_entry(name="tach", *, rule_ref=RULE_TACH, **changes):
    definition = check_contract.definition_for(name)
    entry = {
        "name": name,
        "check_id": definition.check_id,
        "definition_version": definition.definition_version,
        "definition_status": "resolved",
        "event_ref": copy.deepcopy(EVENT),
        "selection_rule_ref": copy.deepcopy(SELECTION_RULE),
        "rule_ref": copy.deepcopy(rule_ref),
        "rule_status": "resolved" if rule_ref is not None else "unknown",
        "profile_ref": None,
        "applicability": {"status": "selected", "reason": "selected-test"},
        "policy_status": "unknown",
        "source_ref": copy.deepcopy(WEAK_SOURCE),
        "input_refs": [],
        "baseline_ref": None,
    }
    entry.update(changes)
    entry["expected_check_ref"] = expected_checks.entry_identity(entry)
    return entry


def make_projection(entry):
    entries = [entry] if isinstance(entry, dict) else list(entry)
    projection = {
        "kind": expected_checks.EXPECTED_KIND,
        "schema_version": 1,
        "projection_source": "check_scope",
        "status": "partial",
        "reason": "test",
        "selection_rule_ref": copy.deepcopy(SELECTION_RULE),
        "definitions_status": "requested_names_complete",
        "applicability_status": "requested_scope",
        "consumer": None,
        "profile_ref": None,
        "profile_source": None,
        "consumer_completeness": "not_claimed",
        "observed_input_closure_status": "not_observed",
        "event_ref": copy.deepcopy(EVENT),
        "source_ref": copy.deepcopy(WEAK_SOURCE),
        "checks": entries,
        "non_check_obligations": [],
        "missing": [],
        "conflicts": [],
    }
    projection["scope_id"] = expected_checks.scope_identity(
        None,
        None,
        None,
        EVENT,
        WEAK_SOURCE,
        SELECTION_RULE,
    )
    projection["expected_set_ref"] = expected_checks.set_identity(
        entries, (), schema_version=1
    )
    return projection


def native(*, status="passed", source=STRONG_SOURCE, rule=RULE_TACH):
    definition = check_contract.definition_for("tach")
    exit_code = 0 if status == "passed" else 1 if status == "failed" else None
    return check_contract.native_result(
        definition,
        source=copy.deepcopy(source),
        status=status,
        result_origin="host",
        result_instance="run-1",
        native_exit_code=exit_code,
        evidence={"stdout": ""},
        rule_ref=copy.deepcopy(rule),
        runner_ref=RUNNER_REF,
        tool=check_contract.tool_ref("tach/check"),
    )


def report_for(result, implementation=IMPLEMENTATION):
    return {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [
            {
                "name": "reconciled",
                "verification_implementation": copy.deepcopy(implementation),
                "native_results": [result],
            }
        ],
    }


def report_ref_for(report):
    return {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": report["schema_version"],
        "sha256": reconciliation._digest(report).split(":", 1)[1],
    }


def job_receipt_for(report_ref, *, issuer=JOB_ISSUER, verification="upstream_verified"):
    return {
        "kind": reconciliation.JOB_RECEIPT_KIND,
        "schema_version": reconciliation.JOB_RECEIPT_SCHEMA_VERSION,
        "job_ref": "job-1",
        "run_ref": RUN_REF,
        "attempt_ref": ATTEMPT_REF,
        "runner_ref": RUNNER_REF,
        "report_ref": copy.deepcopy(report_ref),
        "issuer_ref": copy.deepcopy(issuer),
        "verification_status": verification,
    }


def routing_for(entry):
    return {
        "expected_check_ref": entry["expected_check_ref"],
        "job_refs": ["job-1"],
        "job_instances": [
            {
                "job_ref": "job-1",
                "run_ref": RUN_REF,
                "attempt_ref": ATTEMPT_REF,
                "runner_ref": RUNNER_REF,
                "attempt_eligibility": "eligible",
                "qualification_ref": copy.deepcopy(ATTEMPT_QUALIFICATION_REF),
            }
        ],
        "mapping_status": "routed",
        "scheduler_ref": {"name": "test", "version": "1"},
        "effective_config_ref": {"name": "test-config", "version": "1"},
        "reason": None,
    }


def resolution_for(entry):
    return {
        "expected_check_ref": entry["expected_check_ref"],
        "rule_ref": copy.deepcopy(RULE_TACH),
        "source_ref": copy.deepcopy(STRONG_SOURCE),
        "baseline_ref": None,
        "rule_source_ref": copy.deepcopy(RULE_SOURCE_REF),
        "source_receipt_ref": copy.deepcopy(SOURCE_RECEIPT_REF),
    }


def observation_for(report, report_ref, job_receipt):
    result = report["checks"][0]["native_results"][0]
    return {
        "job_ref": "job-1",
        "run_ref": RUN_REF,
        "attempt_ref": ATTEMPT_REF,
        "runner_ref": RUNNER_REF,
        "report": report,
        "report_ref": copy.deepcopy(report_ref),
        "native_results": [result],
        "job_receipt": copy.deepcopy(job_receipt),
    }


def setup(
    *,
    status="passed",
    implementation=IMPLEMENTATION,
    issuer=JOB_ISSUER,
    result_source=STRONG_SOURCE,
):
    entry = make_entry()
    projection = make_projection(entry)
    result = native(status=status, source=result_source)
    report = report_for(result, implementation=implementation)
    report_ref = report_ref_for(report)
    job_receipt = job_receipt_for(report_ref, issuer=issuer)
    receipt = reconciliation.reconcile_expected_results(
        projection,
        routing=[routing_for(entry)],
        resolutions=[resolution_for(entry)],
        observations=[observation_for(report, report_ref, job_receipt)],
    )
    return projection, receipt, report, job_receipt


def policy_for(
    *, issuer=JOB_ISSUER, implementation=IMPLEMENTATION, allowed_runner_refs=None
):
    policy = {
        "trusted_issuers": [copy.deepcopy(issuer)],
        "approved_implementations": [
            {
                "check_id": "eci/hygiene/tach",
                "definition_version": check_contract.definition_for(
                    "tach"
                ).definition_version,
                "implementation_id": implementation["implementation_id"],
                "implementation_version": implementation["implementation_version"],
                "implementation_sha256": implementation["implementation_sha256"],
            }
        ],
    }
    if allowed_runner_refs is not None:
        policy["allowed_runner_refs"] = list(allowed_runner_refs)
    return policy


def admit(projection, receipt, report, job_receipt, policy):
    return admission.admit_evidence(
        projection,
        receipt,
        reports=[report],
        job_receipts=[job_receipt],
        policy=policy,
    )


def test_t1a_admits_real_verified_native_pass():
    projection, receipt, report, job_receipt = setup()
    assert receipt["entries"][0]["binding_status"] == "matched"
    result = admit(projection, receipt, report, job_receipt, policy_for())
    assert result["status"] == "admitted"
    entry = result["entries"][0]
    assert entry["origin_status"] == "verified"
    assert entry["obligation_status"] == "satisfied"
    assert entry["admitted"] is True
    assert (
        result["accepted_coverage_by_instance"][0]["expected_check_ref"]
        == (receipt["entries"][0]["expected_check_ref"])
    )
    assert result["remaining_work"] == []
    assert result["verified_native_facts"][0]["native_exit_code"] == 0


def test_t1a_verified_native_fail_is_unsatisfied_not_accepted():
    projection, receipt, report, job_receipt = setup(status="failed")
    result = admit(projection, receipt, report, job_receipt, policy_for())
    entry = result["entries"][0]
    assert entry["origin_status"] == "verified"
    assert entry["obligation_status"] == "unsatisfied"
    assert entry["admitted"] is False
    assert result["accepted_coverage_by_instance"] == []
    assert "native_failed" in entry["reasons"]
    assert result["status"] in {"partial", "rejected", "unknown"}


def test_t1a_missing_raw_report_is_unverified_and_remaining():
    projection, receipt, _report, job_receipt = setup()
    result = admission.admit_evidence(
        projection, receipt, reports=[], job_receipts=[job_receipt], policy=policy_for()
    )
    entry = result["entries"][0]
    assert entry["origin_status"] == "unverified"
    assert entry["obligation_status"] == "unknown"
    assert entry["admitted"] is False
    assert result["remaining_work"]


def test_t1a_tampered_report_is_rejected():
    projection, receipt, report, job_receipt = setup()
    tampered = copy.deepcopy(report)
    tampered["checks"][0]["native_results"][0]["status"] = "passed"
    tampered["checks"][0]["native_results"][0]["native_exit_code"] = 0
    tampered["checks"][0]["native_results"][0]["evidence"] = {"stdout": "changed"}
    result = admission.admit_evidence(
        projection,
        receipt,
        reports=[tampered],
        job_receipts=[job_receipt],
        policy=policy_for(),
    )
    entry = result["entries"][0]
    assert entry["origin_status"] == "rejected"
    assert entry["admitted"] is False


def test_t1a_untrusted_issuer_is_rejected():
    projection, receipt, report, job_receipt = setup()
    other_issuer = {"name": "untrusted", "version": "1", "sha256": "e" * 64}
    result = admission.admit_evidence(
        projection,
        receipt,
        reports=[report],
        job_receipts=[job_receipt],
        policy=policy_for(issuer=other_issuer),
    )
    entry = result["entries"][0]
    assert entry["origin_status"] == "rejected"
    assert "issuer_untrusted" in entry["reasons"]
    assert entry["admitted"] is False


def test_t1a_unapproved_implementation_rejects_coverage_not_execution_fact():
    projection, receipt, report, job_receipt = setup()
    other = {
        "implementation_id": "eci.test.fixed-pass",
        "implementation_version": "1",
        "implementation_sha256": "f" * 64,
    }
    result = admission.admit_evidence(
        projection,
        receipt,
        reports=[report],
        job_receipts=[job_receipt],
        policy=policy_for(implementation=other),
    )
    entry = result["entries"][0]
    assert entry["origin_status"] == "verified"
    assert entry["obligation_status"] == "unsatisfied"
    assert entry["admitted"] is False
    assert "verification_implementation_unapproved" in entry["reasons"]


def test_t1a_receipt_scope_mismatch_is_refused():
    projection, receipt, report, job_receipt = setup()
    other_receipt = copy.deepcopy(receipt)
    other_receipt["scope_id"] = "sha256:" + "0" * 64
    other_receipt["receipt_ref"] = reconciliation._digest(
        {key: value for key, value in other_receipt.items() if key != "receipt_ref"}
    )
    with pytest.raises(
        admission.AdmissionError, match="admission_receipt_scope_mismatch"
    ):
        admission.admit_evidence(
            projection,
            other_receipt,
            reports=[report],
            job_receipts=[job_receipt],
            policy=policy_for(),
        )


def test_t1a_policy_requires_trusted_issuer_and_approved_implementation():
    projection, receipt, report, job_receipt = setup()
    with pytest.raises(admission.AdmissionError, match="admission_policy"):
        admission.admit_evidence(
            projection,
            receipt,
            reports=[report],
            job_receipts=[job_receipt],
            policy={"trusted_issuers": [], "approved_implementations": []},
        )
    assert json.dumps(policy_for()) == json.dumps(policy_for())


def test_t1a_wrong_source_is_verified_but_unsatisfied():
    projection, receipt, report, job_receipt = setup(result_source=OTHER_SOURCE)
    result = admission.admit_evidence(
        projection,
        receipt,
        reports=[report],
        job_receipts=[job_receipt],
        policy=policy_for(),
    )
    entry = result["entries"][0]
    assert entry["origin_status"] == "verified"
    assert entry["obligation_status"] == "unsatisfied"
    assert entry["admitted"] is False
    assert result["accepted_coverage_by_instance"] == []


def test_t1a_job_receipt_tamper_or_wrong_attempt_is_rejected():
    projection, receipt, report, job_receipt = setup()
    tampered = copy.deepcopy(job_receipt)
    tampered["attempt_ref"] = "attempt/2"
    result = admission.admit_evidence(
        projection,
        receipt,
        reports=[report],
        job_receipts=[tampered],
        policy=policy_for(),
    )
    entry = result["entries"][0]
    assert entry["origin_status"] == "rejected"
    assert entry["admitted"] is False


def test_t1a_runner_outside_policy_is_rejected():
    projection, receipt, report, job_receipt = setup()
    result = admission.admit_evidence(
        projection,
        receipt,
        reports=[report],
        job_receipts=[job_receipt],
        policy=policy_for(allowed_runner_refs=["execution:other"]),
    )
    entry = result["entries"][0]
    assert entry["origin_status"] == "rejected"
    assert "runner_not_authorized" in entry["reasons"]


def test_t1a_partial_admission_keeps_unrouted_remaining_work():
    tach = make_entry()
    vulture = make_entry("vulture", rule_ref=RULE_VULTURE)
    projection = make_projection([tach, vulture])
    result = native()
    report = report_for(result)
    report_ref = report_ref_for(report)
    job_receipt = job_receipt_for(report_ref)
    receipt = reconciliation.reconcile_expected_results(
        projection,
        routing=[routing_for(tach)],
        resolutions=[resolution_for(tach)],
        observations=[observation_for(report, report_ref, job_receipt)],
    )
    admitted = admission.admit_evidence(
        projection,
        receipt,
        reports=[report],
        job_receipts=[job_receipt],
        policy=policy_for(),
    )
    assert admitted["status"] == "partial"
    assert admitted["summary"]["admitted"] == 1
    assert len(admitted["accepted_coverage_by_instance"]) == 1
    assert len(admitted["remaining_work"]) == 1
    assert (
        admitted["remaining_work"][0]["expected_check_ref"]
        == (vulture["expected_check_ref"])
    )
