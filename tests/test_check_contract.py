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
    # A single-tool Check: one native PASS can stand for the supplied instance.
    definition = check_contract.definition_for("tach")
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
        tool=check_contract.tool_ref("tach/check"),
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
        tool=check_contract.tool_ref("tach/check"),
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
        definition_version=definition.definition_version + 1,
    )
    with pytest.raises(
        check_contract.CheckContractError, match="result_check_version_mismatch"
    ):
        check_contract.native_result(
            incompatible,
            source=closed_source(),
            status="passed",
            result_origin="host",
            result_instance="future-revision",
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


def test_declared_input_refs_and_coverage_participate_in_local_satisfaction():
    definition = check_contract.definition_for("tach")
    rule = check_contract.rule_ref("hygiene", 1, digest="a" * 64)
    config = {"kind": "config", "path": "ci-hygiene.toml", "sha256": "b" * 64}
    extra = {"kind": "data", "path": "docs/other.md", "sha256": "c" * 64}
    coverage = {"paths": ["docs/guide.md"]}
    checked = closed_source()
    native = check_contract.native_result(
        definition,
        source=checked,
        status="passed",
        result_origin="host",
        result_instance="input-scope-check",
        native_exit_code=0,
        rule_ref=rule,
        input_refs=[config, extra],
        coverage=coverage,
        evidence={"stdout": ""},
        tool=check_contract.tool_ref("tach/check"),
    )
    expected = {
        "check_id": definition.check_id,
        "definition_version": definition.definition_version,
        "rule_ref": rule,
        "source_ref": checked,
        "input_refs": [config],
        "coverage": coverage,
    }
    assert check_contract.result_covers_expected(native, expected)
    assert not check_contract.result_covers_expected(
        native, {**expected, "input_refs": [{**config, "sha256": "d" * 64}]}
    )
    assert not check_contract.result_covers_expected(
        native, {**expected, "coverage": {"paths": ["docs/other.md"]}}
    )
    with pytest.raises(
        check_contract.CheckContractError, match="expected_input_refs_invalid"
    ):
        check_contract.result_covers_expected(
            native, {**expected, "input_refs": "not-a-list"}
        )
    # Candidate identity matching remains intentionally weaker.
    assert check_contract.result_matches_expected(native, expected)


def test_native_nonexecuted_and_not_applicable_exit_consistency():
    definition = check_contract.definition_for("docs")
    base = {
        "source": source(),
        "result_origin": "host",
        "result_instance": "native-state",
    }
    for status, exit_code, error in (
        ("not_executed", 0, "result_nonexecuted_exit_inconsistent"),
        ("not_executed", 1, "result_nonexecuted_exit_inconsistent"),
        ("not_applicable", 1, "result_not_applicable_exit_inconsistent"),
    ):
        with pytest.raises(check_contract.CheckContractError, match=error):
            check_contract.native_result(
                definition, status=status, native_exit_code=exit_code, **base
            )
    with pytest.raises(
        check_contract.CheckContractError,
        match="result_not_applicable_evidence_missing",
    ):
        check_contract.native_result(
            definition, status="not_applicable", native_exit_code=0, **base
        )
    # A successful native probe may establish non-applicability with evidence.
    native = check_contract.native_result(
        definition,
        status="not_applicable",
        native_exit_code=0,
        evidence={"reason": "no_applicable_documents"},
        **base,
    )
    assert check_contract.validate_result(native) is native
    # Recompute hashes to prove the semantic validator, not just the digest,
    # rejects a contradictory native process result.
    forged = {**native, "native_exit_code": 1}
    payload = {
        key: value
        for key, value in forged.items()
        if key not in {"content_id", "result_id"}
    }
    content = {key: value for key, value in payload.items() if key != "result_instance"}
    forged["content_id"] = check_contract._digest(content)
    forged["result_id"] = check_contract._digest(
        {**payload, "content_id": forged["content_id"]}
    )
    with pytest.raises(
        check_contract.CheckContractError,
        match="result_not_applicable_exit_inconsistent",
    ):
        check_contract.validate_result(forged)


def test_duplicate_result_id_is_refused():
    one = result(instance="job-1")
    with pytest.raises(check_contract.CheckContractError):
        check_contract.validate_result_set([one, one])


def test_composed_docs_result_matches_one_expected_not_the_whole_expected_set():
    definition = check_contract.definition_for("docs")
    checked = closed_source()
    rule = check_contract.rule_ref("docs", 1, digest="a" * 64)
    required_input = {
        "kind": "data",
        "path": "docs/guide.md",
        "sha256": "b" * 64,
    }
    expected = {
        "check_id": definition.check_id,
        "definition_version": definition.definition_version,
        "rule_ref": rule,
        "source_ref": checked,
        "input_refs": [required_input],
    }
    inventory = check_contract.native_result(
        definition,
        source=checked,
        status="passed",
        result_origin="host",
        result_instance="inventory",
        native_exit_code=0,
        rule_ref=rule,
        tool=check_contract.tool_ref("git/ls-files"),
        evidence={"stdout": "docs/guide.md"},
    )
    links = check_contract.native_result(
        definition,
        source=checked,
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
    )
    assert check_contract.results_cover_expected([inventory, links], expected)
    assert not check_contract.results_cover_expected([inventory], expected)
    assert not check_contract.results_cover_expected([links], expected)
    assert not check_contract.result_covers_expected(inventory, expected)
    assert not check_contract.result_covers_expected(links, expected)
    assert not check_contract.results_cover_expected(
        [inventory, links],
        {**expected, "source_ref": closed_source("c" * 40)},
    )
    assert not check_contract.results_cover_expected(
        [inventory, links],
        {**expected, "input_refs": [{"kind": "other"}]},
    )
    with pytest.raises(check_contract.CheckContractError, match="result_id_duplicate"):
        check_contract.results_cover_expected([inventory, inventory], expected)
    with pytest.raises(
        check_contract.CheckContractError, match="expected_rule_unproven"
    ):
        check_contract.results_cover_expected(
            [inventory, links], {**expected, "rule_ref": None}
        )
    unlinked = check_contract.native_result(
        definition,
        source=checked,
        status="passed",
        result_origin="host",
        result_instance="unlinked",
        native_exit_code=0,
        rule_ref=rule,
        tool=check_contract.tool_ref("lychee/docs"),
        input_refs=[required_input],
        evidence={"stdout": "ok"},
    )
    assert not check_contract.results_cover_expected([inventory, unlinked], expected)


def test_failed_native_result_cannot_supply_missing_expected_coverage():
    definition = check_contract.definition_for("docs")
    checked = closed_source()
    rule = check_contract.rule_ref("docs", 1, digest="a" * 64)
    required_input = {
        "kind": "data",
        "path": "docs/guide.md",
        "sha256": "b" * 64,
    }
    inventory = check_contract.native_result(
        definition,
        source=checked,
        status="passed",
        result_origin="host",
        result_instance="inventory",
        native_exit_code=0,
        rule_ref=rule,
        tool=check_contract.tool_ref("git/ls-files"),
        evidence={"stdout": "docs/guide.md"},
    )
    links = check_contract.native_result(
        definition,
        source=checked,
        status="passed",
        result_origin="host",
        result_instance="links",
        native_exit_code=0,
        rule_ref=rule,
        tool=check_contract.tool_ref("lychee/docs"),
        input_refs=[{"kind": "check_result", "result_id": inventory["result_id"]}],
        evidence={"stdout": "ok"},
    )
    failed = check_contract.native_result(
        definition,
        source=checked,
        status="failed",
        result_origin="host",
        result_instance="failed-links",
        native_exit_code=1,
        rule_ref=rule,
        tool=check_contract.tool_ref("lychee/docs"),
        input_refs=[required_input],
        coverage={"paths": ["docs/guide.md"]},
        evidence={"stderr": "bad link"},
    )
    expected = {
        "check_id": definition.check_id,
        "definition_version": definition.definition_version,
        "rule_ref": rule,
        "source_ref": checked,
        "input_refs": [required_input],
    }
    assert not check_contract.results_cover_expected(
        [inventory, links, failed], expected
    )
    assert not check_contract.results_cover_expected(
        [inventory, links, failed],
        {
            **expected,
            "input_refs": [],
            "coverage": {"paths": ["docs/guide.md"]},
        },
    )


def test_expected_satisfaction_selects_valid_successful_alternative():
    definition = check_contract.definition_for("docs")
    checked = closed_source()
    rule = check_contract.rule_ref("docs", 1, digest="a" * 64)
    required_input = {"kind": "data", "path": "docs/guide.md", "sha256": "b" * 64}
    inventory = check_contract.native_result(
        definition,
        source=checked,
        status="passed",
        result_origin="host",
        result_instance="inventory",
        native_exit_code=0,
        rule_ref=rule,
        tool=check_contract.tool_ref("git/ls-files"),
        evidence={"stdout": "docs/guide.md"},
    )

    def link_result(label, *, inputs=(), coverage=None, status="passed"):
        return check_contract.native_result(
            definition,
            source=checked,
            status=status,
            result_origin="host",
            result_instance=label,
            native_exit_code=0 if status == "passed" else 1,
            rule_ref=rule,
            tool=check_contract.tool_ref("lychee/docs"),
            input_refs=[
                {"kind": "check_result", "result_id": inventory["result_id"]},
                *inputs,
            ],
            coverage=coverage,
            evidence={"stdout": "ok"} if status == "passed" else {"stderr": "bad"},
        )

    coverage = {"paths": ["docs/guide.md"]}
    incomplete = link_result("incomplete")
    complete = link_result(
        "complete", inputs=[required_input], coverage=coverage
    )
    failed = link_result(
        "failed", inputs=[required_input], coverage=coverage, status="failed"
    )
    expected = {
        "check_id": definition.check_id,
        "definition_version": definition.definition_version,
        "rule_ref": rule,
        "source_ref": checked,
        "input_refs": [required_input],
        "coverage": coverage,
    }
    decision = check_contract.evaluate_expected_satisfaction(
        [inventory, incomplete, failed, complete], expected
    )
    assert decision["status"] == "satisfied"
    assert decision["selected_result_ids"] == [
        inventory["result_id"],
        complete["result_id"],
    ]
    assert check_contract.results_cover_expected(
        [inventory, incomplete, failed, complete], expected
    )
    assert not check_contract.results_cover_expected(
        [inventory, incomplete, failed],
        expected,
    )
    assert not check_contract.results_cover_expected(
        [inventory, incomplete, failed],
        {**expected, "input_refs": []},
    )


def test_expected_coverage_requires_terminal_native_result():
    definition = check_contract.definition_for("docs")
    checked = closed_source()
    rule = check_contract.rule_ref("docs", 1, digest="a" * 64)
    coverage = {"paths": ["docs/guide.md"]}
    inventory = check_contract.native_result(
        definition,
        source=checked,
        status="passed",
        result_origin="host",
        result_instance="inventory",
        native_exit_code=0,
        rule_ref=rule,
        tool=check_contract.tool_ref("git/ls-files"),
        coverage=coverage,
        evidence={"stdout": "docs/guide.md"},
    )
    links = check_contract.native_result(
        definition,
        source=checked,
        status="passed",
        result_origin="host",
        result_instance="links",
        native_exit_code=0,
        rule_ref=rule,
        tool=check_contract.tool_ref("lychee/docs"),
        input_refs=[{"kind": "check_result", "result_id": inventory["result_id"]}],
        evidence={"stdout": "ok"},
    )
    expected = {
        "check_id": definition.check_id,
        "definition_version": definition.definition_version,
        "rule_ref": rule,
        "source_ref": checked,
        "coverage": coverage,
    }
    decision = check_contract.evaluate_expected_satisfaction(
        [inventory, links], expected
    )
    assert decision["status"] == "unsatisfied"
    assert decision["reason"] == "expected_coverage_unproven"
    assert not check_contract.results_cover_expected([inventory, links], expected)
    # A per-requirement proof may describe producer inventory, but cannot
    # suppress an independently declared terminal scan coverage obligation.
    assert check_contract.results_cover_expected(
        [inventory, links],
        {
            **{key: value for key, value in expected.items() if key != "coverage"},
            "coverage_by_requirement": {"inventory": coverage},
        },
    )
    assert not check_contract.results_cover_expected(
        [inventory, links],
        {
            **expected,
            "coverage_by_requirement": {"inventory": coverage},
        },
    )
    with pytest.raises(
        check_contract.CheckContractError,
        match="expected_coverage_requirement_unknown",
    ):
        check_contract.evaluate_expected_satisfaction(
            [inventory, links],
            {
                **expected,
                "coverage_by_requirement": {"not-a-requirement": coverage},
            },
        )


def test_composed_result_contract_preserves_single_native_check_semantics():
    definition = check_contract.definition_for("tach")
    checked = closed_source()
    rule = check_contract.rule_ref("tach", 1, digest="a" * 64)
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
        result_instance="single",
        native_exit_code=0,
        rule_ref=rule,
        tool=check_contract.tool_ref("tach/check"),
        evidence={"stdout": "ok"},
    )
    assert check_contract.results_cover_expected([passed], expected)
    assert not check_contract.results_cover_expected(
        [passed], {**expected, "definition_version": 1}
    )
