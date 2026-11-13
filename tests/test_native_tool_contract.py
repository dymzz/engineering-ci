"""Native tool requirements, observations and multi-result satisfaction."""

import pytest

from engineering_ci import check_contract


def closed_source(tree="a" * 40):
    return check_contract.source_ref(
        "worktree",
        declared_target_tree=tree,
        content_digest="c" * 64,
        verified_scope=["docs/guide.md"],
        scope_kind="check_input_closure",
    )


def observed(access_id, *, tool_id=None, version=None, executable=None):
    return check_contract.tool_ref(
        access_id, tool_id=tool_id, version=version, executable=executable
    )


def result(
    name="docs",
    *,
    tool=None,
    status="passed",
    exit_code=0,
    instance="run-1",
    evidence=None,
    input_refs=(),
):
    definition = check_contract.definition_for(name)
    assert definition is not None
    return check_contract.native_result(
        definition,
        source=closed_source(),
        status=status,
        result_origin="host",
        result_instance=instance,
        native_exit_code=exit_code,
        evidence=evidence if evidence is not None else {"stdout": ""},
        input_refs=input_refs,
        tool=tool,
    )


def requirements(definition):
    return {item.requirement_id: item for item in definition.tool_requirements}


def test_builtin_definitions_declare_their_real_native_tools():
    docs = check_contract.definition_for("docs")
    inventory = requirements(docs)["inventory"]
    links = requirements(docs)["links"]
    assert docs.definition_version == 2
    assert inventory.alternatives[0].access_id == "git/ls-files"
    assert inventory.alternatives[0].authority == "eci_local_entry"
    assert inventory.after == ()
    assert links.alternatives[0].access_id == "lychee/docs"
    assert links.after == ("inventory",)
    assert links.alternatives[0].tool_id is None

    for name, access in (
        ("tach", "tach/check"),
        ("tach_external", "tach/check-external"),
        ("vulture", "vulture/dead-code"),
        ("whitespace", "git/diff-check"),
    ):
        definition = check_contract.definition_for(name)
        assert definition.definition_version == 2
        assert [
            item.alternatives[0].access_id for item in definition.tool_requirements
        ] == [access]
    for name in ("forwarding", "source"):
        definition = check_contract.definition_for(name)
        assert definition.definition_version == 1
        assert definition.tool_requirements == ()


def test_definition_versions_preserve_the_pre_tool_contract():
    docs = check_contract.definition_for("docs")
    legacy = check_contract.validate_definition(
        check_contract.CheckDefinition(check_id=docs.check_id, definition_version=1)
    )
    assert legacy.definition_version == 1
    assert legacy.tool_requirements == ()
    assert legacy.requires_baseline == docs.requires_baseline
    with pytest.raises(
        check_contract.CheckContractError, match="result_check_version_mismatch"
    ):
        check_contract.validate_definition(
            check_contract.CheckDefinition(check_id=docs.check_id, definition_version=3)
        )
    with pytest.raises(check_contract.CheckContractError, match="result_check_unknown"):
        check_contract.validate_definition(
            check_contract.CheckDefinition(
                check_id="eci/not/registered", definition_version=1
            )
        )


def test_definition_rejects_conflicting_tool_requirements():
    alternative = check_contract.ToolIdentity("tool/one")

    def definition(*items):
        return check_contract.CheckDefinition(
            check_id="eci/hygiene/docs",
            definition_version=1,
            tool_requirements=items,
        )

    with pytest.raises(
        check_contract.CheckContractError, match="tool_requirement_duplicate"
    ):
        check_contract.validate_definition(
            definition(
                check_contract.ToolRequirement("scan", (alternative,)),
                check_contract.ToolRequirement("scan", (alternative,)),
            )
        )
    with pytest.raises(
        check_contract.CheckContractError, match="tool_requirement_unknown_prerequisite"
    ):
        check_contract.validate_definition(
            definition(
                check_contract.ToolRequirement(
                    "scan", (alternative,), after=("missing",)
                ),
            )
        )
    with pytest.raises(
        check_contract.CheckContractError, match="tool_requirement_self_prerequisite"
    ):
        check_contract.validate_definition(
            definition(
                check_contract.ToolRequirement("scan", (alternative,), after=("scan",)),
            )
        )
    with pytest.raises(
        check_contract.CheckContractError, match="tool_requirement_cycle"
    ):
        check_contract.validate_definition(
            definition(
                check_contract.ToolRequirement("a", (alternative,), after=("b",)),
                check_contract.ToolRequirement(
                    "b",
                    (check_contract.ToolIdentity("tool/two"),),
                    after=("a",),
                ),
            )
        )
    with pytest.raises(
        check_contract.CheckContractError, match="tool_alternatives_missing"
    ):
        check_contract.validate_definition(
            definition(check_contract.ToolRequirement("scan", ()))
        )
    with pytest.raises(
        check_contract.CheckContractError, match="tool_alternative_duplicate"
    ):
        check_contract.validate_definition(
            definition(
                check_contract.ToolRequirement("scan", (alternative, alternative))
            )
        )


def test_definition_rejects_unverifiable_tool_identity():
    with pytest.raises(
        check_contract.CheckContractError, match="tool_access_id_invalid"
    ):
        check_contract.validate_definition(
            check_contract.CheckDefinition(
                check_id="eci/hygiene/docs",
                tool_requirements=(
                    check_contract.ToolRequirement(
                        "scan", (check_contract.ToolIdentity(""),)
                    ),
                ),
            )
        )
    with pytest.raises(
        check_contract.CheckContractError, match="tool_artifact_sha256_invalid"
    ):
        check_contract.validate_definition(
            check_contract.CheckDefinition(
                check_id="eci/hygiene/docs",
                tool_requirements=(
                    check_contract.ToolRequirement(
                        "scan",
                        (
                            check_contract.ToolIdentity(
                                "tool/one", artifact_sha256="nope"
                            ),
                        ),
                    ),
                ),
            )
        )


def test_forged_tool_requirements_cannot_construct_results():
    docs = check_contract.definition_for("docs")
    forged = check_contract.CheckDefinition(
        check_id=docs.check_id,
        definition_version=docs.definition_version,
        tool_requirements=(
            check_contract.ToolRequirement(
                "anything",
                (check_contract.ToolIdentity("osv/scan"),),
            ),
        ),
    )
    with pytest.raises(
        check_contract.CheckContractError, match="result_tool_requirements_mismatch"
    ):
        check_contract.native_result(
            forged,
            source=closed_source(),
            status="passed",
            result_origin="host",
            result_instance="forged",
            native_exit_code=0,
            evidence={"stdout": ""},
            tool=observed("osv/scan"),
        )


def test_declared_tool_observation_is_recorded_and_binds_identity():
    first = result(tool=observed("lychee/docs", executable="lychee"))
    second = result(tool=observed("lychee/docs", version="0.24.2", executable="lychee"))
    assert first["tool"]["access_id"] == "lychee/docs"
    assert first["tool"]["executable"] == "lychee"
    assert first["tool"]["version"] is None
    assert second["tool"]["version"] == "0.24.2"
    assert first["content_id"] != second["content_id"]
    assert check_contract.validate_result(first) is first


def test_undeclared_or_forbidden_tool_observations_are_refused():
    with pytest.raises(
        check_contract.CheckContractError, match="result_tool_undeclared"
    ):
        result(tool=observed("osv/scan"))
    # No ToolAsset identity was verified for Lychee in this contract, so an
    # ECI-local entry may not invent one.
    with pytest.raises(
        check_contract.CheckContractError, match="tool_id_forbidden_for_local_entry"
    ):
        observed("lychee/docs", tool_id="lychee")
    with pytest.raises(
        check_contract.CheckContractError, match="result_tool_unexpected"
    ):
        result("forwarding", tool=observed("lychee/docs"))


def test_direct_result_needs_a_verified_matching_tool_identity():
    docs = check_contract.definition_for("docs")
    with pytest.raises(
        check_contract.CheckContractError, match="direct_tool_identity_missing"
    ):
        check_contract.native_result(
            docs,
            source=closed_source(),
            status="passed",
            result_origin="direct_tool",
            result_instance="direct",
            native_exit_code=0,
            evidence={"stdout": ""},
            tool=observed("lychee/docs"),
        )


def test_satisfaction_requires_every_declared_tool_result():
    only_links = result(tool=observed("lychee/docs", executable="lychee"))
    decision = check_contract.evaluate_check_satisfaction(
        check_contract.definition_for("docs"), [only_links]
    )
    assert decision["status"] == "unsatisfied"
    assert decision["requirements"][0]["requirement_id"] == "inventory"
    assert decision["requirements"][0]["reason"] == "result_missing"
    assert decision["consumer_scope"] == "supplied_results_only"
    assert decision["required"] is False
    assert decision["trusted"] is False


def test_satisfaction_requires_a_real_prerequisite_reference():
    inventory = result(
        "docs", instance="inventory", tool=observed("git/ls-files", executable="git")
    )
    unreferenced = result(
        "docs",
        instance="links",
        tool=observed("lychee/docs", executable="lychee"),
    )
    decision = check_contract.evaluate_check_satisfaction(
        check_contract.definition_for("docs"), [inventory, unreferenced]
    )
    assert decision["status"] == "unsatisfied"
    assert decision["requirements"][1]["reason"] == "prerequisite_unevidenced"

    referenced = result(
        "docs",
        instance="links",
        tool=observed("lychee/docs", executable="lychee"),
        input_refs=(
            {
                "kind": "check_result",
                "result_id": inventory["result_id"],
                "path": "inventory",
            },
        ),
    )
    satisfied = check_contract.evaluate_check_satisfaction(
        check_contract.definition_for("docs"), [inventory, referenced]
    )
    assert satisfied["status"] == "satisfied"
    assert satisfied["requirements"][0]["satisfied_by"] == inventory["result_id"]
    assert satisfied["requirements"][1]["satisfied_by"] == referenced["result_id"]


def test_satisfaction_rejects_results_from_another_check():
    tach = result("tach", tool=observed("tach/check", executable="tach"))
    with pytest.raises(
        check_contract.CheckContractError, match="satisfaction_check_mismatch"
    ):
        check_contract.evaluate_check_satisfaction(
            check_contract.definition_for("docs"), [tach]
        )


def test_satisfaction_is_not_a_required_decision():
    decision = check_contract.evaluate_check_satisfaction(
        check_contract.definition_for("forwarding"), []
    )
    assert decision["status"] == "not_applicable"
    assert decision["reason"] == "no_tool_requirements"
    assert decision["required"] is False
    assert decision["trusted"] is False


def test_single_result_coverage_cannot_cover_a_multi_tool_check():
    definition = check_contract.definition_for("docs")
    rule = check_contract.rule_ref("hygiene", 1, digest="a" * 64)
    native = check_contract.native_result(
        definition,
        source=closed_source(),
        status="passed",
        result_origin="host",
        result_instance="links",
        native_exit_code=0,
        evidence={"stdout": ""},
        rule_ref=rule,
        tool=observed("lychee/docs"),
    )
    expected = {
        "check_id": definition.check_id,
        "definition_version": definition.definition_version,
        "rule_ref": rule,
        "source_ref": closed_source(),
    }
    assert not check_contract.result_covers_expected(native, expected)

    tach = check_contract.definition_for("tach")
    tach_result = check_contract.native_result(
        tach,
        source=closed_source(),
        status="passed",
        result_origin="host",
        result_instance="tach-run",
        native_exit_code=0,
        evidence={"stdout": ""},
        rule_ref=rule,
        tool=observed("tach/check"),
    )
    assert check_contract.result_covers_expected(
        tach_result,
        {
            "check_id": tach.check_id,
            "definition_version": tach.definition_version,
            "rule_ref": rule,
            "source_ref": closed_source(),
        },
    )


def test_schema_version_one_results_remain_readable():
    payload = {
        "kind": check_contract.RESULT_KIND,
        "schema_version": 1,
        "check": {
            "check_id": "eci/hygiene/docs",
            "definition_version": 1,
            "tool_id": None,
            "access_id": None,
            "rule_ref": None,
            "profile_ref": None,
        },
        "source_ref": closed_source(),
        "input_refs": [],
        "status": "passed",
        "native_exit_code": 0,
        "result_origin": "host",
        "runner_ref": None,
        "result_instance": "legacy",
        "artifact_refs": [],
        "coverage": None,
        "evidence": {"stdout": ""},
        "baseline_ref": None,
        "reason": None,
    }
    content = {key: value for key, value in payload.items() if key != "result_instance"}
    payload["content_id"] = check_contract._digest(content)
    payload["result_id"] = check_contract._digest(payload)
    assert check_contract.validate_result(payload) is payload


def test_single_result_coverage_uses_the_declared_requirement_satisfaction():
    tach = check_contract.definition_for("tach")
    rule = check_contract.rule_ref("hygiene", 1, digest="a" * 64)
    expected = {
        "check_id": tach.check_id,
        "definition_version": tach.definition_version,
        "rule_ref": rule,
        "source_ref": closed_source(),
    }
    bare = check_contract.native_result(
        tach,
        source=closed_source(),
        status="passed",
        result_origin="host",
        result_instance="bare",
        native_exit_code=0,
        evidence={"stdout": ""},
        rule_ref=rule,
    )
    assert (
        check_contract.evaluate_check_satisfaction(tach, [bare])["status"]
        == "unsatisfied"
    )
    assert not check_contract.result_covers_expected(bare, expected)

    with_tool = check_contract.native_result(
        tach,
        source=closed_source(),
        status="passed",
        result_origin="host",
        result_instance="with-tool",
        native_exit_code=0,
        evidence={"stdout": ""},
        rule_ref=rule,
        tool=observed("tach/check"),
    )
    assert (
        check_contract.evaluate_check_satisfaction(tach, [with_tool])["status"]
        == "satisfied"
    )
    assert check_contract.result_covers_expected(with_tool, expected)

    forwarding = check_contract.definition_for("forwarding")
    baseline = {"base_commit": "a" * 40}
    internal = check_contract.native_result(
        forwarding,
        source=closed_source(),
        status="passed",
        result_origin="host",
        result_instance="internal",
        evidence={"analysis": "forwarding"},
        rule_ref=rule,
        baseline_ref=baseline,
    )
    expected_internal = {
        "check_id": forwarding.check_id,
        "definition_version": forwarding.definition_version,
        "rule_ref": rule,
        "source_ref": closed_source(),
        "baseline_ref": baseline,
    }
    assert (
        check_contract.evaluate_check_satisfaction(forwarding, [internal])["status"]
        == "not_applicable"
    )
    assert check_contract.result_covers_expected(internal, expected_internal)


def test_satisfaction_refuses_results_from_different_check_instances():
    docs = check_contract.definition_for("docs")
    rule = check_contract.rule_ref("hygiene", 1, digest="a" * 64)
    other_rule = check_contract.rule_ref("hygiene", 2, digest="b" * 64)
    source_a = closed_source("a" * 40)
    source_b = closed_source("b" * 40)

    def inventory_result(*, source=source_a, rule_value=rule):
        return check_contract.native_result(
            docs,
            source=source,
            status="passed",
            result_origin="host",
            result_instance="inventory",
            native_exit_code=0,
            evidence={"stdout": ""},
            rule_ref=rule_value,
            tool=observed("git/ls-files", executable="git"),
        )

    def links_result(
        inventory,
        *,
        source=source_a,
        rule_value=rule,
        baseline=None,
        instance="links",
    ):
        return check_contract.native_result(
            docs,
            source=source,
            status="passed",
            result_origin="host",
            result_instance=instance,
            native_exit_code=0,
            evidence={"stdout": ""},
            rule_ref=rule_value,
            tool=observed("lychee/docs", executable="lychee"),
            baseline_ref=baseline,
            input_refs=[
                {
                    "kind": "check_result",
                    "result_id": inventory["result_id"],
                    "path": "git/ls-files",
                }
            ],
        )

    inventory = inventory_result()
    assert (
        check_contract.evaluate_check_satisfaction(
            docs, [inventory, links_result(inventory)]
        )["status"]
        == "satisfied"
    )
    # A different run instance of the same Check instance may be combined.
    assert (
        check_contract.evaluate_check_satisfaction(
            docs, [inventory, links_result(inventory, instance="another-job")]
        )["status"]
        == "satisfied"
    )
    for kwargs in (
        {"source": source_b},
        {"rule_value": other_rule},
        {"baseline": {"base_commit": "b" * 40}},
    ):
        with pytest.raises(
            check_contract.CheckContractError, match="satisfaction_instance_mismatch"
        ):
            check_contract.evaluate_check_satisfaction(
                docs, [inventory, links_result(inventory, **kwargs)]
            )


def test_legacy_definition_result_cannot_satisfy_the_upgraded_check():
    docs = check_contract.definition_for("docs")
    rule = check_contract.rule_ref("hygiene", 1, digest="a" * 64)
    legacy = {
        "kind": check_contract.RESULT_KIND,
        "schema_version": 1,
        "check": {
            "check_id": docs.check_id,
            "definition_version": 1,
            "tool_id": None,
            "access_id": None,
            "rule_ref": rule,
            "profile_ref": None,
        },
        "source_ref": closed_source(),
        "input_refs": [],
        "status": "passed",
        "native_exit_code": 0,
        "result_origin": "host",
        "runner_ref": None,
        "result_instance": "legacy",
        "artifact_refs": [],
        "coverage": None,
        "evidence": {"stdout": ""},
        "baseline_ref": None,
        "reason": None,
    }
    content = {key: value for key, value in legacy.items() if key != "result_instance"}
    legacy["content_id"] = check_contract._digest(content)
    legacy["result_id"] = check_contract._digest(legacy)
    assert check_contract.validate_result(legacy) is legacy
    with pytest.raises(
        check_contract.CheckContractError,
        match="satisfaction_definition_version_mismatch",
    ):
        check_contract.evaluate_check_satisfaction(docs, [legacy])
    # The old single-result contract remains readable for its own definition.
    expected_v1 = {
        "check_id": docs.check_id,
        "definition_version": 1,
        "rule_ref": rule,
        "source_ref": closed_source(),
    }
    assert check_contract.result_covers_expected(legacy, expected_v1)
    recreated = check_contract.native_result(
        check_contract.CheckDefinition(check_id=docs.check_id, definition_version=1),
        source=closed_source(),
        status="passed",
        result_origin="host",
        result_instance="recreated",
        native_exit_code=0,
        evidence={"stdout": ""},
        rule_ref=rule,
    )
    assert recreated["check"]["definition_version"] == 1
    assert check_contract.validate_result(recreated)["check"]["definition_version"] == 1
    with pytest.raises(
        check_contract.CheckContractError, match="result_check_version_mismatch"
    ):
        check_contract.native_result(
            check_contract.CheckDefinition(
                check_id=docs.check_id, definition_version=3
            ),
            source=closed_source(),
            status="passed",
            result_origin="host",
            result_instance="future",
            native_exit_code=0,
            evidence={"stdout": ""},
        )


def test_tool_identity_authority_is_explicit():
    with pytest.raises(
        check_contract.CheckContractError, match="tool_id_forbidden_for_local_entry"
    ):
        check_contract.validate_tool_identity(
            check_contract.ToolIdentity("lychee/docs", tool_id="lychee")
        )
    with pytest.raises(
        check_contract.CheckContractError, match="tool_id_required_for_runtime_access"
    ):
        check_contract.validate_tool_identity(
            check_contract.ToolIdentity("osv/scan", authority="runtime_tool_access")
        )
    with pytest.raises(
        check_contract.CheckContractError, match="tool_authority_invalid"
    ):
        check_contract.validate_tool_identity(
            check_contract.ToolIdentity("osv/scan", authority="runtime")
        )
    runtime = check_contract.ToolIdentity(
        "osv/scan", tool_id="osv-scanner", authority="runtime_tool_access"
    )
    assert check_contract.validate_tool_identity(runtime) is runtime
    with pytest.raises(
        check_contract.CheckContractError, match="tool_id_forbidden_for_local_entry"
    ):
        check_contract.tool_ref("lychee/docs", tool_id="lychee")
    observed_runtime = check_contract.tool_ref(
        "osv/scan", tool_id="osv-scanner", authority="runtime_tool_access"
    )
    assert observed_runtime["authority"] == "runtime_tool_access"
