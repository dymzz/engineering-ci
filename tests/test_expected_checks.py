"""#66-B Expected Check projection and Plan validation."""

import dataclasses
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from engineering_ci import check_contract, expected_checks, plan_contract, selection

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/selection-app"


def run(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


@pytest.fixture
def repo(tmp_path):
    target = tmp_path / "repo"
    shutil.copytree(FIXTURE, target)
    run(target, "init", "-q", "-b", "main")
    run(target, "config", "user.email", "expected@example.invalid")
    run(target, "config", "user.name", "Expected Test")
    run(target, "add", "-A")
    run(target, "commit", "-q", "-m", "base")
    return target, run(target, "rev-parse", "HEAD").strip()


def make_plan(repo):
    path, base = repo
    config = selection.load_config(path / "ci-selection.toml")
    changes = selection.changeset(path, base=base, mode="worktree")
    chosen = selection.select(changes, config, root=path)
    return selection.plan(
        chosen,
        config=config,
        config_path=path / "ci-selection.toml",
        root=path,
    )


def test_plan_contains_partial_expected_checks(repo):
    plan = make_plan(repo)
    expected = plan["expected_checks"]
    assert expected["schema_version"] == 1
    assert expected["status"] == "partial"
    assert expected["consumer_completeness"] == "not_claimed"
    assert expected["definitions_status"] in {"partial", "requested_names_complete"}
    assert expected["checks"]
    assert all("job_refs" not in item for item in expected["checks"])
    assert all(
        item["source_ref"]["snapshot"] == plan["git"]["snapshot"]
        for item in expected["checks"]
    )
    assert plan_contract.validate(plan) is plan


def test_expected_set_ref_changes_with_scope_or_source(repo):
    plan = make_plan(repo)
    expected = plan["expected_checks"]
    changed = json.loads(json.dumps(expected))
    changed["checks"][0]["applicability"]["status"] = "not_applicable"
    assert expected_checks._digest(changed["checks"]) != expected["expected_set_ref"]


def test_expected_public_api_cannot_forge_complete(repo):
    path, base = repo
    changes = selection.changeset(path, base=base, mode="worktree")
    result = expected_checks.derive_expected_checks(
        changes,
        [{"name": "not-a-check", "status": "selected", "inputs": [], "reason": "x"}],
        config_path=path / "ci-selection.toml",
        profile_ref={"name": "forged", "version": "1"},
        profile_source="forged",
    )
    assert result["status"] == "partial"
    assert result["definitions_status"] == "partial"
    assert result["consumer_completeness"] == "not_claimed"
    empty = expected_checks.derive_expected_checks(
        changes,
        [],
        config_path=path / "ci-selection.toml",
        profile_ref={"name": "forged", "version": "1"},
        profile_source="forged",
    )
    assert empty["status"] == "unknown"
    assert empty["definitions_status"] == "unknown"


def refresh_expected(expected):
    for entry in expected["checks"]:
        entry["expected_check_ref"] = expected_checks.entry_identity(entry)
    expected["expected_set_ref"] = expected_checks._digest(expected["checks"])
    expected["scope_id"] = expected_checks.scope_identity(
        expected.get("consumer"),
        expected.get("profile_ref"),
        expected.get("profile_source"),
        expected.get("event_ref"),
        expected.get("source_ref"),
        expected.get("selection_rule_ref"),
    )


def test_expected_complete_requires_evidence(repo):
    plan = make_plan(repo)
    plan["expected_checks"]["status"] = "complete"
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(plan)
    assert raised.value.code == "expected_complete_unproven"
    plan = make_plan(repo)
    plan["expected_checks"]["consumer_completeness"] = "complete"
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(plan)
    assert raised.value.code == "expected_consumer_completeness_unproven"


def test_expected_entry_must_match_parent_scope(repo):
    cases = (
        ("event_ref", "head_ref", "refs/heads/other", "expected_entry_event_mismatch"),
        (
            "profile_ref",
            None,
            {"name": "other", "version": "1"},
            "expected_entry_profile_mismatch",
        ),
    )
    for field, key, value, code in cases:
        plan = make_plan(repo)
        entry = plan["expected_checks"]["checks"][0]
        if key is None:
            entry[field] = value
        else:
            entry[field][key] = value
        refresh_expected(plan["expected_checks"])
        with pytest.raises(plan_contract.PlanError) as raised:
            plan_contract.validate(plan)
        assert raised.value.code == code
    plan = make_plan(repo)
    entry = plan["expected_checks"]["checks"][0]
    entry["source_ref"]["snapshot"] = "commit"
    refresh_expected(plan["expected_checks"])
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(plan)
    assert raised.value.code == "expected_entry_source_mismatch"


def test_expected_index_target_is_not_head_tree(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 42\n", encoding="utf-8")
    run(path, "add", "src/pkg/sub.py")
    config = selection.load_config(path / "ci-selection.toml")
    changes = selection.changeset(path, base=base, mode="index")
    chosen = selection.select(changes, config, root=path)
    plan = selection.plan(
        chosen, config=config, config_path=path / "ci-selection.toml", root=path
    )
    expected_checks.validate_projection(
        plan["expected_checks"], plan["git"], plan["check_scope"]
    )
    tampered = json.loads(json.dumps(plan["expected_checks"]))
    tampered["source_ref"]["declared_target_tree"] = plan["git"]["tree"]
    for entry in tampered["checks"]:
        entry["source_ref"]["declared_target_tree"] = plan["git"]["tree"]
    refresh_expected(tampered)
    with pytest.raises(expected_checks.ExpectedError) as raised:
        expected_checks.validate_projection(tampered, plan["git"], plan["check_scope"])
    assert raised.value.code == "expected_source_mismatch"


def test_expected_worktree_content_binding_changes_identity(repo):
    path, _ = repo
    (path / "docs/guide.md").write_text("# one\n", encoding="utf-8")
    first = make_plan(repo)
    (path / "docs/guide.md").write_text("# two\n", encoding="utf-8")
    second = make_plan(repo)
    assert first["expected_checks"]["source_ref"]["content_digest"]
    assert (
        first["expected_checks"]["expected_set_ref"]
        != second["expected_checks"]["expected_set_ref"]
    )
    tampered = json.loads(json.dumps(first))
    tampered["git"]["content_binding"]["digest"] = "0" * 64
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(tampered)
    assert raised.value.code == "expected_content_binding_mismatch"


def test_expected_multi_ref_must_match_plan_references(repo):
    path, base = repo
    changes = selection.changeset(path, base=base, mode="worktree")
    refs = [
        {"before": "a", "after": "b", "paths": ["a.py"], "complete": True},
        {"before": "c", "after": "d", "paths": ["b.py"], "complete": True},
    ]
    changes = dataclasses.replace(changes, references=tuple(refs))
    expected = expected_checks.derive_expected_checks(
        changes,
        [{"name": "docs", "status": "selected", "inputs": []}],
        config_path=path / "ci-selection.toml",
    )
    git = {
        "snapshot": changes.snapshot,
        "tree": changes.tree,
        "references": refs,
        "base_ref": changes.base_ref,
        "head_ref": changes.head_ref,
        "base_commit": changes.base_commit,
        "head_commit": changes.head_commit,
    }
    expected_checks.validate_projection(
        expected, git, [{"name": "docs", "status": "selected", "inputs": []}]
    )
    tampered = json.loads(json.dumps(expected))
    tampered["event_ref"]["references"][1]["after"] = "e"
    for entry in tampered["checks"]:
        entry["event_ref"]["references"][1]["after"] = "e"
    refresh_expected(tampered)
    with pytest.raises(expected_checks.ExpectedError) as raised:
        expected_checks.validate_projection(
            tampered, git, [{"name": "docs", "status": "selected", "inputs": []}]
        )
    assert raised.value.code == "expected_event_mismatch"


def test_expected_definition_must_resolve(repo):
    plan = make_plan(repo)
    entry = plan["expected_checks"]["checks"][0]
    entry["check_id"] = "eci/hygiene/nonexistent"
    entry["definition_version"] = 999
    entry["definition_status"] = "resolved"
    refresh_expected(plan["expected_checks"])
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(plan)
    assert raised.value.code == "expected_definition_mismatch"


def test_expected_unknown_missing_must_agree(repo):
    plan = make_plan(repo)
    plan["expected_checks"]["missing"] = ["not-a-check"]
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(plan)
    assert raised.value.code == "expected_status_inconsistent"


def test_expected_scope_binds_selection_rule(repo, tmp_path):
    path, base = repo
    changes = selection.changeset(path, base=base, mode="worktree")
    scope = [{"name": "docs", "status": "selected", "inputs": []}]
    first = expected_checks.derive_expected_checks(
        changes, scope, config_path=path / "ci-selection.toml"
    )
    other = tmp_path / "other.toml"
    other.write_bytes((path / "ci-selection.toml").read_bytes() + b"\n")
    second = expected_checks.derive_expected_checks(changes, scope, config_path=other)
    assert first["selection_rule_ref"] != second["selection_rule_ref"]
    assert first["scope_id"] != second["scope_id"]
    assert first["source_ref"]["actual_execution_source"] == "unknown"


def test_expected_projection_source_is_check_scope(repo):
    plan = make_plan(repo)
    plan["expected_checks"]["projection_source"] = "profile"
    refresh_expected(plan["expected_checks"])
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(plan)
    assert raised.value.code == "expected_projection_source_unsupported"

    plan = make_plan(repo)
    plan.pop("check_scope")
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(plan)
    assert raised.value.code == "expected_check_scope_missing"

    plan = make_plan(repo)
    plan["expected_checks"]["checks"].pop()
    refresh_expected(plan["expected_checks"])
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(plan)
    assert raised.value.code == "expected_scope_mismatch"


def test_expected_selection_rule_matches_generator(repo):
    plan = make_plan(repo)
    plan["expected_checks"]["selection_rule_ref"]["sha256"] = "sha256:" + "0" * 64
    refresh_expected(plan["expected_checks"])
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(plan)
    assert raised.value.code == "expected_selection_rule_mismatch"


def test_expected_scope_matches_plan_scope(repo):
    plan = make_plan(repo)
    expected = plan["expected_checks"]
    expected["source_ref"]["verified_input_scope"] = ["other.py"]
    for entry in expected["checks"]:
        entry["source_ref"]["verified_input_scope"] = ["other.py"]
    refresh_expected(expected)
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(plan)
    assert raised.value.code == "expected_source_scope_mismatch"

    plan = make_plan(repo)
    entry = plan["expected_checks"]["checks"][0]
    entry["applicability"]["status"] = "not_applicable"
    refresh_expected(plan["expected_checks"])
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(plan)
    assert raised.value.code == "expected_applicability_mismatch"


def test_expected_baseline_matches_plan(repo):
    plan = make_plan(repo)
    entry = plan["expected_checks"]["checks"][0]
    entry["baseline_ref"]["base_commit"] = "0" * 40
    refresh_expected(plan["expected_checks"])
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(plan)
    assert raised.value.code == "expected_baseline_mismatch"


def test_expected_definition_history_and_unknown(repo):
    assert check_contract.definition_for("docs", 1) is not None
    assert check_contract.definition_for("docs", 999) is None
    plan = make_plan(repo)
    entry = next(
        item
        for item in plan["expected_checks"]["checks"]
        if item["definition_status"] == "resolved"
    )
    entry["definition_version"] = 999
    refresh_expected(plan["expected_checks"])
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(plan)
    assert raised.value.code == "expected_definition_mismatch"


def test_expected_source_and_rule_match_check_contract(repo):
    plan = make_plan(repo)
    entry = plan["expected_checks"]["checks"][0]
    check_contract.validate_source_ref(entry["source_ref"])
    assert entry["rule_ref"] is None
    assert entry["rule_status"] == "unknown"
    assert entry["selection_rule_ref"]["sha256"].startswith("sha256:")
    assert entry["event_ref"]["references"] == []


def test_expected_identity_binds_event_not_local_paths(repo, tmp_path):
    path, base = repo
    changes = selection.changeset(path, base=base, mode="worktree")
    scope = [{"name": "docs", "status": "selected", "inputs": ["docs/a.md"]}]
    first = expected_checks.derive_expected_checks(
        changes, scope, config_path=path / "ci-selection.toml"
    )
    copy = tmp_path / "copy.toml"
    copy.write_bytes((path / "ci-selection.toml").read_bytes())
    second = expected_checks.derive_expected_checks(changes, scope, config_path=copy)
    assert first["expected_set_ref"] == second["expected_set_ref"]
    assert (
        first["checks"][0]["expected_check_ref"]
        == (second["checks"][0]["expected_check_ref"])
    )
    other_ref = dataclasses.replace(changes, head_ref="refs/heads/other")
    third = expected_checks.derive_expected_checks(
        other_ref, scope, config_path=path / "ci-selection.toml"
    )
    assert (
        third["checks"][0]["expected_check_ref"]
        != (first["checks"][0]["expected_check_ref"])
    )


def test_expected_identity_tampering_is_rejected(repo):
    plan = make_plan(repo)
    for mutate, code in (
        (
            lambda item: item["applicability"].__setitem__("status", "x"),
            "expected_check_ref_mismatch",
        ),
        (
            lambda item: item.__setitem__("rule_ref", {"name": "x"}),
            "expected_check_ref_mismatch",
        ),
        (
            lambda item: item["input_refs"].append({"path": "x"}),
            "expected_check_ref_mismatch",
        ),
    ):
        tampered = json.loads(json.dumps(plan))
        mutate(tampered["expected_checks"]["checks"][0])
        with pytest.raises(plan_contract.PlanError) as raised:
            plan_contract.validate(tampered)
        assert raised.value.code == code
    tampered = json.loads(json.dumps(plan))
    tampered["expected_checks"]["expected_set_ref"] = "sha256:" + "0" * 64
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(tampered)
    assert raised.value.code == "expected_set_ref_mismatch"
    tampered = json.loads(json.dumps(plan))
    tampered["expected_checks"]["event_ref"]["head_ref"] = "refs/heads/other"
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(tampered)
    assert raised.value.code == "expected_event_mismatch"
    tampered = json.loads(json.dumps(plan))
    tampered["expected_checks"]["source_ref"]["declared_target_tree"] = "0" * 40
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(tampered)
    assert raised.value.code == "expected_source_mismatch"


def test_unknown_check_is_partial_and_no_legal_empty(repo):
    path, base = repo
    changes = selection.changeset(path, base=base, mode="worktree")
    result = expected_checks.derive_expected_checks(
        changes,
        [
            {"name": "not-a-check", "status": "selected", "inputs": [], "reason": "x"},
        ],
        config_path=path / "ci-selection.toml",
    )
    assert result["definitions_status"] == "partial"
    assert result["status"] == "partial"
    assert result["missing"] == ["not-a-check"]
    empty = expected_checks.derive_expected_checks(
        changes, [], config_path=path / "ci-selection.toml"
    )
    assert empty["status"] == "unknown"
    assert empty["reason"] == "no_expected_checks"
    assert empty["checks"] == []
    assert empty["definitions_status"] == "unknown"


def test_expected_plan_rejects_routing_and_duplicate(repo):
    plan = make_plan(repo)
    plan["expected_checks"]["checks"][0]["job_refs"] = ["job-1"]
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(plan)
    assert raised.value.code == "expected_routing_forbidden"

    plan = make_plan(repo)
    plan["expected_checks"]["checks"].append(
        json.loads(json.dumps(plan["expected_checks"]["checks"][0]))
    )
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(plan)
    assert raised.value.code == "expected_duplicate"


def test_expected_source_must_match_plan_git(repo):
    plan = make_plan(repo)
    plan["expected_checks"]["source_ref"]["snapshot"] = "commit"
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(plan)
    assert raised.value.code == "expected_source_mismatch"


def test_expected_requires_plan_v2_and_valid_schema(repo):
    plan = make_plan(repo)
    plan["schema_version"] = 1
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(plan)
    assert raised.value.code == "expected_checks_requires_plan_v2"

    plan = make_plan(repo)
    plan["expected_checks"]["schema_version"] = 2
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(plan)
    assert raised.value.code == "expected_checks_schema_unsupported"


def _sample_effective_profile(
    *,
    context="worktree",
    action="worktree",
    target="HEAD",
    targets=None,
    effective_at="2026-11-15T00:00:00Z",
    evaluated_at="2026-11-15T01:00:00Z",
    valid_from=None,
    valid_until=None,
    approval_source="governance-signed",
    authority_verified=True,
    required_obligations=None,
    advisory_checks=None,
    allowed_inapplicability=None,
    declared_checks=None,
    domain_complete=True,
):
    return expected_checks.build_effective_profile(
        profile_ref={"name": "consumer.pr", "version": "1.0.0"},
        profile_revision="rev-2026-11-15",
        approval_source=approval_source,
        authority_verified=authority_verified,
        effective_at=effective_at,
        evaluated_at=evaluated_at,
        valid_from=valid_from,
        valid_until=valid_until,
        context=context,
        action=action,
        target=target,
        targets=targets,
        required_obligations=required_obligations or [],
        advisory_checks=advisory_checks or [],
        allowed_inapplicability=allowed_inapplicability or [],
        declared_checks=declared_checks,
        domain_complete=domain_complete,
    )


def test_multi_ref_per_ref_identities_distinguish_baselines_on_same_tree(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 11\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "branch-a")
    branch_a = run(path, "rev-parse", "HEAD").strip()

    run(path, "checkout", "-q", base)
    (path / "docs/note.md").write_text("# note\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "shared-target")
    shared = run(path, "rev-parse", "HEAD").strip()

    multi = selection.changeset_multi(
        path,
        [
            ("refs/heads/a", shared, "refs/heads/a", base),
            ("refs/heads/b", shared, "refs/heads/b", branch_a),
        ],
    )
    sel = selection.select(
        multi, selection.load_config(path / "ci-selection.toml"), root=path
    )
    p = selection.plan(
        sel,
        config=selection.load_config(path / "ci-selection.toml"),
        config_path=path / "ci-selection.toml",
        root=path,
    )
    plan_contract.validate(p)
    per_ref = p["expected_checks"]["per_ref"]
    assert len(per_ref) == 2
    assert per_ref[0]["baseline_ref"] != per_ref[1]["baseline_ref"]
    assert per_ref[0]["expected_set_ref"] != per_ref[1]["expected_set_ref"]
    assert per_ref[0]["scope_id"] != per_ref[1]["scope_id"]
    assert (
        per_ref[0]["checks"][0]["expected_check_ref"]
        != per_ref[1]["checks"][0]["expected_check_ref"]
    )

    # Tampering a per-ref baseline is rejected rather than bypassed
    tampered = json.loads(json.dumps(p))
    tampered["expected_checks"]["per_ref"][1]["checks"][0]["baseline_ref"][
        "base_commit"
    ] = base
    tampered["expected_checks"]["per_ref"][1]["checks"][0]["expected_check_ref"] = (
        expected_checks.entry_identity(
            tampered["expected_checks"]["per_ref"][1]["checks"][0]
        )
    )
    tampered["expected_checks"]["per_ref"][1]["expected_set_ref"] = (
        expected_checks._digest(tampered["expected_checks"]["per_ref"][1]["checks"])
    )
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(tampered)
    assert raised.value.code == "expected_baseline_mismatch"


def test_schema_v2_effective_profile_many_to_many_and_completeness(repo):
    path, base = repo
    (path / "docs/guide.md").write_text("# docs change\n", encoding="utf-8")
    changes = selection.changeset(path, base=base, mode="worktree")
    cfg = selection.load_config(path / "ci-selection.toml")
    sel = selection.select(changes, cfg, root=path)
    profile = _sample_effective_profile(
        declared_checks=[
            "whitespace",
            "forwarding",
            "docs",
            "vulture",
            "tach",
        ],
        required_obligations=[
            {
                "obligation_ref": "ob/architecture",
                "kind": "check",
                "check_names": ["tach", "docs"],
                "allow_inapplicable": True,
            },
            {
                "obligation_ref": "ob/security-boundary",
                "kind": "check",
                "check_names": ["tach"],
                "allow_inapplicable": True,
            },
            {
                "obligation_ref": "ob/release-evidence",
                "kind": "non_check",
                "reason": "terminal telemetry required for release",
            },
        ],
        advisory_checks=[{"name": "tach", "obligation_ref": "ob/advisory-arch"}],
    )
    p = selection.plan(
        sel,
        config=cfg,
        config_path=path / "ci-selection.toml",
        root=path,
        effective_profile=profile,
    )
    plan_contract.validate(p)
    exp = p["expected_checks"]
    assert exp["schema_version"] == 2
    assert exp["projection_source"] == "effective_profile"
    assert exp["status"] == "complete"
    assert exp["definitions_status"] == "domain_complete"
    assert exp["consumer_completeness"] == "expected_domain_complete"
    assert exp["observed_input_closure_status"] == "not_observed"
    assert len(exp["non_check_obligations"]) == 1
    assert exp["non_check_obligations"][0]["obligation_ref"] == "ob/release-evidence"
    by_name = {item["name"]: item for item in exp["checks"]}
    assert set(by_name["tach"]["required_by_obligations"]) == {
        "ob/architecture",
        "ob/security-boundary",
    }
    assert by_name["tach"]["advisory_for_obligations"] == ["ob/advisory-arch"]
    assert len(by_name["tach"]["obligation_refs"]) == 3


def test_schema_v2_hygiene_conflict_and_legitimate_empty_delete(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 8\n", encoding="utf-8")
    changes = selection.changeset(path, base=base, mode="worktree")
    cfg = selection.load_config(path / "ci-selection.toml")
    sel = selection.select(changes, cfg, root=path)
    profile = _sample_effective_profile(
        declared_checks=[],
        required_obligations=[
            {
                "obligation_ref": "ob/ext-deps",
                "kind": "check",
                "check_names": ["tach_external"],
            }
        ],
    )
    # Local ci-hygiene.toml has tach.external = false; must record conflict without silent drop
    p = selection.plan(
        sel,
        config=cfg,
        config_path=path / "ci-selection.toml",
        root=path,
        effective_profile=profile,
    )
    plan_contract.validate(p)
    exp = p["expected_checks"]
    assert exp["status"] == "partial"
    assert exp["consumer_completeness"] == "incomplete_conflict"
    assert exp["conflicts"][0]["kind"] == "hygiene_profile_conflict"
    assert any(item["name"] == "tach_external" for item in exp["checks"])

    # Deletion with non-check obligation only produces legitimate complete empty check set
    del_changes = selection.changeset(
        path,
        base=base,
        mode="push",
        before=base,
        after=selection.ZERO_OID,
        remote_ref="refs/heads/topic",
    )
    del_sel = selection.select(del_changes, cfg, root=path)
    del_profile = _sample_effective_profile(
        context="push",
        action="delete",
        target="refs/heads/topic",
        declared_checks=[],
    )
    del_plan = selection.plan(
        del_sel,
        config=cfg,
        config_path=path / "ci-selection.toml",
        root=path,
        effective_profile=del_profile,
    )
    plan_contract.validate(del_plan)
    del_exp = del_plan["expected_checks"]
    assert del_exp["checks"] == []
    assert del_exp["status"] == "complete"
    assert del_exp["reason"] == "domain_complete_empty"
    assert any(ob["kind"] == "ref_deletion" for ob in del_exp["non_check_obligations"])


def test_schema_v2_rejects_untrusted_wrong_time_wrong_event_and_unresolved_rules(
    repo,
):
    path, base = repo
    (path / "docs/guide.md").write_text("# docs\n", encoding="utf-8")
    changes = selection.changeset(path, base=base, mode="worktree")
    cfg = selection.load_config(path / "ci-selection.toml")
    sel = selection.select(changes, cfg, root=path)

    # 1. Untrusted approval_source / missing verification / forged digest rejected
    untrusted = _sample_effective_profile(
        approval_source="local-override", declared_checks=["docs"]
    )
    with pytest.raises(expected_checks.ExpectedError) as raised:
        selection.plan(
            sel,
            config=cfg,
            config_path=path / "ci-selection.toml",
            root=path,
            effective_profile=untrusted,
        )
    assert raised.value.code == "effective_profile_authority_untrusted"

    forged = _sample_effective_profile(declared_checks=["docs"])
    forged["required_obligations"] = [
        {"obligation_ref": "ob/tamper", "kind": "non_check"}
    ]
    with pytest.raises(expected_checks.ExpectedError) as raised:
        selection.plan(
            sel,
            config=cfg,
            config_path=path / "ci-selection.toml",
            root=path,
            effective_profile=forged,
        )
    assert raised.value.code == "effective_profile_digest_mismatch"

    # 2. Expired or not-yet-effective profile rejected
    expired = _sample_effective_profile(
        declared_checks=["docs"],
        effective_at="2026-11-10T00:00:00Z",
        valid_until="2026-11-12T00:00:00Z",
        evaluated_at="2026-11-15T00:00:00Z",
    )
    with pytest.raises(expected_checks.ExpectedError) as raised:
        selection.plan(
            sel,
            config=cfg,
            config_path=path / "ci-selection.toml",
            root=path,
            effective_profile=expired,
        )
    assert raised.value.code == "effective_profile_not_active"

    # 3. Context x Action x Target mismatch rejected
    wrong_event = _sample_effective_profile(
        context="push",
        action="update",
        target="refs/heads/main",
        declared_checks=["docs"],
    )
    with pytest.raises(expected_checks.ExpectedError) as raised:
        selection.plan(
            sel,
            config=cfg,
            config_path=path / "ci-selection.toml",
            root=path,
            effective_profile=wrong_event,
        )
    assert raised.value.code == "effective_profile_event_mismatch"

    # 4. Unresolved rule_status prevents complete even when domain_complete=True
    (path / "src/pkg/sub.py").write_text("VALUE = 9\n", encoding="utf-8")
    code_changes = selection.changeset(path, base=base, mode="worktree")
    code_sel = selection.select(code_changes, cfg, root=path)
    unresolved_rule_profile = _sample_effective_profile(
        declared_checks=["unittest", "tach"]
    )
    partial_plan = selection.plan(
        code_sel,
        config=cfg,
        config_path=path / "ci-selection.toml",
        root=path,
        effective_profile=unresolved_rule_profile,
    )
    plan_contract.validate(partial_plan)
    assert partial_plan["expected_checks"]["status"] == "partial"
    assert partial_plan["expected_checks"]["reason"] == "check_rule_unresolved"

    # Claiming complete with unresolved rule_status fails validation
    forged_complete = json.loads(json.dumps(partial_plan))
    forged_complete["expected_checks"]["status"] = "complete"
    forged_complete["expected_checks"]["consumer_completeness"] = (
        "expected_domain_complete"
    )
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(forged_complete)
    assert raised.value.code == "expected_complete_unproven"


def test_schema_v2_protects_non_check_obligations_and_detects_rule_conflicts(repo):
    path, base = repo
    (path / "docs/guide.md").write_text("# docs\n", encoding="utf-8")
    changes = selection.changeset(path, base=base, mode="worktree")
    cfg = selection.load_config(path / "ci-selection.toml")
    sel = selection.select(changes, cfg, root=path)

    profile = _sample_effective_profile(
        declared_checks=["docs", "tach"],
        required_obligations=[
            {
                "obligation_ref": "ob/release-terminal",
                "kind": "non_check",
                "reason": "terminal telemetry required",
            },
            {
                "obligation_ref": "ob/docs-check",
                "kind": "check",
                "check_names": ["docs"],
            },
        ],
    )
    p = selection.plan(
        sel,
        config=cfg,
        config_path=path / "ci-selection.toml",
        root=path,
        effective_profile=profile,
    )
    plan_contract.validate(p)

    # Deleting non_check_obligations is caught even if caller did NOT update set_ref, AND if they did
    dropped_ob = json.loads(json.dumps(p))
    dropped_ob["expected_checks"]["non_check_obligations"] = []
    dropped_ob["expected_checks"]["expected_set_ref"] = expected_checks.set_identity(
        dropped_ob["expected_checks"]["checks"], [], schema_version=2
    )
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(dropped_ob)
    assert raised.value.code == "expected_non_check_obligations_mismatch"

    # Tampering M:N obligation_refs on a check entry is rejected even after recomputing digests
    tampered_m2n = json.loads(json.dumps(p))
    tampered_m2n["expected_checks"]["checks"][0]["required_by_obligations"] = []
    tampered_m2n["expected_checks"]["checks"][0]["expected_check_ref"] = (
        expected_checks.entry_identity(tampered_m2n["expected_checks"]["checks"][0])
    )
    tampered_m2n["expected_checks"]["expected_set_ref"] = expected_checks.set_identity(
        tampered_m2n["expected_checks"]["checks"],
        tampered_m2n["expected_checks"]["non_check_obligations"],
        schema_version=2,
    )
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(tampered_m2n)
    assert raised.value.code == "expected_obligation_mapping_mismatch"

    # Two required obligations binding same check to conflicting rules record conflict, not overwrite
    conflict_profile = _sample_effective_profile(
        declared_checks=["tach"],
        required_obligations=[
            {
                "obligation_ref": "ob/rule-a",
                "kind": "check",
                "check_names": ["tach"],
                "rule_ref": {"name": "tach", "sha256": "sha256:" + "1" * 64},
            },
            {
                "obligation_ref": "ob/rule-b",
                "kind": "check",
                "check_names": ["tach"],
                "rule_ref": {"name": "tach", "sha256": "sha256:" + "2" * 64},
            },
        ],
    )
    conflict_plan = selection.plan(
        sel,
        config=cfg,
        config_path=path / "ci-selection.toml",
        root=path,
        effective_profile=conflict_profile,
    )
    plan_contract.validate(conflict_plan)
    cexp = conflict_plan["expected_checks"]
    assert cexp["status"] == "partial"
    assert cexp["consumer_completeness"] == "incomplete_conflict"
    assert any(
        c["kind"] == "conflicting_rule_requirements" and c["check"] == "tach"
        for c in cexp["conflicts"]
    )
    tach_entry = next(item for item in cexp["checks"] if item["name"] == "tach")
    assert tach_entry["rule_ref"] is None
    assert tach_entry["rule_status"] == "conflict"
    assert tach_entry["policy_status"] == "conflict"


def test_multi_ref_per_ref_cross_ref_tampering_and_delete_protection(repo):
    path, base = repo
    (path / "docs/note.md").write_text("# note\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "update-docs")
    head = run(path, "rev-parse", "HEAD").strip()

    multi = selection.changeset_multi(
        path,
        [
            ("refs/heads/main", head, "refs/heads/main", base),
            ("refs/heads/old", selection.ZERO_OID, "refs/heads/old", head),
        ],
    )
    cfg = selection.load_config(path / "ci-selection.toml")
    sel = selection.select(multi, cfg, root=path)
    p = selection.plan(
        sel, config=cfg, config_path=path / "ci-selection.toml", root=path
    )
    plan_contract.validate(p)

    # 1. Tampering per_ref[0]["scope_id"] is caught
    bad_scope = json.loads(json.dumps(p))
    bad_scope["expected_checks"]["per_ref"][0]["scope_id"] = "sha256:" + "9" * 64
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(bad_scope)
    assert raised.value.code == "expected_per_ref_scope_id_mismatch"

    # 2. Tampering per_ref[0]["event_ref"] is caught
    bad_event = json.loads(json.dumps(p))
    bad_event["expected_checks"]["per_ref"][0]["event_ref"]["head_ref"] = (
        "refs/heads/other"
    )
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(bad_event)
    assert raised.value.code == "expected_per_ref_event_mismatch"

    # 3. Deleting ref_deletion non_check_obligation on deleted ref is caught even if set_ref recomputed
    bad_del = json.loads(json.dumps(p))
    bad_del["expected_checks"]["per_ref"][1]["non_check_obligations"] = []
    bad_del["expected_checks"]["per_ref"][1]["expected_set_ref"] = (
        expected_checks.set_identity(
            bad_del["expected_checks"]["per_ref"][1]["checks"], [], schema_version=1
        )
    )
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(bad_del)
    assert raised.value.code == "expected_per_ref_obligations_mismatch"

    # 4. Tampering per_ref[0] check applicability (selected -> not_applicable) and recomputing refs is caught
    bad_app = json.loads(json.dumps(p))
    ref0_docs = next(
        item
        for item in bad_app["expected_checks"]["per_ref"][0]["checks"]
        if item["name"] == "docs"
    )
    ref0_docs["applicability"] = {"status": "not_applicable", "reason": "no_changes"}
    ref0_docs["input_refs"] = []
    ref0_docs["expected_check_ref"] = expected_checks.entry_identity(ref0_docs)
    bad_app["expected_checks"]["per_ref"][0]["expected_set_ref"] = (
        expected_checks.set_identity(
            bad_app["expected_checks"]["per_ref"][0]["checks"],
            bad_app["expected_checks"]["per_ref"][0]["non_check_obligations"],
            schema_version=1,
        )
    )
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(bad_app)
    assert raised.value.code == "expected_per_ref_applicability_mismatch"

    # 5. Tampering per_ref[0]["status"] to complete is caught
    bad_status = json.loads(json.dumps(p))
    bad_status["expected_checks"]["per_ref"][0]["status"] = "complete"
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(bad_status)
    assert raised.value.code == "expected_per_ref_status_invalid"


def test_effective_profile_digest_bindings_declared_domain_and_obligation_waiver(
    repo, capsys
):
    path, base = repo
    (path / "docs/note.md").write_text("# note\n", encoding="utf-8")
    changes = selection.changeset(path, base=base, head="HEAD", mode="worktree")
    cfg = selection.load_config(path / "ci-selection.toml")
    sel = selection.select(changes, cfg, root=path)

    full_profile = _sample_effective_profile(
        declared_checks=["whitespace", "forwarding", "docs", "vulture", "tach"],
        required_obligations=[
            {
                "obligation_ref": "ob/docs",
                "kind": "check",
                "check_names": ["docs"],
            }
        ],
    )
    full_plan = selection.plan(
        sel,
        config=cfg,
        config_path=path / "ci-selection.toml",
        root=path,
        effective_profile=full_profile,
    )
    plan_contract.validate(full_plan)

    # P1-1a: Mutating domain_complete or evaluated_at invalidates profile_sha256
    for field, mutated in (
        ("domain_complete", False),
        ("evaluated_at", "2026-04-20T09:00:00Z"),
    ):
        tampered = json.loads(json.dumps(full_plan))
        tampered["expected_checks"]["effective_profile"][field] = mutated
        with pytest.raises(plan_contract.PlanError) as raised:
            plan_contract.validate(tampered)
        assert raised.value.code == "effective_profile_digest_mismatch"

    # P1-1b: Mutating required_obligations and recomputing profile_sha256 fails approval_binding,
    # and recomputing both approval_binding + profile_sha256 fails inputs.effective_profile binding
    rehashed = json.loads(json.dumps(full_plan))
    rehashed_prof = rehashed["expected_checks"]["effective_profile"]
    rehashed_prof["required_obligations"] = []
    rehashed_prof["profile_sha256"] = expected_checks.profile_digest(rehashed_prof)
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(rehashed)
    assert raised.value.code == "effective_profile_digest_mismatch"

    rebound = json.loads(json.dumps(full_plan))
    rebound_prof = rebound["expected_checks"]["effective_profile"]
    rebound_prof["evaluated_at"] = "2026-11-16T09:00:00Z"
    rebound_prof["profile_sha256"] = expected_checks.profile_digest(rebound_prof)
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(rebound)
    assert raised.value.code == "effective_profile_input_mismatch"

    # P1-1c: domain_complete=True with partial declared_checks=["docs"] cannot produce complete
    partial_domain_profile = _sample_effective_profile(
        declared_checks=["docs"],
        required_obligations=[
            {
                "obligation_ref": "ob/docs",
                "kind": "check",
                "check_names": ["docs"],
            }
        ],
        advisory_checks=[],
        domain_complete=True,
    )
    partial_domain_plan = selection.plan(
        sel,
        config=cfg,
        config_path=path / "ci-selection.toml",
        root=path,
        effective_profile=partial_domain_profile,
    )
    plan_contract.validate(partial_domain_plan)
    assert partial_domain_plan["expected_checks"]["status"] == "partial"
    assert (
        partial_domain_plan["expected_checks"]["reason"] == "declared_domain_incomplete"
    )
    assert (
        partial_domain_plan["expected_checks"]["consumer_completeness"]
        == "incomplete_profile"
    )
    forged_complete = json.loads(json.dumps(partial_domain_plan))
    forged_complete["expected_checks"]["status"] = "complete"
    forged_complete["expected_checks"]["consumer_completeness"] = (
        "expected_domain_complete"
    )
    forged_complete["expected_checks"]["definitions_status"] = "domain_complete"
    forged_complete["expected_checks"]["applicability_status"] = "domain_complete"
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(forged_complete)
    assert raised.value.code == "expected_complete_unproven"

    # P1-2: Deleting a check from declared_checks in expected_checks["checks"] and recomputing set_ref is rejected
    unmapped_declared_profile = _sample_effective_profile(
        declared_checks=["whitespace", "docs", "forwarding", "vulture", "tach"],
        required_obligations=[
            {
                "obligation_ref": "ob/ws",
                "kind": "check",
                "check_names": ["whitespace"],
            }
        ],
        advisory_checks=[],
    )
    unmapped_plan = selection.plan(
        sel,
        config=cfg,
        config_path=path / "ci-selection.toml",
        root=path,
        effective_profile=unmapped_declared_profile,
    )
    plan_contract.validate(unmapped_plan)
    dropped_docs = json.loads(json.dumps(unmapped_plan))
    dropped_docs["expected_checks"]["checks"] = [
        c for c in dropped_docs["expected_checks"]["checks"] if c["name"] != "docs"
    ]
    dropped_docs["expected_checks"]["expected_set_ref"] = expected_checks.set_identity(
        dropped_docs["expected_checks"]["checks"],
        dropped_docs["expected_checks"]["non_check_obligations"],
        schema_version=2,
    )
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(dropped_docs)
    assert raised.value.code == "expected_declared_check_missing"

    # P1-3: Obligation A (allow_inapplicable=True) does NOT waive Obligation B (allow_inapplicable=False) on same check
    shared_inapp_profile = _sample_effective_profile(
        declared_checks=["whitespace", "forwarding", "docs", "vulture", "tach"],
        required_obligations=[
            {
                "obligation_ref": "ob/waived",
                "kind": "check",
                "check_names": ["tach"],
                "allow_inapplicable": True,
            },
            {
                "obligation_ref": "ob/strict",
                "kind": "check",
                "check_names": ["tach"],
                "allow_inapplicable": False,
            },
        ],
        allowed_inapplicability=[],
    )
    shared_inapp_plan = selection.plan(
        sel,
        config=cfg,
        config_path=path / "ci-selection.toml",
        root=path,
        effective_profile=shared_inapp_profile,
    )
    plan_contract.validate(shared_inapp_plan)
    sexp = shared_inapp_plan["expected_checks"]
    assert sexp["status"] == "partial"
    assert sexp["consumer_completeness"] == "incomplete_conflict"
    inapp_conflicts = [
        c
        for c in sexp["conflicts"]
        if c["kind"] == "inapplicable_required_obligation" and c["check"] == "tach"
    ]
    assert len(inapp_conflicts) == 1
    assert inapp_conflicts[0]["obligation_refs"] == ["ob/strict"]
    tach_check = next(c for c in sexp["checks"] if c["name"] == "tach")
    assert tach_check["policy_status"] == "conflict"

    # Suppressing the inapplicable conflict on tach is rejected
    suppressed = json.loads(json.dumps(shared_inapp_plan))
    suppressed["expected_checks"]["conflicts"] = []
    sup_tach = next(
        c for c in suppressed["expected_checks"]["checks"] if c["name"] == "tach"
    )
    sup_tach["policy_status"] = "resolved"
    sup_tach["expected_check_ref"] = expected_checks.entry_identity(sup_tach)
    suppressed["expected_checks"]["expected_set_ref"] = expected_checks.set_identity(
        suppressed["expected_checks"]["checks"],
        suppressed["expected_checks"]["non_check_obligations"],
        schema_version=2,
    )
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(suppressed)
    assert raised.value.code == "expected_inapplicable_obligation_unrecorded"

    # CLI --effective-profile loads profile from JSON file and binds inputs.effective_profile
    profile_file = path.parent / "effective-profile.json"
    profile_file.write_text(json.dumps(full_profile), encoding="utf-8")
    exit_code = selection.main(
        [
            "--root",
            str(path),
            "--base",
            base,
            "--head",
            "HEAD",
            "--effective-profile",
            str(profile_file),
            "--json",
        ]
    )
    assert exit_code == 0
    cli_plan = json.loads(capsys.readouterr().out)
    plan_contract.validate(cli_plan)
    assert cli_plan["expected_checks"]["schema_version"] == 2
    assert cli_plan["expected_checks"]["status"] == "complete"
    assert (
        cli_plan["inputs"]["effective_profile"]["effective_profile_ref"]["sha256"]
        == full_profile["profile_sha256"]
    )
