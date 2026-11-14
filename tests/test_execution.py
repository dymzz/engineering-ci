"""Selection-plan execution: native tests, snapshot refusal, report status."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from engineering_ci import execution, hygiene, selection

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
    run(target, "config", "user.email", "execution@example.invalid")
    run(target, "config", "user.name", "Execution Test")
    run(target, "add", "-A")
    run(target, "commit", "-q", "-m", "base")
    return target, run(target, "rev-parse", "HEAD").strip()


class FakeRunner:
    def __init__(self, code=0, stdout="", stderr="", missing=()):
        self.code = code
        self.stdout = stdout
        self.stderr = stderr
        self.missing = set(missing)
        self.calls = []

    def __call__(self, argv, root, data=None):
        self.calls.append((tuple(argv), data))
        if argv[0] == "git":
            return hygiene._run(argv, root, data)
        name = Path(argv[0]).stem
        if name in self.missing:
            return subprocess.CompletedProcess(argv, 127, "", f"missing {name}")
        return subprocess.CompletedProcess(argv, self.code, self.stdout, self.stderr)


def plan(tests, *, tree=None, framework="unittest", git=None, check_scope=None):
    value = {
        "schema_version": 1,
        "kind": "engineering-ci-selection-plan",
        "generator": {"name": "engineering-ci", "version": "0.7.0"},
        "git": {"execution_tree": tree, "head_commit": "a" * 40},
        "selection": {
            "tests": tests,
            "framework": framework,
            "src_root": "src",
            "tests_dir": "tests",
        },
    }
    if git is not None:
        value["git"] = git
    if check_scope is not None:
        value["check_scope"] = check_scope
    return value


def test_host_does_not_bypass_weaver_dagger_boundary(tmp_path):
    value = plan(
        [],
        git={"snapshot": "worktree"},
        check_scope=[{"name": "weaver_live", "status": "selected"}],
    )
    runner = FakeRunner()
    result = execution.execute(value, root=tmp_path, runner=runner)
    assert result["status"] == "blocked"
    assert result["checks"][0]["status"] == "not_executed"
    assert not runner.calls


def test_explicit_not_applicable_scope_does_not_fall_back_to_check_names(tmp_path):
    value = plan(
        [],
        git={"snapshot": "worktree"},
        check_scope=[{"name": "weaver_live", "status": "not_applicable"}],
    )
    value["selection"]["checks"] = ["weaver_live"]
    assert execution._selected_checks(value) == []
    assert execution.execute(value, root=tmp_path)["status"] == "not_executed"


def docs_only_plan(base, head, *, paths=("docs/guide.md",)):
    return plan(
        [],
        git={
            "base_ref": base,
            "head_ref": "HEAD",
            "base_commit": base,
            "head_commit": head,
            "tree": None,
            "worktree_dirty": True,
            "snapshot": "worktree",
            "execution_tree": None,
            "changed_paths": list(paths),
        },
        check_scope=[
            {
                "name": "unittest",
                "status": "no_tests",
                "inputs": [],
                "reason": "no mapped tests",
            },
            {
                "name": "docs",
                "status": "selected",
                "inputs": list(paths),
                "reason": "changed documents",
            },
            {
                "name": "tach",
                "status": "not_applicable",
                "inputs": [],
                "reason": "no code changes",
            },
            {
                "name": "vulture",
                "status": "not_applicable",
                "inputs": [],
                "reason": "no code changes",
            },
            {
                "name": "source",
                "status": "not_applicable",
                "inputs": [],
                "reason": "no source changes",
            },
        ],
    )


def test_execute_passes_selected_tests(repo):
    path, _ = repo
    report = execution.execute(plan(["tests.test_pkg"]), root=path)
    assert report["status"] == "passed"
    assert report["exit_code"] == 0
    assert report["targets"] == ["tests.test_pkg"]


def test_execute_propagates_test_failure(repo):
    path, _ = repo
    (path / "tests/test_pkg.py").write_text(
        "import unittest\n\n\nclass F(unittest.TestCase):\n"
        "    def test_fail(self):\n        self.fail('nope')\n",
        encoding="utf-8",
    )
    report = execution.execute(plan(["tests.test_pkg"]), root=path)
    assert report["status"] == "failed"
    assert report["exit_code"] not in {0, 3, 4}


def test_execute_refuses_a_different_tree(repo):
    path, _ = repo
    tree = run(path, "rev-parse", "HEAD^{tree}").strip()
    (path / "src/pkg/sub.py").write_text("VALUE = 99\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "moved")
    report = execution.execute(plan(["tests.test_pkg"], tree=tree), root=path)
    assert report["status"] == "not_executed"
    assert report["reason"] == "tree_mismatch"
    assert report["exit_code"] == 4


def test_execute_without_targets_is_not_executed(repo):
    path, _ = repo
    report = execution.execute(plan([]), root=path)
    assert report["status"] == "not_executed"
    assert report["exit_code"] == 3
    assert report["reason"] == "no_selected_tests"


def test_execute_runs_selected_docs_check_without_tests(repo):
    path, base = repo
    (path / "docs/guide.md").write_text("# changed\n", encoding="utf-8")
    fake = FakeRunner()
    report = execution.execute(
        docs_only_plan(base, base),
        root=path,
        hygiene_config=path / "ci-hygiene.toml",
        runner=fake,
    )
    assert report["status"] == "passed"
    assert report["reason"] is None
    assert report["exit_code"] == 0
    assert report["tests"]["status"] == "not_executed"
    assert report["checks"][0]["name"] == "docs"
    assert report["checks"][0]["status"] == "passed"
    assert report["checks"][0]["check_id"] == "eci/hygiene/docs"
    assert report["checks"][0]["check_version"] == 2
    assert report["checks"][0]["result_id"].startswith("sha256:")
    assert report["checks"][0]["native_result"]["check"]["check_id"] == (
        "eci/hygiene/docs"
    )
    assert report["check_contract"] == {
        "kind": "engineering-ci-check-contract",
        "schema_version": 2,
    }
    assert report["checks"][0]["source_ref"]["snapshot"] == "worktree"
    assert report["checks"][0]["native_result"]["check"]["rule_ref"]["sha256"]
    tools = [call[0] for call in fake.calls if call[0][0] != "git"]
    assert len(tools) == 1
    assert Path(tools[0][0]).stem == "lychee"


def test_execute_propagates_docs_failure_without_tests(repo):
    path, base = repo
    (path / "docs/guide.md").write_text("# changed\n", encoding="utf-8")
    report = execution.execute(
        docs_only_plan(base, base),
        root=path,
        hygiene_config=path / "ci-hygiene.toml",
        runner=FakeRunner(code=2),
    )
    assert report["status"] == "failed"
    assert report["reason"] == "checks_failed"
    assert report["exit_code"] == 1
    assert report["checks"][0]["reason"] == "lychee_findings"


def forwarding_plan(base, path="src/pkg/sub.py"):
    return plan(
        [],
        git={
            "base_ref": base,
            "head_ref": "HEAD",
            "base_commit": base,
            "head_commit": base,
            "snapshot": "worktree",
            "changed_paths": [path],
        },
        check_scope=[{"name": "forwarding", "status": "selected"}],
    )


def test_forwarding_internal_result_is_valid_without_process_exit(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("from .other import VALUE\n", encoding="utf-8")
    report = execution.execute(
        forwarding_plan(base),
        root=path,
        hygiene_config=path / "ci-hygiene.toml",
        runner=FakeRunner(),
    )
    check = report["checks"][0]
    assert check["name"] == "forwarding"
    assert check["check_contract"]["status"] == "valid"
    assert check["native_result"]["native_exit_code"] is None
    assert check["native_result"]["status"] == "advisory"
    assert check["result_id"]


def test_forwarding_parse_failure_stays_failed_and_valid(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("def broken(:\n", encoding="utf-8")
    report = execution.execute(
        forwarding_plan(base),
        root=path,
        hygiene_config=path / "ci-hygiene.toml",
        runner=FakeRunner(),
    )
    check = report["checks"][0]
    assert check["status"] == "failed"
    assert check["reason"] == "module_unparsable"
    assert check["check_contract"]["status"] == "valid"
    assert check["native_result"]["status"] == "failed"


def test_config_failure_is_not_started_and_has_no_native_result(repo):
    path, base = repo
    (path / "docs/guide.md").write_text("# changed\n", encoding="utf-8")
    report = execution.execute(
        docs_only_plan(base, base),
        root=path,
        hygiene_config=path / "missing-hygiene.toml",
        runner=FakeRunner(),
    )
    check = report["checks"][0]
    assert check["execution_started"] is False
    assert check["check_contract"]["status"] == "not_started"
    assert "native_result" not in check
    assert report["status"] == "failed"


def test_tach_missing_config_is_pre_execution_failure(repo):
    path, base = repo
    (path / "tach.toml").unlink()
    value = plan(
        [],
        git={
            "base_ref": base,
            "head_ref": "HEAD",
            "base_commit": base,
            "head_commit": base,
            "snapshot": "worktree",
            "changed_paths": ["src/pkg/sub.py"],
        },
        check_scope=[{"name": "tach", "status": "selected"}],
    )
    report = execution.execute(
        value,
        root=path,
        hygiene_config=path / "ci-hygiene.toml",
        runner=FakeRunner(),
    )
    check = report["checks"][0]
    assert check["name"] == "tach"
    assert check["status"] == "failed"
    assert check["reason"] == "tach_config_missing"
    assert check["execution_started"] is False
    assert check["check_contract"]["status"] == "not_started"
    assert "native_result" not in check
    assert report["status"] == "failed"


def test_invalid_contract_blocks_without_erasing_native_execution(repo, monkeypatch):
    path, base = repo
    (path / "docs/guide.md").write_text("# changed\n", encoding="utf-8")

    def bad_contract(*args, **kwargs):
        raise execution.check_contract.CheckContractError("fixture_invalid_contract")

    monkeypatch.setattr(execution.check_contract, "native_result", bad_contract)
    report = execution.execute(
        docs_only_plan(base, base),
        root=path,
        hygiene_config=path / "ci-hygiene.toml",
        runner=FakeRunner(),
    )
    check = report["checks"][0]
    assert check["status"] == "passed"  # Real native execution fact is retained
    assert check["check_contract"]["status"] == "fixture_invalid_contract"
    assert "native_result" not in check
    assert report["status"] == "blocked"
    assert report["reason"] == "check_contract_invalid"


def test_missing_check_identity_preserves_successful_native_execution(
    repo, monkeypatch
):
    path, base = repo
    (path / "docs/guide.md").write_text("# changed\n", encoding="utf-8")
    original = execution.check_contract.definition_for

    def missing_docs_identity(name):
        return None if name == "docs" else original(name)

    monkeypatch.setattr(
        execution.check_contract, "definition_for", missing_docs_identity
    )
    runner = FakeRunner()
    report = execution.execute(
        docs_only_plan(base, base),
        root=path,
        hygiene_config=path / "ci-hygiene.toml",
        runner=runner,
    )
    check = report["checks"][0]
    assert check["status"] == "passed"
    assert check["reason"] == "check_identity_missing"
    assert check["check_contract"]["status"] == "identity_missing"
    assert "native_result" not in check
    assert report["status"] == "blocked"
    assert report["reason"] == "check_contract_invalid"
    assert any(Path(args[0]).stem == "lychee" for args, _ in runner.calls)


def test_execute_blocks_when_required_checks_are_not_executed(repo):
    path, _ = repo
    value = plan(["tests.test_pkg"])
    value["selection"]["prerequisites"] = ["identity_commit"]
    report = execution.execute(value, root=path)
    assert report["status"] == "blocked"
    assert report["exit_code"] == 5
    assert report["reason"] == "required_checks_not_executed"
    assert report["tests"]["status"] == "passed"
    assert report["prerequisites"][0]["status"] == "not_executed"
    assert report["prerequisites"][0]["reason"] == "prerequisite_parameters_missing"


def test_execute_runs_identity_commit_prerequisite(repo):
    path, base = repo
    value = plan(["tests.test_pkg"])
    value["selection"]["prerequisites"] = ["identity_commit"]
    value["prerequisite_actions"] = {
        "identity_commit": {"type": "git_commit_present", "commit": base}
    }
    report = execution.execute(value, root=path)
    assert report["status"] == "passed"
    assert report["prerequisites"][0]["status"] == "passed"
    assert report["prerequisites"][0]["commit"] == base


def test_execute_fails_missing_identity_commit(repo):
    path, _ = repo
    value = plan(["tests.test_pkg"])
    value["selection"]["prerequisites"] = ["identity_commit"]
    value["prerequisite_actions"] = {
        "identity_commit": {
            "type": "git_commit_present",
            "commit": "0" * 40,
        }
    }
    report = execution.execute(value, root=path)
    assert report["status"] == "failed"
    assert report["reason"] == "prerequisites_failed"
    assert report["prerequisites"][0]["status"] == "failed"


def test_execute_runs_command_prerequisite(repo):
    path, _ = repo
    value = plan(["tests.test_pkg"])
    value["selection"]["prerequisites"] = ["wheel_smoke"]
    value["prerequisite_actions"] = {
        "wheel_smoke": {
            "type": "command",
            "argv": ["python", "-c", "raise SystemExit(0)"],
        }
    }
    report = execution.execute(value, root=path, runner=FakeRunner())
    assert report["status"] == "passed"
    assert report["prerequisites"][0]["status"] == "passed"
    assert report["prerequisites"][0]["exit_code"] == 0


def test_export_without_original_odb_is_not_a_missing_commit(repo):
    path, base = repo
    value = plan(["tests.test_pkg"], git={"snapshot": "worktree"})
    value["selection"]["prerequisites"] = ["identity_commit"]
    value["prerequisite_actions"] = {
        "identity_commit": {"type": "git_commit_present", "commit": base}
    }
    assert execution.execute(value, root=path)["status"] == "passed"
    exported = path.parent / "exported"
    shutil.copytree(path, exported, ignore=shutil.ignore_patterns(".git"))
    result = execution.execute(value, root=exported)
    assert result["status"] == "blocked" and result["exit_code"] == 5
    assert result["tests"]["status"] == "passed"
    assert result["prerequisites"][0]["reason"] == "git_odb_unavailable_or_unbound"


def test_dagger_export_context_does_not_probe_any_host_git_odb(repo, monkeypatch):
    path, base = repo
    value = plan([], git={"snapshot": "worktree"})
    value["selection"]["prerequisites"] = ["identity_commit"]
    value["prerequisite_actions"] = {
        "identity_commit": {"type": "git_commit_present", "commit": base}
    }
    calls = []
    monkeypatch.setattr(selection, "_git", lambda *a: calls.append(a))
    result = execution.execute(value, root=path, execution_context="dagger_export")
    assert calls == []
    assert result["prerequisites"][0]["result_origin"] == "dagger_export_boundary"
    assert result["prerequisites"][0]["status"] == "not_executed"


def test_execute_runs_selected_source_check(repo):
    path, base = repo
    content = b"source-bytes"
    acquired = path / "data/acquired"
    acquired.mkdir(parents=True)
    (acquired / "a.bin").write_bytes(content)
    (path / "data/catalog").mkdir(parents=True)
    (path / "data/catalog/manifest.json").write_text(
        json.dumps(
            [
                {
                    "path": "data/acquired/a.bin",
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "size_bytes": len(content),
                }
            ]
        ),
        encoding="utf-8",
    )
    (path / "ci-sources.toml").write_text(
        """schema_version = 1
manifest = "data/catalog/manifest.json"
acquired_roots = ["data/acquired"]
exempt_paths = []
environment_prefixes = []
require_text_unset = false
""",
        encoding="utf-8",
    )
    value = plan(
        [],
        git={
            "base_ref": base,
            "head_ref": "HEAD",
            "base_commit": base,
            "head_commit": base,
            "snapshot": "worktree",
            "execution_tree": None,
            "changed_paths": ["data/acquired/a.bin"],
        },
        check_scope=[
            {
                "name": "source",
                "status": "selected",
                "inputs": ["data/acquired/a.bin"],
                "reason": "source inputs changed",
            }
        ],
    )
    report = execution.execute(
        value,
        root=path,
        sources_config=path / "ci-sources.toml",
        runner=FakeRunner(),
    )
    assert report["status"] == "passed"
    assert report["checks"][0]["name"] == "source"
    assert report["checks"][0]["status"] == "passed"


def test_execute_propagates_source_failure(repo):
    path, base = repo
    (path / "data/acquired").mkdir(parents=True)
    (path / "data/catalog").mkdir(parents=True)
    (path / "data/catalog/manifest.json").write_text(
        json.dumps(
            [
                {
                    "path": "data/acquired/a.bin",
                    "sha256": hashlib.sha256(b"x").hexdigest(),
                    "size_bytes": 1,
                }
            ]
        ),
        encoding="utf-8",
    )
    (path / "ci-sources.toml").write_text(
        """schema_version = 1
manifest = "data/catalog/manifest.json"
acquired_roots = ["data/acquired"]
exempt_paths = []
environment_prefixes = []
require_text_unset = false
""",
        encoding="utf-8",
    )
    value = plan(
        [],
        git={
            "base_ref": base,
            "head_ref": "HEAD",
            "base_commit": base,
            "head_commit": base,
            "snapshot": "worktree",
            "execution_tree": None,
            "changed_paths": ["data/acquired/a.bin"],
        },
        check_scope=[
            {
                "name": "source",
                "status": "selected",
                "inputs": ["data/acquired/a.bin"],
                "reason": "source inputs changed",
            }
        ],
    )
    report = execution.execute(
        value,
        root=path,
        sources_config=path / "ci-sources.toml",
        runner=FakeRunner(),
    )
    assert report["status"] == "failed"
    assert report["reason"] == "checks_failed"
    assert report["checks"][0]["status"] == "failed"
    assert report["checks"][0]["reason"] == "source_reconcile_failed"


def test_execute_refuses_stale_index_tree(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 2\n", encoding="utf-8")
    run(path, "add", "src/pkg/sub.py")
    index_tree = run(path, "write-tree").strip()
    (path / "src/pkg/sub.py").write_text("VALUE = 3\n", encoding="utf-8")
    run(path, "add", "src/pkg/sub.py")
    value = plan(
        ["tests.test_pkg"],
        git={
            "base_ref": base,
            "head_ref": "HEAD",
            "base_commit": base,
            "head_commit": base,
            "snapshot": "index",
            "index_tree": index_tree,
            "execution_tree": index_tree,
            "changed_paths": ["src/pkg/sub.py"],
        },
    )
    report = execution.execute(value, root=path)
    assert report["status"] == "not_executed"
    assert report["exit_code"] == 4
    assert report["reason"] == "index_tree_mismatch"


def _external_git(path, index, *args):
    env = {**os.environ, "GIT_INDEX_FILE": str(index)}
    return subprocess.run(
        ["git", *args],
        cwd=path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _external_tree(path, index):
    return _external_git(path, index, "write-tree").strip()


def test_execute_uses_plan_index_file_when_worktree_matches(repo, tmp_path):
    path, base = repo
    external = tmp_path / "external-index"
    (path / "docs/new.md").write_text("# new\n", encoding="utf-8")
    _external_git(path, external, "read-tree", "HEAD")
    _external_git(path, external, "add", "docs/new.md")
    report = execution.execute(
        plan(
            [],
            git={
                "base_ref": base,
                "head_ref": "HEAD",
                "base_commit": base,
                "head_commit": base,
                "snapshot": "index",
                "index_tree": _external_tree(path, external),
                "index_file": str(external),
                "changed_paths": ["docs/new.md"],
            },
            check_scope=[{"name": "docs", "status": "selected"}],
        ),
        root=path,
        hygiene_config=path / "ci-hygiene.toml",
        runner=FakeRunner(),
    )
    assert "checks" in report, (report.get("status"), report.get("reason"))
    assert report["checks"][0]["check_contract"]["status"] == "valid"


def test_execute_rejects_conflicting_index_tree_identities(repo, tmp_path):
    path, base = repo
    external = tmp_path / "external-index"
    (path / "docs/new.md").write_text("# new\n", encoding="utf-8")
    _external_git(path, external, "read-tree", "HEAD")
    _external_git(path, external, "add", "docs/new.md")
    external_tree = _external_tree(path, external)
    default_tree = run(path, "write-tree").strip()
    assert external_tree != default_tree
    for execution_tree, index_tree in (
        (external_tree, default_tree),
        (default_tree, external_tree),
    ):
        fake = FakeRunner()
        report = execution.execute(
            plan(
                [],
                git={
                    "base_ref": base,
                    "head_ref": "HEAD",
                    "base_commit": base,
                    "head_commit": base,
                    "snapshot": "index",
                    "execution_tree": execution_tree,
                    "index_tree": index_tree,
                    "index_file": str(external),
                    "changed_paths": ["docs/new.md"],
                },
                check_scope=[{"name": "docs", "status": "selected"}],
            ),
            root=path,
            hygiene_config=path / "ci-hygiene.toml",
            runner=fake,
        )
        assert report["status"] == "not_executed"
        assert report["reason"] == "index_source_identity_mismatch"
        assert not fake.calls


def test_execute_runs_tests_with_single_execution_tree(repo, tmp_path):
    path, base = repo
    external = tmp_path / "external-index"
    (path / "docs/new.md").write_text("# new\n", encoding="utf-8")
    _external_git(path, external, "read-tree", "HEAD")
    _external_git(path, external, "add", "docs/new.md")
    report = execution.execute(
        plan(
            ["tests.test_pkg"],
            git={
                "base_ref": base,
                "head_ref": "HEAD",
                "base_commit": base,
                "head_commit": base,
                "snapshot": "index",
                "execution_tree": _external_tree(path, external),
                "index_file": str(external),
                "changed_paths": ["docs/new.md"],
            },
        ),
        root=path,
    )
    assert report["status"] != "not_executed", report.get("reason")
    assert report["tests"]["status"] == "passed"
    assert report["tests"]["exit_code"] == 0


def test_execute_runs_tests_with_external_index(repo, tmp_path):
    path, base = repo
    external = tmp_path / "external-index"
    (path / "docs/new.md").write_text("# new\n", encoding="utf-8")
    _external_git(path, external, "read-tree", "HEAD")
    _external_git(path, external, "add", "docs/new.md")
    report = execution.execute(
        plan(
            ["tests.test_pkg"],
            git={
                "base_ref": base,
                "head_ref": "HEAD",
                "base_commit": base,
                "head_commit": base,
                "snapshot": "index",
                "index_tree": _external_tree(path, external),
                "index_file": str(external),
                "changed_paths": ["docs/new.md"],
            },
        ),
        root=path,
    )
    assert report["status"] != "not_executed", report.get("reason")
    assert report["tests"]["status"] in {"passed", "failed"}
    assert report["tests"].get("exit_code") != 4


def test_execute_keeps_native_pytest_exit_four(repo, tmp_path):
    path, base = repo
    external = tmp_path / "external-index"
    (path / "docs/new.md").write_text("# new\n", encoding="utf-8")
    _external_git(path, external, "read-tree", "HEAD")
    _external_git(path, external, "add", "docs/new.md")
    report = execution.execute(
        plan(
            ["tests/does_not_exist.py"],
            framework="pytest",
            git={
                "base_ref": base,
                "head_ref": "HEAD",
                "base_commit": base,
                "head_commit": base,
                "snapshot": "index",
                "index_tree": _external_tree(path, external),
                "index_file": str(external),
                "changed_paths": ["docs/new.md"],
            },
        ),
        root=path,
    )
    assert report["status"] == "failed"
    assert report["reason"] != "index_source_refused"
    assert report["tests"]["status"] == "failed"
    assert report["tests"]["exit_code"] == 4
    assert report["native_stderr"]


def test_run_tests_reports_source_refusal_separately(repo):
    path, _ = repo
    capture = {}
    code = selection.run_tests(
        ["tests.test_pkg"],
        root=path,
        framework="unittest",
        index_tree="0" * 40,
        capture=capture,
    )
    assert code == 4
    assert capture["source_refusal"] == "index_tree_mismatch"


def test_execute_rejects_changed_external_index(repo, tmp_path):
    path, base = repo
    external = tmp_path / "external-index"
    (path / "docs/new.md").write_text("# new\n", encoding="utf-8")
    _external_git(path, external, "read-tree", "HEAD")
    _external_git(path, external, "add", "docs/new.md")
    expected = _external_tree(path, external)
    (path / "docs/new.md").write_text("# changed index\n", encoding="utf-8")
    _external_git(path, external, "add", "docs/new.md")
    report = execution.execute(
        plan(
            [],
            git={
                "base_ref": base,
                "head_ref": "HEAD",
                "base_commit": base,
                "head_commit": base,
                "snapshot": "index",
                "index_tree": expected,
                "index_file": str(external),
                "changed_paths": ["docs/new.md"],
            },
            check_scope=[{"name": "docs", "status": "selected"}],
        ),
        root=path,
        hygiene_config=path / "ci-hygiene.toml",
        runner=FakeRunner(),
    )
    assert report["status"] == "not_executed"
    assert report["reason"] == "index_tree_mismatch"


def test_execute_refuses_index_without_tree(repo):
    path, base = repo
    value = plan(
        ["tests.test_pkg"],
        git={
            "base_ref": base,
            "head_ref": "HEAD",
            "base_commit": base,
            "head_commit": base,
            "snapshot": "index",
            "changed_paths": ["src/pkg/sub.py"],
        },
    )
    report = execution.execute(value, root=path)
    assert report["status"] == "not_executed"
    assert report["reason"] == "index_tree_missing"


def test_execute_refuses_index_when_worktree_differs(repo):
    path, base = repo
    index_tree = run(path, "write-tree").strip()
    (path / "src/pkg/sub.py").write_text("VALUE = 3\n", encoding="utf-8")
    value = plan(
        ["tests.test_pkg"],
        git={
            "base_ref": base,
            "head_ref": "HEAD",
            "base_commit": base,
            "head_commit": base,
            "snapshot": "index",
            "index_tree": index_tree,
            "execution_tree": index_tree,
            "changed_paths": ["src/pkg/sub.py"],
        },
    )
    report = execution.execute(value, root=path)
    assert report["status"] == "not_executed"
    assert report["reason"] == "index_worktree_mismatch"


def test_execute_refuses_index_with_untracked_inputs(repo):
    path, base = repo
    index_tree = run(path, "write-tree").strip()
    (path / "docs/untracked.md").write_text("# untracked\n", encoding="utf-8")
    value = plan(
        ["tests.test_pkg"],
        git={
            "base_ref": base,
            "head_ref": "HEAD",
            "base_commit": base,
            "head_commit": base,
            "snapshot": "index",
            "index_tree": index_tree,
            "execution_tree": index_tree,
            "changed_paths": ["src/pkg/sub.py"],
        },
    )
    report = execution.execute(value, root=path)
    assert report["status"] == "not_executed"
    assert report["reason"] == "index_untracked_inputs"


def test_execute_refuses_worktree_content_drift(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 2\n", encoding="utf-8")
    binding = selection.worktree_content_binding(path, ("src/pkg/sub.py",))
    (path / "src/pkg/sub.py").write_text("VALUE = 3\n", encoding="utf-8")
    value = plan(
        ["tests.test_pkg"],
        git={
            "base_ref": base,
            "head_ref": "HEAD",
            "base_commit": base,
            "head_commit": base,
            "snapshot": "worktree",
            "worktree_dirty": True,
            "execution_tree": None,
            "changed_paths": ["src/pkg/sub.py"],
            "content_binding": binding,
        },
    )
    report = execution.execute(value, root=path)
    assert report["status"] == "not_executed"
    assert report["exit_code"] == 4
    assert report["reason"] == "worktree_snapshot_stale"


def test_execute_push_requires_matching_head_commit(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 2\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "after")
    after = run(path, "rev-parse", "HEAD").strip()
    after_tree = run(path, "rev-parse", "HEAD^{tree}").strip()
    run(path, "reset", "--hard", base)
    value = plan(
        ["tests.test_pkg"],
        git={
            "base_ref": base,
            "head_ref": after,
            "base_commit": base,
            "head_commit": after,
            "snapshot": "push",
            "execution_tree": after_tree,
            "changed_paths": ["src/pkg/sub.py"],
        },
    )
    report = execution.execute(value, root=path)
    assert report["status"] == "not_executed"
    assert report["exit_code"] == 4
    assert report["reason"] in {"tree_mismatch", "head_commit_mismatch"}


def test_execute_commit_refuses_dirty_worktree(repo):
    path, base = repo
    tree = run(path, "rev-parse", "HEAD^{tree}").strip()
    (path / "src/pkg/sub.py").write_text("VALUE = 77\n", encoding="utf-8")
    value = plan(
        ["tests.test_pkg"],
        git={
            "base_ref": base,
            "head_ref": "HEAD",
            "base_commit": base,
            "head_commit": base,
            "snapshot": "commit",
            "execution_tree": tree,
            "changed_paths": ["src/pkg/sub.py"],
        },
    )
    report = execution.execute(value, root=path)
    assert report["status"] == "not_executed"
    assert report["reason"] == "worktree_tree_mismatch"


def test_execute_commit_refuses_untracked_inputs(repo):
    path, base = repo
    tree = run(path, "rev-parse", "HEAD^{tree}").strip()
    (path / "docs/untracked.md").write_text("# untracked\n", encoding="utf-8")
    value = plan(
        ["tests.test_pkg"],
        git={
            "base_ref": base,
            "head_ref": "HEAD",
            "base_commit": base,
            "head_commit": base,
            "snapshot": "commit",
            "execution_tree": tree,
            "changed_paths": ["src/pkg/sub.py"],
        },
    )
    report = execution.execute(value, root=path)
    assert report["status"] == "not_executed"
    assert report["reason"] == "worktree_untracked_inputs"


def test_execute_pr_merge_refuses_feature_head(repo):
    path, base = repo
    run(path, "checkout", "-q", "-b", "feature")
    (path / "src/pkg/sub.py").write_text("VALUE = 10\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "feature")
    feature = run(path, "rev-parse", "HEAD").strip()
    merged = selection.changeset(path, base=base, head=feature, mode="pr")
    if not merged.complete or not merged.merge_tree:
        pytest.skip("git merge-tree unavailable")
    if merged.merge_tree == selection._tree_of(path, "HEAD"):
        pytest.skip("merge tree equals feature HEAD tree")
    value = plan(
        ["tests.test_pkg"],
        git={
            "base_ref": base,
            "head_ref": feature,
            "base_commit": merged.base_commit,
            "head_commit": feature,
            "snapshot": "pr_merge",
            "merge_tree": merged.merge_tree,
            "execution_tree": merged.merge_tree,
            "changed_paths": list(merged.paths),
        },
    )
    report = execution.execute(value, root=path)
    assert report["status"] == "not_executed"
    assert report["exit_code"] == 4
    assert report["reason"] == "pr_merge_tree_unmaterialized"


def test_execute_passes_expected_tree_into_run_tests(repo, monkeypatch):
    path, base = repo
    tree = run(path, "rev-parse", "HEAD^{tree}").strip()
    seen = {}

    def fake_run_tests(*args, **kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(selection, "run_tests", fake_run_tests)
    value = plan(
        ["tests.test_pkg"],
        tree=tree,
        git={
            "base_ref": base,
            "head_ref": "HEAD",
            "base_commit": base,
            "head_commit": base,
            "snapshot": "commit",
            "execution_tree": tree,
            "changed_paths": [],
        },
    )
    report = execution.execute(value, root=path)
    assert report["status"] == "passed"
    assert seen.get("expected_tree") == tree


def test_execute_blocks_unvalidated_tach_narrowing(repo):
    path, _ = repo
    value = plan(["tests.test_pkg"])
    value["selection"]["requires_tach_check"] = True
    value["selection"]["tach_graph_validation"] = {"status": "not_executed"}
    report = execution.execute(value, root=path)
    assert report["status"] == "blocked"
    assert report["reason"] == "tach_graph_unvalidated"
    assert report["exit_code"] == 5


def test_execute_blocks_when_coverage_evidence_is_required(repo):
    path, _ = repo
    value = plan(["tests.test_pkg"])
    value["selection"]["coverage_required"] = True
    report = execution.execute(value, root=path)
    assert report["status"] == "blocked"
    assert report["exit_code"] == 5
    assert report["reason"] == "coverage_evidence_required"
    assert report["coverage"]["satisfied"] is False


def test_cli_writes_json_and_exit(repo, tmp_path, capsys):
    path, _ = repo
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan(["tests.test_pkg"])), encoding="utf-8")
    assert (
        execution.main(["--plan", str(plan_path), "--root", str(path), "--json"]) == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "engineering-ci-execution-report"
    assert payload["status"] == "passed"


def test_docs_invokes_lychee_with_explicit_targets(repo):
    path, base = repo
    (path / "docs/guide.md").write_text("# changed\n", encoding="utf-8")
    fake = FakeRunner()
    execution.execute(
        docs_only_plan(base, base),
        root=path,
        hygiene_config=path / "ci-hygiene.toml",
        runner=fake,
    )
    calls = [call for call in fake.calls if Path(call[0][0]).stem == "lychee"]
    assert len(calls) == 1
    argv, data = calls[0]
    # Real Lychee rejects an invocation without explicit inputs; the document
    # set must be passed as arguments, not as stdin data.
    assert data is None
    assert argv[-1].endswith("docs/guide.md")


def test_docs_emits_inventory_and_links_native_results(repo):
    path, base = repo
    (path / "docs/guide.md").write_text("# changed\n", encoding="utf-8")
    report = execution.execute(
        docs_only_plan(base, base),
        root=path,
        hygiene_config=path / "ci-hygiene.toml",
        runner=FakeRunner(),
    )
    check = report["checks"][0]
    assert report["status"] == "passed"
    results = check["native_results"]
    assert [item["tool"]["access_id"] for item in results] == [
        "git/ls-files",
        "lychee/docs",
    ]
    inventory, links = results
    assert inventory["tool"]["executable"] == "git"
    assert Path(links["tool"]["executable"]).stem == "lychee"
    assert check["native_result"]["result_id"] == links["result_id"]
    assert links["input_refs"][0] == {
        "kind": "check_result",
        "result_id": inventory["result_id"],
        "path": "git/ls-files",
    }
    assert links["input_refs"][1]["kind"] == "config"
    satisfaction = check["native_satisfaction"]
    assert satisfaction["status"] == "satisfied"
    assert satisfaction["required"] is False
    assert satisfaction["trusted"] is False
    assert satisfaction["consumer_scope"] == "supplied_results_only"


def test_tool_check_emits_its_declared_entry(repo):
    path, base = repo
    value = plan(
        [],
        git={
            "base_ref": base,
            "head_ref": "HEAD",
            "base_commit": base,
            "head_commit": base,
            "snapshot": "worktree",
            "changed_paths": ["src/pkg/sub.py"],
        },
        check_scope=[{"name": "tach", "status": "selected"}],
    )
    report = execution.execute(
        value,
        root=path,
        hygiene_config=path / "ci-hygiene.toml",
        runner=FakeRunner(),
    )
    check = report["checks"][0]
    assert len(check["native_results"]) == 1
    native = check["native_results"][0]
    assert native["tool"]["access_id"] == "tach/check"
    assert Path(native["tool"]["executable"]).stem == "tach"
    assert check["native_satisfaction"]["status"] == "satisfied"


def test_unsatisfied_native_satisfaction_blocks_the_report(repo):
    path, base = repo
    (path / "docs/guide.md").write_text("# changed\n", encoding="utf-8")
    fake = FakeRunner()

    def failing_inventory(argv, root, data=None):
        # The inventory step fails while the link scan still runs and passes:
        # without the git input the Check cannot be considered satisfied.
        if list(argv)[:2] == ["git", "ls-files"]:
            return subprocess.CompletedProcess(argv, 1, "", "inventory failed")
        return fake(argv, root, data)

    report = execution.execute(
        docs_only_plan(base, base),
        root=path,
        hygiene_config=path / "ci-hygiene.toml",
        runner=failing_inventory,
    )
    check = report["checks"][0]
    assert check["native_satisfaction"]["status"] == "unsatisfied"
    assert report["status"] == "blocked"
    assert report["reason"] == "native_satisfaction_incomplete"


def test_failed_inventory_cannot_become_a_clean_not_applicable(repo):
    path, base = repo
    (path / "docs/guide.md").unlink()
    (path / "README.md").unlink()
    fake = FakeRunner()

    def failing_inventory(argv, root, data=None):
        if list(argv)[:2] == ["git", "ls-files"]:
            return subprocess.CompletedProcess(argv, 1, "", "inventory failed")
        return fake(argv, root, data)

    report = execution.execute(
        docs_only_plan(base, base),
        root=path,
        hygiene_config=path / "ci-hygiene.toml",
        runner=failing_inventory,
    )
    check = report["checks"][0]
    assert report["status"] == "failed"
    assert report["reason"] == "checks_failed"
    assert check["status"] == "failed"
    assert check["reason"] == "git_inventory_failed"
    assert check["execution_started"] is False
    assert check["inventory"]["exit_code"] == 1
    # The inventory actually ran: its native failure must survive even though
    # the link scan never started, and no Lychee result may be fabricated.
    results = check["native_results"]
    assert len(results) == 1
    inventory = results[0]
    assert inventory["tool"]["access_id"] == "git/ls-files"
    assert inventory["status"] == "failed"
    assert inventory["native_exit_code"] == 1
    assert "inventory failed" in inventory["evidence"]["stderr"]
    assert check["native_result"]["result_id"] == inventory["result_id"]
    assert check["native_satisfaction"]["status"] == "unsatisfied"
    assert check["check_contract"]["status"] == "valid"


def test_genuine_no_documents_does_not_become_a_failure(repo):
    path, base = repo
    (path / "docs/guide.md").unlink()
    (path / "README.md").unlink()
    report = execution.execute(
        docs_only_plan(base, base),
        root=path,
        hygiene_config=path / "ci-hygiene.toml",
        runner=FakeRunner(),
    )
    check = report["checks"][0]
    assert report["status"] == "passed"
    assert check["status"] == "not_applicable"
    assert check["reason"] == "no_applicable_documents"
    assert check["inventory"]["exit_code"] == 0


def test_execute_refuses_skip_worktree_and_assume_unchanged_on_commit(repo):
    path, base = repo
    tree = run(path, "rev-parse", "HEAD^{tree}").strip()
    git = {
        "base_ref": base,
        "head_ref": "HEAD",
        "base_commit": base,
        "head_commit": base,
        "snapshot": "commit",
        "execution_tree": tree,
        "changed_paths": [],
    }
    value = plan(["tests.test_pkg"], tree=tree, git=git)

    run(path, "update-index", "--skip-worktree", "src/pkg/sub.py")
    (path / "src/pkg/sub.py").write_text("VALUE = 50\n", encoding="utf-8")
    try:
        report_skip = execution.execute(value, root=path)
        assert report_skip["status"] == "not_executed"
        assert report_skip["exit_code"] == 4
        assert report_skip["reason"] == "checkout_skip_worktree_bytes_mismatch"
    finally:
        run(path, "update-index", "--no-skip-worktree", "src/pkg/sub.py")
        run(path, "checkout", "--", "src/pkg/sub.py")

    run(path, "update-index", "--assume-unchanged", "src/pkg/sub.py")
    (path / "src/pkg/sub.py").write_text("VALUE = 51\n", encoding="utf-8")
    try:
        report_assume = execution.execute(value, root=path)
        assert report_assume["status"] == "not_executed"
        assert report_assume["exit_code"] == 4
        assert report_assume["reason"] == "checkout_assume_unchanged_bytes_mismatch"
    finally:
        run(path, "update-index", "--no-assume-unchanged", "src/pkg/sub.py")
        run(path, "checkout", "--", "src/pkg/sub.py")


def test_execute_verifies_declared_hygiene_and_sources_configs(repo, tmp_path):
    path, _base = repo
    runner = FakeRunner()

    # 1. Digest drift in declared hygiene config refuses tool execution
    hygiene_path = path / "ci-hygiene.toml"
    drift_plan = plan(
        [],
        git={"snapshot": "worktree"},
        check_scope=[{"name": "docs", "status": "selected"}],
    )
    drift_plan["inputs"] = {
        "hygiene": {
            "path": "ci-hygiene.toml",
            "sha256": "0" * 64,
            "schema_version": 1,
        }
    }
    drift_report = execution.execute(drift_plan, root=path, runner=runner)
    assert drift_report["status"] == "failed"
    assert drift_report["checks"][0]["reason"] == "hygiene_config_digest_mismatch"
    assert not runner.calls

    # 2. Override path mismatch is refused
    other_hygiene = path / "alt-hygiene.toml"
    other_hygiene.write_bytes(hygiene_path.read_bytes())
    real_sha = hashlib.sha256(hygiene_path.read_bytes()).hexdigest()
    override_plan = plan(
        [],
        git={"snapshot": "worktree"},
        check_scope=[{"name": "docs", "status": "selected"}],
    )
    override_plan["inputs"] = {
        "hygiene": {"path": "ci-hygiene.toml", "sha256": real_sha, "schema_version": 1}
    }
    override_report = execution.execute(
        override_plan, root=path, hygiene_config=other_hygiene, runner=runner
    )
    assert override_report["checks"][0]["reason"] == "hygiene_config_override_mismatch"

    # 3. Path escape outside repository root is refused
    escape_cfg = tmp_path / "outside-hygiene.toml"
    escape_cfg.write_bytes(hygiene_path.read_bytes())
    escape_plan = plan(
        [],
        git={"snapshot": "worktree"},
        check_scope=[{"name": "docs", "status": "selected"}],
    )
    escape_plan["inputs"] = {"hygiene": {"path": "../outside-hygiene.toml"}}
    escape_report = execution.execute(escape_plan, root=path, runner=runner)
    assert escape_report["checks"][0]["reason"] == "hygiene_config_outside_root"

    # 4. Sources config digest drift is also refused without invoking tools
    (path / "ci-sources.toml").write_text(
        'schema_version = 1\nmanifest = "data/manifest.json"\nacquired_roots = ["data/acquired"]\n',
        encoding="utf-8",
    )
    src_drift = plan(
        [],
        git={"snapshot": "worktree"},
        check_scope=[{"name": "source", "status": "selected"}],
    )
    src_drift["inputs"] = {
        "sources": {"path": "ci-sources.toml", "sha256": "1" * 64, "schema_version": 1}
    }
    src_report = execution.execute(src_drift, root=path, runner=runner)
    assert src_report["status"] == "failed"
    assert src_report["checks"][0]["reason"] == "sources_config_digest_mismatch"
    assert not runner.calls


def test_tach_external_unauthorized_failure_is_preserved_with_attribution(
    repo, monkeypatch
):
    path, _base = repo
    value = plan(
        [],
        git={"snapshot": "worktree"},
        check_scope=[{"name": "tach", "status": "selected"}],
    )

    def fake_hygiene_run(changes, cfg, *, root, runner):
        return {
            "status": "failed",
            "checks": [
                {"name": "tach", "status": "passed", "exit_code": 0, "findings": []},
                {
                    "name": "tach_external",
                    "status": "failed",
                    "exit_code": 1,
                    "reason": "tach_external_failed",
                    "findings": [],
                },
            ],
        }

    monkeypatch.setattr(hygiene, "run", fake_hygiene_run)
    report = execution.execute(
        value, root=path, hygiene_config=path / "ci-hygiene.toml", runner=FakeRunner()
    )
    assert report["status"] == "failed"
    assert report["reason"] == "checks_failed"
    assert report["routing_conflicts"][0]["check"] == "tach_external"
    by_name = {item["name"]: item for item in report["checks"]}
    assert by_name["tach"]["plan_authorized"] is True
    assert by_name["tach"]["status"] == "passed"
    assert by_name["tach_external"]["plan_authorized"] is False
    assert by_name["tach_external"]["selected"] == "not_selected"
    assert by_name["tach_external"]["status"] == "failed"
    assert by_name["tach_external"]["native_result"]["status"] == "failed"


def test_dagger_export_check_origin_is_host_with_dagger_parent(repo):
    path, base = repo
    (path / "docs/guide.md").write_text("# changed\n", encoding="utf-8")
    value = plan(
        [],
        git={
            "base_ref": base,
            "head_ref": "HEAD",
            "base_commit": base,
            "head_commit": base,
            "snapshot": "worktree",
            "changed_paths": ["docs/guide.md"],
        },
        check_scope=[{"name": "docs", "status": "selected"}],
    )
    report = execution.execute(
        value,
        root=path,
        hygiene_config=path / "ci-hygiene.toml",
        execution_context="dagger_export",
        runner=FakeRunner(),
    )
    check = report["checks"][0]
    assert check["execution_context"] == "dagger_export"
    assert check["parent_execution_context"] == "dagger"
    assert check["result_origin"] == "host"
    assert check["native_result"]["result_origin"] == "host"
    assert check["native_result"]["runner_ref"] == "execution:dagger_export"
    assert check["source_ref"]["actual_execution_source"] == "dagger_directory"


def test_target_test_gate_blocks_before_container_startup():
    gated_tach = plan(["tests.test_pkg"], git={"snapshot": "worktree"})
    gated_tach["selection"]["requires_tach_check"] = True
    gated_tach["selection"]["tach_graph_validation"] = {"status": "not_executed"}
    assert execution.target_test_gate(gated_tach) == ("tach_graph_unvalidated", None)

    gated_cov = plan(["tests.test_pkg"], git={"snapshot": "worktree"})
    gated_cov["selection"]["coverage_required"] = True
    assert execution.target_test_gate(gated_cov) == (
        "coverage_evidence_required",
        "not_measured",
    )

    main_source = (ROOT / ".dagger/src/engineering_ci_module/main.py").read_text(
        encoding="utf-8"
    )
    gate_pos = main_source.index("execution.target_test_gate(value)")
    container_pos = main_source.index("self._uv_container(")
    assert gate_pos < container_pos


def test_execute_refuses_ignored_export_subdir_inside_parent_repo(repo):
    path, _base = repo
    (path / ".gitignore").write_text("exports/\n", encoding="utf-8")
    run(path, "add", ".gitignore")
    run(path, "commit", "-q", "-m", "ignore exports")
    head = run(path, "rev-parse", "HEAD").strip()
    tree = run(path, "rev-parse", "HEAD^{tree}").strip()

    exported = path / "exports/sub"
    shutil.copytree(path, exported, ignore=shutil.ignore_patterns(".git", "exports"))
    # Diverge the exported subdirectory bytes while parent repo stays completely clean
    (exported / "src/pkg/sub.py").write_text("VALUE = 999\n", encoding="utf-8")

    assert selection._resolve_git_dir(exported) is None
    assert selection.worktree_tree_refusal(exported, tree) == "source_git_root_mismatch"

    commit_plan = plan(
        [],
        tree=tree,
        git={
            "base_ref": head,
            "head_ref": "HEAD",
            "base_commit": head,
            "head_commit": head,
            "snapshot": "commit",
            "execution_tree": tree,
            "changed_paths": [],
        },
        check_scope=[{"name": "docs", "status": "selected"}],
    )
    report = execution.execute(commit_plan, root=exported)
    assert report["status"] == "not_executed"
    assert report["exit_code"] == 4
    assert report["reason"] == "source_git_root_mismatch"

    # Even with source_tree_id handoff, modified export directory must be rejected
    tampered_plan = json.loads(json.dumps(commit_plan))
    tampered_plan["git"]["source_tree_id"] = selection.tree_content_id(path, tree)
    tampered_plan["check_scope"] = [{"name": "docs", "status": "selected"}]
    tampered_report = execution.execute(tampered_plan, root=exported)
    assert tampered_report["status"] == "not_executed"
    assert tampered_report["reason"] == "source_tree_mismatch"


def test_execution_propagates_code_prefixes_and_blocks_unplanned_tach_external(repo):
    path, _base = repo
    (path / "custom_pkg").mkdir()
    (path / "custom_pkg/mod.py").write_text("X = 1\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "add custom pkg")
    base = run(path, "rev-parse", "HEAD~1").strip()

    sel_text = (path / "ci-selection.toml").read_text(encoding="utf-8")
    (path / "ci-selection.toml").write_text(
        sel_text.split("[prerequisites]")[0]
        + '\n[check_scope]\ncode_prefixes = ["custom_pkg/"]\n',
        encoding="utf-8",
    )
    hygiene_text = (path / "ci-hygiene.toml").read_text(encoding="utf-8")
    (path / "ci-hygiene.toml").write_text(
        hygiene_text.replace("external = false", "external = true"),
        encoding="utf-8",
    )
    run(path, "add", "ci-selection.toml", "ci-hygiene.toml")
    run(path, "commit", "-q", "-m", "custom code prefix and tach external")
    base = run(path, "rev-parse", "HEAD").strip()

    # Modify custom_pkg/mod.py only; default code_prefixes would miss it
    (path / "custom_pkg/mod.py").write_text("X = 2\n", encoding="utf-8")
    changes = selection.changeset(path, base=base, mode="worktree")
    cfg = selection.load_config(path / "ci-selection.toml")
    sel = selection.select(changes, cfg, root=path)
    p = selection.plan(
        sel,
        config=cfg,
        config_path=path / "ci-selection.toml",
        root=path,
        tach_validation={
            "status": "passed",
            "tach_sha256": selection._digest(path / cfg.tach),
            "source_tree": sel.changeset.execution_tree or sel.changeset.tree,
        },
    )
    assert p["selection"]["code_prefixes"] == ["custom_pkg/"]

    runner = FakeRunner()
    report = execution.execute(p, root=path, runner=runner)
    by_name = {item["name"]: item for item in report["checks"]}
    assert by_name["tach"]["status"] == "passed"
    assert by_name["tach_external"]["status"] == "passed"
    assert any("check-external" in call[0] for call in runner.calls)
    assert any("vulture" in call[0] for call in runner.calls)

    # 1. Unauthorized deletion of tach_external from Plan while ci-hygiene.toml has external = true:
    #    execution MUST NOT start tach check-external AND MUST block the report as configured_check_omitted_from_plan
    without_ext = json.loads(json.dumps(p))
    without_ext["check_scope"] = [
        item for item in without_ext["check_scope"] if item["name"] != "tach_external"
    ]
    without_ext["selection"]["check_scope"] = [
        item
        for item in without_ext["selection"].get("check_scope", [])
        if item["name"] != "tach_external"
    ]
    if "expected_checks" in without_ext:
        without_ext["expected_checks"]["checks"] = [
            item
            for item in without_ext["expected_checks"].get("checks", [])
            if item.get("name") != "tach_external"
        ]
        without_ext["expected_checks"]["expected_set_ref"] = (
            selection.expected_checks.set_identity(
                without_ext["expected_checks"]["checks"],
                without_ext["expected_checks"].get("non_check_obligations"),
                schema_version=without_ext["expected_checks"].get("schema_version", 1),
            )
        )
    runner_no_ext = FakeRunner()
    report_no_ext = execution.execute(without_ext, root=path, runner=runner_no_ext)
    external_calls = [
        call[0] for call in runner_no_ext.calls if "check-external" in call[0]
    ]
    assert external_calls == []
    assert report_no_ext["status"] == "blocked"
    assert report_no_ext["reason"] == "configured_check_omitted_from_plan"
    omitted_entry = next(
        item for item in report_no_ext["checks"] if item["name"] == "tach_external"
    )
    assert omitted_entry["status"] == "not_executed"
    assert omitted_entry["execution_started"] is False
    assert omitted_entry["plan_authorized"] is False
    assert omitted_entry["routing_conflict"] == "configured_check_omitted_from_plan"

    # 2. Authorized disablement (external = false in ci-hygiene.toml):
    #    tach check-external is not started AND report passes cleanly
    (path / "ci-hygiene.toml").write_text(hygiene_text, encoding="utf-8")
    p_disabled = selection.plan(
        sel,
        config=cfg,
        config_path=path / "ci-selection.toml",
        root=path,
        tach_validation={
            "status": "passed",
            "tach_sha256": selection._digest(path / cfg.tach),
            "source_tree": sel.changeset.execution_tree or sel.changeset.tree,
        },
    )
    runner_disabled = FakeRunner()
    report_disabled = execution.execute(p_disabled, root=path, runner=runner_disabled)
    assert report_disabled["status"] == "passed"
    assert [
        call[0] for call in runner_disabled.calls if "check-external" in call[0]
    ] == []
    assert all(item["name"] != "tach_external" for item in report_disabled["checks"])


def test_execution_refuses_missing_target_tree_and_deleted_push_ref(repo):
    path, _ = repo
    head = run(path, "rev-parse", "HEAD").strip()

    # 1. Missing target tree on commit/push/pr_merge snapshots is refused before running tools
    for snap, expected_reason in (
        ("commit", "target_tree_missing"),
        ("push", "target_tree_missing"),
        ("pr_merge", "pr_merge_tree_missing"),
    ):
        missing_tree_plan = plan(
            [],
            check_scope=[{"name": "whitespace", "status": "selected", "inputs": []}],
            git={
                "snapshot": snap,
                "execution_tree": None,
                "tree": None,
                "merge_tree": None,
            },
        )
        runner = FakeRunner()
        report = execution.execute(missing_tree_plan, root=path, runner=runner)
        assert report["status"] == "not_executed"
        assert report["exit_code"] == 4
        assert report["reason"] == expected_reason
        assert runner.calls == []

    # 2. Deleted ref push plan has no target tree and is refused by execution without fabricating PASS
    del_changes = selection.changeset(
        path,
        base=head,
        head="refs/heads/gone",
        mode="push",
        before=head,
        after=selection.ZERO_OID,
        local_ref="(delete)",
        remote_ref="refs/heads/gone",
    )
    cfg = selection.load_config(path / "ci-selection.toml")
    del_sel = selection.select(del_changes, cfg, root=path)
    del_plan = selection.plan(
        del_sel,
        config=cfg,
        config_path=path / "ci-selection.toml",
        root=path,
    )
    runner_del = FakeRunner()
    report_del = execution.execute(del_plan, root=path, runner=runner_del)
    assert report_del["status"] == "not_executed"
    assert report_del["exit_code"] == 4
    assert report_del["reason"] == "deleted_ref_no_execution_tree"
    assert runner_del.calls == []

    # 3. Forging a target tree on a deleted ref push plan is refused
    head_tree = run(path, "rev-parse", "HEAD^{tree}").strip()
    forged_del = plan(
        [],
        check_scope=[{"name": "whitespace", "status": "selected", "inputs": []}],
        git={
            "snapshot": "push",
            "ref_action": "delete",
            "execution_tree": head_tree,
            "tree": head_tree,
            "head_commit": None,
        },
    )
    report_forged = execution.execute(forged_del, root=path, runner=FakeRunner())
    assert report_forged["status"] == "not_executed"
    assert report_forged["exit_code"] == 4
    assert report_forged["reason"] == "deleted_ref_forged_target_tree"
