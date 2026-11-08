"""Generic Git impact selection, plan identity and native unittest execution."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from eci_core import selection

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
    assert plan["schema_version"] == 1
    assert plan["kind"] == "engineering-ci-selection-plan"
    assert plan["git"]["base_commit"] is not None
    assert plan["git"]["head_commit"] == base
    assert plan["git"]["worktree_dirty"] is True
    assert "src/pkg/sub.py" in plan["git"]["changed_paths"]
    assert plan["generator"]["config_sha256"]
    assert plan["generator"]["tach_sha256"]
    assert plan["selection"]["tests"] == list(result.tests)
    assert plan["selection"]["prerequisites"] == ["identity_commit", "wheel_smoke"]


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
    assert plan["selection"]["tach_graph_validation"] == "not_executed"


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
