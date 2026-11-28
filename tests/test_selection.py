"""Generic Git impact selection, plan identity and native unittest execution."""

import dataclasses
import hashlib
import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from engineering_ci import execution, plan_contract, selection, weaver_tool

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
        ["git", "-c", "core.autocrlf=false", "checkout-index", "-a", "-f"],
        cwd=export,
        env=env,
        check=True,
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


@pytest.mark.parametrize("bad_image", [False, True])
def test_selection_preserves_explicit_native_producer_inputs(tmp_path, bad_image):
    image = (
        "python:latest" if bad_image else "example.invalid/python@sha256:" + "a" * 64
    )
    path = tmp_path / "ci-selection.toml"
    path.write_text(
        "schema_version = 1\n[prerequisite_actions.build]\n"
        'type = "dagger_command"\nargv = ["python", "build.py"]\n'
        f'image = "{image}"\nplatform = "linux/amd64"\ncache = "source"\n',
        encoding="utf-8",
    )
    if bad_image:
        with pytest.raises(
            selection.SelectionError, match="dagger_command_parameters_invalid"
        ):
            selection.load_config(path)
    else:
        spec = selection.load_config(path).prerequisite_actions["build"]
        assert spec == {
            "type": "dagger_command",
            "argv": ["python", "build.py"],
            "image": image,
            "platform": "linux/amd64",
            "cache": "source",
        }


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
    path, base = repo
    (path / "telemetry").mkdir()
    (path / "telemetry/capture.json").write_text("{}")
    sel_text = (path / "ci-selection.toml").read_text(encoding="utf-8")
    (path / "ci-selection.toml").write_text(
        sel_text.replace(
            'checks = ["hygiene", "tach", "vulture"]',
            'checks = ["weaver_registry", "weaver_live"]',
        ),
        encoding="utf-8",
    )
    cfg = selection.load_config(path / "ci-selection.toml")
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
    path, base = repo
    (path / "telemetry").mkdir()
    (path / "telemetry/capture.json").write_text("{}")
    (path / "telemetry/expected.json").write_text("{}")
    sel_text = (path / "ci-selection.toml").read_text(encoding="utf-8")
    (path / "ci-selection.toml").write_text(
        sel_text.replace(
            'checks = ["hygiene", "tach", "vulture"]',
            'checks = ["weaver_registry", "weaver_live", "weaver_terminal"]',
        ),
        encoding="utf-8",
    )
    cfg = selection.load_config(path / "ci-selection.toml")
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
    path, _ = repo
    sel_text = (path / "ci-selection.toml").read_text(encoding="utf-8")
    (path / "ci-selection.toml").write_text(
        sel_text.replace(
            'checks = ["hygiene", "tach", "vulture"]',
            'checks = ["weaver_terminal"]',
        ),
        encoding="utf-8",
    )
    cfg = selection.load_config(path / "ci-selection.toml")
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


def test_changeset_index_file_refuses_worktree_mismatch(repo, tmp_path):
    path, base = repo
    (path / "docs/indexed.md").write_text("# indexed\n", encoding="utf-8")
    index = tmp_path / "index-b"
    env = {**os.environ, "GIT_INDEX_FILE": str(index)}
    subprocess.run(
        ["git", "read-tree", "HEAD"], cwd=path, env=env, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "add", "docs/indexed.md"],
        cwd=path,
        env=env,
        check=True,
        capture_output=True,
    )
    (path / "docs/indexed.md").write_text("# worktree\n", encoding="utf-8")
    changeset = selection.changeset(path, base=base, mode="index", index_file=index)
    assert changeset.source_refusal == "index_worktree_mismatch"
    assert changeset.complete is False
    with pytest.raises(selection.SelectionError, match="index_worktree_mismatch"):
        selection.select(changeset, config(path), root=path)


def test_changeset_index_write_tree_failure_is_explicit(repo, tmp_path):
    path, base = repo
    missing = tmp_path / "missing" / "index"
    changeset = selection.changeset(path, base=base, mode="index", index_file=missing)
    assert changeset.index_tree is None
    assert changeset.source_refusal == "index_tree_unavailable"


def test_index_source_refusal_rejects_git_query_failure(repo, monkeypatch):
    path, _ = repo
    tree = run(path, "write-tree").strip()
    real = selection._git

    def fake(root, *args, env=None):
        if args and args[0] == "diff":
            return subprocess.CompletedProcess(args, 2, "", "git failure")
        return real(root, *args, env=env)

    monkeypatch.setattr(selection, "_git", fake)
    assert selection.index_source_refusal(path, tree) == "index_git_check_failed"


def test_select_run_refuses_index_worktree_mismatch(repo, tmp_path):
    path, base = repo
    (path / "docs/indexed.md").write_text("# indexed\n", encoding="utf-8")
    index = tmp_path / "index-b"
    env = {**os.environ, "GIT_INDEX_FILE": str(index)}
    subprocess.run(
        ["git", "read-tree", "HEAD"], cwd=path, env=env, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "add", "docs/indexed.md"],
        cwd=path,
        env=env,
        check=True,
        capture_output=True,
    )
    (path / "docs/indexed.md").write_text("# worktree\n", encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        selection.main(
            [
                "--root",
                str(path),
                "--config",
                "ci-selection.toml",
                "--base",
                base,
                "--mode",
                "index",
                "--index-file",
                str(index),
                "--run",
            ]
        )
    assert raised.value.code == 4


def test_index_source_refusal_allows_eol_checkout_representation(repo):
    path, _ = repo
    run(path, "config", "core.autocrlf", "false")
    (path / ".gitattributes").write_text("*.txt text eol=crlf\n", encoding="utf-8")
    (path / "input.txt").write_text("one\ntwo\n", encoding="utf-8")
    run(path, "add", ".gitattributes", "input.txt")
    run(path, "commit", "-q", "-m", "attributes")
    tree = run(path, "write-tree").strip()
    (path / "input.txt").write_bytes(b"one\r\ntwo\r\n")
    assert (
        subprocess.run(["git", "diff", "--quiet"], cwd=path, check=False).returncode
        == 0
    )
    assert selection.index_source_refusal(path, tree) is None


@pytest.mark.parametrize(
    ("flag", "expected_reason"),
    [
        pytest.param(
            "--skip-worktree",
            "index_skip_worktree_bytes_mismatch",
            id="skip-worktree",
        ),
        pytest.param(
            "--assume-unchanged",
            "index_assume_unchanged_bytes_mismatch",
            id="assume-unchanged",
        ),
    ],
)
def test_index_source_refusal_rejects_worktree_metadata_bytes(
    repo, flag, expected_reason
):
    path, _ = repo
    tree = run(path, "write-tree").strip()
    run(path, "update-index", flag, "src/pkg/sub.py")
    (path / "src/pkg/sub.py").write_text("VALUE = 99\n", encoding="utf-8")
    assert (
        subprocess.run(["git", "diff", "--quiet"], cwd=path, check=False).returncode
        == 0
    )
    assert selection.index_source_refusal(path, tree) == expected_reason


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


def test_push_policy_disables_test_selection_only_for_push(repo):
    path, base = repo
    config_path = path / "ci-selection.toml"
    with config_path.open("ab") as stream:
        stream.write(b"\n[push]\nselect_tests = false\n")
    cfg = config(path)
    restored = selection.config_from_identity(
        selection.config_identity(config_path, cfg)
    )
    assert restored.push.select_tests is False
    (path / "src/pkg/sub.py").write_bytes(b"VALUE = 8\n")
    run(path, "add", "src/pkg/sub.py")
    run(path, "commit", "-qm", "Change mapped source")
    head = run(path, "rev-parse", "HEAD").strip()
    pushed = changes(path, base=base, mode="push", before=base, after=head)

    assert selection.select(pushed, restored, root=path).status == "no_tests"
    assert (
        "tests.test_sub"
        in selection.select(pushed, restored, root=path, full=True).tests
    )
    committed = selection.changeset(path, base=base, head=head, mode="commit")
    assert "tests.test_sub" in selection.select(committed, restored, root=path).tests


def test_commit_policy_disables_index_test_selection_only(repo):
    path, base = repo
    config_path = path / "ci-selection.toml"
    with config_path.open("ab") as stream:
        stream.write(b"\n[commit]\nselect_tests = false\n")
    (path / "src/pkg/sub.py").write_bytes(b"VALUE = 8\n")
    run(path, "add", "ci-selection.toml", "src/pkg/sub.py")
    cfg = config(path)
    restored = selection.config_from_identity(
        selection.config_identity(config_path, cfg)
    )
    assert restored.commit.select_tests is False
    indexed = selection.changeset(path, base=base, mode="index")
    assert indexed.snapshot == "index"
    assert indexed.source_refusal is None
    selected = selection.select(indexed, restored, root=path)
    assert selected.status == "no_tests"
    assert selected.tests == ()
    assert (
        "tests.test_sub"
        in selection.select(indexed, restored, root=path, full=True).tests
    )
    run(path, "commit", "-qm", "Change mapped source")
    head = run(path, "rev-parse", "HEAD").strip()
    committed = selection.changeset(path, base=base, head=head, mode="commit")
    assert "tests.test_sub" in selection.select(committed, restored, root=path).tests
    pushed = changes(path, base=base, mode="push", before=base, after=head)
    assert "tests.test_sub" in selection.select(pushed, restored, root=path).tests


def test_commit_policy_normalize_omits_default_and_binds_false(repo):
    path, _base = repo
    config_path = path / "ci-selection.toml"
    base_text = config_path.read_text(encoding="utf-8")

    undeclared = config(path)
    undeclared_norm = selection._normalize_config(undeclared)
    assert "commit" not in undeclared_norm
    undeclared_identity = selection.config_identity(config_path, undeclared)
    assert (
        selection.verify_config_identity_matches_file(
            undeclared_identity, root=path
        ).commit.select_tests
        is True
    )

    config_path.write_text(
        base_text + "\n[commit]\nselect_tests = true\n", encoding="utf-8"
    )
    explicit_true = config(path)
    true_norm = selection._normalize_config(explicit_true)
    assert "commit" not in true_norm
    assert true_norm == undeclared_norm
    true_identity = selection.config_identity(config_path, explicit_true)
    assert (
        selection.verify_config_identity_matches_file(
            true_identity, root=path
        ).commit.select_tests
        is True
    )

    config_path.write_text(
        base_text + "\n[commit]\nselect_tests = false\n", encoding="utf-8"
    )
    disabled = config(path)
    disabled_norm = selection._normalize_config(disabled)
    assert disabled_norm["commit"] == {"select_tests": False}
    disabled_identity = selection.config_identity(config_path, disabled)
    restored_false = selection.verify_config_identity_matches_file(
        disabled_identity, root=path
    )
    assert restored_false.commit.select_tests is False
    assert disabled_identity["normalized"] != undeclared_identity["normalized"]
    assert (
        disabled_identity["effective_config_ref"]
        != undeclared_identity["effective_config_ref"]
    )


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


def test_dependency_edges_config_activates_prerequisites_and_emits_v2_graph(repo):
    path, base = repo
    selection_toml = path / "ci-selection.toml"
    selection_toml.write_text(
        selection_toml.read_text(encoding="utf-8")
        + "\n[prerequisite_actions.gen_docs]\n"
        + 'type = "command"\n'
        + 'argv = ["python", "-c", "pass"]\n\n'
        + "[[dependency_edges]]\n"
        + 'producer_ref = { kind = "prerequisite_action", name = "gen_docs" }\n'
        + 'consumer_ref = { kind = "check", name = "docs" }\n'
        + 'dependency_kind = "data"\n'
        + "requires_pass = true\n"
        + 'output_ref = { kind = "file", path = "docs/generated.md" }\n\n'
        + "[[dependency_edges]]\n"
        + 'producer_ref = { kind = "prerequisite_action", name = "gen_docs" }\n'
        + 'consumer_ref = { kind = "execution_report", name = "artifacts" }\n'
        + 'dependency_kind = "data"\n'
        + "requires_pass = true\n"
        + 'output_ref = { kind = "file", path = "docs/generated.md" }\n\n'
        + "[[dependency_edges]]\n"
        + 'producer_ref = { kind = "check", name = "tach" }\n'
        + 'consumer_ref = { kind = "tests" }\n'
        + 'dependency_kind = "pre_execution_pass"\n',
        encoding="utf-8",
    )
    run(path, "add", "ci-selection.toml")
    run(path, "commit", "-q", "-m", "configure dependency edges")
    base = run(path, "rev-parse", "HEAD").strip()

    # Document-only change activates gen_docs -> docs + execution_report/artifacts, not tach -> tests.
    (path / "docs/guide.md").write_text("# Updated\n", encoding="utf-8")
    doc_sel = select(path, base)
    assert doc_sel.prerequisites == frozenset({"gen_docs"})
    doc_plan = selection.plan(
        doc_sel, config=config(path), config_path=selection_toml, root=path
    )
    execution.validate_plan(doc_plan)
    doc_contract = doc_plan["dependency_contract"]
    assert doc_contract["schema_version"] == 2
    assert {
        (e["producer_ref"]["name"], e["consumer_ref"]["kind"])
        for e in doc_contract["edges"]
    } == {("gen_docs", "check"), ("gen_docs", "execution_report")}

    # Code change activates tach -> tests + [prerequisites] policy_only edges, without gen_docs.
    run(path, "checkout", "--", "docs/guide.md")
    (path / "src/pkg/sub.py").write_text("VALUE = 2\n", encoding="utf-8")
    code_sel = select(path, base)
    assert "gen_docs" not in code_sel.prerequisites
    assert "identity_commit" in code_sel.prerequisites
    code_plan = selection.plan(
        code_sel, config=config(path), config_path=selection_toml, root=path
    )
    execution.validate_plan(code_plan)
    code_contract = code_plan["dependency_contract"]
    assert code_contract["schema_version"] == 2
    assert any(
        e["producer_ref"] == {"kind": "check", "name": "tach"}
        and e["consumer_ref"] == {"kind": "tests"}
        and e["dependency_kind"] == "pre_execution_pass"
        for e in code_contract["edges"]
    )
    assert any(
        e["producer_ref"] == {"kind": "prerequisite_action", "name": "identity_commit"}
        and e["dependency_kind"] == "policy_only"
        for e in code_contract["edges"]
    )


def test_dependency_edges_preserve_unresolved_action_and_reject_invalid(repo):
    path, base = repo
    selection_toml = path / "ci-selection.toml"
    base_text = selection_toml.read_text(encoding="utf-8")
    selection_toml.write_text(
        base_text
        + "\n[[dependency_edges]]\n"
        + 'producer_ref = { kind = "prerequisite_action", name = "missing_gen" }\n'
        + 'consumer_ref = { kind = "check", name = "docs" }\n'
        + 'dependency_kind = "data"\n'
        + 'output_ref = { kind = "file", path = "docs/generated.md" }\n',
        encoding="utf-8",
    )
    (path / "docs/guide.md").write_text("# Dirty doc\n", encoding="utf-8")
    sel = select(path, base)
    value = selection.plan(
        sel, config=config(path), config_path=selection_toml, root=path
    )
    execution.validate_plan(value)
    edge = value["dependency_contract"]["edges"][0]
    assert edge["resolution"] == "unresolved"
    assert edge["unresolved_reason"] == "action_parameters_missing"

    for bad_snippet in (
        (
            'producer_ref = { kind = "check", name = "tach" }\n'
            'consumer_ref = { kind = "tests" }\n'
            'dependency_kind = "data"\n'
            'output_ref = { kind = "file", path = "out.txt" }\n'
        ),
        (
            'producer_ref = { kind = "prerequisite_action", name = "gen" }\n'
            'consumer_ref = { kind = "tests" }\n'
            'dependency_kind = "data"\n'
            'output_ref = { kind = "file", path = "../escape.txt" }\n'
        ),
        (
            'producer_ref = { kind = "check", name = "docs" }\n'
            'consumer_ref = { kind = "check", name = "docs" }\n'
            'dependency_kind = "pre_execution_pass"\n'
        ),
    ):
        selection_toml.write_text(
            base_text + "\n[[dependency_edges]]\n" + bad_snippet,
            encoding="utf-8",
        )
        with pytest.raises(selection.SelectionError, match="dependency_edges_invalid"):
            selection.load_config(selection_toml)


def test_plan_recomputes_dependency_prerequisites_from_final_scope(repo, tmp_path):
    path, base = repo
    hygiene_toml = path / "ci-hygiene.toml"
    hygiene_toml.write_text(
        ('schema_version = 1\nchecks = ["whitespace", "docs", "tach", "vulture"]\n'),
        encoding="utf-8",
    )
    selection_toml = path / "ci-selection.toml"
    selection_toml.write_text(
        selection_toml.read_text(encoding="utf-8")
        + "\n[prerequisite_actions.gen_docs]\n"
        + 'type = "command"\n'
        + 'argv = ["python", "-c", "pass"]\n\n'
        + "[[dependency_edges]]\n"
        + 'producer_ref = { kind = "prerequisite_action", name = "gen_docs" }\n'
        + 'consumer_ref = { kind = "check", name = "docs" }\n'
        + 'dependency_kind = "data"\n'
        + "requires_pass = true\n"
        + 'output_ref = { kind = "file", path = "docs/generated.md" }\n\n'
        + "[[dependency_edges]]\n"
        + 'producer_ref = { kind = "prerequisite_action", name = "gen_docs" }\n'
        + 'consumer_ref = { kind = "execution_report", name = "artifacts" }\n'
        + 'dependency_kind = "data"\n'
        + "requires_pass = true\n"
        + 'output_ref = { kind = "file", path = "docs/generated.md" }\n',
        encoding="utf-8",
    )
    run(path, "add", "ci-hygiene.toml", "ci-selection.toml")
    run(path, "commit", "-q", "-m", "configure dependency prerequisite scope test")
    base = run(path, "rev-parse", "HEAD").strip()

    # Repo default enables docs -> select() activates dependency-only gen_docs alongside test-mapped identity_commit.
    (path / "docs/guide.md").write_text("# Updated\n", encoding="utf-8")
    (path / "src/pkg/sub.py").write_text("VALUE = 3\n", encoding="utf-8")
    cfg = config(path)
    sel = select(path, base)
    assert {"gen_docs", "identity_commit", "wheel_smoke"} <= sel.prerequisites
    assert sel.base_prerequisites == frozenset({"identity_commit", "wheel_smoke"})

    # External policy disables docs -> gen_docs must be retracted while test-mapped prerequisites stay.
    ext_off = tmp_path / "ext-hygiene-off.toml"
    ext_off.write_text(
        'schema_version = 1\nchecks = ["whitespace", "tach", "vulture"]\n',
        encoding="utf-8",
    )
    plan_off = selection.plan(
        sel,
        config=cfg,
        config_path=selection_toml,
        root=path,
        hygiene_config_path=ext_off,
    )
    execution.validate_plan(plan_off)
    assert plan_off["selection"]["prerequisites"] == ["identity_commit", "wheel_smoke"]
    assert "gen_docs" not in {
        edge["producer_ref"]["name"]
        for edge in plan_off["dependency_contract"]["edges"]
    }

    # Reverse: repo default disables docs -> select() excludes gen_docs; external policy enables docs -> plan() activates it.
    hygiene_toml.write_text(
        'schema_version = 1\nchecks = ["whitespace", "tach", "vulture"]\n',
        encoding="utf-8",
    )
    run(path, "add", "ci-hygiene.toml")
    run(path, "commit", "-q", "-m", "disable docs in repo default")
    base2 = run(path, "rev-parse", "HEAD").strip()
    sel_off = select(path, base2)
    assert "gen_docs" not in sel_off.prerequisites
    assert sel_off.base_prerequisites == frozenset({"identity_commit", "wheel_smoke"})

    ext_on = tmp_path / "ext-hygiene-on.toml"
    ext_on.write_text(
        'schema_version = 1\nchecks = ["whitespace", "docs", "tach", "vulture"]\n',
        encoding="utf-8",
    )
    plan_on = selection.plan(
        sel_off,
        config=cfg,
        config_path=selection_toml,
        root=path,
        hygiene_config_path=ext_on,
    )
    execution.validate_plan(plan_on)
    assert plan_on["selection"]["prerequisites"] == [
        "gen_docs",
        "identity_commit",
        "wheel_smoke",
    ]
    assert plan_on["dependency_contract"]["schema_version"] == 2
    assert {
        (edge["producer_ref"]["name"], edge["consumer_ref"]["kind"])
        for edge in plan_on["dependency_contract"]["edges"]
        if edge["dependency_kind"] == "data"
    } == {("gen_docs", "check"), ("gen_docs", "execution_report")}


def test_linked_worktree_tree_content_id_and_worktree_tree_refusal(repo, tmp_path):
    path, base = repo
    linked = tmp_path / "linked-wt"
    run(
        path,
        "-c",
        "core.autocrlf=false",
        "worktree",
        "add",
        "-q",
        "-b",
        "wt-branch",
        str(linked),
        base,
    )
    try:
        tree = run(linked, "rev-parse", "HEAD^{tree}").strip()
        assert selection._resolve_git_dir(linked) is not None
        assert selection.tree_content_id(
            linked, tree
        ) == selection.directory_content_id(linked)
        assert selection.worktree_tree_refusal(linked, tree) is None

        (linked / "src/pkg/sub.py").write_text("VALUE = 99\n", encoding="utf-8")
        assert selection.worktree_tree_refusal(linked, tree) == "worktree_tree_mismatch"
        run(linked, "update-index", "--skip-worktree", "src/pkg/sub.py")
        assert (
            selection.worktree_tree_refusal(linked, tree)
            == "checkout_skip_worktree_bytes_mismatch"
        )
        assert (
            selection.run_tests(("tests.test_pkg",), root=linked, expected_tree=tree)
            == 4
        )
        tach_result = selection._tach_check(
            linked,
            config(linked),
            selection.changeset(linked, base=base, head="HEAD", mode="commit"),
        )
        assert tach_result["status"] == "refused"
        assert tach_result["reason"] == "checkout_skip_worktree_bytes_mismatch"
        run(linked, "update-index", "--no-skip-worktree", "src/pkg/sub.py")
        run(linked, "checkout", "--", "src/pkg/sub.py")

        run(linked, "update-index", "--assume-unchanged", "src/pkg/sub.py")
        (linked / "src/pkg/sub.py").write_text("VALUE = 100\n", encoding="utf-8")
        assert (
            selection.worktree_tree_refusal(linked, tree)
            == "checkout_assume_unchanged_bytes_mismatch"
        )
    finally:
        run(linked, "update-index", "--no-assume-unchanged", "src/pkg/sub.py")
        run(path, "worktree", "remove", "--force", str(linked))


def test_push_ref_classification_new_delete_force_missing_and_non_commit(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 2\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "second")
    second = run(path, "rev-parse", "HEAD").strip()

    # New ref without controlled policy baseline never assumes origin/main merge-base
    new_unknown = selection.changeset(
        path,
        base=selection.ZERO_OID,
        mode="push",
        before=selection.ZERO_OID,
        after=second,
        remote_ref="refs/heads/feature",
    )
    assert new_unknown.ref_action == "create"
    assert new_unknown.baseline_status == "unknown"
    assert new_unknown.complete is False
    assert new_unknown.error == "new_ref_baseline_unknown"

    # Explicit controlled baseline allows scoped diff
    new_policy = selection.changeset(
        path,
        base=selection.ZERO_OID,
        mode="push",
        before=selection.ZERO_OID,
        after=second,
        remote_ref="refs/heads/feature",
        new_ref_baseline=base,
    )
    assert new_policy.ref_action == "create"
    assert new_policy.baseline_status == "policy"
    assert new_policy.complete is True
    assert new_policy.paths == ("src/pkg/sub.py",)

    # Controlled [push] policy in ci-selection.toml resolves new_ref_baseline per target pattern
    policy_path = path / "ci-selection.toml"
    original_policy = policy_path.read_bytes()
    policy_path.write_bytes(
        original_policy
        + f'\n[push]\nnew_ref_baseline = "{base}"\n[push.target_baselines]\n"refs/heads/*" = "{base}"\n'.encode(),
    )
    push_cfg = selection.load_config(path / "ci-selection.toml")
    cfg_policy = selection.changeset(
        path,
        base=selection.ZERO_OID,
        mode="push",
        before=selection.ZERO_OID,
        after=second,
        remote_ref="refs/heads/feature",
        config=push_cfg,
    )
    assert cfg_policy.ref_action == "create"
    assert cfg_policy.baseline_status == "policy"
    assert cfg_policy.complete is True
    assert cfg_policy.paths == ("src/pkg/sub.py",)
    policy_path.write_bytes(original_policy)

    # Deleting remote ref marks content checks not_applicable and preserves ref_deletion obligation
    deleted_cs = selection.changeset(
        path,
        base=base,
        mode="push",
        before=base,
        after=selection.ZERO_OID,
        remote_ref="refs/heads/old",
    )
    assert deleted_cs.ref_action == "delete"
    assert deleted_cs.complete is True
    assert deleted_cs.non_check_obligations == ("ref_deletion",)
    deleted_sel = selection.select(deleted_cs, config(path), root=path)
    assert deleted_sel.status == "not_applicable"
    deleted_plan = selection.plan(
        deleted_sel,
        config=config(path),
        config_path=path / "ci-selection.toml",
        root=path,
    )
    scope = {item["name"]: item for item in deleted_plan["check_scope"]}
    assert scope["docs"]["status"] == "not_applicable"
    assert scope["docs"]["reason"] == "remote_ref_deleted"
    assert any(
        ob["kind"] == "ref_deletion"
        for ob in deleted_plan["expected_checks"].get("non_check_obligations", [])
    )

    # Non-fast-forward force push with available old OID computes exact diff
    run(path, "checkout", "-q", "-b", "alt", base)
    (path / "src/pkg/other.py").write_text("VALUE = 3\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "alt")
    alt = run(path, "rev-parse", "HEAD").strip()
    force_cs = selection.changeset(
        path, base=second, mode="push", before=second, after=alt
    )
    assert force_cs.ref_action == "force_update"
    assert force_cs.force_push is True
    assert force_cs.complete is True
    assert set(force_cs.paths) == {"src/pkg/other.py", "src/pkg/sub.py"}

    # Missing old OID stays incomplete without fake pass
    missing_old = selection.changeset(
        path, base="f" * 40, mode="push", before="f" * 40, after=alt
    )
    assert missing_old.complete is False
    assert missing_old.error == "old_object_unavailable"
    assert missing_old.baseline_status == "old_object_unavailable"

    # Non-commit object (tree/blob OID) is classified explicitly
    tree_oid = run(path, "rev-parse", f"{alt}^{{tree}}").strip()
    non_commit = selection.changeset(
        path, base=base, mode="push", before=base, after=tree_oid
    )
    assert non_commit.complete is False
    assert non_commit.error == "non_commit_object"
    assert non_commit.object_type == "tree"


def test_pre_push_stdin_and_plan_records_hygiene_and_sources_inputs(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 5\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "c1")
    c1 = run(path, "rev-parse", "HEAD").strip()

    parsed = selection.parse_pre_push_stdin(
        f"refs/heads/main {c1} refs/heads/main {base}\n"
        f"refs/heads/del {selection.ZERO_OID} refs/heads/del {base}\n"
    )
    multi = selection.changeset_multi(path, parsed)
    assert multi.snapshot == "multi"
    assert multi.base_commit is None
    assert multi.head_commit is None
    assert len(multi.references) == 2

    (path / "ci-hygiene.toml").write_text(
        'schema_version = 1\nchecks = ["whitespace", "forwarding", "docs", "vulture", "tach"]\n'
        "[tach]\nexternal = true\n",
        encoding="utf-8",
    )
    sel = select(path, base)
    p = selection.plan(
        sel, config=config(path), config_path=path / "ci-selection.toml", root=path
    )
    assert "hygiene" in p["inputs"]
    assert p["inputs"]["hygiene"]["path"] == "ci-hygiene.toml"
    scope = {item["name"]: item for item in p["check_scope"]}
    assert "whitespace" in scope
    assert "forwarding" in scope
    assert "tach_external" in scope


def test_p1_2_multi_ref_independent_per_ref_selection_and_scope(repo):
    path, base = repo
    (path / "src/pkg/other.py").write_text("# leaf change\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "targeted-src")
    src_commit = run(path, "rev-parse", "HEAD").strip()

    run(path, "checkout", "-q", base)
    (path / "docs/readme.md").write_text("# doc only\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "docs-only")
    docs_commit = run(path, "rev-parse", "HEAD").strip()

    run(path, "checkout", "-q", base)
    (path / "pyproject.toml").write_text(
        (path / "pyproject.toml").read_text(encoding="utf-8") + "\n# escalate\n",
        encoding="utf-8",
    )
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "escalated")
    esc_commit = run(path, "rev-parse", "HEAD").strip()

    multi = selection.changeset_multi(
        path,
        [
            ("refs/heads/src", src_commit, "refs/heads/src", base),
            ("refs/heads/docs", docs_commit, "refs/heads/docs", base),
            ("refs/heads/esc", esc_commit, "refs/heads/esc", base),
        ],
    )
    cfg = config(path)
    sel = selection.select(multi, cfg, root=path)
    assert sel.escalated is True
    assert sel.status == "escalated"
    per_ref_sel = {entry["remote_ref"]: entry for entry in sel.per_ref}
    assert per_ref_sel["refs/heads/src"]["status"] == "targeted"
    assert per_ref_sel["refs/heads/src"]["tests"] == ["tests.test_other"]
    assert per_ref_sel["refs/heads/docs"]["status"] == "no_tests"
    assert per_ref_sel["refs/heads/docs"]["tests"] == []
    assert per_ref_sel["refs/heads/esc"]["status"] == "escalated"
    assert set(per_ref_sel["refs/heads/esc"]["tests"]) == {
        "tests.test_other",
        "tests.test_pkg",
        "tests.test_sub",
    }

    p = selection.plan(
        sel, config=cfg, config_path=path / "ci-selection.toml", root=path
    )
    plan_contract.validate(p)
    exp_per_ref = {
        entry["remote_ref"]: entry for entry in p["expected_checks"]["per_ref"]
    }
    assert (
        next(
            c
            for c in exp_per_ref["refs/heads/src"]["checks"]
            if c["name"] == "unittest"
        )["applicability"]["status"]
        == "targeted"
    )
    assert (
        next(
            c
            for c in exp_per_ref["refs/heads/docs"]["checks"]
            if c["name"] == "unittest"
        )["applicability"]["status"]
        == "no_tests"
    )
    assert (
        next(
            c
            for c in exp_per_ref["refs/heads/esc"]["checks"]
            if c["name"] == "unittest"
        )["applicability"]["status"]
        == "escalated"
    )

    tampered = json.loads(json.dumps(p))
    tampered["selection"]["per_ref"][1]["status"] = "targeted"
    with pytest.raises(plan_contract.PlanError) as raised:
        plan_contract.validate(tampered)
    assert raised.value.code == "expected_per_ref_selection_mismatch"


def test_p1_3_source_check_applicability_with_custom_sources_toml(repo):
    path, _ = repo
    (path / "custom").mkdir(parents=True, exist_ok=True)
    (path / "vendor/src").mkdir(parents=True, exist_ok=True)
    (path / "ci-sources.toml").write_text(
        """schema_version = 1
manifest = "custom/lock.toml"
acquired_roots = ["vendor/src"]
""",
        encoding="utf-8",
    )
    run(path, "add", "ci-sources.toml")
    run(path, "commit", "-q", "-m", "add custom ci-sources.toml")
    base2 = run(path, "rev-parse", "HEAD").strip()

    # Modifying custom acquired_root outside default source_prefixes triggers `source` check
    (path / "vendor/src/lib.py").write_text("X = 1\n", encoding="utf-8")
    run(path, "add", "vendor/src/lib.py")
    run(path, "commit", "-q", "-m", "touch acquired root")
    src_commit = run(path, "rev-parse", "HEAD").strip()

    # Modifying custom manifest also triggers `source` check, while docs does not
    run(path, "checkout", "-q", base2)
    (path / "custom/lock.toml").write_text("pin = 1\n", encoding="utf-8")
    run(path, "add", "custom/lock.toml")
    run(path, "commit", "-q", "-m", "touch manifest")
    lock_commit = run(path, "rev-parse", "HEAD").strip()

    run(path, "checkout", "-q", base2)
    (path / "docs/plain.md").write_text("plain\n", encoding="utf-8")
    run(path, "add", "docs/plain.md")
    run(path, "commit", "-q", "-m", "docs only")
    docs_commit = run(path, "rev-parse", "HEAD").strip()

    cfg = config(path)
    single_sel = selection.select(
        selection.changeset(path, base=base2, head=src_commit), cfg, root=path
    )
    single_plan = selection.plan(
        single_sel, config=cfg, config_path=path / "ci-selection.toml", root=path
    )
    plan_contract.validate(single_plan)
    single_scope = {c["name"]: c for c in single_plan["check_scope"]}
    single_exp = {c["name"]: c for c in single_plan["expected_checks"]["checks"]}
    assert single_scope["source"]["status"] == "selected"
    assert single_exp["source"]["applicability"]["status"] == "selected"

    multi = selection.changeset_multi(
        path,
        [
            ("refs/heads/lock", lock_commit, "refs/heads/lock", base2),
            ("refs/heads/docs", docs_commit, "refs/heads/docs", base2),
        ],
    )
    multi_sel = selection.select(multi, cfg, root=path)
    multi_plan = selection.plan(
        multi_sel, config=cfg, config_path=path / "ci-selection.toml", root=path
    )
    plan_contract.validate(multi_plan)
    per_ref = {e["remote_ref"]: e for e in multi_plan["expected_checks"]["per_ref"]}
    assert (
        next(c for c in per_ref["refs/heads/lock"]["checks"] if c["name"] == "source")[
            "applicability"
        ]["status"]
        == "selected"
    )
    assert (
        next(c for c in per_ref["refs/heads/docs"]["checks"] if c["name"] == "source")[
            "applicability"
        ]["status"]
        == "not_applicable"
    )


def test_repository_root_selection_and_tach_configs_target_src_and_dagger():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    cfg = selection.load_config(root / "ci-selection.toml")
    modules, _ = selection._tach_graph(root / cfg.tach)
    assert modules
    assert not (set(modules) - set(cfg.module_tests))

    def _worktree_changes(*rels):
        return selection.ChangeSet(
            root=root,
            base_ref="HEAD",
            head_ref="WORKTREE",
            base_commit="0" * 40,
            head_commit="1" * 40,
            tree=None,
            paths=tuple(rels),
            worktree_dirty=True,
            complete=True,
        )

    hyg_sel = selection.select(
        _worktree_changes("src/engineering_ci/hygiene.py"), cfg, root=root
    )
    assert hyg_sel.status == "targeted"
    assert hyg_sel.escalated is False
    assert "tests.test_hygiene" in hyg_sel.tests
    assert "tests.test_selection" in hyg_sel.tests

    dagger_sel = selection.select(
        _worktree_changes(".dagger/src/engineering_ci_module/link_check.py"),
        cfg,
        root=root,
    )
    assert dagger_sel.status == "targeted"
    assert dagger_sel.escalated is False
    assert "tests.test_link_check" in dagger_sel.tests

    repo_helper_sel = selection.select(
        _worktree_changes(".dagger/src/engineering_ci_module/repository.py"),
        cfg,
        root=root,
    )
    assert "tests.test_dagger_native_telemetry" in repo_helper_sel.tests
    assert "tests.test_script_analysis" in repo_helper_sel.tests
    assert "tests.test_weaver" in repo_helper_sel.tests

    tooling_sel = selection.select(
        _worktree_changes(".dagger/src/engineering_ci_module/tooling.py"),
        cfg,
        root=root,
    )
    assert "tests.test_dagger_native_telemetry" in tooling_sel.tests
    assert "tests.test_execution" in tooling_sel.tests
    assert "tests.test_script_analysis" in tooling_sel.tests

    for path_rel, expected_tests in (
        (
            ".dagger/src/engineering_ci_module/pytest_ci.py",
            {
                "tests.test_ci_performance",
                "tests.test_plan_result",
                "tests.test_pytest_ci",
                "tests.test_pytest_quality",
            },
        ),
        (
            ".dagger/src/engineering_ci_module/pytest_quality.py",
            {
                "tests.test_plan_result",
                "tests.test_pytest_matrix",
                "tests.test_pytest_quality",
            },
        ),
        (
            ".dagger/src/engineering_ci_module/pytest_report.py",
            {
                "tests.test_ci_performance",
                "tests.test_manual_release",
                "tests.test_plan_result",
                "tests.test_pytest_ci",
                "tests.test_pytest_matrix",
                "tests.test_pytest_quality",
                "tests.test_pytest_report",
            },
        ),
        (
            "tests/fixtures/python-app/pyproject.toml",
            {
                "tests.test_native_hooks",
                "tests.test_pytest_ci",
                "tests.test_pytest_report",
            },
        ),
        (
            "tests/fixtures/selection-app/ci-hygiene.toml",
            {
                "tests.test_selection",
                "tests.test_execution",
                "tests.test_expected_checks",
                "tests.test_hygiene",
                "tests.test_issue52_githubreceiver_e2e",
                "tests.test_plan_result",
                "tests.test_reconciliation",
                "tests.test_terminal_expected",
            },
        ),
        (
            "tests/fixtures/weaver-cicd/.weaver.toml",
            {
                "tests.test_weaver",
                "tests.test_cicd_telemetry",
                "tests.test_cicd_telemetry_attributes",
                "tests.test_github_receiver",
                "tests.test_issue52_githubreceiver_e2e",
                "tests.test_selection",
                "tests.test_terminal_expected",
            },
        ),
        (
            "config/otel-collector-github-receiver.yaml",
            {"tests.test_issue52_githubreceiver_e2e"},
        ),
    ):
        single_sel = selection.select(_worktree_changes(path_rel), cfg, root=root)
        assert single_sel.status == "targeted", path_rel
        assert single_sel.escalated is False, path_rel
        assert expected_tests <= set(single_sel.tests), (path_rel, single_sel.tests)
        if path_rel.endswith("pytest_ci.py"):
            assert "tests.test_weaver" not in single_sel.tests

    docs_sel = selection.select(
        _worktree_changes("docs/architecture/hygiene.md"), cfg, root=root
    )
    assert docs_sel.status == "no_tests"
    assert docs_sel.escalated is False


def test_schema1_default_code_prefixes_identity_and_explicit_dagger_scope(tmp_path):
    legacy = tmp_path / "legacy-selection.toml"
    legacy.write_text('schema_version = 1\nframework = "pytest"\n', encoding="utf-8")
    loaded = selection.load_config(legacy)
    assert loaded.check_scope.code_prefixes == ("src/", "scripts/", "tests/")
    identity = selection.config_identity(legacy, loaded, root=tmp_path)
    assert identity["normalized"]["check_scope"]["code_prefixes"] == [
        "src/",
        "scripts/",
        "tests/",
    ]
    reconstructed = selection.verify_config_identity_matches_file(
        identity, root=tmp_path, require_file=True
    )
    assert reconstructed.check_scope.code_prefixes == ("src/", "scripts/", "tests/")

    explicit = tmp_path / "dagger-selection.toml"
    explicit.write_text(
        "schema_version = 1\n"
        'framework = "pytest"\n'
        "[check_scope]\n"
        'code_prefixes = ["src/", "scripts/", "tests/", ".dagger/src/"]\n',
        encoding="utf-8",
    )
    explicit_cfg = selection.load_config(explicit)
    assert ".dagger/src/" in explicit_cfg.check_scope.code_prefixes
    explicit_id = selection.config_identity(explicit, explicit_cfg, root=tmp_path)
    verified_explicit = selection.verify_config_identity_matches_file(
        explicit_id, root=tmp_path, require_file=True
    )
    assert ".dagger/src/" in verified_explicit.check_scope.code_prefixes


def test_pytest_norecursedirs_aligns_worktree_tree_per_ref_and_standalone_fixture(
    repo, tmp_path
):
    root = Path(__file__).resolve().parents[1]
    cfg = selection.load_config(root / "ci-selection.toml")
    worktree_mods = selection._test_modules(
        root / "tests", "tests", framework=cfg.framework
    )
    assert not any("fixtures" in mod for mod in worktree_mods)
    assert "tests/test_selection.py" in worktree_mods

    full_worktree = selection.select(
        selection.ChangeSet(
            root=root,
            base_ref="HEAD",
            head_ref="WORKTREE",
            base_commit="0" * 40,
            head_commit="1" * 40,
            tree=None,
            paths=(),
            worktree_dirty=False,
            complete=True,
        ),
        cfg,
        root=root,
        full=True,
    )
    assert full_worktree.tests == worktree_mods

    # Container-safe Git repo with `[tool.pytest.ini_options].norecursedirs` verifies
    # worktree, immutable tree, and per-ref snapshot parity (`.git` may be excluded in containers).
    root_like = tmp_path / "root_like"
    (root_like / "tests/fixtures/nested/tests").mkdir(parents=True)
    (root_like / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\nnorecursedirs = ["fixtures", ".*"]\n',
        encoding="utf-8",
    )
    (root_like / "ci-selection.toml").write_text(
        'schema_version = 1\nframework = "pytest"\n',
        encoding="utf-8",
    )
    (root_like / "tests/test_unit.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (root_like / "tests/fixtures/nested/tests/test_ignored.py").write_text(
        "def test_fixture():\n    assert False\n", encoding="utf-8"
    )
    run(root_like, "init", "-q", "-b", "main")
    run(root_like, "config", "user.email", "selection@example.invalid")
    run(root_like, "config", "user.name", "Selection Test")
    run(root_like, "add", "-A")
    run(root_like, "commit", "-qm", "root-like baseline")

    rl_cfg = selection.load_config(root_like / "ci-selection.toml")
    tree_id = run(root_like, "rev-parse", "HEAD^{tree}").strip()
    head = run(root_like, "rev-parse", "HEAD").strip()
    assert selection._test_modules(
        root_like / "tests", "tests", framework="pytest"
    ) == ("tests/test_unit.py",)
    assert selection._tree_test_modules(
        root_like, tree_id, "tests", "tests", framework="pytest"
    ) == ("tests/test_unit.py",)
    snap = selection._ref_source_snapshot(
        root_like,
        {"tree": tree_id, "ref_action": "update"},
        config=rl_cfg,
        entry={
            "remote_ref": "refs/heads/main",
            "local_ref": "refs/heads/main",
            "before": head,
            "after": head,
            "tree": tree_id,
            "ref_action": "update",
            "tests": ["tests/test_unit.py"],
        },
    )
    assert snap["tests_modules"] == ["tests/test_unit.py"]

    # Standalone fixture project continues discovering its own tests and running dotted pytest targets.
    fixture_repo, _ = repo
    standalone_mods = selection._test_modules(fixture_repo / "tests", "tests")
    assert standalone_mods == ("tests.test_other", "tests.test_pkg", "tests.test_sub")
    assert (
        selection.run_tests(("tests.test_pkg",), root=fixture_repo, framework="pytest")
        == 0
    )
    assert (
        selection.run_tests(
            ("tests.missing_target",), root=fixture_repo, framework="pytest"
        )
        == 4
    )

    # Corrupt UTF-8 pyproject.toml falls back safely instead of crashing.
    bad_root = tmp_path / "bad_pyproject"
    bad_root.mkdir()
    (bad_root / "pyproject.toml").write_bytes(b"\xff\xfe\x00broken")
    assert selection._pytest_norecursedirs(bad_root) == ()


def test_pytest_targets_stay_bound_to_source_under_external_pythonpath(
    tmp_path, monkeypatch
):
    import os

    external = tmp_path / "external_site"
    (external / "tests").mkdir(parents=True)
    (external / "tests/__init__.py").write_text("", encoding="utf-8")
    (external / "tests/test_selected.py").write_text(
        "def test_selected():\n    assert True\n", encoding="utf-8"
    )
    (external / "tests/test_missing.py").write_text(
        "def test_missing():\n    assert True\n", encoding="utf-8"
    )
    (external / "legit_ext.py").write_text("EXTRA = 7\n", encoding="utf-8")

    repo_root = tmp_path / "bound_repo"
    (repo_root / "src").mkdir(parents=True)
    (repo_root / "tests").mkdir(parents=True)
    # Root tests/ intentionally has no __init__.py (matching engineering-ci layout).
    (repo_root / "src/app_mod.py").write_text("VALUE = 42\n", encoding="utf-8")
    (repo_root / "tests/test_selected.py").write_text(
        "import app_mod\n\ndef test_selected():\n    assert app_mod.VALUE == 0\n",
        encoding="utf-8",
    )
    (repo_root / "tests/test_pass.py").write_text(
        "import app_mod\nimport legit_ext\n\ndef test_ok():\n    assert app_mod.VALUE + legit_ext.EXTRA == 49\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("PYTHONPATH", str(external))

    # Exact reviewer counterexample: external PYTHONPATH package MUST NOT shadow repo failing test.
    fail_cap = {}
    rc = selection.run_tests(
        ("tests.test_selected",), root=repo_root, framework="pytest", capture=fail_cap
    )
    assert rc == 1, fail_cap
    assert "--pyargs" not in fail_cap["argv"]
    assert "--" in fail_cap["argv"]
    assert fail_cap["argv"].index("--") < fail_cap["argv"].index(
        "tests/test_selected.py"
    )

    # Dotted module with ::nodeid suffix resolves to bound relative path + ::nodeid after "--".
    nodeid_cap = {}
    rc_nodeid = selection.run_tests(
        ("tests.test_selected::test_selected",),
        root=repo_root,
        framework="pytest",
        capture=nodeid_cap,
    )
    assert rc_nodeid == 1, nodeid_cap
    assert "--" in nodeid_cap["argv"]
    assert nodeid_cap["argv"].index("--") < nodeid_cap["argv"].index(
        "tests/test_selected.py::test_selected"
    )

    # Missing repo target stays bound after "--" and fails natively (exit 4) instead of running external module.
    miss_cap = {}
    rc_miss = selection.run_tests(
        ("tests.test_missing",), root=repo_root, framework="pytest", capture=miss_cap
    )
    assert rc_miss == 4, miss_cap
    assert "source_refusal" not in miss_cap
    assert "--pyargs" not in miss_cap["argv"]
    assert "--" in miss_cap["argv"]
    assert miss_cap["argv"].index("--") < miss_cap["argv"].index(
        "tests/test_missing.py"
    )

    # Legitimate PYTHONPATH imports remain available for bound repo tests, with junitxml before "--".
    pass_cap = {}
    junit_out = tmp_path / "bound-junit.xml"
    rc_pass = selection.run_tests(
        ("tests.test_pass",),
        root=repo_root,
        framework="pytest",
        junit_path=junit_out,
        capture=pass_cap,
    )
    assert rc_pass == 0, pass_cap
    assert junit_out.is_file()
    sep_idx = pass_cap["argv"].index("--")
    assert any(arg.startswith("--junitxml=") for arg in pass_cap["argv"][:sep_idx])
    assert pass_cap["argv"][sep_idx + 1 :] == ["tests/test_pass.py"]

    # Out-of-source traversal, absolute path, and pre/post-normalization option-injected targets are refused pre-exec.
    for bad_target, expected_reason in (
        ("../external_site/tests/test_selected.py", "pytest_target_outside_source"),
        (
            str(external / "tests/test_selected.py"),
            "pytest_target_outside_source",
        ),
        ("./C:/outside", "pytest_target_outside_source"),
        ("./C:outside", "pytest_target_outside_source"),
        (".\\C:\\outside", "pytest_target_outside_source"),
        ("//server/share", "pytest_target_outside_source"),
        ("--pyargs", "pytest_target_invalid"),
        ("./--collect-only", "pytest_target_invalid"),
        ("./--help", "pytest_target_invalid"),
        ("./--version", "pytest_target_invalid"),
        (".\\.\\--version", "pytest_target_invalid"),
        ("./--collect-only::test_selected", "pytest_target_invalid"),
        (" ./--version", "pytest_target_invalid"),
        (" -q", "pytest_target_invalid"),
    ):
        refuse_cap = {}
        rc_bad = selection.run_tests(
            (bad_target, "tests.test_selected"),
            root=repo_root,
            framework="pytest",
            capture=refuse_cap,
        )
        assert rc_bad == 4
        assert refuse_cap["source_refusal"] == expected_reason
        assert "argv" not in refuse_cap

    # P1-06: pytest 9.0.3 evaluates fromfile_prefix_chars="@" even after "--"; bound "@" targets
    # MUST be passed with explicit "./" so @argsfile expansion cannot substitute another target.
    ext_pass = str((external / "tests/test_selected.py").resolve())
    (repo_root / "@args.py").write_text(
        "def test_bound_at_fail():\n    assert False\n", encoding="utf-8"
    )
    (repo_root / "args.py").write_text(ext_pass + "\n", encoding="utf-8")
    (repo_root / "@dir").mkdir()
    (repo_root / "@dir/test_in_dir.py").write_text(
        "def test_in_dir_fail():\n    assert False\n", encoding="utf-8"
    )
    (repo_root / "dir").mkdir()
    (repo_root / "dir/test_in_dir.py").write_text(ext_pass + "\n", encoding="utf-8")
    (repo_root / "missing.py").write_text(ext_pass + "\n", encoding="utf-8")
    (repo_root / "tests/@args.py").write_text(
        "def test_nested_at_fail():\n    assert False\n", encoding="utf-8"
    )

    for target, expected_cli in (
        ("@args.py", "./@args.py"),
        ("./@args.py", "./@args.py"),
        ("@args.py::test_bound_at_fail", "./@args.py::test_bound_at_fail"),
        (".\\.\\@dir\\test_in_dir.py", "./@dir/test_in_dir.py"),
        ("@dir", "./@dir"),
        ("tests/@args.py", "tests/@args.py"),
    ):
        at_cap = {}
        at_junit = tmp_path / "at-bound-junit.xml"
        rc_at = selection.run_tests(
            (target,),
            root=repo_root,
            framework="pytest",
            junit_path=at_junit,
            capture=at_cap,
        )
        assert rc_at == 1, (target, at_cap)
        sep_idx = at_cap["argv"].index("--")
        assert at_cap["argv"][sep_idx + 1 :] == [expected_cli]
        assert at_junit.is_file()

    # Missing @missing.py MUST exit 4 ("not found"), NEVER expand missing.py as an @argsfile.
    at_miss_cap = {}
    rc_at_miss = selection.run_tests(
        ("@missing.py",), root=repo_root, framework="pytest", capture=at_miss_cap
    )
    assert rc_at_miss == 4, at_miss_cap
    assert "source_refusal" not in at_miss_cap
    assert at_miss_cap["argv"][at_miss_cap["argv"].index("--") + 1 :] == [
        "./@missing.py"
    ]
    assert "not found" in (at_miss_cap["stdout"] + at_miss_cap["stderr"]).lower()

    # Directory target on real bound repo tests still runs pytest natively, and explicit
    # directory paths (including "./chosen" and dotted "./sub.pkg") stay idempotent even when
    # same-stem .py modules exist in the workspace.
    (repo_root / "chosen").mkdir()
    (repo_root / "chosen/test_dir.py").write_text(
        "def test_dir_fail():\n    assert False\n", encoding="utf-8"
    )
    (repo_root / "chosen.py").write_text(
        "def test_file_pass():\n    assert True\n", encoding="utf-8"
    )
    (repo_root / "sub.pkg").mkdir()
    (repo_root / "sub.pkg/test_dotdir.py").write_text(
        "def test_dotdir_fail():\n    assert False\n", encoding="utf-8"
    )
    (repo_root / "sub").mkdir()
    (repo_root / "sub/pkg.py").write_text(
        "def test_dotted_pass():\n    assert True\n", encoding="utf-8"
    )
    for dir_target in ("./chosen", "./sub.pkg"):
        once, ref_once = selection.resolve_pytest_targets([dir_target], repo_root)
        twice, ref_twice = selection.resolve_pytest_targets(once, repo_root)
        assert ref_once is None and ref_twice is None
        assert once == [dir_target]
        assert twice == [dir_target]
        chosen_cap = {}
        rc_chosen = selection.run_tests(
            (dir_target,), root=repo_root, framework="pytest", capture=chosen_cap
        )
        assert rc_chosen == 1, (dir_target, chosen_cap)
        assert chosen_cap["argv"][chosen_cap["argv"].index("--") + 1 :] == [dir_target]

    dir_cap = {}
    rc_dir = selection.run_tests(
        ("tests/test_pass.py",), root=repo_root, framework="pytest", capture=dir_cap
    )
    assert rc_dir == 0, dir_cap

    # Escaping symlink inside tests/ is refused before launching pytest.
    escape_link = repo_root / "tests/test_escape.py"
    try:
        os.symlink(external / "tests/test_selected.py", escape_link)
    except OSError:
        pass
    else:
        link_cap = {}
        rc_link = selection.run_tests(
            ("tests.test_escape",), root=repo_root, framework="pytest", capture=link_cap
        )
        assert rc_link == 4
        assert link_cap["source_refusal"] == "pytest_target_outside_source"
        assert "argv" not in link_cap


def test_resolve_pytest_targets_blocks_recursive_dir_symlinks_and_keeps_valid_internal_links(
    tmp_path,
):
    import sys
    import xml.etree.ElementTree as ET

    external = tmp_path / "outside"
    external.mkdir()
    (external / "test_outside.py").write_text(
        "def test_external():\n    assert True\n", encoding="utf-8"
    )

    repo_root = tmp_path / "repo"
    (repo_root / ".git/hooks").mkdir(parents=True)
    (repo_root / ".git/hooks/test_git_hook.py").write_text(
        "def test_hook():\n    assert True\n", encoding="utf-8"
    )
    (repo_root / "chosen").mkdir()
    (repo_root / "chosen/test_ok.py").write_text(
        "def test_inside():\n    assert True\n", encoding="utf-8"
    )
    (repo_root / "chosen_dir").mkdir()
    (repo_root / "chosen_dot").mkdir()
    (repo_root / "chosen_git").mkdir()
    (repo_root / "chosen_broken/sub").mkdir(parents=True)

    symlink_available = True
    try:
        os.symlink(external / "test_outside.py", repo_root / "chosen/test_outside.py")
        os.symlink(external, repo_root / "chosen_dir/ext_sub", target_is_directory=True)
        os.symlink(external / "test_outside.py", repo_root / "chosen_dot/test_ext.py")
        os.symlink(
            repo_root / ".git/hooks/test_git_hook.py",
            repo_root / "chosen_git/test_hook.py",
        )
        os.symlink(
            external / "missing_outside.py",
            repo_root / "chosen_broken/sub/test_broken.py",
        )
    except OSError:
        symlink_available = False
        assert sys.platform == "win32"
        import _winapi

        # Unprivileged Windows fallback: real directory junctions are reparse points
        # that native pytest traverses recursively unless blocked by _check_bound_pytest_directory.
        _winapi.CreateJunction(str(external), str(repo_root / "chosen/ext_outside"))
        _winapi.CreateJunction(str(external), str(repo_root / "chosen_dir/ext_sub"))
        _winapi.CreateJunction(str(external), str(repo_root / "chosen_dot/ext_dot"))
        _winapi.CreateJunction(
            str(repo_root / ".git/hooks"), str(repo_root / "chosen_git/git_hooks")
        )
        missing_outside = external / "missing_outside_dir"
        missing_outside.mkdir()
        _winapi.CreateJunction(
            str(missing_outside), str(repo_root / "chosen_broken/sub/broken_dir")
        )
        missing_outside.rmdir()

    for target_expr in (
        "./chosen",
        "chosen",
        "./chosen_dir",
        "chosen_dot",
        "./chosen_git",
        "./chosen_broken",
    ):
        resolved, refusal = selection.resolve_pytest_targets([target_expr], repo_root)
        assert resolved is None, target_expr
        assert refusal == "pytest_target_outside_source", target_expr
        cap = {}
        rc = selection.run_tests(
            (target_expr,), root=repo_root, framework="pytest", capture=cap
        )
        assert rc == 4
        assert cap["source_refusal"] == "pytest_target_outside_source"
        assert "argv" not in cap

    # Explicit sibling file target stays allowed even when chosen/ holds an escaping link.
    (repo_root / "chosen.py").write_text(
        "def test_explicit_file():\n    assert True\n", encoding="utf-8"
    )
    assert selection.resolve_pytest_targets(["chosen.py"], repo_root) == (
        ["chosen.py"],
        None,
    )
    assert (
        selection.run_tests(
            ("chosen.py",), root=repo_root, framework="pytest", capture={}
        )
        == 0
    )

    # Valid internal links (plus ordinary stdlib imports) pass natively without false refusal.
    # Also ensure cyclic internal directory links terminate in resolve_pytest_targets without infinite recursion.
    (repo_root / "shared_dir").mkdir()
    (repo_root / "shared_dir/test_shared_dir.py").write_text(
        "import json\n\ndef test_shared_dir():\n    assert json.loads('{\"ok\": 1}')['ok'] == 1\n",
        encoding="utf-8",
    )
    (repo_root / "shared_file.py").write_text(
        "def test_shared_file():\n    assert True\n", encoding="utf-8"
    )
    (repo_root / "chosen_valid").mkdir()
    (repo_root / "chosen_cycle").mkdir()
    if symlink_available:
        os.symlink(
            repo_root / "shared_dir",
            repo_root / "chosen_valid/internal_dir",
            target_is_directory=True,
        )
        os.symlink(
            repo_root / "shared_file.py",
            repo_root / "chosen_valid/test_internal_file.py",
        )
        os.symlink(
            repo_root / "chosen_cycle",
            repo_root / "chosen_cycle/loop_back",
            target_is_directory=True,
        )
        expected_cases = {"test_shared_dir", "test_shared_file"}
    else:
        import _winapi

        _winapi.CreateJunction(
            str(repo_root / "shared_dir"), str(repo_root / "chosen_valid/internal_dir")
        )
        _winapi.CreateJunction(
            str(repo_root / "chosen_cycle"), str(repo_root / "chosen_cycle/loop_back")
        )
        (repo_root / "chosen_valid/test_direct.py").write_text(
            "def test_direct():\n    assert True\n", encoding="utf-8"
        )
        expected_cases = {"test_shared_dir", "test_direct"}
    assert selection.resolve_pytest_targets(["./chosen_cycle"], repo_root) == (
        ["./chosen_cycle"],
        None,
    )
    assert selection.resolve_pytest_targets(["./chosen_valid"], repo_root) == (
        ["./chosen_valid"],
        None,
    )
    valid_junit = tmp_path / "valid-junit.xml"
    valid_cap = {}
    rc_valid = selection.run_tests(
        ("./chosen_valid",),
        root=repo_root,
        framework="pytest",
        junit_path=valid_junit,
        capture=valid_cap,
    )
    assert rc_valid == 0, valid_cap
    root_xml = ET.fromstring(valid_junit.read_text(encoding="utf-8"))
    case_names = {tc.attrib["name"] for tc in root_xml.iter("testcase")}
    assert case_names == expected_cases


def test_selection_rejects_unsupported_custom_tach_config_paths(repo, tmp_path):
    path, base = repo
    (path / "tach-alt.toml").write_text(
        (path / "tach.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    run(path, "add", "tach-alt.toml")
    run(path, "commit", "-qm", "add unused custom tach file alongside tach.toml")
    (path / "src/pkg/sub.py").write_text("VALUE = 5\n", encoding="utf-8")

    # "./tach.toml" normalizes cleanly to "tach.toml".
    norm_cfg_path = tmp_path / "norm-selection.toml"
    norm_cfg_path.write_text(
        'schema_version = 1\ntach = "./tach.toml"\n', encoding="utf-8"
    )
    assert selection.load_config(norm_cfg_path).tach == "tach.toml"

    # Custom filename (e.g. "tach-alt.toml") or subdir ("sub/tach.toml") is rejected cleanly.
    for custom in ("tach-alt.toml", "sub/tach.toml", "pyproject.toml"):
        bad_cfg_path = tmp_path / f"bad-{custom.replace('/', '_')}.toml"
        bad_cfg_path.write_text(
            f'schema_version = 1\ntach = "{custom}"\n', encoding="utf-8"
        )
        with pytest.raises(
            selection.SelectionError, match="tach_config_path_unsupported"
        ):
            selection.load_config(bad_cfg_path)

    # Programmatic Config bypassing load_config still refuses before running native tach or reading tach.toml.
    custom_cfg = dataclasses.replace(config(path), tach="tach-alt.toml")
    changes = selection.changeset(path, base=base)
    with pytest.raises(selection.SelectionError, match="tach_config_path_unsupported"):
        selection.select(changes, custom_cfg, root=path)
    tach_res = selection._tach_check(path, custom_cfg, changes)
    assert tach_res["status"] == "refused"
    assert tach_res["reason"] == "tach_config_path_unsupported"

    # Missing root tach.toml refuses before invoking tach (prevents parent-directory fallback).
    nested = tmp_path / "parent_has_tach/nested_missing"
    (tmp_path / "parent_has_tach/tach.toml").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "parent_has_tach/tach.toml").write_text(
        (path / "tach.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    nested.mkdir(parents=True, exist_ok=True)
    missing_res = selection._tach_check(nested, config(path), changes)
    assert missing_res["status"] == "refused"
    assert missing_res["reason"] == "tach_config_missing"


def test_pytest_lossless_paths_avoid_dotted_dir_collision_and_keep_legacy_aliases(
    tmp_path,
):
    path = tmp_path / "dot_collision"
    (path / "src").mkdir(parents=True)
    (path / "tests/sub.pkg").mkdir(parents=True)
    (path / "tests/sub/pkg").mkdir(parents=True)
    (path / ".gitignore").write_text(
        ".pytest_cache/\n__pycache__/\n", encoding="utf-8", newline="\n"
    )
    (path / "ci-hygiene.toml").write_text(
        'schema_version = 1\nchecks = ["whitespace"]\n', encoding="utf-8", newline="\n"
    )
    (path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
        encoding="utf-8",
        newline="\n",
    )
    (path / "tach.toml").write_text(
        'source_roots = ["src"]\n\n[[modules]]\npath = "pkg"\ndepends_on = []\n',
        encoding="utf-8",
        newline="\n",
    )
    (path / "ci-selection.toml").write_text(
        'schema_version = 1\nframework = "pytest"\nchecks = []\n\n[module_tests]\n"pkg" = ["tests.sub.pkg.test_case"]\n',
        encoding="utf-8",
        newline="\n",
    )
    (path / "src/pkg.py").write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
    (path / "tests/sub.pkg/test_case.py").write_text(
        "def test_dot_dir():\n    assert False\n", encoding="utf-8", newline="\n"
    )
    (path / "tests/sub/pkg/test_case.py").write_text(
        "def test_nested():\n    assert True\n", encoding="utf-8", newline="\n"
    )
    run(path, "init", "-q", "-b", "main")
    run(path, "config", "core.autocrlf", "false")
    run(path, "config", "user.email", "selection@example.invalid")
    run(path, "config", "user.name", "Selection Test")
    run(path, "add", "-A")
    run(path, "commit", "-qm", "baseline")
    base = run(path, "rev-parse", "HEAD").strip()
    tree_id = run(path, "rev-parse", "HEAD^{tree}").strip()
    cfg = config(path)

    expected_discovered = ("tests/sub.pkg/test_case.py", "tests/sub/pkg/test_case.py")
    assert (
        selection._test_modules(path / "tests", "tests", framework="pytest")
        == expected_discovered
    )
    assert (
        selection._tree_test_modules(
            path, tree_id, "tests", "tests", framework="pytest"
        )
        == expected_discovered
    )
    full_sel = selection.select(changes(path, base), cfg, root=path, full=True)
    assert full_sel.tests == expected_discovered
    tach_sha = selection._digest(path / "tach.toml")

    def _valid_tach(sel_result):
        return {
            "status": "passed",
            "exit_code": 0,
            "tach_sha256": tach_sha,
            "source_tree": (
                sel_result.changeset.execution_tree or sel_result.changeset.tree
            ),
        }

    # Direct modification of tests/sub.pkg/test_case.py selects and executes ONLY that file (exit 1).
    (path / "tests/sub.pkg/test_case.py").write_text(
        "def test_dot_dir():\n    assert False, 'boom'\n",
        encoding="utf-8",
        newline="\n",
    )
    dot_sel = select(path, base)
    assert dot_sel.tests == ("tests/sub.pkg/test_case.py",)
    dot_plan = selection.plan(
        dot_sel,
        config=cfg,
        config_path=path / "ci-selection.toml",
        root=path,
        tach_validation=_valid_tach(dot_sel),
    )
    assert dot_plan["selection"]["tests"] == ["tests/sub.pkg/test_case.py"]
    assert next(c for c in dot_plan["check_scope"] if c["name"] == "pytest")[
        "inputs"
    ] == ["tests/sub.pkg/test_case.py"]
    dot_exec = execution.execute(dot_plan, root=path)
    assert dot_exec["status"] == "failed"
    assert dot_exec["exit_code"] == 1
    assert any(
        ref.get("kind") in {"test_target", "changed_path"}
        and ref.get("path") == "tests/sub.pkg/test_case.py"
        for ref in dot_exec["tests"]["native_result"]["input_refs"]
    )
    assert "test_dot_dir" in dot_exec["native_stdout"]
    assert "test_nested" not in dot_exec["native_stdout"]
    run(path, "checkout", "--", "tests/sub.pkg/test_case.py")

    # Direct modification of tests/sub/pkg/test_case.py executes ONLY the nested passing file (exit 0).
    (path / "tests/sub/pkg/test_case.py").write_text(
        "def test_nested():\n    assert 1 == 1\n", encoding="utf-8", newline="\n"
    )
    nested_sel = select(path, base)
    assert nested_sel.tests == ("tests/sub/pkg/test_case.py",)
    nested_plan = selection.plan(
        nested_sel,
        config=cfg,
        config_path=path / "ci-selection.toml",
        root=path,
        tach_validation=_valid_tach(nested_sel),
    )
    nested_exec = execution.execute(nested_plan, root=path)
    assert nested_exec["status"] == "passed"
    assert nested_exec["exit_code"] == 0
    run(path, "checkout", "--", "tests/sub/pkg/test_case.py")

    # Index snapshot preserves lossless dot-directory path too.
    (path / "tests/sub.pkg/test_case.py").write_text(
        "def test_dot_dir():\n    assert False, 'staged'\n",
        encoding="utf-8",
        newline="\n",
    )
    run(path, "add", "tests/sub.pkg/test_case.py")
    idx_changes = selection.changeset(path, base=base, mode="index")
    idx_sel = selection.select(idx_changes, cfg, root=path)
    assert idx_sel.tests == ("tests/sub.pkg/test_case.py",)
    idx_plan = selection.plan(
        idx_sel,
        config=cfg,
        config_path=path / "ci-selection.toml",
        root=path,
        tach_validation=_valid_tach(idx_sel),
    )
    assert execution.execute(idx_plan, root=path)["exit_code"] == 1
    run(path, "reset", "HEAD", "--", "tests/sub.pkg/test_case.py")
    run(path, "checkout", "--", "tests/sub.pkg/test_case.py")

    # Mixed historical dotted [module_tests] + direct path edit deduplicate cleanly in run_tests.
    (path / "src/pkg.py").write_text("VALUE = 2\n", encoding="utf-8", newline="\n")
    (path / "tests/sub/pkg/test_case.py").write_text(
        "def test_nested():\n    assert 2 == 2\n", encoding="utf-8", newline="\n"
    )
    mixed_sel = select(path, base)
    assert mixed_sel.tests == ("tests.sub.pkg.test_case", "tests/sub/pkg/test_case.py")
    cap = {}
    assert (
        selection.run_tests(mixed_sel.tests, root=path, framework="pytest", capture=cap)
        == 0
    )
    assert "--" in cap["argv"]
    assert cap["argv"][cap["argv"].index("--") + 1 :] == ["tests/sub/pkg/test_case.py"]

    # Per-ref multi snapshot + materialized verification preserve lossless dot-dir paths
    # alongside valid historical dotted aliases, and reject forged/ambiguous targets.
    run(path, "add", "src/pkg.py", "tests/sub/pkg/test_case.py")
    (path / "tests/sub.pkg/test_case.py").write_text(
        "def test_dot_dir():\n    assert False, 'committed'\n",
        encoding="utf-8",
        newline="\n",
    )
    run(path, "add", "tests/sub.pkg/test_case.py")
    run(path, "commit", "-qm", "c2")
    c2 = run(path, "rev-parse", "HEAD").strip()
    multi_ch = selection.changeset_multi(
        path, [("refs/heads/main", c2, "refs/heads/main", base)], config=cfg
    )
    multi_sel = selection.select(multi_ch, cfg, root=path)
    multi_plan = selection.plan(
        multi_sel,
        config=cfg,
        config_path=path / "ci-selection.toml",
        root=path,
    )
    entry = multi_plan["selection"]["per_ref"][0]
    assert entry["tests"] == [
        "tests.sub.pkg.test_case",
        "tests/sub.pkg/test_case.py",
        "tests/sub/pkg/test_case.py",
    ]
    assert entry["source_snapshot_ref"]["tests_modules"] == [
        "tests/sub.pkg/test_case.py",
        "tests/sub/pkg/test_case.py",
    ]
    plan_ref = multi_plan["git"]["references"][0]
    plan_inputs = multi_plan["inputs"]
    selection.verify_per_ref_source_snapshot(
        entry, plan_ref=plan_ref, inputs=plan_inputs
    )
    # P1-13: a dotted base with a native nodeid binds a distinct execution
    # target, recorded against the Ref's real members.
    nodeid_targets = ["tests.sub.pkg.test_case::test_nested"]
    from engineering_ci import git_source

    with git_source.materialized_tree(path, plan_ref["tree"]) as (checkout, _env):
        nodeid_member = selection._worktree_member(checkout)
        nodeid_bindings = [
            selection._binding_record(
                selection._target_binding(
                    target, nodeid_member, framework=cfg.framework
                )
            )
            for target in nodeid_targets
        ]
    nodeid_entry = {**entry, "tests": nodeid_targets}
    nodeid_snap_raw = {
        **entry["source_snapshot_ref"],
        "target_bindings": nodeid_bindings,
        "bindings_digest": "sha256:" + selection._json_sha256(nodeid_bindings),
        "selection_digest": selection._selection_entry_digest(
            nodeid_entry,
            ref=plan_ref,
            sources_identity=plan_inputs.get("sources"),
        ),
    }
    nodeid_snap = {
        **nodeid_snap_raw,
        "snapshot_digest": "sha256:"
        + selection._json_sha256(
            {k: v for k, v in nodeid_snap_raw.items() if k != "snapshot_digest"}
        ),
    }
    selection.verify_per_ref_source_snapshot(
        {**nodeid_entry, "source_snapshot_ref": nodeid_snap},
        plan_ref=plan_ref,
        inputs=plan_inputs,
    )
    mat_dir = tmp_path / "mat_dot"
    shutil.copytree(
        path,
        mat_dir,
        ignore=shutil.ignore_patterns(".git", ".pytest_cache", "__pycache__"),
    )
    selection.verify_materialized_source_snapshot(
        entry, mat_dir, sel_cfg=cfg, plan_ref=plan_ref, inputs=plan_inputs
    )

    missing_entry = {**entry, "tests": ["tests/sub.pkg/missing.py"]}
    missing_snap_raw = {
        **entry["source_snapshot_ref"],
        "selection_digest": selection._selection_entry_digest(
            missing_entry,
            ref=plan_ref,
            sources_identity=plan_inputs.get("sources"),
        ),
    }
    missing_snap = {
        **missing_snap_raw,
        "snapshot_digest": "sha256:"
        + selection._json_sha256(
            {k: v for k, v in missing_snap_raw.items() if k != "snapshot_digest"}
        ),
    }
    with pytest.raises(
        selection.SelectionError, match="expected_per_ref_source_unverifiable"
    ):
        selection.verify_per_ref_source_snapshot(
            {**missing_entry, "source_snapshot_ref": missing_snap},
            plan_ref=plan_ref,
            inputs=plan_inputs,
        )
    # A recorded binding for the same-name nested module is file-first, so the
    # historical dotted alias verifies against that real member.
    alias_entry = {**entry, "tests": ["tests.sub.pkg.test_case"]}
    alias_snap_raw = {
        **entry["source_snapshot_ref"],
        "selection_digest": selection._selection_entry_digest(
            alias_entry,
            ref=plan_ref,
            sources_identity=plan_inputs.get("sources"),
        ),
    }
    alias_snap = {
        **alias_snap_raw,
        "snapshot_digest": "sha256:"
        + selection._json_sha256(
            {k: v for k, v in alias_snap_raw.items() if k != "snapshot_digest"}
        ),
    }
    selection.verify_per_ref_source_snapshot(
        {**alias_entry, "source_snapshot_ref": alias_snap},
        plan_ref=plan_ref,
        inputs=plan_inputs,
    )


def test_pytest_norecursedirs_path_patterns_and_non_dict_toml_sections(tmp_path):
    # P2-04: path/glob norecursedirs patterns work across worktree, tree, and direct selection.
    path = tmp_path / "norecurse_paths"
    (path / "tests/sub.pkg").mkdir(parents=True)
    (path / "tests/group/nested").mkdir(parents=True)
    (path / "tests/fixtures").mkdir(parents=True)
    (path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\nnorecursedirs = ["fixtures", "tests/sub.pkg", "tests/**/nested"]\n',
        encoding="utf-8",
    )
    (path / "tach.toml").write_text(
        'source_roots = ["src"]\n\n[[modules]]\npath = "pkg"\ndepends_on = []\n',
        encoding="utf-8",
    )
    (path / "src").mkdir(parents=True)
    (path / "src/pkg.py").write_text("VALUE = 1\n", encoding="utf-8")
    (path / "ci-selection.toml").write_text(
        'schema_version = 1\nframework = "pytest"\n', encoding="utf-8"
    )
    (path / "tests/test_keep.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (path / "tests/sub.pkg/test_ignored.py").write_text(
        "def test_no():\n    assert False\n", encoding="utf-8"
    )
    (path / "tests/group/nested/test_deep.py").write_text(
        "def test_no():\n    assert False\n", encoding="utf-8"
    )
    (path / "tests/fixtures/test_fix.py").write_text(
        "def test_no():\n    assert False\n", encoding="utf-8"
    )
    run(path, "init", "-q", "-b", "main")
    run(path, "config", "user.email", "selection@example.invalid")
    run(path, "config", "user.name", "Selection Test")
    run(path, "add", "-A")
    run(path, "commit", "-qm", "init")
    base = run(path, "rev-parse", "HEAD").strip()
    tree_id = run(path, "rev-parse", "HEAD^{tree}").strip()

    assert selection._test_modules(path / "tests", "tests", framework="pytest") == (
        "tests/test_keep.py",
    )
    assert selection._tree_test_modules(
        path, tree_id, "tests", "tests", framework="pytest"
    ) == ("tests/test_keep.py",)
    (path / "tests/sub.pkg/test_ignored.py").write_text(
        "def test_no():\n    assert 1 == 2\n", encoding="utf-8"
    )
    (path / "tests/group/nested/test_deep.py").write_text(
        "def test_no():\n    assert 1 == 2\n", encoding="utf-8"
    )
    # Excluded paths under tests/ are never treated as discovered test modules; without explicit
    # non-code/ignore mapping they fail closed to the active discovered suite ('tests/test_keep.py').
    esc_ignored = select(path, base)
    assert esc_ignored.status == "escalated"
    assert esc_ignored.tests == ("tests/test_keep.py",)
    # With ignored_prefixes configured for those excluded fixture subtrees, changes resolve to no_tests.
    direct_ignored = selection.select(
        changes(path, base),
        dataclasses.replace(
            config(path),
            ignored_prefixes=("tests/sub.pkg/", "tests/group/nested/"),
        ),
        root=path,
    )
    assert direct_ignored.status == "no_tests"
    assert direct_ignored.tests == ()

    # P2-05: non-dict TOML section types fail closed to () without AttributeError.
    for idx, toml_body in enumerate(
        (
            'tool = "x"\n',
            '[tool]\npytest = "x"\n',
            "[tool.pytest]\nini_options = 1\n",
            '[tool.pytest.ini_options]\nnorecursedirs = "fixtures"\n',
        )
    ):
        case_dir = tmp_path / f"bad_toml_{idx}"
        case_dir.mkdir()
        (case_dir / "pyproject.toml").write_text(toml_body, encoding="utf-8")
        assert selection._pytest_norecursedirs(case_dir) == ()


def test_pytest_norecursedirs_matches_native_fnmatch_ex_contract(tmp_path):
    # P2-04: matching follows native POSIX _pytest.pathlib.fnmatch_ex semantics
    # on every host so a Windows host selects what the Linux containers execute.
    from _pytest.pathlib import fnmatch_ex

    parity = (
        ("fixtures", "tests/fixtures"),
        (".*", "tests/.hidden"),
        ("tests/sub.pkg", "tests/sub.pkg"),
        ("tests/sub.pkg", "other/tests/sub.pkg"),
        ("tests/**/nested", "tests/group/nested"),
        ("*/sub.pkg", "tests/sub.pkg"),
        ("tests/[ab]pk", "tests/apk"),
        ("tests/?.pkg", "tests/a.pkg"),
    )
    for pattern, rel in parity:
        name = rel.rsplit("/", 1)[-1]
        ours = selection._fnmatch_norecurse(pattern, rel, name)
        # Native collection paths are absolute, which anchors separator patterns.
        native = fnmatch_ex(
            pattern, ("C:/repo/" if os.name == "nt" else "/repo/") + rel
        )
        assert ours is native, (pattern, rel)

    # Declared contract: case-sensitive (POSIX) matching, so hosts and containers agree.
    # A Windows-native pytest folds case here; ECI over-selects instead of skipping.
    assert selection._fnmatch_norecurse("Sub.PKG", "tests/sub.pkg", "sub.pkg") is False
    assert os.name != "nt" or fnmatch_ex("Sub.PKG", "/repo/tests/sub.pkg") is True

    # No rewriting: POSIX pytest does not strip './', rewrite '\' or a trailing
    # '/', so matching them would exclude directories pytest still collects.
    for pattern in ("./fixtures", "tests\\fixtures", "fixtures/"):
        assert (
            selection._fnmatch_norecurse(pattern, "tests/fixtures", "fixtures") is False
        ), pattern
        if os.name != "nt":
            assert fnmatch_ex(pattern, "/repo/tests/fixtures") is False, pattern

    # Absolute patterns are refused fail-closed, not silently unmatched.
    for pattern in (
        "/repo/tests/fixtures",
        "C:/repo/tests/fixtures",
        "\\\\server\\share",
    ):
        case_dir = tmp_path / pattern.replace("/", "_").replace("\\", "_").replace(
            ":", "_"
        )
        case_dir.mkdir()
        (case_dir / "pyproject.toml").write_text(
            f"[tool.pytest.ini_options]\nnorecursedirs = [{json.dumps(pattern)}]\n",
            encoding="utf-8",
        )
        with pytest.raises(
            selection.SelectionError, match="pytest_norecursedirs_unsupported"
        ):
            selection._pytest_norecursedirs(case_dir)


def test_pytest_norecursedirs_case_semantics_agree_across_worktree_tree_and_selection(
    tmp_path,
):
    # A lowercase basename pattern must not silently drop a case-differing real
    # directory: worktree discovery, tree discovery and direct selection agree.
    path = tmp_path / "norecurse_case"
    (path / "tests/Sub.PKG").mkdir(parents=True)
    (path / "tests").mkdir(exist_ok=True)
    (path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\nnorecursedirs = ["sub.pkg"]\n',
        encoding="utf-8",
    )
    (path / "tach.toml").write_text(
        'source_roots = ["src"]\n\n[[modules]]\npath = "pkg"\ndepends_on = []\n',
        encoding="utf-8",
    )
    (path / "src").mkdir(parents=True)
    (path / "src/pkg.py").write_text("VALUE = 1\n", encoding="utf-8")
    (path / "ci-selection.toml").write_text(
        'schema_version = 1\nframework = "pytest"\n', encoding="utf-8"
    )
    (path / "tests/test_keep.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (path / "tests/Sub.PKG/test_case.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    run(path, "init", "-q", "-b", "main")
    run(path, "config", "user.email", "selection@example.invalid")
    run(path, "config", "user.name", "Selection Test")
    run(path, "add", "-A")
    run(path, "commit", "-qm", "init")
    tree_id = run(path, "rev-parse", "HEAD^{tree}").strip()

    expected = ("tests/Sub.PKG/test_case.py", "tests/test_keep.py")
    assert (
        selection._test_modules(path / "tests", "tests", framework="pytest") == expected
    )
    assert (
        selection._tree_test_modules(
            path, tree_id, "tests", "tests", framework="pytest"
        )
        == expected
    )
    base = run(path, "rev-parse", "HEAD").strip()
    (path / "tests/Sub.PKG/test_case.py").write_text(
        "def test_ok():\n    assert 1 == 1\n", encoding="utf-8"
    )
    direct = select(path, base)
    assert direct.tests == ("tests/Sub.PKG/test_case.py",)


def test_pytest_dotted_prerequisites_activate_for_lossless_path_targets(tmp_path):
    # P1-13: one pytest target identity across direct paths, dotted config aliases and nodeids.
    path = tmp_path / "pytest_prereq"
    (path / "tests").mkdir(parents=True)
    (path / "src").mkdir()
    (path / "src/pkg.py").write_text("VALUE = 1\n", encoding="utf-8")
    (path / "tests/test_other.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
    )
    (path / "tach.toml").write_text(
        'source_roots = ["src"]\n\n[[modules]]\npath = "pkg"\ndepends_on = []\n',
        encoding="utf-8",
    )
    (path / "ci-selection.toml").write_text(
        'schema_version = 1\nframework = "pytest"\n\n'
        '[module_tests]\n"pkg" = ["tests.test_other"]\n\n'
        '[prerequisites]\nwheel_smoke = ["tests.test_other"]\n'
        'node_case = ["tests/test_other.py::test_only_that_node"]\n',
        encoding="utf-8",
    )
    run(path, "init", "-q", "-b", "main")
    run(path, "config", "user.email", "selection@example.invalid")
    run(path, "config", "user.name", "Selection Test")
    run(path, "add", "-A")
    run(path, "commit", "-qm", "init")
    base = run(path, "rev-parse", "HEAD").strip()

    # Direct pytest file modification selects a lossless path but keeps the dotted prerequisite.
    (path / "tests/test_other.py").write_text(
        "def test_ok():\n    assert 1 == 1\n", encoding="utf-8"
    )
    direct = select(path, base)
    assert direct.tests == ("tests/test_other.py",)
    assert "wheel_smoke" in direct.prerequisites
    # A nodeid target is a distinct execution target from the whole file.
    assert "node_case" not in direct.prerequisites
    assert "wheel_smoke" in direct.base_prerequisites
    direct_plan = selection.plan(
        direct,
        config=config(path),
        config_path=path / "ci-selection.toml",
        root=path,
    )
    execution.validate_plan(direct_plan)
    assert direct_plan["selection"]["prerequisites"] == ["wheel_smoke"]
    run(path, "checkout", "--", "tests/test_other.py")

    # Module impact still activates the same prerequisite through the dotted alias.
    (path / "src/pkg.py").write_text("VALUE = 2\n", encoding="utf-8")
    impacted = select(path, base)
    assert "tests.test_other" in impacted.tests
    assert "wheel_smoke" in impacted.prerequisites

    # Both spellings of the same real file keep one prerequisite and one native target.
    (path / "tests/test_other.py").write_text(
        "def test_ok():\n    assert 1 == 1\n", encoding="utf-8"
    )
    mixed = select(path, base)
    assert set(mixed.tests) == {"tests.test_other", "tests/test_other.py"}
    assert "wheel_smoke" in mixed.prerequisites
    resolved, refusal = selection.resolve_pytest_targets(mixed.tests, path)
    assert refusal is None
    assert resolved == ["tests/test_other.py"]


def test_pytest_target_identity_follows_resolver_and_keeps_directories_distinct(
    tmp_path,
):
    # P1-13: binding resolution reproduces the resolver's file-first rule and
    # keeps distinct execution targets distinct (file, directory, nodeid,
    # unittest method). Member facts come from the real Source.
    member_kinds = {
        "tests/test_case.py": "file",
        "tests/test_case": "dir",
        "tests/test_case/test_inner.py": "file",
        "tests/test_regular.py": "file",
    }

    def member(rel):
        return member_kinds.get(rel, "missing"), None

    def identity(target, framework="pytest"):
        return selection._binding_identity(
            selection._target_binding(target, member, framework=framework)
        )

    file_identity = identity("tests.test_case")
    assert file_identity == "file:tests/test_case.py"
    assert identity("tests/test_case.py") == file_identity
    dir_identity = identity("tests/test_case")
    assert dir_identity == "dir:tests/test_case"
    assert dir_identity != file_identity
    # A nodeid narrows the execution target; it is not the whole file.
    assert identity("tests/test_case.py::TestX::test_y") == (
        "file:tests/test_case.py::TestX::test_y"
    )
    assert identity("tests/test_case.py::TestX::test_y") != file_identity
    # unittest class/method targets stay separate execution targets.
    assert identity("tests.test_regular.TestCase.test_a", "unittest") == (
        "unittest:tests.test_regular.TestCase.test_a"
    )
    assert identity("tests.test_regular.TestCase.test_a", "unittest") != identity(
        "tests.test_regular.TestCase.test_b", "unittest"
    )
    for illegal in (
        "/tests/test_case.py",
        " tests/test_case.py",
        "tests/test_case.py ",
        "-q",
        "tests/test_case.py::",
        "../tests/test_case.py",
    ):
        assert identity(illegal) is None, illegal

    # Native exit codes show the file and the directory really execute differently.
    repo = tmp_path / "same_name"
    (repo / "tests/test_case").mkdir(parents=True)
    (repo / "tests/test_case.py").write_text(
        "def test_file():\n    assert True\n", encoding="utf-8"
    )
    (repo / "tests/test_case/test_inner.py").write_text(
        "def test_dir():\n    assert False\n", encoding="utf-8"
    )
    file_exit = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--", "tests/test_case.py"],
        cwd=repo,
        capture_output=True,
        check=False,
    ).returncode
    dir_exit = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--", "tests/test_case"],
        cwd=repo,
        capture_output=True,
        check=False,
    ).returncode
    assert (file_exit, dir_exit) == (0, 1)


def test_pytest_directory_target_does_not_activate_a_dotted_file_prerequisite(tmp_path):
    path = tmp_path / "dir_vs_file"
    (path / "tests/test_case").mkdir(parents=True)
    (path / "src").mkdir()
    (path / "src/pkg.py").write_text("VALUE = 1\n", encoding="utf-8")
    (path / "tests/test_case.py").write_text(
        "def test_file():\n    assert True\n", encoding="utf-8"
    )
    (path / "tests/test_case/test_inner.py").write_text(
        "def test_dir():\n    assert True\n", encoding="utf-8"
    )
    (path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
    )
    (path / "tach.toml").write_text(
        'source_roots = ["src"]\n\n[[modules]]\npath = "pkg"\ndepends_on = []\n',
        encoding="utf-8",
    )
    (path / "ci-selection.toml").write_text(
        'schema_version = 1\nframework = "pytest"\n\n'
        '[prerequisites]\nwheel_smoke = ["tests.test_case"]\n',
        encoding="utf-8",
    )
    run(path, "init", "-q", "-b", "main")
    run(path, "config", "user.email", "selection@example.invalid")
    run(path, "config", "user.name", "Selection Test")
    run(path, "add", "-A")
    run(path, "commit", "-qm", "init")
    base = run(path, "rev-parse", "HEAD").strip()

    # The dotted prerequisite names the file, so a directory-inner test edit
    # must not activate it.
    (path / "tests/test_case/test_inner.py").write_text(
        "def test_dir():\n    assert 1 == 1\n", encoding="utf-8"
    )
    dir_sel = select(path, base)
    assert dir_sel.tests == ("tests/test_case/test_inner.py",)
    assert "wheel_smoke" not in dir_sel.prerequisites
    run(path, "checkout", "--", "tests")

    # The same-name file does activate it.
    (path / "tests/test_case.py").write_text(
        "def test_file():\n    assert 1 == 1\n", encoding="utf-8"
    )
    file_sel = select(path, base)
    assert file_sel.tests == ("tests/test_case.py",)
    assert "wheel_smoke" in file_sel.prerequisites


def test_absolute_norecursedirs_refuses_only_test_discovery(tmp_path):
    # P2-04: an unsupported exclusion pattern may refuse an obligation that
    # discovers tests, but not an independent check plan that selects none.
    path = tmp_path / "norecurse_abs"
    (path / "tests").mkdir(parents=True)
    (path / "tests/test_keep.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
        'norecursedirs = ["/abs/fixtures"]\n',
        encoding="utf-8",
    )
    (path / "ci-selection.toml").write_text(
        'schema_version = 1\nframework = "pytest"\nchecks = ["hygiene"]\n',
        encoding="utf-8",
    )
    run(path, "init", "-q", "-b", "main")
    run(path, "config", "user.email", "selection@example.invalid")
    run(path, "config", "user.name", "Selection Test")
    run(path, "add", "-A")
    run(path, "commit", "-qm", "init")
    base = run(path, "rev-parse", "HEAD").strip()

    no_change = select(path, "HEAD")
    assert no_change.status == "no_changes"
    planned = selection.plan(
        no_change,
        config=config(path),
        config_path=path / "ci-selection.toml",
        root=path,
    )
    # Independent Hygiene obligations must still be scoped, not refused.
    assert {"whitespace", "forwarding", "docs"} <= {
        item["name"] for item in planned["check_scope"]
    }
    execution.validate_plan(planned)

    # A policy-disabled selection (no tests bound) behaves the same.
    disabled = dataclasses.replace(no_change, status="no_tests")
    planned_disabled = selection.plan(
        disabled,
        config=config(path),
        config_path=path / "ci-selection.toml",
        root=path,
    )
    assert {"whitespace", "forwarding", "docs"} <= {
        item["name"] for item in planned_disabled["check_scope"]
    }
    execution.validate_plan(planned_disabled)

    # Selecting tests must still refuse the unsupported pattern.
    (path / "tests/test_keep.py").write_text(
        "def test_ok():\n    assert 1 == 1\n", encoding="utf-8"
    )
    with pytest.raises(
        selection.SelectionError, match="pytest_norecursedirs_unsupported"
    ):
        select(path, base)


def test_explicit_source_targets_keep_prerequisites_outside_auto_discovery(tmp_path):
    # P1-13: explicit Source targets that auto-discovery never lists still bind
    # and still activate their prerequisite (main's string intersection did).
    path = tmp_path / "explicit_targets"
    (path / "tests/fixtures").mkdir(parents=True)
    (path / "tests/checks").mkdir(parents=True)
    (path / "data").mkdir()
    (path / "src").mkdir()
    (path / "src/pkg.py").write_text("VALUE = 1\n", encoding="utf-8")
    (path / "data/input.txt").write_text("x\n", encoding="utf-8")
    (path / "tests/check_special.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (path / "tests/fixtures/test_fix.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (path / "tests/checks.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (path / "tests/checks/test_inner.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
        'norecursedirs = ["fixtures"]\n',
        encoding="utf-8",
    )
    (path / "tach.toml").write_text(
        'source_roots = ["src"]\n\n[[modules]]\npath = "pkg"\ndepends_on = []\n',
        encoding="utf-8",
    )
    (path / "ci-selection.toml").write_text(
        'schema_version = 1\nframework = "pytest"\n\n'
        '[[non_code_rules]]\nexact = "data/input.txt"\n'
        'tests = ["tests/check_special.py", "tests/fixtures/test_fix.py", '
        '"tests.checks"]\nreason = "explicit targets"\n\n'
        "[prerequisites]\n"
        'wheel_smoke = ["tests/check_special.py"]\n'
        'fixture_probe = ["tests/fixtures/test_fix.py"]\n'
        'same_name = ["tests.checks"]\n',
        encoding="utf-8",
    )
    run(path, "init", "-q", "-b", "main")
    run(path, "config", "user.email", "selection@example.invalid")
    run(path, "config", "user.name", "Selection Test")
    run(path, "add", "-A")
    run(path, "commit", "-qm", "init")
    base = run(path, "rev-parse", "HEAD").strip()

    # Only the discovored files are auto-discovered; the explicit targets are not.
    discovered = selection._test_modules(path / "tests", "tests", framework="pytest")
    assert "tests/check_special.py" not in discovered
    assert "tests/fixtures/test_fix.py" not in discovered
    assert "tests/checks.py" not in discovered

    (path / "data/input.txt").write_text("y\n", encoding="utf-8")
    explicit = select(path, base)
    assert set(explicit.tests) == {
        "tests/check_special.py",
        "tests/fixtures/test_fix.py",
        "tests.checks",
    }
    assert {"wheel_smoke", "fixture_probe", "same_name"} <= explicit.prerequisites
    planned = selection.plan(
        explicit,
        config=config(path),
        config_path=path / "ci-selection.toml",
        root=path,
    )
    execution.validate_plan(planned)
    assert {"wheel_smoke", "fixture_probe", "same_name"} <= set(
        planned["selection"]["prerequisites"]
    )

    # The dotted name binds the same-name module file, not the directory.
    member = selection._worktree_member(path)
    binding = selection._target_binding("tests.checks", member)
    assert selection._binding_identity(binding) == "file:tests/checks.py"


def test_unittest_method_target_activates_its_prerequisite(tmp_path):
    # P1-13: unittest method targets must keep their original config semantics.
    path = tmp_path / "unittest_method"
    (path / "tests").mkdir(parents=True)
    (path / "data").mkdir()
    (path / "data/input.txt").write_text("x\n", encoding="utf-8")
    (path / "src").mkdir()
    (path / "src/pkg.py").write_text("VALUE = 1\n", encoding="utf-8")
    (path / "tach.toml").write_text(
        'source_roots = ["src"]\n\n[[modules]]\npath = "pkg"\ndepends_on = []\n',
        encoding="utf-8",
    )
    (path / "tests/test_regular.py").write_text(
        "import unittest\n\n\n"
        "class TestCase(unittest.TestCase):\n"
        "    def test_ok(self):\n"
        "        self.assertTrue(True)\n",
        encoding="utf-8",
    )
    (path / "ci-selection.toml").write_text(
        'schema_version = 1\nframework = "unittest"\n\n'
        '[[non_code_rules]]\nexact = "data/input.txt"\n'
        'tests = ["tests.test_regular.TestCase.test_ok"]\n'
        'reason = "method target"\n\n'
        "[prerequisites]\n"
        'wheel_smoke = ["tests.test_regular.TestCase.test_ok"]\n'
        'prepare_other = ["tests.test_regular.TestCase.test_skip"]\n',
        encoding="utf-8",
    )
    run(path, "init", "-q", "-b", "main")
    run(path, "config", "user.email", "selection@example.invalid")
    run(path, "config", "user.name", "Selection Test")
    run(path, "add", "-A")
    run(path, "commit", "-qm", "init")
    base = run(path, "rev-parse", "HEAD").strip()

    (path / "data/input.txt").write_text("y\n", encoding="utf-8")
    method_sel = select(path, base)
    assert method_sel.tests == ("tests.test_regular.TestCase.test_ok",)
    assert "wheel_smoke" in method_sel.prerequisites
    # A different method in the same module is a different execution target.
    assert "prepare_other" not in method_sel.prerequisites
    planned = selection.plan(
        method_sel,
        config=config(path),
        config_path=path / "ci-selection.toml",
        root=path,
    )
    assert "wheel_smoke" in planned["selection"]["prerequisites"]


def test_pytest_norecursedirs_keeps_whitespace_patterns_verbatim(tmp_path):
    # P2-04: native pytest does not trim norecursedirs entries, so a spaced
    # pattern must stay a literal non-match instead of excluding fixtures.
    path = tmp_path / "norecurse_space"
    (path / "tests/fixtures").mkdir(parents=True)
    (path / "tests/test_keep.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (path / "tests/fixtures/test_fix.py").write_text(
        "def test_fix():\n    assert True\n", encoding="utf-8"
    )
    (path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
        'norecursedirs = [" fixtures ", "fixtures "]\n',
        encoding="utf-8",
    )
    assert selection._pytest_norecursedirs(path) == (" fixtures ", "fixtures ")
    assert selection._test_modules(path / "tests", "tests", framework="pytest") == (
        "tests/fixtures/test_fix.py",
        "tests/test_keep.py",
    )
    native = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--collect-only", "tests"],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert native.returncode == 0
    assert "tests/fixtures/test_fix.py" in native.stdout


def test_per_ref_snapshot_distinguishes_not_enumerated_from_empty(tmp_path):
    # P2-07: an empty list alone must not stand for "not applicable" and
    # "enumerated empty"; and a "not selected" claim cannot cover bound targets.
    path = tmp_path / "snapshot_state"
    (path / "tests").mkdir(parents=True)
    (path / "tests/test_unit.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
    )
    (path / "ci-selection.toml").write_text(
        'schema_version = 1\nframework = "pytest"\n', encoding="utf-8"
    )
    run(path, "init", "-q", "-b", "main")
    run(path, "config", "user.email", "selection@example.invalid")
    run(path, "config", "user.name", "Selection Test")
    run(path, "add", "-A")
    run(path, "commit", "-qm", "init")
    tree = run(path, "rev-parse", "HEAD^{tree}").strip()
    cfg = selection.load_config(path / "ci-selection.toml")
    ref = {"tree": tree, "ref_action": "update"}

    idle = selection._ref_source_snapshot(
        path,
        ref,
        config=cfg,
        entry={
            "tests": [],
            "tree": tree,
            "ref_action": "update",
            "config_source_binding": {"status": "verified"},
        },
    )
    assert idle["tests_state"] == "not_selected"
    assert idle["tests_modules"] == []

    bound = selection._ref_source_snapshot(
        path,
        ref,
        config=cfg,
        entry={
            "tests": ["tests/test_unit.py"],
            "tree": tree,
            "ref_action": "update",
        },
    )
    assert bound["tests_state"] == "enumerated"
    assert bound["tests_modules"] == ["tests/test_unit.py"]

    # A "not selected" snapshot cannot verify an entry that binds targets.
    with pytest.raises(
        selection.SelectionError, match="expected_per_ref_source_unverifiable"
    ):
        selection.verify_per_ref_source_snapshot(
            {
                "status": "targeted",
                "tree": tree,
                "ref_action": "update",
                "tests": ["tests/test_unit.py"],
                "config_source_binding": {"status": "verified"},
                "source_snapshot_ref": idle,
            }
        )

    # A directory target is bound by coverage of recorded members, and rejected
    # when it covers none.
    entry_core = {
        "status": "targeted",
        "tree": tree,
        "ref_action": "update",
        "tests": ["tests/sub"],
        "config_source_binding": {"status": "verified"},
    }
    body = {
        "kind": "engineering-ci-per-ref-source-snapshot",
        "schema_version": 2,
        "source_origin": "git_target_tree",
        "target_tree": tree,
        "source_tree_id": "a" * 64,
        "ref_action": "update",
        "tach_path": "tach.toml",
        "tach_sha256": None,
        "tach_graph_digest": None,
        "tests_dir": "tests",
        "tests_state": "enumerated",
        "tests_modules": ["tests/sub/test_x.py"],
        "tests_digest": "sha256:" + selection._json_sha256(["tests/sub/test_x.py"]),
        "target_bindings": [
            {"target": "tests/sub", "kind": "dir", "path": "tests/sub", "nodeid": None}
        ],
        "bindings_digest": "sha256:"
        + selection._json_sha256(
            [
                {
                    "target": "tests/sub",
                    "kind": "dir",
                    "path": "tests/sub",
                    "nodeid": None,
                }
            ]
        ),
        "config_binding": {"status": "verified"},
        "selection_digest": selection._selection_entry_digest(entry_core),
    }
    snap = {**body, "snapshot_digest": "sha256:" + selection._json_sha256(body)}
    entry = {**entry_core, "source_snapshot_ref": snap}
    selection.verify_per_ref_source_snapshot(entry)
    with pytest.raises(
        selection.SelectionError, match="expected_per_ref_source_unverifiable"
    ):
        selection.verify_per_ref_source_snapshot({**entry, "tests": ["tests/missing"]})


def test_per_ref_snapshot_records_real_binding_facts(tmp_path):
    # P1-13: snapshot bindings come from the real Source, so explicit targets
    # outside discovery and same-name modules keep their actual binding.
    path = tmp_path / "binding_facts"
    (path / "tests/fixtures").mkdir(parents=True)
    (path / "tests/checks").mkdir(parents=True)
    (path / "tests/check_special.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (path / "tests/fixtures/test_bad.py").write_text(
        "def test_bad():\n    assert False\n", encoding="utf-8"
    )
    (path / "tests/checks.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (path / "tests/checks/test_inner.py").write_text(
        "def test_bad():\n    assert False\n", encoding="utf-8"
    )
    (path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
        'norecursedirs = ["fixtures"]\n',
        encoding="utf-8",
    )
    (path / "ci-selection.toml").write_text(
        'schema_version = 1\nframework = "pytest"\n', encoding="utf-8"
    )
    run(path, "init", "-q", "-b", "main")
    run(path, "config", "user.email", "selection@example.invalid")
    run(path, "config", "user.name", "Selection Test")
    run(path, "add", "-A")
    run(path, "commit", "-qm", "init")
    tree = run(path, "rev-parse", "HEAD^{tree}").strip()
    cfg = selection.load_config(path / "ci-selection.toml")
    entry = {
        "status": "targeted",
        "tree": tree,
        "ref_action": "update",
        "tests": [
            "tests/check_special.py",
            "tests/fixtures/test_bad.py",
            "tests.checks",
        ],
        "config_source_binding": {"status": "verified"},
    }
    snap = selection._ref_source_snapshot(
        path, {"tree": tree, "ref_action": "update"}, config=cfg, entry=entry
    )
    assert {
        item["target"]: (item["kind"], item["path"]) for item in snap["target_bindings"]
    } == {
        "tests/check_special.py": ("file", "tests/check_special.py"),
        "tests/fixtures/test_bad.py": ("file", "tests/fixtures/test_bad.py"),
        # File-first: the same-name module wins over the directory.
        "tests.checks": ("file", "tests/checks.py"),
    }
    selection.verify_per_ref_source_snapshot({**entry, "source_snapshot_ref": snap})

    # A tampered binding kind is rejected once the recorded digests are redone.
    tampered = [dict(item) for item in snap["target_bindings"]]
    for item in tampered:
        if item["target"] == "tests.checks":
            item["kind"] = "refused"
    body = {
        **snap,
        "target_bindings": tampered,
        "bindings_digest": "sha256:" + selection._json_sha256(tampered),
    }
    body.pop("snapshot_digest")
    tampered_snap = {
        **body,
        "snapshot_digest": "sha256:" + selection._json_sha256(body),
    }
    with pytest.raises(
        selection.SelectionError, match="expected_per_ref_source_unverifiable"
    ):
        selection.verify_per_ref_source_snapshot(
            {**entry, "source_snapshot_ref": tampered_snap}
        )


def test_member_kind_distinguishes_refused_symlink_from_missing(tmp_path):
    # P1-13: a refused escape is not a missing member and gets no identity.
    root = tmp_path / "symlink_members"
    (root / "tests/checks").mkdir(parents=True)
    outside = tmp_path / "outside.py"
    outside.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    link = root / "tests/checks.py"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")
    member = selection._worktree_member(root)
    assert member("tests/checks.py")[0] == "refused"
    assert member("tests/missing.py") == ("missing", None)
    binding = selection._target_binding("tests/checks.py", member)
    assert binding["kind"] == "refused"
    assert selection._binding_identity(binding) is None


@pytest.mark.skipif(
    os.name == "nt", reason="Git Tree symlinks need filesystem support to materialize"
)
def test_member_facts_refuse_escaping_symlink_and_keep_internal_links(tmp_path):
    # P1-13: the materialized checkout is the shared Source fact. An escaping
    # link is refused; a legal internal link (including ../ and an ancestor
    # directory link) binds as the file it points to.
    root = tmp_path / "link_facts"
    (root / "tests/sub").mkdir(parents=True)
    (root / "tests/real").mkdir()
    (root / "tests/test_base.py").write_text(
        "def test_base():\n    assert True\n", encoding="utf-8"
    )
    (root / "tests/real/test_inner.py").write_text(
        "def test_inner():\n    assert True\n", encoding="utf-8"
    )
    (root / "tests/sub/test_link.py").symlink_to(Path("../test_base.py"))
    (root / "tests/alias").symlink_to(Path("real"))
    outside = tmp_path / "outside.py"
    outside.write_text("def test_outside():\n    assert True\n", encoding="utf-8")
    (root / "tests/escaping.py").symlink_to(outside)
    (root / "ci-selection.toml").write_text(
        'schema_version = 1\nframework = "pytest"\n', encoding="utf-8"
    )
    run(root, "init", "-q", "-b", "main")
    run(root, "config", "user.email", "selection@example.invalid")
    run(root, "config", "user.name", "Selection Test")
    run(root, "add", "-A")
    run(root, "commit", "-qm", "init")
    tree = run(root, "rev-parse", "HEAD^{tree}").strip()

    from engineering_ci import git_source

    with git_source.raw_checkout(root, tree) as (checkout, _env, _fmt):
        member = selection._worktree_member(checkout)
        # A relative ../ link binds the file it resolves to.
        assert selection._binding_record(
            selection._target_binding("tests/sub/test_link.py", member)
        ) == {
            "target": "tests/sub/test_link.py",
            "kind": "file",
            "path": "tests/sub/test_link.py",
            "nodeid": None,
        }
        # A link in an ancestor directory is traversed.
        assert (
            selection._target_binding("tests/alias/test_inner.py", member)["kind"]
            == "file"
        )
        # An escaping link is refused, not reported as a file.
        escaping = selection._target_binding("tests/escaping.py", member)
        assert escaping["kind"] == "refused"
        assert selection._binding_identity(escaping) is None
        assert member("tests/missing.py") == ("missing", None)

    snap = selection._ref_source_snapshot(
        root,
        {"tree": tree, "ref_action": "update"},
        config=selection.load_config(root / "ci-selection.toml"),
        entry={
            "status": "targeted",
            "tree": tree,
            "ref_action": "update",
            "tests": ["tests/sub/test_link.py", "tests/alias/test_inner.py"],
            "config_source_binding": {"status": "verified"},
        },
    )
    assert {item["target"]: item["kind"] for item in snap["target_bindings"]} == {
        "tests/sub/test_link.py": "file",
        "tests/alias/test_inner.py": "file",
    }


def test_target_binding_matches_resolver_candidate_for_module_named_directory(
    tmp_path,
):
    # P1-13: the first .py candidate binds when it exists, even as a directory;
    # Selection prerequisite and the native resolver must agree with it.
    root = tmp_path / "module_named_dir"
    (root / "tests/checks.py").mkdir(parents=True)
    (root / "tests/checks").mkdir()
    (root / "tests/checks.py/test_a.py").write_text(
        "def test_a():\n    assert True\n", encoding="utf-8"
    )
    (root / "tests/checks/test_b.py").write_text(
        "def test_b():\n    assert False\n", encoding="utf-8"
    )
    (root / "tests/test_keep.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
    )
    (root / "tach.toml").write_text(
        'source_roots = ["src"]\n\n[[modules]]\npath = "pkg"\ndepends_on = []\n',
        encoding="utf-8",
    )
    (root / "src").mkdir()
    (root / "src/pkg.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "data").mkdir()
    (root / "data/input.txt").write_text("x\n", encoding="utf-8")
    (root / "ci-selection.toml").write_text(
        'schema_version = 1\nframework = "pytest"\n\n'
        '[[non_code_rules]]\nexact = "data/input.txt"\n'
        'tests = ["tests.checks"]\nreason = "module-named directory"\n\n'
        "[prerequisites]\n"
        'bound_target = ["tests.checks"]\n',
        encoding="utf-8",
    )
    run(root, "init", "-q", "-b", "main")
    run(root, "config", "user.email", "selection@example.invalid")
    run(root, "config", "user.name", "Selection Test")
    run(root, "add", "-A")
    run(root, "commit", "-qm", "init")
    base = run(root, "rev-parse", "HEAD").strip()

    member = selection._worktree_member(root)
    binding = selection._target_binding("tests.checks", member)
    assert (binding["kind"], binding["path"]) == ("dir", "tests/checks.py")
    resolved, refusal = selection.resolve_pytest_targets(["tests.checks"], root)
    assert refusal is None
    assert resolved == ["tests/checks.py"]

    (root / "data/input.txt").write_text("y\n", encoding="utf-8")
    selected = select(root, base)
    assert selected.tests == ("tests.checks",)
    assert "bound_target" in selected.prerequisites

    # Native exits prove the two same-named targets are different obligations.
    via_module_dir = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--", "tests/checks.py"],
        cwd=root,
        capture_output=True,
        check=False,
    ).returncode
    via_plain_dir = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--", "tests/checks"],
        cwd=root,
        capture_output=True,
        check=False,
    ).returncode
    assert (via_module_dir, via_plain_dir) == (0, 1)


def test_per_ref_snapshot_rejects_v1_schema(tmp_path):
    # P2-10: a valid v2 record is accepted; changing only the version (and the
    # derived digest) is refused, so the version guard is what rejects it.
    path = tmp_path / "schema_version"
    (path / "tests").mkdir(parents=True)
    (path / "tests/test_unit.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
    )
    (path / "ci-selection.toml").write_text(
        'schema_version = 1\nframework = "pytest"\n', encoding="utf-8"
    )
    run(path, "init", "-q", "-b", "main")
    run(path, "config", "user.email", "selection@example.invalid")
    run(path, "config", "user.name", "Selection Test")
    run(path, "add", "-A")
    run(path, "commit", "-qm", "init")
    tree = run(path, "rev-parse", "HEAD^{tree}").strip()
    cfg = selection.load_config(path / "ci-selection.toml")
    entry = {
        "status": "targeted",
        "tree": tree,
        "ref_action": "update",
        "tests": ["tests/test_unit.py"],
        "config_source_binding": {"status": "verified"},
    }
    snap = selection._ref_source_snapshot(
        path, {"tree": tree, "ref_action": "update"}, config=cfg, entry=entry
    )
    assert snap["schema_version"] == 2
    selection.verify_per_ref_source_snapshot({**entry, "source_snapshot_ref": snap})

    body = {k: v for k, v in snap.items() if k != "snapshot_digest"}
    body["schema_version"] = 1
    v1_snap = {
        **body,
        "snapshot_digest": "sha256:" + selection._json_sha256(body),
    }
    with pytest.raises(
        selection.SelectionError, match="expected_per_ref_source_unverifiable"
    ):
        selection.verify_per_ref_source_snapshot(
            {**entry, "source_snapshot_ref": v1_snap}
        )


def test_plan_tach_digest_comes_from_inspected_tree_raw_bytes(tmp_path):
    # P2-08: the Plan digest is the raw blob of the inspected Tree, not the
    # worktree file, and graph validation consumes that same fact.
    path = tmp_path / "crlf_tach"
    (path / "tests").mkdir(parents=True)
    (path / "src").mkdir()
    (path / "src/pkg.py").write_text("VALUE = 1\n", encoding="utf-8")
    (path / "tests/test_keep.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
    )
    (path / "tach.toml").write_bytes(
        b'source_roots = ["src"]\r\n\r\n[[modules]]\r\npath = "pkg"\r\n'
        b"depends_on = []\r\n"
    )
    (path / "ci-selection.toml").write_text(
        'schema_version = 1\nframework = "pytest"\n', encoding="utf-8"
    )
    run(path, "init", "-q", "-b", "main")
    run(path, "config", "user.email", "selection@example.invalid")
    run(path, "config", "user.name", "Selection Test")
    run(path, "config", "core.autocrlf", "false")
    run(path, "add", "-A")
    run(path, "commit", "-qm", "base")
    base = run(path, "rev-parse", "HEAD").strip()
    (path / "tests/test_keep.py").write_text(
        "def test_ok():\n    assert 1 == 1\n", encoding="utf-8"
    )
    run(path, "add", "-A")
    run(path, "commit", "-qm", "change")
    tree = run(path, "rev-parse", "HEAD^{tree}").strip()
    # The checkout no longer matches the inspected Tree.
    (path / "tach.toml").write_bytes(
        b'source_roots = ["src"]\n\n[[modules]]\npath = "other"\ndepends_on = []\n'
    )
    raw_blob = subprocess.run(
        ["git", "cat-file", "blob", f"{tree}:tach.toml"],
        cwd=path,
        capture_output=True,
        check=True,
    ).stdout
    expected_sha = hashlib.sha256(raw_blob).hexdigest()
    checkout_sha = hashlib.sha256((path / "tach.toml").read_bytes()).hexdigest()
    assert b"\r\n" in raw_blob
    assert expected_sha != checkout_sha

    cfg = selection.load_config(path / "ci-selection.toml")
    inspected = selection.select(
        selection.changeset(path, base=base, head="HEAD", mode="commit"),
        cfg,
        root=path,
    )
    planned = selection.plan(
        inspected,
        config=cfg,
        config_path=path / "ci-selection.toml",
        root=path,
    )
    assert planned["inputs"]["tach"]["sha256"] == expected_sha
    assert planned["generator"]["tach_sha256"] == expected_sha

    graph_value = {
        "status": "passed",
        "exit_code": 0,
        "tach_sha256": expected_sha,
        "source_tree": tree,
    }
    assert (
        selection._graph_validation(
            inspected,
            cfg,
            path,
            graph_value,
            tach_sha256=expected_sha,
            tach_sha_authoritative=True,
        )["status"]
        == "passed"
    )
    assert (
        selection._graph_validation(
            inspected,
            cfg,
            path,
            {**graph_value, "tach_sha256": checkout_sha},
            tach_sha256=expected_sha,
            tach_sha_authoritative=True,
        )["status"]
        == "untrusted"
    )


def test_graph_validation_untrusted_when_inspected_digest_unavailable(tmp_path):
    # A known inspected Tree whose Tach record cannot supply a digest must not
    # accept a passed record with a missing/None digest.
    class _Selected:
        changeset = types.SimpleNamespace(execution_tree="a" * 40, tree="a" * 40)

    class _Config:
        tach = "tach.toml"

    for value in (
        {
            "status": "passed",
            "exit_code": 0,
            "tach_sha256": None,
            "source_tree": "a" * 40,
        },
        {"status": "passed", "exit_code": 0, "source_tree": "a" * 40},
    ):
        result = selection._graph_validation(
            _Selected(),
            _Config(),
            tmp_path,
            value,
            tach_sha256=None,
            tach_sha_authoritative=True,
        )
        assert result == {
            "status": "untrusted",
            "reason": "tach_config_unavailable",
        }
        # The gate consumes that result and must block the test runner.
        plan = {
            "selection": {
                "tests": ["tests/test_unit.py"],
                "requires_tach_check": True,
                "tach_graph_validation": result,
            }
        }
        assert execution.target_test_gate(plan) == ("tach_graph_unvalidated", None)

    passed_value = {
        "status": "passed",
        "exit_code": 0,
        "tach_sha256": "b" * 64,
        "source_tree": "a" * 40,
    }
    passed = selection._graph_validation(
        _Selected(),
        _Config(),
        tmp_path,
        passed_value,
        tach_sha256="b" * 64,
        tach_sha_authoritative=True,
    )
    assert passed["status"] == "passed"
    assert (
        execution.target_test_gate(
            {
                "selection": {
                    "tests": ["tests/test_unit.py"],
                    "requires_tach_check": True,
                    "tach_graph_validation": passed,
                }
            }
        )
        is None
    )


def test_tree_tach_graph_requires_strict_utf8(tmp_path):
    path = tmp_path / "bad_tach_bytes"
    (path / "tests").mkdir(parents=True)
    (path / "tests/test_keep.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    run(path, "init", "-q", "-b", "main")
    run(path, "config", "user.email", "selection@example.invalid")
    run(path, "config", "user.name", "Selection Test")
    run(path, "add", "-A")
    run(path, "commit", "-qm", "init")
    blob = (
        subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=path,
            input=b'source_roots = ["sr\xffc"]\n',
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .strip()
    )
    run(path, "update-index", "--add", "--cacheinfo", f"100644,{blob},tach.toml")
    run(path, "commit", "-qm", "bad tach")
    tree = run(path, "rev-parse", "HEAD^{tree}").strip()
    with pytest.raises(selection.SelectionError, match="tach_config_invalid"):
        selection._tree_tach_graph(path, tree, "tach.toml")


def test_dynamic_consumer_mappings_keep_their_tests(tmp_path):
    # P2-11: exact rules and module_tests must name the tests that actually
    # consume these contracts, including cross-root/dynamic consumers.
    config = selection.load_config(Path(__file__).parents[1] / "ci-selection.toml")
    assert set(config.module_tests["engineering_ci.ci_check_inputs"]) >= {
        "tests.test_plan_result",
        "tests.test_link_check",
    }
    assert set(config.module_tests["engineering_ci.pytest_contracts"]) >= {
        "tests.test_manual_release",
        "tests.test_plan_result",
        "tests.test_pytest_ci",
        "tests.test_pytest_matrix",
        "tests.test_pytest_quality",
        "tests.test_verify_report",
    }
    rules = [
        r for r in config.non_code_rules if (r.exact or "").endswith("pytest_report.py")
    ]
    assert rules, "pytest_report rule missing"
    assert "tests.test_verify_report" in rules[0].tests

    exact_rules = {r.exact: r.tests for r in config.non_code_rules if r.exact}
    for name in ("repository.py", "tooling.py"):
        assert (
            "tests.test_ci_applicability"
            in exact_rules[".dagger/src/engineering_ci_module/" + name]
        )
    prefix_rules = {
        r.prefix: r.tests
        for r in config.non_code_rules
        if getattr(r, "prefix", None) == ".dagger/src/engineering_ci_module/"
    }
    assert (
        "tests.test_ci_applicability"
        in prefix_rules[".dagger/src/engineering_ci_module/"]
    )
    assert (
        "tests.test_ci_performance"
        in config.module_tests["engineering_ci.pytest_contracts"]
    )
    assert config.test_helpers["tests/test_hooks.py"] == (
        "tests.test_execution",
        "tests.test_native_hooks",
        "tests.test_pre_execution_source_proof",
    )
    closure = selection._test_helper_consumers(config, ["tests/test_hooks.py"])
    assert {"tests.test_plan_result", "tests.test_verify_report"} <= closure


def test_tach_digest_matches_blob_when_checkout_has_crlf(tmp_path):
    # P2-14: with core.autocrlf the checkout rewrites CRLF but the Plan digest
    # must stay the inspected Tree blob; validation then accepts the record.
    path = tmp_path / "tach_crlf"
    (path / "tests").mkdir(parents=True)
    (path / "tests/test_keep.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
    )
    (path / "tach.toml").write_bytes(
        b'source_roots = ["src"]\n\n[[modules]]\npath = "pkg"\ndepends_on = []\n'
    )
    (path / "ci-selection.toml").write_text(
        'schema_version = 1\nframework = "pytest"\n', encoding="utf-8"
    )
    run(path, "init", "-q", "-b", "main")
    run(path, "config", "user.email", "selection@example.invalid")
    run(path, "config", "user.name", "Selection Test")
    run(path, "config", "core.autocrlf", "true")
    run(path, "add", "-A")
    run(path, "commit", "-qm", "init")
    base = run(path, "rev-parse", "HEAD").strip()
    # Force the checkout to hold CRLF while the Blob stays LF.
    (path / "tach.toml").write_bytes(
        b'source_roots = ["src"]\r\n\r\n[[modules]]\r\npath = "pkg"\r\ndepends_on = []\r\n'
    )
    blob = subprocess.run(
        ["git", "cat-file", "blob", "HEAD:tach.toml"],
        cwd=path,
        capture_output=True,
        check=True,
    ).stdout
    checkout = (path / "tach.toml").read_bytes()
    assert b"\r\n" in checkout
    assert b"\r\n" not in blob
    changed = selection.changeset(path, base=base, head="HEAD", mode="commit")
    digest = selection._tach_source_sha256(path, "tach.toml", changed)
    assert digest == hashlib.sha256(blob).hexdigest()
    assert digest != hashlib.sha256(checkout).hexdigest()


def test_dagger_native_implementation_covers_source_filter_module(tmp_path):
    # P2-13: the shared source-filter authority must be part of the Dagger
    # implementation identity, so a change to it changes implementation_sha256.
    import importlib.util

    from engineering_ci import ci_check_inputs

    module_path = (
        Path(__file__).parents[1]
        / ".dagger/src/engineering_ci_module/native_results.py"
    )
    spec = importlib.util.spec_from_file_location("_eci_native_results", module_path)
    assert spec is not None and spec.loader is not None
    native_results = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native_results)

    first = tmp_path / "first_ci_check_inputs.py"
    first.write_text("SOURCE_NOISE = ()\n", encoding="utf-8")
    second = tmp_path / "second_ci_check_inputs.py"
    second.write_text("SOURCE_NOISE = ('x',)\n", encoding="utf-8")
    original = ci_check_inputs.__file__
    try:
        results = {}
        for kind in ("pytest", "weaver"):
            ci_check_inputs.__file__ = str(first)
            one = native_results.implementation(kind)
            ci_check_inputs.__file__ = str(second)
            two = native_results.implementation(kind)
            results[kind] = (
                one[0]["implementation_sha256"],
                two[0]["implementation_sha256"],
            )
    finally:
        ci_check_inputs.__file__ = original
    for kind, (first_sha, second_sha) in results.items():
        assert first_sha != second_sha, kind


def test_tach_digest_unavailable_for_known_tree_without_blob(tmp_path):
    # P2-14: a known inspected Tree without the Tach Blob is unavailable; only a
    # real non-ODB Source may fall back to the checkout file digest.
    path = tmp_path / "missing_blob"
    (path / "tests").mkdir(parents=True)
    (path / "tests/test_keep.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (path / "ci-selection.toml").write_text(
        'schema_version = 1\nframework = "pytest"\n', encoding="utf-8"
    )
    run(path, "init", "-q", "-b", "main")
    run(path, "config", "user.email", "selection@example.invalid")
    run(path, "config", "user.name", "Selection Test")
    run(path, "add", "-A")
    run(path, "commit", "-qm", "without tach")
    base = run(path, "rev-parse", "HEAD").strip()
    (path / "tach.toml").write_text('source_roots = ["src"]\n', encoding="utf-8")
    changed = selection.changeset(path, base=base, head="HEAD", mode="commit")
    assert selection._tach_source_sha256(path, "tach.toml", changed) is None
