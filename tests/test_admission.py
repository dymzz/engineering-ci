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


def make_entry(name="tach", *, rule_ref=RULE_TACH, source=WEAK_SOURCE, **changes):
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
        "source_ref": copy.deepcopy(source),
        "input_refs": [],
        "baseline_ref": None,
    }
    entry.update(changes)
    entry["expected_check_ref"] = expected_checks.entry_identity(entry)
    return entry


def make_projection(entry, *, source=WEAK_SOURCE):
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
        "source_ref": copy.deepcopy(source),
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
        source,
        SELECTION_RULE,
    )
    projection["expected_set_ref"] = expected_checks.set_identity(
        entries, (), schema_version=1
    )
    return projection


def native(*, status="passed", source=STRONG_SOURCE, rule=RULE_TACH, profile_ref=None):
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
        profile_ref=copy.deepcopy(profile_ref),
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


def policy_for_check(
    check_id,
    definition_version,
    *,
    issuer=JOB_ISSUER,
    implementation=IMPLEMENTATION,
    allowed_runner_refs=None,
):
    policy = {
        "trusted_issuers": [copy.deepcopy(issuer)],
        "approved_implementations": [
            {
                "check_id": check_id,
                "definition_version": definition_version,
                "implementation_id": implementation["implementation_id"],
                "implementation_version": implementation["implementation_version"],
                "implementation_sha256": implementation["implementation_sha256"],
            }
        ],
    }
    if allowed_runner_refs is not None:
        policy["allowed_runner_refs"] = list(allowed_runner_refs)
    return policy


def policy_for(
    *, issuer=JOB_ISSUER, implementation=IMPLEMENTATION, allowed_runner_refs=None
):
    return policy_for_check(
        "eci/hygiene/tach",
        check_contract.definition_for("tach").definition_version,
        issuer=issuer,
        implementation=implementation,
        allowed_runner_refs=allowed_runner_refs,
    )


def authority_for(issuer=JOB_ISSUER, implementation=IMPLEMENTATION):
    def verify(context):
        return (
            context["issuer_ref"] == issuer
            and context["implementation"] == implementation
        )

    return verify


def resolution_authority_for():
    return lambda context: context["resolution"].get("source_receipt_ref") is not None


def resolutions_for(receipt):
    resolutions = []
    for entry in receipt["entries"]:
        resolved = entry.get("resolved_expected") or {}
        resolution = {
            "expected_check_ref": entry["expected_check_ref"],
            "rule_ref": copy.deepcopy(resolved.get("rule_ref")),
            "source_ref": copy.deepcopy(resolved.get("source_ref")),
            "baseline_ref": copy.deepcopy(resolved.get("baseline_ref")),
            "rule_source_ref": {
                "name": "test-rule-source",
                "version": "1",
                "sha256": "7" * 64,
            },
            "source_receipt_ref": {
                "name": "test-source-receipt",
                "version": "1",
                "sha256": "8" * 64,
            },
        }
        if resolved.get("input_refs"):
            resolution["input_refs"] = copy.deepcopy(resolved["input_refs"])
        if resolved.get("baseline_ref"):
            resolution["baseline_source_ref"] = {
                "name": "test-baseline-source",
                "version": "1",
                "sha256": "6" * 64,
            }
        resolutions.append(resolution)
    return resolutions


def admit_evidence(projection, receipt, **kwargs):
    kwargs.setdefault("authority", authority_for())
    kwargs.setdefault("resolutions", resolutions_for(receipt))
    kwargs.setdefault("resolution_authority", resolution_authority_for())
    return admission.admit_evidence(projection, receipt, **kwargs)


def compact_evidence(result, report_ref, job_receipt):
    return {
        "result_id": result["result_id"],
        "status": result["status"],
        "result_origin": result["result_origin"],
        "native_exit_code": result.get("native_exit_code"),
        "job_ref": job_receipt["job_ref"],
        "run_ref": job_receipt["run_ref"],
        "attempt_ref": job_receipt["attempt_ref"],
        "runner_ref": job_receipt["runner_ref"],
        "report_ref": copy.deepcopy(report_ref),
        "job_receipt_ref": reconciliation._digest(job_receipt),
    }


def with_observed(receipt, evidence):
    updated = copy.deepcopy(receipt)
    entry = updated["entries"][0]
    entry["observed_result_ids"].append(evidence["result_id"])
    entry["observed_results"].append(copy.deepcopy(evidence))
    updated["receipt_ref"] = reconciliation._digest(
        {key: value for key, value in updated.items() if key != "receipt_ref"}
    )
    return updated


def admit(projection, receipt, report, job_receipt, policy):
    return admit_evidence(
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
    key = f"{receipt['scope_id']}/{receipt['entries'][0]['expected_check_ref']}"
    coverage = result["accepted_coverage_by_instance"][key]
    assert coverage["expected_check_ref"] == receipt["entries"][0]["expected_check_ref"]
    assert coverage["admission_status"] == "admitted"
    assert result["remaining_work"] == []
    assert result["verified_native_facts"][0]["native_exit_code"] == 0


def test_t1a_verified_native_fail_is_unsatisfied_not_accepted():
    projection, receipt, report, job_receipt = setup(status="failed")
    result = admit(projection, receipt, report, job_receipt, policy_for())
    entry = result["entries"][0]
    assert entry["origin_status"] == "verified"
    assert entry["obligation_status"] == "unsatisfied"
    assert entry["admitted"] is False
    assert result["summary"]["admitted"] == 0
    assert "native_failed" in entry["reasons"]
    assert result["status"] in {"partial", "rejected", "unknown"}


def test_t1a_missing_raw_report_is_unverified_and_remaining():
    projection, receipt, _report, job_receipt = setup()
    result = admit_evidence(
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
    result = admit_evidence(
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
    result = admit_evidence(
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
    result = admit_evidence(
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
        admit_evidence(
            projection,
            other_receipt,
            reports=[report],
            job_receipts=[job_receipt],
            policy=policy_for(),
        )


def test_t1a_policy_requires_trusted_issuer_and_approved_implementation():
    projection, receipt, report, job_receipt = setup()
    with pytest.raises(admission.AdmissionError, match="admission_policy"):
        admit_evidence(
            projection,
            receipt,
            reports=[report],
            job_receipts=[job_receipt],
            policy={"trusted_issuers": [], "approved_implementations": []},
        )
    assert json.dumps(policy_for()) == json.dumps(policy_for())


def test_t1a_wrong_source_is_verified_but_unsatisfied():
    projection, receipt, report, job_receipt = setup(result_source=OTHER_SOURCE)
    result = admit_evidence(
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
    assert result["summary"]["admitted"] == 0


def test_t1a_job_receipt_tamper_or_wrong_attempt_is_rejected():
    projection, receipt, report, job_receipt = setup()
    tampered = copy.deepcopy(job_receipt)
    tampered["attempt_ref"] = "attempt/2"
    result = admit_evidence(
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
    result = admit_evidence(
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
    admitted = admit_evidence(
        projection,
        receipt,
        reports=[report],
        job_receipts=[job_receipt],
        policy=policy_for(),
    )
    assert admitted["status"] == "partial"
    assert admitted["summary"]["admitted"] == 1
    assert len(admitted["accepted_coverage_by_instance"]) == 2
    assert (
        sum(
            item["admitted"]
            for item in admitted["accepted_coverage_by_instance"].values()
        )
        == 1
    )
    assert len(admitted["remaining_work"]) == 1
    assert (
        admitted["remaining_work"][0]["expected_check_ref"]
        == (vulture["expected_check_ref"])
    )


def test_t1a_without_authority_does_not_admit_production_coverage():
    projection, receipt, report, job_receipt = setup()
    result = admit_evidence(
        projection,
        receipt,
        reports=[report],
        job_receipts=[job_receipt],
        policy=policy_for(),
        authority=None,
    )
    entry = result["entries"][0]
    assert entry["origin_status"] == "unverified"
    assert entry["admitted"] is False
    assert result["summary"]["admitted"] == 0
    assert result["verified_native_facts"] == []
    assert result["integrity_validated_native_facts"]
    assert result["remaining_work"]


def test_t1a_arbitrary_authority_json_is_not_a_trust_boundary():
    projection, receipt, report, job_receipt = setup()
    with pytest.raises(
        admission.AdmissionError, match="admission_authority_verifier_required"
    ):
        admission.admit_evidence(
            projection,
            receipt,
            reports=[report],
            job_receipts=[job_receipt],
            policy=policy_for(),
            authority={"verification_status": "verified"},
        )


def test_t1a_authority_verifier_can_reject_a_matching_fixture():
    projection, receipt, report, job_receipt = setup()
    result = admit_evidence(
        projection,
        receipt,
        reports=[report],
        job_receipts=[job_receipt],
        policy=policy_for(),
        authority=lambda context: False,
    )
    entry = result["entries"][0]
    assert entry["origin_status"] == "rejected"
    assert entry["admitted"] is False
    assert result["summary"]["admitted"] == 0


def test_t1a_multi_requirement_witness_is_evaluated_collectively():
    definition = check_contract.definition_for("docs")
    rule = check_contract.rule_ref("docs", 1, digest="a" * 64)
    required_input = {
        "kind": "data",
        "path": "docs/guide.md",
        "sha256": "b" * 64,
    }
    inventory = check_contract.native_result(
        definition,
        source=STRONG_SOURCE,
        status="passed",
        result_origin="host",
        result_instance="inventory",
        native_exit_code=0,
        rule_ref=rule,
        tool=check_contract.tool_ref("git/ls-files"),
        evidence={"stdout": "docs/guide.md"},
        runner_ref=RUNNER_REF,
    )
    links = check_contract.native_result(
        definition,
        source=STRONG_SOURCE,
        status="passed",
        result_origin="host",
        result_instance="links",
        native_exit_code=0,
        rule_ref=rule,
        tool=check_contract.tool_ref("lychee/docs"),
        input_refs=[
            {"kind": "check_result", "result_id": inventory["result_id"]},
            required_input,
        ],
        evidence={"stdout": "ok"},
        runner_ref=RUNNER_REF,
    )
    entry = make_entry("docs", rule_ref=rule, input_refs=[required_input])
    projection = make_projection(entry)
    routing = routing_for(entry)
    resolution = {
        "expected_check_ref": entry["expected_check_ref"],
        "rule_ref": copy.deepcopy(rule),
        "source_ref": copy.deepcopy(STRONG_SOURCE),
        "baseline_ref": None,
        "rule_source_ref": {"name": "docs-rule", "version": "1", "sha256": "7" * 64},
        "source_receipt_ref": {
            "name": "docs-source",
            "version": "1",
            "sha256": "8" * 64,
        },
    }
    report = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [
            {
                "name": "reconciled",
                "verification_implementation": copy.deepcopy(IMPLEMENTATION),
                "native_results": [inventory, links],
            }
        ],
    }
    report_ref = report_ref_for(report)
    job_receipt = job_receipt_for(report_ref)
    receipt = reconciliation.reconcile_expected_results(
        projection,
        routing=[routing],
        resolutions=[resolution],
        observations=[
            {
                "job_ref": "job-1",
                "run_ref": RUN_REF,
                "attempt_ref": ATTEMPT_REF,
                "runner_ref": RUNNER_REF,
                "report": report,
                "report_ref": report_ref,
                "native_results": [inventory, links],
                "job_receipt": job_receipt,
            }
        ],
    )
    assert set(receipt["entries"][0]["selected_result_ids"]) == {
        inventory["result_id"],
        links["result_id"],
    }
    admitted = admit_evidence(
        projection,
        receipt,
        reports=[report],
        job_receipts=[job_receipt],
        policy=policy_for_check(definition.check_id, definition.definition_version),
    )
    assert admitted["status"] == "admitted"
    assert admitted["entries"][0]["admitted"] is True
    assert set(admitted["entries"][0]["result_ids"]) == {
        inventory["result_id"],
        links["result_id"],
    }


def test_t1a_unrelated_bad_observation_does_not_contaminate_selected_witness():
    projection, receipt, report, job_receipt = setup()
    bad_result = native(status="failed")
    bad_report = report_for(bad_result)
    bad_report_ref = report_ref_for(bad_report)
    bad_job_receipt = job_receipt_for(bad_report_ref)
    evidence = compact_evidence(bad_result, bad_report_ref, bad_job_receipt)
    updated = with_observed(receipt, evidence)
    result = admit_evidence(
        projection,
        updated,
        reports=[report, bad_report],
        job_receipts=[job_receipt, bad_job_receipt],
        policy=policy_for(),
    )
    entry = result["entries"][0]
    assert entry["admitted"] is True
    assert entry["origin_status"] == "verified"
    assert entry["observed_diagnostics"]
    assert result["summary"]["admitted"] == 1


def test_t1a_new014_shared_report_verified_failure_blocks_selected_witness():
    entry = make_entry()
    projection = make_projection(entry)
    passed_result = native(status="passed", source=STRONG_SOURCE)
    failed_result = native(status="failed", source=STRONG_SOURCE)
    shared_report = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [
            {
                "name": "reconciled",
                "verification_implementation": copy.deepcopy(IMPLEMENTATION),
                "native_results": [passed_result, failed_result],
            }
        ],
    }
    shared_report_ref = report_ref_for(shared_report)
    shared_job_receipt = job_receipt_for(shared_report_ref)
    receipt = reconciliation.reconcile_expected_results(
        projection,
        routing=[routing_for(entry)],
        resolutions=[resolution_for(entry)],
        observations=[
            {
                "job_ref": "job-1",
                "run_ref": RUN_REF,
                "attempt_ref": ATTEMPT_REF,
                "runner_ref": RUNNER_REF,
                "report": shared_report,
                "report_ref": copy.deepcopy(shared_report_ref),
                "native_results": [passed_result, failed_result],
                "job_receipt": copy.deepcopy(shared_job_receipt),
            }
        ],
    )
    result = admit_evidence(
        projection,
        receipt,
        reports=[shared_report],
        job_receipts=[shared_job_receipt],
        policy=policy_for(),
    )
    entry_result = result["entries"][0]
    assert entry_result["admitted"] is False
    assert entry_result["status"] == "unsatisfied"
    assert entry_result["obligation_status"] == "unsatisfied"
    assert "shared_report_authenticated_failure" in entry_result["reasons"]
    assert "native_failed" in entry_result["reasons"]
    assert result["summary"]["admitted"] == 0
    assert len(result["remaining_work"]) == 1
    assert (
        result["remaining_work"][0]["reason"] == "shared_report_authenticated_failure"
    )


def prerequisite_scenario(
    *,
    prereq_status="passed",
    prereq_job_receipt=True,
    prereq_issuer=JOB_ISSUER,
    prereq_attempt_ref=ATTEMPT_REF,
    consumed_id=None,
):
    """Dependent tach result that consumes a ``check_result`` prerequisite."""
    prereq = native(status=prereq_status, source=STRONG_SOURCE)
    prereq_report = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [
            {
                "name": "docs-inventory",
                "status": prereq_status,
                "verification_implementation": copy.deepcopy(IMPLEMENTATION),
                "native_results": [prereq],
            }
        ],
    }
    prereq_report_ref = report_ref_for(prereq_report)
    prereq_job_receipt_doc = {
        **job_receipt_for(prereq_report_ref, issuer=prereq_issuer),
        "job_ref": "job-prereq",
        "attempt_ref": prereq_attempt_ref,
    }
    prereq_input = [
        {"kind": "check_result", "result_id": consumed_id or prereq["result_id"]}
    ]
    entry = make_entry(source=STRONG_SOURCE, input_refs=prereq_input)
    projection = make_projection(entry, source=STRONG_SOURCE)
    dependent = check_contract.native_result(
        check_contract.definition_for("tach"),
        source=copy.deepcopy(STRONG_SOURCE),
        status="passed",
        result_origin="host",
        result_instance="run-1",
        native_exit_code=0,
        evidence={"stdout": ""},
        rule_ref=copy.deepcopy(RULE_TACH),
        input_refs=copy.deepcopy(prereq_input),
        runner_ref=RUNNER_REF,
        profile_ref=None,
        tool=check_contract.tool_ref("tach/check"),
    )
    report = report_for(dependent)
    report_ref = report_ref_for(report)
    job_receipt = job_receipt_for(report_ref)
    resolution = {**resolution_for(entry), "input_refs": copy.deepcopy(prereq_input)}
    receipt = reconciliation.reconcile_expected_results(
        projection,
        routing=[routing_for(entry)],
        resolutions=[resolution],
        observations=[observation_for(report, report_ref, job_receipt)],
    )
    assert receipt["entries"][0]["selected_result_ids"] == [dependent["result_id"]]
    job_receipts = [job_receipt]
    if prereq_job_receipt:
        job_receipts.append(prereq_job_receipt_doc)
    result = admit_evidence(
        projection,
        receipt,
        reports=[report, prereq_report],
        job_receipts=job_receipts,
        policy=policy_for(),
        resolutions=[resolution],
    )
    return result, result["entries"][0], prereq


def test_t1a_new014_authenticated_prerequisite_pass_admits_dependent():
    result, entry_result, prereq = prerequisite_scenario(prereq_status="passed")
    assert entry_result["admitted"] is True
    assert result["remaining_work"] == []
    derived = [
        fact
        for fact in entry_result["facts"]
        if fact["result_id"] == prereq["result_id"]
    ]
    assert derived and derived[0]["selected"] is False
    assert derived[0]["issuer_status"] == "verified"


@pytest.mark.parametrize(
    "scenario,reason",
    [
        ({"prereq_job_receipt": False}, "prerequisite_job_receipt_missing"),
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
        ({"consumed_id": "sha256:" + "a" * 64}, "prerequisite_result_missing"),
    ],
)
def test_t1a_new014_unauthenticated_prerequisite_pass_cannot_satisfy_dependency(
    scenario, reason
):
    result, entry_result, _prereq = prerequisite_scenario(
        prereq_status="passed", **scenario
    )
    assert entry_result["admitted"] is False
    assert entry_result["status"] == "unverified"
    assert entry_result["obligation_status"] == "unknown"
    assert reason in entry_result["reasons"]
    assert "prerequisite_result_failed" not in entry_result["reasons"]
    assert len(result["remaining_work"]) == 1


def test_t1a_new014_prerequisite_from_other_attempt_is_scope_mismatch():
    result, entry_result, _prereq = prerequisite_scenario(
        prereq_status="passed", prereq_attempt_ref="attempt/0"
    )
    assert entry_result["admitted"] is False
    assert "prerequisite_result_scope_mismatch" in entry_result["reasons"]
    assert len(result["remaining_work"]) == 1


def test_t1a_new014_authenticated_prerequisite_failure_blocks_dependent():
    result, entry_result, prereq = prerequisite_scenario(prereq_status="failed")
    assert entry_result["admitted"] is False
    assert entry_result["status"] == "unsatisfied"
    assert entry_result["obligation_status"] == "unsatisfied"
    assert "prerequisite_result_failed" in entry_result["reasons"]
    assert len(result["remaining_work"]) == 1
    assert any(
        fact["result_id"] == prereq["result_id"] and fact["status"] == "failed"
        for fact in entry_result["facts"]
    )


def test_t1a_new014_unrelated_same_name_failure_does_not_contaminate():
    projection, _receipt, report, _job_receipt = setup()
    # Even when an authorized report carries an informational blocked_by edge
    # naming a check that happens to fail in a separate, unconsumed Report, no
    # failure propagates across unrelated reports without a typed check_result.
    authorized_report = copy.deepcopy(report)
    authorized_report["checks"][0]["plan_authorized"] = True
    authorized_report["checks"][0]["check_contract"] = {
        "kind": check_contract.CHECK_CONTRACT_KIND,
        "schema_version": check_contract.CONTRACT_VERSION,
        "status": "valid",
    }
    authorized_report["checks"][0]["blocked_by"] = [
        {"producer_ref": {"kind": "check", "name": "docs-inventory"}}
    ]
    auth_ref = report_ref_for(authorized_report)
    auth_receipt_doc = job_receipt_for(auth_ref)
    entry = projection["checks"][0]
    auth_receipt = reconciliation.reconcile_expected_results(
        projection,
        routing=[routing_for(entry)],
        resolutions=[resolution_for(entry)],
        observations=[observation_for(authorized_report, auth_ref, auth_receipt_doc)],
    )
    unrelated_failed = native(status="failed", source=STRONG_SOURCE)
    unrelated_report = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [
            {
                "name": "docs-inventory",
                "status": "failed",
                "verification_implementation": copy.deepcopy(IMPLEMENTATION),
                "native_results": [unrelated_failed],
            }
        ],
    }
    unrelated_ref = report_ref_for(unrelated_report)
    unrelated_receipt = {
        **job_receipt_for(unrelated_ref),
        "job_ref": "job-unrelated",
    }
    result = admit_evidence(
        projection,
        auth_receipt,
        reports=[authorized_report, unrelated_report],
        job_receipts=[auth_receipt_doc, unrelated_receipt],
        policy=policy_for(),
    )
    entry_result = result["entries"][0]
    assert entry_result["admitted"] is True
    assert result["remaining_work"] == []


def test_t1a_new014_transitive_prerequisite_and_unapproved_impl_block_dependent():
    unapproved_impl = {
        **IMPLEMENTATION,
        "implementation_sha256": "f" * 64,
    }
    prereq = native(status="passed", source=STRONG_SOURCE)
    prereq_report = report_for(prereq, implementation=unapproved_impl)
    prereq_ref = report_ref_for(prereq_report)
    prereq_receipt = {**job_receipt_for(prereq_ref), "job_ref": "job-prereq"}
    prereq_input = [{"kind": "check_result", "result_id": prereq["result_id"]}]
    entry = make_entry(source=STRONG_SOURCE, input_refs=prereq_input)
    projection = make_projection(entry, source=STRONG_SOURCE)
    dependent = check_contract.native_result(
        check_contract.definition_for("tach"),
        source=copy.deepcopy(STRONG_SOURCE),
        status="passed",
        result_origin="host",
        result_instance="run-1",
        native_exit_code=0,
        evidence={"stdout": ""},
        rule_ref=copy.deepcopy(RULE_TACH),
        input_refs=copy.deepcopy(prereq_input),
        runner_ref=RUNNER_REF,
        profile_ref=None,
        tool=check_contract.tool_ref("tach/check"),
    )
    report = report_for(dependent)
    ref = report_ref_for(report)
    jr = job_receipt_for(ref)
    res = {**resolution_for(entry), "input_refs": copy.deepcopy(prereq_input)}
    receipt = reconciliation.reconcile_expected_results(
        projection,
        routing=[routing_for(entry)],
        resolutions=[res],
        observations=[observation_for(report, ref, jr)],
    )
    unapproved_result = admit_evidence(
        projection,
        receipt,
        reports=[report, prereq_report],
        job_receipts=[jr, prereq_receipt],
        policy=policy_for(),
        resolutions=[res],
        authority=lambda ctx: ctx["issuer_ref"] == JOB_ISSUER,
    )
    assert unapproved_result["entries"][0]["admitted"] is False
    assert (
        "prerequisite_implementation_unapproved"
        in unapproved_result["entries"][0]["reasons"]
    )

    # Transitive chain: dependent -> middle -> root(missing receipt).
    root_res = native(status="passed", source=STRONG_SOURCE)
    root_report = report_for(root_res)
    middle = check_contract.native_result(
        check_contract.definition_for("tach"),
        source=copy.deepcopy(STRONG_SOURCE),
        status="passed",
        result_origin="host",
        result_instance="mid-1",
        native_exit_code=0,
        evidence={"stdout": ""},
        rule_ref=copy.deepcopy(RULE_TACH),
        input_refs=[{"kind": "check_result", "result_id": root_res["result_id"]}],
        runner_ref=RUNNER_REF,
        profile_ref=None,
        tool=check_contract.tool_ref("tach/check"),
    )
    mid_report = report_for(middle)
    mid_ref = report_ref_for(mid_report)
    mid_jr = {**job_receipt_for(mid_ref), "job_ref": "job-mid"}
    top_input = [{"kind": "check_result", "result_id": middle["result_id"]}]
    top_entry = make_entry(source=STRONG_SOURCE, input_refs=top_input)
    top_proj = make_projection(top_entry, source=STRONG_SOURCE)
    top = check_contract.native_result(
        check_contract.definition_for("tach"),
        source=copy.deepcopy(STRONG_SOURCE),
        status="passed",
        result_origin="host",
        result_instance="top-1",
        native_exit_code=0,
        evidence={"stdout": ""},
        rule_ref=copy.deepcopy(RULE_TACH),
        input_refs=copy.deepcopy(top_input),
        runner_ref=RUNNER_REF,
        profile_ref=None,
        tool=check_contract.tool_ref("tach/check"),
    )
    top_report = report_for(top)
    top_ref = report_ref_for(top_report)
    top_jr = job_receipt_for(top_ref)
    top_res = {**resolution_for(top_entry), "input_refs": copy.deepcopy(top_input)}
    top_receipt = reconciliation.reconcile_expected_results(
        top_proj,
        routing=[routing_for(top_entry)],
        resolutions=[top_res],
        observations=[observation_for(top_report, top_ref, top_jr)],
    )
    transitive_res = admit_evidence(
        top_proj,
        top_receipt,
        reports=[top_report, mid_report, root_report],
        job_receipts=[top_jr, mid_jr],
        policy=policy_for(),
        resolutions=[top_res],
    )
    assert transitive_res["entries"][0]["admitted"] is False
    assert "prerequisite_job_receipt_missing" in transitive_res["entries"][0]["reasons"]


def test_t1a_new014_same_job_attempt_independent_checks_isolate_pass_and_fail():
    tach_entry = make_entry("tach", rule_ref=RULE_TACH, source=STRONG_SOURCE)
    vulture_entry = make_entry("vulture", rule_ref=RULE_VULTURE, source=STRONG_SOURCE)
    projection = make_projection([tach_entry, vulture_entry], source=STRONG_SOURCE)
    tach_pass = native(status="passed", source=STRONG_SOURCE, rule=RULE_TACH)
    vulture_def = check_contract.definition_for("vulture")
    vulture_fail = check_contract.native_result(
        vulture_def,
        source=copy.deepcopy(STRONG_SOURCE),
        status="failed",
        result_origin="host",
        result_instance="run-1",
        native_exit_code=1,
        evidence={"stdout": "unused symbol"},
        rule_ref=copy.deepcopy(RULE_VULTURE),
        runner_ref=RUNNER_REF,
        profile_ref=None,
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
    job_receipt = job_receipt_for(report_ref)
    resolutions = [
        resolution_for(tach_entry),
        {
            **resolution_for(vulture_entry),
            "rule_ref": copy.deepcopy(RULE_VULTURE),
        },
    ]
    receipt = reconciliation.reconcile_expected_results(
        projection,
        routing=[routing_for(tach_entry), routing_for(vulture_entry)],
        resolutions=resolutions,
        observations=[
            {
                "job_ref": "job-1",
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
        "trusted_issuers": [copy.deepcopy(JOB_ISSUER)],
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
    result = admit_evidence(
        projection,
        receipt,
        reports=[report],
        job_receipts=[job_receipt],
        policy=policy,
        resolutions=resolutions,
    )
    assert result["status"] == "partial"
    by_name = {item["name"]: item for item in result["entries"]}
    assert by_name["tach"]["admitted"] is True
    assert by_name["tach"]["obligation_status"] == "satisfied"
    assert by_name["vulture"]["admitted"] is False
    assert by_name["vulture"]["obligation_status"] == "unsatisfied"
    assert "native_failed" in by_name["vulture"]["reasons"]
    assert [item["expected_check_ref"] for item in result["remaining_work"]] == [
        vulture_entry["expected_check_ref"]
    ]


def test_t1a_new014_different_attempt_verified_failure_preserved_for_gate_projection():
    projection, receipt, report, job_receipt = setup()
    failed_result = native(status="failed")
    failed_report = report_for(failed_result)
    failed_report_ref = report_ref_for(failed_report)
    # Prior attempt (attempt/0) failed with verified issuer; current attempt/1
    # passed. T1-a keeps the attempt/1 selected witness while preserving the
    # verified failure in facts/diagnostics for #61 R1 adjudication.
    failed_job_receipt = {
        **job_receipt_for(failed_report_ref),
        "attempt_ref": "attempt/0",
    }
    evidence = compact_evidence(failed_result, failed_report_ref, failed_job_receipt)
    updated = with_observed(receipt, evidence)
    result = admit_evidence(
        projection,
        updated,
        reports=[report, failed_report],
        job_receipts=[job_receipt, failed_job_receipt],
        policy=policy_for(),
    )
    entry = result["entries"][0]
    assert entry["admitted"] is True
    assert "shared_report_authenticated_failure" not in entry["reasons"]
    assert "native_failed" in entry["reasons"]
    assert any(
        fact["status"] == "failed"
        and fact["integrity_status"] == "verified"
        and fact["issuer_status"] == "verified"
        for fact in entry["observed_diagnostics"]
    )


def test_t1a_shared_report_contamination_blocks_selected_witness():
    projection, receipt, report, job_receipt = setup()
    bad_result = native(status="failed")
    bad_report = report_for(bad_result)
    # Deliberately point the bad observation at the selected report identity.
    evidence = compact_evidence(
        bad_result,
        receipt["entries"][0]["selected_results"][0]["report_ref"],
        job_receipt,
    )
    updated = with_observed(receipt, evidence)
    result = admit_evidence(
        projection,
        updated,
        reports=[report, bad_report],
        job_receipts=[job_receipt],
        policy=policy_for(),
    )
    entry = result["entries"][0]
    assert entry["admitted"] is False
    assert entry["origin_status"] == "rejected"
    assert "selected_witness_contaminated" in entry["reasons"]


def test_t1a_schema_v2_same_name_refs_are_separate_instances():
    from test_reconciliation import (
        EVENT as V2_EVENT,
    )
    from test_reconciliation import (
        HYGIENE_RULE,
        PROFILE,
        PROFILE_REF,
        PROFILE_SOURCE,
        SOURCES_RULE,
    )
    from test_reconciliation import (
        SELECTION_RULE as V2_RULE,
    )
    from test_reconciliation import (
        WEAK_SOURCE as V2_SOURCE,
    )
    from test_reconciliation import (
        make_entry as fixture_entry,
    )

    other_event = {**V2_EVENT, "head_ref": "refs/heads/topic2"}
    first = fixture_entry(
        "tach",
        rule_ref=RULE_TACH,
        source=V2_SOURCE,
        event=V2_EVENT,
        selection_rule=V2_RULE,
        profile_ref=PROFILE_REF,
    )
    second = fixture_entry(
        "tach",
        rule_ref=RULE_TACH,
        source=OTHER_SOURCE,
        event=other_event,
        selection_rule=V2_RULE,
        profile_ref=PROFILE_REF,
    )

    def scope_for(event, source):
        return expected_checks.scope_identity(
            None,
            PROFILE_REF,
            PROFILE_SOURCE,
            event,
            source,
            V2_RULE,
            HYGIENE_RULE,
            SOURCES_RULE,
            profile_sha256=PROFILE["profile_sha256"],
            evaluated_at=PROFILE["evaluated_at"],
        )

    def ref_for(entry, event, source):
        return {
            "source_ref": "refs/heads/topic",
            "local_ref": "refs/heads/topic",
            "remote_ref": "origin",
            "ref_action": "update",
            "force_push": False,
            "baseline_status": "ok",
            "event_ref": copy.deepcopy(event),
            "source": copy.deepcopy(source),
            "scope_id": scope_for(event, source),
            "expected_set_ref": expected_checks.set_identity(
                [entry], (), schema_version=2
            ),
            "checks": [entry],
            "non_check_obligations": [],
            "status": "partial",
        }

    first_ref = ref_for(first, V2_EVENT, V2_SOURCE)
    second_ref = ref_for(second, other_event, OTHER_SOURCE)
    projection = {
        "kind": expected_checks.EXPECTED_KIND,
        "schema_version": 2,
        "projection_source": "effective_profile",
        "status": "partial",
        "reason": "test",
        "effective_profile": copy.deepcopy(PROFILE),
        "trusted_admission_status": "not_evaluated",
        "hygiene_rule_ref": copy.deepcopy(HYGIENE_RULE),
        "sources_rule_ref": copy.deepcopy(SOURCES_RULE),
        "selection_rule_ref": copy.deepcopy(V2_RULE),
        "event_ref": copy.deepcopy(V2_EVENT),
        "source_ref": copy.deepcopy(V2_SOURCE),
        "checks": [],
        "non_check_obligations": [],
        "consumer": None,
        "profile_ref": copy.deepcopy(PROFILE_REF),
        "profile_source": PROFILE_SOURCE,
        "consumer_completeness": "per_ref_container_only",
        "per_ref": [first_ref, second_ref],
    }
    result = native(profile_ref=PROFILE_REF)
    report = report_for(result)
    report_ref = report_ref_for(report)
    job_receipt = job_receipt_for(report_ref)
    first_receipt = reconciliation.reconcile_expected_results(
        projection,
        ref_scope_id=first_ref["scope_id"],
        routing=[routing_for(first)],
        resolutions=[resolution_for(first)],
        observations=[observation_for(report, report_ref, job_receipt)],
    )
    second_native = native(source=OTHER_SOURCE, profile_ref=PROFILE_REF)
    second_report = report_for(second_native)
    second_report_ref = report_ref_for(second_report)
    second_job_receipt = job_receipt_for(second_report_ref)
    second_resolution = {
        "expected_check_ref": second["expected_check_ref"],
        "rule_ref": copy.deepcopy(RULE_TACH),
        "source_ref": copy.deepcopy(OTHER_SOURCE),
        "rule_source_ref": {"name": "docs-rule", "version": "1", "sha256": "7" * 64},
        "source_receipt_ref": {
            "name": "docs-source",
            "version": "1",
            "sha256": "8" * 64,
        },
    }
    second_receipt = reconciliation.reconcile_expected_results(
        projection,
        ref_scope_id=second_ref["scope_id"],
        routing=[routing_for(second)],
        resolutions=[second_resolution],
        observations=[
            observation_for(second_report, second_report_ref, second_job_receipt)
        ],
    )
    ref_a_verifier = lambda context: context["scope_id"] == first_ref["scope_id"]
    first_result = admit_evidence(
        projection,
        first_receipt,
        reports=[report],
        job_receipts=[job_receipt],
        policy=policy_for(),
        ref_scope_id=first_ref["scope_id"],
        resolution_authority=ref_a_verifier,
    )
    second_result = admit_evidence(
        projection,
        second_receipt,
        reports=[second_report],
        job_receipts=[second_job_receipt],
        policy=policy_for(),
        resolutions=[second_resolution],
        ref_scope_id=second_ref["scope_id"],
        resolution_authority=ref_a_verifier,
    )
    assert first_result["status"] == "admitted", (
        first_result["entries"][0]["reasons"],
        first_result["entries"][0]["origin_status"],
        first_result["entries"][0]["integrity_status"],
        first_result["entries"][0].get("facts"),
    )
    assert first_result["entries"][0]["admitted"] is True
    assert second_result["entries"][0]["admitted"] is False
    assert "resolution_authority_unverified" in second_result["entries"][0]["reasons"]
    assert second_result["remaining_work"]
    first_key = f"{first_ref['scope_id']}/{first['expected_check_ref']}"
    second_key = f"{second_ref['scope_id']}/{second['expected_check_ref']}"
    assert first_key in first_result["accepted_coverage_by_instance"]
    assert second_key not in first_result["accepted_coverage_by_instance"]
    assert second_key in second_result["accepted_coverage_by_instance"]
    assert second_key not in first_result["accepted_coverage_by_instance"]


def test_t1a_expected_source_derivation_is_enforced():
    entry = make_entry(source=STRONG_SOURCE)
    projection = make_projection(entry, source=STRONG_SOURCE)
    result = native(source=STRONG_SOURCE)
    report = report_for(result)
    report_ref = report_ref_for(report)
    job_receipt = job_receipt_for(report_ref)
    resolution = resolution_for(entry)
    receipt = reconciliation.reconcile_expected_results(
        projection,
        routing=[routing_for(entry)],
        resolutions=[resolution],
        observations=[observation_for(report, report_ref, job_receipt)],
    )
    positive = admit_evidence(
        projection,
        receipt,
        reports=[report],
        job_receipts=[job_receipt],
        policy=policy_for(),
        resolutions=[resolution],
    )
    assert positive["status"] == "admitted"

    forged = copy.deepcopy(resolution)
    forged["source_ref"] = copy.deepcopy(OTHER_SOURCE)
    negative = admit_evidence(
        projection,
        receipt,
        reports=[report],
        job_receipts=[job_receipt],
        policy=policy_for(),
        resolutions=[forged],
    )
    entry_result = negative["entries"][0]
    assert entry_result["admitted"] is False
    assert entry_result["resolution_status"] == "unverified"
    assert negative["remaining_work"]


def test_t1a_duplicate_receipt_entry_is_rejected():
    projection, receipt, report, job_receipt = setup()
    duplicate = copy.deepcopy(receipt)
    duplicate["entries"].append(copy.deepcopy(duplicate["entries"][0]))
    duplicate["receipt_ref"] = reconciliation._digest(
        {key: value for key, value in duplicate.items() if key != "receipt_ref"}
    )
    with pytest.raises(
        admission.AdmissionError, match="admission_receipt_invalid"
    ) as raised:
        admit_evidence(
            projection,
            duplicate,
            reports=[report],
            job_receipts=[job_receipt],
            policy=policy_for(),
        )
    assert raised.value.native_code == "receipt_expected_check_ref_duplicate"
    with pytest.raises(
        reconciliation.ReconciliationError,
        match="receipt_expected_check_ref_duplicate",
    ):
        reconciliation.validate_receipt(duplicate)


def test_t1a_malformed_receipt_entry_is_not_an_attribute_error():
    projection, receipt, report, job_receipt = setup()
    malformed = copy.deepcopy(receipt)
    malformed["entries"].append("not-an-entry")
    malformed["receipt_ref"] = reconciliation._digest(
        {key: value for key, value in malformed.items() if key != "receipt_ref"}
    )
    with pytest.raises(
        admission.AdmissionError, match="admission_receipt_invalid"
    ) as raised:
        admission.admit_evidence(
            projection,
            malformed,
            reports=[report],
            job_receipts=[job_receipt],
            policy=policy_for(),
            authority=authority_for(),
            resolutions=[],
            resolution_authority=resolution_authority_for(),
        )
    assert raised.value.native_code == "receipt_entries_invalid"


def test_t1a_resolution_verifier_exception_keeps_diagnosis():
    projection, receipt, report, job_receipt = setup()

    def broken(context):
        raise RuntimeError("verifier crashed")

    result = admit_evidence(
        projection,
        receipt,
        reports=[report],
        job_receipts=[job_receipt],
        policy=policy_for(),
        resolution_authority=broken,
    )
    entry = result["entries"][0]
    assert entry["resolution_status"] == "rejected"
    assert "resolution_authority_verifier_error" in entry["reasons"]
    assert entry["admitted"] is False


def _bound_source(
    path_hashes,
    *,
    snapshot="worktree",
    actual="worktree",
    head_commit="2" * 40,
    tree="3" * 40,
    scope_kind="check_input_closure",
    binding_status=None,
    materialization_ref=None,
):
    canonical_paths = {str(k).replace("\\", "/"): v for k, v in path_hashes.items()}
    digest = reconciliation._digest(canonical_paths).removeprefix("sha256:")
    source = check_contract.source_ref(
        snapshot,
        declared_target_tree=tree,
        actual_execution_source=actual,
        head_commit=head_commit,
        content_digest=digest,
        verified_scope=sorted(canonical_paths),
        scope_kind=scope_kind,
        binding_status=binding_status,
        materialization_ref=materialization_ref,
    )
    binding = {
        "algorithm": "sha256-paths",
        "digest": digest,
        "paths": dict(sorted(canonical_paths.items())),
    }
    return source, binding


def _prereq_multi_receipt_fixture(
    *,
    prereq_source=STRONG_SOURCE,
    dep_source=STRONG_SOURCE,
    prereq_binding=None,
    dep_binding=None,
    dep_extra_inputs=(),
    receipt_specs=(),
    alt_prereq_reports=(),
    trusted_issuers=None,
    expect_reconcile=True,
):
    docs_def = check_contract.definition_for("docs")
    prereq = check_contract.native_result(
        docs_def,
        source=copy.deepcopy(prereq_source),
        status="passed",
        result_origin="host",
        result_instance="prereq-inv",
        native_exit_code=0,
        evidence={"stdout": "docs/guide.md"},
        rule_ref=check_contract.rule_ref("docs", 1, digest="a" * 64),
        runner_ref=RUNNER_REF,
        profile_ref=None,
        tool=check_contract.tool_ref("git/ls-files"),
    )
    prereq_check_block = {
        "name": "docs-inventory",
        "status": "passed",
        "verification_implementation": copy.deepcopy(IMPLEMENTATION),
        "native_results": [prereq],
    }
    if prereq_binding is not None:
        prereq_check_block["source_content_binding"] = copy.deepcopy(prereq_binding)
    prereq_report = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [prereq_check_block],
    }
    prereq_report_ref = report_ref_for(prereq_report)
    all_reports_map = [prereq_report]
    alt_report_refs = []
    for extra_check_meta in alt_prereq_reports:
        alt_report = {
            "kind": reconciliation.EXECUTION_REPORT_KIND,
            "schema_version": 1,
            "checks": [
                {
                    **copy.deepcopy(prereq_check_block),
                    **copy.deepcopy(extra_check_meta),
                    "native_results": [prereq],
                }
            ],
        }
        all_reports_map.append(alt_report)
        alt_report_refs.append(report_ref_for(alt_report))
    prereq_input = [
        {"kind": "check_result", "result_id": prereq["result_id"]},
        *[copy.deepcopy(item) for item in dep_extra_inputs],
    ]
    entry = make_entry(source=dep_source, input_refs=prereq_input)
    projection = make_projection(entry, source=dep_source)
    dependent = check_contract.native_result(
        check_contract.definition_for("tach"),
        source=copy.deepcopy(dep_source),
        status="passed",
        result_origin="host",
        result_instance="run-1",
        native_exit_code=0,
        evidence={"stdout": ""},
        rule_ref=copy.deepcopy(RULE_TACH),
        input_refs=copy.deepcopy(prereq_input),
        runner_ref=RUNNER_REF,
        profile_ref=None,
        tool=check_contract.tool_ref("tach/check"),
    )
    report = report_for(dependent)
    if dep_binding is not None:
        report["checks"][0]["source_content_binding"] = copy.deepcopy(dep_binding)
    report_ref = report_ref_for(report)
    job_receipt = job_receipt_for(report_ref)
    resolution = {
        **resolution_for(entry),
        "source_ref": copy.deepcopy(dep_source),
        "input_refs": copy.deepcopy(prereq_input),
    }
    receipt = reconciliation.reconcile_expected_results(
        projection,
        routing=[routing_for(entry)],
        resolutions=[resolution],
        observations=[observation_for(report, report_ref, job_receipt)],
    )
    if expect_reconcile:
        assert receipt["entries"][0]["binding_status"] == "matched"
    prereq_receipts = []
    for spec in receipt_specs:
        target_report_idx = spec.get("report_idx")
        ref = (
            prereq_report_ref
            if target_report_idx is None
            else alt_report_refs[target_report_idx]
        )
        doc = {
            **job_receipt_for(ref, issuer=spec.get("issuer", JOB_ISSUER)),
            "job_ref": spec.get("job_ref", "job-prereq"),
            "attempt_ref": spec.get("attempt_ref", ATTEMPT_REF),
            "run_ref": spec.get("run_ref", RUN_REF),
            "runner_ref": spec.get("runner_ref", RUNNER_REF),
        }
        for opt in ("scope_id", "expected_set_ref", "invocation_ref", "event_ref"):
            if opt in spec and spec[opt] is not None:
                doc[opt] = spec[opt]
        prereq_receipts.append(doc)
    policy = policy_for()
    if trusted_issuers:
        policy["trusted_issuers"].extend(copy.deepcopy(trusted_issuers))
    policy["approved_implementations"].append(
        {
            "check_id": docs_def.check_id,
            "definition_version": docs_def.definition_version,
            **IMPLEMENTATION,
        }
    )
    accepted_issuers = [
        copy.deepcopy(JOB_ISSUER),
        *(copy.deepcopy(trusted_issuers or ())),
    ]
    result = admit_evidence(
        projection,
        receipt,
        reports=[report, *all_reports_map],
        job_receipts=[job_receipt, *prereq_receipts],
        policy=policy,
        authority=lambda ctx: (
            ctx["issuer_ref"] in accepted_issuers
            and ctx["implementation"] == IMPLEMENTATION
        ),
        resolutions=[resolution],
    )
    return result, result["entries"][0], prereq, prereq_receipts


def test_t1a_new014_multiple_receipts_select_unique_in_scope_regardless_of_order():
    # Create an out-of-scope authenticated receipt whose digest may sort before
    # or after the valid matching-scope receipt, plus an untrusted noise receipt.
    wrong_scope = {
        "job_ref": "job-prereq-000",
        "scope_id": "sha256:" + "0" * 64,
    }
    wrong_attempt = {
        "job_ref": "job-prereq-zzz",
        "attempt_ref": "attempt/0",
    }
    rogue = {
        "job_ref": "job-prereq-rogue",
        "issuer": {"name": "rogue", "version": "1", "sha256": "9" * 64},
    }
    right = {"job_ref": "job-prereq-match"}
    right_sha = None
    for ordered in (
        [wrong_scope, rogue, right],
        [right, wrong_scope, wrong_attempt],
        [wrong_attempt, wrong_scope, right],
    ):
        res, entry_res, prereq, created = _prereq_multi_receipt_fixture(
            receipt_specs=ordered
        )
        assert entry_res["admitted"] is True
        assert entry_res["status"] == "admitted"
        assert res["remaining_work"] == []
        matching = next(doc for doc in created if doc["job_ref"] == "job-prereq-match")
        expected_ref = reconciliation._digest(matching)
        if right_sha is None:
            right_sha = expected_ref
        else:
            assert expected_ref == right_sha
        prereq_facts = [
            f for f in entry_res["facts"] if f["result_id"] == prereq["result_id"]
        ]
        assert [f["job_receipt_ref"] for f in prereq_facts] == [right_sha]


def test_t1a_new014_duplicate_observation_allows_coverage_with_origin_tracing():
    # Under #66-C duplicate-observation rules, two distinct jobs observing the
    # exact same immutable Report, Issuer, Run, Attempt, Runner, and attribution
    # represent redundant delivery of ONE execution fact rather than a conflict.
    rec_a = {"job_ref": "job-prereq-alpha", "invocation_ref": "inv/1"}
    rec_b = {"job_ref": "job-prereq-beta", "invocation_ref": "inv/1"}
    observed_digests = None
    for ordered in ([rec_a, rec_b], [rec_b, rec_a]):
        res, entry_res, prereq, created = _prereq_multi_receipt_fixture(
            receipt_specs=ordered
        )
        assert entry_res["admitted"] is True
        assert res["remaining_work"] == []
        prereq_facts = [
            f for f in entry_res["facts"] if f["result_id"] == prereq["result_id"]
        ]
        assert len(prereq_facts) == 1
        pf = prereq_facts[0]
        assert pf["duplicate_observation"] is True
        assert pf["observed_job_refs"] == ["job-prereq-alpha", "job-prereq-beta"]
        expected_refs = sorted(reconciliation._digest(doc) for doc in created)
        assert pf["observed_job_receipt_refs"] == expected_refs
        if observed_digests is None:
            observed_digests = (pf["job_receipt_ref"], expected_refs)
        else:
            assert (pf["job_receipt_ref"], expected_refs) == observed_digests


def test_t1a_new014_conflicting_attribution_or_reports_preserve_conflict_not_guess():
    second_issuer = {"name": "secondary-issuer", "version": "1", "sha256": "8" * 64}
    # 1. Conflicting Report wrappers claiming same immutable result_id
    for ordered in (
        [{"job_ref": "job-a"}, {"job_ref": "job-b", "report_idx": 0}],
        [{"job_ref": "job-b", "report_idx": 0}, {"job_ref": "job-a"}],
    ):
        res, entry_res, _, _ = _prereq_multi_receipt_fixture(
            receipt_specs=ordered,
            alt_prereq_reports=[{"name": "docs-alt-wrapper"}],
        )
        assert entry_res["admitted"] is False
        assert "prerequisite_result_conflict" in entry_res["reasons"]
        assert len(res["remaining_work"]) == 1

    # 2. Genuine attribution conflicts (different issuer / runner / invocation /
    #    scope_id omission / duplicate (job_ref, run_ref, attempt_ref))
    conflict_pairs = (
        (
            {"job_ref": "job-a", "issuer": JOB_ISSUER},
            {"job_ref": "job-b", "issuer": second_issuer},
        ),
        (
            {"job_ref": "job-a", "runner_ref": "execution:host"},
            {"job_ref": "job-b", "runner_ref": "execution:dagger"},
        ),
        (
            {"job_ref": "job-a", "invocation_ref": "inv/1"},
            {"job_ref": "job-b", "invocation_ref": "inv/2"},
        ),
        (
            {"job_ref": "job-a", "invocation_ref": "inv/1"},
            {"job_ref": "job-b"},
        ),
        (
            {"job_ref": "job-same", "invocation_ref": "inv/1"},
            {"job_ref": "job-same", "invocation_ref": None},
        ),
    )
    for spec_a, spec_b in conflict_pairs:
        for ordered in ([spec_a, spec_b], [spec_b, spec_a]):
            res, entry_res, _, _ = _prereq_multi_receipt_fixture(
                receipt_specs=ordered,
                trusted_issuers=[second_issuer],
            )
            assert entry_res["admitted"] is False
            assert "prerequisite_result_conflict" in entry_res["reasons"]
            assert len(res["remaining_work"]) == 1


def test_t1a_new014_cross_check_per_check_source_and_consumer_view_closure():
    prereq_src, prereq_bind = _bound_source(
        {"docs/guide.md": "1" * 64},
        actual="worktree",
    )
    dep_overlay_src, dep_bind = _bound_source(
        {"src/app.py": "2" * 64, "tach.toml": "3" * 64, "docs/generated.md": "d" * 64},
        actual="dagger_directory",
        scope_kind="full_input_closure",
        binding_status="byte_tree",
        materialization_ref="sha256:" + "f" * 64,
    )
    res, entry_res, _, _ = _prereq_multi_receipt_fixture(
        prereq_source=prereq_src,
        dep_source=dep_overlay_src,
        prereq_binding=prereq_bind,
        dep_binding=dep_bind,
        dep_extra_inputs=(
            {
                "kind": "generated_input",
                "input_kind": "file",
                "path": "docs/generated.md",
                "sha256": "d" * 64,
            },
        ),
        receipt_specs=[{"job_ref": "job-prereq"}],
    )
    assert entry_res["admitted"] is True
    assert res["remaining_work"] == []

    # Same repo identity check still refuses weak/unknown prereq, drifted
    # commit/tree, or same-closure byte drift without overlay (even when dep
    # carries an unrelated generated_input!).
    different_commit = check_contract.source_ref(
        "worktree",
        declared_target_tree="3" * 40,
        actual_execution_source="worktree",
        head_commit="9" * 40,
        content_digest="a" * 64,
        verified_scope=["docs/guide.md"],
        scope_kind="check_input_closure",
    )
    for bad_prereq, bad_dep, extra in (
        (different_commit, dep_overlay_src, ()),
        (WEAK_SOURCE, STRONG_SOURCE, ()),
        (OTHER_SOURCE, STRONG_SOURCE, ()),
        (
            OTHER_SOURCE,
            STRONG_SOURCE,
            (
                {
                    "kind": "generated_input",
                    "input_kind": "file",
                    "path": "docs/unrelated.md",
                    "sha256": "d" * 64,
                },
            ),
        ),
    ):
        _res, bad_entry, _, _ = _prereq_multi_receipt_fixture(
            prereq_source=bad_prereq,
            dep_source=bad_dep,
            dep_extra_inputs=extra,
            receipt_specs=[{"job_ref": "job-prereq"}],
        )
        assert bad_entry["admitted"] is False
        assert "prerequisite_result_scope_mismatch" in bad_entry["reasons"]


def test_t1a_new014_overlapping_closure_byte_consistency_and_generated_overlay():
    shared_sha = "4" * 64
    drifted_shared = "5" * 64
    gen_old = "6" * 64
    gen_new = "7" * 64

    # 1. Different Check closures WITH real overlap (`docs/shared.md` & `ci-hygiene.toml`):
    #    when intersecting paths agree byte-for-byte, unrelated paths (`docs/only.md` vs
    #    `src/app.py`) and differing execution source (`worktree` vs `dagger_directory`)
    #    pass cleanly.
    p_src, p_bind = _bound_source(
        {
            "ci-hygiene.toml": "1" * 64,
            "docs/shared.md": shared_sha,
            "docs/only.md": "a" * 64,
        },
        actual="worktree",
    )
    d_src, d_bind = _bound_source(
        {
            "ci-hygiene.toml": "1" * 64,
            "docs/shared.md": shared_sha,
            "src/app.py": "b" * 64,
        },
        actual="dagger_directory",
    )
    res_ok, entry_ok, _, _ = _prereq_multi_receipt_fixture(
        prereq_source=p_src,
        dep_source=d_src,
        prereq_binding=p_bind,
        dep_binding=d_bind,
        receipt_specs=[{"job_ref": "job-prereq"}],
    )
    assert entry_ok["admitted"] is True
    assert res_ok["remaining_work"] == []

    # 2. Varying ONLY unrelated non-overlapping file (`docs/only.md`) keeps passing.
    p_unrel_src, p_unrel_bind = _bound_source(
        {
            "ci-hygiene.toml": "1" * 64,
            "docs/shared.md": shared_sha,
            "docs/only.md": "c" * 64,
        },
        actual="worktree",
    )
    res_unrel, entry_unrel, _, _ = _prereq_multi_receipt_fixture(
        prereq_source=p_unrel_src,
        dep_source=d_src,
        prereq_binding=p_unrel_bind,
        dep_binding=d_bind,
        receipt_specs=[{"job_ref": "job-prereq"}],
    )
    assert entry_unrel["admitted"] is True
    assert res_unrel["remaining_work"] == []

    # 3. Drifted byte on overlapping `docs/shared.md`:
    #    (a) no overlay -> rejected
    #    (b) unrelated generated_input -> still rejected
    #    (c) unproven overlap (missing source_content_binding) -> fail-closed rejected
    #    (d) tampered source_content_binding -> rejected
    d_drift_src, d_drift_bind = _bound_source(
        {
            "ci-hygiene.toml": "1" * 64,
            "docs/shared.md": drifted_shared,
            "src/app.py": "b" * 64,
        },
        actual="dagger_directory",
    )
    tampered_bind = copy.deepcopy(d_bind)
    tampered_bind["paths"]["docs/shared.md"] = "f" * 64
    for prereq_s, prereq_b, dep_s, dep_b, extra in (
        (p_src, p_bind, d_drift_src, d_drift_bind, ()),
        (
            p_src,
            p_bind,
            d_drift_src,
            d_drift_bind,
            (
                {
                    "kind": "generated_input",
                    "input_kind": "file",
                    "path": "docs/unrelated.md",
                    "sha256": gen_new,
                },
            ),
        ),
        (p_src, None, d_src, None, ()),
        (p_src, p_bind, d_src, tampered_bind, ()),
        (
            p_src,
            p_bind,
            d_drift_src,
            d_drift_bind,
            (
                {
                    "kind": "generated_input",
                    "input_kind": "file",
                    "path": "docs/shared.md",
                    "sha256": "e" * 64,  # mismatches drifted_shared
                },
            ),
        ),
    ):
        _r, bad_e, _, _ = _prereq_multi_receipt_fixture(
            prereq_source=prereq_s,
            dep_source=dep_s,
            prereq_binding=prereq_b,
            dep_binding=dep_b,
            dep_extra_inputs=extra,
            receipt_specs=[{"job_ref": "job-prereq"}],
        )
        assert bad_e["admitted"] is False
        assert "prerequisite_result_scope_mismatch" in bad_e["reasons"]

    # 4. Generated Overlay specifically proving the divergent overlapping path
    #    (`docs/gen.md`) while `docs/shared.md` & `ci-hygiene.toml` remain identical -> ADMITTED.
    p_gen_src, p_gen_bind = _bound_source(
        {
            "ci-hygiene.toml": "1" * 64,
            "docs/shared.md": shared_sha,
            "docs/gen.md": gen_old,
        },
        actual="worktree",
    )
    d_gen_src, d_gen_bind = _bound_source(
        {
            "ci-hygiene.toml": "1" * 64,
            "docs/shared.md": shared_sha,
            "docs/gen.md": gen_new,
            "src/app.py": "b" * 64,
        },
        actual="dagger_directory",
    )
    res_gen, entry_gen, _, _ = _prereq_multi_receipt_fixture(
        prereq_source=p_gen_src,
        dep_source=d_gen_src,
        prereq_binding=p_gen_bind,
        dep_binding=d_gen_bind,
        dep_extra_inputs=(
            {
                "kind": "generated_input",
                "input_kind": "file",
                "path": "docs/gen.md",
                "sha256": gen_new,
            },
        ),
        receipt_specs=[{"job_ref": "job-prereq"}],
    )
    assert entry_gen["admitted"] is True
    assert res_gen["remaining_work"] == []
