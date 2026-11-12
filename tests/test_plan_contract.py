"""Shared Plan version/refusal boundary; no dependency scheduling is inferred."""

import copy
import hashlib
import json
import sys

import pytest

from engineering_ci import execution, plan_contract


def policy_plan():
    actions = {
        "probe": {
            "type": "command",
            "argv": [sys.executable, "-c", "raise SystemExit(0)"],
        }
    }
    return {
        "kind": plan_contract.PLAN_KIND,
        "schema_version": 2,
        "generator": {"config_sha256": "a" * 64},
        "git": {"snapshot": "worktree"},
        "selection": {"tests": [], "prerequisites": ["probe"]},
        "prerequisite_actions": actions,
        "dependency_contract": plan_contract.prerequisite_contract(
            ["probe"],
            actions,
            declaration={"path": "ci-selection.toml", "sha256": "a" * 64},
            selected_by="targeted",
        ),
    }


def invalid_cases():
    return [
        (lambda p: p.update(generator=[]), "dependency_binding_invalid"),
        (
            lambda p: p["prerequisite_actions"]["probe"].update(type="future"),
            "dependency_action_unsupported",
        ),
        (lambda p: p.update(schema_version=99), "plan_schema_unsupported"),
        (lambda p: p.update(schema_version=True), "plan_schema_unsupported"),
        (lambda p: p.update(schema_version=1), "dependency_contract_requires_plan_v2"),
        (lambda p: p.pop("dependency_contract"), "dependency_contract_missing"),
        (
            lambda p: p["dependency_contract"].update(schema_version=99),
            "dependency_schema_unsupported",
        ),
        (
            lambda p: p["dependency_contract"].update(
                required_semantics=["future_pass"]
            ),
            "dependency_semantics_unsupported",
        ),
        (
            lambda p: p["dependency_contract"]["edges"][0].update(
                dependency_kind="pre_execution_pass", requires_pass=True
            ),
            "dependency_kind_unsupported",
        ),
        (
            lambda p: p["dependency_contract"]["edges"][0].update(
                dependency_kind="data", output_ref="wheel"
            ),
            "dependency_kind_unsupported",
        ),
        (
            lambda p: p["dependency_contract"]["edges"][0].pop("producer_ref"),
            "dependency_producer_invalid",
        ),
        (
            lambda p: p["dependency_contract"]["edges"][0].pop("consumer_ref"),
            "dependency_consumer_unsupported",
        ),
        (
            lambda p: p["dependency_contract"]["edges"][0].pop("requires_pass"),
            "dependency_condition_unsupported",
        ),
        (
            lambda p: p["dependency_contract"]["edges"][0].pop("declaration_ref"),
            "dependency_binding_invalid",
        ),
        (
            lambda p: p["dependency_contract"].update(edges=[]),
            "dependency_inventory_mismatch",
        ),
        (
            lambda p: p["prerequisite_actions"]["probe"].update(argv=[]),
            "dependency_action_parameters_invalid",
        ),
    ]


def malformed_consumer_cases():
    # JSON scalar/container categories, including nonempty containers. Expected
    # reasons and zero consumer starts constrain more than just "doesn't crash".
    cases = []
    for bad in (None, False, 1, 1.5, [], {}, ["pytest"], {"type": "command"}):
        cases.extend(
            [
                (
                    lambda p, bad=bad: p["selection"].update(framework=bad),
                    "framework_invalid",
                ),
                (
                    lambda p, bad=bad: p["prerequisite_actions"]["probe"].update(
                        type=bad
                    ),
                    "dependency_action_unsupported",
                ),
            ]
        )
    for bad in (
        None,
        False,
        "checks",
        {},
        [[]],
        [None],
        [{}],
        [{"name": [], "status": "selected"}],
        [{"name": "probe", "status": {}}],
    ):
        for nested in (False, True):
            cases.append(
                (
                    lambda p, bad=bad, nested=nested: (
                        p["selection"] if nested else p
                    ).update(check_scope=bad),
                    "check_scope_invalid",
                )
            )
    cases.extend(
        [
            (lambda p: p["selection"].update(checks=[{}]), "checks_invalid"),
            (lambda p: p["selection"].update(src_root={}), "src_root_invalid"),
            (
                lambda p: p["selection"].update(tach_graph_validation=[{}]),
                "tach_graph_validation_invalid",
            ),
            (lambda p: p["git"].update(snapshot=[]), "git_invalid"),
            (lambda p: p["git"].update(changed_paths=[{}]), "git_invalid"),
            (lambda p: p["git"].update(content_binding=[{}]), "git_invalid"),
            (lambda p: p.update(inputs=[{}]), "inputs_invalid"),
            (
                lambda p: p.update(inputs={"hygiene": {"path": []}}),
                "inputs_invalid",
            ),
            (
                lambda p: p.update(post_completion={"weaver_config": [{}]}),
                "post_completion_invalid",
            ),
            (
                lambda p: p.update(post_completion={"registry": {"manifest": []}}),
                "post_completion_invalid",
            ),
        ]
    )
    return cases


@pytest.mark.parametrize("change,reason", invalid_cases() + malformed_consumer_cases())
def test_all_host_entry_points_refuse_before_a_consumer_starts(
    tmp_path, monkeypatch, capsys, change, reason
):
    value = policy_plan()
    change(value)
    _assert_refused_before_consumers(value, reason, tmp_path, monkeypatch, capsys)


def _assert_refused_before_consumers(value, reason, tmp_path, monkeypatch, capsys):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        execution,
        "_verify_execution_binding",
        lambda *a: calls.append("source verification"),
    )
    with pytest.raises(execution.ExecutionError, match=reason):
        execution.load_plan(path)
    report = execution.execute(
        value, root=tmp_path, runner=lambda *a: calls.append("command")
    )
    assert report["status"] == "not_executed" and report["reason"] == reason
    assert report["exit_code"] == 2 and report["checks"] == [] and calls == []
    assert report["prerequisites"] == []
    assert report["plan_declaration_consistent"] is False
    assert report["source_binding_verified"] == "unknown"
    with pytest.raises(SystemExit) as rejected:
        execution.main(["--plan", str(path), "--root", str(tmp_path), "--json"])
    assert rejected.value.code == 2
    assert reason in capsys.readouterr().err


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("case", ["root_scope", "selection_scope", "snapshot"])
def test_unknown_execution_semantics_refuse_even_with_test_targets(
    tmp_path, monkeypatch, capsys, version, case
):
    value = policy_plan()
    value["schema_version"] = version
    value["selection"]["tests"] = ["tests.test_pkg"]
    if version == 1:
        value.pop("dependency_contract")
    if case == "snapshot":
        value["git"] = {"snapshot": "future_snapshot"}
        reason = "git_snapshot_unsupported"
    else:
        container = value if case == "root_scope" else value["selection"]
        container["check_scope"] = [{"name": "source", "status": "future_required"}]
        reason = "check_scope_status_unsupported"
    _assert_refused_before_consumers(value, reason, tmp_path, monkeypatch, capsys)


@pytest.mark.parametrize(
    "status",
    [
        "selected",
        "unknown",
        "not_applicable",
        "targeted",
        "escalated",
        "no_tests",
        "no_changes",
    ],
)
@pytest.mark.parametrize("nested", [False, True])
def test_current_scope_statuses_remain_readable(tmp_path, status, nested):
    value = policy_plan()
    container = value["selection"] if nested else value
    container["check_scope"] = [{"name": "unittest", "status": status}]
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    assert execution.load_plan(path) == value


@pytest.mark.parametrize(
    "snapshot", ["worktree", "index", "commit", "push", "pr_merge", "multi"]
)
def test_current_snapshot_identities_remain_readable(tmp_path, snapshot):
    value = policy_plan()
    value["git"]["snapshot"] = snapshot
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    assert execution.load_plan(path) == value


@pytest.mark.parametrize("snapshot", ["future_snapshot", "", [], {}])
def test_binding_helper_itself_refuses_unknown_snapshot(
    tmp_path, monkeypatch, snapshot
):
    value = policy_plan()
    value["git"] = {"snapshot": snapshot}
    calls = []
    monkeypatch.setattr(execution.selection, "_git", lambda *a: calls.append("git"))
    assert execution._verify_execution_binding(value, tmp_path) == (
        False,
        "git_snapshot_unsupported",
        None,
    )
    assert calls == []


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("explicit_null", [False, True])
@pytest.mark.parametrize("tree", [None, "known-tree"])
def test_optional_legacy_snapshot_keeps_the_existing_binding_default(
    tmp_path, monkeypatch, version, explicit_null, tree
):
    value = policy_plan()
    value["schema_version"] = version
    value["git"] = {"execution_tree": tree}
    if version == 1:
        value.pop("dependency_contract")
    if explicit_null:
        value["git"]["snapshot"] = None
    execution.validate_plan(value)
    monkeypatch.setattr(execution.selection, "_tree_of", lambda *a: "known-tree")
    assert execution._verify_execution_binding(value, tmp_path) == (True, None, tree)


def test_supported_policy_executes_in_its_existing_post_check_phase(
    tmp_path, monkeypatch
):
    value = policy_plan()
    value["selection"]["tests"] = ["tests.controlled"]
    order = []
    monkeypatch.setattr(
        execution.selection, "run_tests", lambda *a, **k: order.append("tests") or 0
    )
    from subprocess import CompletedProcess

    report = execution.execute(
        value,
        root=tmp_path,
        runner=lambda *a: order.append("policy") or CompletedProcess(a[0], 0),
    )
    assert report["status"] == "passed" and order == ["tests", "policy"]
    assert report["dependency_contract"] == value["dependency_contract"]
    assert report["plan_declaration_consistent"] is True
    assert report["source_binding_verified"] == "unknown"


@pytest.mark.parametrize("bad", [[], {}, ["command"], {"type": "command"}])
def test_contract_producer_does_not_hash_a_non_string_action_type(bad):
    value = policy_plan()
    value["prerequisite_actions"]["probe"]["type"] = bad
    value["dependency_contract"] = plan_contract.prerequisite_contract(
        ["probe"],
        value["prerequisite_actions"],
        declaration={"path": "ci-selection.toml", "sha256": "a" * 64},
        selected_by="targeted",
    )
    assert value["dependency_contract"]["edges"][0]["resolution"] == "unresolved"
    with pytest.raises(execution.ExecutionError, match="dependency_action_unsupported"):
        execution.validate_plan(value)


@pytest.mark.parametrize("version", [1, 2])
def test_actual_config_drift_cannot_be_claimed_as_verified_source(tmp_path, version):
    from subprocess import CompletedProcess

    value = policy_plan()
    value["schema_version"] = version
    declared = hashlib.sha256(b"config A").hexdigest()
    value["generator"]["config_sha256"] = declared
    value["dependency_contract"]["edges"][0]["declaration_ref"]["sha256"] = declared
    if version == 1:
        value.pop("dependency_contract")
    (tmp_path / "ci-selection.toml").write_bytes(b"config B")
    assert (
        hashlib.sha256((tmp_path / "ci-selection.toml").read_bytes()).hexdigest()
        != declared
    )
    report = execution.execute(
        value, root=tmp_path, runner=lambda *a: CompletedProcess(a[0], 0)
    )
    # This slice reports declaration consistency only, not the #66 receipt or
    # actual-config verification required for B's strict source consumers.
    assert report["plan_declaration_consistent"] is (True if version == 2 else None)
    assert report["source_binding_verified"] == "unknown"


def test_legacy_plan_cannot_bypass_nested_scope_validation(tmp_path):
    value = policy_plan()
    value["schema_version"] = 1
    value.pop("dependency_contract")
    value["selection"]["check_scope"] = [{"name": "probe", "status": []}]
    report = execution.execute(value, root=tmp_path)
    assert (
        report["status"] == "not_executed" and report["reason"] == "check_scope_invalid"
    )
    assert report["checks"] == [] and report["prerequisites"] == []


def test_missing_action_stays_unresolved_and_blocks_the_final_report(tmp_path):
    value = policy_plan()
    value["prerequisite_actions"] = {}
    value["dependency_contract"] = plan_contract.prerequisite_contract(
        ["probe"],
        {},
        declaration={"path": "ci-selection.toml", "sha256": "a" * 64},
        selected_by="full",
    )
    report = execution.execute(value, root=tmp_path)
    assert report["status"] == "blocked" and report["exit_code"] == 5
    assert report["prerequisites"][0]["reason"] == "prerequisite_parameters_missing"


def test_versioned_actions_cannot_be_replaced_after_validation(tmp_path):
    value = policy_plan()
    alternate = copy.deepcopy(value["prerequisite_actions"])
    alternate["probe"]["argv"] = ["other"]
    report = execution.execute(value, root=tmp_path, prerequisite_actions=alternate)
    assert report["reason"] == "dependency_action_override_mismatch"
