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


def changes(repo, base, *, head="HEAD", include_worktree=True):
    return selection.changeset(
        repo, base=base, head=head, include_worktree=include_worktree
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
