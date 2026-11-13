"""Selection-plan execution: native tests, snapshot refusal, report status."""

import hashlib
import json
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
    assert report["checks"][0]["check_version"] == 1
    assert report["checks"][0]["result_id"].startswith("sha256:")
    assert report["checks"][0]["native_result"]["check"]["check_id"] == (
        "eci/hygiene/docs"
    )
    assert report["check_contract"] == {
        "kind": "engineering-ci-check-contract",
        "schema_version": 1,
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
