"""Native tool requirements, observations and multi-result satisfaction."""

import pytest

from engineering_ci import check_contract, weaver_tool


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
        check_contract.CheckContractError, match="tool_asset_source_invalid"
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


def test_tool_entry_authority_and_asset_identity_are_separate_facts():
    # An ECI-local entry can invoke an asset whose identity is verified
    # elsewhere; the entry namespace does not erase the asset identity.
    local_with_asset = check_contract.ToolIdentity(
        "lychee/docs",
        tool_id="lychee",
        tool_id_source="eci_pinned_asset",
    )
    assert check_contract.validate_tool_identity(local_with_asset) is local_with_asset
    # ... but a claimed asset identity without a source is refused.
    with pytest.raises(
        check_contract.CheckContractError, match="tool_asset_source_invalid"
    ):
        check_contract.validate_tool_identity(
            check_contract.ToolIdentity("lychee/docs", tool_id="lychee")
        )
    with pytest.raises(
        check_contract.CheckContractError, match="tool_id_source_without_asset"
    ):
        check_contract.validate_tool_identity(
            check_contract.ToolIdentity(
                "lychee/docs", tool_id_source="eci_pinned_asset"
            )
        )
    # Runtime ToolAccess keys use the Runtime-registered text, not ECI slash
    # naming, and must carry the registered ToolAsset identity.
    runtime = check_contract.ToolIdentity(
        "trivy.scan",
        tool_id="trivy",
        tool_id_source="runtime_tool_asset",
        authority="runtime_tool_access",
    )
    assert check_contract.validate_tool_identity(runtime) is runtime
    assert check_contract.validate_tool_identity(
        check_contract.ToolIdentity(
            "trivy.sbom",
            tool_id="trivy",
            tool_id_source="runtime_tool_asset",
            authority="runtime_tool_access",
        )
    )
    with pytest.raises(
        check_contract.CheckContractError, match="tool_id_required_for_runtime_access"
    ):
        check_contract.validate_tool_identity(
            check_contract.ToolIdentity("trivy.scan", authority="runtime_tool_access")
        )
    with pytest.raises(
        check_contract.CheckContractError, match="tool_asset_source_mismatch"
    ):
        check_contract.validate_tool_identity(
            check_contract.ToolIdentity(
                "trivy.scan",
                tool_id="trivy",
                tool_id_source="eci_pinned_asset",
                authority="runtime_tool_access",
            )
        )
    with pytest.raises(
        check_contract.CheckContractError, match="tool_access_id_invalid"
    ):
        check_contract.validate_tool_identity(
            check_contract.ToolIdentity("trivy.scan", authority="eci_local_entry")
        )
    with pytest.raises(
        check_contract.CheckContractError, match="tool_authority_invalid"
    ):
        check_contract.validate_tool_identity(
            check_contract.ToolIdentity("osv/scan", authority="runtime")
        )
    with pytest.raises(
        check_contract.CheckContractError, match="tool_asset_source_invalid"
    ):
        check_contract.tool_ref("lychee/docs", tool_id="lychee")
    observed_runtime = check_contract.tool_ref(
        "trivy.scan",
        tool_id="trivy",
        tool_id_source="runtime_tool_asset",
        authority="runtime_tool_access",
    )
    assert observed_runtime["authority"] == "runtime_tool_access"
    assert observed_runtime["tool_id_source"] == "runtime_tool_asset"


def _weaver_observed(
    access_id,
    *,
    tool_id="weaver",
    tool_id_source="eci_pinned_asset",
    version=weaver_tool.VERSION,
    artifact_sha256=weaver_tool.LINUX_SHA256,
    authority="eci_local_entry",
):
    return check_contract.tool_ref(
        access_id,
        tool_id=tool_id,
        tool_id_source=tool_id_source,
        version=version,
        artifact_sha256=artifact_sha256,
        authority=authority,
        executable="/usr/local/bin/weaver",
    )


def test_test_process_definitions_keep_distinct_engine_identities():
    unittest_def = check_contract.definition_for("unittest")
    pytest_def = check_contract.definition_for("pytest")
    assert unittest_def is not None and pytest_def is not None
    assert unittest_def.check_id == "eci/tests/unittest"
    assert pytest_def.check_id == "eci/tests/pytest"
    assert unittest_def.check_id in check_contract.PROCESS_CHECK_IDS
    assert pytest_def.check_id in check_contract.PROCESS_CHECK_IDS
    assert unittest_def.definition_version == 1
    assert pytest_def.definition_version == 1
    assert not unittest_def.requires_baseline
    assert not pytest_def.requires_baseline
    assert not unittest_def.requires_post_completion
    assert not pytest_def.requires_post_completion
    assert [
        item.alternatives[0].access_id for item in unittest_def.tool_requirements
    ] == ["python/unittest"]
    assert [
        item.alternatives[0].access_id for item in pytest_def.tool_requirements
    ] == ["python/pytest"]

    checked = closed_source()
    rule = check_contract.rule_ref("tests", 1, digest="a" * 64)
    pytest_pass = check_contract.native_result(
        pytest_def,
        source=checked,
        status="passed",
        result_origin="host",
        result_instance="pytest-run",
        native_exit_code=0,
        evidence={"stdout": "2 passed"},
        rule_ref=rule,
        tool=observed("python/pytest", executable="pytest"),
    )
    expected_pytest = {
        "check_id": pytest_def.check_id,
        "definition_version": pytest_def.definition_version,
        "rule_ref": rule,
        "source_ref": checked,
    }
    expected_unittest = {
        "check_id": unittest_def.check_id,
        "definition_version": unittest_def.definition_version,
        "rule_ref": rule,
        "source_ref": checked,
    }
    assert (
        check_contract.evaluate_expected_satisfaction([pytest_pass], expected_pytest)[
            "status"
        ]
        == "satisfied"
    )
    assert not check_contract.result_matches_expected(pytest_pass, expected_unittest)
    mismatch = check_contract.evaluate_expected_satisfaction(
        [pytest_pass], expected_unittest
    )
    assert mismatch["status"] == "unsatisfied"
    assert mismatch["reason"] == "native_result_mismatched"
    with pytest.raises(
        check_contract.CheckContractError, match="satisfaction_check_mismatch"
    ):
        check_contract.evaluate_check_satisfaction(unittest_def, [pytest_pass])

    forged = check_contract.CheckDefinition(
        check_id=unittest_def.check_id,
        definition_version=unittest_def.definition_version,
        tool_requirements=pytest_def.tool_requirements,
    )
    with pytest.raises(
        check_contract.CheckContractError, match="result_tool_requirements_mismatch"
    ):
        check_contract.native_result(
            forged,
            source=checked,
            status="passed",
            result_origin="host",
            result_instance="forged",
            native_exit_code=0,
            evidence={"stdout": "ok"},
            tool=observed("python/pytest"),
        )


def _weaver_step(
    definition,
    access_id,
    *,
    source,
    rule,
    instance,
    status="passed",
    native_exit_code=0,
    evidence=None,
    input_refs=(),
    phase=None,
    coverage=None,
):
    return check_contract.native_result(
        definition,
        source=source,
        status=status,
        result_origin="dagger",
        result_instance=instance,
        native_exit_code=native_exit_code,
        evidence=evidence or {"stdout": "ok"},
        rule_ref=rule,
        tool=_weaver_observed(access_id),
        input_refs=input_refs,
        phase=phase,
        coverage=coverage,
    )


def _weaver_packaging_chain(
    definition,
    *,
    source,
    rule,
    prefix="reg",
    include_local=True,
    local_status="passed",
    local_exit_code=0,
    phase=None,
):
    check = _weaver_step(
        definition,
        "weaver/registry-check",
        source=source,
        rule=rule,
        instance=f"{prefix}-check",
        evidence={"findings": 0},
        phase=phase,
    )
    pkg = _weaver_step(
        definition,
        "weaver/registry-package",
        source=source,
        rule=rule,
        instance=f"{prefix}-pkg",
        evidence={"resolved_sha256": "d" * 64},
        input_refs=[{"kind": "check_result", "result_id": check["result_id"]}],
        phase=phase,
    )
    results = [check, pkg]
    if include_local:
        local = _weaver_step(
            definition,
            "weaver/registry-local-check",
            source=source,
            rule=rule,
            instance=f"{prefix}-local",
            status=local_status,
            native_exit_code=local_exit_code,
            evidence={"findings": 0 if local_status == "passed" else 1},
            input_refs=[{"kind": "check_result", "result_id": pkg["result_id"]}],
            phase=phase,
        )
        results.append(local)
    return results


def _weaver_baseline_chain(
    definition,
    *,
    source,
    rule,
    registry_local,
    prefix="base",
    local_status="passed",
    local_exit_code=0,
    diff_status="passed",
    diff_exit_code=0,
    include_diff=True,
    link_baseline=True,
    diff_coverage=None,
):
    check = _weaver_step(
        definition,
        "weaver/baseline-check",
        source=source,
        rule=rule,
        instance=f"{prefix}-check",
        evidence={"findings": 0},
    )
    pkg = _weaver_step(
        definition,
        "weaver/baseline-package",
        source=source,
        rule=rule,
        instance=f"{prefix}-pkg",
        evidence={"resolved_sha256": "e" * 64},
        input_refs=[{"kind": "check_result", "result_id": check["result_id"]}],
    )
    local = _weaver_step(
        definition,
        "weaver/baseline-local-check",
        source=source,
        rule=rule,
        instance=f"{prefix}-local",
        status=local_status,
        native_exit_code=local_exit_code,
        evidence={"findings": 0 if local_status == "passed" else 1},
        input_refs=[{"kind": "check_result", "result_id": pkg["result_id"]}],
    )
    results = [check, pkg, local]
    if include_diff:
        diff_inputs = [
            {"kind": "check_result", "result_id": registry_local["result_id"]}
        ]
        if link_baseline:
            diff_inputs.append(
                {"kind": "check_result", "result_id": local["result_id"]}
            )
        diff = _weaver_step(
            definition,
            "weaver/registry-diff",
            source=source,
            rule=rule,
            instance=f"{prefix}-diff",
            status=diff_status,
            native_exit_code=diff_exit_code,
            evidence={"diff_findings": 0 if diff_status == "passed" else 1},
            input_refs=diff_inputs,
            coverage=diff_coverage,
        )
        results.append(diff)
    return results


def test_weaver_definitions_pin_tool_asset_and_prerequisite_stages():
    registry_def = check_contract.definition_for("weaver_registry")
    live_def = check_contract.definition_for("weaver_live")
    terminal_def = check_contract.definition_for("weaver_terminal")
    for definition in (registry_def, live_def, terminal_def):
        assert definition is not None
        assert definition.check_id in check_contract.PROCESS_CHECK_IDS
        assert definition.definition_version == 1
        assert not definition.requires_baseline
    assert not registry_def.requires_post_completion
    assert not live_def.requires_post_completion
    assert terminal_def.requires_post_completion

    reg_reqs = requirements(registry_def)
    assert list(reg_reqs) == [
        "registry-check",
        "registry-package",
        "registry-local-check",
        "baseline-check",
        "baseline-package",
        "baseline-local-check",
        "registry-diff",
    ]
    assert reg_reqs["registry-package"].after == ("registry-check",)
    assert reg_reqs["registry-local-check"].after == ("registry-package",)
    assert reg_reqs["baseline-check"].when == "registry_baseline_rule"
    assert reg_reqs["baseline-package"].after == ("baseline-check",)
    assert reg_reqs["baseline-package"].when == "registry_baseline_rule"
    assert reg_reqs["baseline-local-check"].after == ("baseline-package",)
    assert reg_reqs["baseline-local-check"].when == "registry_baseline_rule"
    assert reg_reqs["registry-diff"].after == (
        "registry-local-check",
        "baseline-local-check",
    )
    assert reg_reqs["registry-diff"].when == "registry_baseline_rule"

    live_reqs = requirements(live_def)
    assert list(live_reqs) == [
        "registry-check",
        "registry-package",
        "registry-local-check",
        "live-check",
    ]
    assert live_reqs["registry-package"].after == ("registry-check",)
    assert live_reqs["registry-local-check"].after == ("registry-package",)
    assert live_reqs["live-check"].after == ("registry-local-check",)

    term_reqs = requirements(terminal_def)
    assert list(term_reqs) == [
        "registry-check",
        "registry-package",
        "registry-local-check",
        "terminal-live-check",
    ]
    assert term_reqs["registry-package"].after == ("registry-check",)
    assert term_reqs["registry-local-check"].after == ("registry-package",)
    assert term_reqs["terminal-live-check"].after == ("registry-local-check",)

    for alt in (
        *(item.alternatives[0] for item in reg_reqs.values()),
        *(item.alternatives[0] for item in live_reqs.values()),
        *(item.alternatives[0] for item in term_reqs.values()),
    ):
        assert alt.tool_id == "weaver"
        assert alt.tool_id_source == "eci_pinned_asset"
        assert alt.version == weaver_tool.VERSION
        assert alt.artifact_sha256 == weaver_tool.LINUX_SHA256
        assert alt.authority == "eci_local_entry"

    # Container is NOT a Check Origin; native Dagger container execution uses dagger.
    assert "container" not in check_contract.ORIGINS
    checked = closed_source()
    rule = check_contract.rule_ref("weaver", 1, digest="b" * 64)
    with pytest.raises(
        check_contract.CheckContractError, match="result_origin_invalid"
    ):
        check_contract.native_result(
            live_def,
            source=checked,
            status="passed",
            result_origin="container",
            result_instance="bad-origin",
            native_exit_code=0,
            evidence={"stdout": ""},
            rule_ref=rule,
            tool=_weaver_observed("weaver/registry-package"),
        )

    reg_check, pkg, local = _weaver_packaging_chain(
        live_def, source=checked, rule=rule, prefix="live"
    )
    unlinked_live = check_contract.native_result(
        live_def,
        source=checked,
        status="passed",
        result_origin="dagger",
        result_instance="live-unlinked",
        native_exit_code=0,
        evidence={"findings": 0},
        rule_ref=rule,
        tool=_weaver_observed("weaver/live-check"),
    )
    missing_link = check_contract.evaluate_check_satisfaction(
        live_def, [reg_check, pkg, local, unlinked_live]
    )
    assert missing_link["status"] == "unsatisfied"
    assert missing_link["reason"] == "prerequisite_unevidenced"

    with pytest.raises(check_contract.CheckContractError, match="result_tool_mismatch"):
        check_contract.native_result(
            live_def,
            source=checked,
            status="passed",
            result_origin="dagger",
            result_instance="live-wrong-version",
            native_exit_code=0,
            evidence={"findings": 0},
            rule_ref=rule,
            tool=_weaver_observed("weaver/live-check", version="0.0.1"),
            input_refs=[{"kind": "check_result", "result_id": local["result_id"]}],
        )
    with pytest.raises(check_contract.CheckContractError, match="result_tool_mismatch"):
        check_contract.native_result(
            live_def,
            source=checked,
            status="passed",
            result_origin="dagger",
            result_instance="live-wrong-sha",
            native_exit_code=0,
            evidence={"findings": 0},
            rule_ref=rule,
            tool=_weaver_observed("weaver/live-check", artifact_sha256="0" * 64),
            input_refs=[{"kind": "check_result", "result_id": local["result_id"]}],
        )
    with pytest.raises(check_contract.CheckContractError, match="result_tool_mismatch"):
        check_contract.native_result(
            live_def,
            source=checked,
            status="passed",
            result_origin="dagger",
            result_instance="live-unpinned",
            native_exit_code=0,
            evidence={"findings": 0},
            rule_ref=rule,
            tool=check_contract.tool_ref("weaver/live-check"),
            input_refs=[{"kind": "check_result", "result_id": local["result_id"]}],
        )

    valid_live = check_contract.native_result(
        live_def,
        source=checked,
        status="passed",
        result_origin="dagger",
        result_instance="live-valid",
        native_exit_code=0,
        evidence={"findings": 0},
        rule_ref=rule,
        tool=_weaver_observed("weaver/live-check"),
        input_refs=[{"kind": "check_result", "result_id": local["result_id"]}],
    )
    expected_live = {
        "check_id": live_def.check_id,
        "definition_version": live_def.definition_version,
        "rule_ref": rule,
        "source_ref": checked,
    }
    decision = check_contract.evaluate_expected_satisfaction(
        [reg_check, pkg, local, valid_live], expected_live
    )
    assert decision["status"] == "satisfied"
    assert decision["selected_result_ids"] == [
        reg_check["result_id"],
        pkg["result_id"],
        local["result_id"],
        valid_live["result_id"],
    ]


def test_weaver_terminal_enforces_post_completion_phase_semantics():
    terminal_def = check_contract.definition_for("weaver_terminal")
    live_def = check_contract.definition_for("weaver_live")
    checked = closed_source()
    rule = check_contract.rule_ref("weaver", 1, digest="e" * 64)

    with pytest.raises(check_contract.CheckContractError, match="result_phase_invalid"):
        check_contract.native_result(
            terminal_def,
            source=checked,
            status="not_applicable",
            result_origin="host",
            result_instance="bad-phase",
            rule_ref=rule,
            phase="pre_merge",
        )

    for bad_phase in (None, check_contract.EXECUTION_PHASE):
        with pytest.raises(
            check_contract.CheckContractError,
            match="result_post_completion_phase_required",
        ):
            check_contract.native_result(
                terminal_def,
                source=checked,
                status="passed",
                result_origin="dagger",
                result_instance=f"term-bad-{bad_phase}",
                native_exit_code=0,
                evidence={"findings": 0},
                rule_ref=rule,
                tool=_weaver_observed("weaver/terminal-live-check"),
                phase=bad_phase,
            )

    normal_pr_na = check_contract.native_result(
        terminal_def,
        source=checked,
        status="not_applicable",
        result_origin="host",
        result_instance="pr-na",
        rule_ref=rule,
        reason="requires post-completion plan",
    )
    assert check_contract.validate_result(normal_pr_na) is normal_pr_na

    # Tampered raw payload claiming passed without post_completion is rejected too.
    mutated = dict(normal_pr_na)
    mutated["status"] = "passed"
    mutated["native_exit_code"] = 0
    mutated["evidence"] = {"findings": 0}
    mutated["phase"] = check_contract.EXECUTION_PHASE
    content = {
        key: value
        for key, value in mutated.items()
        if key not in {"result_instance", "content_id", "result_id"}
    }
    mutated["content_id"] = check_contract._digest(content)
    mutated["result_id"] = check_contract._digest(
        {key: value for key, value in mutated.items() if key != "result_id"}
    )
    with pytest.raises(
        check_contract.CheckContractError,
        match="result_post_completion_phase_required",
    ):
        check_contract.validate_result(mutated)

    for missing_phase_expected in (
        {
            "check_id": terminal_def.check_id,
            "definition_version": terminal_def.definition_version,
            "rule_ref": rule,
            "source_ref": checked,
        },
        {
            "check_id": terminal_def.check_id,
            "definition_version": terminal_def.definition_version,
            "rule_ref": rule,
            "source_ref": checked,
            "phase": check_contract.EXECUTION_PHASE,
        },
    ):
        with pytest.raises(
            check_contract.CheckContractError,
            match="expected_post_completion_phase_required",
        ):
            check_contract.validate_expected_instance(missing_phase_expected)

    expected_terminal = {
        "check_id": terminal_def.check_id,
        "definition_version": terminal_def.definition_version,
        "rule_ref": rule,
        "source_ref": checked,
        "phase": check_contract.POST_COMPLETION_PHASE,
    }
    assert (
        check_contract.validate_expected_instance(expected_terminal)
        is expected_terminal
    )
    assert not check_contract.result_matches_expected(normal_pr_na, expected_terminal)
    assert (
        check_contract.evaluate_expected_satisfaction(
            [normal_pr_na], expected_terminal
        )["status"]
        == "unsatisfied"
    )

    live_in_post = check_contract.native_result(
        live_def,
        source=checked,
        status="passed",
        result_origin="dagger",
        result_instance="live-post",
        native_exit_code=0,
        evidence={"findings": 0},
        rule_ref=rule,
        tool=_weaver_observed("weaver/live-check"),
        phase=check_contract.POST_COMPLETION_PHASE,
    )
    assert not check_contract.result_matches_expected(live_in_post, expected_terminal)

    forged = check_contract.CheckDefinition(
        check_id=terminal_def.check_id,
        definition_version=terminal_def.definition_version,
        requires_post_completion=False,
        tool_requirements=terminal_def.tool_requirements,
    )
    with pytest.raises(
        check_contract.CheckContractError, match="result_check_semantics_mismatch"
    ):
        check_contract.native_result(
            forged,
            source=checked,
            status="passed",
            result_origin="dagger",
            result_instance="forged-term",
            native_exit_code=0,
            evidence={"findings": 0},
        )
    with pytest.raises(
        check_contract.CheckContractError, match="result_check_semantics_mismatch"
    ):
        check_contract.evaluate_check_satisfaction(forged, [])

    term_check, term_pkg, term_local = _weaver_packaging_chain(
        terminal_def,
        source=checked,
        rule=rule,
        prefix="term",
        phase=check_contract.POST_COMPLETION_PHASE,
    )
    term_live = check_contract.native_result(
        terminal_def,
        source=checked,
        status="passed",
        result_origin="dagger",
        result_instance="term-live",
        native_exit_code=0,
        evidence={"findings": 0, "run_id": "run-1"},
        rule_ref=rule,
        tool=_weaver_observed("weaver/terminal-live-check"),
        input_refs=[{"kind": "check_result", "result_id": term_local["result_id"]}],
        phase=check_contract.POST_COMPLETION_PHASE,
    )
    term_decision = check_contract.evaluate_expected_satisfaction(
        [term_check, term_pkg, term_local, term_live], expected_terminal
    )
    assert term_decision["status"] == "satisfied"
    assert term_decision["selected_result_ids"] == [
        term_check["result_id"],
        term_pkg["result_id"],
        term_local["result_id"],
        term_live["result_id"],
    ]


def test_weaver_registry_live_terminal_require_registry_local_check():
    registry_def = check_contract.definition_for("weaver_registry")
    live_def = check_contract.definition_for("weaver_live")
    terminal_def = check_contract.definition_for("weaver_terminal")
    checked = closed_source()
    rule = check_contract.rule_ref("weaver", 1, digest="c" * 64)

    # 1) weaver_registry: omitting or failing registry-local-check is unsatisfied.
    reg_no_local = _weaver_packaging_chain(
        registry_def, source=checked, rule=rule, prefix="reg-miss", include_local=False
    )
    expected_reg = {
        "check_id": registry_def.check_id,
        "definition_version": registry_def.definition_version,
        "rule_ref": rule,
        "source_ref": checked,
    }
    missing_reg_decision = check_contract.evaluate_expected_satisfaction(
        reg_no_local, expected_reg
    )
    assert missing_reg_decision["status"] == "unsatisfied"
    assert missing_reg_decision["reason"] == "result_missing"

    reg_failed_local = _weaver_packaging_chain(
        registry_def,
        source=checked,
        rule=rule,
        prefix="reg-fail",
        local_status="failed",
        local_exit_code=1,
    )
    failed_reg_decision = check_contract.evaluate_expected_satisfaction(
        reg_failed_local, expected_reg
    )
    assert failed_reg_decision["status"] == "unsatisfied"
    assert failed_reg_decision["reason"] == "result_not_passed"

    reg_full = _weaver_packaging_chain(
        registry_def, source=checked, rule=rule, prefix="reg-pass"
    )
    pass_reg_decision = check_contract.evaluate_expected_satisfaction(
        reg_full, expected_reg
    )
    assert pass_reg_decision["status"] == "satisfied"
    assert pass_reg_decision["selected_result_ids"] == [
        item["result_id"] for item in reg_full
    ]

    # 2) weaver_live: omitting or failing registry-local-check is unsatisfied even if live-check passes.
    expected_live = {
        "check_id": live_def.check_id,
        "definition_version": live_def.definition_version,
        "rule_ref": rule,
        "source_ref": checked,
    }
    live_no_local = _weaver_packaging_chain(
        live_def, source=checked, rule=rule, prefix="live-miss", include_local=False
    )
    live_from_pkg = _weaver_step(
        live_def,
        "weaver/live-check",
        source=checked,
        rule=rule,
        instance="live-from-pkg",
        input_refs=[
            {"kind": "check_result", "result_id": live_no_local[1]["result_id"]}
        ],
    )
    assert (
        check_contract.evaluate_expected_satisfaction(
            [*live_no_local, live_from_pkg], expected_live
        )["status"]
        == "unsatisfied"
    )

    live_failed_local = _weaver_packaging_chain(
        live_def,
        source=checked,
        rule=rule,
        prefix="live-fail",
        local_status="failed",
        local_exit_code=1,
    )
    assert (
        check_contract.evaluate_expected_satisfaction(live_failed_local, expected_live)[
            "status"
        ]
        == "unsatisfied"
    )

    # 3) weaver_terminal: omitting or failing registry-local-check is unsatisfied.
    expected_term = {
        "check_id": terminal_def.check_id,
        "definition_version": terminal_def.definition_version,
        "rule_ref": rule,
        "source_ref": checked,
        "phase": check_contract.POST_COMPLETION_PHASE,
    }
    term_no_local = _weaver_packaging_chain(
        terminal_def,
        source=checked,
        rule=rule,
        prefix="term-miss",
        include_local=False,
        phase=check_contract.POST_COMPLETION_PHASE,
    )
    term_from_pkg = _weaver_step(
        terminal_def,
        "weaver/terminal-live-check",
        source=checked,
        rule=rule,
        instance="term-from-pkg",
        input_refs=[
            {"kind": "check_result", "result_id": term_no_local[1]["result_id"]}
        ],
        phase=check_contract.POST_COMPLETION_PHASE,
    )
    assert (
        check_contract.evaluate_expected_satisfaction(
            [*term_no_local, term_from_pkg], expected_term
        )["status"]
        == "unsatisfied"
    )

    term_failed_local = _weaver_packaging_chain(
        terminal_def,
        source=checked,
        rule=rule,
        prefix="term-fail",
        local_status="failed",
        local_exit_code=1,
        phase=check_contract.POST_COMPLETION_PHASE,
    )
    assert (
        check_contract.evaluate_expected_satisfaction(term_failed_local, expected_term)[
            "status"
        ]
        == "unsatisfied"
    )


def test_weaver_registry_conditional_baseline_diff_driven_by_controlled_rule():
    registry_def = check_contract.definition_for("weaver_registry")
    live_def = check_contract.definition_for("weaver_live")
    checked = closed_source()

    baseline_rule = check_contract.rule_ref(
        "ci-weaver",
        1,
        path="ci-weaver.toml",
        digest="a" * 64,
        baseline_required=True,
    )
    non_baseline_rule = check_contract.rule_ref(
        "ci-weaver",
        1,
        path="ci-weaver.toml",
        digest="b" * 64,
        baseline_required=False,
    )
    expected_with_baseline = {
        "check_id": registry_def.check_id,
        "definition_version": registry_def.definition_version,
        "rule_ref": baseline_rule,
        "source_ref": checked,
    }
    expected_no_baseline = {
        "check_id": registry_def.check_id,
        "definition_version": registry_def.definition_version,
        "rule_ref": non_baseline_rule,
        "source_ref": checked,
    }

    # Baseline enabled: missing baseline stages / registry-diff fails satisfaction.
    reg_only = _weaver_packaging_chain(
        registry_def, source=checked, rule=baseline_rule, prefix="base-reg"
    )
    missing_base = check_contract.evaluate_expected_satisfaction(
        reg_only, expected_with_baseline
    )
    assert missing_base["status"] == "unsatisfied"
    assert missing_base["reason"] == "result_missing"

    # Baseline packaging passes, registry-diff omitted -> unsatisfied.
    base_no_diff = _weaver_baseline_chain(
        registry_def,
        source=checked,
        rule=baseline_rule,
        registry_local=reg_only[-1],
        include_diff=False,
    )
    missing_diff = check_contract.evaluate_expected_satisfaction(
        [*reg_only, *base_no_diff], expected_with_baseline
    )
    assert missing_diff["status"] == "unsatisfied"
    assert missing_diff["reason"] == "result_missing"

    # Baseline local-check fails -> unsatisfied even without diff.
    base_failed_local = _weaver_baseline_chain(
        registry_def,
        source=checked,
        rule=baseline_rule,
        registry_local=reg_only[-1],
        prefix="base-fail-local",
        local_status="failed",
        local_exit_code=1,
        include_diff=False,
    )
    failed_base = check_contract.evaluate_expected_satisfaction(
        [*reg_only, *base_failed_local], expected_with_baseline
    )
    assert failed_base["status"] == "unsatisfied"
    assert failed_base["reason"] == "result_not_passed"

    # registry-diff fails -> unsatisfied.
    base_failed_diff = _weaver_baseline_chain(
        registry_def,
        source=checked,
        rule=baseline_rule,
        registry_local=reg_only[-1],
        prefix="base-fail-diff",
        diff_status="failed",
        diff_exit_code=1,
    )
    failed_diff = check_contract.evaluate_expected_satisfaction(
        [*reg_only, *base_failed_diff], expected_with_baseline
    )
    assert failed_diff["status"] == "unsatisfied"
    assert failed_diff["reason"] == "result_not_passed"

    # registry-diff passes without linking baseline-local-check -> unsatisfied.
    base_unlinked_diff = _weaver_baseline_chain(
        registry_def,
        source=checked,
        rule=baseline_rule,
        registry_local=reg_only[-1],
        prefix="base-unlinked",
        link_baseline=False,
    )
    unlinked_diff = check_contract.evaluate_expected_satisfaction(
        [*reg_only, *base_unlinked_diff], expected_with_baseline
    )
    assert unlinked_diff["status"] == "unsatisfied"
    assert unlinked_diff["reason"] == "prerequisite_unevidenced"

    # Baseline enabled and full DAG linked & passing -> satisfied.
    cov = {"kind": "weaver_registry", "diff_checked": True}
    base_pass = _weaver_baseline_chain(
        registry_def,
        source=checked,
        rule=baseline_rule,
        registry_local=reg_only[-1],
        prefix="base-pass",
        diff_coverage=cov,
    )
    satisfied_base = check_contract.evaluate_expected_satisfaction(
        [*reg_only, *base_pass],
        {**expected_with_baseline, "coverage_by_requirement": {"registry-diff": cov}},
    )
    assert satisfied_base["status"] == "satisfied"
    assert satisfied_base["selected_result_ids"] == [
        *[item["result_id"] for item in reg_only],
        *[item["result_id"] for item in base_pass],
    ]

    # Shared ci-weaver.toml rule with baseline_required=True does not invent
    # baseline requirements on weaver_live.
    live_chain = _weaver_packaging_chain(
        live_def, source=checked, rule=baseline_rule, prefix="live-shared-rule"
    )
    live_pass = _weaver_step(
        live_def,
        "weaver/live-check",
        source=checked,
        rule=baseline_rule,
        instance="live-shared-end",
        input_refs=[{"kind": "check_result", "result_id": live_chain[-1]["result_id"]}],
    )
    assert (
        check_contract.evaluate_expected_satisfaction(
            [*live_chain, live_pass],
            {
                "check_id": live_def.check_id,
                "definition_version": live_def.definition_version,
                "rule_ref": baseline_rule,
                "source_ref": checked,
            },
        )["status"]
        == "satisfied"
    )

    # No baseline configured -> satisfies cleanly with packaging chain only.
    no_base_reg = _weaver_packaging_chain(
        registry_def, source=checked, rule=non_baseline_rule, prefix="nobase"
    )
    no_base_decision = check_contract.evaluate_expected_satisfaction(
        no_base_reg, expected_no_baseline
    )
    assert no_base_decision["status"] == "satisfied"
    assert no_base_decision["selected_result_ids"] == [
        item["result_id"] for item in no_base_reg
    ]

    # Unexpected/forged registry-diff when baseline is not configured is rejected.
    spurious_diff = _weaver_step(
        registry_def,
        "weaver/registry-diff",
        source=checked,
        rule=non_baseline_rule,
        instance="spurious-diff",
        input_refs=[
            {"kind": "check_result", "result_id": no_base_reg[-1]["result_id"]}
        ],
    )
    spurious_decision = check_contract.evaluate_expected_satisfaction(
        [*no_base_reg, spurious_diff], expected_no_baseline
    )
    assert spurious_decision["status"] == "unsatisfied"
    assert spurious_decision["reason"] == "unexpected_conditional_requirement"

    # Rule-binding mismatches and unproven baseline toggles are rejected.
    mismatched_rule = check_contract.evaluate_expected_satisfaction(
        no_base_reg, expected_with_baseline
    )
    assert mismatched_rule["status"] == "unsatisfied"
    assert mismatched_rule["reason"] == "native_result_mismatched"

    with pytest.raises(check_contract.CheckContractError, match="rule_ref_unproven"):
        check_contract.rule_ref("ci-weaver", 1, baseline_required=True)
    with pytest.raises(
        check_contract.CheckContractError, match="rule_baseline_required_invalid"
    ):
        check_contract.rule_ref(
            "ci-weaver", 1, digest="a" * 64, baseline_required="yes"
        )
    with pytest.raises(
        check_contract.CheckContractError, match="expected_baseline_rule_unproven"
    ):
        check_contract.validate_expected_instance(
            {**expected_no_baseline, "baseline_required": True}
        )
    with pytest.raises(
        check_contract.CheckContractError,
        match="expected_coverage_requirement_unknown",
    ):
        check_contract.validate_expected_instance(
            {
                **expected_no_baseline,
                "coverage_by_requirement": {"registry-diff": {"diff_checked": True}},
            }
        )


def test_tool_requirement_conditional_prerequisite_validation():
    alt_a = check_contract.ToolIdentity("tool/a")
    alt_b = check_contract.ToolIdentity("tool/b")
    with pytest.raises(
        check_contract.CheckContractError, match="tool_requirement_condition_invalid"
    ):
        check_contract.validate_tool_requirements(
            (check_contract.ToolRequirement("a", (alt_a,), when="uncontrolled"),)
        )
    with pytest.raises(
        check_contract.CheckContractError,
        match="tool_requirement_conditional_prerequisite",
    ):
        check_contract.validate_tool_requirements(
            (
                check_contract.ToolRequirement(
                    "a", (alt_a,), when="registry_baseline_rule"
                ),
                check_contract.ToolRequirement("b", (alt_b,), after=("a",)),
            )
        )
