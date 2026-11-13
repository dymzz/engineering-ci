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
    assert inventory.alternatives[0].access_id == "git/ls-files"
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
        assert [
            item.alternatives[0].access_id for item in definition.tool_requirements
        ] == [access]
    for name in ("forwarding", "source"):
        assert check_contract.definition_for(name).tool_requirements == ()


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
    # No ToolAsset identity was verified for Lychee in this contract, so a
    # result may not invent one.
    with pytest.raises(check_contract.CheckContractError, match="result_tool_mismatch"):
        result(tool=observed("lychee/docs", tool_id="lychee"))
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
