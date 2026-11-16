"""#66-B Expected Check projection and Plan validation."""

import dataclasses
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from engineering_ci import (
    check_contract,
    execution,
    expected_checks,
    hygiene,
    plan_contract,
    selection,
    sources,
)

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


def test_profile_approval_and_selection_rule_cannot_cycle(repo):
    path, base = repo
    (path / "docs/cycle.md").write_text("# cycle\n", encoding="utf-8")
    changes = selection.changeset(path, base=base, mode="worktree")
    cfg = selection.load_config(path / "ci-selection.toml")
    sel = selection.select(changes, cfg, root=path)
    profile = _sample_effective_profile(
        declared_checks=["docs"],
        governed_rules=expected_checks.governed_domain_rules(
            path, config_path=path / "ci-selection.toml"
        ),
    )
    binding = profile["approval_binding"]
    declared = profile.get("declared_checks") or []
    config_path = path / "ci-selection.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + f"""

[effective_profile]
profile_ref = {{ name = "consumer.pr", version = "1.0.0" }}
profile_revision = "rev-2026-11-15"
approval_source = "governance-signed"
approval_digest = "{binding["approval_digest"]}"
effective_at = "2026-11-15T00:00:00Z"
declared_checks = {json.dumps(declared)}
""",
        encoding="utf-8",
    )
    with pytest.raises(expected_checks.ExpectedError) as raised:
        selection.plan(
            sel,
            config=selection.load_config(config_path),
            config_path=config_path,
            root=path,
            effective_profile=profile,
        )
    assert raised.value.code == "effective_profile_approval_rule_cycle"


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


def _profile_policy(profile):
    binding = profile["approval_binding"]
    return {
        "profile_ref": dict(profile["profile_ref"]),
        "profile_revision": profile["profile_revision"],
        "approval_source": binding["approval_source"],
        "approval_digest": binding["approval_digest"],
        "effective_at": profile["effective_at"],
        "declared_checks": list(profile.get("declared_checks") or []),
    }


def _attach_effective_profile_policy(path, profile):
    """Add an independent [effective_profile] approval binding for a profile."""
    profile.pop("governed_rules", None)
    declared_domain = (profile.get("approval_binding") or {}).get("declared_domain")
    if isinstance(declared_domain, dict):
        declared_domain.pop("governed_rules", None)
    binding = profile["approval_binding"]
    binding["approval_digest"] = expected_checks._approval_digest(profile)
    profile["profile_sha256"] = expected_checks.profile_digest(profile)
    declared = profile.get("declared_checks") or []
    candidate = Path(path)
    config_path = candidate if candidate.is_file() else candidate / "ci-selection.toml"
    existing = config_path.read_text(encoding="utf-8")
    existing = existing.split("\n[effective_profile]", 1)[0].rstrip()
    config_path.write_text(
        existing
        + f"""

[effective_profile]
profile_ref = {{ name = "{profile["profile_ref"]["name"]}", version = "{profile["profile_ref"]["version"]}" }}
profile_revision = "{profile["profile_revision"]}"
approval_source = "{binding["approval_source"]}"
approval_digest = "{binding["approval_digest"]}"
effective_at = "{profile["effective_at"]}"
declared_checks = {json.dumps(declared)}
""",
        encoding="utf-8",
    )
    return selection.load_config(config_path)


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
    governed_rules=None,
    phase=None,
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
        governed_rules=governed_rules,
        phase=phase,
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
        governed_rules=expected_checks.governed_domain_rules(
            path, config_path=path / "ci-selection.toml"
        ),
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
    cfg = _attach_effective_profile_policy(path, profile)
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
    cfg = _attach_effective_profile_policy(path, profile)
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
        governed_rules=expected_checks.governed_domain_rules(
            path, config_path=path / "ci-selection.toml"
        ),
    )
    del_cfg = _attach_effective_profile_policy(path, del_profile)
    del_plan = selection.plan(
        del_sel,
        config=del_cfg,
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
        declared_checks=["unittest", "tach"],
        governed_rules=expected_checks.governed_domain_rules(
            path, config_path=path / "ci-selection.toml"
        ),
    )
    rule_cfg = _attach_effective_profile_policy(path, unresolved_rule_profile)
    partial_plan = selection.plan(
        code_sel,
        config=rule_cfg,
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
        governed_rules=expected_checks.governed_domain_rules(
            path, config_path=path / "ci-selection.toml"
        ),
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
        governed_rules=expected_checks.governed_domain_rules(
            path, config_path=path / "ci-selection.toml"
        ),
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
    partial_cfg = _attach_effective_profile_policy(path, partial_domain_profile)
    partial_domain_plan = selection.plan(
        sel,
        config=partial_cfg,
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
    unmapped_cfg = _attach_effective_profile_policy(path, unmapped_declared_profile)
    unmapped_plan = selection.plan(
        sel,
        config=unmapped_cfg,
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
    shared_cfg = _attach_effective_profile_policy(path, shared_inapp_profile)
    shared_inapp_plan = selection.plan(
        sel,
        config=shared_cfg,
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
    _attach_effective_profile_policy(path, full_profile)
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
    assert cli_plan["expected_checks"]["authority_status"] == "verified"
    assert cli_plan["expected_checks"]["status"] == "partial"
    assert cli_plan["expected_checks"]["reason"] == "check_rule_unresolved"
    assert (
        cli_plan["inputs"]["effective_profile"]["effective_profile_ref"]["sha256"]
        == full_profile["profile_sha256"]
    )


def test_p1_1_authority_provenance_default_unverified_and_config_policy(repo):
    path, base = repo
    (path / "docs/note.md").write_text("# note\n", encoding="utf-8")
    changes = selection.changeset(path, base=base, head="HEAD", mode="worktree")
    cfg = selection.load_config(path / "ci-selection.toml")
    sel = selection.select(changes, cfg, root=path)

    # 1. build_effective_profile defaults authority_verified=False and even self-claimed
    # authority_verified=True without governed_rules / config policy stays partial/unverified.
    default_profile = expected_checks.build_effective_profile(
        profile_ref={"name": "consumer.pr", "version": "1.0.0"},
        profile_revision="rev-default",
        approval_source="governance-signed",
        effective_at="2026-11-15T00:00:00Z",
        evaluated_at="2026-11-15T01:00:00Z",
        context="worktree",
        action="worktree",
        target="HEAD",
        declared_checks=["whitespace", "forwarding", "docs", "vulture", "tach"],
        domain_complete=True,
    )
    assert default_profile["authority_verified"] is False
    assert "target_scope" in default_profile["approval_binding"]
    assert "effective_window" in default_profile["approval_binding"]
    assert "declared_domain" in default_profile["approval_binding"]

    self_claimed = _sample_effective_profile(
        authority_verified=True,
        declared_checks=["whitespace", "forwarding", "docs", "vulture", "tach"],
        domain_complete=True,
        governed_rules=None,
    )
    unproven_plan = selection.plan(
        sel,
        config=cfg,
        config_path=path / "ci-selection.toml",
        root=path,
        effective_profile=self_claimed,
    )
    plan_contract.validate(unproven_plan)
    uexp = unproven_plan["expected_checks"]
    assert uexp["status"] == "partial"
    assert uexp["reason"] == "effective_profile_authority_unverified"
    assert uexp["authority_status"] == "unverified"
    assert uexp["trusted_admission_status"] == "not_evaluated"
    assert uexp["consumer_completeness"] == "incomplete_profile"

    # Forging complete, verified authority, or admitted status fails contract validation.
    for mutate in (
        lambda p: p["expected_checks"].update(
            {"status": "complete", "consumer_completeness": "expected_domain_complete"}
        ),
        lambda p: p["expected_checks"].update({"authority_status": "verified"}),
        lambda p: p["expected_checks"].update({"trusted_admission_status": "admitted"}),
    ):
        forged = json.loads(json.dumps(unproven_plan))
        mutate(forged)
        with pytest.raises(plan_contract.PlanError):
            plan_contract.validate(forged)

    # 2. Mismatched governed_rules digest fails validation.
    bad_gov = expected_checks.governed_domain_rules(
        path, config_path=path / "ci-selection.toml"
    )
    bad_gov["hygiene_rule_ref"]["sha256"] = "0" * 64
    mismatch_profile = _sample_effective_profile(
        declared_checks=["whitespace", "forwarding", "docs", "vulture", "tach"],
        governed_rules=bad_gov,
    )
    with pytest.raises(expected_checks.ExpectedError) as raised:
        selection.plan(
            sel,
            config=cfg,
            config_path=path / "ci-selection.toml",
            root=path,
            effective_profile=mismatch_profile,
        )
    assert raised.value.code == "effective_profile_domain_binding_mismatch"

    # 3. Repository [effective_profile] policy in ci-selection.toml proves authority.
    declared_list = ["whitespace", "forwarding", "docs", "vulture", "tach"]
    policy_profile = _sample_effective_profile(
        declared_checks=declared_list,
        effective_at="2026-11-15T00:00:00Z",
        governed_rules=None,
    )
    policy_digest = policy_profile["approval_binding"]["approval_digest"]
    cfg_text = (path / "ci-selection.toml").read_text(encoding="utf-8")
    (path / "ci-selection.toml").write_text(
        cfg_text
        + f"""
[effective_profile]
profile_ref = {{ name = "consumer.pr", version = "1.0.0" }}
profile_revision = "rev-2026-11-15"
approval_source = "governance-signed"
approval_digest = "{policy_digest}"
effective_at = "2026-11-15T00:00:00Z"
declared_checks = {json.dumps(declared_list)}
""",
        encoding="utf-8",
    )
    cfg_with_policy = selection.load_config(path / "ci-selection.toml")
    proven_plan = selection.plan(
        sel,
        config=cfg_with_policy,
        config_path=path / "ci-selection.toml",
        root=path,
        effective_profile=policy_profile,
    )
    plan_contract.validate(proven_plan)
    pexp = proven_plan["expected_checks"]
    assert pexp["status"] == "complete"
    assert pexp["authority_status"] == "verified"
    assert pexp["trusted_admission_status"] == "not_evaluated"


def test_p2_1_cli_relative_effective_profile_outside_cwd(
    repo, tmp_path, monkeypatch, capsys
):
    path, base = repo
    (path / "docs/note.md").write_text("# note\n", encoding="utf-8")
    profile = _sample_effective_profile(
        governed_rules=expected_checks.governed_domain_rules(
            path, config_path=path / "ci-selection.toml"
        ),
        declared_checks=["whitespace", "forwarding", "docs", "vulture", "tach"],
    )
    profile_file = path.parent / "effective-profile.json"
    profile_file.write_text(json.dumps(profile), encoding="utf-8")
    outside_cwd = tmp_path / "nested" / "outside-cwd"
    outside_cwd.mkdir(parents=True)
    monkeypatch.chdir(outside_cwd)

    exit_code = selection.main(
        [
            "--root",
            str(path),
            "--base",
            base,
            "--head",
            "HEAD",
            "--effective-profile",
            "../effective-profile.json",
            "--json",
        ]
    )
    assert exit_code == 0
    cli_plan = json.loads(capsys.readouterr().out)
    plan_contract.validate(cli_plan)
    assert cli_plan["expected_checks"]["status"] == "partial"
    assert cli_plan["expected_checks"]["authority_status"] == "unverified"
    assert (
        cli_plan["expected_checks"]["reason"]
        == "effective_profile_authority_unverified"
    )
    assert cli_plan["inputs"]["effective_profile"]["path"] == str(
        profile_file.resolve()
    )
    assert (
        cli_plan["inputs"]["effective_profile"]["effective_profile_ref"]["sha256"]
        == profile["profile_sha256"]
    )


def test_p2_2_multi_ref_obligation_scoping_and_container_completeness(repo):
    path, base = repo
    profile = _sample_effective_profile(
        context="push",
        action="update",
        target="refs/heads/docs-only",
        targets=["refs/heads/docs-only", "refs/heads/code-branch"],
        declared_checks=["whitespace", "forwarding", "docs", "vulture", "tach"],
        required_obligations=[
            {
                "obligation_ref": "ob/docs-branch",
                "kind": "check",
                "check_names": ["docs"],
                "target_refs": ["refs/heads/docs-only"],
            },
            {
                "obligation_ref": "ob/code-unit",
                "kind": "check",
                "check_names": ["unittest"],
                "target_refs": ["refs/heads/code-branch"],
            },
            {
                "obligation_ref": "ob/release-main-only",
                "kind": "non_check",
                "reason": "only applies to refs/heads/main",
                "target_refs": ["refs/heads/main"],
            },
        ],
    )
    cfg = _attach_effective_profile_policy(path, profile)
    run(path, "add", "ci-selection.toml")
    run(path, "commit", "-q", "-m", "attach effective profile")
    base = run(path, "rev-parse", "HEAD").strip()

    (path / "docs/guide.md").write_text("# guide\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "docs-only")
    docs_commit = run(path, "rev-parse", "HEAD").strip()

    run(path, "checkout", "-q", base)
    (path / "src/pkg/sub.py").write_text("VALUE = 42\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "code-change")
    code_commit = run(path, "rev-parse", "HEAD").strip()

    multi = selection.changeset_multi(
        path,
        [
            ("refs/heads/docs-only", docs_commit, "refs/heads/docs-only", base),
            ("refs/heads/code-branch", code_commit, "refs/heads/code-branch", base),
        ],
    )
    sel = selection.select(multi, cfg, root=path)
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
    assert exp["consumer_completeness"] == "per_ref_container_only"
    assert exp["reason"] == "multi_ref_requires_per_ref_evaluation"
    assert exp["non_check_obligations"] == []
    by_name = {c["name"]: c for c in exp["checks"]}
    assert by_name["docs"]["required_by_obligations"] == ["ob/docs-branch"]
    assert by_name["unittest"]["required_by_obligations"] == ["ob/code-unit"]
    per_ref_docs = {
        c["name"]: c
        for c in next(
            e for e in exp["per_ref"] if e["remote_ref"] == "refs/heads/docs-only"
        )["checks"]
    }
    assert per_ref_docs["docs"]["required_by_obligations"] == ["ob/docs-branch"]
    assert "unittest" not in per_ref_docs
    per_ref_code = {
        c["name"]: c
        for c in next(
            e for e in exp["per_ref"] if e["remote_ref"] == "refs/heads/code-branch"
        )["checks"]
    }
    assert per_ref_code["docs"]["required_by_obligations"] == []
    assert per_ref_code["unittest"]["required_by_obligations"] == ["ob/code-unit"]

    # Forging top-level status=complete on multi-ref container is rejected.
    forged = json.loads(json.dumps(p))
    forged["expected_checks"]["status"] = "complete"
    forged["expected_checks"]["consumer_completeness"] = "expected_domain_complete"
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(forged)
    assert raised.value.code == "expected_complete_unproven"

    # Individual per_ref entries retain independent completeness:
    # refs/heads/docs-only has only docs/whitespace/forwarding (complete);
    # refs/heads/code-branch requires undeclared unittest (partial).
    per_ref = {entry["remote_ref"]: entry for entry in exp["per_ref"]}
    assert per_ref["refs/heads/docs-only"]["status"] == "complete"
    assert per_ref["refs/heads/code-branch"]["status"] == "partial"
    assert per_ref_code["unittest"]["rule_status"] == "unknown"


def test_expanded_definitions_resolve_without_forging_execution_or_required(repo):
    resolved = check_contract.resolve_definitions(list(check_contract.CHECK_IDS))
    assert resolved["status"] == "complete"
    assert resolved["consumer_completeness"] == "not_claimed"
    by_id = {entry["check_id"]: entry for entry in resolved["definitions"]}
    assert set(by_id) == set(check_contract.CHECK_IDS.values())
    assert by_id["eci/weaver/terminal"]["requires_post_completion"] is True
    assert all(
        not entry["requires_post_completion"]
        for check_id, entry in by_id.items()
        if check_id != "eci/weaver/terminal"
    )

    path, _ = repo
    plan = make_plan(repo)
    expected = plan["expected_checks"]
    assert expected["definitions_status"] == "requested_names_complete"
    assert expected["status"] == "partial"
    assert expected["reason"] == "consumer_profile_not_resolved"
    assert expected["consumer_completeness"] == "not_claimed"

    report = execution.execute(plan, root=path)
    assert report.get("trusted", False) is False
    report_checks = {item.get("name"): item for item in report.get("checks", [])}
    for non_hygiene_name in (
        "unittest",
        "pytest",
        "weaver_registry",
        "weaver_live",
        "weaver_terminal",
    ):
        assert non_hygiene_name not in report_checks
    for item in report.get("checks", []):
        satisfaction = item.get("native_satisfaction")
        if satisfaction is not None:
            assert satisfaction["trusted"] is False
            assert satisfaction["required"] is False


def _rmtree(target):
    def _onexc(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    shutil.rmtree(target, onexc=_onexc)


def test_p1_2_external_event_policy_shares_across_refs_with_distinct_configs_and_trees(
    repo, tmp_path
):
    path, base = repo
    ext_dir = tmp_path / "external_policy"
    ext_dir.mkdir()
    ext_selection = ext_dir / "ci-selection.toml"
    shutil.copyfile(path / "ci-selection.toml", ext_selection)
    ext_hygiene = ext_dir / "ci-hygiene.toml"
    ext_hygiene.write_text("schema_version = 1\n", encoding="utf-8")

    # Branch A: code change + distinct tach.toml + distinct tests + stale in-repo ci-selection.toml
    (path / "src/pkg/sub.py").write_text("VALUE = 101\n", encoding="utf-8")
    (path / "tests/test_branch_a.py").write_text(
        "def test_a():\n    assert True\n", encoding="utf-8"
    )
    (path / "tach.toml").write_text(
        (path / "tach.toml").read_text(encoding="utf-8")
        + '\n[[modules]]\npath = "pkg.extra_a"\ndepends_on = ["pkg.sub"]\n',
        encoding="utf-8",
    )
    (path / "ci-selection.toml").write_text(
        'schema_version = 1\ntach = "tach.toml"\nignored_files = ["README.md"]\n',
        encoding="utf-8",
    )
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "branch-a-distinct")
    commit_a = run(path, "rev-parse", "HEAD").strip()

    # Branch B: docs change + distinct tach.toml + distinct tests + different stale in-repo ci-selection.toml
    run(path, "checkout", "-q", base)
    (path / "docs/branch_b.md").write_text("# branch b\n", encoding="utf-8")
    (path / "tests/test_branch_b.py").write_text(
        "def test_b():\n    assert True\n", encoding="utf-8"
    )
    (path / "tach.toml").write_text(
        (path / "tach.toml").read_text(encoding="utf-8")
        + '\n[[modules]]\npath = "pkg.extra_b"\ndepends_on = []\n',
        encoding="utf-8",
    )
    (path / "ci-selection.toml").write_text(
        'schema_version = 1\ntach = "tach.toml"\nignored_files = ["OTHER.md"]\n',
        encoding="utf-8",
    )
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "branch-b-distinct")
    commit_b = run(path, "rev-parse", "HEAD").strip()

    run(path, "checkout", "-q", base)
    multi = selection.changeset_multi(
        path,
        [
            ("refs/heads/branch-a", commit_a, "refs/heads/branch-a", base),
            ("refs/heads/branch-b", commit_b, "refs/heads/branch-b", base),
        ],
    )
    profile = _sample_effective_profile(
        context="push",
        action="update",
        target="refs/heads/branch-a",
        targets=["refs/heads/branch-a", "refs/heads/branch-b"],
        required_obligations=[
            {
                "obligation_ref": "ob/code-unit",
                "kind": "check",
                "check_names": ["unittest"],
                "target_refs": ["refs/heads/branch-a", "refs/heads/branch-b"],
                "rule_ref": {
                    "name": "engineering-ci.unittest",
                    "version": "1",
                    "sha256": f"sha256:{'1' * 64}",
                },
            }
        ],
        declared_checks=[
            "whitespace",
            "forwarding",
            "docs",
            "vulture",
            "tach",
            "unittest",
        ],
    )
    cfg = _attach_effective_profile_policy(ext_selection, profile)
    sel = selection.select(multi, cfg, root=path)
    p = selection.plan(
        sel,
        config=cfg,
        config_path=ext_selection,
        hygiene_config_path=ext_hygiene,
        root=path,
        effective_profile=profile,
    )
    plan_contract.validate(p)
    assert p["inputs"]["config"]["policy_scope"] == "event_external"
    per_sel = p["selection"]["per_ref"]
    assert per_sel[0]["config_source_binding"]["status"] == "event_shared"
    assert per_sel[1]["config_source_binding"]["status"] == "event_shared"
    assert per_sel[0]["source_snapshot_ref"]["source_origin"] == "git_target_tree"
    assert (
        per_sel[0]["source_snapshot_ref"]["tach_sha256"]
        != per_sel[1]["source_snapshot_ref"]["tach_sha256"]
    )
    assert (
        per_sel[0]["source_snapshot_ref"]["tests_digest"]
        != per_sel[1]["source_snapshot_ref"]["tests_digest"]
    )
    per_exp = p["expected_checks"]["per_ref"]
    assert per_exp[0]["status"] == "complete"
    assert per_exp[1]["status"] == "complete"


def test_p1_2_in_repo_policy_mismatch_blocks_ref_and_prevents_complete(repo):
    path, base = repo
    profile = _sample_effective_profile(
        context="push",
        action="update",
        target="refs/heads/match",
        targets=["refs/heads/match", "refs/heads/mismatch"],
        declared_checks=["whitespace", "forwarding", "docs", "vulture", "tach"],
    )
    cfg = _attach_effective_profile_policy(path, profile)
    run(path, "add", "ci-selection.toml")
    run(path, "commit", "-q", "-m", "attach effective profile")
    base = run(path, "rev-parse", "HEAD").strip()

    (path / "docs/match.md").write_text("# match\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "matching-ref")
    match_commit = run(path, "rev-parse", "HEAD").strip()

    run(path, "checkout", "-q", base)
    (path / "src/pkg/sub.py").write_text("VALUE = 202\n", encoding="utf-8")
    (path / "ci-selection.toml").write_text(
        (path / "ci-selection.toml").read_text(encoding="utf-8")
        + "\n# diverged in target ref\n",
        encoding="utf-8",
    )
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "mismatched-ref")
    mismatch_commit = run(path, "rev-parse", "HEAD").strip()

    run(path, "checkout", "-q", base)
    multi = selection.changeset_multi(
        path,
        [
            ("refs/heads/match", match_commit, "refs/heads/match", base),
            ("refs/heads/mismatch", mismatch_commit, "refs/heads/mismatch", base),
        ],
    )
    sel = selection.select(multi, cfg, root=path)
    p = selection.plan(
        sel,
        config=cfg,
        config_path=path / "ci-selection.toml",
        root=path,
        effective_profile=profile,
    )
    plan_contract.validate(p)
    assert p["inputs"]["config"]["policy_scope"] == "repo_target_tree"
    per_sel = p["selection"]["per_ref"]
    assert per_sel[0]["config_source_binding"]["status"] == "verified"
    assert per_sel[1]["config_source_binding"]["status"] == "mismatch"
    assert per_sel[1]["config_source_binding"]["mismatched"] == ["ci-selection.toml"]
    assert per_sel[1]["status"] == "blocked"
    per_exp = p["expected_checks"]["per_ref"]
    assert per_exp[0]["status"] == "complete"
    assert per_exp[1]["status"] == "unknown"
    assert p["expected_checks"]["status"] != "complete"

    # Tampering policy_scope to external or forging mismatched ref status=complete is rejected.
    bad_scope = json.loads(json.dumps(p))
    bad_scope["inputs"]["config"]["policy_scope"] = "event_external"
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(bad_scope)
    assert raised.value.code == "expected_per_ref_source_unverifiable"

    forged_complete = json.loads(json.dumps(p))
    forged_complete["expected_checks"]["per_ref"][1]["status"] = "complete"
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(forged_complete)
    assert raised.value.code == "expected_per_ref_status_invalid"


def _export_tree(repo_root, tree, dest, tmp_root):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "GIT_DIR": str(Path(repo_root) / ".git"),
        "GIT_INDEX_FILE": str(tmp_root / f"index_{dest.name}"),
        "GIT_WORK_TREE": str(dest),
    }
    subprocess.run(["git", "read-tree", tree], cwd=repo_root, env=env, check=True)
    subprocess.run(["git", "checkout-index", "-a", "-f"], cwd=dest, env=env, check=True)
    return dest


def test_p1_3_non_odb_consumer_verifies_bound_per_ref_source_snapshot(repo, tmp_path):
    path, base = repo
    profile = _sample_effective_profile(
        context="push",
        action="update",
        target="refs/heads/alpha",
        targets=["refs/heads/alpha", "refs/heads/beta"],
        declared_checks=["whitespace", "forwarding", "docs", "vulture", "tach"],
    )
    cfg = _attach_effective_profile_policy(path, profile)
    run(path, "add", "ci-selection.toml")
    run(path, "commit", "-q", "-m", "attach effective profile")
    base = run(path, "rev-parse", "HEAD").strip()

    (path / "src/pkg/sub.py").write_text("VALUE = 301\n", encoding="utf-8")
    (path / "tests/test_alpha.py").write_text(
        "def test_alpha():\n    assert True\n", encoding="utf-8"
    )
    (path / "tach.toml").write_text(
        (path / "tach.toml").read_text(encoding="utf-8")
        + '\n[[modules]]\npath = "pkg.alpha"\ndepends_on = []\n',
        encoding="utf-8",
    )
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "alpha")
    alpha_commit = run(path, "rev-parse", "HEAD").strip()

    run(path, "checkout", "-q", base)
    (path / "docs/beta.md").write_text("# beta\n", encoding="utf-8")
    (path / "tests/test_beta.py").write_text(
        "def test_beta():\n    assert True\n", encoding="utf-8"
    )
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "beta")
    beta_commit = run(path, "rev-parse", "HEAD").strip()

    run(path, "checkout", "-q", base)
    multi = selection.changeset_multi(
        path,
        [
            ("refs/heads/alpha", alpha_commit, "refs/heads/alpha", base),
            ("refs/heads/beta", beta_commit, "refs/heads/beta", base),
        ],
    )
    sel = selection.select(multi, cfg, root=path)
    p = selection.plan(
        sel,
        config=cfg,
        config_path=path / "ci-selection.toml",
        root=path,
        effective_profile=profile,
    )
    plan_contract.validate(p)

    tree_alpha = p["git"]["references"][0]["tree"]
    tree_beta = p["git"]["references"][1]["tree"]
    export_alpha = _export_tree(path, tree_alpha, tmp_path / "export_alpha", tmp_path)
    export_beta = _export_tree(path, tree_beta, tmp_path / "export_beta", tmp_path)
    mats = {tree_alpha: export_alpha, tree_beta: export_beta}

    # Remove .git to simulate a non-ODB consumer (e.g. Dagger/export container).
    _rmtree(path / ".git")

    # Without materialized_sources, self-consistency NEVER implies Source-bound PASS:
    handoff_missing = expected_checks.verify_per_ref_source_handoff(p)
    assert handoff_missing["status"] == "unknown"
    assert handoff_missing["reason"] == "source_handoff_insufficient"
    assert handoff_missing["internal_snapshot_status"] == "consistent"
    with pytest.raises(plan_contract.PlanError) as raised_missing:
        plan_contract.validate(p)
    assert raised_missing.value.code == "expected_per_ref_source_unverifiable"
    with pytest.raises(expected_checks.ExpectedError) as raised_proj_missing:
        expected_checks.validate_projection(
            p["expected_checks"], p["git"], p["check_scope"], plan=p
        )
    assert raised_proj_missing.value.code == "expected_per_ref_source_unverifiable"

    # With authentic materialized Source trees, non-ODB consumer verifies & passes:
    handoff_ok = expected_checks.verify_per_ref_source_handoff(
        p, materialized_sources=mats
    )
    assert handoff_ok["status"] == "verified"
    assert handoff_ok["origin"] == "materialized_source_tree"
    assert plan_contract.validate(p, materialized_sources=mats) is p
    expected_checks.validate_projection(
        p["expected_checks"],
        p["git"],
        p["check_scope"],
        plan=p,
        materialized_sources=mats,
    )
    assert (
        p["selection"]["per_ref"][0]["source_snapshot_ref"]
        == p["expected_checks"]["per_ref"][0]["source_snapshot_ref"]
    )
    assert (
        p["selection"]["per_ref"][0]["source_snapshot_ref"]
        != p["selection"]["per_ref"][1]["source_snapshot_ref"]
    )

    # Swapping or tampering with a materialized export directory fails closed:
    with pytest.raises(plan_contract.PlanError) as raised_swapped:
        plan_contract.validate(
            p, materialized_sources={tree_alpha: export_beta, tree_beta: export_alpha}
        )
    assert raised_swapped.value.code == "expected_per_ref_source_unverifiable"

    (export_alpha / "tach.toml").write_text("tampered = true\n", encoding="utf-8")
    with pytest.raises(plan_contract.PlanError) as raised_tampered_export:
        plan_contract.validate(p, materialized_sources=mats)
    assert raised_tampered_export.value.code == "expected_per_ref_source_unverifiable"


def test_p1_3_non_odb_consumer_rejects_missing_or_tampered_source_snapshot(
    repo, tmp_path
):
    path, base = repo
    profile = _sample_effective_profile(
        context="push",
        action="update",
        target="refs/heads/one",
        targets=["refs/heads/one", "refs/heads/two"],
        declared_checks=["whitespace", "forwarding", "docs", "vulture", "tach"],
    )
    cfg = _attach_effective_profile_policy(path, profile)
    run(path, "add", "ci-selection.toml")
    run(path, "commit", "-q", "-m", "attach effective profile")
    base = run(path, "rev-parse", "HEAD").strip()

    (path / "src/pkg/sub.py").write_text("VALUE = 401\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "ref-one")
    commit_1 = run(path, "rev-parse", "HEAD").strip()

    run(path, "checkout", "-q", base)
    (path / "docs/two.md").write_text("# two\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "ref-two")
    commit_2 = run(path, "rev-parse", "HEAD").strip()

    run(path, "checkout", "-q", base)
    multi = selection.changeset_multi(
        path,
        [
            ("refs/heads/one", commit_1, "refs/heads/one", base),
            ("refs/heads/two", commit_2, "refs/heads/two", base),
        ],
    )
    sel = selection.select(multi, cfg, root=path)
    p = selection.plan(
        sel,
        config=cfg,
        config_path=path / "ci-selection.toml",
        root=path,
        effective_profile=profile,
    )
    tree_1 = p["git"]["references"][0]["tree"]
    tree_2 = p["git"]["references"][1]["tree"]
    mats = {
        tree_1: _export_tree(path, tree_1, tmp_path / "export_1", tmp_path),
        tree_2: _export_tree(path, tree_2, tmp_path / "export_2", tmp_path),
    }
    _rmtree(path / ".git")

    def assert_unverifiable(mutated):
        with pytest.raises(plan_contract.PlanError) as raised:
            plan_contract.validate(mutated, materialized_sources=mats)
        assert raised.value.code == "expected_per_ref_source_unverifiable"

    missing_snap = json.loads(json.dumps(p))
    missing_snap["selection"]["per_ref"][0].pop("source_snapshot_ref")
    assert_unverifiable(missing_snap)

    forged_tests = json.loads(json.dumps(p))
    forged_tests["selection"]["per_ref"][0]["tests"] = ["tests.test_forged"]
    assert_unverifiable(forged_tests)

    tampered_tree = json.loads(json.dumps(p))
    tampered_tree["selection"]["per_ref"][0]["source_snapshot_ref"]["target_tree"] = (
        "0" * 40
    )
    assert_unverifiable(tampered_tree)

    unverifiable_origin = json.loads(json.dumps(p))
    snap = unverifiable_origin["selection"]["per_ref"][0]["source_snapshot_ref"]
    snap["source_origin"] = "unverifiable"
    snap["snapshot_digest"] = "sha256:" + selection._json_sha256(
        {k: v for k, v in snap.items() if k != "snapshot_digest"}
    )
    unverifiable_origin["expected_checks"]["per_ref"][0]["source_snapshot_ref"] = snap
    assert_unverifiable(unverifiable_origin)

    mismatched_exp_snap = json.loads(json.dumps(p))
    mismatched_exp_snap["expected_checks"]["per_ref"][0].pop("source_snapshot_ref")
    assert_unverifiable(mismatched_exp_snap)

    missing_sel_per_ref = json.loads(json.dumps(p))
    missing_sel_per_ref["selection"].pop("per_ref")
    assert_unverifiable(missing_sel_per_ref)

    tampered_cfg_digest = json.loads(json.dumps(p))
    tampered_cfg_digest["inputs"]["config"]["sha256"] = "0" * 64
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(tampered_cfg_digest, materialized_sources=mats)
    assert raised.value.code == "config_identity_mismatch"

    mismatched_policy_snapshot = json.loads(json.dumps(p))
    snap2 = mismatched_policy_snapshot["selection"]["per_ref"][0]["source_snapshot_ref"]
    snap2["config_binding"]["policies"]["ci-selection.toml"]["sha256"] = "0" * 64
    snap2["snapshot_digest"] = "sha256:" + selection._json_sha256(
        {k: v for k, v in snap2.items() if k != "snapshot_digest"}
    )
    mismatched_policy_snapshot["expected_checks"]["per_ref"][0][
        "source_snapshot_ref"
    ] = snap2
    assert_unverifiable(mismatched_policy_snapshot)


def test_p1_1_config_identity_rejects_unbacked_in_memory_policy(repo, tmp_path):
    path, _base = repo
    cfg = selection.load_config(path / "ci-selection.toml")
    profile = _sample_effective_profile()
    unbacked_cfg = dataclasses.replace(
        cfg, effective_profile_policy=_profile_policy(profile)
    )
    with pytest.raises(selection.SelectionError) as raised_sel:
        selection.config_identity(path / "ci-selection.toml", unbacked_cfg, root=path)
    assert raised_sel.value.code == "config_identity_file_mismatch"

    hyg_file = tmp_path / "ci-hygiene.toml"
    hyg_file.write_text(
        "schema_version = 1\n[tach]\nexternal = false\n", encoding="utf-8"
    )
    loaded_hyg = hygiene.load_config(hyg_file)
    tampered_hyg = dataclasses.replace(loaded_hyg, checks=("whitespace",))
    with pytest.raises(hygiene.HygieneError) as raised_hyg:
        hygiene.config_identity(hyg_file, tampered_hyg, root=path)
    assert raised_hyg.value.code == "config_identity_file_mismatch"

    src_file = tmp_path / "ci-sources.toml"
    src_file.write_text(
        'schema_version = 1\nmanifest = "data/manifest.json"\nacquired_roots = ["data/acquired"]\n',
        encoding="utf-8",
    )
    loaded_src = sources.load_config(src_file)
    tampered_src = dataclasses.replace(loaded_src, schema_version=99)
    with pytest.raises(sources.SourcesError) as raised_src:
        sources.config_identity(src_file, tampered_src, root=path)
    assert raised_src.value.code == "config_identity_file_mismatch"

    # Tampered inputs.config.normalized against disk bytes must fail replay even if ref matches.
    sel = selection.select(selection.changeset(path, base=_base), cfg, root=path)
    p = selection.plan(
        sel, config=cfg, config_path=path / "ci-selection.toml", root=path
    )
    forged_norm = json.loads(json.dumps(p))
    forged_norm["inputs"]["config"]["normalized"]["effective_profile"] = (
        _profile_policy(profile)
    )
    sha = forged_norm["inputs"]["config"]["sha256"]
    norm = forged_norm["inputs"]["config"]["normalized"]
    forged_norm["inputs"]["config"]["effective_config_ref"]["sha256"] = (
        "sha256:" + selection._json_sha256({"sha256": sha, "normalized": norm})
    )
    with pytest.raises(plan_contract.PlanError) as raised_replay:
        plan_contract.validate(forged_norm)
    assert raised_replay.value.code == "config_identity_file_mismatch"


def test_p1_nested_config_real_odb_two_ref_target_trees(repo):
    path, base = repo
    nested_dir = path / ".ci"
    nested_dir.mkdir()
    nested_cfg = nested_dir / "ci-selection.toml"
    nested_cfg.write_text(
        (path / "ci-selection.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (path / "ci-selection.toml").unlink()
    profile = _sample_effective_profile(
        context="push",
        action="update",
        target="refs/heads/nested-one",
        targets=["refs/heads/nested-one", "refs/heads/nested-two"],
        required_obligations=[
            {
                "obligation_ref": "ob/nested-unit",
                "kind": "check",
                "check_names": ["unittest"],
                "target_refs": ["refs/heads/nested-one", "refs/heads/nested-two"],
                "allow_inapplicable": True,
                "rule_ref": {
                    "name": "engineering-ci.unittest",
                    "version": "1",
                    "sha256": f"sha256:{'1' * 64}",
                },
            }
        ],
        allowed_inapplicability=["no_tests"],
        declared_checks=[
            "whitespace",
            "forwarding",
            "docs",
            "vulture",
            "tach",
            "unittest",
        ],
    )
    cfg = _attach_effective_profile_policy(nested_cfg, profile)
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "move selection config to .ci")
    base = run(path, "rev-parse", "HEAD").strip()

    (path / "src/pkg/sub.py").write_text("VALUE = 501\n", encoding="utf-8")
    (path / "tests/test_nested_one.py").write_text(
        "def test_nested_one():\n    assert True\n", encoding="utf-8"
    )
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "nested-ref-one")
    commit_one = run(path, "rev-parse", "HEAD").strip()

    run(path, "checkout", "-q", base)
    (path / "docs/nested_two.md").write_text("# nested two\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "nested-ref-two")
    commit_two = run(path, "rev-parse", "HEAD").strip()

    run(path, "checkout", "-q", base)
    multi = selection.changeset_multi(
        path,
        [
            ("refs/heads/nested-one", commit_one, "refs/heads/nested-one", base),
            ("refs/heads/nested-two", commit_two, "refs/heads/nested-two", base),
        ],
    )
    sel = selection.select(multi, cfg, root=path)
    p = selection.plan(
        sel,
        config=cfg,
        config_path=nested_cfg,
        root=path,
        effective_profile=profile,
    )
    assert p["inputs"]["config"]["policy_scope"] == "repo_target_tree"
    assert Path(p["inputs"]["config"]["path"]) == nested_cfg.resolve()
    assert Path(p["git"]["repo_root"]) == path.resolve()
    assert not (nested_dir / ".git").exists()

    # Validating via plan_contract / expected_checks without materialized_sources succeeds via git.repo_root ODB:
    assert plan_contract.validate(p) is p
    expected_checks.validate_projection(
        p["expected_checks"], p["git"], p["check_scope"], plan=p
    )
    handoff = expected_checks.verify_per_ref_source_handoff(p)
    assert handoff["status"] == "verified"
    assert handoff["origin"] == "git_odb"
    per_sel = p["selection"]["per_ref"]
    assert per_sel[0]["config_source_binding"]["status"] == "verified"
    assert per_sel[1]["config_source_binding"]["status"] == "verified"
    assert ".ci/ci-selection.toml" in per_sel[0]["config_source_binding"]["policies"]
    assert p["expected_checks"]["per_ref"][0]["status"] == "complete"
    assert p["expected_checks"]["per_ref"][1]["status"] == "complete"

    # Explicit nested config parent directory is NOT accepted as Git root:
    nested_handoff = expected_checks.verify_per_ref_source_handoff(p, root=nested_dir)
    assert nested_handoff["status"] == "unknown"
    assert nested_handoff["reason"] == "source_handoff_insufficient"
    with pytest.raises(plan_contract.PlanError) as raised_nested:
        plan_contract.validate(p, root=nested_dir)
    assert raised_nested.value.code == "expected_per_ref_source_unverifiable"


def test_p1_inaccessible_odb_or_missing_target_object_requires_materialized_handoff_or_refuses(
    repo, tmp_path
):
    path, base = repo
    nested_dir = path / ".ci"
    nested_dir.mkdir()
    nested_cfg = nested_dir / "ci-selection.toml"
    nested_cfg.write_text(
        (path / "ci-selection.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (path / "ci-selection.toml").unlink()
    profile = _sample_effective_profile(
        context="push",
        action="update",
        target="refs/heads/obj-one",
        targets=["refs/heads/obj-one", "refs/heads/obj-two"],
        declared_checks=["whitespace", "forwarding", "docs", "vulture", "tach"],
    )
    cfg = _attach_effective_profile_policy(nested_cfg, profile)
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "nested cfg for inaccessible ODB test")
    base = run(path, "rev-parse", "HEAD").strip()

    (path / "src/pkg/sub.py").write_text("VALUE = 601\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "obj-one")
    commit_one = run(path, "rev-parse", "HEAD").strip()

    run(path, "checkout", "-q", base)
    (path / "docs/obj_two.md").write_text("# obj two\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "obj-two")
    commit_two = run(path, "rev-parse", "HEAD").strip()

    run(path, "checkout", "-q", base)
    multi = selection.changeset_multi(
        path,
        [
            ("refs/heads/obj-one", commit_one, "refs/heads/obj-one", base),
            ("refs/heads/obj-two", commit_two, "refs/heads/obj-two", base),
        ],
    )
    sel = selection.select(multi, cfg, root=path)
    p = selection.plan(
        sel,
        config=cfg,
        config_path=nested_cfg,
        root=path,
        effective_profile=profile,
    )
    tree_one = p["git"]["references"][0]["tree"]
    tree_two = p["git"]["references"][1]["tree"]
    mats = {
        tree_one: _export_tree(path, tree_one, tmp_path / "obj_export_1", tmp_path),
        tree_two: _export_tree(path, tree_two, tmp_path / "obj_export_2", tmp_path),
    }

    # Case 1: Git root exists, but its ODB lacks target tree objects -> falls back to materialized handoff or refuses:
    empty_git = tmp_path / "empty_git"
    empty_git.mkdir()
    run(empty_git, "init", "-q")
    foreign_odb_plan = json.loads(json.dumps(p))
    foreign_odb_plan["git"]["repo_root"] = str(empty_git.resolve())

    missing_obj_handoff = expected_checks.verify_per_ref_source_handoff(
        foreign_odb_plan
    )
    assert missing_obj_handoff["status"] == "unknown"
    assert missing_obj_handoff["reason"] == "source_handoff_insufficient"
    assert missing_obj_handoff["internal_snapshot_status"] == "consistent"
    with pytest.raises(plan_contract.PlanError) as raised_foreign:
        plan_contract.validate(foreign_odb_plan)
    assert raised_foreign.value.code == "expected_per_ref_source_unverifiable"

    verified_foreign = expected_checks.verify_per_ref_source_handoff(
        foreign_odb_plan, materialized_sources=mats
    )
    assert verified_foreign["status"] == "verified"
    assert verified_foreign["origin"] == "materialized_source_tree"
    assert (
        plan_contract.validate(foreign_odb_plan, materialized_sources=mats)
        is foreign_odb_plan
    )

    # Case 2: Original .git is absent -> refuses without materialized_sources, passes only with valid exports:
    _rmtree(path / ".git")
    absent_handoff = expected_checks.verify_per_ref_source_handoff(p)
    assert absent_handoff["status"] == "unknown"
    assert absent_handoff["reason"] == "source_handoff_insufficient"
    with pytest.raises(plan_contract.PlanError) as raised_no_odb:
        plan_contract.validate(p)
    assert raised_no_odb.value.code == "expected_per_ref_source_unverifiable"

    verified_mat = expected_checks.verify_per_ref_source_handoff(
        p, materialized_sources=mats
    )
    assert verified_mat["status"] == "verified"
    assert verified_mat["origin"] == "materialized_source_tree"
    assert plan_contract.validate(p, materialized_sources=mats) is p

    (mats[tree_one] / "src/pkg/sub.py").write_text(
        "VALUE = tampered\n", encoding="utf-8"
    )
    with pytest.raises(plan_contract.PlanError) as raised_tampered:
        plan_contract.validate(p, materialized_sources=mats)
    assert raised_tampered.value.code == "expected_per_ref_source_unverifiable"


def test_phase_profile_projects_check_and_non_check_obligations(repo):
    path, base = repo
    (path / "docs/note.md").write_text("# note\n", encoding="utf-8")
    changes = selection.changeset(path, base=base, mode="worktree")
    cfg = selection.load_config(path / "ci-selection.toml")
    chosen = selection.select(changes, cfg, root=path)
    obligations = [
        {
            "obligation_ref": "eci/always",
            "kind": "check",
            "check_names": ["hygiene"],
        },
        {
            "obligation_ref": "eci/vulture",
            "kind": "check",
            "check_names": ["vulture"],
            "phases": ["post_publish"],
        },
        {
            "obligation_ref": "eci/terminal",
            "kind": "non_check",
            "phases": ["post_publish"],
        },
    ]

    projections = {}
    for phase in ("pre_publish", "post_publish"):
        profile = _sample_effective_profile(
            phase=phase,
            declared_checks=["hygiene", "tach", "vulture"],
            required_obligations=obligations,
        )
        expected_checks.validate_effective_profile(profile)
        plan = selection.plan(
            chosen,
            config=cfg,
            config_path=path / "ci-selection.toml",
            root=path,
            effective_profile=profile,
        )
        projections[phase] = plan["expected_checks"]

    pre = projections["pre_publish"]
    post = projections["post_publish"]
    assert pre["expected_set_ref"] != post["expected_set_ref"]
    assert {item["obligation_ref"] for item in pre["non_check_obligations"]} == set()
    assert {item["obligation_ref"] for item in post["non_check_obligations"]} == {
        "eci/terminal"
    }
    pre_vulture = next(item for item in pre["checks"] if item["name"] == "vulture")
    post_vulture = next(item for item in post["checks"] if item["name"] == "vulture")
    assert "eci/vulture" not in pre_vulture["required_by_obligations"]
    assert "eci/vulture" in post_vulture["required_by_obligations"]
