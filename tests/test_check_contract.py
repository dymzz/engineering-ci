"""Stable Check identity and Native Result contract tests."""

import pytest

from engineering_ci import check_contract


def source(tree="a" * 40):
    return check_contract.source_ref(
        "worktree", declared_target_tree=tree, head_commit="b" * 40
    )


def result(
    name="docs", *, instance="job-a", origin="host", status="passed", source_value=None
):
    definition = check_contract.definition_for(name)
    assert definition is not None
    return check_contract.native_result(
        definition,
        source=source_value or source(),
        status=status,
        result_origin=origin,
        result_instance=instance,
        native_exit_code=0 if status == "passed" else 1,
        evidence={"stdout": ""},
    )


def test_same_check_keeps_identity_but_results_are_distinct():
    first = result(instance="pre-commit")
    second = result(instance="pre-push")
    assert first["check"]["check_id"] == second["check"]["check_id"]
    assert first["result_id"] != second["result_id"]
    assert check_contract.validate_result_set([first, second])


def test_one_job_can_cover_multiple_checks():
    docs = result("docs", instance="job-1")
    tach = result("tach", instance="job-1")
    assert docs["runner_ref"] is None
    assert docs["check"]["check_id"] != tach["check"]["check_id"]
    assert docs["result_id"] != tach["result_id"]


def test_missing_check_identity_is_refused_not_inferred():
    with pytest.raises(check_contract.CheckContractError):
        check_contract.validate_definition({"check_id": "docs"})
    with pytest.raises(check_contract.CheckContractError):
        check_contract.validate_result(
            {
                "kind": check_contract.RESULT_KIND,
                "schema_version": 1,
                "check": {"definition_version": 1},
                "source_ref": source(),
                "status": "passed",
                "result_id": "sha256:" + "0" * 64,
            }
        )
    assert check_contract.definition_for("not-a-check") is None


def test_native_failure_and_reason_are_preserved():
    failed = result(status="failed", instance="job-1")
    assert failed["status"] == "failed"
    assert failed["native_exit_code"] == 1
    check_contract.validate_result(failed)


def test_rule_or_origin_change_produces_a_different_result():
    definition = check_contract.definition_for("docs")
    rule_a = check_contract.rule_ref("hygiene", 1, digest="a" * 64)
    rule_b = check_contract.rule_ref("hygiene", 2, digest="b" * 64)
    base = {
        "source": source(),
        "status": "passed",
        "result_origin": "host",
        "result_instance": "job-1",
        "native_exit_code": 0,
        "evidence": {"stdout": ""},
    }
    first = check_contract.native_result(definition, rule_ref=rule_a, **base)
    second = check_contract.native_result(definition, rule_ref=rule_b, **base)
    assert first["result_id"] != second["result_id"]
    assert check_contract.validate_result_set([first, second])


def test_declared_tree_does_not_claim_byte_binding():
    identity = check_contract.source_ref(
        "worktree", declared_target_tree="a" * 40, head_commit="b" * 40
    )
    assert identity["binding_status"] == "identity_only"
    with pytest.raises(check_contract.CheckContractError):
        check_contract.source_ref(
            "worktree",
            declared_target_tree="a" * 40,
            binding_status="byte_tree",
        )
    scoped = check_contract.source_ref(
        "worktree",
        declared_target_tree="a" * 40,
        content_digest="c" * 64,
        verified_scope=["docs/guide.md"],
    )
    assert scoped["binding_status"] == "content_digest"
    assert scoped["verified_input_scope"] == ["docs/guide.md"]


def test_tampered_result_is_rejected():
    original = result(instance="job-1")
    tampered = dict(original)
    tampered["status"] = "passed"
    tampered["native_exit_code"] = 1
    with pytest.raises(check_contract.CheckContractError):
        check_contract.validate_result(tampered)
    changed_id = dict(original)
    changed_id["result_id"] = "sha256:" + "0" * 64
    with pytest.raises(check_contract.CheckContractError):
        check_contract.validate_result(changed_id)
    unknown = result(instance="job-2")
    unknown["check"]["check_id"] = "eci/not-registered"
    with pytest.raises(check_contract.CheckContractError):
        check_contract.validate_result(unknown)


def test_result_id_binds_native_evidence_and_instance():
    definition = check_contract.definition_for("docs")
    base = {
        "source": source(),
        "status": "failed",
        "result_origin": "host",
        "result_instance": "job-1",
        "native_exit_code": 2,
    }
    first = check_contract.native_result(
        definition, evidence={"findings": ["broken link"]}, **base
    )
    second = check_contract.native_result(
        definition, evidence={"findings": ["other link"]}, **base
    )
    assert first["content_id"] != second["content_id"]
    assert first["result_id"] != second["result_id"]
    same_content = check_contract.native_result(
        definition,
        evidence={"findings": ["broken link"]},
        **{**base, "result_instance": "job-2"},
    )
    assert same_content["content_id"] == first["content_id"]
    assert same_content["result_id"] != first["result_id"]


def test_constructor_and_validator_share_direct_tool_requirements():
    definition = check_contract.definition_for("docs")
    assert definition is not None
    with pytest.raises(
        check_contract.CheckContractError, match="direct_tool_identity_missing"
    ):
        check_contract.native_result(
            definition,
            source=source(),
            status="passed",
            result_origin="direct_tool",
            result_instance="direct-attempt",
            native_exit_code=0,
            evidence={"stdout": "checked"},
        )

    # A valid public construction must also pass public validation.
    native = check_contract.native_result(
        definition,
        source=source(),
        status="passed",
        result_origin="host",
        result_instance="host-attempt",
        native_exit_code=0,
        evidence={"stdout": "checked"},
    )
    assert check_contract.validate_result(native) is native


def test_definition_rejects_reference_digest_rejected_by_result_validator():
    definition = check_contract.definition_for("docs")
    assert definition is not None
    invalid = check_contract.CheckDefinition(
        check_id=definition.check_id,
        definition_version=definition.definition_version,
        rule_ref={"name": "project", "version": "1", "sha256": "invalid"},
    )
    with pytest.raises(
        check_contract.CheckContractError, match="rule_ref_sha256_invalid"
    ):
        check_contract.validate_definition(invalid)
    with pytest.raises(
        check_contract.CheckContractError, match="rule_ref_sha256_invalid"
    ):
        check_contract.native_result(
            invalid,
            source=source(),
            status="passed",
            result_origin="host",
            result_instance="bad-definition",
            native_exit_code=0,
            evidence={"stdout": "checked"},
        )


def test_scoped_definitions_report_complete_partial_unknown():
    complete = check_contract.resolve_definitions(["docs", "tach"])
    assert complete["status"] == "complete"
    assert len(complete["definitions"]) == 2
    partial = check_contract.resolve_definitions(["docs", "unknown-check"])
    assert partial["status"] == "partial"
    assert partial["missing"] == ["unknown-check"]
    unknown = check_contract.resolve_definitions(["unknown-check"])
    assert unknown["status"] == "unknown"
    empty = check_contract.resolve_definitions([])
    assert empty["status"] == "unknown"
    assert empty["reason"] == "empty_request_not_proof"
    assert empty["consumer_completeness"] == "not_claimed"


def closed_source(tree="a" * 40):
    return check_contract.source_ref(
        "worktree",
        declared_target_tree=tree,
        content_digest="c" * 64,
        verified_scope=["docs/guide.md"],
        scope_kind="check_input_closure",
    )


def test_distinct_result_instances_can_be_matched_without_claiming_coverage():
    definition = check_contract.definition_for("docs")
    first = check_contract.native_result(
        definition,
        source=closed_source("a" * 40),
        status="passed",
        result_origin="host",
        result_instance="ref-a",
        native_exit_code=0,
        evidence={"stdout": ""},
    )
    second = check_contract.native_result(
        definition,
        source=closed_source("b" * 40),
        status="failed",
        result_origin="host",
        result_instance="ref-b",
        native_exit_code=1,
        evidence={"stderr": "broken"},
    )
    assert check_contract.validate_result_set([first, second])
    expected = {"check_id": definition.check_id, "source_ref": first["source_ref"]}
    assert check_contract.result_matches_expected(first, expected)
    assert not check_contract.result_matches_expected(second, expected)
    # Candidate matching does not certify native PASS, full consumer
    # completeness, evidence admission or Required Gate.
    assert check_contract.result_matches_expected(
        second, {"check_id": definition.check_id}
    )
    assert not hasattr(check_contract, "validate_expected_coverage")
    assert not hasattr(check_contract, "validate_completeness_receipt")
    assert hasattr(check_contract, "result_covers_expected")


def test_native_satisfaction_is_strictly_per_supplied_expected_instance():
    definition = check_contract.definition_for("docs")
    rule = check_contract.rule_ref("hygiene", 1, digest="a" * 64)
    checked = closed_source("a" * 40)
    expected = {
        "check_id": definition.check_id,
        "definition_version": definition.definition_version,
        "rule_ref": rule,
        "source_ref": checked,
    }
    passed = check_contract.native_result(
        definition,
        source=checked,
        status="passed",
        result_origin="host",
        result_instance="run-a",
        native_exit_code=0,
        evidence={"stdout": ""},
        rule_ref=rule,
    )
    failed = check_contract.native_result(
        definition,
        source=checked,
        status="failed",
        result_origin="host",
        result_instance="run-b",
        native_exit_code=1,
        evidence={"stderr": "broken"},
        rule_ref=rule,
    )
    assert check_contract.result_covers_expected(passed, expected)
    assert not check_contract.result_covers_expected(failed, expected)
    assert not check_contract.result_covers_expected(
        passed, {**expected, "source_ref": closed_source("b" * 40)}
    )
    with pytest.raises(check_contract.CheckContractError):
        check_contract.result_covers_expected(passed, {"check_id": definition.check_id})
    # This local predicate cannot vouch for a consumer ExpectedSet or Required Gate.
    assert not hasattr(check_contract, "validate_expected_coverage")


def test_comparison_check_requires_immutable_baseline_for_association():
    definition = check_contract.definition_for("forwarding")
    assert definition.requires_baseline
    baseline = {"base_commit": "a" * 40}
    native = check_contract.native_result(
        definition,
        source=closed_source(),
        status="passed",
        result_origin="host",
        result_instance="forwarding-run",
        evidence={"analysis": "forwarding"},
        baseline_ref=baseline,
    )
    assert check_contract.validate_result(native) is native
    assert not check_contract.result_matches_expected(
        native, {"check_id": definition.check_id}
    )
    assert not check_contract.result_matches_expected(
        native,
        {"check_id": definition.check_id, "baseline_ref": {"base_commit": "b" * 40}},
    )
    assert check_contract.result_matches_expected(
        native, {"check_id": definition.check_id, "baseline_ref": baseline}
    )
    rule = check_contract.rule_ref("hygiene", 1, digest="a" * 64)
    scoped = {
        "check_id": definition.check_id,
        "definition_version": definition.definition_version,
        "rule_ref": rule,
        "source_ref": closed_source(),
    }
    with pytest.raises(
        check_contract.CheckContractError, match="expected_baseline_missing"
    ):
        check_contract.validate_expected_instance(scoped)
    with pytest.raises(
        check_contract.CheckContractError, match="result_baseline_missing"
    ):
        check_contract.native_result(
            definition,
            source=closed_source(),
            status="passed",
            result_origin="host",
            result_instance="missing-baseline",
            evidence={"analysis": "forwarding"},
        )
    with pytest.raises(
        check_contract.CheckContractError, match="result_baseline_missing"
    ):
        check_contract.native_result(
            definition,
            source=closed_source(),
            status="passed",
            result_origin="host",
            result_instance="unresolved-base",
            evidence={"analysis": "forwarding"},
            baseline_ref={"base_ref": "origin/main"},
        )
    assert check_contract.definition_for("whitespace").requires_baseline
    assert not check_contract.definition_for("docs").requires_baseline


def test_constructor_and_validator_reject_unknown_definition_revision():
    definition = check_contract.definition_for("docs")
    incompatible = check_contract.CheckDefinition(
        check_id=definition.check_id,
        definition_version=2,
    )
    with pytest.raises(
        check_contract.CheckContractError, match="result_check_version_mismatch"
    ):
        check_contract.native_result(
            incompatible,
            source=closed_source(),
            status="passed",
            result_origin="host",
            result_instance="revision-2",
            native_exit_code=0,
            evidence={"stdout": ""},
        )
    forged = check_contract.CheckDefinition(
        check_id=definition.check_id,
        definition_version=1,
        requires_baseline=True,
    )
    with pytest.raises(
        check_contract.CheckContractError, match="result_check_semantics_mismatch"
    ):
        check_contract.native_result(
            forged,
            source=closed_source(),
            status="passed",
            result_origin="host",
            result_instance="forged",
            native_exit_code=0,
            evidence={"stdout": ""},
        )


def test_baseline_ref_changes_result_identity():
    definition = check_contract.definition_for("docs")
    base = {
        "source": closed_source(),
        "status": "passed",
        "result_origin": "host",
        "result_instance": "job-1",
        "native_exit_code": 0,
        "evidence": {"stdout": ""},
    }
    first = check_contract.native_result(
        definition, baseline_ref={"base_commit": "a" * 40}, **base
    )
    second = check_contract.native_result(
        definition, baseline_ref={"base_commit": "b" * 40}, **base
    )
    assert first["result_id"] != second["result_id"]
    assert first["baseline_ref"] != second["baseline_ref"]


def test_duplicate_result_id_is_refused():
    one = result(instance="job-1")
    with pytest.raises(check_contract.CheckContractError):
        check_contract.validate_result_set([one, one])
