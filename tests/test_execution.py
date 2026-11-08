"""Selection-plan execution: native tests, snapshot refusal, report status."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from eci_core import execution

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


def plan(tests, *, tree=None, framework="unittest"):
    return {
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
