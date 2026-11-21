"""Selection-plan execution: native tests, snapshot refusal, report status."""

import dataclasses
import hashlib
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
    hygiene,
    reconciliation,
    selection,
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


def _tree_blob(repo, tree, relative):
    listing = subprocess.run(
        ["git", "ls-tree", "-r", "-z", tree],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    for record in listing.split("\0"):
        if not record:
            continue
        meta, _separator, name = record.partition("\t")
        if name == relative:
            _mode, kind, oid = meta.split(" ")
            assert kind == "blob"
            return oid
    raise AssertionError(relative)


def test_forwarding_execution_compares_committed_blob_bytes(repo):
    """Plan base_commit echo is not the bytes the forwarding check compared."""
    path, base = repo
    relative = "src/pkg/sub.py"
    (path / relative).write_text("from .other import VALUE\n", encoding="utf-8")
    commit = run(path, "rev-parse", "--verify", f"{base}^{{commit}}").strip()
    tree = run(path, "rev-parse", "--verify", f"{commit}^{{tree}}").strip()
    oid = _tree_blob(path, tree, relative)
    expected = subprocess.run(
        ["git", "cat-file", "-p", oid],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    assert expected == "VALUE = 1\n"
    assert (path / relative).read_text(encoding="utf-8") != expected
    value = forwarding_plan(base)
    report = execution.execute(
        value,
        root=path,
        hygiene_config=path / "ci-hygiene.toml",
    )
    check = report["checks"][0]
    native = check["native_result"]
    observed = native["evidence"]["observation"]
    assert observed["base_commit"] == commit
    assert observed["base_tree"] == tree
    assert observed["paths"] == [
        {
            "path": relative,
            "base_entry": "blob",
            "base_mode": "100644",
            "base_object_oid": oid,
            "base_compared_text_sha256": hashlib.sha256(expected.encode()).hexdigest(),
            "worktree_file_sha256": hashlib.sha256(
                (path / relative).read_bytes()
            ).hexdigest(),
        }
    ]
    assert native["status"] == "advisory"
    assert any(relative in item for item in check["findings"])


def test_forwarding_execution_missing_blob_is_source_failure(repo):
    path, base = repo
    relative = "src/pkg/sub.py"
    (path / relative).write_text(
        "from .other import VALUE\n__all__ = ['VALUE']\n", encoding="utf-8"
    )
    tree = run(path, "rev-parse", "--verify", f"{base}^{{commit}}^{{tree}}").strip()
    oid = _tree_blob(path, tree, relative)
    config_path = path / "ci-hygiene.toml"
    config_path.write_text(
        'schema_version = 1\nchecks = ["forwarding"]\n', encoding="utf-8"
    )
    selection_path = path / "ci-selection.toml"
    selection_path.write_text(
        'schema_version = 1\nchecks = ["hygiene"]\n', encoding="utf-8"
    )
    value = selection.plan(
        selection.Selection(
            changeset=selection.changeset(path, base=base, mode="worktree"),
            tests=(),
            checks=("hygiene",),
            status="selected",
            escalated=False,
        ),
        config=selection.load_config(selection_path),
        config_path=selection_path,
        root=path,
    )
    expected = value["expected_checks"]
    loose = path / ".git" / "objects" / oid[:2] / oid[2:]
    assert loose.is_file()
    os.chmod(loose, stat.S_IWRITE)
    loose.unlink()
    missing = subprocess.run(
        ["git", "cat-file", "-e", oid],
        cwd=path,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert missing.returncode != 0
    resolution = execution.build_source_resolution(value, "forwarding", root=path)
    report = execution.execute(
        value,
        root=path,
        hygiene_config=path / "ci-hygiene.toml",
    )
    assert report["status"] == "failed", report
    check = next(item for item in report["checks"] if item["name"] == "forwarding")
    assert report["reason"] == "checks_failed"
    assert check["status"] == "failed"
    assert check["reason"] == "base_content_unavailable"
    native = _failed_forwarding_native(check)
    source = native["evidence"]["observation"]
    assert source["base_commit"] == base
    assert source["base_tree"] == tree
    assert source["paths"] == [
        {
            "path": relative,
            "base_entry": "blob",
            "base_mode": "100644",
            "base_object_oid": oid,
        }
    ]
    assert source["probes"][-1] == {
        "argv": ["git", "cat-file", "-e", oid],
        "exit_code": missing.returncode,
        "stderr": missing.stderr,
    }
    assert native["source_ref"] == resolution["source_ref"]
    assert relative in native["source_ref"]["verified_input_scope"]
    entry = _forwarding_failure_receipt(expected, resolution, report, native)
    assert entry["reason"] == "result_not_passed"
    assert entry["native_satisfaction"]["status"] == "unsatisfied"


def _forwarding_receipt(expected, resolution, report, native):
    from test_reconciliation import make_observation, make_routing

    observation = make_observation(
        "forwarding-host", (), runner_ref=native["runner_ref"], report=report
    )
    del observation["native_results"]  # Extract membership from the actual report.
    routing = make_routing(
        next(item for item in expected["checks"] if item["name"] == "forwarding"),
        jobs=("forwarding-host",),
        runner_ref=native["runner_ref"],
    )
    receipt = reconciliation.reconcile_expected_results(
        expected,
        routing=[routing],
        resolutions=[resolution],
        observations=[observation],
    )
    reconciliation.validate_receipt(receipt)
    entry = next(item for item in receipt["entries"] if item["name"] == "forwarding")
    return entry, observation


def _forwarding_failure_receipt(
    expected, resolution, report, native, *, binding="insufficient"
):
    entry, observation = _forwarding_receipt(expected, resolution, report, native)
    assert entry["native_status"] == "failed"
    assert entry["binding_status"] == binding, entry
    assert entry["observed_result_ids"] == [native["result_id"]]
    assert entry["selected_result_ids"] == []
    evidence = entry["observed_results"][0]
    assert evidence["run_ref"] == observation["run_ref"]
    assert evidence["attempt_ref"] == observation["attempt_ref"]
    assert evidence["runner_ref"] == native["runner_ref"]
    assert evidence["native_exit_code"] is None
    assert evidence["report_ref"] == observation["report_ref"]
    return entry


@pytest.mark.parametrize("snapshot", ["worktree", "commit"])
def test_forwarding_does_not_trust_forged_deleted_path(repo, snapshot):
    from test_reconciliation import make_observation, make_routing

    path, base = repo
    relative = "src/pkg/sub.py"
    (path / relative).write_text(
        "from .other import VALUE\n__all__ = ['VALUE']\n", encoding="utf-8"
    )
    selection_path = path / "ci-selection.toml"
    selection_path.write_text(
        'schema_version = 1\nchecks = ["hygiene"]\n', encoding="utf-8"
    )
    config_path = path / "ci-hygiene.toml"
    config_path.write_text(
        'schema_version = 1\nchecks = ["forwarding"]\n', encoding="utf-8"
    )
    if snapshot == "commit":
        run(path, "add", "-A")
        run(path, "commit", "-qm", "record target")
    changes = selection.changeset(path, base=base, mode=snapshot)
    value = selection.plan(
        selection.Selection(changes, (), ("hygiene",), "selected", False),
        config=selection.load_config(selection_path),
        config_path=selection_path,
        root=path,
    )
    expected = value["expected_checks"]
    expected_entry = next(
        item for item in expected["checks"] if item["name"] == "forwarding"
    )
    assert expected_entry["applicability"]["status"] == "selected"
    value["git"]["deleted_paths"] = [relative]
    execution.validate_plan(value)
    claimed = dataclasses.replace(
        changes, deleted_paths=(relative,), deleted_paths_resolved=True
    )
    scope = hygiene.check_applicability(
        claimed, hygiene.load_config(config_path), root=path
    )
    assert scope["forwarding"]["status"] == "selected"
    assert scope["forwarding"]["inputs"] == [relative]

    resolution = execution.build_source_resolution(value, "forwarding", root=path)
    report = execution.execute(value, root=path)
    check = next(item for item in report["checks"] if item["name"] == "forwarding")
    assert check["status"] == "advisory", check
    native = check["native_result"]
    check_contract.validate_result(native)
    assert native["status"] == "advisory"
    assert native["native_exit_code"] is None
    assert native["source_ref"] == resolution["source_ref"]
    observation = make_observation(
        "forwarding-host", (), runner_ref=native["runner_ref"], report=report
    )
    del observation["native_results"]
    routing = make_routing(
        expected_entry, jobs=("forwarding-host",), runner_ref=native["runner_ref"]
    )
    receipt = reconciliation.reconcile_expected_results(
        expected,
        routing=[routing],
        resolutions=[resolution],
        observations=[observation],
    )
    reconciliation.validate_receipt(receipt)
    entry = next(item for item in receipt["entries"] if item["name"] == "forwarding")
    assert entry["binding_status"] == "insufficient"
    assert entry["reason"] == "result_not_passed"
    assert entry["native_satisfaction"]["status"] == "unsatisfied"
    assert entry["observed_results"][0]["status"] == "advisory"
    assert entry["selected_result_ids"] == []
    assert entry["observed_result_ids"] == [native["result_id"]]


def test_forwarding_commit_resolves_real_deleted_path_from_tree(repo):
    path, base = repo
    relative = "src/pkg/sub.py"
    run(path, "rm", "--", relative)
    run(path, "commit", "-qm", "delete module")
    changes = selection.changeset(path, base=base, mode="commit")
    assert changes.deleted_paths == (relative,)
    config = dataclasses.replace(
        hygiene.load_config(path / "ci-hygiene.toml"), checks=("forwarding",)
    )
    # Plans with an unresolved deletion list can derive exact absence from Git.
    changes = dataclasses.replace(
        changes, deleted_paths=(), deleted_paths_resolved=False
    )
    scope = hygiene.check_applicability(changes, config, root=path)["forwarding"]
    assert scope["status"] == "not_applicable"
    assert scope["inputs"] == []
    result = hygiene.run(changes, config, root=path)["checks"][0]
    assert result["status"] == "not_applicable"
    assert result["reason"] == "no_python_changes"


@pytest.mark.parametrize("missing", ["blob", "tree"])
def test_forwarding_commit_target_object_unavailable_remains_applicable(repo, missing):
    path, base = repo
    relative = "src/pkg/sub.py"
    (path / relative).write_text("from .other import VALUE\n", encoding="utf-8")
    selection_path = path / "ci-selection.toml"
    selection_path.write_text(
        'schema_version = 1\nchecks = ["hygiene"]\n', encoding="utf-8"
    )
    config_path = path / "ci-hygiene.toml"
    config_path.write_text(
        'schema_version = 1\nchecks = ["forwarding"]\n', encoding="utf-8"
    )
    run(path, "add", "-A")
    run(path, "commit", "-qm", "record forwarding target")
    changes = selection.changeset(path, base=base, mode="commit")
    assert changes.deleted_paths == ()
    plan_value = selection.plan(
        selection.Selection(changes, (), ("hygiene",), "selected", False),
        config=selection.load_config(selection_path),
        config_path=selection_path,
        root=path,
    )
    selected = next(
        row for row in plan_value["check_scope"] if row["name"] == "forwarding"
    )
    assert selected["status"] == "selected"
    # A forged deletion must not hide an unavailable target Tree or Blob.
    plan_value["git"]["deleted_paths"] = [relative]
    changes = dataclasses.replace(
        changes, deleted_paths=(relative,), deleted_paths_resolved=True
    )
    tree = changes.execution_tree
    blob = _tree_blob(path, tree, relative)
    oid = blob if missing == "blob" else tree
    loose = path / ".git" / "objects" / oid[:2] / oid[2:]
    os.chmod(loose, stat.S_IWRITE)
    loose.unlink()

    config = hygiene.load_config(config_path)
    scope = hygiene.check_applicability(changes, config, root=path)["forwarding"]
    assert scope["status"] == "selected"
    assert scope["inputs"] == [relative]
    if missing == "tree":
        argv = ["git", "ls-tree", "-r", "-z", tree]
        probe = hygiene._run(argv, path)
        assert probe.returncode != 0
        result = hygiene.run(changes, config, root=path)["checks"][0]
        assert result["status"] == "failed"
        assert result["reason"] == "current_tree_unavailable"
        assert result["observation"]["probes"][-1] == {
            "argv": argv,
            "exit_code": probe.returncode,
            "stderr": probe.stderr,
        }
        snapshots = execution._prepare_source_snapshots(
            plan_value, [selected], root=path, execution_context="host"
        )
        assert snapshots == {"kind": "unproven"}
        report = execution.execute(plan_value, root=path)
        assert report["status"] == "not_executed", report
        assert report["exit_code"] == 4
        assert not report.get("checks")
        return

    assert _tree_blob(path, tree, relative) == blob
    argv = ["git", "cat-file", "blob", blob]
    probe = hygiene._run(argv, path)
    assert probe.returncode != 0
    resolution = execution.build_source_resolution(plan_value, "forwarding", root=path)
    report = execution.execute(plan_value, root=path)
    assert report["status"] == "failed", report
    check = next(row for row in report["checks"] if row["name"] == "forwarding")
    assert check["reason"] == "current_content_unavailable"
    native = _failed_forwarding_native(check)
    observed = native["evidence"]["observation"]
    assert observed["paths"][0]["current_object_oid"] == blob
    assert observed["probes"][-1] == {
        "argv": argv,
        "exit_code": probe.returncode,
        "stderr": probe.stderr,
    }
    assert native["source_ref"] == resolution["source_ref"]
    entry = _forwarding_failure_receipt(
        plan_value["expected_checks"], resolution, report, native
    )
    assert entry["reason"] == "result_not_passed"
    assert entry["native_satisfaction"]["status"] == "unsatisfied"


def _source_symlink(link, target, *, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows requires privilege to create symlinks")
        raise


@pytest.mark.parametrize("snapshot", ["index", "commit", "push", "pr_merge"])
def test_immutable_source_snapshot_retains_unmaterialized_git_link_member(
    repo, snapshot
):
    path, base = repo
    relative = "src/pkg/absent.py"
    oid = run(path, "rev-parse", "HEAD:src/pkg/sub.py").strip()
    run(path, "update-index", "--add", "--cacheinfo", f"120000,{oid},{relative}")
    tree = run(path, "write-tree").strip()
    assert not (path / relative).exists()
    assert _tree_blob(path, tree, relative) == oid
    value = forwarding_plan(base)
    value["git"].update(snapshot=snapshot, execution_tree=tree)
    proof = execution._prepare_source_snapshots(
        value, [], root=path, execution_context="host"
    )
    assert proof["kind"] == "full"
    assert relative in proof["files"]
    assert proof["binding"]["paths"][relative] is None
    assert proof["binding"]["unverified_paths"] == [relative]


def test_forwarding_commit_dangling_link_limits_full_source_proof(repo):
    path, _ = repo
    link = "src/pkg/unrelated.py"
    _source_symlink(path / link, "missing.py")
    run(path, "config", "core.symlinks", "true")
    run(path, "add", "--", link)
    run(path, "commit", "-qm", "record unrelated link")
    base = run(path, "rev-parse", "HEAD").strip()
    relative = "src/pkg/sub.py"
    (path / relative).write_text("from .other import VALUE\n", encoding="utf-8")
    run(path, "add", "--", relative)
    run(path, "commit", "-qm", "record forwarding change")
    head = run(path, "rev-parse", "HEAD").strip()
    tree = run(path, "rev-parse", "HEAD^{tree}").strip()
    value = forwarding_plan(base)
    value["git"].update(
        snapshot="commit", head_commit=head, tree=tree, execution_tree=tree
    )

    source = execution._prepare_source_snapshots(
        value, [], root=path, execution_context="host"
    )
    assert source["binding"]["unverified_paths"] == [link]
    assert source["binding"]["paths"][relative]
    report = execution.execute(value, root=path)
    assert report["status"] == "passed", report
    native = report["checks"][0]["native_result"]
    assert native["status"] == "advisory"
    assert native["source_ref"]["scope_kind"] == "changed_paths"
    assert native["source_ref"]["binding_status"] == "identity_only"
    proof = execution.build_pre_execution_source_proof(value, "forwarding", root=path)
    assert proof["status"] == "unverified"


@pytest.mark.parametrize(
    ("target_kind", "target_status"),
    [
        ("external", "outside_source"),
        ("internal", "inside_source"),
        ("broken", "missing"),
    ],
)
def test_forwarding_selected_link_checks_target_source(
    repo, target_kind, target_status
):
    path, base = repo
    relative = "src/pkg/sub.py"
    source = path / relative
    if target_kind == "external":
        target = path.parent / "outside.py"
        target.write_text("from somewhere import VALUE\n", encoding="utf-8")
    elif target_kind == "internal":
        target = "target.py"
        (source.parent / target).write_text(
            "from .other import VALUE\n__all__ = ['VALUE']\n", encoding="utf-8"
        )
    else:
        target = "missing.py"
    source.unlink()
    _source_symlink(source, target)

    config_path = path / "ci-selection.toml"
    config_path.write_text(
        'schema_version = 1\nchecks = ["hygiene"]\n', encoding="utf-8"
    )
    hyg_path = path / "ci-hygiene.toml"
    hyg_path.write_text(
        'schema_version = 1\nchecks = ["forwarding"]\n', encoding="utf-8"
    )
    value = selection.plan(
        selection.Selection(
            changeset=selection.changeset(path, base=base, mode="worktree"),
            tests=(),
            checks=("hygiene",),
            status="selected",
            escalated=False,
        ),
        config=selection.load_config(config_path),
        config_path=config_path,
        root=path,
    )
    binding = value["git"]["content_binding"]
    assert binding["unverified_paths"] == [relative]
    if target_kind == "internal":
        assert (
            binding["paths"][relative]
            == hashlib.sha256((source.parent / target).read_bytes()).hexdigest()
        )
    else:
        assert binding["paths"][relative] is None
    if target_kind == "external":
        directory_id = selection.directory_content_id(path)
        target.write_text("VALUE = 2\n", encoding="utf-8")
        assert selection.worktree_content_binding(path, binding["paths"]) == binding
        assert selection.directory_content_id(path) == directory_id

    expected = value["expected_checks"]
    selected = next(item for item in expected["checks"] if item["name"] == "forwarding")
    assert selected["source_ref"]["binding_status"] == "unknown"
    resolution = execution.build_source_resolution(value, "forwarding", root=path)
    report = execution.execute(value, root=path)
    check = next(item for item in report["checks"] if item["name"] == "forwarding")
    if target_kind == "internal":
        # The changed selection configuration also selects an unwired test
        # entry; the Forwarding Native Result remains independently observable.
        assert report["status"] == "blocked", report
        assert report["reason"] == "required_checks_not_executed"
        assert check["status"] == "advisory"
        native = check["native_result"]
        check_contract.validate_result(native)
        observed = next(
            item
            for item in native["evidence"]["observation"]["paths"]
            if item["path"] == relative
        )
        expected_link = subprocess.run(
            ["git", "hash-object", "--stdin"],
            cwd=path,
            input=target,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=True,
        ).stdout.strip()
        assert observed["current_links"] == [
            {"path": relative, "worktree_object_oid": expected_link}
        ]
        assert observed["resolved_target_path"] == "src/pkg/target.py"
        assert observed["worktree_file_sha256"] == binding["paths"][relative]
        assert native["source_ref"] == resolution["source_ref"]
        entry, _ = _forwarding_receipt(expected, resolution, report, native)
        assert entry["binding_status"] == "unknown"
        assert entry["reason"] == "expected_source_unproven"
        assert entry["selected_result_ids"] == []
        alternate = source.with_name("alternate.py")
        alternate.write_bytes((source.parent / target).read_bytes())
        source.unlink()
        _source_symlink(source, alternate.name)
        changed = selection.worktree_content_binding(path, binding["paths"])
        assert changed["paths"][relative] == binding["paths"][relative]
        assert changed["digest"] != binding["digest"]
        stale = execution.execute(value, root=path)
        assert stale["status"] == "not_executed"
        assert stale["reason"] == "worktree_snapshot_stale"
        return
    assert report["status"] == "failed", report
    assert check["reason"] == "forwarding_worktree_link_unsupported"
    native = _failed_forwarding_native(check)
    observed = native["evidence"]["observation"]["paths"][0]
    assert observed["worktree_path_status"] == "link"
    assert observed["link_component"] == relative
    assert observed["link_target_status"] == target_status
    assert "worktree_file_sha256" not in observed
    assert native["source_ref"] == resolution["source_ref"]
    assert native["source_ref"]["scope_kind"] == "changed_paths"
    entry = _forwarding_failure_receipt(
        expected, resolution, report, native, binding="unknown"
    )
    assert entry["reason"] == "expected_source_unproven"
    assert entry["native_satisfaction"] is None
    proof = execution.build_pre_execution_source_proof(value, "forwarding", root=path)
    assert proof["status"] == "unverified"


def test_forwarding_rejects_a_link_in_the_selected_path(repo):
    path, base = repo
    outside = path.parent / "outside"
    outside.mkdir()
    (outside / "sub.py").write_text("VALUE = 1\n", encoding="utf-8")
    _source_symlink(path / "src/linked", outside, directory=True)
    relative = "src/linked/sub.py"
    report = execution.execute(forwarding_plan(base, relative), root=path)
    check = report["checks"][0]
    assert check["reason"] == "forwarding_worktree_link_unsupported"
    observed = _failed_forwarding_native(check)["evidence"]["observation"]["paths"][0]
    assert observed["link_component"] == "src/linked"
    assert observed["link_target_status"] == "outside_source"
    assert "worktree_file_sha256" not in observed


def test_forwarding_rejects_a_link_into_git_metadata(repo):
    path, base = repo
    relative = "src/pkg/sub.py"
    (path / relative).unlink()
    _source_symlink(path / relative, path / ".git/config")
    report = execution.execute(forwarding_plan(base), root=path)
    check = report["checks"][0]
    assert check["reason"] == "forwarding_worktree_link_unsupported"
    observed = _failed_forwarding_native(check)["evidence"]["observation"]["paths"][0]
    assert observed["link_target_status"] == "outside_source"
    assert "worktree_file_sha256" not in observed


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("link_cycle", "forwarding_link_cycle"),
        ("link_depth_exceeded", "forwarding_link_depth_exceeded"),
        ("non_regular", "forwarding_worktree_file_unreadable"),
        ("forwarding_current_link_unbound", "forwarding_current_link_unbound"),
    ],
)
def test_forwarding_records_the_inner_link_error_in_native_result(
    repo, monkeypatch, kind, reason
):
    from engineering_ci._paths import SourcePathError

    path, base = repo
    relative = "src/pkg/sub.py"
    failed_at = "src/pkg/nested.py"
    original_stat = hygiene.stat_source_file

    def first_link(root, selected):
        if selected == relative:
            raise SourcePathError("link", Path(relative))
        return original_stat(root, selected)

    def interrupted_chain(root, selected, *, validate_link, validate_target):
        validate_link(selected, b"alias.py")
        validate_link("src/pkg/alias.py", b"nested.py")
        raise SourcePathError(kind, Path(failed_at))

    monkeypatch.setattr(hygiene, "stat_source_file", first_link)
    monkeypatch.setattr(hygiene, "read_internal_linked_source", interrupted_chain)
    report = execution.execute(forwarding_plan(base), root=path)
    assert report["status"] == "failed"
    check = report["checks"][0]
    assert check["reason"] == reason
    native = _failed_forwarding_native(check)
    observed = native["evidence"]["observation"]["paths"][0]
    assert observed["link_error"] == {"kind": kind, "component": failed_at}
    assert [link["path"] for link in observed["current_links"]] == [
        relative,
        "src/pkg/alias.py",
    ]
    assert "worktree_file_sha256" not in observed


@pytest.mark.parametrize("kind", ["cycle", "depth"])
def test_forwarding_reports_real_link_chain_failure(repo, kind):
    path, base = repo
    relative = "src/pkg/sub.py"
    source = path / relative
    source.unlink()
    if kind == "cycle":
        _source_symlink(source, "alias.py")
        _source_symlink(source.parent / "alias.py", source.name)
        expected_kind = "link_cycle"
        expected_component = relative
        expected_chain = [relative, "src/pkg/alias.py"]
    else:
        _source_symlink(source, "link_00.py")
        for index in range(40):
            _source_symlink(
                source.parent / f"link_{index:02d}.py", f"link_{index + 1:02d}.py"
            )
        expected_kind = "link_depth_exceeded"
        expected_component = "src/pkg/link_39.py"
        expected_chain = [relative] + [
            f"src/pkg/link_{index:02d}.py" for index in range(39)
        ]

    report = execution.execute(forwarding_plan(base), root=path)
    assert report["status"] == "failed"
    check = report["checks"][0]
    assert check["reason"] == f"forwarding_{expected_kind}"
    native = _failed_forwarding_native(check)
    observed = native["evidence"]["observation"]["paths"][0]
    assert observed["link_error"] == {
        "kind": expected_kind,
        "component": expected_component,
    }
    assert [link["path"] for link in observed["current_links"]] == expected_chain
    assert "worktree_file_sha256" not in observed


def test_forwarding_unbound_nested_git_link_records_the_failed_component(repo):
    path, base = repo
    relative = "src/pkg/sub.py"
    source = path / relative
    alias = source.with_name("alias.py")
    source.unlink()
    _source_symlink(source, alias.name)
    _source_symlink(alias, "target.py")
    (source.parent / "target.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source.parent / "other.py").write_text("VALUE = 1\n", encoding="utf-8")
    run(path, "config", "core.symlinks", "true")
    run(path, "add", "--", "src/pkg")
    run(path, "commit", "-qm", "record nested links")
    head = run(path, "rev-parse", "HEAD").strip()
    tree = run(path, "rev-parse", "HEAD^{tree}").strip()
    first_oid = run(path, "rev-parse", f"HEAD:{relative}").strip()
    second_oid = run(path, "rev-parse", "HEAD:src/pkg/alias.py").strip()

    # Exercise the native check after a link changes from the selected Tree,
    # as it could between a snapshot preflight and the actual file read.
    alias.unlink()
    _source_symlink(alias, "other.py")
    value = forwarding_plan(base)
    value["git"].update(
        snapshot="commit", head_commit=head, tree=tree, execution_tree=tree
    )
    check = execution.run_host_check(
        value, {"name": "forwarding", "status": "selected"}, root=path
    )
    assert check["reason"] == "forwarding_current_link_unbound"
    native = _failed_forwarding_native(check)
    observed = native["evidence"]["observation"]["paths"][0]
    assert observed["link_error"] == {
        "kind": "forwarding_current_link_unbound",
        "component": "src/pkg/alias.py",
    }
    assert observed["current_links"][0]["tree_object_oid"] == first_oid
    assert observed["current_links"][1]["path"] == "src/pkg/alias.py"
    assert observed["current_links"][1]["worktree_object_oid"] != second_oid
    assert "worktree_file_sha256" not in observed


def test_forwarding_commit_internal_link_binds_both_git_objects(repo, monkeypatch):
    from engineering_ci import _paths

    path, base = repo
    relative = "src/pkg/sub.py"
    target = "src/pkg/other.py"
    target_bytes = b"from .sub import VALUE\n__all__: list[str] = ['VALUE']\n"
    (path / target).write_bytes(target_bytes)
    (path / relative).unlink()
    _source_symlink(path / relative, "other.py")
    run(path, "config", "core.symlinks", "true")
    run(path, "add", "--", relative, target)
    run(path, "commit", "-qm", "record internal module link")
    head = run(path, "rev-parse", "HEAD").strip()
    tree = run(path, "rev-parse", "HEAD^{tree}").strip()
    link_oid = run(path, "rev-parse", f"HEAD:{relative}").strip()
    target_oid = run(path, "rev-parse", f"HEAD:{target}").strip()
    value = forwarding_plan(base)
    value["git"].update(
        snapshot="commit", head_commit=head, tree=tree, execution_tree=tree
    )
    report = execution.execute(value, root=path)
    check = report["checks"][0]
    assert check["status"] == "advisory", report
    native = check["native_result"]
    check_contract.validate_result(native)
    observed = native["evidence"]["observation"]["paths"][0]
    assert observed["current_git_mode"] == "120000"
    assert observed["current_object_oid"] == link_oid
    assert observed["current_links"] == [
        {"path": relative, "worktree_object_oid": link_oid, "tree_object_oid": link_oid}
    ]
    assert observed["resolved_target_path"] == target
    assert observed["target_object_oid"] == target_oid
    assert observed["worktree_file_sha256"] == hashlib.sha256(target_bytes).hexdigest()
    assert native["source_ref"]["scope_kind"] == "changed_paths"
    assert native["source_ref"]["binding_status"] == "identity_only"
    (path / target).write_bytes(b"VALUE = 7\n")
    stale = execution.execute(value, root=path)
    assert stale["status"] == "not_executed"
    assert stale["reason"] == "worktree_tree_mismatch"

    (path / target).write_bytes(target_bytes)
    loose = path / ".git" / "objects" / target_oid[:2] / target_oid[2:]
    os.chmod(loose, stat.S_IWRITE)
    loose.unlink()
    read_paths = []
    original_read = _paths.read_source_file

    def observed_read(root, relative):
        read_paths.append(Path(relative).as_posix())
        return original_read(root, relative)

    monkeypatch.setattr(_paths, "read_source_file", observed_read)
    config = dataclasses.replace(
        hygiene.load_config(path / "ci-hygiene.toml"), checks=("forwarding",)
    )
    changes = execution._changeset_from_plan(value, path)
    refused = hygiene.run(changes, config, root=path)["checks"][0]
    assert refused["status"] == "failed"
    assert refused["reason"] == "current_content_unavailable"
    assert refused["observation"]["probes"][-1]["argv"] == [
        "git",
        "cat-file",
        "blob",
        target_oid,
    ]
    assert refused["observation"]["probes"][-1]["exit_code"] != 0
    assert target not in read_paths


@pytest.mark.parametrize("link_is_base", [False, True])
@pytest.mark.parametrize("snapshot", ["commit", "worktree"])
def test_forwarding_keeps_git_link_mode_on_plain_file_checkout(
    repo, link_is_base, snapshot
):
    path, base = repo
    relative = "src/pkg/sub.py"
    source = path / relative
    source.write_text("from .other import VALUE\n", encoding="utf-8", newline="\n")
    run(path, "config", "core.symlinks", "false")
    oid = run(path, "hash-object", "-w", relative).strip()
    run(path, "update-index", "--cacheinfo", f"120000,{oid},{relative}")
    run(path, "commit", "-qm", "record link mode")
    head = run(path, "rev-parse", "HEAD").strip()
    if link_is_base:
        base = head
        run(path, "update-index", "--cacheinfo", f"100644,{oid},{relative}")
        run(path, "commit", "-qm", "restore regular file mode")
        head = run(path, "rev-parse", "HEAD").strip()
    tree = run(path, "rev-parse", "HEAD^{tree}").strip()
    value = forwarding_plan(base)
    if snapshot == "commit":
        value["git"].update(
            snapshot=snapshot, head_commit=head, tree=tree, execution_tree=tree
        )
    report = execution.execute(value, root=path)
    assert report["status"] == "failed", report
    check = report["checks"][0]
    side = "base" if link_is_base else "current"
    assert check["reason"] == f"forwarding_{side}_git_mode_unsupported"
    observed = _failed_forwarding_native(check)["evidence"]["observation"]["paths"][0]
    assert observed["base_mode"] == ("120000" if link_is_base else "100644")
    if snapshot == "commit":
        assert observed["current_git_mode"] == ("100644" if link_is_base else "120000")
    elif not link_is_base:
        assert observed["index_mode"] == "120000"
    assert "base_compared_text_sha256" not in observed
    assert "worktree_file_sha256" not in observed


def _failed_forwarding_native(check):
    assert check["exit_code"] is None
    assert check["check_contract"]["status"] == "valid"
    native = check["native_result"]
    check_contract.validate_result(native)
    assert native["status"] == "failed"
    assert native["native_exit_code"] is None
    assert check["result_id"] == native["result_id"]
    satisfaction = check["native_satisfaction"]
    assert satisfaction["status"] == "not_applicable"
    assert satisfaction["reason"] == "no_tool_requirements"
    assert satisfaction["result_ids"] == [native["result_id"]]
    assert satisfaction["selected_result_ids"] == []
    return native


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("tree", "base_tree_unavailable"),
        ("commit", "base_odb_unavailable"),
        ("listing", "base_tree_listing_invalid"),
    ],
)
def test_forwarding_source_failures_keep_internal_result(repo, kind, reason):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("from .other import VALUE\n", encoding="utf-8")
    if kind != "listing":
        oid = run(path, "rev-parse", f"{base}^{{{kind}}}").strip()
        loose = path / ".git" / "objects" / oid[:2] / oid[2:]
        os.chmod(loose, stat.S_IWRITE)
        loose.unlink()

    def runner(argv, root):
        if kind == "listing" and argv[:2] == ["git", "ls-tree"]:
            return subprocess.CompletedProcess(argv, 0, "truncated listing", "")
        return hygiene._run(argv, root)

    report = execution.execute(forwarding_plan(base), root=path, runner=runner)
    assert report["status"] == "failed"
    check = report["checks"][0]
    assert check["reason"] == reason
    native = _failed_forwarding_native(check)
    probe = native["evidence"]["observation"]["probes"][-1]
    if kind == "listing":
        assert probe["exit_code"] == 0
        assert probe["stderr"] == ""
        assert native["evidence"]["observation"]["tree_parse_error"]
    else:
        assert probe["exit_code"] != 0


def test_forwarding_dagger_export_without_git_context_is_not_started(repo, tmp_path):
    path, base = repo
    root = tmp_path / "export"
    shutil.copytree(path, root, ignore=shutil.ignore_patterns(".git"))
    (root / "src/pkg/sub.py").write_text("from .other import VALUE\n", encoding="utf-8")
    report = execution.execute(
        forwarding_plan(base), root=root, execution_context="dagger_export"
    )
    assert report["status"] == "blocked"
    check = report["checks"][0]
    assert check["execution_context"] == "dagger_export"
    assert check["reason"] == "forwarding_git_context_unavailable"
    assert check["status"] == "not_executed"
    assert check["execution_started"] is False
    assert check["check_contract"]["status"] == "not_started"
    assert "native_result" not in check
    assert check["observation"]["git_context"] == "unavailable"
    assert len(check["observation"]["probes"]) == 1


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


@pytest.mark.parametrize(
    ("object_format", "content", "native_exit"),
    [("sha1", b"VALUE = 1\n", 0), ("sha256", b"VALUE = 1 \n", 2)],
)
def test_first_commit_index_report_and_reconciliation(
    tmp_path, object_format, content, native_exit
):
    from test_reconciliation import _attach_effective_profile_policy

    from engineering_ci import expected_checks, hook_checks, hooks

    path = tmp_path / "repository"
    path.mkdir()
    run(path, "init", "-q", "-b", "main", f"--object-format={object_format}")
    run(path, "config", "user.email", "execution@example.invalid")
    run(path, "config", "user.name", "Execution Test")
    config_path = path / "ci-selection.toml"
    config_path.write_text(
        'schema_version = 1\nchecks = ["hygiene"]\nstrict_modules = false\n'
        'ignored_files = ["ci-selection.toml", "ci-hygiene.toml", '
        '"ci-effective-profile.json", "tach.toml"]\n',
        encoding="utf-8",
        newline="\n",
    )
    (path / "ci-hygiene.toml").write_text(
        'schema_version = 1\nchecks = ["whitespace", "forwarding"]\n',
        encoding="utf-8",
        newline="\n",
    )
    (path / "src").mkdir()
    target = path / "src" / "initial.py"
    target.write_bytes(content)
    (path / "tach.toml").write_text(
        'source_roots = ["src"]\n[[modules]]\npath = "initial"\ndepends_on = []\n',
        encoding="utf-8",
        newline="\n",
    )

    names = ["whitespace", "forwarding"]
    profile = expected_checks.build_effective_profile(
        profile_ref=check_contract.profile_ref("eci/default", "1.0.0"),
        profile_revision="initial-index",
        approval_source="governance-signed",
        effective_at="2026-11-16T00:00:00Z",
        evaluated_at="2026-11-16T00:00:00Z",
        context="index",
        action="index",
        target="HEAD",
        declared_checks=names,
        required_obligations=[
            {"obligation_ref": f"req-{name}", "check_names": [name]} for name in names
        ],
        governed_rules=expected_checks.governed_domain_rules(
            path, config_path=config_path
        ),
        domain_complete=True,
        authority_verified=True,
    )
    _attach_effective_profile_policy(config_path, profile)
    config_path.write_bytes(config_path.read_text(encoding="utf-8").encode("utf-8"))
    (path / "ci-effective-profile.json").write_text(
        json.dumps(profile), encoding="utf-8"
    )
    run(path, "add", "-A")
    event = hooks.capture_pre_commit(path)
    invocation = hooks.persist_event(path, event)
    binding = hooks.load_invocation_binding(invocation.invocation_path)
    changes = selection.changeset(path, base="HEAD", mode="index")
    assert changes.complete and "src/initial.py" in changes.paths
    assert changes.base_commit is None and changes.head_commit is None
    assert changes.base_tree == event["base_tree"]
    assert changes.execution_tree == event["index"]["tree"]
    assert (
        selection.changeset(path, base="refs/heads/missing", mode="index").error
        == "head_unresolved"
    )
    working = b"VALUE = 2\n" if native_exit else b"VALUE = 2 \n"
    target.write_bytes(working)
    (path / "ci-hygiene.toml").write_text("invalid working config", encoding="utf-8")
    assert hook_checks.prepare(path, binding)["exit_code"] == 0
    selected_plan = json.loads(
        invocation.invocation_path.with_suffix(".plan").read_bytes()
    )["plan"]
    assert selected_plan["selection"]["status"] == "no_tests"
    expected = selected_plan["expected_checks"]
    report = hook_checks.run_checks(path, binding)
    assert "checks" in report, report.get("reason")
    checks = {entry["name"]: entry for entry in report["checks"]}
    assert checks["whitespace"]["exit_code"] == native_exit
    assert checks["forwarding"]["status"] == "passed"
    assert report["status"] == ("failed" if native_exit else "passed")
    baseline = {"base_tree": event["base_tree"], "baseline_status": "unborn"}
    assert checks["whitespace"]["native_result"]["baseline_ref"] == baseline
    assert expected["event_ref"]["base_tree"] == event["base_tree"]

    with pytest.raises(hooks.HookEventError, match="hook_final_result_missing"):
        hook_checks.consume_index_result(path, binding)
    outcome = hook_checks.reconcile(path, binding)
    receipt = outcome["receipt"]
    reconciliation.validate_receipt(receipt)
    entries = {entry["name"]: entry for entry in receipt["entries"]}
    assert entries["forwarding"]["binding_status"] == "matched"
    assert entries["whitespace"]["binding_status"] == (
        "insufficient" if native_exit else "matched"
    )
    assert entries["forwarding"]["evidence_refs"]["baseline_source_ref"]["name"] == (
        "git-odb-empty-tree"
    )
    assert reconciliation.hook_exit_code([receipt]) == (1 if native_exit else 0)
    assert hook_checks.consume_index_result(path, binding)["exit_code"] == int(
        bool(native_exit)
    )
    assert bool(outcome["exit_code"]) == bool(native_exit)
    assert target.read_bytes() == working

    if not native_exit:
        invalid_plan = json.loads(json.dumps(selected_plan))
        invalid_plan["git"]["base_tree"] = changes.index_tree
        assert execution.execute(invalid_plan, root=path)["reason"] == (
            "index_unborn_baseline_invalid"
        )
        run(path, "commit", "-qm", "first commit")
        assert execution.execute(selected_plan, root=path)["reason"] == (
            "index_unborn_baseline_stale"
        )


def test_push_reconciles_each_target_with_its_approved_profile(tmp_path):
    from test_reconciliation import _attach_effective_profile_policy

    from engineering_ci import expected_checks, hook_checks, hooks

    root = tmp_path / "repository"
    root.mkdir()
    run(root, "init", "-q", "-b", "main")
    run(root, "config", "user.email", "execution@example.invalid")
    run(root, "config", "user.name", "Execution Test")
    config = root / "ci-selection.toml"
    selection_config = (
        'schema_version = 1\nchecks = ["hygiene"]\nstrict_modules = false\n'
        '[push]\nnew_ref_baseline = "refs/heads/baseline"\n'
    )
    (root / "ci-hygiene.toml").write_bytes(
        b'schema_version = 1\nchecks = ["whitespace"]\n'
    )
    (root / "src").mkdir()
    source = root / "src" / "sample.py"
    source.write_bytes(b"VALUE = 1\n")
    (root / "tach.toml").write_bytes(
        b'source_roots = ["src"]\n[[modules]]\npath = "sample"\ndepends_on = []\n'
    )
    approvals = []
    profile_paths = []
    for action, branch in (("create", "topic"), ("update", "main")):
        target = f"refs/heads/{branch}"
        profile = expected_checks.build_effective_profile(
            profile_ref=check_contract.profile_ref("eci/local-push", "1.0.0"),
            profile_revision=action,
            approval_source="governance-signed",
            effective_at="2026-11-16T00:00:00Z",
            evaluated_at="2026-11-16T00:00:00Z",
            context="push",
            action=action,
            target=target,
            declared_checks=["whitespace"],
            required_obligations=[
                {
                    "obligation_ref": "req-whitespace",
                    "check_names": ["whitespace"],
                    "target_refs": [target],
                }
            ],
            domain_complete=True,
            authority_verified=True,
        )
        config.write_bytes(selection_config.encode())
        _attach_effective_profile_policy(config, profile)
        approvals.append(
            config.read_text(encoding="utf-8").split("[effective_profile]")[1]
        )
        filename = f"push-{action}.json"
        (root / filename).write_text(json.dumps(profile), encoding="utf-8")
        profile_paths.append(filename)
    config.write_bytes(
        (
            selection_config
            + "".join(f"\n[[effective_profile]]{part}" for part in approvals)
        ).encode()
    )
    run(root, "add", "-A")
    run(root, "commit", "-qm", "approved fixture policies")
    baseline = run(root, "rev-parse", "HEAD").strip()
    run(root, "branch", "baseline")
    run(root, "switch", "-c", "topic")
    source.write_bytes(b"VALUE = 2\n")
    run(root, "add", "src/sample.py")
    run(root, "commit", "-qm", "topic change")
    topic = run(root, "rev-parse", "HEAD").strip()
    run(root, "switch", "main")
    source.write_bytes(b"VALUE = 3 \n")
    run(root, "add", "src/sample.py")
    run(root, "commit", "-qm", "main change")
    head = run(root, "rev-parse", "HEAD").strip()
    source.write_bytes(b"VALUE = 9\n")
    event = hooks.capture_pre_push(
        root,
        "origin",
        "remote repo",
        f"refs/heads/topic {topic} refs/heads/topic {'0' * len(topic)}\n"
        f"refs/heads/main {head} refs/heads/main {baseline}\n",
    )
    invocation = hooks.persist_event(root, event)
    binding = hooks.load_invocation_binding(invocation.invocation_path)
    prepared = hook_checks.prepare_push(root, binding, effective_profiles=profile_paths)
    assert prepared["exit_code"] == 0
    assert [ref["after"] for ref in prepared["references"]] == [topic, head]
    assert prepared["references"][0]["before"] == baseline
    reports = hook_checks.run_push_checks(root, binding)["references"]
    assert [ref["report"]["status"] for ref in reports] == ["passed", "failed"], [
        ref["report"]["reason"] for ref in reports
    ]
    outcome = hook_checks.reconcile_push(root, binding)
    assert outcome["exit_code"] != 0
    assert [ref["receipt"]["status"] for ref in outcome["references"]] == [
        "matched",
        "partial",
    ]
    assert source.read_bytes() == b"VALUE = 9\n"


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

    plan_exec_source = (
        ROOT / ".dagger/src/engineering_ci_module/plan_execution.py"
    ).read_text(encoding="utf-8")
    native_section = plan_exec_source.split("async def _native_results(", 1)[1].split(
        "async def ", 1
    )[0]
    dag_section = plan_exec_source.split("async def _execute_dag(", 1)[1].split(
        "async def execute(", 1
    )[0]
    assert native_section.index(
        "execution.target_test_gate(value)"
    ) < native_section.index("_tests(")
    assert dag_section.index("execution.target_test_gate(value)") < dag_section.index(
        "_tests("
    )


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


def test_pr90_generated_paths_and_test_requirement_failure_interface(repo):
    path, base = repo
    (path / "docs/generated.md").write_text("# generated doc\n", encoding="utf-8")
    p = plan(["tests.test_main"], git={"base": base})
    assert execution.test_requirement_failure(p) is None

    no_tests_plan = plan([], git={"base": base})
    assert execution.test_requirement_failure(no_tests_plan) is None

    tach_blocked_plan = plan(["tests.test_main"], git={"base": base})
    tach_blocked_plan["selection"]["requires_tach_check"] = True
    assert (
        execution.test_requirement_failure(tach_blocked_plan)
        == "tach_graph_unvalidated"
    )

    cov_blocked_plan = plan(["tests.test_main"], git={"base": base})
    cov_blocked_plan["selection"]["coverage_required"] = True
    assert (
        execution.test_requirement_failure(cov_blocked_plan)
        == "coverage_evidence_required"
    )

    cfg = selection.load_config(path / "ci-selection.toml")
    real_sel = selection.select(selection.changeset(path, base=base), cfg, root=path)
    real_plan = selection.plan(
        real_sel,
        config=cfg,
        config_path=path / "ci-selection.toml",
        root=path,
    )
    gen_refs = [{"path": "docs/generated.md", "sha256": "a" * 64}]
    runner = FakeRunner()
    hyg_results, hyg_failed = execution._run_hygiene_checks(
        real_plan,
        [{"name": "docs", "status": "selected", "inputs": []}],
        root=path,
        hygiene_config=None,
        runner=runner,
        extra_paths=("docs/generated.md",),
        generated_input_refs=gen_refs,
    )
    assert hyg_failed is False
    docs_res = next(r for r in hyg_results if r["name"] == "docs")
    assert docs_res["status"] == "passed"
    assert docs_res["generated_input_refs"] == gen_refs
    lychee_calls = [call for call in runner.calls if call[0][0] == "lychee"]
    assert len(lychee_calls) == 1
    assert "docs/generated.md" in (lychee_calls[0][1] or " ".join(lychee_calls[0][0]))


def test_p1_2_selection_to_execution_event_external_handoff(repo, tmp_path):
    path, _base = repo
    (path / "custom_pkg").mkdir()
    (path / "custom_pkg/mod.py").write_text("X = 1\n", encoding="utf-8")
    (path / "data/catalog").mkdir(parents=True)
    content = b"source-bytes"
    (path / "data/acquired").mkdir(parents=True)
    (path / "data/acquired/a.bin").write_bytes(content)
    (path / "data/catalog/manifest.json").write_text(
        json.dumps(
            [
                {
                    "path": "data/acquired/a.bin",
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "size_bytes": len(content),
                }
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "add custom pkg and sources catalog")
    base = run(path, "rev-parse", "HEAD").strip()

    # In-repo configs deliberately have different/stale settings than external policy.
    in_repo_hygiene = (path / "ci-hygiene.toml").read_text(encoding="utf-8")
    assert "external = false" in in_repo_hygiene
    ext_dir = tmp_path / "external_policies"
    ext_dir.mkdir()
    ext_sel = ext_dir / "ci-selection.toml"
    ext_sel.write_text(
        (path / "ci-selection.toml")
        .read_text(encoding="utf-8")
        .split("[prerequisites]")[0]
        + '\n[check_scope]\ncode_prefixes = ["custom_pkg/"]\n',
        encoding="utf-8",
    )
    ext_hyg = ext_dir / "ci-hygiene.toml"
    ext_hyg.write_text(
        in_repo_hygiene.replace("external = false", "external = true"),
        encoding="utf-8",
    )
    ext_src = ext_dir / "ci-sources.toml"
    ext_src.write_text(
        """schema_version = 1
manifest = "data/catalog/manifest.json"
acquired_roots = ["data/acquired"]
exempt_paths = ["data/exempt.txt"]
environment_prefixes = []
require_text_unset = false
""",
        encoding="utf-8",
    )

    (path / "custom_pkg/mod.py").write_text("X = 2\n", encoding="utf-8")
    (path / "data/exempt.txt").write_text("allowed\n", encoding="utf-8")
    changes = selection.changeset(path, base=base, mode="worktree")
    cfg = selection.load_config(ext_sel)
    sel = selection.select(changes, cfg, root=path)
    p = selection.plan(
        sel,
        config=cfg,
        config_path=ext_sel,
        hygiene_config_path=ext_hyg,
        sources_config_path=ext_src,
        root=path,
        tach_validation={
            "status": "passed",
            "tach_sha256": selection._digest(path / cfg.tach),
            "source_tree": sel.changeset.execution_tree or sel.changeset.tree,
        },
    )
    assert p["inputs"]["config"]["policy_scope"] == "event_external"
    assert p["inputs"]["hygiene"]["policy_scope"] == "event_external"
    assert p["inputs"]["sources"]["policy_scope"] == "event_external"
    assert p["selection"]["code_prefixes"] == ["custom_pkg/"]

    runner = FakeRunner()
    report = execution.execute(p, root=path, runner=runner)
    assert report["status"] == "passed"
    by_name = {item["name"]: item for item in report["checks"]}
    assert by_name["tach_external"]["status"] == "passed"
    assert by_name["source"]["status"] == "passed"
    assert any("check-external" in call[0] for call in runner.calls)

    # Matching explicit overrides to the same external paths are also admitted.
    runner_override = FakeRunner()
    report_override = execution.execute(
        p,
        root=path,
        hygiene_config=ext_hyg,
        sources_config=ext_src,
        runner=runner_override,
    )
    assert report_override["status"] == "passed"

    # Spoofed/stripped external bindings fail closed with outside_root or digest mismatch.
    no_scope = json.loads(json.dumps(p))
    no_scope["inputs"]["hygiene"]["policy_scope"] = "repo_target_tree"
    no_scope_report = execution.execute(no_scope, root=path, runner=FakeRunner())
    assert no_scope_report["status"] == "failed"
    assert no_scope_report["checks"][0]["reason"] == "hygiene_config_outside_root"

    no_ref_src = json.loads(json.dumps(p))
    no_ref_src["inputs"]["sources"].pop("effective_check_config_ref")
    no_ref_src_report = execution.execute(no_ref_src, root=path, runner=FakeRunner())
    assert no_ref_src_report["status"] == "failed"
    src_check = next(
        item for item in no_ref_src_report["checks"] if item["name"] == "source"
    )
    assert src_check["reason"] == "sources_config_outside_root"

    other_hyg = ext_dir / "other-hygiene.toml"
    other_hyg.write_text(ext_hyg.read_text(encoding="utf-8"), encoding="utf-8")
    bad_override_report = execution.execute(
        p, root=path, hygiene_config=other_hyg, runner=FakeRunner()
    )
    assert bad_override_report["status"] == "failed"
    assert bad_override_report["checks"][0]["reason"] == "hygiene_config_outside_root"

    ext_hyg.write_text(
        ext_hyg.read_text(encoding="utf-8") + "\n# modified external bytes\n",
        encoding="utf-8",
    )
    tampered_report = execution.execute(p, root=path, runner=FakeRunner())
    assert tampered_report["status"] == "failed"
    assert tampered_report["checks"][0]["reason"] == "hygiene_config_digest_mismatch"


def test_a1_a2_real_producer_profile_and_source_closure_reconcile_end_to_end(
    repo, tmp_path
):
    from test_reconciliation import (
        ATTEMPT_QUALIFICATION_REF,
        ATTEMPT_REF,
        JOB_ISSUER_REF,
        RUN_REF,
        _attach_effective_profile_policy,
        report_ref_for,
    )

    from engineering_ci import expected_checks, reconciliation

    path, base = repo
    (path / "docs/guide.md").write_text("# updated\n", encoding="utf-8")
    (path / "src/pkg/mod.py").write_text("VALUE = 2\n", encoding="utf-8")

    declared = ["whitespace", "docs", "tach"]
    prof_ref = check_contract.profile_ref("eci/default", "1.0.0")
    cfg_path = path / "ci-selection.toml"
    rules = expected_checks.governed_domain_rules(path, config_path=cfg_path)
    profile = expected_checks.build_effective_profile(
        profile_ref=prof_ref,
        profile_revision="rev-107",
        approval_source="governance-signed",
        effective_at="2026-11-16T00:00:00Z",
        evaluated_at="2026-11-16T00:00:00Z",
        context="worktree",
        action="worktree",
        target="HEAD",
        declared_checks=declared,
        required_obligations=[
            {"obligation_ref": f"req-{name}", "check_names": [name]}
            for name in declared
        ],
        governed_rules=rules,
        domain_complete=True,
        authority_verified=True,
    )
    cfg = _attach_effective_profile_policy(cfg_path, profile)
    hyg_cfg = hygiene.load_config(path / "ci-hygiene.toml")
    cs = selection.changeset(path, base=base, mode="worktree")
    sel = selection.select(cs, cfg, root=path)
    p = selection.plan(
        sel,
        config=cfg,
        config_path=cfg_path,
        hygiene_config=hyg_cfg,
        hygiene_config_path=path / "ci-hygiene.toml",
        root=path,
        effective_profile=profile,
        tach_validation={
            "status": "passed",
            "tach_sha256": selection._digest(path / cfg.tach),
            "source_tree": sel.changeset.execution_tree or sel.changeset.tree,
        },
    )
    exp = p["expected_checks"]
    assert exp["authority_status"] == "verified"

    report = execution.execute(p, root=path, runner=FakeRunner())
    by_name = {c["name"]: c for c in report["checks"]}
    for name in declared:
        assert by_name[name]["status"] == "passed", by_name[name]

    # Unmodified raw NativeResults carry bound profile_ref, relative rule_ref,
    # changed_path input_refs, and verified check_input_closure source_ref:
    resolutions = []
    routings = []
    raw_results = []
    exp_by = {c["name"]: c for c in exp["checks"] if c["name"] in declared}
    for name in declared:
        check_entry = by_name[name]
        for nr in check_entry["native_results"]:
            assert nr["check"]["profile_ref"] == prof_ref
            assert nr["source_ref"]["scope_kind"] == "check_input_closure"
            assert nr["source_ref"]["binding_status"] == "content_digest"
            assert "ci-hygiene.toml" in nr["source_ref"]["verified_input_scope"]
            raw_results.append(nr)
        primary = check_entry["native_result"]
        assert primary["check"]["rule_ref"] == exp_by[name]["rule_ref"]
        resolutions.append(execution.source_resolution_receipt(p, check_entry))
        routings.append(
            {
                "expected_check_ref": exp_by[name]["expected_check_ref"],
                "job_refs": ["job-1"],
                "job_instances": [
                    {
                        "job_ref": "job-1",
                        "run_ref": RUN_REF,
                        "attempt_ref": ATTEMPT_REF,
                        "runner_ref": primary["runner_ref"],
                        "attempt_eligibility": "eligible",
                        "qualification_ref": dict(ATTEMPT_QUALIFICATION_REF),
                        "invocation_ref": "inv-107",
                    }
                ],
                "mapping_status": "routed",
            }
        )

    exec_report = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [
            {
                "name": by_name[name]["name"],
                "check_id": by_name[name]["check_id"],
                "status": by_name[name]["status"],
                "plan_authorized": True,
                "check_contract": by_name[name]["check_contract"],
                "native_result": by_name[name]["native_result"],
                "native_results": by_name[name]["native_results"],
            }
            for name in declared
        ],
    }
    rref = report_ref_for(exec_report)
    primary_runner = by_name["tach"]["native_result"]["runner_ref"]
    obs = {
        "job_ref": "job-1",
        "run_ref": RUN_REF,
        "attempt_ref": ATTEMPT_REF,
        "runner_ref": primary_runner,
        "report": exec_report,
        "report_ref": rref,
        "native_results": raw_results,
        "job_receipt": {
            "kind": reconciliation.JOB_RECEIPT_KIND,
            "schema_version": reconciliation.JOB_RECEIPT_SCHEMA_VERSION,
            "job_ref": "job-1",
            "run_ref": RUN_REF,
            "attempt_ref": ATTEMPT_REF,
            "runner_ref": primary_runner,
            "report_ref": rref,
            "issuer_ref": dict(JOB_ISSUER_REF),
            "verification_status": "upstream_verified",
            "invocation_ref": "inv-107",
            "event_ref": exp["event_ref"],
            "scope_id": exp["scope_id"],
            "expected_set_ref": exp["expected_set_ref"],
        },
    }
    # Expected only includes applicable checks; check non-applicable ones aren't broken:
    all_routings = routings + [
        {"expected_check_ref": c["expected_check_ref"], "mapping_status": "unrouted"}
        for c in exp["checks"]
        if c["name"] not in declared
    ]
    receipt = reconciliation.reconcile_expected_results(
        exp,
        routing=all_routings,
        resolutions=resolutions,
        observations=[obs],
        invocation_ref="inv-107",
    )
    reconciliation.validate_receipt(receipt)
    rec_by = {item["name"]: item for item in receipt["entries"]}
    for name in declared:
        assert rec_by[name]["binding_status"] == "matched", rec_by[name]


def test_a2_index_and_commit_produce_full_input_closure_byte_tree(repo):
    path, _base = repo
    (path / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    run(path, "add", ".gitignore")
    run(path, "commit", "-q", "-m", "ignore pycache")
    base = run(path, "rev-parse", "HEAD").strip()
    (path / "src/pkg/mod.py").write_text("VALUE = 3\n", encoding="utf-8")
    run(path, "add", "src/pkg/mod.py")
    idx_cs = selection.changeset(path, base=base, mode="index")
    cfg = selection.load_config(path / "ci-selection.toml")
    hyg_cfg = hygiene.load_config(path / "ci-hygiene.toml")
    idx_sel = selection.select(idx_cs, cfg, root=path)
    idx_plan = selection.plan(
        idx_sel,
        config=cfg,
        config_path=path / "ci-selection.toml",
        hygiene_config=hyg_cfg,
        hygiene_config_path=path / "ci-hygiene.toml",
        root=path,
        tach_validation={
            "status": "passed",
            "tach_sha256": selection._digest(path / cfg.tach),
            "source_tree": idx_sel.changeset.execution_tree or idx_sel.changeset.tree,
        },
    )
    closed_checks = {"whitespace", "forwarding", "docs", "tach"}
    idx_report = execution.execute(idx_plan, root=path, runner=FakeRunner())
    assert idx_report["checks"]
    for check in idx_report["checks"]:
        assert check["status"] in {"passed", "not_applicable"}, check
        if not check.get("native_result"):
            continue
        sr = check["native_result"]["source_ref"]
        if check["name"] in closed_checks:
            assert sr["scope_kind"] == "full_input_closure"
            assert sr["binding_status"] == "byte_tree"
            assert sr["materialization_ref"].startswith("sha256:")
            assert "src/pkg/mod.py" in sr["verified_input_scope"]
        else:
            assert sr["scope_kind"] == "changed_paths"
            assert sr["binding_status"] != "byte_tree"

    run(path, "commit", "-q", "-m", "commit mod change")
    head = run(path, "rev-parse", "HEAD").strip()
    cmt_cs = selection.changeset(path, base=base, head=head, mode="commit")
    cmt_sel = selection.select(cmt_cs, cfg, root=path)
    cmt_plan = selection.plan(
        cmt_sel,
        config=cfg,
        config_path=path / "ci-selection.toml",
        hygiene_config=hyg_cfg,
        hygiene_config_path=path / "ci-hygiene.toml",
        root=path,
        tach_validation={
            "status": "passed",
            "tach_sha256": selection._digest(path / cfg.tach),
            "source_tree": cmt_sel.changeset.execution_tree or cmt_sel.changeset.tree,
        },
    )
    cmt_report = execution.execute(cmt_plan, root=path, runner=FakeRunner())
    assert cmt_report["checks"]
    for check in cmt_report["checks"]:
        assert check["status"] in {"passed", "not_applicable"}, check
        if not check.get("native_result"):
            continue
        sr = check["native_result"]["source_ref"]
        if check["name"] in closed_checks:
            assert sr["scope_kind"] == "full_input_closure"
            assert sr["binding_status"] == "byte_tree"
            assert sr["declared_target_tree"] == idx_cs.index_tree
            assert sr["materialization_ref"].startswith("sha256:")
        else:
            assert sr["scope_kind"] == "changed_paths"
            assert sr["binding_status"] != "byte_tree"


def test_a1_profile_reference_fail_closed_converged_contract(repo):
    from test_reconciliation import _attach_effective_profile_policy

    from engineering_ci import expected_checks

    path, base = repo
    (path / "src/pkg/mod.py").write_text("VALUE = 11\n", encoding="utf-8")
    declared = ["whitespace", "forwarding", "tach"]
    prof_ref = check_contract.profile_ref("eci/default", "1.0.0")
    cfg_path = path / "ci-selection.toml"
    rules = expected_checks.governed_domain_rules(path, config_path=cfg_path)
    profile = expected_checks.build_effective_profile(
        profile_ref=prof_ref,
        profile_revision="rev-a1",
        approval_source="governance-signed",
        effective_at="2026-11-16T00:00:00Z",
        evaluated_at="2026-11-16T00:00:00Z",
        context="worktree",
        action="worktree",
        target="HEAD",
        declared_checks=declared,
        required_obligations=[
            {"obligation_ref": f"req-{name}", "check_names": [name]}
            for name in declared
        ],
        governed_rules=rules,
        domain_complete=True,
        authority_verified=True,
    )
    cfg = _attach_effective_profile_policy(cfg_path, profile)
    hyg_cfg = hygiene.load_config(path / "ci-hygiene.toml")
    sel = selection.select(
        selection.changeset(path, base=base, mode="worktree"), cfg, root=path
    )
    p = selection.plan(
        sel,
        config=cfg,
        config_path=cfg_path,
        hygiene_config=hyg_cfg,
        hygiene_config_path=path / "ci-hygiene.toml",
        root=path,
        effective_profile=profile,
        tach_validation={
            "status": "passed",
            "tach_sha256": selection._digest(path / cfg.tach),
            "source_tree": sel.changeset.execution_tree or sel.changeset.tree,
        },
    )

    assert execution._profile_reference(p, root=path) == prof_ref

    # Policy in ci-selection.toml alone without Plan expected/effective_profile has NO authority:
    bare_plan = json.loads(json.dumps(p))
    bare_plan.pop("expected_checks", None)
    bare_plan["inputs"].pop("effective_profile", None)
    bare_plan["generator"].pop("effective_profile_sha256", None)
    assert execution._profile_reference(bare_plan, root=path) is None

    # Dagger export context requires matching source_tree_id + disk config proof:
    dagger_plan = json.loads(json.dumps(p))
    assert (
        execution._profile_reference(
            dagger_plan, root=path, execution_context="dagger_export"
        )
        is None
    )
    dagger_plan["git"]["source_tree_id"] = selection.directory_content_id(path)
    assert (
        execution._profile_reference(
            dagger_plan, root=path, execution_context="dagger_export"
        )
        == prof_ref
    )
    dagger_plan["git"]["source_tree_id"] = "0" * 64
    assert (
        execution._profile_reference(
            dagger_plan, root=path, execution_context="dagger_export"
        )
        is None
    )

    # Tampering or deleting ci-selection.toml on Host fails closed:
    original_toml = cfg_path.read_text(encoding="utf-8")
    cfg_path.write_text(original_toml + "\n# tampered\n", encoding="utf-8")
    assert execution._profile_reference(p, root=path) is None
    cfg_path.unlink()
    assert execution._profile_reference(p, root=path) is None


def test_a2_real_non_mocked_host_execution_and_reconciliation(repo):
    from test_reconciliation import (
        ATTEMPT_QUALIFICATION_REF,
        ATTEMPT_REF,
        JOB_ISSUER_REF,
        RUN_REF,
        _attach_effective_profile_policy,
        report_ref_for,
    )

    from engineering_ci import expected_checks, reconciliation

    path, base = repo
    (path / "src/pkg/mod.py").write_text(
        'VALUE = 22\n__all__ = ["VALUE"]\n', encoding="utf-8"
    )

    # Always-available non-mocked Host execution (git diff-check + AST forwarding):
    declared = ["whitespace", "forwarding"]
    prof_ref = check_contract.profile_ref("eci/default", "1.0.0")
    cfg_path = path / "ci-selection.toml"
    rules = expected_checks.governed_domain_rules(path, config_path=cfg_path)
    profile = expected_checks.build_effective_profile(
        profile_ref=prof_ref,
        profile_revision="rev-real-host-core",
        approval_source="governance-signed",
        effective_at="2026-11-16T00:00:00Z",
        evaluated_at="2026-11-16T00:00:00Z",
        context="worktree",
        action="worktree",
        target="HEAD",
        declared_checks=declared,
        required_obligations=[
            {"obligation_ref": f"req-{name}", "check_names": [name]}
            for name in declared
        ],
        governed_rules=rules,
        domain_complete=True,
        authority_verified=True,
    )
    cfg = _attach_effective_profile_policy(cfg_path, profile)
    hyg_cfg = hygiene.load_config(path / "ci-hygiene.toml")
    sel = selection.select(
        selection.changeset(path, base=base, mode="worktree"), cfg, root=path
    )
    p = selection.plan(
        sel,
        config=cfg,
        config_path=cfg_path,
        hygiene_config=hyg_cfg,
        hygiene_config_path=path / "ci-hygiene.toml",
        root=path,
        effective_profile=profile,
        tach_validation={
            "status": "passed",
            "tach_sha256": selection._digest(path / cfg.tach),
            "source_tree": sel.changeset.execution_tree or sel.changeset.tree,
        },
    )
    exp = p["expected_checks"]

    pre_resolutions = [
        execution.build_source_resolution(p, name, root=path) for name in declared
    ]
    res_by_name = {
        c["name"]: r
        for c, r in zip(
            [next(e for e in exp["checks"] if e["name"] == n) for n in declared],
            pre_resolutions,
            strict=True,
        )
    }

    report = execution.execute(p, root=path, runner=None)
    by_name = {c["name"]: c for c in report["checks"]}
    for name in declared:
        assert by_name[name]["status"] == "passed", by_name[name]
        assert by_name[name]["native_result"]["status"] == "passed"
        assert (
            by_name[name]["native_result"]["source_ref"]["scope_kind"]
            == "check_input_closure"
        )
        assert res_by_name[name]["source_ref"]["scope_kind"] == "check_input_closure"

    exp_by = {c["name"]: c for c in exp["checks"] if c["name"] in declared}
    raw_results = []
    routings = []
    for name in declared:
        check_entry = by_name[name]
        raw_results.extend(check_entry["native_results"])
        primary = check_entry["native_result"]
        routings.append(
            {
                "expected_check_ref": exp_by[name]["expected_check_ref"],
                "job_refs": ["job-real-host"],
                "job_instances": [
                    {
                        "job_ref": "job-real-host",
                        "run_ref": RUN_REF,
                        "attempt_ref": ATTEMPT_REF,
                        "runner_ref": primary["runner_ref"],
                        "attempt_eligibility": "eligible",
                        "qualification_ref": dict(ATTEMPT_QUALIFICATION_REF),
                        "invocation_ref": "inv-real-host",
                    }
                ],
                "mapping_status": "routed",
            }
        )

    exec_report = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [
            {
                "name": by_name[name]["name"],
                "check_id": by_name[name]["check_id"],
                "status": by_name[name]["status"],
                "plan_authorized": True,
                "check_contract": by_name[name]["check_contract"],
                "native_result": by_name[name]["native_result"],
                "native_results": by_name[name]["native_results"],
            }
            for name in declared
        ],
    }
    rref = report_ref_for(exec_report)
    primary_runner = by_name["whitespace"]["native_result"]["runner_ref"]
    obs = {
        "job_ref": "job-real-host",
        "run_ref": RUN_REF,
        "attempt_ref": ATTEMPT_REF,
        "runner_ref": primary_runner,
        "report": exec_report,
        "report_ref": rref,
        "native_results": raw_results,
        "job_receipt": {
            "kind": reconciliation.JOB_RECEIPT_KIND,
            "schema_version": reconciliation.JOB_RECEIPT_SCHEMA_VERSION,
            "job_ref": "job-real-host",
            "run_ref": RUN_REF,
            "attempt_ref": ATTEMPT_REF,
            "runner_ref": primary_runner,
            "report_ref": rref,
            "issuer_ref": dict(JOB_ISSUER_REF),
            "verification_status": "upstream_verified",
            "invocation_ref": "inv-real-host",
            "event_ref": exp["event_ref"],
            "scope_id": exp["scope_id"],
            "expected_set_ref": exp["expected_set_ref"],
        },
    }
    all_routings = routings + [
        {"expected_check_ref": c["expected_check_ref"], "mapping_status": "unrouted"}
        for c in exp["checks"]
        if c["name"] not in declared
    ]
    receipt = reconciliation.reconcile_expected_results(
        exp,
        routing=all_routings,
        resolutions=pre_resolutions,
        observations=[obs],
        invocation_ref="inv-real-host",
    )
    reconciliation.validate_receipt(receipt)
    rec_by = {item["name"]: item for item in receipt["entries"]}
    for name in declared:
        assert rec_by[name]["binding_status"] == "matched", rec_by[name]


@pytest.mark.skipif(
    shutil.which("tach") is None or shutil.which("vulture") is None,
    reason="tach/vulture binaries unavailable on PATH",
)
def test_a2_real_non_mocked_tach_and_vulture_host_e2e(repo):
    from test_reconciliation import (
        ATTEMPT_QUALIFICATION_REF,
        ATTEMPT_REF,
        JOB_ISSUER_REF,
        RUN_REF,
        _attach_effective_profile_policy,
        report_ref_for,
    )

    from engineering_ci import expected_checks, reconciliation

    path, base = repo
    # Satisfy real tach.toml dependencies cleanly on base commit and avoid dead symbols:
    (path / "tach.toml").write_text(
        'source_roots = ["src"]\n'
        "exact = true\n"
        "forbid_circular_dependencies = true\n"
        'root_module = "forbid"\n\n'
        '[[modules]]\npath = "pkg"\ndepends_on = []\n\n'
        '[[modules]]\npath = "pkg.sub"\ndepends_on = []\n\n'
        '[[modules]]\npath = "pkg.other"\ndepends_on = []\n',
        encoding="utf-8",
    )
    (path / "src/pkg/sub.py").write_text(
        'VALUE = 1\n__all__ = ["VALUE"]\n', encoding="utf-8"
    )
    (path / "src/pkg/other.py").write_text(
        'VALUE = 2\n__all__ = ["VALUE"]\n', encoding="utf-8"
    )
    (path / "src/pkg/mod.py").write_text(
        'VALUE = 1\n__all__ = ["VALUE"]\n', encoding="utf-8"
    )
    run(
        path, "add", "tach.toml", "src/pkg/sub.py", "src/pkg/other.py", "src/pkg/mod.py"
    )
    run(path, "commit", "-q", "-m", "clean tach graph for real host execution")
    base = run(path, "rev-parse", "HEAD").strip()
    (path / "src/pkg/mod.py").write_text(
        'VALUE = 22\n__all__ = ["VALUE"]\n', encoding="utf-8"
    )

    # Include closed static checks (whitespace, forwarding, tach) + open-world vulture:
    declared = ["whitespace", "forwarding", "tach", "vulture"]
    prof_ref = check_contract.profile_ref("eci/default", "1.0.0")
    cfg_path = path / "ci-selection.toml"
    rules = expected_checks.governed_domain_rules(path, config_path=cfg_path)
    profile = expected_checks.build_effective_profile(
        profile_ref=prof_ref,
        profile_revision="rev-real-host",
        approval_source="governance-signed",
        effective_at="2026-11-16T00:00:00Z",
        evaluated_at="2026-11-16T00:00:00Z",
        context="worktree",
        action="worktree",
        target="HEAD",
        declared_checks=declared,
        required_obligations=[
            {"obligation_ref": f"req-{name}", "check_names": [name]}
            for name in declared
        ],
        governed_rules=rules,
        domain_complete=True,
        authority_verified=True,
    )
    cfg = _attach_effective_profile_policy(cfg_path, profile)
    hyg_cfg = hygiene.load_config(path / "ci-hygiene.toml")
    sel = selection.select(
        selection.changeset(path, base=base, mode="worktree"), cfg, root=path
    )
    p = selection.plan(
        sel,
        config=cfg,
        config_path=cfg_path,
        hygiene_config=hyg_cfg,
        hygiene_config_path=path / "ci-hygiene.toml",
        root=path,
        effective_profile=profile,
        tach_validation={
            "status": "passed",
            "tach_sha256": selection._digest(path / cfg.tach),
            "source_tree": sel.changeset.execution_tree or sel.changeset.tree,
        },
    )
    exp = p["expected_checks"]

    # Build independent Source Resolutions BEFORE running execute() — never reads native_result:
    pre_resolutions = [
        execution.build_source_resolution(p, name, root=path) for name in declared
    ]
    res_by_name = {
        c["name"]: r
        for c, r in zip(
            [next(e for e in exp["checks"] if e["name"] == n) for n in declared],
            pre_resolutions,
            strict=True,
        )
    }

    # Real non-mocked Host execution (runner=None invokes real git, AST forwarding, tach, vulture):
    report = execution.execute(p, root=path, runner=None)
    by_name = {c["name"]: c for c in report["checks"]}
    for name in declared:
        assert by_name[name]["status"] == "passed", by_name[name]
        assert by_name[name]["native_result"]["status"] == "passed"

    for closed in ("whitespace", "forwarding", "tach"):
        assert (
            by_name[closed]["native_result"]["source_ref"]["scope_kind"]
            == "check_input_closure"
        )
        assert res_by_name[closed]["source_ref"]["scope_kind"] == "check_input_closure"

    # Open-world vulture must NOT claim check_input_closure or full_input_closure:
    assert (
        by_name["vulture"]["native_result"]["source_ref"]["scope_kind"]
        == "changed_paths"
    )
    assert res_by_name["vulture"]["source_ref"]["scope_kind"] == "changed_paths"

    exp_by = {c["name"]: c for c in exp["checks"] if c["name"] in declared}
    raw_results = []
    routings = []
    for name in declared:
        check_entry = by_name[name]
        raw_results.extend(check_entry["native_results"])
        primary = check_entry["native_result"]
        routings.append(
            {
                "expected_check_ref": exp_by[name]["expected_check_ref"],
                "job_refs": ["job-real-host"],
                "job_instances": [
                    {
                        "job_ref": "job-real-host",
                        "run_ref": RUN_REF,
                        "attempt_ref": ATTEMPT_REF,
                        "runner_ref": primary["runner_ref"],
                        "attempt_eligibility": "eligible",
                        "qualification_ref": dict(ATTEMPT_QUALIFICATION_REF),
                        "invocation_ref": "inv-real-host",
                    }
                ],
                "mapping_status": "routed",
            }
        )

    exec_report = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [
            {
                "name": by_name[name]["name"],
                "check_id": by_name[name]["check_id"],
                "status": by_name[name]["status"],
                "plan_authorized": True,
                "check_contract": by_name[name]["check_contract"],
                "native_result": by_name[name]["native_result"],
                "native_results": by_name[name]["native_results"],
            }
            for name in declared
        ],
    }
    rref = report_ref_for(exec_report)
    primary_runner = by_name["tach"]["native_result"]["runner_ref"]
    obs = {
        "job_ref": "job-real-host",
        "run_ref": RUN_REF,
        "attempt_ref": ATTEMPT_REF,
        "runner_ref": primary_runner,
        "report": exec_report,
        "report_ref": rref,
        "native_results": raw_results,
        "job_receipt": {
            "kind": reconciliation.JOB_RECEIPT_KIND,
            "schema_version": reconciliation.JOB_RECEIPT_SCHEMA_VERSION,
            "job_ref": "job-real-host",
            "run_ref": RUN_REF,
            "attempt_ref": ATTEMPT_REF,
            "runner_ref": primary_runner,
            "report_ref": rref,
            "issuer_ref": dict(JOB_ISSUER_REF),
            "verification_status": "upstream_verified",
            "invocation_ref": "inv-real-host",
            "event_ref": exp["event_ref"],
            "scope_id": exp["scope_id"],
            "expected_set_ref": exp["expected_set_ref"],
        },
    }
    all_routings = routings + [
        {"expected_check_ref": c["expected_check_ref"], "mapping_status": "unrouted"}
        for c in exp["checks"]
        if c["name"] not in declared
    ]
    receipt = reconciliation.reconcile_expected_results(
        exp,
        routing=all_routings,
        resolutions=pre_resolutions,
        observations=[obs],
        invocation_ref="inv-real-host",
    )
    reconciliation.validate_receipt(receipt)
    rec_by = {item["name"]: item for item in receipt["entries"]}
    for closed in ("whitespace", "forwarding", "tach"):
        assert rec_by[closed]["binding_status"] == "matched", rec_by[closed]
    assert rec_by["vulture"]["binding_status"] == "unknown", rec_by["vulture"]
    assert rec_by["vulture"]["reason"] == "expected_source_scope_insufficient"

    # Real vulture advisory exit 3 on unused symbol: native verdict advisory != passed:
    (path / "src/pkg/mod.py").write_text(
        "VALUE = 22\n\n\ndef _unused_dead_fn():\n    return 99\n",
        encoding="utf-8",
    )
    adv_sel = selection.select(
        selection.changeset(path, base=base, mode="worktree"), cfg, root=path
    )
    adv_plan = selection.plan(
        adv_sel,
        config=cfg,
        config_path=cfg_path,
        hygiene_config=hyg_cfg,
        hygiene_config_path=path / "ci-hygiene.toml",
        root=path,
        effective_profile=profile,
        tach_validation={
            "status": "passed",
            "tach_sha256": selection._digest(path / cfg.tach),
            "source_tree": adv_sel.changeset.execution_tree or adv_sel.changeset.tree,
        },
    )
    adv_report = execution.execute(adv_plan, root=path, runner=None)
    adv_vulture = next(c for c in adv_report["checks"] if c["name"] == "vulture")
    assert adv_vulture["status"] == "advisory"
    assert adv_vulture["native_result"]["status"] == "advisory"
    assert adv_vulture["native_result"]["native_exit_code"] == 3
    assert adv_vulture["native_result"]["source_ref"]["scope_kind"] == "changed_paths"


def test_a2_negative_index_b_worktree_c_divergence_and_resolution_refusals(
    repo, tmp_path
):
    from test_reconciliation import _attach_effective_profile_policy

    from engineering_ci import expected_checks

    path, base = repo
    cfg_path = path / "ci-selection.toml"
    declared = ["whitespace", "forwarding", "tach"]
    prof_ref = check_contract.profile_ref("eci/default", "1.0.0")
    rules = expected_checks.governed_domain_rules(path, config_path=cfg_path)
    profile = expected_checks.build_effective_profile(
        profile_ref=prof_ref,
        profile_revision="rev-neg-idx",
        approval_source="governance-signed",
        effective_at="2026-11-16T00:00:00Z",
        evaluated_at="2026-11-16T00:00:00Z",
        context="index",
        action="index",
        target="HEAD",
        declared_checks=declared,
        required_obligations=[
            {"obligation_ref": f"req-{name}", "check_names": [name]}
            for name in declared
        ],
        governed_rules=rules,
        domain_complete=True,
        authority_verified=True,
    )
    cfg = _attach_effective_profile_policy(cfg_path, profile)
    run(path, "add", "ci-selection.toml")
    run(path, "commit", "-q", "-m", "commit profile policy before index B")
    base = run(path, "rev-parse", "HEAD").strip()
    hyg_cfg = hygiene.load_config(path / "ci-hygiene.toml")

    # Stage change ONLY into temporary Index B while Index A stays clean and Worktree C drifts:
    (path / "src/pkg/mod.py").write_text("VALUE = 101\n", encoding="utf-8")
    index_b = tmp_path / "index.b"
    env_b = {**os.environ, "GIT_INDEX_FILE": str(index_b)}
    subprocess.run(["git", "read-tree", "HEAD"], cwd=path, env=env_b, check=True)
    subprocess.run(["git", "add", "src/pkg/mod.py"], cwd=path, env=env_b, check=True)
    idx_cs = selection.changeset(path, base=base, mode="index", index_file=str(index_b))
    idx_sel = selection.select(idx_cs, cfg, root=path)
    idx_plan = selection.plan(
        idx_sel,
        config=cfg,
        config_path=cfg_path,
        hygiene_config=hyg_cfg,
        hygiene_config_path=path / "ci-hygiene.toml",
        root=path,
        effective_profile=profile,
        tach_validation={
            "status": "passed",
            "tach_sha256": selection._digest(path / cfg.tach),
            "source_tree": idx_sel.changeset.execution_tree or idx_sel.changeset.tree,
        },
    )

    # Worktree C diverges from Temp Index B -> Host closure proof MUST refuse byte_tree:
    (path / "src/pkg/mod.py").write_text(
        "VALUE = 999  # drifted worktree C\n", encoding="utf-8"
    )
    assert selection.index_source_refusal(
        path, idx_cs.index_tree, index_file=str(index_b)
    )
    res_drifted = execution.build_source_resolution(idx_plan, "whitespace", root=path)
    assert res_drifted["source_ref"]["binding_status"] != "byte_tree"

    with pytest.raises(
        execution.ExecutionError, match="source_resolution_changed_paths_stale"
    ):
        # Stale changed_paths in worktree mode raises explicitly:
        wt_profile = expected_checks.build_effective_profile(
            profile_ref=prof_ref,
            profile_revision="rev-neg-wt",
            approval_source="governance-signed",
            effective_at="2026-11-16T00:00:00Z",
            evaluated_at="2026-11-16T00:00:00Z",
            context="worktree",
            action="worktree",
            target="HEAD",
            declared_checks=declared,
            required_obligations=[
                {"obligation_ref": f"req-{name}", "check_names": [name]}
                for name in declared
            ],
            governed_rules=rules,
            domain_complete=True,
            authority_verified=True,
        )
        wt_cfg = _attach_effective_profile_policy(cfg_path, wt_profile)
        wt_cs = selection.changeset(path, base=base, mode="worktree")
        wt_sel = selection.select(wt_cs, wt_cfg, root=path)
        wt_plan = selection.plan(
            wt_sel,
            config=wt_cfg,
            config_path=cfg_path,
            root=path,
            effective_profile=wt_profile,
        )
        (path / "src/pkg/mod.py").write_text("VALUE = 1000\n", encoding="utf-8")
        execution.build_source_resolution(wt_plan, "whitespace", root=path)

    # Deleted ci-selection.toml refuses independent rule/profile verification:
    cfg_path.unlink()
    with pytest.raises(
        execution.ExecutionError, match="source_resolution_profile_unverified"
    ):
        execution.build_source_resolution(idx_plan, "tach", root=path)


def _hygiene_worktree_plan(path, base):
    cfg = selection.load_config(path / "ci-selection.toml")
    hyg_cfg = hygiene.load_config(path / "ci-hygiene.toml")
    sel = selection.select(
        selection.changeset(path, base=base, mode="worktree"), cfg, root=path
    )
    return selection.plan(
        sel,
        config=cfg,
        config_path=path / "ci-selection.toml",
        hygiene_config=hyg_cfg,
        hygiene_config_path=path / "ci-hygiene.toml",
        root=path,
        tach_validation={
            "status": "passed",
            "tach_sha256": selection._digest(path / cfg.tach),
            "source_tree": sel.changeset.execution_tree or sel.changeset.tree,
        },
    )


def _whitespace_result(report):
    return next(c for c in report["checks"] if c["name"] == "whitespace")


def test_whitespace_native_scope_equals_plan_paths_and_source_closure(repo):
    path, base = repo
    (path / "src/pkg/mod.py").write_text("VALUE = 5\n", encoding="utf-8")
    p = _hygiene_worktree_plan(path, base)
    assert p["git"]["changed_paths"] == ["src/pkg/mod.py"]

    # A tracked file outside the Plan gains a whitespace error after Plan creation:
    (path / "src/pkg/sub.py").write_text("VALUE = 1   \n", encoding="utf-8")
    seen = []
    real = execution.hygiene._run

    def spy(argv, root, data=None):
        seen.append(list(argv))
        return real(argv, root, data)

    report = execution.execute(p, root=path, runner=spy)
    ws = _whitespace_result(report)
    diff_calls = [a for a in seen if a[:3] == ["git", "diff", "--check"]]
    assert diff_calls and all(
        a[-2:] == ["--", ":(literal)src/pkg/mod.py"] for a in diff_calls
    )
    # The out-of-Plan file was not read, so it cannot affect the verdict:
    assert ws["status"] == "passed"
    sr = ws["native_result"]["source_ref"]
    assert sr["scope_kind"] == "check_input_closure"
    assert "src/pkg/sub.py" not in sr["verified_input_scope"]


def test_whitespace_error_in_plan_path_fails_and_plan_path_drift_is_refused(repo):
    path, base = repo
    (path / "src/pkg/mod.py").write_text("VALUE = 6\n", encoding="utf-8")
    p = _hygiene_worktree_plan(path, base)

    # Unstaged then staged whitespace error in a Plan path stays failed:
    (path / "src/pkg/mod.py").write_text("VALUE = 6\n", encoding="utf-8")
    # Plan path content drift is a stale snapshot, not a silent pass:
    (path / "src/pkg/mod.py").write_text("VALUE = 7   \n", encoding="utf-8")
    drifted = execution.execute(p, root=path)
    assert drifted["status"] == "not_executed"
    assert drifted["reason"] == "worktree_snapshot_stale"

    # A Plan created with the error present is checked natively and fails:
    p2 = _hygiene_worktree_plan(path, base)
    run(path, "add", "src/pkg/mod.py")
    report = execution.execute(p2, root=path)
    ws = _whitespace_result(report)
    assert ws["status"] == "failed"
    assert ws["reason"] == "whitespace_errors"


def test_whitespace_deleted_and_added_plan_paths_are_scoped(repo):
    path, base = repo
    (path / "src/pkg/other.py").unlink()
    (path / "src/pkg/new.py").write_text("VALUE = 9\n", encoding="utf-8")
    run(path, "add", "-N", "src/pkg/new.py")
    p = _hygiene_worktree_plan(path, base)
    assert set(p["git"]["changed_paths"]) == {"src/pkg/other.py", "src/pkg/new.py"}
    report = execution.execute(p, root=path)
    ws = _whitespace_result(report)
    assert ws["status"] == "passed", ws
    sr = ws["native_result"]["source_ref"]
    assert "src/pkg/other.py" not in sr["verified_input_scope"]
    assert "src/pkg/new.py" in sr["verified_input_scope"]


def test_whitespace_literal_pathspec_prevents_glob_expansion_on_special_chars(repo):
    path, _base = repo
    special_files = [("src/pkg/a[1].py", "src/pkg/a1.py")]
    if os.name != "nt":
        special_files.append(("src/pkg/star*.py", "src/pkg/star1.py"))

    for target_rel, decoy_rel in special_files:
        (path / target_rel).write_text("BASE = 1\n", encoding="utf-8")
        (path / decoy_rel).write_text("DECOY = 1\n", encoding="utf-8")
    (path / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "add special character paths")
    base = run(path, "rev-parse", "HEAD").strip()

    for target_rel, decoy_rel in special_files:
        # Modify BOTH the literal target (clean whitespace) and decoy (trailing spaces),
        # while Plan is constructed ONLY from a changeset containing target_rel:
        (path / target_rel).write_text("BASE = 2\n", encoding="utf-8")
        (path / decoy_rel).write_text("DECOY = 2   \n", encoding="utf-8")

        # Confirm standard Git pathspec WITHOUT --literal-pathspecs DOES expand to decoy_rel:
        glob_names = {
            line
            for line in run(
                path, "diff", "--name-only", base, "--", target_rel
            ).splitlines()
            if line
        }
        assert decoy_rel in glob_names

        # Construct worktree changeset scoped strictly to target_rel:
        (path / decoy_rel).write_text("DECOY = 1\n", encoding="utf-8")
        p = _hygiene_worktree_plan(path, base)
        assert p["git"]["changed_paths"] == [target_rel]

        # Now inject whitespace error into decoy_rel (outside Plan) AFTER Plan creation:
        (path / decoy_rel).write_text("DECOY = 2   \n", encoding="utf-8")
        seen = []
        real = execution.hygiene._run

        def spy(argv, rroot, data=None, *, _seen=seen, _real=real):
            _seen.append(list(argv))
            return _real(argv, rroot, data)

        report = execution.execute(p, root=path, runner=spy)
        ws = _whitespace_result(report)
        assert ws["status"] == "passed", ws
        diff_calls = [a for a in seen if a[:3] == ["git", "diff", "--check"]]
        assert diff_calls
        diff_paths = diff_calls[0][diff_calls[0].index("--") + 1 :]
        assert diff_paths == [f":(literal){target_rel}"]
        native_read = {
            line
            for line in run(
                path,
                "diff",
                "--name-only",
                base,
                "--",
                *diff_paths,
            ).splitlines()
            if line
        }
        assert native_read == {target_rel}
        assert decoy_rel not in native_read
        sr = ws["native_result"]["source_ref"]
        assert sr["scope_kind"] == "check_input_closure"
        assert target_rel in sr["verified_input_scope"]
        assert decoy_rel not in sr["verified_input_scope"]

        # Unstaged whitespace error on literal target_rel itself must fail cleanly:
        (path / target_rel).write_text("BASE = 3   \n", encoding="utf-8")
        p_err = _hygiene_worktree_plan(path, base)
        if decoy_rel in p_err["git"]["changed_paths"]:
            # Reset decoy cleanly so p_err targets only target_rel:
            (path / decoy_rel).write_text("DECOY = 1\n", encoding="utf-8")
            p_err = _hygiene_worktree_plan(path, base)
        assert p_err["git"]["changed_paths"] == [target_rel]
        fail_unstaged = _whitespace_result(execution.execute(p_err, root=path))
        assert fail_unstaged["status"] == "failed"
        assert fail_unstaged["reason"] == "whitespace_errors"

        # Staged whitespace error on literal target_rel also fails cleanly:
        run(path, "add", target_rel)
        fail_staged = _whitespace_result(execution.execute(p_err, root=path))
        assert fail_staged["status"] == "failed"
        assert fail_staged["reason"] == "whitespace_errors"
        # Reset repo before next iteration:
        run(path, "reset", "--hard", "-q", base)


def test_raw_execution_reports_carry_plan_authorization_for_reconciliation(repo):
    from test_reconciliation import report_ref_for

    from engineering_ci import reconciliation

    path, base = repo
    # Raw Source Check report, never rebuilt by hand:
    content = b"source-bytes"
    (path / "data/acquired").mkdir(parents=True)
    (path / "data/acquired/a.bin").write_bytes(content)
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
        'schema_version = 1\nmanifest = "data/catalog/manifest.json"\n'
        'acquired_roots = ["data/acquired"]\nexempt_paths = []\n'
        "environment_prefixes = []\nrequire_text_unset = false\n",
        encoding="utf-8",
    )
    source_plan = plan(
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
    raw = execution.execute(
        source_plan,
        root=path,
        sources_config=path / "ci-sources.toml",
        runner=FakeRunner(),
    )
    source_check = raw["checks"][0]
    assert source_check["name"] == "source"
    assert source_check["plan_authorized"] is True
    assert source_check["check_contract"]["status"] == "valid"
    results, rejections = reconciliation._execution_report(raw, report_ref_for(raw))
    assert results
    assert all(r["result_id"] not in rejections for r in results)

    # Full end-to-end reconciliation consuming an UNMODIFIED raw Report directly:
    from test_reconciliation import (
        ATTEMPT_QUALIFICATION_REF,
        ATTEMPT_REF,
        JOB_ISSUER_REF,
        RUN_REF,
        _attach_effective_profile_policy,
    )

    from engineering_ci import expected_checks

    (path / "src/pkg/mod.py").write_text("VALUE = 8\n", encoding="utf-8")
    declared = ["whitespace", "forwarding"]
    prof_ref = check_contract.profile_ref("eci/default", "1.0.0")
    cfg_path = path / "ci-selection.toml"
    rules = expected_checks.governed_domain_rules(path, config_path=cfg_path)
    profile = expected_checks.build_effective_profile(
        profile_ref=prof_ref,
        profile_revision="rev-raw-e2e",
        approval_source="governance-signed",
        effective_at="2026-11-16T00:00:00Z",
        evaluated_at="2026-11-16T00:00:00Z",
        context="worktree",
        action="worktree",
        target="HEAD",
        declared_checks=declared,
        required_obligations=[
            {"obligation_ref": f"req-{name}", "check_names": [name]}
            for name in declared
        ],
        governed_rules=rules,
        domain_complete=True,
        authority_verified=True,
    )
    cfg = _attach_effective_profile_policy(cfg_path, profile)
    hyg_cfg = hygiene.load_config(path / "ci-hygiene.toml")
    sel = selection.select(
        selection.changeset(path, base=base, mode="worktree"), cfg, root=path
    )
    hyg_plan = selection.plan(
        sel,
        config=cfg,
        config_path=cfg_path,
        hygiene_config=hyg_cfg,
        hygiene_config_path=path / "ci-hygiene.toml",
        root=path,
        effective_profile=profile,
        tach_validation={
            "status": "passed",
            "tach_sha256": selection._digest(path / cfg.tach),
            "source_tree": sel.changeset.execution_tree or sel.changeset.tree,
        },
    )
    exp = hyg_plan["expected_checks"]
    hyg_raw = execution.execute(hyg_plan, root=path, runner=None)
    raw_by = {c["name"]: c for c in hyg_raw["checks"]}
    assert all(raw_by[n]["plan_authorized"] is True for n in declared)
    raw_results = [
        r for c in hyg_raw["checks"] for r in (c.get("native_results") or ())
    ]
    exp_by = {c["name"]: c for c in exp["checks"]}
    primary_runner = raw_by["whitespace"]["native_result"]["runner_ref"]
    routings = [
        {
            "expected_check_ref": exp_by[n]["expected_check_ref"],
            "job_refs": ["job-raw"],
            "job_instances": [
                {
                    "job_ref": "job-raw",
                    "run_ref": RUN_REF,
                    "attempt_ref": ATTEMPT_REF,
                    "runner_ref": primary_runner,
                    "attempt_eligibility": "eligible",
                    "qualification_ref": dict(ATTEMPT_QUALIFICATION_REF),
                    "invocation_ref": "inv-raw",
                }
            ],
            "mapping_status": "routed",
        }
        for n in declared
    ] + [
        {"expected_check_ref": c["expected_check_ref"], "mapping_status": "unrouted"}
        for c in exp["checks"]
        if c["name"] not in declared
    ]
    resolutions = [
        execution.build_source_resolution(hyg_plan, n, root=path) for n in declared
    ]
    raw_ref = report_ref_for(hyg_raw)
    obs = {
        "job_ref": "job-raw",
        "run_ref": RUN_REF,
        "attempt_ref": ATTEMPT_REF,
        "runner_ref": primary_runner,
        "report": hyg_raw,
        "report_ref": raw_ref,
        "native_results": raw_results,
        "job_receipt": {
            "kind": reconciliation.JOB_RECEIPT_KIND,
            "schema_version": reconciliation.JOB_RECEIPT_SCHEMA_VERSION,
            "job_ref": "job-raw",
            "run_ref": RUN_REF,
            "attempt_ref": ATTEMPT_REF,
            "runner_ref": primary_runner,
            "report_ref": raw_ref,
            "issuer_ref": dict(JOB_ISSUER_REF),
            "verification_status": "upstream_verified",
            "invocation_ref": "inv-raw",
            "event_ref": exp["event_ref"],
            "scope_id": exp["scope_id"],
            "expected_set_ref": exp["expected_set_ref"],
        },
    }
    receipt = reconciliation.reconcile_expected_results(
        exp,
        routing=routings,
        resolutions=resolutions,
        observations=[obs],
        invocation_ref="inv-raw",
    )
    reconciliation.validate_receipt(receipt)
    rec_by = {item["name"]: item for item in receipt["entries"]}
    assert rec_by["whitespace"]["binding_status"] == "matched"
    assert rec_by["forwarding"]["binding_status"] == "matched"

    # Mixed raw report: one Check unauthorized -> rejected alone; independent sibling stays matched:
    mixed_raw = json.loads(json.dumps(hyg_raw))
    for item in mixed_raw["checks"]:
        if item["name"] == "forwarding":
            item["plan_authorized"] = False
    mixed_ref = report_ref_for(mixed_raw)
    mixed_obs = {
        **obs,
        "report": mixed_raw,
        "report_ref": mixed_ref,
        "job_receipt": {**obs["job_receipt"], "report_ref": mixed_ref},
    }
    mixed_rec = reconciliation.reconcile_expected_results(
        exp,
        routing=routings,
        resolutions=resolutions,
        observations=[mixed_obs],
        invocation_ref="inv-raw",
    )
    reconciliation.validate_receipt(mixed_rec)
    mixed_by = {item["name"]: item for item in mixed_rec["entries"]}
    assert mixed_by["whitespace"]["binding_status"] == "matched"
    assert mixed_by["forwarding"]["binding_status"] == "insufficient"
    assert mixed_by["forwarding"]["reason"] == "report_check_unauthorized"

    # Also test unauthorized native source report directly:
    unauth_raw = json.loads(json.dumps(raw))
    unauth_raw["checks"][0]["plan_authorized"] = False
    _ures, urej = reconciliation._execution_report(
        unauth_raw, report_ref_for(unauth_raw)
    )
    assert unauth_raw["checks"][0]["native_results"][0]["result_id"] in urej


def test_directory_artifact_closure_emits_member_digests(tmp_path):
    artifact_root = tmp_path / "artifacts"
    (artifact_root / "docs" / "nested").mkdir(parents=True)
    (artifact_root / "docs" / "empty_dir").mkdir(parents=True)
    (artifact_root / "out").mkdir(parents=True)
    gen_bytes = b"# Generated guide\n"
    sub_bytes = b"nested-payload\n"
    single_bytes = b"single-file\n"
    (artifact_root / "docs" / "generated.md").write_bytes(gen_bytes)
    (artifact_root / "docs" / "nested" / "sub.txt").write_bytes(sub_bytes)
    (artifact_root / "out" / "single.txt").write_bytes(single_bytes)

    expected_dir_sha = execution.artifact_directory_id(artifact_root / "docs")
    dir_closure = execution.verify_artifact_output(
        artifact_root,
        {"kind": "directory", "path": "docs", "sha256": expected_dir_sha},
    )
    assert dir_closure["ok"] is True
    assert dir_closure["sha256"] == expected_dir_sha
    assert dir_closure["files"] == ["docs/generated.md", "docs/nested/sub.txt"]
    assert dir_closure["file_digests"] == {
        "docs/generated.md": hashlib.sha256(gen_bytes).hexdigest(),
        "docs/nested/sub.txt": hashlib.sha256(sub_bytes).hexdigest(),
    }

    single_sha = hashlib.sha256(single_bytes).hexdigest()
    file_closure = execution.verify_artifact_output(
        artifact_root,
        {"kind": "file", "path": "out/single.txt", "sha256": single_sha},
    )
    assert file_closure["ok"] is True
    assert file_closure["file_digests"] == {"out/single.txt": single_sha}

    ok, reason, edge_entry = execution.evaluate_edge(
        {
            "producer_ref": {"kind": "prerequisite_action", "name": "gen"},
            "consumer_ref": {"kind": "check", "name": "docs"},
            "dependency_kind": "data",
            "requires_pass": True,
            "output_ref": {
                "kind": "directory",
                "path": "docs",
                "sha256": expected_dir_sha,
            },
        },
        {"status": "passed"},
        artifact_root=artifact_root,
    )
    assert ok is True and reason is None
    assert edge_entry["verified"] is True
    assert edge_entry["materialized_files"] == [
        "docs/generated.md",
        "docs/nested/sub.txt",
    ]
    assert edge_entry["materialized_file_digests"] == dir_closure["file_digests"]

    mismatch = execution.verify_artifact_output(
        artifact_root,
        {"kind": "directory", "path": "docs", "sha256": "0" * 64},
    )
    assert mismatch["ok"] is False
    assert mismatch["reason"] == "data_artifact_digest_mismatch"
    assert mismatch["file_digests"] == dir_closure["file_digests"]
    missing = execution.verify_artifact_output(
        artifact_root,
        {"kind": "directory", "path": "missing_dir"},
    )
    assert missing["ok"] is False
    assert missing["file_digests"] == {}
