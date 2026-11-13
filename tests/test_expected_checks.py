"""#66-B Expected Check projection and Plan validation."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from engineering_ci import expected_checks, plan_contract, selection

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
    assert empty["reason"] == "no_expected_checks_or_profile"
    assert empty["checks"] == []


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
