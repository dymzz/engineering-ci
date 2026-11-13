"""Generic Git impact selection, plan identity and native unittest execution."""

import dataclasses
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from engineering_ci import execution, selection, weaver_tool

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
    run(target, "config", "user.email", "selection@example.invalid")
    run(target, "config", "user.name", "Selection Test")
    run(target, "add", "-A")
    run(target, "commit", "-q", "-m", "base")
    return target, run(target, "rev-parse", "HEAD").strip()


def config(repo):
    return selection.load_config(repo / "ci-selection.toml")


def changes(repo, base, *, head="HEAD", include_worktree=True, **kwargs):
    return selection.changeset(
        repo,
        base=base,
        head=head,
        include_worktree=include_worktree,
        **kwargs,
    )


def select(repo, base, *, full=False, include_worktree=True):
    return selection.select(
        changes(repo, base, include_worktree=include_worktree),
        config(repo),
        root=repo,
        full=full,
    )


def test_plan_validator_accepts_all_actual_selector_scope_states(repo):
    path, base = repo
    original = changes(path, base)
    observed = set()
    for paths, complete, full in (
        (("src/pkg/sub.py",), True, False),
        (("README.md",), True, False),
        ((), True, False),
        (("pyproject.toml",), True, False),
        ((), False, False),
        (("README.md",), True, True),
    ):
        actual = selection.select(
            dataclasses.replace(original, paths=paths, complete=complete),
            config(path),
            root=path,
            full=full,
        )
        value = selection.plan(
            actual,
            config=config(path),
            config_path=path / "ci-selection.toml",
            root=path,
        )
        execution.validate_plan(value)
        observed.update(item["status"] for item in value["check_scope"])
    assert observed == {
        "selected",
        "unknown",
        "not_applicable",
        "targeted",
        "escalated",
        "no_tests",
        "no_changes",
    }


def test_dynamic_discovery_uses_configured_tests_directory(repo):
    path, _ = repo
    result = select(path, "HEAD")
    assert result.tests == ()
    assert result.status == "no_changes"
    assert result.escalated is False


def test_empty_change_set_is_not_a_pass(repo):
    path, base = repo
    result = select(path, base)
    assert result.status == "no_changes"
    assert result.tests == ()
    plan = selection.plan(
        result,
        config=config(path),
        config_path=path / "ci-selection.toml",
        root=path,
    )
    assert plan["selection"]["status"] == "no_changes"
    assert plan["selection"]["tests"] == []


def test_tach_reverse_closure_selects_dependent_tests(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 2\n", encoding="utf-8")
    result = select(path, base)
    assert result.status == "targeted" and result.escalated is False
    assert result.tests == ("tests.test_other", "tests.test_pkg", "tests.test_sub")
    assert "tests.test_pkg" in result.reasons
    assert any("pkg.sub" in item for item in result.reasons["tests.test_sub"])
    assert result.prerequisites == frozenset({"identity_commit", "wheel_smoke"})


def test_leaf_module_only_selects_its_own_tests(repo):
    path, base = repo
    (path / "src/pkg/other.py").write_text("# changed\n", encoding="utf-8")
    result = select(path, base)
    assert result.tests == ("tests.test_other",)
    assert result.prerequisites == frozenset({"wheel_smoke"})


def test_non_code_rules_are_configurable_and_ordered(repo):
    path, base = repo
    (path / "data").mkdir()
    (path / "data/input.json").write_text("{}\n", encoding="utf-8")
    result = select(path, base)
    assert result.tests == ("tests.test_pkg",)
    assert "data input" in result.reasons["tests.test_pkg"][0]


def test_ignored_paths_do_not_claim_test_coverage(repo):
    path, base = repo
    (path / "docs/guide.md").write_text("# changed\n", encoding="utf-8")
    result = select(path, base)
    assert result.status == "no_tests"
    assert result.tests == ()
    assert result.escalated is False


def test_config_and_workflow_escalation_is_conservative(repo):
    path, base = repo
    (path / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\n', encoding="utf-8"
    )
    result = select(path, base)
    assert result.status == "escalated" and result.escalated is True
    assert "pyproject.toml" in result.escalation_reason
    assert result.tests == (
        "tests.test_other",
        "tests.test_pkg",
        "tests.test_sub",
    )


def test_unknown_path_escalates_instead_of_zero_tests(repo):
    path, base = repo
    (path / "unknown.bin").write_bytes(b"x")
    result = select(path, base)
    assert result.escalated is True
    assert "unknown.bin" in result.escalation_reason
    assert result.tests


def test_deleted_test_file_escalates(repo):
    path, base = repo
    (path / "tests/test_pkg.py").unlink()
    result = select(path, base)
    assert result.escalated is True
    assert "deleted" in result.escalation_reason.lower()


def test_unmapped_module_escalates(repo):
    path, _ = repo
    (path / "tach.toml").write_text(
        Path(path / "tach.toml").read_text(encoding="utf-8")
        + '\n[[modules]]\npath = "pkg.new"\ndepends_on = []\n',
        encoding="utf-8",
    )
    run(path, "add", "tach.toml")
    run(path, "commit", "-q", "-m", "declare module")
    base = run(path, "rev-parse", "HEAD").strip()
    (path / "src/pkg/new.py").write_text("VALUE = 1\n", encoding="utf-8")
    result = select(path, base)
    assert result.escalated is True
    assert "pkg.new" in result.escalation_reason


def test_force_full_selects_every_discovered_module(repo):
    path, base = repo
    result = select(path, base, full=True)
    assert result.status == "escalated"
    assert result.tests == (
        "tests.test_other",
        "tests.test_pkg",
        "tests.test_sub",
    )
    assert result.prerequisites == frozenset({"identity_commit", "wheel_smoke"})


def test_full_selection_explicitly_marks_tests_only(repo):
    path, base = repo
    result = select(path, base, full=True)
    plan = selection.plan(
        result,
        config=config(path),
        config_path=path / "ci-selection.toml",
        root=path,
    )
    assert plan["selection"]["full_tests"] is True
    scope = {item["name"]: item for item in plan["check_scope"]}
    assert scope["unittest"]["status"] == "escalated"
    assert scope["docs"]["status"] == "not_applicable"
    assert scope["tach"]["status"] == "not_applicable"
    assert scope["vulture"]["status"] == "not_applicable"


def test_plan_records_git_config_and_tach_identity(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 3\n", encoding="utf-8")
    result = select(path, base)
    plan = selection.plan(
        result,
        config=config(path),
        config_path=path / "ci-selection.toml",
        root=path,
    )
    assert plan["schema_version"] == 2
    assert plan["kind"] == "engineering-ci-selection-plan"
    assert plan["git"]["base_commit"] is not None
    assert plan["git"]["head_commit"] == base
    assert plan["git"]["worktree_dirty"] is True
    assert "src/pkg/sub.py" in plan["git"]["changed_paths"]
    assert plan["generator"]["config_sha256"]
    assert plan["generator"]["tach_sha256"]
    assert plan["selection"]["tests"] == list(result.tests)
    assert plan["selection"]["prerequisites"] == ["identity_commit", "wheel_smoke"]
    assert plan["prerequisite_actions"] == {}


def test_plan_emits_content_binding_and_source_tree_id(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 4\n", encoding="utf-8")
    dirty = select(path, base)
    dirty_plan = selection.plan(
        dirty,
        config=config(path),
        config_path=path / "ci-selection.toml",
        root=path,
    )
    assert dirty_plan["git"]["snapshot"] == "worktree"
    assert dirty_plan["git"]["execution_tree"] is None
    assert dirty_plan["git"]["content_binding"]["digest"]
    assert "src/pkg/sub.py" in dirty_plan["git"]["content_binding"]["paths"]

    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "committed")
    head = run(path, "rev-parse", "HEAD").strip()
    committed = changes(path, base, mode="commit", head=head)
    commit_plan = selection.plan(
        selection.select(committed, config(path), root=path),
        config=config(path),
        config_path=path / "ci-selection.toml",
        root=path,
    )
    assert commit_plan["git"]["execution_tree"]
    assert commit_plan["git"]["source_tree_id"]
    assert commit_plan["git"]["source_tree_id"] == selection.tree_content_id(
        path, commit_plan["git"]["execution_tree"]
    )


def test_directory_content_id_matches_tree_content_id(repo, tmp_path):
    path, _ = repo
    tree = run(path, "rev-parse", "HEAD^{tree}").strip()
    expected = selection.tree_content_id(path, tree)
    export = tmp_path / "tree"
    export.mkdir()
    env = {
        **os.environ,
        "GIT_DIR": str(path / ".git"),
        "GIT_INDEX_FILE": str(tmp_path / "index"),
        "GIT_WORK_TREE": str(export),
    }
    subprocess.run(["git", "read-tree", tree], cwd=path, env=env, check=True)
    subprocess.run(
        ["git", "checkout-index", "-a", "-f"], cwd=export, env=env, check=True
    )
    assert selection.directory_content_id(export) == expected


def test_directory_content_id_detects_tampering(repo, tmp_path):
    path, _ = repo
    tree = run(path, "rev-parse", "HEAD^{tree}").strip()
    expected = selection.tree_content_id(path, tree)
    export = tmp_path / "tree"
    export.mkdir()
    env = {
        **os.environ,
        "GIT_DIR": str(path / ".git"),
        "GIT_INDEX_FILE": str(tmp_path / "index"),
        "GIT_WORK_TREE": str(export),
    }
    subprocess.run(["git", "read-tree", tree], cwd=path, env=env, check=True)
    subprocess.run(
        ["git", "checkout-index", "-a", "-f"], cwd=export, env=env, check=True
    )
    (export / "src/pkg/sub.py").write_text("VALUE = tampered\n", encoding="utf-8")
    assert selection.directory_content_id(export) != expected


def test_plan_includes_typed_prerequisite_actions(repo):
    path, base = repo
    config_path = path / "ci-selection.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + f"""
[prerequisite_actions.identity_commit]
type = "git_commit_present"
commit = "{base}"

[prerequisite_actions.wheel_smoke]
type = "command"
argv = ["python", "-c", "raise SystemExit(0)"]
""",
        encoding="utf-8",
    )
    (path / "src/pkg/sub.py").write_text("VALUE = 3\n", encoding="utf-8")
    result = select(path, base)
    plan = selection.plan(
        result,
        config=config(path),
        config_path=config_path,
        root=path,
    )
    assert plan["prerequisite_actions"]["identity_commit"] == {
        "type": "git_commit_present",
        "commit": base,
    }
    assert plan["prerequisite_actions"]["wheel_smoke"]["type"] == "command"
    assert plan["prerequisite_actions"]["wheel_smoke"]["argv"][0] == "python"


@pytest.mark.parametrize("mode", ["targeted", "full", "escalated"])
def test_dependency_contract_records_actual_selection_without_inventing_actions(
    repo, mode
):
    path, base = repo
    changed = path / ("pyproject.toml" if mode == "escalated" else "src/pkg/other.py")
    changed.write_text(
        changed.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8"
    )
    selected = select(path, base, full=mode == "full")
    value = selection.plan(
        selected, config=config(path), config_path=path / "ci-selection.toml", root=path
    )
    execution.validate_plan(value)
    edges = value["dependency_contract"]["edges"]
    assert {edge["producer_ref"]["name"] for edge in edges} == set(
        value["selection"]["prerequisites"]
    )
    assert {edge["selection"]["selected_by"] for edge in edges} == {mode}
    assert all(
        edge["resolution"] == "unresolved"
        and edge["unresolved_reason"] == "action_parameters_missing"
        for edge in edges
    )


def test_changeset_includes_renames_and_untracked_paths(repo):
    path, base = repo
    run(path, "mv", "src/pkg/other.py", "src/pkg/renamed.py")
    (path / "untracked.txt").write_text("x\n", encoding="utf-8")
    changed = changes(path, base)
    assert any(item.endswith("renamed.py") for item in changed.paths)
    assert any(item.endswith("other.py") for item in changed.paths)
    assert "untracked.txt" in changed.paths
    assert changed.worktree_dirty is True


def test_paths_preserve_leading_spaces_from_git_reports(repo):
    path, base = repo
    run(path, "mv", "src/pkg/other.py", "src/pkg/ leading.py")
    changed = changes(path, base)
    assert "src/pkg/ leading.py" in changed.paths
    assert "src/pkg/other.py" in changed.paths


def test_paths_preserve_spaces_across_rename_and_deletion(repo):
    path, base = repo
    run(path, "mv", "docs/guide.md", "docs/space name.md")
    renamed = changes(path, base)
    assert "docs/space name.md" in renamed.paths
    assert "docs/guide.md" in renamed.paths
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "rename document with spaces")
    base = run(path, "rev-parse", "HEAD").strip()
    (path / "docs/space name.md").unlink()
    deleted = changes(path, base)
    assert "docs/space name.md" in deleted.paths
    result = selection.select(deleted, config(path), root=path)
    plan = selection.plan(
        result,
        config=config(path),
        config_path=path / "ci-selection.toml",
        root=path,
    )
    scope = {item["name"]: item for item in plan["check_scope"]}
    assert scope["docs"]["recheck_all"] is True
    assert "docs/space name.md" in scope["docs"]["inputs"]


def test_missing_base_escalates_instead_of_assuming_no_changes(repo):
    path, _ = repo
    result = selection.select(
        selection.changeset(path, base="does-not-exist"),
        config(path),
        root=path,
    )
    assert result.escalated is True
    assert result.tests


def test_unittest_runner_executes_selected_modules(repo):
    path, _ = repo
    result = selection.run_tests(("tests.test_pkg",), root=path)
    assert result == 0


def test_unittest_runner_propagates_failure(repo):
    path, _ = repo
    (path / "tests/test_pkg.py").write_text(
        "import unittest\n\n\nclass F(unittest.TestCase):\n"
        "    def test_fail(self):\n        self.fail('nope')\n",
        encoding="utf-8",
    )
    assert selection.run_tests(("tests.test_pkg",), root=path) != 0


def test_pytest_runner_accepts_multiple_targets_in_one_execution(repo):
    path, _ = repo
    result = selection.run_tests(
        ("tests/test_pkg.py", "tests/test_sub.py"),
        root=path,
        framework="pytest",
    )
    assert result == 0


def test_pytest_runner_propagates_failure_from_one_target(repo):
    path, _ = repo
    (path / "tests/test_sub.py").write_text(
        "import unittest\n\n\nclass F(unittest.TestCase):\n"
        "    def test_fail(self):\n        self.fail('nope')\n",
        encoding="utf-8",
    )
    result = selection.run_tests(
        ("tests/test_pkg.py", "tests/test_sub.py"),
        root=path,
        framework="pytest",
    )
    assert result != 0


def test_pytest_runner_writes_junit_and_preserves_exit(repo, tmp_path):
    path, _ = repo
    output = tmp_path / "junit.xml"
    assert (
        selection.run_tests(
            ("tests/test_pkg.py", "tests/test_sub.py"),
            root=path,
            framework="pytest",
            junit_path=output,
        )
        == 0
    )
    assert "testsuite" in output.read_text(encoding="utf-8")
    (path / "tests/test_sub.py").write_text(
        "import unittest\n\n\nclass F(unittest.TestCase):\n"
        "    def test_fail(self):\n        self.fail('nope')\n",
        encoding="utf-8",
    )
    failing = tmp_path / "failing.xml"
    assert (
        selection.run_tests(
            ("tests/test_pkg.py", "tests/test_sub.py"),
            root=path,
            framework="pytest",
            junit_path=failing,
        )
        != 0
    )
    assert failing.is_file()


def test_check_scope_separates_documents_from_business_tests(repo):
    path, base = repo
    (path / "docs/guide.md").write_text("# changed\n", encoding="utf-8")
    result = select(path, base)
    plan = selection.plan(
        result,
        config=config(path),
        config_path=path / "ci-selection.toml",
        root=path,
    )
    scope = {item["name"]: item for item in plan["check_scope"]}
    assert scope["unittest"]["status"] == "no_tests"
    assert scope["docs"]["status"] == "selected"
    assert "docs/guide.md" in scope["docs"]["inputs"]
    assert scope["tach"]["status"] == "not_applicable"


def test_weaver_scope_is_explicit_and_changes_with_its_inputs(repo):
    from dataclasses import replace

    path, base = repo
    (path / "telemetry").mkdir()
    (path / "telemetry/capture.json").write_text("{}")
    cfg = replace(config(path), checks=("weaver_registry", "weaver_live"))
    changes = selection.changeset(path, base=base)
    chosen = selection.select(changes, cfg, root=path)
    value = selection.plan(
        chosen, config=cfg, config_path=path / "ci-selection.toml", root=path
    )
    scope = {item["name"]: item for item in value["check_scope"]}
    assert scope["weaver_registry"]["status"] == "selected"
    assert scope["weaver_live"]["inputs"] == ["telemetry/capture.json"]
    assert "weaver_terminal" not in scope


def test_weaver_terminal_requires_post_completion_plan(repo):
    from dataclasses import replace

    path, base = repo
    (path / "telemetry").mkdir()
    (path / "telemetry/capture.json").write_text("{}")
    (path / "telemetry/expected.json").write_text("{}")
    cfg = replace(
        config(path), checks=("weaver_registry", "weaver_live", "weaver_terminal")
    )
    capture_plan = selection.plan(
        selection.select(selection.changeset(path, base=base), cfg, root=path),
        config=cfg,
        config_path=path / "ci-selection.toml",
        root=path,
    )
    capture_scope = {item["name"]: item for item in capture_plan["check_scope"]}
    assert capture_scope["weaver_live"]["status"] == "selected"
    assert capture_scope["weaver_terminal"]["status"] == "not_applicable"
    assert capture_scope["weaver_terminal"]["reason"] == "requires post-completion plan"
    assert capture_scope["weaver_terminal"]["inputs"] == []


def test_post_completion_plan_binds_terminal_inputs(tmp_path):
    fixture = ROOT / "tests/fixtures/weaver-cicd"
    if not fixture.is_dir():
        pytest.skip("weaver-cicd fixture unavailable")
    root = tmp_path / "src"
    shutil.copytree(fixture, root)
    plan = selection.post_completion_plan(
        root=root,
        config_path=root / "ci-selection.toml",
        weaver_config="ci-weaver.toml",
        run_id="controlled-42/1",
        attempt=1,
        result="success",
    )
    assert plan["phase"] == selection.POST_COMPLETION_PHASE
    assert plan["check_scope"][0]["name"] == "weaver_terminal"
    assert plan["check_scope"][0]["status"] == "selected"
    binding = plan["post_completion"]
    assert binding["run_id"] == "controlled-42/1"
    assert binding["otlp"]["path"] == "capture.json"
    assert binding["expectations"]["path"] == "expected.json"
    assert binding["registry"]["sha256"]
    assert binding["advice_policies"]["files"]["cicd-error-type.rego"]
    assert binding["sha256"]
    # Plan closure and Dagger download share engineering_ci.weaver_tool.
    assert binding["weaver_tool"] == weaver_tool.RECORD
    gate = selection.validate_post_completion_binding(plan, root)
    assert gate["ok"] and gate["input_closure_verified"] == "verified"
    assert gate["plan_closure_digest"] == binding["sha256"]

    blocked = dict(plan)
    blocked["phase"] = None
    gate = selection.validate_post_completion_binding(blocked, root)
    assert not gate["ok"] and gate["reason"] == "post_completion_phase_required"

    missing = dict(plan)
    missing["post_completion"] = dict(plan["post_completion"])
    del missing["post_completion"]["otlp"]
    gate = selection.validate_post_completion_binding(missing, root)
    assert not gate["ok"] and gate["reason"] == "post_completion_binding_incomplete"

    drifted = dict(plan)
    drifted["post_completion"] = dict(plan["post_completion"])
    (root / "advice-policies/cicd-error-type.rego").write_text(
        (root / "advice-policies/cicd-error-type.rego").read_text() + "\n# drift\n"
    )
    gate = selection.validate_post_completion_binding(drifted, root)
    assert not gate["ok"]
    assert gate["reason"] == "post_completion_closure_digest_mismatch"


def test_directory_content_id_uses_platform_independent_path_order(tmp_path):
    import hashlib

    files = {"Z.txt": b"upper", "a-b.txt": b"dash", "a/child.txt": b"child"}
    for name, data in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    expected = hashlib.sha256()
    for name in sorted(files):
        expected.update(name.encode() + b"\0" + files[name] + b"\0")
    assert selection.directory_content_id(tmp_path) == expected.hexdigest()


def test_incomplete_weaver_scope_remains_unknown(repo):
    from dataclasses import replace

    path, _ = repo
    cfg = replace(config(path), checks=("weaver_terminal",))
    chosen = selection.select(
        selection.changeset(path, base="missing-base"), cfg, root=path
    )
    value = selection.plan(
        chosen, config=cfg, config_path=path / "ci-selection.toml", root=path
    )
    scope = {item["name"]: item for item in value["check_scope"]}
    assert scope["weaver_terminal"]["status"] == "unknown"


def test_check_scope_rechecks_all_documents_after_deletion(repo):
    path, base = repo
    (path / "docs/guide.md").unlink()
    result = select(path, base)
    plan = selection.plan(
        result,
        config=config(path),
        config_path=path / "ci-selection.toml",
        root=path,
    )
    scope = {item["name"]: item for item in plan["check_scope"]}
    assert scope["docs"]["status"] == "selected"
    assert scope["docs"]["recheck_all"] is True


def test_check_scope_marks_code_checks_for_code_changes(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 5\n", encoding="utf-8")
    result = select(path, base)
    plan = selection.plan(
        result,
        config=config(path),
        config_path=path / "ci-selection.toml",
        root=path,
    )
    scope = {item["name"]: item for item in plan["check_scope"]}
    assert scope["unittest"]["status"] == "targeted"
    assert scope["tach"]["status"] == "selected"
    assert scope["vulture"]["status"] == "selected"
    assert scope["docs"]["status"] == "not_applicable"


def test_incomplete_git_scope_is_unknown_not_not_applicable(repo):
    path, _ = repo
    result = selection.select(
        selection.changeset(path, base="missing-base"),
        config(path),
        root=path,
    )
    plan = selection.plan(
        result,
        config=config(path),
        config_path=path / "ci-selection.toml",
        root=path,
    )
    scope = {item["name"]: item for item in plan["check_scope"]}
    assert result.escalated is True
    for name in ("docs", "tach", "vulture", "source"):
        assert scope[name]["status"] == "unknown"
    assert plan["selection"]["tach_graph_validation"]["status"] == "not_executed"


def test_tach_validation_is_bound_to_config_and_tree(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 20\n", encoding="utf-8")
    result = select(path, base)
    tree = result.changeset.execution_tree or result.changeset.tree
    valid = {
        "status": "passed",
        "exit_code": 0,
        "tach_sha256": selection._digest(path / "tach.toml"),
        "source_tree": tree,
    }
    plan = selection.plan(
        result,
        config=config(path),
        config_path=path / "ci-selection.toml",
        root=path,
        tach_validation=valid,
    )
    assert plan["selection"]["tach_graph_validation"]["status"] == "passed"
    stale = selection.plan(
        result,
        config=config(path),
        config_path=path / "ci-selection.toml",
        root=path,
        tach_validation={**valid, "source_tree": "0" * 40},
    )
    assert stale["selection"]["tach_graph_validation"] == {
        "status": "untrusted",
        "reason": "source_tree_mismatch",
    }


def test_escalation_without_discovered_tests_refuses_success(repo):
    path, base = repo
    shutil.rmtree(path / "tests")
    (path / "unknown.bin").write_bytes(b"x")
    result = select(path, base)
    assert result.status == "escalated"
    assert result.tests == ()
    assert selection.run_tests((), root=path) != 0
    assert (
        selection.main(
            [
                "--root",
                str(path),
                "--base",
                base,
                "--run",
                "--config",
                str(path / "ci-selection.toml"),
            ]
        )
        != 0
    )


def test_index_snapshot_excludes_unstaged_worktree_changes(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 6\n", encoding="utf-8")
    run(path, "add", "src/pkg/sub.py")
    (path / "src/pkg/other.py").write_text("VALUE = 7\n", encoding="utf-8")
    index = changes(path, base, mode="index")
    assert "src/pkg/sub.py" in index.paths
    assert "src/pkg/other.py" not in index.paths
    assert index.snapshot == "index"
    worktree = changes(path, base, mode="worktree")
    assert {"src/pkg/sub.py", "src/pkg/other.py"} <= set(worktree.paths)


def test_changeset_index_file_reads_alternate_index(repo, tmp_path):
    path, base = repo
    (path / "docs/new.md").write_text("# new\n", encoding="utf-8")
    index = tmp_path / "index-b"
    env = {**os.environ, "GIT_INDEX_FILE": str(index)}
    subprocess.run(
        ["git", "read-tree", "HEAD"], cwd=path, env=env, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "add", "docs/new.md"],
        cwd=path,
        env=env,
        check=True,
        capture_output=True,
    )
    external_tree = subprocess.run(
        ["git", "write-tree"],
        cwd=path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    default_tree = run(path, "write-tree").strip()
    assert external_tree != default_tree
    changeset = selection.changeset(path, base=base, mode="index", index_file=index)
    assert changeset.index_tree == external_tree
    assert "docs/new.md" in changeset.paths


def test_push_snapshot_uses_before_after_commits(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 8\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "first")
    first = run(path, "rev-parse", "HEAD").strip()
    (path / "src/pkg/other.py").write_text("VALUE = 9\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "second")
    second = run(path, "rev-parse", "HEAD").strip()
    pushed = changes(path, base=base, mode="push", before=first, after=second)
    assert pushed.snapshot == "push"
    assert pushed.base_commit == first
    assert pushed.head_commit == second
    assert pushed.paths == ("src/pkg/other.py",)


def test_pr_merge_snapshot_records_merge_tree(repo):
    path, base = repo
    run(path, "checkout", "-q", "-b", "feature")
    (path / "src/pkg/sub.py").write_text("VALUE = 10\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "feature")
    feature = run(path, "rev-parse", "HEAD").strip()
    merged = changes(path, base=base, head=feature, mode="pr")
    if not merged.complete:
        pytest.skip("git merge-tree is unavailable")
    assert merged.snapshot == "pr_merge"
    assert merged.merge_tree
    assert "src/pkg/sub.py" in merged.paths


def test_execution_refuses_a_different_tree(repo):
    path, _ = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 11\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "target")
    target = run(path, "rev-parse", "HEAD").strip()
    tree = run(path, "rev-parse", f"{target}^{{tree}}").strip()
    (path / "src/pkg/sub.py").write_text("VALUE = 12\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "newer")
    assert selection.run_tests(("tests.test_pkg",), root=path, expected_tree=tree) == 4


def test_multi_ref_union_refuses_single_execution(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 21\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "first")
    first = run(path, "rev-parse", "HEAD").strip()
    (path / "src/pkg/other.py").write_text("VALUE = 22\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "second")
    second = run(path, "rev-parse", "HEAD").strip()
    multi = selection.changeset_multi(path, [(base, first), (first, second)])
    assert multi.snapshot == "multi"
    assert multi.execution_tree is None
    assert multi.paths == ("src/pkg/other.py", "src/pkg/sub.py")
    assert len(multi.references) == 2
    plan = {
        "schema_version": 1,
        "kind": selection.PLAN_KIND,
        "generator": {},
        "git": {"snapshot": "multi"},
        "selection": {"tests": ["tests.test_pkg"], "framework": "unittest"},
    }
    report = execution.execute(plan, root=path)
    assert report["status"] == "not_executed"
    assert report["reason"] == "multi_ref_requires_per_ref_execution"


def test_overlapping_rules_prioritize_exact_and_do_not_shadow(repo):
    path, base = repo
    config_path = path / "ci-selection.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + '\n[[non_code_rules]]\nexact = "data/input.json"\n'
        'tests = ["tests.test_sub"]\nreason = "exact data input"\n',
        encoding="utf-8",
    )
    run(path, "add", "ci-selection.toml")
    run(path, "commit", "-q", "-m", "exact rule")
    base = run(path, "rev-parse", "HEAD").strip()
    (path / "data").mkdir()
    (path / "data/input.json").write_text("{}\n", encoding="utf-8")
    result = select(path, base)
    assert result.tests == ("tests.test_sub",)


def test_nested_test_discovery_and_direct_mapping(repo):
    path, base = repo
    nested = path / "tests/unit"
    nested.mkdir(parents=True)
    (nested / "test_nested.py").write_text(
        "import unittest\n\n\nclass T(unittest.TestCase):\n"
        "    def test_ok(self):\n        self.assertTrue(True)\n",
        encoding="utf-8",
    )
    full = select(path, base, full=True)
    assert "tests.unit.test_nested" in full.tests
    (nested / "test_nested.py").write_text(
        (nested / "test_nested.py").read_text(encoding="utf-8") + "\n# changed\n",
        encoding="utf-8",
    )
    targeted = select(path, base)
    assert targeted.tests == ("tests.unit.test_nested",)


def test_pr_merge_uses_base_tip_not_merge_base(repo):
    path, _ = repo
    run(path, "checkout", "-q", "-b", "feature")
    (path / "src/pkg/sub.py").write_text("VALUE = 30\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "feature")
    feature = run(path, "rev-parse", "HEAD").strip()
    run(path, "checkout", "-q", "main")
    (path / "src/pkg/other.py").write_text("VALUE = 31\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "main moves")
    base_tip = run(path, "rev-parse", "HEAD").strip()
    merged = changes(path, base=base_tip, head=feature, mode="pr")
    if not merged.complete:
        pytest.skip("git merge-tree is unavailable")
    assert merged.base_tip == base_tip
    assert merged.base_commit != base_tip
    assert merged.merge_tree != run(path, "rev-parse", f"{feature}^{{tree}}").strip()
    assert merged.paths == ("src/pkg/sub.py",)


def test_cli_prints_plan_and_rejects_run_with_json(repo, capsys):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 4\n", encoding="utf-8")
    assert (
        selection.main(
            [
                "--root",
                str(path),
                "--base",
                base,
                "--json",
                "--config",
                str(path / "ci-selection.toml"),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "engineering-ci-selection-plan"
    assert "tests.test_sub" in payload["selection"]["tests"]

    with pytest.raises(SystemExit) as error:
        selection.main(
            [
                "--root",
                str(path),
                "--base",
                base,
                "--json",
                "--run",
            ]
        )
    assert error.value.code == 2


def test_cli_run_without_tests_is_not_success(repo):
    path, base = repo
    assert (
        selection.main(
            [
                "--root",
                str(path),
                "--base",
                base,
                "--run",
                "--config",
                str(path / "ci-selection.toml"),
            ]
        )
        == 3
    )
