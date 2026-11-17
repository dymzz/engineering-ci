"""#63 T2 historical reuse admission contract tests."""

import copy

import pytest

from engineering_ci import admission, check_contract, expected_checks

EVENT_CURRENT = {
    "base_ref": "refs/heads/main",
    "head_ref": "refs/heads/topic-b",
    "base_commit": "b" * 40,
    "head_commit": "c" * 40,
    "references": [],
}
EVENT_HISTORICAL = {
    "base_ref": "refs/heads/main",
    "head_ref": "refs/heads/topic-a",
    "base_commit": "a" * 40,
    "head_commit": "1" * 40,
    "references": [],
}
STRONG_SOURCE = check_contract.source_ref(
    "worktree",
    declared_target_tree="3" * 40,
    actual_execution_source="worktree",
    content_digest="c" * 64,
    verified_scope=["src/pkg/module.py"],
    scope_kind="check_input_closure",
)
CHANGED_PATHS_SOURCE = check_contract.source_ref(
    "worktree",
    declared_target_tree="3" * 40,
    actual_execution_source="worktree",
    content_digest="c" * 64,
    verified_scope=["src/pkg/module.py"],
    scope_kind="changed_paths",
    binding_status="content_digest",
)
OTHER_SOURCE = check_contract.source_ref(
    "worktree",
    declared_target_tree="3" * 40,
    actual_execution_source="worktree",
    content_digest="e" * 64,
    verified_scope=["src/pkg/module.py"],
    scope_kind="check_input_closure",
)
SELECTION_RULE = {
    "name": "engineering-ci.selection",
    "version": "1",
    "sha256": "a" * 64,
}
RULE_TACH = check_contract.rule_ref("tach", 1, digest="b" * 64)
PROFILE_REF = {"name": "org/engineering", "version": "1"}


def make_entry(*, source=STRONG_SOURCE, event=EVENT_CURRENT, **changes):
    definition = check_contract.definition_for("tach")
    entry = {
        "name": "tach",
        "check_id": definition.check_id,
        "definition_version": definition.definition_version,
        "definition_status": "resolved",
        "event_ref": copy.deepcopy(event),
        "selection_rule_ref": copy.deepcopy(SELECTION_RULE),
        "rule_ref": copy.deepcopy(RULE_TACH),
        "rule_status": "resolved",
        "profile_ref": copy.deepcopy(PROFILE_REF),
        "applicability": {"status": "selected", "reason": "selected-test"},
        "policy_status": "unknown",
        "source_ref": copy.deepcopy(source),
        "input_refs": [],
        "baseline_ref": None,
    }
    entry.update(changes)
    entry["expected_check_ref"] = expected_checks.entry_identity(entry)
    return entry


def make_projection(entry, *, source=STRONG_SOURCE, event=EVENT_CURRENT):
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
        "profile_ref": copy.deepcopy(PROFILE_REF),
        "profile_source": "org-governance",
        "consumer_completeness": "not_claimed",
        "observed_input_closure_status": "not_observed",
        "event_ref": copy.deepcopy(event),
        "source_ref": copy.deepcopy(source),
        "checks": entries,
        "non_check_obligations": [],
        "missing": [],
        "conflicts": [],
    }
    projection["scope_id"] = expected_checks.scope_identity(
        projection["consumer"],
        projection["profile_ref"],
        projection["profile_source"],
        event,
        source,
        SELECTION_RULE,
    )
    projection["expected_set_ref"] = expected_checks.set_identity(
        entries, (), schema_version=1
    )
    return projection


def native(
    *, status="passed", source=STRONG_SOURCE, rule=RULE_TACH, profile_ref=PROFILE_REF
):
    definition = check_contract.definition_for("tach")
    exit_code = 0 if status == "passed" else 1 if status == "failed" else None
    return check_contract.native_result(
        definition,
        source=copy.deepcopy(source),
        status=status,
        result_origin="host",
        result_instance="historical-run-1",
        native_exit_code=exit_code,
        evidence={"stdout": ""},
        rule_ref=copy.deepcopy(rule),
        runner_ref="execution:host",
        profile_ref=copy.deepcopy(profile_ref),
        tool=check_contract.tool_ref("tach/check"),
    )


def pre_proof_for(entry, *, source=STRONG_SOURCE, status="verified_candidate_inputs"):
    definition = check_contract.definition_for(entry["name"])
    source_ref = copy.deepcopy(source)
    if status == "verified_candidate_inputs":
        check_contract.validate_source_ref(source_ref)
    closure_payload = {
        "check_id": definition.check_id,
        "definition_version": definition.definition_version,
        "execution_context": "host",
        "scope_kind": source_ref.get("scope_kind"),
        "snapshot": source_ref.get("snapshot"),
        "content_digest": source_ref.get("content_digest"),
        "verified_input_scope": source_ref.get("verified_input_scope"),
        "declared_target_tree": source_ref.get("declared_target_tree"),
        "rule_ref": entry.get("rule_ref"),
        "profile_ref": entry.get("profile_ref"),
        "baseline_ref": entry.get("baseline_ref"),
        "baseline_source_ref": None,
        "environment_inputs": None,
        "tool_identities": None,
        "prerequisite_inputs": (),
    }
    return {
        "phase": "pre_execution",
        "check_name": entry["name"],
        "check_id": definition.check_id,
        "definition_version": definition.definition_version,
        "scope_id": None,
        "expected_check_ref": entry["expected_check_ref"],
        "execution_context": "host",
        "execution_started": False,
        "trusted": False,
        "required": False,
        "status": status,
        "reason": None
        if status == "verified_candidate_inputs"
        else "fixture_unverified",
        "source_ref": source_ref if status == "verified_candidate_inputs" else None,
        "rule_ref": copy.deepcopy(entry.get("rule_ref")),
        "rule_source_ref": None,
        "profile_ref": copy.deepcopy(entry.get("profile_ref")),
        "baseline_ref": copy.deepcopy(entry.get("baseline_ref")),
        "baseline_source_ref": None,
        "environment_inputs": None,
        "tool_identities": None,
        "prerequisite_inputs": (),
        "closure_digest": admission._digest(closure_payload),
    }


def historical_candidate(
    entry,
    result,
    *,
    admitted=True,
    origin_status="verified",
    obligation_status="satisfied",
    issuer_verified=True,
    historical_scope_id="sha256:historical-scope",
):
    return {
        "historical_scope_id": historical_scope_id,
        "historical_expected_check_ref": entry["expected_check_ref"],
        "historical_event_ref": copy.deepcopy(EVENT_HISTORICAL),
        "origin_trace": {
            "run_ref": "run/099",
            "attempt_ref": "attempt/1",
            "runner_ref": "execution:host",
            "result_origin": "host",
            "result_id": result["result_id"],
        },
        "origin_status": origin_status,
        "obligation_status": obligation_status,
        "admitted": admitted,
        "issuer_verified": issuer_verified,
        "results": [copy.deepcopy(result)],
        "check_name": entry["name"],
        "check_id": entry["check_id"],
        "definition_version": entry["definition_version"],
        "applicability": copy.deepcopy(entry.get("applicability")),
    }


def reuse_policy(**changes):
    policy = {
        "require_closed_source": True,
        "permit_native_fail_reuse": False,
        "forbid_phase_obligation_transfer": True,
        "current_phase": "pre_publish",
        "current_action": "validate",
        "current_target_ref": "refs/heads/main",
    }
    policy.update(changes)
    return policy


def setup_equivalent(*, historical_status="passed"):
    current_entry = make_entry(event=EVENT_CURRENT)
    # Distinct Expected identity for the historical instance with the same inputs.
    historical_entry = make_entry(event=EVENT_HISTORICAL)
    projection = make_projection(current_entry, event=EVENT_CURRENT)
    result = native(status=historical_status, source=STRONG_SOURCE)
    proof = pre_proof_for(current_entry, source=STRONG_SOURCE)
    proof["scope_id"] = projection["scope_id"]
    candidate = historical_candidate(historical_entry, result)
    return projection, proof, candidate, current_entry, historical_entry, result


def test_equivalent_closed_source_historical_pass_is_admitted():
    projection, proof, candidate, current_entry, historical_entry, result = (
        setup_equivalent()
    )

    decision = admission.admit_historical_reuse(
        projection,
        pre_execution_proofs=[proof],
        historical_candidates=[candidate],
        reuse_policy=reuse_policy(),
    )

    key = f"{projection['scope_id']}/{current_entry['expected_check_ref']}"
    coverage = decision["accepted_coverage_by_instance"][key]
    assert decision["kind"] == admission.HISTORICAL_REUSE_KIND
    assert decision["receipt_ref"] is None
    assert decision["status"] == "admitted"
    assert decision["remaining_work"] == []
    assert coverage["admitted"] is True
    assert coverage["source_kind"] == "historical_reuse"
    assert coverage["historical_scope_id"] == candidate["historical_scope_id"]
    assert (
        coverage["historical_expected_check_ref"]
        == historical_entry["expected_check_ref"]
    )
    assert (
        coverage["historical_expected_check_ref"] != current_entry["expected_check_ref"]
    )
    assert coverage["origin_trace"]["run_ref"] == "run/099"
    assert coverage["equivalence_basis"]["verified"] is True
    assert coverage["equivalence_basis"]["closed_source_scope_kind"] == (
        "check_input_closure"
    )
    assert coverage["result_ids"] == [result["result_id"]]
    assert "fixture_origin_claims_are_not_production_trust" in decision["limitations"]


def test_source_content_mismatch_keeps_remaining_work():
    projection, proof, candidate, current_entry, _, _ = setup_equivalent()
    candidate["results"] = [native(status="passed", source=OTHER_SOURCE)]

    decision = admission.admit_historical_reuse(
        projection,
        pre_execution_proofs=[proof],
        historical_candidates=[candidate],
        reuse_policy=reuse_policy(),
    )

    key = f"{projection['scope_id']}/{current_entry['expected_check_ref']}"
    coverage = decision["accepted_coverage_by_instance"][key]
    assert decision["status"] == "rejected"
    assert coverage["admitted"] is False
    assert coverage["reasons"] == ["source_content_mismatch"]
    assert decision["remaining_work"][0]["reason"] == "source_content_mismatch"


def test_changed_paths_pre_proof_is_not_reused():
    current_entry = make_entry(source=CHANGED_PATHS_SOURCE)
    projection = make_projection(current_entry, source=CHANGED_PATHS_SOURCE)
    proof = pre_proof_for(current_entry, source=CHANGED_PATHS_SOURCE)
    proof["scope_id"] = projection["scope_id"]
    historical_entry = make_entry(event=EVENT_HISTORICAL)
    candidate = historical_candidate(
        historical_entry, native(status="passed", source=STRONG_SOURCE)
    )

    decision = admission.admit_historical_reuse(
        projection,
        pre_execution_proofs=[proof],
        historical_candidates=[candidate],
        reuse_policy=reuse_policy(),
    )

    assert decision["status"] == "unknown"
    assert decision["remaining_work"][0]["reason"] == "pre_execution_source_unverified"


def test_unaccepted_historical_origin_is_not_reused():
    projection, proof, candidate, _, _, _ = setup_equivalent()
    candidate["admitted"] = False
    candidate["obligation_status"] = "unknown"
    candidate["issuer_verified"] = False

    decision = admission.admit_historical_reuse(
        projection,
        pre_execution_proofs=[proof],
        historical_candidates=[candidate],
        reuse_policy=reuse_policy(),
    )

    assert decision["status"] == "unknown"
    assert decision["remaining_work"][0]["reason"] == "historical_origin_not_accepted"


def test_verified_native_fail_is_not_successful_coverage():
    projection, proof, candidate, current_entry, _, result = setup_equivalent(
        historical_status="failed"
    )
    candidate["obligation_status"] = "unsatisfied"
    # Origin can be verified for a FAIL, but T2 still must not admit success.
    candidate["admitted"] = True
    candidate["origin_status"] = "verified"
    candidate["issuer_verified"] = True
    candidate["obligation_status"] = "satisfied"

    decision = admission.admit_historical_reuse(
        projection,
        pre_execution_proofs=[proof],
        historical_candidates=[candidate],
        reuse_policy=reuse_policy(),
    )

    key = f"{projection['scope_id']}/{current_entry['expected_check_ref']}"
    coverage = decision["accepted_coverage_by_instance"][key]
    assert coverage["admitted"] is False
    assert coverage["origin_status"] == "verified"
    assert coverage["obligation_status"] == "unsatisfied"
    assert coverage["reasons"] == ["verified_native_fail_not_reusable_as_coverage"]
    assert result["status"] == "failed"
    assert decision["remaining_work"]


def test_missing_pre_execution_proof_keeps_remaining_work():
    projection, _, candidate, _, _, _ = setup_equivalent()

    decision = admission.admit_historical_reuse(
        projection,
        pre_execution_proofs=[],
        historical_candidates=[candidate],
        reuse_policy=reuse_policy(),
    )

    assert decision["status"] == "unknown"
    assert decision["remaining_work"][0]["reason"] == "pre_execution_proof_missing"


def test_rule_mismatch_rejects_reuse():
    projection, proof, candidate, _, _, _ = setup_equivalent()
    other_rule = check_contract.rule_ref("tach", 1, digest="f" * 64)
    candidate["results"] = [
        native(status="passed", source=STRONG_SOURCE, rule=other_rule)
    ]

    decision = admission.admit_historical_reuse(
        projection,
        pre_execution_proofs=[proof],
        historical_candidates=[candidate],
        reuse_policy=reuse_policy(),
    )

    assert decision["remaining_work"][0]["reason"] == "rule_mismatch"


def test_policy_can_forbid_check_reuse():
    projection, proof, candidate, _, _, _ = setup_equivalent()

    decision = admission.admit_historical_reuse(
        projection,
        pre_execution_proofs=[proof],
        historical_candidates=[candidate],
        reuse_policy=reuse_policy(permitted_check_ids=["eci/other"]),
    )

    assert decision["remaining_work"][0]["reason"] == "reuse_policy_check_not_permitted"


def test_historical_not_applicable_is_not_transferred():
    projection, proof, candidate, _, _, _ = setup_equivalent()
    candidate["applicability"] = {"status": "not_applicable", "reason": "old-phase"}

    decision = admission.admit_historical_reuse(
        projection,
        pre_execution_proofs=[proof],
        historical_candidates=[candidate],
        reuse_policy=reuse_policy(),
    )

    assert decision["remaining_work"][0]["reason"] == (
        "historical_phase_obligation_not_transferable"
    )


def test_does_not_require_current_receipt_input():
    projection, proof, candidate, _, _, _ = setup_equivalent()
    # Explicitly no receipt kwarg / current Job is part of the contract.
    decision = admission.admit_historical_reuse(
        projection,
        pre_execution_proofs=[proof],
        historical_candidates=[candidate],
        reuse_policy=reuse_policy(),
    )
    assert decision["receipt_ref"] is None
    assert (
        "current_receipt_not_required_for_pre_execution_reuse"
        in decision["limitations"]
    )


def test_malformed_reuse_policy_raises():
    projection, proof, candidate, _, _, _ = setup_equivalent()
    with pytest.raises(admission.AdmissionError) as error:
        admission.admit_historical_reuse(
            projection,
            pre_execution_proofs=[proof],
            historical_candidates=[candidate],
            reuse_policy={"unknown": True},
        )
    assert error.value.code == "reuse_policy_invalid"
