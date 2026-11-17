"""#63 T2 historical reuse admission contract tests."""

import copy

import pytest

from engineering_ci import admission, check_contract, expected_checks, reconciliation

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
IMPLEMENTATION = {
    "implementation_id": "eci.test.tach-checker",
    "implementation_version": "1",
    "implementation_sha256": "d" * 64,
}
REPORT_REF = {
    "kind": reconciliation.EXECUTION_REPORT_KIND,
    "schema_version": 1,
    "sha256": "9" * 64,
}
AUTH = {
    "phase": "pre_publish",
    "action": "validate",
    "target_ref": "refs/heads/main",
}


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


def pre_proof_for(
    entry,
    *,
    source=STRONG_SOURCE,
    status="verified_candidate_inputs",
    changed_paths_content_digest=None,
):
    definition = check_contract.definition_for(entry["name"])
    source_ref = copy.deepcopy(source)
    if status == "verified_candidate_inputs":
        check_contract.validate_source_ref(source_ref)
    if changed_paths_content_digest is None:
        expected_source = entry.get("source_ref") or {}
        if (
            expected_source.get("scope_kind") == "changed_paths"
            and isinstance(source_ref, dict)
            and source_ref.get("scope_kind")
            in {"check_input_closure", "full_input_closure"}
            and set(expected_source.get("verified_input_scope") or ())
            < set(source_ref.get("verified_input_scope") or ())
        ):
            changed_paths_content_digest = expected_source.get("content_digest")
    proof = {
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
        "changed_paths_content_digest": changed_paths_content_digest,
    }
    proof["closure_digest"] = admission._recompute_closure_digest(proof)
    return proof


def result_origin_for(result, *, report_ref=REPORT_REF):
    return {
        "result_id": result["result_id"],
        "run_ref": "run/099",
        "attempt_ref": "attempt/1",
        "runner_ref": "execution:host",
        "result_origin": result["result_origin"],
        "report_ref": copy.deepcopy(report_ref),
        "job_receipt_ref": "sha256:" + ("e" * 64),
    }


def historical_candidate(
    entry,
    result,
    *,
    historical_scope_id="sha256:historical-scope",
    implementation=IMPLEMENTATION,
    applicability=None,
):
    results = [result] if isinstance(result, dict) else list(result)
    return {
        "historical_scope_id": historical_scope_id,
        "historical_expected_check_ref": entry["expected_check_ref"],
        "historical_event_ref": copy.deepcopy(EVENT_HISTORICAL),
        "result_origins": [result_origin_for(item) for item in results],
        "results": [copy.deepcopy(item) for item in results],
        "verification_implementation": copy.deepcopy(implementation),
        "check_name": entry["name"],
        "check_id": entry["check_id"],
        "definition_version": entry["definition_version"],
        "applicability": copy.deepcopy(
            applicability if applicability is not None else entry.get("applicability")
        ),
    }


def reuse_policy(**changes):
    definition = check_contract.definition_for("tach")
    policy = {
        "approved_implementations": [
            {
                "check_id": definition.check_id,
                "definition_version": definition.definition_version,
                "implementation_id": IMPLEMENTATION["implementation_id"],
                "implementation_version": IMPLEMENTATION["implementation_version"],
                "implementation_sha256": IMPLEMENTATION["implementation_sha256"],
            }
        ],
    }
    policy.update(changes)
    return policy


def accept_all(_context):
    return True


def reject_all(_context):
    return False


def admit(
    projection,
    proof,
    candidate,
    *,
    historical_origin_authority=accept_all,
    pre_execution_authority=accept_all,
    authorization_authority=accept_all,
    current_authorization=AUTH,
    reuse_policy_value=None,
):
    proofs = [proof] if proof is not None else []
    return admission.admit_historical_reuse(
        projection,
        pre_execution_proofs=proofs,
        historical_candidates=[candidate],
        reuse_policy=reuse_policy_value or reuse_policy(),
        current_authorization=current_authorization,
        historical_origin_authority=historical_origin_authority,
        pre_execution_authority=pre_execution_authority,
        authorization_authority=authorization_authority,
    )


def setup_equivalent(*, historical_status="passed"):
    current_entry = make_entry(event=EVENT_CURRENT)
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

    decision = admit(projection, proof, candidate)

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
    assert coverage["result_origins"][0]["run_ref"] == "run/099"
    assert coverage["equivalence_basis"]["verified"] is True
    assert coverage["equivalence_basis"]["origin_authenticated"] is True
    assert coverage["equivalence_basis"]["current_phase"] == "pre_publish"
    assert coverage["result_ids"] == [result["result_id"]]
    assert (
        "fixture_authority_verifiers_are_not_production_trust"
        in decision["limitations"]
    )


def test_self_declared_flags_without_origin_authority_remain_unresolved():
    projection, proof, candidate, _, _, _ = setup_equivalent()

    decision = admit(
        projection,
        proof,
        candidate,
        historical_origin_authority=None,
    )

    assert decision["status"] == "unknown"
    assert decision["remaining_work"][0]["reason"] == (
        "historical_origin_authority_missing"
    )


def test_origin_authority_rejection_blocks_matching_native_inputs():
    projection, proof, candidate, _, _, _ = setup_equivalent()

    decision = admit(
        projection,
        proof,
        candidate,
        historical_origin_authority=reject_all,
    )

    assert decision["remaining_work"][0]["reason"] == "historical_origin_not_accepted"


def test_fake_pre_execution_proof_without_authority_is_rejected():
    projection, proof, candidate, _, _, _ = setup_equivalent()

    decision = admit(
        projection,
        proof,
        candidate,
        pre_execution_authority=None,
    )

    assert decision["remaining_work"][0]["reason"] == "pre_execution_authority_missing"


def test_tampered_closure_digest_is_rejected():
    projection, proof, candidate, _, _, _ = setup_equivalent()
    proof["closure_digest"] = "sha256:" + ("0" * 64)

    decision = admit(projection, proof, candidate)

    assert decision["remaining_work"][0]["reason"] == (
        "pre_execution_closure_digest_mismatch"
    )


def test_phase_authorization_difference_yields_separate_outcomes():
    projection, proof, candidate, _, _, _ = setup_equivalent()

    def only_pre_publish(context):
        return context["current_authorization"]["phase"] == "pre_publish"

    admitted = admit(
        projection,
        proof,
        candidate,
        authorization_authority=only_pre_publish,
        current_authorization={**AUTH, "phase": "pre_publish"},
    )
    blocked = admit(
        projection,
        proof,
        candidate,
        authorization_authority=only_pre_publish,
        current_authorization={**AUTH, "phase": "post_publish"},
    )

    assert admitted["status"] == "admitted"
    assert blocked["remaining_work"][0]["reason"] == "current_authorization_unverified"


def test_candidate_digest_authorization_difference():
    projection, proof, candidate, _, _, _ = setup_equivalent()

    def only_candidate_a(context):
        return context["current_authorization"].get("candidate_digest") == "cand-a"

    admitted = admit(
        projection,
        proof,
        candidate,
        authorization_authority=only_candidate_a,
        current_authorization={**AUTH, "candidate_digest": "cand-a"},
    )
    blocked = admit(
        projection,
        proof,
        candidate,
        authorization_authority=only_candidate_a,
        current_authorization={**AUTH, "candidate_digest": "cand-b"},
    )

    assert admitted["status"] == "admitted"
    assert blocked["remaining_work"][0]["reason"] == "current_authorization_unverified"


def test_unapproved_implementation_blocks_reuse():
    projection, proof, candidate, _, _, _ = setup_equivalent()
    candidate["verification_implementation"] = {
        "implementation_id": "eci.pr.fixed-pass",
        "implementation_version": "1",
        "implementation_sha256": "f" * 64,
    }

    decision = admit(projection, proof, candidate)

    assert decision["remaining_work"][0]["reason"] == (
        "verification_implementation_unapproved"
    )


def test_result_origin_membership_mismatch_raises():
    projection, proof, candidate, _, _, result = setup_equivalent()
    candidate["result_origins"][0]["result_id"] = "missing-result"

    with pytest.raises(admission.AdmissionError) as error:
        admit(projection, proof, candidate)
    assert error.value.code == "historical_result_origin_membership_mismatch"
    assert result["result_id"] != "missing-result"


def test_source_content_mismatch_keeps_remaining_work():
    projection, proof, candidate, current_entry, _, _ = setup_equivalent()
    other = native(status="passed", source=OTHER_SOURCE)
    candidate["results"] = [other]
    candidate["result_origins"] = [result_origin_for(other)]

    decision = admit(projection, proof, candidate)

    key = f"{projection['scope_id']}/{current_entry['expected_check_ref']}"
    coverage = decision["accepted_coverage_by_instance"][key]
    assert decision["status"] == "rejected"
    assert coverage["admitted"] is False
    assert coverage["reasons"] == ["source_content_mismatch"]


def test_changed_paths_pre_proof_is_not_reused():
    current_entry = make_entry(source=CHANGED_PATHS_SOURCE)
    projection = make_projection(current_entry, source=CHANGED_PATHS_SOURCE)
    proof = pre_proof_for(current_entry, source=CHANGED_PATHS_SOURCE)
    proof["scope_id"] = projection["scope_id"]
    historical_entry = make_entry(event=EVENT_HISTORICAL)
    candidate = historical_candidate(
        historical_entry, native(status="passed", source=STRONG_SOURCE)
    )

    decision = admit(projection, proof, candidate)

    assert decision["status"] == "unknown"
    assert decision["remaining_work"][0]["reason"] == "pre_execution_source_unverified"


def test_verified_native_fail_is_not_successful_coverage():
    projection, proof, candidate, current_entry, _, result = setup_equivalent(
        historical_status="failed"
    )

    decision = admit(projection, proof, candidate)

    key = f"{projection['scope_id']}/{current_entry['expected_check_ref']}"
    coverage = decision["accepted_coverage_by_instance"][key]
    assert coverage["admitted"] is False
    assert coverage["origin_status"] == "verified"
    assert coverage["obligation_status"] == "unsatisfied"
    assert coverage["reasons"] == ["verified_native_fail_not_reusable_as_coverage"]
    assert result["status"] == "failed"


def test_missing_pre_execution_proof_keeps_remaining_work():
    projection, _, candidate, _, _, _ = setup_equivalent()

    decision = admit(projection, None, candidate)

    assert decision["status"] == "unknown"
    assert decision["remaining_work"][0]["reason"] == "pre_execution_proof_missing"


def test_rule_mismatch_rejects_reuse():
    projection, proof, candidate, _, _, _ = setup_equivalent()
    other_rule = check_contract.rule_ref("tach", 1, digest="f" * 64)
    other = native(status="passed", source=STRONG_SOURCE, rule=other_rule)
    candidate["results"] = [other]
    candidate["result_origins"] = [result_origin_for(other)]

    decision = admit(projection, proof, candidate)

    assert decision["remaining_work"][0]["reason"] == "rule_mismatch"


def test_policy_can_forbid_check_reuse():
    projection, proof, candidate, _, _, _ = setup_equivalent()

    decision = admit(
        projection,
        proof,
        candidate,
        reuse_policy_value=reuse_policy(permitted_check_ids=["eci/other"]),
    )

    assert decision["remaining_work"][0]["reason"] == "reuse_policy_check_not_permitted"


def test_historical_not_applicable_execution_may_cover_current_required():
    """Historical obligation decision must not transfer; real PASS still reusable."""
    projection, proof, candidate, current_entry, _, result = setup_equivalent()
    candidate["applicability"] = {"status": "not_applicable", "reason": "old-phase"}

    decision = admit(projection, proof, candidate)

    key = f"{projection['scope_id']}/{current_entry['expected_check_ref']}"
    coverage = decision["accepted_coverage_by_instance"][key]
    assert decision["status"] == "admitted"
    assert coverage["admitted"] is True
    assert coverage["source_kind"] == "historical_reuse"
    assert coverage["result_ids"] == [result["result_id"]]
    assert decision["remaining_work"] == []


SUBSET_CHANGED_SOURCE = check_contract.source_ref(
    "worktree",
    declared_target_tree="3" * 40,
    actual_execution_source="worktree",
    content_digest="a" * 64,
    verified_scope=["src/pkg/module.py"],
    scope_kind="changed_paths",
    binding_status="content_digest",
)
EXPANDED_CLOSED_SOURCE = check_contract.source_ref(
    "worktree",
    declared_target_tree="3" * 40,
    actual_execution_source="worktree",
    content_digest="b" * 64,
    verified_scope=["src/pkg/module.py", "src/pkg/extra.py"],
    scope_kind="check_input_closure",
)


def test_weak_expected_promoted_by_authenticated_closed_p0_is_admitted():
    """X≠Y changed_paths→closed expansion with bound subset digest is admitted."""
    current_entry = make_entry(source=SUBSET_CHANGED_SOURCE, event=EVENT_CURRENT)
    historical_entry = make_entry(event=EVENT_HISTORICAL)
    projection = make_projection(
        current_entry, source=SUBSET_CHANGED_SOURCE, event=EVENT_CURRENT
    )
    result = native(status="passed", source=EXPANDED_CLOSED_SOURCE)
    proof = pre_proof_for(
        current_entry,
        source=EXPANDED_CLOSED_SOURCE,
        changed_paths_content_digest="a" * 64,
    )
    proof["scope_id"] = projection["scope_id"]
    candidate = historical_candidate(historical_entry, result)

    decision = admit(projection, proof, candidate)

    key = f"{projection['scope_id']}/{current_entry['expected_check_ref']}"
    coverage = decision["accepted_coverage_by_instance"][key]
    assert current_entry["source_ref"]["content_digest"] == "a" * 64
    assert proof["source_ref"]["content_digest"] == "b" * 64
    assert proof["changed_paths_content_digest"] == "a" * 64
    assert decision["status"] == "admitted"
    assert coverage["admitted"] is True
    assert coverage["equivalence_basis"]["closed_source_scope_kind"] == (
        "check_input_closure"
    )


def test_forged_changed_paths_digest_blocks_expansion_reuse():
    current_entry = make_entry(source=SUBSET_CHANGED_SOURCE, event=EVENT_CURRENT)
    historical_entry = make_entry(event=EVENT_HISTORICAL)
    projection = make_projection(
        current_entry, source=SUBSET_CHANGED_SOURCE, event=EVENT_CURRENT
    )
    result = native(status="passed", source=EXPANDED_CLOSED_SOURCE)
    proof = pre_proof_for(
        current_entry,
        source=EXPANDED_CLOSED_SOURCE,
        changed_paths_content_digest="a" * 64,
    )
    proof["scope_id"] = projection["scope_id"]
    # Forge the subset digest without rebinding closure_digest.
    proof["changed_paths_content_digest"] = "f" * 64
    candidate = historical_candidate(historical_entry, result)

    decision = admit(projection, proof, candidate)

    assert decision["remaining_work"][0]["reason"] == (
        "pre_execution_closure_digest_mismatch"
    )


def test_missing_changed_paths_digest_blocks_x_neq_y_expansion():
    current_entry = make_entry(source=SUBSET_CHANGED_SOURCE, event=EVENT_CURRENT)
    historical_entry = make_entry(event=EVENT_HISTORICAL)
    projection = make_projection(
        current_entry, source=SUBSET_CHANGED_SOURCE, event=EVENT_CURRENT
    )
    result = native(status="passed", source=EXPANDED_CLOSED_SOURCE)
    proof = pre_proof_for(
        current_entry,
        source=EXPANDED_CLOSED_SOURCE,
        changed_paths_content_digest=None,
    )
    # Force absence even if helper would auto-fill.
    proof["changed_paths_content_digest"] = None
    proof["closure_digest"] = admission._recompute_closure_digest(proof)
    proof["scope_id"] = projection["scope_id"]
    candidate = historical_candidate(historical_entry, result)

    decision = admit(projection, proof, candidate)

    assert decision["remaining_work"][0]["reason"] == (
        "expected_source_content_digest_mismatch"
    )


def test_does_not_require_current_receipt_input():
    projection, proof, candidate, _, _, _ = setup_equivalent()
    decision = admit(projection, proof, candidate)
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
            current_authorization=AUTH,
            historical_origin_authority=accept_all,
            pre_execution_authority=accept_all,
            authorization_authority=accept_all,
        )
    assert error.value.code == "reuse_policy_invalid"
