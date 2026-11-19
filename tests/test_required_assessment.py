"""#61 R1 Required Assessment over contract samples and real #66-C receipts."""

import copy

import pytest

from engineering_ci import (
    check_contract,
    expected_checks,
    reconciliation,
    required_assessment,
)

PROFILE_REF = {"name": "org/engineering", "version": "1"}
PROFILE_SOURCE = "org-governance"
EVENT = {
    "base_ref": "refs/heads/main",
    "head_ref": "refs/heads/topic",
    "base_commit": "1" * 40,
    "head_commit": "2" * 40,
    "references": [],
}
SOURCE = check_contract.source_ref(
    "worktree",
    declared_target_tree="3" * 40,
    actual_execution_source="worktree",
    content_digest="c" * 64,
    verified_scope=["src/engineering_ci/x.py"],
    scope_kind="check_input_closure",
)
SELECTION_RULE = {
    "name": "engineering-ci.selection",
    "version": "1",
    "sha256": "a" * 64,
}
RULE_TACH = check_contract.rule_ref("tach", 1, digest="b" * 64)
RULE_SOURCE_REF = {"name": "hygiene-rule", "version": "1", "sha256": "4" * 64}
SOURCE_RECEIPT_REF = {"name": "p0-source-receipt", "version": "1", "sha256": "5" * 64}
CLOSURE_REF = {"name": "org/policy-closure", "version": "3", "sha256": "6" * 64}
AUTHORITY_REF = {"name": "org/governance-source", "version": "2", "sha256": "7" * 64}
BASIS_REF = {"name": "org/inapplicability-rule", "version": "1", "sha256": "0" * 64}
PHASE_ORDER_REF = {
    "name": "org/release-phase-order",
    "version": "1",
    "sha256": "3" * 64,
}
PHASE_ORDER = {
    "ref": copy.deepcopy(PHASE_ORDER_REF),
    "phases": ["pre_publish", "publish", "post_publish"],
}
VERIFIER_REF = {"name": "trivy", "version": "0.58.0", "sha256": "8" * 64}
VERIFIER_AUTH_REF = {
    "name": "org/verifier-approval",
    "version": "1",
    "sha256": "9" * 64,
}
VERIFIER_RULE_REF = {"name": "sbom/rule", "version": "2", "sha256": "a" * 64}
EVIDENCE_REF = {"name": "sbom", "version": "1", "sha256": "e" * 64}
EVALUATED_AT = "2026-11-16T01:00:00Z"
RUN_REF = "run/123"
ATTEMPT_REF = "attempt/1"
RUNNER_REF = "execution:host"
JOB_ISSUER_REF = {"name": "hook-runner", "version": "1", "sha256": "c" * 64}
ATTEMPT_QUALIFICATION_REF = {
    "name": "scheduler-attempt",
    "version": "1",
    "sha256": "d" * 64,
}
RESULT_ID = "sha256:" + "f" * 64
FAILURE_RESULT_ID = "sha256:" + "1" * 64


def make_profile(
    *,
    obligations=None,
    declared_checks=None,
    authority_verified=True,
    domain_complete=True,
    evaluated_at="2026-11-16T00:10:00Z",
    phase=None,
    allowed_inapplicability=None,
):
    profile = expected_checks.build_effective_profile(
        profile_ref=copy.deepcopy(PROFILE_REF),
        profile_revision="rev-2026-11-16",
        approval_source=PROFILE_SOURCE,
        authority_verified=authority_verified,
        effective_at="2026-11-16T00:00:00Z",
        evaluated_at=evaluated_at,
        context="release",
        action="publish",
        target="candidate",
        phase=phase,
        required_obligations=list(
            obligations
            if obligations is not None
            else [{"obligation_ref": "o/tach", "check_names": ["tach"]}]
        ),
        advisory_checks=[],
        allowed_inapplicability=list(allowed_inapplicability or ()),
        declared_checks=list(
            declared_checks if declared_checks is not None else ["tach"]
        ),
        domain_complete=domain_complete,
    )
    return profile


def make_entry(
    name="tach",
    *,
    required_by=("o/tach",),
    rule_ref=RULE_TACH,
    event=None,
    source=None,
    applicability="selected",
):
    definition = check_contract.definition_for(name)
    entry = {
        "name": name,
        "check_id": definition.check_id,
        "definition_version": definition.definition_version,
        "definition_status": "resolved",
        "event_ref": copy.deepcopy(event or EVENT),
        "selection_rule_ref": copy.deepcopy(SELECTION_RULE),
        "rule_ref": copy.deepcopy(rule_ref),
        "rule_status": "resolved",
        "profile_ref": copy.deepcopy(PROFILE_REF),
        "applicability": {"status": applicability, "reason": "test"},
        "policy_status": "resolved",
        "source_ref": copy.deepcopy(source or SOURCE),
        "input_refs": [{"kind": "changed_path", "path": "src/engineering_ci/x.py"}],
        "baseline_ref": None,
        "obligation_refs": [
            {"obligation_ref": ref, "role": "required"} for ref in required_by
        ],
        "required_by_obligations": list(required_by),
        "advisory_for_obligations": [],
    }
    entry["expected_check_ref"] = expected_checks.entry_identity(entry)
    return entry


def make_projection(
    profile,
    checks,
    non_check=(),
    *,
    status="complete",
    event=None,
    source=None,
    phase_status=None,
):
    event = event or EVENT
    source = source or SOURCE
    phase = profile.get("phase")
    if phase_status is None:
        phase_status = "unverified" if phase is not None else "not_applicable"
    scope_id = expected_checks.scope_identity(
        "engineering-ci/required-assessment",
        PROFILE_REF,
        PROFILE_SOURCE,
        event,
        source,
        SELECTION_RULE,
        None,
        None,
        profile_sha256=profile["profile_sha256"],
        evaluated_at=profile["evaluated_at"],
    )
    return {
        "kind": expected_checks.EXPECTED_KIND,
        "schema_version": 2,
        "projection_source": "effective_profile",
        "status": status,
        "reason": "test",
        "scope_id": scope_id,
        "expected_set_ref": expected_checks.set_identity(
            checks, non_check, schema_version=2
        ),
        "selection_rule_ref": copy.deepcopy(SELECTION_RULE),
        "hygiene_rule_ref": None,
        "sources_rule_ref": None,
        "consumer": "engineering-ci/required-assessment",
        "profile_ref": copy.deepcopy(PROFILE_REF),
        "profile_source": PROFILE_SOURCE,
        "effective_profile": profile,
        "authority_status": "verified",
        "phase": phase,
        "phase_status": phase_status,
        "trusted_admission_status": "not_evaluated",
        "consumer_completeness": "expected_domain_complete",
        "observed_input_closure_status": "not_observed",
        "event_ref": copy.deepcopy(event),
        "source_ref": copy.deepcopy(source),
        "checks": checks,
        "non_check_obligations": list(non_check),
        "missing": [],
        "conflicts": [],
    }


def make_per_ref_container(
    profile,
    checks,
    *,
    ref_action="update",
    local_ref="refs/heads/main",
    remote_ref=None,
    event=None,
    source=None,
    non_check=(),
    child_status="complete",
    parent_status="partial",
    parent_completeness="per_ref_container_only",
    parent_phase_status=None,
):
    single = make_projection(
        profile,
        checks,
        non_check=non_check,
        event=event,
        source=source,
    )
    target_remote = local_ref if remote_ref is None else remote_ref
    child = {
        "source_ref": target_remote or local_ref,
        "local_ref": local_ref,
        "remote_ref": target_remote,
        "ref_action": ref_action,
        "force_push": False,
        "baseline_status": "ok",
        "event_ref": copy.deepcopy(event or EVENT),
        "source": copy.deepcopy(source or SOURCE),
        "scope_id": single["scope_id"],
        "expected_set_ref": single["expected_set_ref"],
        "checks": checks,
        "non_check_obligations": list(non_check),
        "conflicts": [],
        "status": child_status,
        "consumer_completeness": "per_ref",
        "effective_profile": profile,
    }
    container = copy.deepcopy(single)
    container["status"] = parent_status
    container["consumer_completeness"] = parent_completeness
    container["checks"] = []
    container["non_check_obligations"] = []
    container["expected_set_ref"] = expected_checks.set_identity(
        [], (), schema_version=2
    )
    if parent_phase_status is None:
        parent_phase_status = (
            "unverified" if profile.get("phase") is not None else "not_applicable"
        )
    container["phase"] = profile.get("phase")
    container["phase_status"] = parent_phase_status
    container["per_ref"] = [child]
    return container, child


def check_obligation(
    ref,
    check_refs,
    *,
    phase=None,
    applicability="required",
    target_ref="refs/heads/main",
    action="publish",
    candidate_digest=None,
    due_phase=None,
    due_at=None,
    basis_ref=None,
):
    return {
        "obligation_ref": ref,
        "kind": "check",
        "target_ref": target_ref,
        "action": action,
        "phase": phase,
        "candidate_digest": candidate_digest,
        "applicability": applicability,
        "reason": "required_by_effective_profile",
        "applicability_basis_ref": copy.deepcopy(basis_ref),
        "due_phase": due_phase,
        "due_at": due_at,
        "expected_check_refs": list(check_refs),
        "check_names": ["tach"],
    }


def non_check_obligation(
    ref,
    *,
    phase=None,
    applicability="required",
    target_ref="refs/heads/main",
    action="publish",
    candidate_digest=None,
    source_digest=None,
    due_phase=None,
    due_at=None,
    basis_ref=None,
    applicability_authorization=None,
    verifier_ref=VERIFIER_REF,
    verifier_authorization_ref=VERIFIER_AUTH_REF,
    verification_rule_ref=VERIFIER_RULE_REF,
    required_subject_fields=None,
):
    if required_subject_fields is None:
        required_subject_fields = ["commit"] if applicability == "required" else []
    return {
        "obligation_ref": ref,
        "kind": "non_check",
        "target_ref": target_ref,
        "action": action,
        "phase": phase,
        "candidate_digest": candidate_digest,
        "source_digest": source_digest,
        "applicability": applicability,
        "reason": "required_by_effective_profile",
        "applicability_basis_ref": copy.deepcopy(basis_ref),
        "applicability_authorization": copy.deepcopy(applicability_authorization),
        "due_phase": due_phase,
        "due_at": due_at,
        "verifier_ref": copy.deepcopy(verifier_ref),
        "verifier_authorization_ref": copy.deepcopy(verifier_authorization_ref),
        "verification_rule_ref": copy.deepcopy(verification_rule_ref),
        "required_subject_fields": list(required_subject_fields),
    }


def inapplicability_authorization(
    *,
    authority_ref=BASIS_REF,
    rule_ref=VERIFIER_RULE_REF,
    target_ref="refs/heads/main",
    action="publish",
    phase=None,
    obligation_ref="o/sbom",
    obligation_instance_ref=None,
    candidate_digest=None,
    source_digest=None,
    valid_from=None,
    valid_until=None,
    revoked_at=None,
):
    authorization = {
        "authority_ref": copy.deepcopy(authority_ref),
        "rule_ref": copy.deepcopy(rule_ref),
        "scope": {
            "target_ref": target_ref,
            "action": action,
            "phase": phase,
            "obligation_ref": obligation_ref,
            "candidate_digest": candidate_digest,
            "source_digest": source_digest,
        },
        "valid_from": valid_from,
        "valid_until": valid_until,
        "revoked_at": revoked_at,
    }
    if obligation_instance_ref is not None:
        authorization["obligation_instance_ref"] = obligation_instance_ref
    return authorization


def make_universe(
    profile,
    projection,
    obligations=(),
    *,
    phase="pre_publish",
    action="publish",
    target="candidate",
    context="release",
    refs=None,
    candidate=None,
    include_candidate=True,
    phase_order=None,
    include_phase_order=True,
    universe_status="complete",
    universe_reason=None,
    authority_status="verified",
    authority_override=None,
    valid_until=None,
    revoked_at=None,
    closure_status="closed",
    delegation_domains=(),
    check_domain_override=None,
    contract_sample=True,
):
    check_domain = {
        "scope_id": projection["scope_id"],
        "expected_set_ref": projection["expected_set_ref"],
        "status": projection["status"],
        "consumer_completeness": projection["consumer_completeness"],
    }
    if check_domain_override is not None:
        check_domain.update(check_domain_override)
    authority = {
        "status": authority_status,
        "source_ref": copy.deepcopy(AUTHORITY_REF),
        "approved_at": "2026-11-16T00:00:00Z",
        "effective_at": "2026-11-16T00:00:00Z",
        "valid_from": None,
        "valid_until": valid_until,
        "revoked_at": revoked_at,
        "evaluated_at": profile["evaluated_at"],
    }
    if authority_override is not None:
        authority.update(authority_override)
    universe = {
        "kind": required_assessment.UNIVERSE_KIND,
        "schema_version": 1,
        "profile": {
            "profile_ref": copy.deepcopy(profile["profile_ref"]),
            "profile_revision": profile["profile_revision"],
            "approval_source": profile["approval_source"],
            "profile_sha256": profile["profile_sha256"],
            "approval_digest": profile["approval_binding"]["approval_digest"],
        },
        "authority": authority,
        "governance_domain": {
            "closure_status": closure_status,
            "closure_ref": copy.deepcopy(CLOSURE_REF),
            "scope": "organization",
            "delegation_domains": [copy.deepcopy(item) for item in delegation_domains],
            "inheritance": [],
            "exclusions": [],
        },
        "evaluation": {
            "context": context,
            "action": action,
            "target": target,
            "phase": phase,
            "phase_order": (
                copy.deepcopy(phase_order if phase_order is not None else PHASE_ORDER)
                if include_phase_order
                else None
            ),
            "refs": copy.deepcopy(
                refs or [{"target_ref": "refs/heads/main", "ref_action": "update"}]
            ),
            "candidate": (
                copy.deepcopy(
                    candidate if candidate is not None else {"commit": "2" * 40}
                )
                if include_candidate
                else None
            ),
            "evaluated_at": profile["evaluated_at"],
        },
        "check_domain": check_domain,
        "obligations": [copy.deepcopy(item) for item in obligations],
        "universe_status": universe_status,
        "reason": universe_reason,
        "contract_sample": contract_sample,
    }
    universe["universe_ref"] = required_assessment._digest(universe)
    return universe


def coverage(
    projection,
    entry,
    result_id=RESULT_ID,
    *,
    status="admitted",
    source_kind="current_run",
    phase="pre_publish",
    target_ref="refs/heads/main",
    action="publish",
    candidate_digest=None,
    rule_ref=RULE_TACH,
    issuer_verified=True,
    raw_result_verified=True,
    receipt_binding_status="matched",
    equivalence_basis=None,
    reason=None,
):
    return {
        "scope_id": projection["scope_id"],
        "expected_check_ref": entry["expected_check_ref"],
        "check_name": entry["name"],
        "admission_status": status,
        "source_kind": source_kind,
        "witness_result_id": result_id,
        "raw_result_verified": raw_result_verified,
        "issuer_verified": issuer_verified,
        "origin_trace": {
            "run_ref": RUN_REF,
            "attempt_ref": ATTEMPT_REF,
            "runner_ref": RUNNER_REF,
            "result_origin": "host",
        },
        "applicable_to": {
            "target_ref": target_ref,
            "phase": phase,
            "action": action,
            "candidate_digest": candidate_digest,
            "rule_ref": copy.deepcopy(rule_ref),
            "profile_sha256": projection["effective_profile"]["profile_sha256"],
        },
        "receipt_binding_status": receipt_binding_status,
        "equivalence_basis": copy.deepcopy(equivalence_basis),
        "reason": reason,
    }


def historical_coverage(projection, entry, **kwargs):
    kwargs.setdefault("source_kind", "historical_reuse")
    kwargs.setdefault("receipt_binding_status", None)
    kwargs.setdefault(
        "equivalence_basis",
        {
            "verified": True,
            "closed_source_scope_kind": "check_input_closure",
            "raw_native_source_digest_equal": True,
            "raw_native_rule_and_profile_equal": True,
            "issuer_verified": True,
        },
    )
    return coverage(projection, entry, **kwargs)


def instance_key(projection, entry):
    return f"{projection['scope_id']}/{entry['expected_check_ref']}"


def make_admission(
    projection,
    *,
    coverage_entries=(),
    failures=None,
    adjudications=(),
    remaining=(),
    contract_sample=True,
    all_observations_reconciled=True,
):
    admission = {
        "kind": required_assessment.ADMISSION_KIND,
        "schema_version": 1,
        "phase": required_assessment.ADMISSION_PHASE,
        "scope_id": projection["scope_id"],
        "expected_set_ref": projection["expected_set_ref"],
        "expected_completeness": projection["consumer_completeness"],
        "all_observations_reconciled": all_observations_reconciled,
        "accepted_coverage_by_instance": {
            instance_key(projection, entry): copy.deepcopy(record)
            for entry, record in coverage_entries
        },
        "authenticated_failures_by_instance": copy.deepcopy(failures or {}),
        "adjudications": [copy.deepcopy(item) for item in adjudications],
        "remaining_work": [copy.deepcopy(item) for item in remaining],
        "required_gate_ready": False,
        "required_gate_block_reason": "test",
        "contract_sample": contract_sample,
    }
    admission["admission_ref"] = required_assessment._digest(admission)
    return admission


def failure(
    entry,
    *,
    result_id=FAILURE_RESULT_ID,
    failure_kind="native_failure",
    phase="pre_publish",
    target_ref="refs/heads/main",
    action="publish",
    issued_at="2026-11-16T00:50:00Z",
):
    return {
        "result_id": result_id,
        "failure_kind": failure_kind,
        "native_status": "failed",
        "origin_trace": {
            "run_ref": RUN_REF,
            "attempt_ref": ATTEMPT_REF,
            "runner_ref": RUNNER_REF,
            "result_origin": "host",
        },
        "applicable_to": {
            "target_ref": target_ref,
            "phase": phase,
            "action": action,
            "candidate_digest": None,
            "rule_ref": None,
            "profile_sha256": None,
        },
        "issued_at": issued_at,
        "reason": "native_failed",
    }


def adjudication(
    projection,
    entry,
    result_ids=(FAILURE_RESULT_ID,),
    *,
    disposition="failure_superseded",
    decided_at="2026-11-16T00:55:00Z",
    valid_until=None,
    revoked_at=None,
    authority_ref=AUTHORITY_REF,
    policy_ref=BASIS_REF,
):
    return {
        "scope_id": projection["scope_id"],
        "expected_check_ref": entry["expected_check_ref"],
        "disposition": disposition,
        "authority_ref": copy.deepcopy(authority_ref),
        "policy_ref": copy.deepcopy(policy_ref),
        "decided_at": decided_at,
        "valid_until": valid_until,
        "revoked_at": revoked_at,
        "applies_to_result_ids": list(result_ids),
        "reason": "policy_approved",
    }


def remaining_work(projection, entry, reason="raw_report_missing"):
    return {
        "scope_id": projection["scope_id"],
        "expected_check_ref": entry["expected_check_ref"],
        "check_name": entry["name"],
        "reason": reason,
    }


def non_check_expected(ref="o/sbom"):
    return {
        "obligation_ref": ref,
        "kind": "non_check",
        "status": "required",
        "reason": "required_by_effective_profile",
    }


def evidence_entry(
    ref="o/sbom",
    *,
    phase=None,
    result="satisfied",
    target_ref="refs/heads/main",
    action="publish",
    candidate_digest=None,
    source_digest=None,
    subject_ref=None,
    verifier_ref=VERIFIER_REF,
    verifier_authorization_ref=VERIFIER_AUTH_REF,
    verification_rule_ref=VERIFIER_RULE_REF,
    evaluated_at="2026-11-16T00:40:00Z",
    valid_until=None,
    revoked_at=None,
    reason=None,
):
    entry = {
        "obligation_ref": ref,
        "target_ref": target_ref,
        "action": action,
        "phase": phase,
        "candidate_digest": candidate_digest,
        "source_digest": source_digest,
        "verifier_ref": copy.deepcopy(verifier_ref),
        "verifier_authorization_ref": copy.deepcopy(verifier_authorization_ref),
        "verification_rule_ref": copy.deepcopy(verification_rule_ref),
        "result": result,
        "reason": reason,
        "evaluated_at": evaluated_at,
        "valid_until": valid_until,
        "revoked_at": revoked_at,
        "subject_ref": copy.deepcopy(
            subject_ref if subject_ref is not None else {"commit": "2" * 40}
        ),
        "evidence_ref": copy.deepcopy(EVIDENCE_REF),
    }
    return entry


def make_evidence(entries, *, contract_sample=True):
    document = {
        "kind": required_assessment.NON_CHECK_EVIDENCE_KIND,
        "schema_version": 1,
        "entries": [copy.deepcopy(entry) for entry in entries],
        "contract_sample": contract_sample,
    }
    return document


def report_ref_for(report):
    digest = reconciliation._digest(report).split(":", 1)[1]
    return {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": report["schema_version"],
        "sha256": digest,
    }


def native_result(entry, *, status="passed", result_instance="run-1"):
    definition = check_contract.definition_for(entry["name"])
    exit_code = 0 if status == "passed" else 1 if status == "failed" else None
    return check_contract.native_result(
        definition,
        source=copy.deepcopy(SOURCE),
        status=status,
        result_origin="host",
        result_instance=result_instance,
        native_exit_code=exit_code,
        evidence={"stdout": ""},
        rule_ref=copy.deepcopy(RULE_TACH),
        baseline_ref=None,
        input_refs=[dict(item) for item in entry.get("input_refs", ())],
        coverage=None,
        phase=None,
        runner_ref=RUNNER_REF,
        profile_ref=copy.deepcopy(PROFILE_REF),
        tool=check_contract.tool_ref("tach/check"),
    )


def make_receipt(projection, entry, *, status="passed"):
    result = native_result(entry, status=status)
    report = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [{"name": "reconciled", "native_results": [result]}],
    }
    report_ref = report_ref_for(report)
    observation = {
        "job_ref": "job/release",
        "run_ref": RUN_REF,
        "attempt_ref": ATTEMPT_REF,
        "runner_ref": RUNNER_REF,
        "report": report,
        "report_ref": copy.deepcopy(report_ref),
        "native_results": [result],
        "job_receipt": {
            "kind": reconciliation.JOB_RECEIPT_KIND,
            "schema_version": reconciliation.JOB_RECEIPT_SCHEMA_VERSION,
            "job_ref": "job/release",
            "run_ref": RUN_REF,
            "attempt_ref": ATTEMPT_REF,
            "runner_ref": RUNNER_REF,
            "report_ref": copy.deepcopy(report_ref),
            "issuer_ref": copy.deepcopy(JOB_ISSUER_REF),
            "verification_status": "upstream_verified",
        },
    }
    routing = {
        "expected_check_ref": entry["expected_check_ref"],
        "job_refs": ["job/release"],
        "job_instances": [
            {
                "job_ref": "job/release",
                "run_ref": RUN_REF,
                "attempt_ref": ATTEMPT_REF,
                "runner_ref": RUNNER_REF,
                "attempt_eligibility": "eligible",
                "qualification_ref": dict(ATTEMPT_QUALIFICATION_REF),
                "report_ref": copy.deepcopy(report_ref),
            }
        ],
        "mapping_status": "routed",
        "scheduler_ref": {"name": "lefthook", "version": "2.2.2"},
        "effective_config_ref": {"name": "hook-job", "version": "1"},
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
        projection,
        routing=[routing],
        resolutions=[resolution],
        observations=[observation],
    )
    return receipt, result["result_id"]


def assess(universe, projection, **kwargs):
    kwargs.setdefault("evaluated_at", EVALUATED_AT)
    return required_assessment.assess_required_obligations(
        universe=universe,
        expected=projection,
        **kwargs,
    )


def check_row(assessment, entry):
    return next(
        row
        for row in assessment["check_instances"]
        if row["expected_check_ref"] == entry["expected_check_ref"]
    )


def obligation_row(assessment, obligation):
    instance = required_assessment.obligation_instance_ref(obligation)
    return next(
        row for row in assessment["obligations"] if row["instance_ref"] == instance
    )


# ---------------------------------------------------------------------------
# Positive paths and legal closures
# ---------------------------------------------------------------------------


def test_complete_current_run_assessment_passes_with_all_four_facts():
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    receipt, result_id = make_receipt(projection, entry)
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, projection, [obligation])
    admission = make_admission(
        projection,
        coverage_entries=[(entry, coverage(projection, entry, result_id))],
    )

    assessment = assess(universe, projection, admission=admission, receipt=receipt)

    assert assessment["status"] == "passed"
    assert assessment["blocked_reasons"] == []
    assert assessment["verification"] == {
        "authority": {"status": "verified", "reasons": []},
        "check_domain": {"status": "verified", "reasons": []},
        "obligations_universe": {"status": "verified", "reasons": []},
        "evidence_satisfaction": {"status": "satisfied", "reasons": []},
    }
    row = check_row(assessment, entry)
    assert row["status"] == "satisfied"
    assert row["admission"]["status"] == "admitted"
    assert row["structural"]["binding_status"] == "matched"
    assert row["structural"]["selected_result_ids"] == [result_id]
    assert assessment["inputs"]["receipt_ref"] == receipt["receipt_ref"]
    assert assessment["inputs"]["contract_sample"] is True
    assert (
        "contract_sample_inputs_not_production_admission" in assessment["limitations"]
    )
    assert "assessment_does_not_publish_github_check" in assessment["limitations"]
    assert required_assessment.validate_assessment(assessment) is assessment


def test_live_inputs_are_not_labeled_as_contract_samples():
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, projection, [obligation], contract_sample=False)
    admission = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
        contract_sample=False,
    )

    assessment = assess(universe, projection, admission=admission)

    assert assessment["status"] == "passed"
    assert assessment["inputs"]["contract_sample"] is False
    assert (
        "contract_sample_inputs_not_production_admission"
        not in assessment["limitations"]
    )


def test_historical_reuse_passes_without_a_current_receipt():
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, projection, [obligation])
    admission = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
    )

    assessment = assess(universe, projection, admission=admission)

    assert assessment["status"] == "passed"
    row = check_row(assessment, entry)
    assert row["status"] == "satisfied"
    assert row["admission"]["source_kind"] == "historical_reuse"
    assert row["structural"]["status"] == "not_supplied"


def test_legal_empty_projection_and_universe_passes():
    profile = make_profile(obligations=[], declared_checks=[])
    projection = make_projection(profile, [])
    universe = make_universe(profile, projection, [])
    admission = make_admission(projection)

    assessment = assess(universe, projection, admission=admission)

    assert assessment["status"] == "passed"
    assert assessment["summary"]["obligations"]["total"] == 0
    assert assessment["summary"]["check_instances"]["total"] == 0
    assert assessment["verification"]["check_domain"]["status"] == "verified"


def test_authorized_non_check_evidence_satisfies():
    profile = make_profile(
        obligations=[{"obligation_ref": "o/sbom", "kind": "non_check"}],
        declared_checks=[],
    )
    projection = make_projection(profile, [], non_check=[non_check_expected()])
    obligation = non_check_obligation("o/sbom")
    universe = make_universe(profile, projection, [obligation])
    admission = make_admission(projection)
    evidence = make_evidence([evidence_entry("o/sbom")])

    assessment = assess(
        universe,
        projection,
        admission=admission,
        non_check_evidence=evidence,
    )

    assert assessment["status"] == "passed"
    row = obligation_row(assessment, obligation)
    assert row["status"] == "satisfied"
    assert row["evidence_refs"] == [EVIDENCE_REF]


def test_authorized_not_applicable_obligation_is_not_required_evidence():
    profile = make_profile(
        obligations=[{"obligation_ref": "o/sbom", "kind": "non_check"}],
        declared_checks=[],
    )
    projection = make_projection(profile, [], non_check=[non_check_expected()])
    obligation = non_check_obligation(
        "o/sbom",
        applicability="not_applicable",
        basis_ref=BASIS_REF,
        applicability_authorization=inapplicability_authorization(),
    )
    universe = make_universe(profile, projection, [obligation])
    admission = make_admission(projection)

    assessment = assess(universe, projection, admission=admission)

    assert assessment["status"] == "passed"
    row = obligation_row(assessment, obligation)
    assert row["status"] == "not_applicable"


def test_authorized_check_inapplicability_passes_without_native_evidence():
    profile = make_profile(
        obligations=[
            {
                "obligation_ref": "o/tach",
                "check_names": ["tach"],
                "allow_inapplicable": True,
            }
        ]
    )
    entry = make_entry(applicability="not_applicable")
    projection = make_projection(profile, [entry])
    obligation = check_obligation(
        "o/tach",
        [entry["expected_check_ref"]],
        applicability="not_applicable",
        basis_ref=BASIS_REF,
    )
    universe = make_universe(profile, projection, [obligation])
    admission = make_admission(projection)

    assessment = assess(universe, projection, admission=admission)

    assert assessment["status"] == "passed"
    assert check_row(assessment, entry)["status"] == "not_applicable"
    assert obligation_row(assessment, obligation)["status"] == "not_applicable"
    assert "required_check_omitted_by_universe" not in assessment["cross_check_reasons"]


def test_check_inapplicability_requires_profile_waiver():
    profile = make_profile()
    entry = make_entry(applicability="not_applicable")
    projection = make_projection(profile, [entry])
    obligation = check_obligation(
        "o/tach",
        [entry["expected_check_ref"]],
        applicability="not_applicable",
        basis_ref=BASIS_REF,
    )
    universe = make_universe(profile, projection, [obligation])

    assessment = assess(universe, projection, admission=make_admission(projection))

    assert assessment["status"] == "blocked"
    row = check_row(assessment, entry)
    assert row["status"] == "unverified"
    assert "applicability_unauthorized" in row["reasons"]
    assert obligation_row(assessment, obligation)["status"] == "unverified"


@pytest.mark.parametrize(
    "obligations",
    [
        [
            {
                "obligation_ref": "o/other",
                "check_names": ["tach"],
                "allow_inapplicable": True,
            }
        ],
        [
            {
                "obligation_ref": "o/tach",
                "check_names": ["tach"],
                "allow_inapplicable": True,
                "target_refs": ["refs/heads/other"],
            }
        ],
    ],
)
def test_check_inapplicability_waiver_must_match_obligation_scope(obligations):
    profile = make_profile(obligations=obligations)
    entry = make_entry(applicability="not_applicable")
    projection = make_projection(profile, [entry])
    obligation = check_obligation(
        "o/tach",
        [entry["expected_check_ref"]],
        applicability="not_applicable",
        basis_ref=BASIS_REF,
    )
    universe = make_universe(profile, projection, [obligation])

    assessment = assess(universe, projection, admission=make_admission(projection))

    assert assessment["status"] == "blocked"
    assert obligation_row(assessment, obligation)["status"] == "unverified"
    assert "applicability_unauthorized" in check_row(assessment, entry)["reasons"]


@pytest.mark.parametrize(
    ("authorization_change", "reason"),
    [
        (None, "applicability_authorization_missing"),
        (
            {
                "authority_ref": {
                    "name": "other/rule",
                    "version": "1",
                    "sha256": "9" * 64,
                }
            },
            "applicability_authorization_mismatch",
        ),
        (
            {
                "rule_ref": {
                    "name": "other/rule",
                    "version": "1",
                    "sha256": "9" * 64,
                }
            },
            "applicability_rule_mismatch",
        ),
        ({"target_ref": "refs/heads/other"}, "applicability_scope_mismatch"),
        ({"phase": "post_publish"}, "applicability_scope_mismatch"),
        (
            {"revoked_at": "2026-11-16T00:50:00Z"},
            "applicability_authorization_revoked",
        ),
        (
            {"valid_until": "2026-11-16T00:50:00Z"},
            "applicability_authorization_expired",
        ),
        (
            {"valid_from": "2026-11-16T02:00:00Z"},
            "applicability_authorization_not_yet_valid",
        ),
        (
            {"obligation_ref": None},
            "applicability_authorization_instance_unbound",
        ),
        (
            {"obligation_ref": "o/other"},
            "applicability_authorization_obligation_mismatch",
        ),
    ],
)
def test_non_check_inapplicability_requires_a_valid_scoped_authorization(
    authorization_change, reason
):
    profile = make_profile(
        obligations=[{"obligation_ref": "o/sbom", "kind": "non_check"}],
        declared_checks=[],
    )
    projection = make_projection(profile, [], non_check=[non_check_expected()])
    authorization = (
        None
        if authorization_change is None
        else inapplicability_authorization(**authorization_change)
    )
    obligation = non_check_obligation(
        "o/sbom",
        applicability="not_applicable",
        basis_ref=BASIS_REF,
        applicability_authorization=authorization,
    )
    universe = make_universe(profile, projection, [obligation])

    assessment = assess(universe, projection, admission=make_admission(projection))

    assert assessment["status"] == "blocked"
    row = obligation_row(assessment, obligation)
    assert row["status"] == "unverified"
    assert row["reasons"] == [reason]


def test_non_check_authorization_cannot_be_reused_across_obligation_refs():
    profile = make_profile(
        obligations=[
            {"obligation_ref": "o/sbom", "kind": "non_check"},
            {"obligation_ref": "o/license", "kind": "non_check"},
        ],
        declared_checks=[],
    )
    projection = make_projection(
        profile,
        [],
        non_check=[non_check_expected("o/sbom"), non_check_expected("o/license")],
    )
    sbom = non_check_obligation(
        "o/sbom",
        applicability="not_applicable",
        basis_ref=BASIS_REF,
        applicability_authorization=inapplicability_authorization(
            obligation_ref="o/sbom"
        ),
    )
    # Same Rule/Authority/Target/Action/Phase, but authorization names o/sbom.
    license_obligation = non_check_obligation(
        "o/license",
        applicability="not_applicable",
        basis_ref=BASIS_REF,
        applicability_authorization=inapplicability_authorization(
            obligation_ref="o/sbom"
        ),
    )
    universe = make_universe(profile, projection, [sbom, license_obligation])

    assessment = assess(universe, projection, admission=make_admission(projection))

    assert assessment["status"] == "blocked"
    assert obligation_row(assessment, sbom)["status"] == "not_applicable"
    license_row = obligation_row(assessment, license_obligation)
    assert license_row["status"] == "unverified"
    assert license_row["reasons"] == ["applicability_authorization_obligation_mismatch"]


def test_non_check_authorization_binds_candidate_and_instance_identity():
    candidate_a = "sha256:" + "a" * 64
    candidate_b = "sha256:" + "b" * 64
    profile = make_profile(
        obligations=[{"obligation_ref": "o/sbom", "kind": "non_check"}],
        declared_checks=[],
    )
    projection = make_projection(profile, [], non_check=[non_check_expected()])

    unbound = non_check_obligation(
        "o/sbom",
        applicability="not_applicable",
        basis_ref=BASIS_REF,
        candidate_digest=candidate_a,
        applicability_authorization=inapplicability_authorization(
            obligation_ref="o/sbom"
        ),
    )
    mismatched = non_check_obligation(
        "o/sbom",
        applicability="not_applicable",
        basis_ref=BASIS_REF,
        candidate_digest=candidate_a,
        applicability_authorization=inapplicability_authorization(
            obligation_ref="o/sbom",
            candidate_digest=candidate_b,
        ),
    )
    matched = non_check_obligation(
        "o/sbom",
        applicability="not_applicable",
        basis_ref=BASIS_REF,
        candidate_digest=candidate_a,
        applicability_authorization=inapplicability_authorization(
            obligation_ref="o/sbom",
            candidate_digest=candidate_a,
        ),
    )
    instance_ref = required_assessment.obligation_instance_ref(
        {
            "obligation_ref": "o/sbom",
            "kind": "non_check",
            "target_ref": "refs/heads/main",
            "action": "publish",
            "phase": None,
            "candidate_digest": candidate_a,
            "source_digest": None,
        }
    )
    by_instance = non_check_obligation(
        "o/sbom",
        applicability="not_applicable",
        basis_ref=BASIS_REF,
        candidate_digest=candidate_a,
        applicability_authorization=inapplicability_authorization(
            obligation_ref=None,
            obligation_instance_ref=instance_ref,
        ),
    )
    wrong_instance = non_check_obligation(
        "o/sbom",
        applicability="not_applicable",
        basis_ref=BASIS_REF,
        candidate_digest=candidate_a,
        applicability_authorization=inapplicability_authorization(
            obligation_ref=None,
            obligation_instance_ref="sha256:" + "0" * 64,
        ),
    )

    candidate = {"commit": "2" * 40, "artifact_digest": candidate_a}
    for obligation, expected_status, reason in (
        (unbound, "unverified", "applicability_authorization_candidate_unbound"),
        (mismatched, "unverified", "applicability_authorization_candidate_mismatch"),
        (matched, "not_applicable", None),
        (by_instance, "not_applicable", None),
        (wrong_instance, "unverified", "applicability_authorization_instance_mismatch"),
    ):
        universe = make_universe(profile, projection, [obligation], candidate=candidate)
        assessment = assess(universe, projection, admission=make_admission(projection))
        row = obligation_row(assessment, obligation)
        assert row["status"] == expected_status
        if reason is None:
            assert assessment["status"] == "passed"
        else:
            assert assessment["status"] == "blocked"
            assert row["reasons"] == [reason]


def test_unauthorized_inapplicability_keeps_proven_check_witness():
    profile = make_profile(
        obligations=[
            {"obligation_ref": "o/tach", "check_names": ["tach"]},
            {"obligation_ref": "o/vulture", "check_names": ["vulture"]},
        ],
        declared_checks=["tach", "vulture"],
    )
    kept = make_entry("tach", required_by=("o/tach",))
    unauthorized = make_entry(
        "vulture",
        required_by=("o/vulture",),
        rule_ref=check_contract.rule_ref("vulture", 1, digest="d" * 64),
        applicability="not_applicable",
    )
    projection = make_projection(profile, [kept, unauthorized])
    kept_obligation = check_obligation("o/tach", [kept["expected_check_ref"]])
    unauthorized_obligation = check_obligation(
        "o/vulture",
        [unauthorized["expected_check_ref"]],
        applicability="not_applicable",
        basis_ref=BASIS_REF,
    )
    universe = make_universe(
        profile, projection, [kept_obligation, unauthorized_obligation]
    )
    admission = make_admission(
        projection,
        coverage_entries=[(kept, historical_coverage(projection, kept))],
    )

    assessment = assess(universe, projection, admission=admission)

    assert assessment["status"] == "blocked"
    assert check_row(assessment, kept)["status"] == "satisfied"
    assert check_row(assessment, unauthorized)["status"] == "unverified"
    assert (
        "applicability_unauthorized" in check_row(assessment, unauthorized)["reasons"]
    )


def test_inapplicable_check_cannot_satisfy_a_required_obligation():
    profile = make_profile()
    entry = make_entry(applicability="not_applicable")
    projection = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, projection, [obligation])
    admission = make_admission(projection)

    assessment = assess(universe, projection, admission=admission)

    assert assessment["status"] == "blocked"
    row = check_row(assessment, entry)
    assert row["status"] == "unverified"
    assert "check_inapplicable_for_required_obligation" in row["reasons"]
    assert obligation_row(assessment, obligation)["status"] == "unverified"
    assert required_assessment.validate_assessment(assessment) is assessment


# ---------------------------------------------------------------------------
# Independent authority, Check-domain and universe closure facts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"authority_status": "unverified"}, "profile_authority_unverified"),
        (
            {"valid_until": "2026-11-16T00:30:00Z"},
            "profile_expired",
        ),
        (
            {"revoked_at": "2026-11-16T00:20:00Z"},
            "profile_revoked",
        ),
    ],
)
def test_authority_fact_blocks_without_erasing_proven_check_coverage(override, reason):
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, projection, [obligation], **override)
    admission = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
    )

    assessment = assess(universe, projection, admission=admission)

    assert assessment["status"] == "blocked"
    assert assessment["verification"]["authority"]["status"] == "unverified"
    assert reason in assessment["verification"]["authority"]["reasons"]
    # The independently proven per-Check coverage is preserved.
    assert check_row(assessment, entry)["status"] == "satisfied"
    assert obligation_row(assessment, obligation)["status"] == "satisfied"


@pytest.mark.parametrize(
    ("closure_status", "delegation_domains", "reason"),
    [
        ("incomplete", (), "governance_domain_not_closed"),
        ("unverified", (), "governance_domain_not_closed"),
        (
            "closed",
            (
                {
                    "domain_ref": {
                        "name": "org/security",
                        "version": "1",
                        "sha256": "1" * 64,
                    },
                    "authority_ref": {
                        "name": "org/security-auth",
                        "version": "1",
                        "sha256": "2" * 64,
                    },
                    "included": False,
                    "closure_status": "closed",
                },
            ),
            "governance_domain_delegation_incomplete",
        ),
        (
            "closed",
            (
                {
                    "domain_ref": {
                        "name": "org/security",
                        "version": "1",
                        "sha256": "1" * 64,
                    },
                    "authority_ref": {
                        "name": "org/security-auth",
                        "version": "1",
                        "sha256": "2" * 64,
                    },
                    "included": True,
                    "closure_status": "incomplete",
                },
            ),
            "governance_domain_delegation_incomplete",
        ),
    ],
)
def test_governance_domain_closure_must_be_proven(
    closure_status, delegation_domains, reason
):
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(
        profile,
        projection,
        [obligation],
        closure_status=closure_status,
        delegation_domains=delegation_domains,
    )
    admission = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
    )

    assessment = assess(universe, projection, admission=admission)

    assert assessment["status"] == "blocked"
    assert assessment["verification"]["obligations_universe"]["status"] == "unverified"
    assert reason in assessment["verification"]["obligations_universe"]["reasons"]
    assert check_row(assessment, entry)["status"] == "satisfied"


def test_closed_multi_domain_universe_passes():
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    domain = {
        "domain_ref": {"name": "org/security", "version": "1", "sha256": "1" * 64},
        "authority_ref": {
            "name": "org/security-auth",
            "version": "1",
            "sha256": "2" * 64,
        },
        "included": True,
        "closure_status": "closed",
    }
    universe = make_universe(
        profile, projection, [obligation], delegation_domains=(domain,)
    )
    admission = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
    )

    assessment = assess(universe, projection, admission=admission)

    assert assessment["status"] == "passed"


def test_universe_status_and_profile_binding_are_facts():
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, projection, [obligation])
    universe["universe_status"] = "incomplete"
    universe["universe_ref"] = required_assessment._digest(
        {key: value for key, value in universe.items() if key != "universe_ref"}
    )
    admission = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
    )

    assessment = assess(universe, projection, admission=admission)

    assert assessment["status"] == "blocked"
    assert (
        "universe_incomplete"
        in assessment["verification"]["obligations_universe"]["reasons"]
    )

    old_profile = make_universe(profile, projection, [obligation])
    old_profile["profile"]["profile_revision"] = "rev-old"
    old_profile["universe_ref"] = required_assessment._digest(
        {key: value for key, value in old_profile.items() if key != "universe_ref"}
    )
    assessment = assess(old_profile, projection, admission=admission)

    assert assessment["status"] == "blocked"
    assert (
        "profile_binding_mismatch"
        in assessment["verification"]["obligations_universe"]["reasons"]
    )


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"context": "pull_request"}, "evaluation_context_mismatch"),
        ({"action": "verify"}, "evaluation_action_mismatch"),
        ({"target": "release"}, "evaluation_target_mismatch"),
    ],
)
def test_universe_evaluation_context_action_target_bind_to_profile(change, reason):
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation(
        "o/tach",
        [entry["expected_check_ref"]],
        action=change.get("action", "publish"),
    )
    universe = make_universe(profile, projection, [obligation], **change)
    admission = make_admission(
        projection,
        coverage_entries=[
            (
                entry,
                historical_coverage(
                    projection,
                    entry,
                    action=change.get("action", "publish"),
                ),
            )
        ],
    )

    assessment = assess(universe, projection, admission=admission)

    assert assessment["status"] == "blocked"
    assert reason in assessment["verification"]["obligations_universe"]["reasons"]
    assert check_row(assessment, entry)["status"] == "satisfied"


def test_universe_evaluation_ref_must_match_expected_event():
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation(
        "o/tach",
        [entry["expected_check_ref"]],
        target_ref="refs/heads/other",
    )
    universe = make_universe(
        profile,
        projection,
        [obligation],
        refs=[{"target_ref": "refs/heads/other", "ref_action": "update"}],
    )
    admission = make_admission(
        projection,
        coverage_entries=[
            (
                entry,
                historical_coverage(projection, entry, target_ref="refs/heads/other"),
            )
        ],
    )

    assessment = assess(universe, projection, admission=admission)

    assert assessment["status"] == "blocked"
    assert (
        "evaluation_ref_not_in_expected_event"
        in assessment["verification"]["obligations_universe"]["reasons"]
    )
    assert check_row(assessment, entry)["status"] == "satisfied"


def test_phase_bearing_scope_with_unverified_phase_blocks():
    profile = make_profile(phase="pre_publish")
    entry = make_entry()
    projection = make_projection(profile, [entry])
    assert projection["phase"] == "pre_publish"
    assert projection["phase_status"] == "unverified"
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, projection, [obligation], phase="pre_publish")
    admission = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
    )

    assessment = assess(universe, projection, admission=admission)

    assert assessment["status"] == "blocked"
    assert assessment["verification"]["check_domain"]["status"] == "unverified"
    assert (
        "expected_phase_unverified"
        in assessment["verification"]["check_domain"]["reasons"]
    )
    # The local Check evidence is preserved; only the phase authorization blocks.
    assert check_row(assessment, entry)["status"] == "satisfied"


def test_self_reported_verified_phase_without_trusted_producer_blocks():
    # #112's current producer emits phase_status="unverified" because no
    # trusted lifecycle Phase Producer contract exists yet. R1 retains "verified"
    # as an evolutionary interface field only and refuses to authorize the
    # Check Domain from an unauthenticated phase_status="verified" string alone.
    profile = make_profile(phase="pre_publish")
    entry = make_entry()
    projection = make_projection(profile, [entry], phase_status="verified")
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    admission = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
    )

    matching = make_universe(profile, projection, [obligation], phase="pre_publish")
    matched = assess(matching, projection, admission=admission)
    assert matched["status"] == "blocked"
    assert matched["verification"]["check_domain"]["status"] == "unverified"
    assert (
        "expected_phase_proof_unverified"
        in matched["verification"]["check_domain"]["reasons"]
    )
    assert matched["verification"]["obligations_universe"]["status"] == "verified"
    assert check_row(matched, entry)["status"] == "satisfied"

    mismatched = make_universe(profile, projection, [obligation], phase="post_publish")
    mismatched_admission = make_admission(
        projection,
        coverage_entries=[
            (
                entry,
                historical_coverage(projection, entry, phase="post_publish"),
            )
        ],
    )
    assessment = assess(mismatched, projection, admission=mismatched_admission)

    assert assessment["status"] == "blocked"
    assert (
        "expected_phase_proof_unverified"
        in assessment["verification"]["check_domain"]["reasons"]
    )
    assert (
        "evaluation_phase_mismatch"
        in assessment["verification"]["obligations_universe"]["reasons"]
    )
    assert check_row(assessment, entry)["status"] == "satisfied"


def test_obligation_candidate_digest_requires_a_real_candidate_identity():
    profile = make_profile(
        obligations=[{"obligation_ref": "o/sbom", "kind": "non_check"}],
        declared_checks=[],
    )
    projection = make_projection(profile, [], non_check=[non_check_expected()])
    artifact_a = "sha256:" + "a" * 64
    artifact_b = "sha256:" + "b" * 64
    obligation = non_check_obligation("o/sbom", candidate_digest=artifact_a)
    admission = make_admission(projection)

    missing = make_universe(profile, projection, [obligation], include_candidate=False)
    assessment = assess(missing, projection, admission=admission)
    assert assessment["status"] == "blocked"
    assert (
        "obligation_candidate_identity_missing"
        in assessment["verification"]["obligations_universe"]["reasons"]
    )

    different = make_universe(
        profile,
        projection,
        [obligation],
        candidate={"commit": "2" * 40, "artifact_digest": artifact_b},
    )
    assessment = assess(different, projection, admission=admission)
    assert assessment["status"] == "blocked"
    assert (
        "obligation_candidate_out_of_scope"
        in assessment["verification"]["obligations_universe"]["reasons"]
    )

    matching = make_universe(
        profile,
        projection,
        [obligation],
        candidate={"commit": "2" * 40, "artifact_digest": artifact_a},
    )
    evidence = make_evidence([evidence_entry("o/sbom", candidate_digest=artifact_a)])
    assessment = assess(
        matching,
        projection,
        admission=admission,
        non_check_evidence=evidence,
    )
    assert assessment["status"] == "passed"


def test_obligation_source_digest_binds_expected_source_and_candidate():
    profile = make_profile(
        obligations=[{"obligation_ref": "o/sbom", "kind": "non_check"}],
        declared_checks=[],
    )
    source_a = "sha256:" + "1" * 64
    source_b = "sha256:" + "2" * 64

    def projection_for_source(raw_digest):
        source = check_contract.source_ref(
            "worktree",
            declared_target_tree="3" * 40,
            actual_execution_source="worktree",
            content_digest=raw_digest,
            verified_scope=["src/engineering_ci/x.py"],
            scope_kind="check_input_closure",
        )
        return make_projection(
            profile, [], non_check=[non_check_expected()], source=source
        )

    projection = projection_for_source("1" * 64)
    obligation = non_check_obligation("o/sbom", source_digest=source_a)
    admission = make_admission(projection)
    evidence = make_evidence([evidence_entry("o/sbom", source_digest=source_a)])

    matching = make_universe(
        profile,
        projection,
        [obligation],
        candidate={"commit": "2" * 40, "source_digest": source_a},
    )
    assessment = assess(
        matching,
        projection,
        admission=admission,
        non_check_evidence=evidence,
    )
    assert assessment["status"] == "passed"

    missing = make_universe(
        profile,
        projection,
        [obligation],
        candidate={"commit": "2" * 40},
    )
    assessment = assess(
        missing,
        projection,
        admission=admission,
        non_check_evidence=evidence,
    )
    assert assessment["status"] == "blocked"
    assert (
        "obligation_source_identity_missing"
        in assessment["verification"]["obligations_universe"]["reasons"]
    )
    assert obligation_row(assessment, obligation)["status"] == "satisfied"

    different = make_universe(
        profile,
        projection,
        [obligation],
        candidate={"commit": "2" * 40, "source_digest": source_b},
    )
    assessment = assess(
        different,
        projection,
        admission=admission,
        non_check_evidence=evidence,
    )
    assert assessment["status"] == "blocked"
    assert (
        "obligation_source_out_of_scope"
        in assessment["verification"]["obligations_universe"]["reasons"]
    )
    assert obligation_row(assessment, obligation)["status"] == "satisfied"

    mismatched_source = projection_for_source("2" * 64)
    mismatched_admission = make_admission(mismatched_source)
    assessment = assess(
        matching,
        mismatched_source,
        admission=mismatched_admission,
        non_check_evidence=evidence,
    )
    assert assessment["status"] == "blocked"
    assert (
        "obligation_source_out_of_scope"
        in assessment["verification"]["obligations_universe"]["reasons"]
    )


def test_incomplete_check_domain_blocks_even_with_satisfied_evidence():
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry], status="partial")
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, projection, [obligation])
    admission = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
    )

    assessment = assess(universe, projection, admission=admission)

    assert assessment["status"] == "blocked"
    assert assessment["verification"]["check_domain"]["status"] == "unverified"
    assert (
        "expected_check_domain_incomplete"
        in assessment["verification"]["check_domain"]["reasons"]
    )
    assert check_row(assessment, entry)["status"] == "satisfied"


def test_missing_authority_status_does_not_verify_check_domain():
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    del projection["authority_status"]
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, projection, [obligation])
    admission = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
    )

    assessment = assess(universe, projection, admission=admission)

    assert assessment["status"] == "blocked"
    assert assessment["verification"]["check_domain"]["status"] == "unverified"
    assert (
        "expected_authority_unverified"
        in assessment["verification"]["check_domain"]["reasons"]
    )


@pytest.mark.parametrize(
    "label", [None, "unknown", "per_ref_container_only", "incomplete_profile"]
)
def test_complete_check_domain_requires_matching_completeness_label(label):
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    projection["consumer_completeness"] = label
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, projection, [obligation])
    admission = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
    )

    assessment = assess(universe, projection, admission=admission)

    assert assessment["status"] == "blocked"
    assert assessment["verification"]["check_domain"]["status"] == "unverified"
    assert (
        "expected_consumer_completeness_unproven"
        in assessment["verification"]["check_domain"]["reasons"]
    )
    assert check_row(assessment, entry)["status"] == "satisfied"


def test_required_check_omitted_by_universe_is_unverified_not_silent():
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    universe = make_universe(profile, projection, [])
    admission = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
    )

    assessment = assess(universe, projection, admission=admission)

    assert assessment["status"] == "blocked"
    assert (
        "required_check_omitted_by_universe"
        in assessment["verification"]["obligations_universe"]["reasons"]
    )
    row = check_row(assessment, entry)
    assert row["status"] == "unverified"
    assert row["reasons"] == ["required_check_omitted_by_universe"]


# ---------------------------------------------------------------------------
# Trusted admission: no receipt/window promotion, conflicts and adjudications
# ---------------------------------------------------------------------------


def test_matched_receipt_does_not_upgrade_an_unverified_admission():
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    receipt, result_id = make_receipt(projection, entry)
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, projection, [obligation])
    admission = make_admission(
        projection,
        coverage_entries=[
            (
                entry,
                coverage(
                    projection,
                    entry,
                    result_id,
                    status="unverified",
                    reason="issuer_unverified",
                ),
            )
        ],
    )

    assessment = assess(universe, projection, admission=admission, receipt=receipt)

    assert assessment["status"] == "blocked"
    row = check_row(assessment, entry)
    assert row["status"] == "unverified"
    assert "admission_coverage_unverified" in row["reasons"]
    assert row["structural"]["binding_status"] == "matched"


def test_current_run_admission_requires_a_matching_receipt():
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, projection, [obligation])
    admission = make_admission(
        projection,
        coverage_entries=[(entry, coverage(projection, entry))],
    )

    assessment = assess(universe, projection, admission=admission)
    assert assessment["status"] == "blocked"
    assert "receipt_missing_for_current_run" in check_row(assessment, entry)["reasons"]

    failed_receipt, _ = make_receipt(projection, entry, status="failed")
    assessment = assess(
        universe, projection, admission=admission, receipt=failed_receipt
    )
    assert assessment["status"] == "blocked"
    reasons = check_row(assessment, entry)["reasons"]
    assert "receipt_entry_not_matched" in reasons
    assert "receipt_witness_missing" in reasons


def test_receipt_binding_status_must_agree_with_the_admission():
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    receipt, result_id = make_receipt(projection, entry)
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, projection, [obligation])
    record = coverage(projection, entry, result_id)
    record["receipt_binding_status"] = "missing"
    admission = make_admission(projection, coverage_entries=[(entry, record)])

    assessment = assess(universe, projection, admission=admission, receipt=receipt)

    assert assessment["status"] == "blocked"
    assert "receipt_binding_status_mismatch" in check_row(assessment, entry)["reasons"]


def test_admitted_coverage_cannot_remain_in_remaining_work():
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, projection, [obligation])
    admission = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
        remaining=[remaining_work(projection, entry)],
    )

    assessment = assess(universe, projection, admission=admission)

    assert assessment["status"] == "blocked"
    assert "admission_admitted_but_remaining" in check_row(assessment, entry)["reasons"]


def test_trusted_admission_missing_keeps_structural_fact_only():
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    receipt, _result_id = make_receipt(projection, entry)
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, projection, [obligation])

    assessment = assess(universe, projection, receipt=receipt)

    assert assessment["status"] == "blocked"
    row = check_row(assessment, entry)
    assert row["status"] == "unverified"
    assert "trusted_admission_missing" in row["reasons"]
    assert row["structural"]["binding_status"] == "matched"
    assert "trusted_admission_not_supplied" in assessment["limitations"]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"phase": "post_publish"}, "admission_phase_mismatch"),
        ({"target_ref": "refs/heads/other"}, "admission_target_out_of_scope"),
        (
            {"rule_ref": check_contract.rule_ref("tach", 2, digest="0" * 64)},
            "admission_rule_mismatch",
        ),
        (
            {"candidate_digest": "sha256:" + "0" * 64},
            "admission_candidate_mismatch",
        ),
    ],
)
def test_admission_fact_must_be_bound_to_the_current_instance(change, reason):
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, projection, [obligation])
    record = historical_coverage(projection, entry, **change)
    admission = make_admission(projection, coverage_entries=[(entry, record)])

    assessment = assess(universe, projection, admission=admission)

    assert assessment["status"] == "blocked"
    assert reason in check_row(assessment, entry)["reasons"]


def test_historical_pass_and_current_failure_need_an_effective_adjudication():
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, projection, [obligation])
    failures = {
        instance_key(projection, entry): [failure(entry)],
    }
    admission = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
        failures=failures,
    )

    assessment = assess(universe, projection, admission=admission)

    assert assessment["status"] == "blocked"
    row = check_row(assessment, entry)
    assert row["status"] == "unmet"
    assert "authenticated_native_failure" in row["reasons"]
    assert row["authenticated_failures"][0]["in_scope"] is True

    resolved = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
        failures=failures,
        adjudications=[adjudication(projection, entry)],
    )
    assessment = assess(universe, projection, admission=resolved)
    assert assessment["status"] == "passed"
    row = check_row(assessment, entry)
    assert row["status"] == "satisfied"
    assert (
        row["authenticated_failures"][0]["adjudication"]["disposition"]
        == "failure_superseded"
    )


@pytest.mark.parametrize(
    "disposition", ["failure_superseded", "conflict_resolved", "unresolved"]
)
def test_two_conflicting_failures_require_an_explicit_resolution(disposition):
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, projection, [obligation])
    first = failure(entry, result_id=FAILURE_RESULT_ID, failure_kind="conflict")
    second = failure(
        entry,
        result_id="sha256:" + "2" * 64,
        failure_kind="native_failure",
    )
    admission = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
        failures={instance_key(projection, entry): [first, second]},
        adjudications=[
            adjudication(
                projection,
                entry,
                result_ids=(FAILURE_RESULT_ID, "sha256:" + "2" * 64),
                disposition=disposition,
            )
        ],
    )

    assessment = assess(universe, projection, admission=admission)

    if disposition == "unresolved":
        assert assessment["status"] == "blocked"
        assert check_row(assessment, entry)["status"] == "unmet"
    else:
        assert assessment["status"] == "passed"
        assert check_row(assessment, entry)["status"] == "satisfied"


def test_expired_or_revoked_adjudication_does_not_resolve_a_failure():
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, projection, [obligation])
    for change in (
        {"valid_until": "2026-11-16T00:56:00Z"},
        {"revoked_at": "2026-11-16T00:57:00Z"},
    ):
        admission = make_admission(
            projection,
            coverage_entries=[(entry, historical_coverage(projection, entry))],
            failures={instance_key(projection, entry): [failure(entry)]},
            adjudications=[adjudication(projection, entry, **change)],
        )
        assessment = assess(universe, projection, admission=admission)
        assert assessment["status"] == "blocked"
        assert check_row(assessment, entry)["status"] == "unmet"


def test_policy_approved_retry_pass_resolves_the_prior_attempt_failure():
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    receipt, result_id = make_receipt(projection, entry)
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, projection, [obligation])
    admission = make_admission(
        projection,
        coverage_entries=[(entry, coverage(projection, entry, result_id))],
        failures={instance_key(projection, entry): [failure(entry)]},
        adjudications=[adjudication(projection, entry)],
    )

    assessment = assess(universe, projection, admission=admission, receipt=receipt)

    assert assessment["status"] == "passed"
    row = check_row(assessment, entry)
    assert row["status"] == "satisfied"
    assert row["admission"]["source_kind"] == "current_run"
    assert row["authenticated_failures"][0]["in_scope"] is True


def test_out_of_scope_old_failure_does_not_block_current_success():
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, projection, [obligation])
    old_failure = failure(entry, target_ref="refs/heads/release-old")
    admission = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
        failures={instance_key(projection, entry): [old_failure]},
    )

    assessment = assess(universe, projection, admission=admission)

    assert assessment["status"] == "passed"
    row = check_row(assessment, entry)
    assert row["status"] == "satisfied"
    assert row["authenticated_failures"][0]["in_scope"] is False


# ---------------------------------------------------------------------------
# Obligation instance identity: phases, refs and M:N Check mappings
# ---------------------------------------------------------------------------


def test_same_obligation_ref_is_a_distinct_instance_per_phase():
    profile = make_profile(
        obligations=[{"obligation_ref": "o/sbom", "kind": "non_check"}],
        declared_checks=[],
    )
    pre_projection = make_projection(
        profile, [], non_check=[non_check_expected("o/sbom")]
    )
    pre_obligation = non_check_obligation("o/sbom", phase="pre_publish")
    post_obligation = non_check_obligation("o/sbom", phase="post_publish")
    pre_universe = make_universe(
        profile,
        pre_projection,
        [pre_obligation, post_obligation],
        phase="pre_publish",
    )
    pre_assessment = assess(
        pre_universe,
        pre_projection,
        admission=make_admission(pre_projection),
        non_check_evidence=make_evidence(
            [evidence_entry("o/sbom", phase="pre_publish")]
        ),
    )

    assert pre_assessment["status"] == "passed"
    assert obligation_row(pre_assessment, pre_obligation)["status"] == "satisfied"
    assert obligation_row(pre_assessment, post_obligation)["status"] == ("not_yet_due")

    post_projection = make_projection(
        profile, [], non_check=[non_check_expected("o/sbom")]
    )
    post_universe = make_universe(
        profile,
        post_projection,
        [copy.deepcopy(pre_obligation), copy.deepcopy(post_obligation)],
        phase="post_publish",
    )
    post_assessment = assess(
        post_universe,
        post_projection,
        admission=make_admission(post_projection),
        non_check_evidence=make_evidence([]),
    )

    assert post_assessment["status"] == "blocked"
    post_row = obligation_row(post_assessment, post_obligation)
    assert post_row["status"] == "unverified"
    assert post_row["reasons"] == ["non_check_evidence_missing"]
    # A different phase is not automatically a future phase: the authorized
    # order proves pre_publish already passed, so it cannot be silently deferred.
    past_row = obligation_row(post_assessment, pre_obligation)
    assert past_row["status"] == "unverified"
    assert past_row["reasons"] == ["phase_already_passed"]
    assert required_assessment.obligation_instance_ref(
        pre_obligation
    ) != required_assessment.obligation_instance_ref(post_obligation)


def test_same_obligation_ref_is_a_distinct_instance_per_ref():
    profile = make_profile(
        obligations=[{"obligation_ref": "o/sbom", "kind": "non_check"}],
        declared_checks=[],
    )
    event_a = {**EVENT, "head_ref": "refs/heads/a", "head_commit": "a" * 40}
    event_b = {**EVENT, "head_ref": "refs/heads/b", "head_commit": "b" * 40}
    projection_a = make_projection(
        profile, [], non_check=[non_check_expected()], event=event_a
    )
    projection_b = make_projection(
        profile, [], non_check=[non_check_expected()], event=event_b
    )
    ref_a = non_check_obligation("o/sbom", target_ref="refs/heads/a")
    ref_b = non_check_obligation("o/sbom", target_ref="refs/heads/b")
    universe_a = make_universe(
        profile,
        projection_a,
        [ref_a],
        refs=[{"target_ref": "refs/heads/a", "ref_action": "update"}],
    )
    universe_b = make_universe(
        profile,
        projection_b,
        [ref_b],
        refs=[{"target_ref": "refs/heads/b", "ref_action": "update"}],
    )
    admission_a = make_admission(projection_a)
    evidence_a = make_evidence([evidence_entry("o/sbom", target_ref="refs/heads/a")])
    assessment_a = assess(
        universe_a,
        projection_a,
        admission=admission_a,
        non_check_evidence=evidence_a,
    )

    assert assessment_a["status"] == "passed"
    assert required_assessment.obligation_instance_ref(
        ref_a
    ) != required_assessment.obligation_instance_ref(ref_b)

    # The Ref A admission is not a valid admission for the Ref B scope, and the
    # Ref A evidence document cannot satisfy the Ref B instance.
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="admission_scope_mismatch",
    ):
        assess(universe_b, projection_b, admission=admission_a)
    assessment_b = assess(
        universe_b,
        projection_b,
        admission=make_admission(projection_b),
        non_check_evidence=evidence_a,
    )
    assert assessment_b["status"] == "blocked"
    assert obligation_row(assessment_b, ref_b)["status"] == "unverified"
    assert assessment_b["non_check_evidence_ignored"] == 1


def test_one_obligation_with_multiple_required_checks_needs_each_check():
    profile = make_profile(
        obligations=[
            {"obligation_ref": "o/pair", "check_names": ["tach"]},
        ],
        declared_checks=["tach", "vulture"],
    )
    first = make_entry("tach", required_by=("o/pair",))
    second = make_entry(
        "vulture",
        required_by=("o/pair",),
        rule_ref=check_contract.rule_ref("vulture", 1, digest="d" * 64),
    )
    projection = make_projection(profile, [first, second])
    obligation = check_obligation(
        "o/pair", [first["expected_check_ref"], second["expected_check_ref"]]
    )
    universe = make_universe(profile, projection, [obligation])
    admission = make_admission(
        projection,
        coverage_entries=[(first, historical_coverage(projection, first))],
    )

    assessment = assess(universe, projection, admission=admission)

    assert assessment["status"] == "blocked"
    assert check_row(assessment, first)["status"] == "satisfied"
    assert check_row(assessment, second)["status"] == "unverified"
    row = obligation_row(assessment, obligation)
    assert row["status"] == "unverified"
    assert row["check_instances"] == [
        first["expected_check_ref"],
        second["expected_check_ref"],
    ]


def test_not_yet_due_requires_an_authorized_basis_and_future_phase():
    profile = make_profile(
        obligations=[{"obligation_ref": "o/sbom", "kind": "non_check"}],
        declared_checks=[],
    )
    projection = make_projection(profile, [], non_check=[non_check_expected()])
    future = non_check_obligation(
        "o/sbom",
        applicability="not_yet_due",
        basis_ref=BASIS_REF,
        due_phase="post_publish",
    )
    universe = make_universe(profile, projection, [future])
    assessment = assess(universe, projection, admission=make_admission(projection))
    assert assessment["status"] == "passed"
    assert obligation_row(assessment, future)["status"] == "not_yet_due"

    for broken in (
        non_check_obligation(
            "o/sbom", applicability="not_yet_due", due_phase="post_publish"
        ),
        non_check_obligation(
            "o/sbom",
            applicability="not_yet_due",
            basis_ref=BASIS_REF,
            due_phase="pre_publish",
        ),
        non_check_obligation(
            "o/sbom", applicability="not_yet_due", basis_ref=BASIS_REF
        ),
        non_check_obligation(
            "o/sbom",
            applicability="not_yet_due",
            basis_ref=BASIS_REF,
            due_phase="post_publish",
            due_at="2026-11-16T00:30:00Z",
        ),
    ):
        broken_universe = make_universe(profile, projection, [broken])
        broken_assessment = assess(
            broken_universe, projection, admission=make_admission(projection)
        )
        assert broken_assessment["status"] == "blocked"
        assert obligation_row(broken_assessment, broken)["status"] == "unverified"


def test_past_phase_not_yet_due_cannot_hide_a_due_obligation():
    profile = make_profile(
        obligations=[{"obligation_ref": "o/sbom", "kind": "non_check"}],
        declared_checks=[],
    )
    projection = make_projection(profile, [], non_check=[non_check_expected()])
    obligation = non_check_obligation(
        "o/sbom",
        phase="pre_publish",
        applicability="not_yet_due",
        basis_ref=BASIS_REF,
    )
    universe = make_universe(profile, projection, [obligation], phase="post_publish")

    assessment = assess(universe, projection, admission=make_admission(projection))

    assert assessment["status"] == "blocked"
    row = obligation_row(assessment, obligation)
    assert row["status"] == "unverified"
    assert row["reasons"] == ["not_yet_due_phase_already_passed"]


def test_required_obligation_in_past_phase_is_unverified():
    profile = make_profile(
        obligations=[{"obligation_ref": "o/sbom", "kind": "non_check"}],
        declared_checks=[],
    )
    projection = make_projection(profile, [], non_check=[non_check_expected()])
    obligation = non_check_obligation("o/sbom", phase="pre_publish")
    universe = make_universe(profile, projection, [obligation], phase="post_publish")

    assessment = assess(universe, projection, admission=make_admission(projection))

    assert assessment["status"] == "blocked"
    row = obligation_row(assessment, obligation)
    assert row["status"] == "unverified"
    assert row["reasons"] == ["phase_already_passed"]


@pytest.mark.parametrize(
    "phase_order",
    [
        None,
        {"ref": copy.deepcopy(PHASE_ORDER_REF), "phases": ["pre_publish", "publish"]},
        {"ref": copy.deepcopy(PHASE_ORDER_REF), "phases": ["post_publish"]},
    ],
)
def test_unknown_phase_order_blocks_future_deferral(phase_order):
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation(
        "o/tach", [entry["expected_check_ref"]], phase="post_publish"
    )
    universe = make_universe(
        profile,
        projection,
        [obligation],
        phase="pre_publish",
        phase_order=phase_order,
        include_phase_order=phase_order is not None,
    )
    admission = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
    )

    assessment = assess(universe, projection, admission=admission)

    assert assessment["status"] == "blocked"
    row = obligation_row(assessment, obligation)
    assert row["status"] == "unverified"
    assert row["reasons"] == ["phase_position_unproven"]
    assert check_row(assessment, entry)["status"] == "unverified"
    assert "phase_position_unproven" in check_row(assessment, entry)["reasons"]
    assert required_assessment.validate_assessment(assessment) is assessment


def test_phase_order_incomplete_blocks_the_universe_fact():
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(
        profile,
        projection,
        [obligation],
        phase="pre_publish",
        phase_order={
            "ref": copy.deepcopy(PHASE_ORDER_REF),
            "phases": ["publish", "post_publish"],
        },
    )
    admission = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
    )

    assessment = assess(universe, projection, admission=admission)

    assert assessment["status"] == "blocked"
    assert (
        "phase_order_incomplete"
        in assessment["verification"]["obligations_universe"]["reasons"]
    )
    # The phase-agnostic Required check itself is still provable.
    assert check_row(assessment, entry)["status"] == "satisfied"


def test_future_only_check_obligation_requires_explicit_profile_phase_scope():
    # 1. Negative: Profile has NO phases[] restriction on o/tach (so it applies
    #    to current pre_publish per _obligation_matches_scope). A future-only
    #    post_publish universe instance must NEVER waive current Required.
    unscoped_profile = make_profile()
    entry = make_entry()
    unscoped_proj = make_projection(unscoped_profile, [entry])
    post_only = check_obligation(
        "o/tach", [entry["expected_check_ref"]], phase="post_publish"
    )
    unscoped_universe = make_universe(
        unscoped_profile, unscoped_proj, [post_only], phase="pre_publish"
    )
    blocked = assess(
        unscoped_universe, unscoped_proj, admission=make_admission(unscoped_proj)
    )
    assert blocked["status"] == "blocked"
    assert "required_check_omitted_by_universe" in blocked["cross_check_reasons"]
    assert required_assessment.validate_assessment(blocked) is blocked

    # 2. Dual instances (current + future for same obligation_ref) must NOT
    #    cancel each other out: missing current evidence blocks, admitted PASSES.
    pre_instance = check_obligation(
        "o/tach", [entry["expected_check_ref"]], phase="pre_publish"
    )
    dual_universe = make_universe(
        unscoped_profile,
        unscoped_proj,
        [pre_instance, copy.deepcopy(post_only)],
        phase="pre_publish",
    )
    dual_missing = assess(
        dual_universe, unscoped_proj, admission=make_admission(unscoped_proj)
    )
    assert dual_missing["status"] == "blocked"
    assert check_row(dual_missing, entry)["status"] == "unverified"
    assert obligation_row(dual_missing, pre_instance)["status"] == "unverified"
    assert obligation_row(dual_missing, post_only)["status"] == "not_yet_due"

    dual_admitted = make_admission(
        unscoped_proj,
        coverage_entries=[(entry, historical_coverage(unscoped_proj, entry))],
    )
    dual_ok = assess(dual_universe, unscoped_proj, admission=dual_admitted)
    assert dual_ok["status"] == "passed"
    assert check_row(dual_ok, entry)["status"] == "satisfied"
    assert obligation_row(dual_ok, pre_instance)["status"] == "satisfied"
    assert obligation_row(dual_ok, post_only)["status"] == "not_yet_due"
    assert required_assessment.validate_assessment(dual_ok) is dual_ok

    # 3. Positive future-only scoping: Profile explicitly restricts o/future to
    #    post_publish (#112 _obligation_matches_scope -> excluded from current
    #    Expected required_by_obligations). Obligations universe & evidence pass;
    #    Check Domain stays blocked solely on expected_phase_unverified until a
    #    trusted lifecycle Phase Producer exists.
    scoped_profile = make_profile(
        phase="pre_publish",
        obligations=[
            {"obligation_ref": "o/tach", "check_names": ["tach"]},
            {
                "obligation_ref": "o/future",
                "check_names": ["vulture"],
                "phases": ["post_publish"],
            },
        ],
        declared_checks=["tach", "vulture"],
    )
    current_entry = make_entry("tach", required_by=("o/tach",))
    future_entry = make_entry(
        "vulture",
        required_by=(),
        rule_ref=check_contract.rule_ref("vulture", 1, digest="d" * 64),
    )
    scoped_proj = make_projection(scoped_profile, [current_entry, future_entry])
    current_ob = check_obligation(
        "o/tach", [current_entry["expected_check_ref"]], phase="pre_publish"
    )
    future_ob = check_obligation(
        "o/future", [future_entry["expected_check_ref"]], phase="post_publish"
    )
    scoped_universe = make_universe(
        scoped_profile,
        scoped_proj,
        [current_ob, future_ob],
        phase="pre_publish",
    )
    scoped_admission = make_admission(
        scoped_proj,
        coverage_entries=[
            (current_entry, historical_coverage(scoped_proj, current_entry))
        ],
    )
    scoped_assessment = assess(scoped_universe, scoped_proj, admission=scoped_admission)
    assert scoped_assessment["cross_check_reasons"] == []
    assert (
        scoped_assessment["verification"]["obligations_universe"]["status"]
        == "verified"
    )
    assert (
        scoped_assessment["verification"]["evidence_satisfaction"]["status"]
        == "satisfied"
    )
    assert check_row(scoped_assessment, future_entry)["status"] == "not_yet_due"
    assert obligation_row(scoped_assessment, future_ob)["status"] == "not_yet_due"
    assert scoped_assessment["verification"]["check_domain"]["reasons"] == [
        "expected_phase_unverified"
    ]
    assert required_assessment.validate_assessment(scoped_assessment) is (
        scoped_assessment
    )


def test_future_only_non_check_obligation_requires_explicit_profile_phase_scope():
    unscoped_profile = make_profile(
        obligations=[{"obligation_ref": "o/sbom", "kind": "non_check"}],
        declared_checks=[],
    )
    unscoped_proj = make_projection(
        unscoped_profile, [], non_check=[non_check_expected()]
    )
    post_only = non_check_obligation("o/sbom", phase="post_publish")
    unscoped_universe = make_universe(
        unscoped_profile, unscoped_proj, [post_only], phase="pre_publish"
    )

    blocked = assess(
        unscoped_universe, unscoped_proj, admission=make_admission(unscoped_proj)
    )

    assert blocked["status"] == "blocked"
    assert "non_check_obligation_omitted_by_universe" in blocked["cross_check_reasons"]
    assert required_assessment.validate_assessment(blocked) is blocked

    # Explicit future-only scope in Profile passes universe membership and
    # evidence satisfaction while leaving Check Domain blocked solely on
    # expected_phase_unverified.
    scoped_profile = make_profile(
        phase="pre_publish",
        obligations=[
            {
                "obligation_ref": "o/sbom",
                "kind": "non_check",
                "phases": ["post_publish"],
            }
        ],
        declared_checks=[],
    )
    scoped_proj = make_projection(scoped_profile, [], non_check=[])
    scoped_universe = make_universe(
        scoped_profile, scoped_proj, [post_only], phase="pre_publish"
    )
    scoped_assessment = assess(
        scoped_universe, scoped_proj, admission=make_admission(scoped_proj)
    )
    assert scoped_assessment["cross_check_reasons"] == []
    assert (
        scoped_assessment["verification"]["obligations_universe"]["status"]
        == "verified"
    )
    assert (
        scoped_assessment["verification"]["evidence_satisfaction"]["status"]
        == "satisfied"
    )
    assert obligation_row(scoped_assessment, post_only)["status"] == "not_yet_due"
    assert scoped_assessment["verification"]["check_domain"]["reasons"] == [
        "expected_phase_unverified"
    ]
    assert required_assessment.validate_assessment(scoped_assessment) is (
        scoped_assessment
    )


def test_future_only_membership_does_not_waive_a_truly_omitted_check():
    profile = make_profile(
        obligations=[
            {"obligation_ref": "o/tach", "check_names": ["tach"]},
            {"obligation_ref": "o/vulture", "check_names": ["vulture"]},
        ],
        declared_checks=["tach", "vulture"],
    )
    future_entry = make_entry("tach", required_by=("o/tach",))
    omitted_entry = make_entry(
        "vulture",
        required_by=("o/vulture",),
        rule_ref=check_contract.rule_ref("vulture", 1, digest="d" * 64),
    )
    projection = make_projection(profile, [future_entry, omitted_entry])
    obligation = check_obligation(
        "o/tach", [future_entry["expected_check_ref"]], phase="post_publish"
    )
    universe = make_universe(profile, projection, [obligation], phase="pre_publish")

    assessment = assess(universe, projection, admission=make_admission(projection))

    assert assessment["status"] == "blocked"
    assert "required_check_omitted_by_universe" in assessment["cross_check_reasons"]
    assert check_row(assessment, future_entry)["status"] == "not_yet_due"
    omitted_row = check_row(assessment, omitted_entry)
    assert omitted_row["status"] == "unverified"
    assert omitted_row["reasons"] == ["required_check_omitted_by_universe"]
    assert required_assessment.validate_assessment(assessment) is assessment


def test_future_only_membership_requires_the_same_obligation_ref():
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation(
        "o/other", [entry["expected_check_ref"]], phase="post_publish"
    )
    universe = make_universe(profile, projection, [obligation], phase="pre_publish")

    assessment = assess(universe, projection, admission=make_admission(projection))

    assert assessment["status"] == "blocked"
    assert "required_check_association_unresolved" in assessment["cross_check_reasons"]
    assert (
        "required_check_association_unresolved"
        in assessment["verification"]["obligations_universe"]["reasons"]
    )
    assert check_row(assessment, entry)["status"] == "not_yet_due"
    assert required_assessment.validate_assessment(assessment) is assessment


def test_not_applicable_without_authorized_basis_is_unverified():
    profile = make_profile(
        obligations=[{"obligation_ref": "o/sbom", "kind": "non_check"}],
        declared_checks=[],
    )
    projection = make_projection(profile, [], non_check=[non_check_expected()])
    obligation = non_check_obligation("o/sbom", applicability="not_applicable")
    universe = make_universe(profile, projection, [obligation])

    assessment = assess(universe, projection, admission=make_admission(projection))

    assert assessment["status"] == "blocked"
    row = obligation_row(assessment, obligation)
    assert row["status"] == "unverified"
    assert row["reasons"] == ["applicability_basis_missing"]


# ---------------------------------------------------------------------------
# Non-check evidence identity, authorization and validity
# ---------------------------------------------------------------------------


def non_check_setup(**universe_changes):
    profile = make_profile(
        obligations=[{"obligation_ref": "o/sbom", "kind": "non_check"}],
        declared_checks=[],
    )
    projection = make_projection(profile, [], non_check=[non_check_expected()])
    obligation = non_check_obligation("o/sbom")
    universe = make_universe(profile, projection, [obligation], **universe_changes)
    admission = make_admission(projection)
    return profile, projection, obligation, universe, admission


def test_missing_non_check_evidence_blocks():
    _profile, projection, obligation, universe, admission = non_check_setup()
    assessment = assess(universe, projection, admission=admission)
    assert assessment["status"] == "blocked"
    assert obligation_row(assessment, obligation)["reasons"] == [
        "non_check_evidence_missing"
    ]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (
            {
                "verifier_ref": {
                    "name": "fixed-pass",
                    "version": "1",
                    "sha256": "0" * 64,
                }
            },
            "non_check_verifier_mismatch",
        ),
        (
            {
                "verifier_authorization_ref": {
                    "name": "pr-local",
                    "version": "1",
                    "sha256": "0" * 64,
                }
            },
            "non_check_verifier_mismatch",
        ),
        (
            {
                "verification_rule_ref": {
                    "name": "pr-rule",
                    "version": "9",
                    "sha256": "0" * 64,
                }
            },
            "non_check_verifier_mismatch",
        ),
        (
            {"subject_ref": {"commit": "9" * 40}},
            "non_check_subject_mismatch",
        ),
        (
            {"subject_ref": {"source_digest": "9" * 64}},
            "non_check_subject_incomplete",
        ),
        (
            {"valid_until": "2026-11-16T00:50:00Z"},
            "non_check_evidence_expired",
        ),
        (
            {"revoked_at": "2026-11-16T00:50:00Z"},
            "non_check_evidence_revoked",
        ),
        (
            {"evaluated_at": "2026-11-16T02:00:00Z"},
            "non_check_evidence_not_yet_valid",
        ),
    ],
)
def test_pr_modified_or_invalid_non_check_evidence_is_rejected(change, reason):
    _profile, projection, obligation, universe, admission = non_check_setup()
    entry = evidence_entry("o/sbom", **change)
    assessment = assess(
        universe,
        projection,
        admission=admission,
        non_check_evidence=make_evidence([entry]),
    )

    assert assessment["status"] == "blocked"
    row = obligation_row(assessment, obligation)
    assert row["status"] == "unverified"
    assert row["reasons"] == [reason]


def test_artifact_level_non_check_requires_the_actual_artifact_digest():
    profile = make_profile(
        obligations=[{"obligation_ref": "o/sbom", "kind": "non_check"}],
        declared_checks=[],
    )
    projection = make_projection(profile, [], non_check=[non_check_expected()])
    artifact_digest = "sha256:" + "a" * 64
    candidate = {"commit": "2" * 40, "artifact_digest": artifact_digest}
    obligation = non_check_obligation(
        "o/sbom", required_subject_fields=["artifact_digest"]
    )
    universe = make_universe(profile, projection, [obligation], candidate=candidate)
    admission = make_admission(projection)

    commit_only = evidence_entry("o/sbom", subject_ref={"commit": "2" * 40})
    assessment = assess(
        universe,
        projection,
        admission=admission,
        non_check_evidence=make_evidence([commit_only]),
    )
    assert assessment["status"] == "blocked"
    assert obligation_row(assessment, obligation)["reasons"] == [
        "non_check_subject_incomplete"
    ]

    wrong_artifact = evidence_entry(
        "o/sbom",
        subject_ref={
            "commit": "2" * 40,
            "artifact_digest": "sha256:" + "b" * 64,
        },
    )
    assessment = assess(
        universe,
        projection,
        admission=admission,
        non_check_evidence=make_evidence([wrong_artifact]),
    )
    assert assessment["status"] == "blocked"
    assert obligation_row(assessment, obligation)["reasons"] == [
        "non_check_subject_mismatch"
    ]

    right_artifact = evidence_entry(
        "o/sbom",
        subject_ref={"commit": "2" * 40, "artifact_digest": artifact_digest},
    )
    assessment = assess(
        universe,
        projection,
        admission=admission,
        non_check_evidence=make_evidence([right_artifact]),
    )
    assert assessment["status"] == "passed"
    assert obligation_row(assessment, obligation)["status"] == "satisfied"


def test_commit_level_non_check_obligation_accepts_a_commit_subject():
    profile = make_profile(
        obligations=[{"obligation_ref": "o/sbom", "kind": "non_check"}],
        declared_checks=[],
    )
    projection = make_projection(profile, [], non_check=[non_check_expected()])
    candidate = {
        "commit": "2" * 40,
        "artifact_digest": "sha256:" + "a" * 64,
    }
    obligation = non_check_obligation("o/sbom", required_subject_fields=["commit"])
    universe = make_universe(profile, projection, [obligation], candidate=candidate)
    admission = make_admission(projection)
    commit_only = evidence_entry("o/sbom", subject_ref={"commit": "2" * 40})

    assessment = assess(
        universe,
        projection,
        admission=admission,
        non_check_evidence=make_evidence([commit_only]),
    )

    assert assessment["status"] == "passed"
    assert obligation_row(assessment, obligation)["status"] == "satisfied"


def test_candidate_missing_a_required_subject_field_is_unverified():
    profile = make_profile(
        obligations=[{"obligation_ref": "o/sbom", "kind": "non_check"}],
        declared_checks=[],
    )
    projection = make_projection(profile, [], non_check=[non_check_expected()])
    obligation = non_check_obligation(
        "o/sbom", required_subject_fields=["artifact_digest"]
    )
    universe = make_universe(
        profile,
        projection,
        [obligation],
        candidate={"commit": "2" * 40},
    )

    assessment = assess(
        universe,
        projection,
        admission=make_admission(projection),
        non_check_evidence=make_evidence(
            [
                evidence_entry(
                    "o/sbom",
                    subject_ref={
                        "commit": "2" * 40,
                        "artifact_digest": "sha256:" + "a" * 64,
                    },
                )
            ]
        ),
    )

    assert assessment["status"] == "blocked"
    assert obligation_row(assessment, obligation)["reasons"] == [
        "non_check_subject_identity_missing"
    ]


def test_missing_subject_requirement_is_unverified():
    profile = make_profile(
        obligations=[{"obligation_ref": "o/sbom", "kind": "non_check"}],
        declared_checks=[],
    )
    projection = make_projection(profile, [], non_check=[non_check_expected()])
    obligation = non_check_obligation("o/sbom", required_subject_fields=[])
    universe = make_universe(profile, projection, [obligation])

    assessment = assess(
        universe,
        projection,
        admission=make_admission(projection),
        non_check_evidence=make_evidence([evidence_entry("o/sbom")]),
    )

    assert assessment["status"] == "blocked"
    assert obligation_row(assessment, obligation)["reasons"] == [
        "non_check_subject_requirement_missing"
    ]


def test_unmet_non_check_verdict_is_not_promoted():
    _profile, projection, obligation, universe, admission = non_check_setup()
    entry = evidence_entry("o/sbom", result="unmet", reason="scan_findings")
    assessment = assess(
        universe,
        projection,
        admission=admission,
        non_check_evidence=make_evidence([entry]),
    )
    assert assessment["status"] == "blocked"
    assert obligation_row(assessment, obligation)["status"] == "unmet"


def test_conflicting_non_check_evidence_is_unverified():
    _profile, projection, obligation, universe, admission = non_check_setup()
    entries = [
        evidence_entry("o/sbom", result="satisfied"),
        evidence_entry("o/sbom", result="unmet"),
    ]
    assessment = assess(
        universe,
        projection,
        admission=admission,
        non_check_evidence=make_evidence(entries),
    )
    assert assessment["status"] == "blocked"
    row = obligation_row(assessment, obligation)
    assert row["status"] == "unverified"
    assert row["reasons"] == ["non_check_evidence_conflict"]


def test_unbound_non_check_verifier_metadata_is_unverified():
    profile = make_profile(
        obligations=[{"obligation_ref": "o/sbom", "kind": "non_check"}],
        declared_checks=[],
    )
    projection = make_projection(profile, [], non_check=[non_check_expected()])
    obligation = non_check_obligation(
        "o/sbom",
        verifier_ref=None,
        verifier_authorization_ref=None,
        verification_rule_ref=None,
    )
    universe = make_universe(profile, projection, [obligation])
    assessment = assess(
        universe,
        projection,
        admission=make_admission(projection),
        non_check_evidence=make_evidence([evidence_entry("o/sbom")]),
    )
    assert assessment["status"] == "blocked"
    assert obligation_row(assessment, obligation)["reasons"] == [
        "non_check_verifier_unbound"
    ]


def test_non_check_obligation_omitted_by_universe_is_a_gap():
    profile = make_profile(
        obligations=[{"obligation_ref": "o/sbom", "kind": "non_check"}],
        declared_checks=[],
    )
    projection = make_projection(profile, [], non_check=[non_check_expected()])
    universe = make_universe(profile, projection, [])

    assessment = assess(universe, projection, admission=make_admission(projection))

    assert assessment["status"] == "blocked"
    assert (
        "non_check_obligation_omitted_by_universe"
        in assessment["verification"]["obligations_universe"]["reasons"]
    )


# ---------------------------------------------------------------------------
# Input integrity and multi-ref scope selection
# ---------------------------------------------------------------------------


def test_tampered_inputs_are_refused():
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, projection, [obligation])
    admission = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
    )

    tampered = copy.deepcopy(universe)
    tampered["obligations"][0]["applicability"] = "not_applicable"
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="universe_ref_mismatch",
    ):
        assess(tampered, projection, admission=admission)

    tampered = copy.deepcopy(universe)
    tampered["obligations"][0]["obligation_ref"] = "o/other"
    tampered["universe_ref"] = required_assessment._digest(
        {key: value for key, value in tampered.items() if key != "universe_ref"}
    )
    assessment = required_assessment.assess_required_obligations(
        universe=tampered,
        expected=projection,
        admission=admission,
        evaluated_at=EVALUATED_AT,
    )
    assert assessment["status"] == "blocked"
    assert "universe_obligation_mapping_mismatch" in assessment["cross_check_reasons"]
    assert (
        "universe_obligation_mapping_mismatch"
        in assessment["verification"]["obligations_universe"]["reasons"]
    )

    tampered = copy.deepcopy(admission)
    tampered["accepted_coverage_by_instance"][instance_key(projection, entry)][
        "raw_result_verified"
    ] = False
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="admission_ref_mismatch",
    ):
        assess(universe, projection, admission=tampered)

    tampered = copy.deepcopy(projection)
    tampered["expected_set_ref"] = "sha256:" + "0" * 64
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="expected_set_ref_mismatch",
    ):
        assess(universe, tampered, admission=admission)

    tampered = copy.deepcopy(projection)
    tampered["checks"][0]["applicability"] = {
        "status": "not_applicable",
        "reason": "tampered",
    }
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="expected_check_ref_mismatch",
    ):
        assess(universe, tampered, admission=admission)


def test_admission_for_another_scope_is_refused():
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, projection, [obligation])
    admission = make_admission(projection, contract_sample=False)

    other = copy.deepcopy(projection)
    other["consumer"] = "engineering-ci/other"
    other["scope_id"] = expected_checks.scope_identity(
        other["consumer"],
        PROFILE_REF,
        PROFILE_SOURCE,
        EVENT,
        SOURCE,
        SELECTION_RULE,
        None,
        None,
        profile_sha256=profile["profile_sha256"],
        evaluated_at=profile["evaluated_at"],
    )
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="admission_scope_mismatch",
    ):
        assess(universe, other, admission=admission)


def test_same_check_name_in_distinct_ref_scopes_never_merges():
    profile = make_profile()
    event_a = {**EVENT, "head_ref": "refs/heads/a", "head_commit": "a" * 40}
    event_b = {**EVENT, "head_ref": "refs/heads/b", "head_commit": "b" * 40}
    entry_a = make_entry(event=event_a)
    entry_b = make_entry(event=event_b)
    projection_a = make_projection(profile, [entry_a], event=event_a)
    projection_b = make_projection(profile, [entry_b], event=event_b)
    obligation_a = check_obligation("o/tach", [entry_a["expected_check_ref"]])
    obligation_b = check_obligation("o/tach", [entry_b["expected_check_ref"]])
    universe_a = make_universe(profile, projection_a, [obligation_a])
    universe_b = make_universe(profile, projection_b, [obligation_b])
    admission_a = make_admission(
        projection_a,
        coverage_entries=[(entry_a, historical_coverage(projection_a, entry_a))],
    )
    admission_b = make_admission(
        projection_b,
        coverage_entries=[(entry_b, historical_coverage(projection_b, entry_b))],
    )

    assert entry_a["name"] == entry_b["name"] == "tach"
    assert entry_a["expected_check_ref"] != entry_b["expected_check_ref"]
    assert projection_a["scope_id"] != projection_b["scope_id"]
    assert assess(universe_a, projection_a, admission=admission_a)["status"] == (
        "passed"
    )
    assert assess(universe_b, projection_b, admission=admission_b)["status"] == (
        "passed"
    )
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="admission_scope_mismatch",
    ):
        assess(universe_b, projection_b, admission=admission_a)


def test_source_digest_distinguishes_non_check_instances():
    profile = make_profile(
        obligations=[{"obligation_ref": "o/sbom", "kind": "non_check"}],
        declared_checks=[],
    )
    source = check_contract.source_ref(
        "worktree",
        declared_target_tree="3" * 40,
        actual_execution_source="worktree",
        content_digest="1" * 64,
        verified_scope=["src/engineering_ci/x.py"],
        scope_kind="check_input_closure",
    )
    projection = make_projection(
        profile, [], non_check=[non_check_expected()], source=source
    )
    source_a = "sha256:" + "1" * 64
    source_b = "sha256:" + "2" * 64
    candidate = {"commit": "2" * 40, "source_digest": source_a}
    obligation_a = non_check_obligation("o/sbom", source_digest=source_a)
    obligation_b = non_check_obligation("o/sbom", source_digest=source_b)
    universe_a = make_universe(profile, projection, [obligation_a], candidate=candidate)
    universe_b = make_universe(profile, projection, [obligation_b], candidate=candidate)
    admission = make_admission(projection)
    evidence_a = make_evidence([evidence_entry("o/sbom", source_digest=source_a)])

    assert required_assessment.obligation_instance_ref(
        obligation_a
    ) != required_assessment.obligation_instance_ref(obligation_b)
    assert (
        assess(
            universe_a,
            projection,
            admission=admission,
            non_check_evidence=evidence_a,
        )["status"]
        == "passed"
    )
    assessment_b = assess(
        universe_b,
        projection,
        admission=admission,
        non_check_evidence=evidence_a,
    )
    assert assessment_b["status"] == "blocked"
    assert obligation_row(assessment_b, obligation_b)["reasons"] == [
        "non_check_evidence_missing"
    ]
    assert assessment_b["non_check_evidence_ignored"] == 1


def test_multi_ref_container_requires_one_explicit_ref_scope():
    profile = make_profile()
    entry = make_entry()
    single = make_projection(profile, [entry])
    child = {
        "source_ref": "refs/heads/main",
        "local_ref": "refs/heads/main",
        "remote_ref": "refs/heads/main",
        "ref_action": "update",
        "force_push": False,
        "baseline_status": "ok",
        "event_ref": copy.deepcopy(EVENT),
        "source": copy.deepcopy(SOURCE),
        "scope_id": single["scope_id"],
        "expected_set_ref": single["expected_set_ref"],
        "checks": [entry],
        "non_check_obligations": [],
        "conflicts": [],
        "status": "complete",
        "consumer_completeness": "per_ref",
        "effective_profile": profile,
    }
    container = copy.deepcopy(single)
    container["checks"] = []
    container["expected_set_ref"] = expected_checks.set_identity(
        [], (), schema_version=2
    )
    container["consumer_completeness"] = "per_ref_container_only"
    container["per_ref"] = [child]
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, child, [obligation])
    child_admission = make_admission(
        child,
        coverage_entries=[(entry, historical_coverage(child, entry))],
    )

    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="expected_ref_scope_required",
    ):
        assess(universe, container, admission=None)

    child_assessment = assess(
        universe,
        container,
        ref_scope_id=child["scope_id"],
        admission=child_admission,
    )
    assert child_assessment["status"] == "passed"
    assert child_assessment["inputs"]["check_domain"]["scope_id"] == child["scope_id"]
    assert child_assessment["refs"] == [
        {"target_ref": "refs/heads/main", "ref_action": "update"}
    ]


def test_multi_ref_child_complete_cannot_upgrade_unverified_parent_phase():
    profile = make_profile(phase="pre_publish")
    entry = make_entry()
    container, child = make_per_ref_container(
        profile, [entry], parent_phase_status="unverified"
    )
    assert container["phase"] == "pre_publish"
    assert container["phase_status"] == "unverified"
    assert child["status"] == "complete"
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, child, [obligation], phase="pre_publish")
    admission = make_admission(
        child,
        coverage_entries=[(entry, historical_coverage(child, entry))],
    )

    assessment = assess(
        universe,
        container,
        ref_scope_id=child["scope_id"],
        admission=admission,
    )

    assert assessment["status"] == "blocked"
    assert assessment["verification"]["check_domain"]["status"] == "unverified"
    assert (
        "expected_phase_unverified"
        in assessment["verification"]["check_domain"]["reasons"]
    )
    # Local structural completeness proves the Check, not the lifecycle phase.
    assert check_row(assessment, entry)["status"] == "satisfied"
    assert required_assessment.validate_assessment(assessment) is assessment


def test_per_ref_ref_action_conflict_blocks_but_keeps_check_witness():
    profile = make_profile()
    entry = make_entry()
    container, child = make_per_ref_container(profile, [entry], ref_action="update")
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(
        profile,
        child,
        [obligation],
        refs=[{"target_ref": "refs/heads/main", "ref_action": "delete"}],
    )
    admission = make_admission(
        child,
        coverage_entries=[(entry, historical_coverage(child, entry))],
    )

    assessment = assess(
        universe,
        container,
        ref_scope_id=child["scope_id"],
        admission=admission,
    )

    assert assessment["status"] == "blocked"
    assert (
        "evaluation_ref_action_mismatch"
        in assessment["verification"]["obligations_universe"]["reasons"]
    )
    assert check_row(assessment, entry)["status"] == "satisfied"


@pytest.mark.parametrize("ref_action", ["update", "delete"])
def test_per_ref_ref_action_update_and_delete_are_legal(ref_action):
    profile = make_profile(obligations=[], declared_checks=[])
    container, child = make_per_ref_container(profile, [], ref_action=ref_action)
    universe = make_universe(
        profile,
        child,
        [],
        refs=[{"target_ref": "refs/heads/main", "ref_action": ref_action}],
    )

    assessment = assess(
        universe,
        container,
        ref_scope_id=child["scope_id"],
        admission=make_admission(child),
    )

    assert assessment["status"] == "passed"
    assert assessment["verification"]["obligations_universe"]["status"] == "verified"


def test_per_ref_prefers_remote_ref_over_local_ref_like_upstream():
    profile = make_profile()
    push_event = {
        **EVENT,
        "base_ref": "refs/heads/main",
        "head_ref": "refs/heads/main",
        "local_ref": "refs/heads/feature",
        "remote_ref": "refs/heads/main",
        "ref_action": "update",
        "references": [
            {
                "local_ref": "refs/heads/feature",
                "remote_ref": "refs/heads/main",
                "ref_action": "update",
            }
        ],
    }
    entry = make_entry(event=push_event)
    container, child = make_per_ref_container(
        profile,
        [entry],
        ref_action="update",
        local_ref="refs/heads/feature",
        remote_ref="refs/heads/main",
        event=push_event,
    )
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    admission = make_admission(
        child,
        coverage_entries=[(entry, historical_coverage(child, entry))],
    )

    ok_universe = make_universe(
        profile,
        child,
        [obligation],
        refs=[{"target_ref": "refs/heads/main", "ref_action": "update"}],
    )
    ok_assessment = assess(
        ok_universe, container, ref_scope_id=child["scope_id"], admission=admission
    )
    assert ok_assessment["status"] == "passed"
    assert ok_assessment["refs"] == [
        {"target_ref": "refs/heads/main", "ref_action": "update"}
    ]

    local_only_universe = make_universe(
        profile,
        child,
        [obligation],
        refs=[{"target_ref": "refs/heads/feature", "ref_action": "update"}],
    )
    wrong_target = assess(
        local_only_universe,
        container,
        ref_scope_id=child["scope_id"],
        admission=admission,
    )
    assert wrong_target["status"] == "blocked"
    assert (
        "evaluation_ref_scope_mismatch"
        in wrong_target["verification"]["obligations_universe"]["reasons"]
    )
    wrong_target_row = check_row(wrong_target, entry)
    assert wrong_target_row["status"] == "unverified"
    assert "admission_target_out_of_scope" in wrong_target_row["reasons"]

    wrong_action_universe = make_universe(
        profile,
        child,
        [obligation],
        refs=[{"target_ref": "refs/heads/main", "ref_action": "delete"}],
    )
    wrong_action = assess(
        wrong_action_universe,
        container,
        ref_scope_id=child["scope_id"],
        admission=admission,
    )
    assert wrong_action["status"] == "blocked"
    assert (
        "evaluation_ref_action_mismatch"
        in wrong_action["verification"]["obligations_universe"]["reasons"]
    )
    assert check_row(wrong_action, entry)["status"] == "satisfied"

    # Legal fallback when remote_ref is absent: uses local_ref like _multi_ref_scopes.
    fallback_event = {
        **EVENT,
        "local_ref": "refs/heads/main",
        "remote_ref": None,
        "ref_action": "update",
        "references": [{"local_ref": "refs/heads/main", "ref_action": "update"}],
    }
    fb_entry = make_entry(event=fallback_event)
    fb_container, fb_child = make_per_ref_container(
        profile,
        [fb_entry],
        ref_action="update",
        local_ref="refs/heads/main",
        event=fallback_event,
    )
    fb_child["remote_ref"] = None
    fb_ob = check_obligation("o/tach", [fb_entry["expected_check_ref"]])
    fb_universe = make_universe(
        profile,
        fb_child,
        [fb_ob],
        refs=[{"target_ref": "refs/heads/main", "ref_action": "update"}],
    )
    fb_admission = make_admission(
        fb_child,
        coverage_entries=[(fb_entry, historical_coverage(fb_child, fb_entry))],
    )
    fb_assessment = assess(
        fb_universe,
        fb_container,
        ref_scope_id=fb_child["scope_id"],
        admission=fb_admission,
    )
    assert fb_assessment["status"] == "passed"


def test_v1_projection_is_not_accepted():
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    projection["schema_version"] = 1
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="expected_schema_requires_v2",
    ):
        assess(make_universe(profile, make_projection(profile, [entry])), projection)


def test_unknown_universe_check_instance_is_blocked_and_self_consistent():
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    unknown_ref = "sha256:" + "0" * 64
    obligation = check_obligation("o/tach", [unknown_ref])
    universe = make_universe(profile, projection, [obligation])
    admission = make_admission(projection)

    assessment = assess(universe, projection, admission=admission)

    assert assessment["status"] == "blocked"
    assert (
        "universe_check_instance_unknown"
        in assessment["verification"]["obligations_universe"]["reasons"]
    )
    row = obligation_row(assessment, obligation)
    assert row["status"] == "unverified"
    assert row["reasons"] == ["obligation_check_instance_unresolved"]
    assert required_assessment.validate_assessment(assessment) is assessment


def test_validate_assessment_rejects_input_fact_contradictions():
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, projection, [obligation])
    admission = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
    )
    assessment = assess(universe, projection, admission=admission)
    assert assessment["status"] == "passed"

    def rehash(value):
        value["assessment_ref"] = required_assessment._digest(
            {key: item for key, item in value.items() if key != "assessment_ref"}
        )
        return value

    tampered = copy.deepcopy(assessment)
    tampered["inputs"]["check_domain"]["status"] = "partial"
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="assessment_verification_inconsistent",
    ):
        required_assessment.validate_assessment(rehash(tampered))

    tampered = copy.deepcopy(assessment)
    tampered["inputs"]["universe_status"] = "incomplete"
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="assessment_verification_inconsistent",
    ):
        required_assessment.validate_assessment(rehash(tampered))

    tampered = copy.deepcopy(assessment)
    tampered["inputs"]["authority"]["revoked_at"] = "2026-11-16T00:30:00Z"
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="assessment_verification_inconsistent",
    ):
        required_assessment.validate_assessment(rehash(tampered))

    tampered = copy.deepcopy(assessment)
    tampered["check_instances"][0]["scope_id"] = "sha256:" + "0" * 64
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="assessment_check_instance_scope_mismatch",
    ):
        required_assessment.validate_assessment(rehash(tampered))

    tampered = copy.deepcopy(assessment)
    tampered["obligations"][0]["target_ref"] = "refs/heads/other"
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="assessment_instance_ref_mismatch",
    ):
        required_assessment.validate_assessment(rehash(tampered))


def test_validate_assessment_rejects_rehashed_contradictions():
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, projection, [obligation])
    admission = make_admission(projection)
    assessment = assess(universe, projection, admission=admission)
    assert assessment["status"] == "blocked"

    tampered = copy.deepcopy(assessment)
    tampered["status"] = "passed"
    tampered["blocked_reasons"] = []
    tampered["assessment_ref"] = required_assessment._digest(
        {key: value for key, value in tampered.items() if key != "assessment_ref"}
    )
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="assessment_gate_inconsistent",
    ):
        required_assessment.validate_assessment(tampered)

    missing_ref = copy.deepcopy(assessment)
    missing_ref["assessment_ref"] = "sha256:" + "0" * 64
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="assessment_ref_mismatch",
    ):
        required_assessment.validate_assessment(missing_ref)


# ---------------------------------------------------------------------------
# D-F02: original #63 T1/T2 admission -> #61 R1 authorized projection
# ---------------------------------------------------------------------------


def _df02_scope():
    profile = make_profile()
    entry = make_entry()
    expected = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, expected, [obligation])
    scope = expected["scope_id"]
    ref = entry["expected_check_ref"]
    return universe, expected, entry, f"{scope}/{ref}"


def _df02_current_authorization():
    return {
        "phase": "pre_publish",
        "action": "publish",
        "target_ref": "refs/heads/main",
        "candidate_digest": None,
    }


def df02_source_authority(source, universe, authorization=None):
    expected_source = copy.deepcopy(source)
    universe_ref = universe["universe_ref"]
    current_authorization = authorization or _df02_current_authorization()

    def verify(request):
        if (
            request["source_admission"] != expected_source
            or request["current_evaluation"]["universe_ref"] != universe_ref
            or request["current_authorization"] != current_authorization
        ):
            return None
        return {
            "status": "verified",
            "source_admission_ref": request["source_admission_ref"],
            "current_authorization_ref": request["current_authorization_ref"],
            "issuer_ref": copy.deepcopy(JOB_ISSUER_REF),
            "verification_ref": copy.deepcopy(SOURCE_RECEIPT_REF),
        }

    return verify


def _df02_t1_source(expected, entry, key, *, admitted=True):
    receipt, result_id = make_receipt(expected, entry)
    fact = {
        "result_id": result_id,
        "status": "passed" if admitted else "failed",
        "selected": True,
        "integrity_status": "verified",
        "issuer_status": "verified",
        "implementation_status": "approved",
        "run_ref": RUN_REF,
        "attempt_ref": ATTEMPT_REF,
        "runner_ref": RUNNER_REF,
        "result_origin": "host",
    }
    status = "admitted" if admitted else "unsatisfied"
    item = {
        "scope_id": expected["scope_id"],
        "expected_check_ref": entry["expected_check_ref"],
        "status": status,
        "admitted": admitted,
        "origin_status": "verified",
        "integrity_status": "verified",
        "resolution_status": "verified",
        "obligation_status": "satisfied" if admitted else "unsatisfied",
        "result_ids": [result_id],
        "facts": [fact],
        "reasons": [] if admitted else ["native_failed"],
    }
    source = {
        "kind": "engineering-ci-evidence-admission",
        "schema_version": 1,
        "scope_id": expected["scope_id"],
        "expected_set_ref": expected["expected_set_ref"],
        "receipt_ref": receipt["receipt_ref"],
        "evidence_admission": "evaluated",
        "required_gate": "not_evaluated",
        "entries": [item],
        "accepted_coverage_by_instance": {
            key: {
                "admission_status": status,
                "result_ids": [result_id],
            },
        },
        "remaining_work": [],
    }
    return source, receipt


def test_t1_t2_original_admission_projection_current_pass_and_reject():
    universe, expected, entry, key = _df02_scope()
    original, receipt = _df02_t1_source(expected, entry, key)
    authorization = _df02_current_authorization()
    assessment = assess(
        universe,
        expected,
        admission=original,
        receipt=receipt,
        admission_current_authorization=authorization,
        admission_source_authority=df02_source_authority(original, universe),
        admission_contract_sample=True,
    )
    assert assessment["status"] == "passed"
    row = check_row(assessment, entry)
    assert row["status"] == "satisfied"
    assert row["admission"]["source_kind"] == "current_run"
    assert assessment["inputs"]["contract_sample"] is True
    assert assessment["inputs"]["admission_ref"].startswith("sha256:")
    verification = assessment["inputs"]["admission_source_verification"]
    assert verification["source_admission_ref"] == required_assessment._digest(original)
    assert verification["source_receipt_ref"] == receipt["receipt_ref"]
    assert verification["issuer_ref"] == JOB_ISSUER_REF
    assert verification["verification_ref"] == SOURCE_RECEIPT_REF
    assert (
        assessment["inputs"]["admission_observations_reconciled"]
        is receipt["all_observations_reconciled"]
    )
    required_assessment.validate_assessment(assessment)

    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="admission_source_authority_unverified",
    ):
        assess(
            universe,
            expected,
            admission=original,
            receipt=receipt,
            admission_current_authorization=authorization,
            admission_source_authority=lambda _facts: True,
        )
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="admission_source_authority_missing",
    ):
        assess(
            universe,
            expected,
            admission=original,
            receipt=receipt,
            admission_current_authorization=authorization,
        )

    out_of_scope = copy.deepcopy(original)
    out_of_scope["scope_id"] = "sha256:" + "0" * 64
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="admission_scope_mismatch",
    ):
        assess(
            universe,
            expected,
            admission=out_of_scope,
            receipt=receipt,
            admission_current_authorization=authorization,
            admission_source_authority=lambda _facts: True,
        )


def test_df02_t1_authenticated_native_failure_is_unmet_not_pass():
    universe, expected, entry, key = _df02_scope()
    original, receipt = _df02_t1_source(expected, entry, key, admitted=False)
    assessment = assess(
        universe,
        expected,
        admission=original,
        receipt=receipt,
        admission_current_authorization=_df02_current_authorization(),
        admission_source_authority=df02_source_authority(original, universe),
        admission_contract_sample=True,
    )
    assert assessment["status"] == "blocked"
    row = check_row(assessment, entry)
    assert row["status"] == "unmet"
    assert "authenticated_native_failure" in row["reasons"]
    assert row["admission"]["source_disposition"]["status"] == "unsatisfied"


def test_df02_t2_historical_result_binding_and_no_current_receipt():
    universe, expected, entry, key = _df02_scope()
    result_id = RESULT_ID
    additional_id = "sha256:" + "e" * 64
    authorization = {
        **_df02_current_authorization(),
        "profile_sha256": expected["effective_profile"]["profile_sha256"],
    }
    basis = {
        "verified": True,
        "check_satisfaction_status": "satisfied",
        "closed_source_scope_kind": "check_input_closure",
        "issuer_verified": True,
        "selected_result_ids": [result_id, additional_id],
    }
    record = {
        "scope_id": expected["scope_id"],
        "expected_check_ref": entry["expected_check_ref"],
        "status": "admitted",
        "admitted": True,
        "origin_status": "verified",
        "obligation_status": "satisfied",
        "result_ids": [result_id, additional_id],
        "result_origins": [
            {
                "result_id": result_id,
                "run_ref": RUN_REF,
                "attempt_ref": ATTEMPT_REF,
                "runner_ref": RUNNER_REF,
                "result_origin": "host",
            },
            {
                "result_id": additional_id,
                "run_ref": RUN_REF,
                "attempt_ref": ATTEMPT_REF,
                "runner_ref": RUNNER_REF,
                "result_origin": "host",
            },
        ],
        "equivalence_basis": basis,
        "reasons": [],
    }
    original = {
        "kind": "engineering-ci-historical-reuse-admission",
        "schema_version": 1,
        "scope_id": expected["scope_id"],
        "expected_set_ref": expected["expected_set_ref"],
        "receipt_ref": None,
        "current_authorization": authorization,
        "evidence_admission": "evaluated",
        "required_gate": "not_evaluated",
        "entries": [record],
        "accepted_coverage_by_instance": {
            key: {
                "admission_status": "admitted",
                "result_ids": [result_id, additional_id],
            },
        },
        "remaining_work": [],
    }
    result = assess(
        universe,
        expected,
        admission=original,
        admission_current_authorization=_df02_current_authorization(),
        admission_source_authority=df02_source_authority(original, universe),
        admission_contract_sample=True,
    )
    assert result["status"] == "passed"
    row = check_row(result, entry)
    assert row["status"] == "satisfied"
    assert row["admission"]["source_kind"] == "historical_reuse"
    assert row["admission"]["witness_result_ids"] == [result_id, additional_id]
    assert row["evidence_refs"] == [result_id, additional_id]
    assert row["structural"]["status"] == "not_supplied"

    changed = copy.deepcopy(original)
    changed["current_authorization"]["target_ref"] = "refs/heads/other"
    with pytest.raises(
        required_assessment.RequiredAssessmentError,
        match="admission_authorization_mismatch",
    ):
        assess(
            universe,
            expected,
            admission=changed,
            admission_current_authorization=_df02_current_authorization(),
            admission_source_authority=lambda _facts: True,
        )
