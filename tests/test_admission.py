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


def admit_evidence(projection, receipt, **kwargs):
    kwargs.setdefault("authority", authority_for())
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
    # Same Job/Run/Attempt labels as the selected observation, but an
    # independent Report and Job Receipt.
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
    second_receipt = reconciliation.reconcile_expected_results(
        projection,
        ref_scope_id=second_ref["scope_id"],
        routing=[],
        resolutions=[],
        observations=[],
    )
    first_result = admit_evidence(
        projection,
        first_receipt,
        reports=[report],
        job_receipts=[job_receipt],
        policy=policy_for(),
        ref_scope_id=first_ref["scope_id"],
    )
    second_result = admit_evidence(
        projection,
        second_receipt,
        reports=[],
        job_receipts=[],
        policy=policy_for(),
        ref_scope_id=second_ref["scope_id"],
    )
    assert first_result["status"] == "admitted", (
        first_result["entries"][0]["reasons"],
        first_result["entries"][0]["origin_status"],
        first_result["entries"][0]["integrity_status"],
        first_result["entries"][0].get("facts"),
    )
    assert first_result["entries"][0]["admitted"] is True
    assert second_result["entries"][0]["admitted"] is False
    first_key = f"{first_ref['scope_id']}/{first['expected_check_ref']}"
    second_key = f"{second_ref['scope_id']}/{second['expected_check_ref']}"
    assert first_key in first_result["accepted_coverage_by_instance"]
    assert second_key not in first_result["accepted_coverage_by_instance"]
    assert second_key in second_result["accepted_coverage_by_instance"]
    assert second_key not in first_result["accepted_coverage_by_instance"]
