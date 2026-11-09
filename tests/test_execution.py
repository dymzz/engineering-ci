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


def test_execute_blocks_when_required_checks_are_not_executed(repo):
    path, _ = repo
    value = plan(["tests.test_pkg"])
    value["check_scope"] = [
        {"name": "docs", "status": "selected", "inputs": ["docs/guide.md"]}
    ]
    value["selection"]["prerequisites"] = ["identity_commit"]
    report = execution.execute(value, root=path)
    assert report["status"] == "blocked"
    assert report["exit_code"] == 5
    assert report["reason"] == "required_checks_not_executed"
    assert report["tests"]["status"] == "passed"
    assert report["checks"][0]["status"] == "not_executed"


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


def test_execute_no_tests_without_checks_passes(repo):
    path, _ = repo
    value = plan([])
    value["selection"]["status"] = "no_tests"
    value["check_scope"] = [
        {"name": "docs", "status": "not_applicable"},
        {"name": "tach", "status": "not_applicable"},
        {"name": "vulture", "status": "not_applicable"},
        {"name": "source", "status": "not_applicable"},
    ]
    report = execution.execute(value, root=path)
    assert report["status"] == "passed"
    assert report["exit_code"] == 0
    assert report["tests"]["status"] == "not_applicable"
    assert report["selection"]["tests"]["selection_status"] == "no_tests"


def test_execute_docs_only_blocks_when_unexecuted(repo):
    path, _ = repo
    value = plan([])
    value["selection"]["status"] = "no_tests"
    value["check_scope"] = [
        {"name": "docs", "status": "selected", "inputs": ["docs/guide.md"]}
    ]
    report = execution.execute(value, root=path)
    assert report["status"] == "blocked"
    assert report["exit_code"] == 5
    assert report["reason"] == "required_checks_not_executed"
    assert report["selection"]["checks"]["docs"]["applicability"] == "selected"
    assert report["execution"]["checks"]["docs"]["status"] == "not_executed"


def test_execute_docs_only_passes_with_check_results(repo):
    path, _ = repo
    value = plan([])
    value["selection"]["status"] = "no_tests"
    value["check_scope"] = [
        {"name": "docs", "status": "selected", "inputs": ["docs/guide.md"]}
    ]
    check_results = {"docs": {"status": "passed", "native_exit_code": 0}}
    report = execution.execute(value, root=path, check_results=check_results)
    assert report["status"] == "passed"
    assert report["exit_code"] == 0
    assert report["execution"]["checks"]["docs"]["status"] == "passed"
    assert report["tests"]["status"] == "not_applicable"


def test_execute_sources_only_blocks_when_unexecuted(repo):
    path, _ = repo
    value = plan([])
    value["selection"]["status"] = "no_tests"
    value["check_scope"] = [
        {"name": "source", "status": "selected", "inputs": [".gitattributes"]}
    ]
    report = execution.execute(value, root=path)
    assert report["status"] == "blocked"
    assert report["exit_code"] == 5
    assert report["reason"] == "required_checks_not_executed"
    assert report["selection"]["checks"]["source"]["applicability"] == "selected"


def test_execute_structured_selection_and_execution_shape(repo):
    path, _ = repo
    value = plan(["tests.test_pkg"])
    value["checks_scope"] = {
        "docs": {"applicability": "not_applicable", "inputs": [], "reason": "no docs changed"},
        "tach": {"applicability": "selected", "inputs": [], "reason": "code changed"},
    }
    check_results = {"tach": {"status": "passed", "native_exit_code": 0}}
    report = execution.execute(value, root=path, check_results=check_results)
    assert report["status"] == "passed"
    assert report["exit_code"] == 0
    assert "snapshot" in report
    assert "selection" in report
    assert report["selection"]["tests"]["selection_status"] == "targeted"
    assert report["selection"]["tests"]["targets"] == ["tests.test_pkg"]
    assert report["selection"]["checks"]["tach"]["applicability"] == "selected"
    assert report["selection"]["checks"]["docs"]["applicability"] == "not_applicable"
    assert "execution" in report
    assert report["execution"]["tests"]["status"] == "passed"
    assert report["execution"]["checks"]["tach"]["status"] == "passed"
