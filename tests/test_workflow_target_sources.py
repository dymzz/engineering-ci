"""Source boundaries for the Workflow and Shell files passed to native tools."""

import hashlib
import subprocess

import pytest
from test_workflow_security_checks import _build_plan_and_expected, _git, _init_repo

from engineering_ci import execution, selection
from engineering_ci import workflow_analysis as workflow


def _symlink(path, target, *, directory=False):
    try:
        path.symlink_to(target, target_is_directory=directory)
    except OSError as error:
        pytest.skip(f"filesystem does not permit symlinks: {error}")


@pytest.mark.parametrize(
    ("discover", "prefix", "suffix"),
    [
        (workflow.discover_workflow_files, ".github/workflows", ".yml"),
        (workflow.discover_shell_files, "scripts", ".sh"),
    ],
)
def test_discovery_reports_unsafe_targets(tmp_path, discover, prefix, suffix):
    root = tmp_path / "repo"
    directory = root / prefix
    directory.mkdir(parents=True)
    normal = directory / ("normal" + suffix)
    normal.write_bytes(b"shared")
    outside = tmp_path / ("outside" + suffix)
    outside.write_bytes(b"shared")
    for name, target in (
        ("inside", normal),
        ("outside", outside),
        ("dangling", directory / "missing"),
    ):
        _symlink(directory / (name + suffix), target)
    problems = {}
    assert discover(root, (prefix,), problems=problems) == [
        f"{prefix}/inside{suffix}",
        f"{prefix}/normal{suffix}",
    ]
    assert problems == {
        f"{prefix}/dangling{suffix}": "workflow_target_missing",
        f"{prefix}/outside{suffix}": "workflow_target_outside_root",
    }
    admitted = {
        f"{prefix}/outside{suffix}": {
            "path": str(outside),
            "sha256": hashlib.sha256(b"shared").hexdigest(),
            "policy_scope": "event_external",
        }
    }
    assert f"{prefix}/outside{suffix}" in discover(
        root, (prefix,), admitted_external_targets=admitted
    )


def test_discovery_does_not_walk_external_prefix(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "example.sh").write_bytes(b"#!/bin/sh\n")
    _symlink(root / "scripts", outside, directory=True)
    problems = {}
    assert workflow.discover_shell_files(root, ("scripts",), problems=problems) == []
    assert problems == {"scripts": "workflow_target_outside_root"}


def test_external_source_authorization_and_native_failure(tmp_path):
    body = b"#!/bin/sh\necho $UNQUOTED\n"
    outside = tmp_path / "outside.sh"
    outside.write_bytes(body)
    root, _ = _init_repo(
        tmp_path / "dirs",
        workflows={},
        shells={"scripts/ok.sh": "#!/bin/sh\necho ok\n"},
    )
    alias = "scripts/external.sh"
    _symlink(root / alias, outside)
    plan, _ = _build_plan_and_expected(root, [alias])
    calls = []

    def native(argv, cwd):
        calls.append(tuple(argv))
        if "--format=json1" in argv:
            staged = cwd / argv[-1]
            assert staged.is_relative_to(root) and staged.read_bytes() == body
        return workflow.default_run(argv, cwd)

    blocked = execution.execute(plan, root=root, runner=native)
    result = next(item for item in blocked["checks"] if item["name"] == "shellcheck")
    assert blocked["status"] == "blocked"
    assert result["reason"] == "workflow_target_outside_root"
    assert "native_result" not in result
    assert not any(any("shellcheck" in arg for arg in argv) for argv in calls)
    assert selection.worktree_content_binding(root, [alias])["paths"] == {alias: None}

    external = {
        alias: {
            "path": str(outside.resolve()),
            "sha256": hashlib.sha256(body).hexdigest(),
            "policy_scope": "event_external",
        }
    }
    admitted = execution.execute(
        plan, root=root, runner=native, admitted_external_targets=external
    )
    result = next(item for item in admitted["checks"] if item["name"] == "shellcheck")
    assert admitted["status"] == "failed"
    raw = result["native_result"]
    assert raw["status"] == "failed" and raw["native_exit_code"] == 1
    assert (
        raw["source_ref"]["actual_execution_source"] == "worktree_with_event_external"
    )
    assert raw["source_ref"]["verified_input_scope"] == [alias]
    assert raw["evidence"]["target_sources"] == [
        {
            "path": alias,
            "resolved_path": str(outside.resolve()),
            "sha256": external[alias]["sha256"],
            "policy_scope": "event_external",
        }
    ]
    assert any("--format=json1" in argv for argv in calls)
    assert not any(argv[-1] == str(outside.resolve()) for argv in calls)
    assert any(finding["rule"] == "SC2086" for finding in result["findings"])


def test_workflow_and_shell_native_inputs_have_independent_sources(tmp_path):
    root, _ = _init_repo(
        tmp_path,
        checks=("actionlint", "shellcheck"),
        workflows={
            ".github/workflows/ci.yml": "name: ci\non: [push]\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n"
        },
        shells={"scripts/check.sh": "#!/bin/sh\necho ok\n"},
    )
    paths = [".github/workflows/ci.yml", "scripts/check.sh"]
    plan, _ = _build_plan_and_expected(root, paths)
    for check, path in zip(("actionlint", "shellcheck"), paths, strict=True):
        spec = next(row for row in plan["check_scope"] if row["name"] == check)
        result = execution.run_host_check(plan, spec, root=root)
        assert "native_result" in result, result
        raw = result["native_result"]
        assert raw["status"] == "passed"
        assert raw["source_ref"]["verified_input_scope"] == [path]
        assert (
            raw["source_ref"]["content_digest"]
            == selection.worktree_content_binding(root, [path])["digest"]
        )
        assert (
            raw["evidence"]["target_sources"][0]["sha256"]
            == hashlib.sha256((root / path).read_bytes()).hexdigest()
        )


def test_extra_paths_and_changed_bytes_are_checked_at_execution(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    normal = root / "normal.sh"
    normal.write_bytes(b"#!/bin/sh\necho ok\n")
    alias = root / "alias.sh"
    _symlink(alias, normal)
    outside = tmp_path / "outside.sh"
    outside.write_bytes(normal.read_bytes())
    _symlink(root / "external.sh", outside)
    selected = {"shellcheck": {"status": "selected", "inputs": ["normal.sh"]}}
    calls = []

    def runner(argv, cwd):
        calls.append(tuple(argv))
        if "--version" in argv:
            return subprocess.CompletedProcess(argv, 0, "version: 0.11.0", "")
        assert argv[-2:] == ["normal.sh", "normal.sh"]
        normal.write_bytes(b"#!/bin/sh\necho changed\n")
        return subprocess.CompletedProcess(argv, 0, '{"comments": []}', "")

    grant = {
        "external.sh": {
            "path": str(outside.resolve()),
            "sha256": "0" * 64,
            "policy_scope": "event_external",
        }
    }
    for external, reason in [(None, "outside_root"), (grant, "digest_mismatch")]:
        result = workflow.run_workflow_script_suite(
            selected,
            root=root,
            runner=runner,
            extra_paths=("external.sh",),
            admitted_external_targets=external,
        )["shellcheck"]
        assert result["reason"] == "workflow_target_" + reason
        assert result["status"] == "not_executed"
        assert not calls
    result = workflow.run_workflow_script_suite(
        selected,
        root=root,
        runner=runner,
        extra_paths=("alias.sh",),
    )["shellcheck"]
    assert result["status"] == "unknown"
    assert result["native_status"] == "passed" and result["exit_code"] == 0
    assert result["reason"] == "workflow_target_changed_during_execution"


def test_git_source_rejects_bytes_from_another_tree(tmp_path):
    root, _ = _init_repo(
        tmp_path,
        workflows={},
        shells={"scripts/check.sh": "#!/bin/sh\necho original\n"},
    )
    tree = _git(root, "rev-parse", "HEAD^{tree}").strip()
    path = "scripts/check.sh"
    validate = execution._workflow_target_validator(
        {"git": {"snapshot": "commit", "execution_tree": tree}}, root, ()
    )
    original = (root / path).read_bytes()
    target = {
        "path": path,
        "resolved_path": path,
        "policy_scope": "repo_target_tree",
        "sha256": hashlib.sha256(original).hexdigest(),
    }
    assert validate(target) is None
    assert validate({**target, "sha256": "a" * 64}) == "workflow_target_tree_mismatch"


def test_shared_actionlint_cache_cannot_reuse_another_target_snapshot(tmp_path):
    path = ".github/workflows/ci.yml"
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"name: first\n")
    selected = {"actionlint": {"status": "selected", "inputs": [path]}}
    cache = {}
    scans = []

    def runner(argv, cwd):
        if "-version" in argv:
            return subprocess.CompletedProcess(argv, 0, "1.7.12", "")
        scans.append((cwd / argv[-1]).read_bytes())
        return subprocess.CompletedProcess(argv, 0, "[]", "")

    first = workflow.run_workflow_script_suite(
        selected, root=tmp_path, runner=runner, shared_cache=cache
    )["actionlint"]
    target.write_bytes(b"name: second\n")
    second = workflow.run_workflow_script_suite(
        selected, root=tmp_path, runner=runner, shared_cache=cache
    )["actionlint"]
    assert scans == [b"name: first\n", b"name: second\n"]
    assert first["target_sources"][0]["sha256"] != second["target_sources"][0]["sha256"]
