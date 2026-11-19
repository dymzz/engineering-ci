"""Source boundaries for the Workflow and Shell files passed to native tools."""

import hashlib
import subprocess

import pytest
from test_workflow_security_checks import (
    UNPINNED_VALID_ACTIONLINT_WORKFLOW,
    _build_plan_and_expected,
    _git,
    _init_repo,
)

from engineering_ci import execution, selection
from engineering_ci import workflow_analysis as workflow


def _symlink(path, target, *, directory=False):
    try:
        path.symlink_to(target, target_is_directory=directory)
    except OSError as error:
        pytest.skip(f"filesystem does not permit symlinks: {error}")


def _external_authority(plan, alias, target, digest, checks):
    plan_ref = execution._canonical_sha256(plan)
    event = plan["git"]

    def authorize(request):
        return (
            request["kind"] == "external_target"
            and request["plan_ref"] == plan_ref
            and request["event"]["head_commit"] == event["head_commit"]
            and request["event"]["snapshot"] == event["snapshot"]
            and request["check"] in checks
            and request["source"]
            == {
                "path": alias,
                "resolved_path": str(target.resolve()),
                "sha256": digest,
            }
        )

    return authorize


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
    untrusted = execution.execute(
        plan, root=root, runner=native, admitted_external_targets=external
    )
    assert untrusted["status"] == "blocked"
    assert not any(any("shellcheck" in arg for arg in argv) for argv in calls)
    admitted = execution.execute(
        plan,
        root=root,
        runner=native,
        admitted_external_targets=external,
        workflow_source_authority=_external_authority(
            plan, alias, outside, external[alias]["sha256"], {"shellcheck"}
        ),
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
        assert raw["source_ref"]["verified_input_scope"] == sorted(paths)
        assert raw["source_ref"]["scope_kind"] == "changed_paths"
        assert (
            raw["source_ref"]["content_digest"]
            == selection.worktree_content_binding(root, paths)["digest"]
        )
        assert (
            raw["evidence"]["target_sources"][0]["sha256"]
            == hashlib.sha256((root / path).read_bytes()).hexdigest()
        )
        target_refs = [ref for ref in raw["input_refs"] if ref["kind"] == "scan_target"]
        assert [ref["path"] for ref in target_refs] == [path]


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
        scan_paths = [cwd / arg for arg in argv[-2:]]
        assert [path.name for path in scan_paths] == ["alias.sh", "normal.sh"]
        assert all(path.read_bytes() == b"#!/bin/sh\necho ok\n" for path in scan_paths)
        normal.write_bytes(b"#!/bin/sh\necho changed\n")
        assert all(path.read_bytes() == b"#!/bin/sh\necho ok\n" for path in scan_paths)
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
            authorize_external_target=lambda _name, _claim: True,
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


def test_git_free_directory_needs_independent_tree_materialization_proof(tmp_path):
    path = "scripts/check.sh"
    script = tmp_path / path
    script.parent.mkdir()
    script.write_bytes(b"#!/bin/sh\necho original\n")
    tree = "a" * 40
    content_id = selection.directory_content_id(tmp_path)
    plan = {
        "git": {
            "snapshot": "commit",
            "execution_tree": tree,
            "source_tree_id": content_id,
        }
    }
    record, problem = workflow._inspect_target(tmp_path, path, None)
    assert problem is None
    assert (
        execution._workflow_target_validator(plan, tmp_path, ())(record)
        == "workflow_target_tree_unverified"
    )

    def authority(kind, check, proof):
        return (
            kind == "tree_materialization"
            and check == "shellcheck"
            and proof == {"tree": tree, "source_tree_id": content_id}
        )

    validate = execution._workflow_target_validator(
        plan, tmp_path, (), authority=authority
    )
    assert validate(record, check_name="shellcheck") is None
    script.write_bytes(b"#!/bin/sh\necho different\n")
    changed, _ = workflow._inspect_target(tmp_path, path, None)
    validate = execution._workflow_target_validator(
        plan, tmp_path, (), authority=authority
    )
    assert (
        validate(changed, check_name="shellcheck") == "workflow_target_tree_unverified"
    )


@pytest.mark.parametrize("extra", [False, True])
def test_absolute_scan_paths_keep_their_path_error(tmp_path, extra):
    target = str(tmp_path / "outside.yml")
    checks = {"actionlint": {"status": "selected", "inputs": [] if extra else [target]}}

    def never_run(*_args):
        pytest.fail("invalid input started a native process")

    result = workflow.run_workflow_script_suite(
        checks,
        root=tmp_path,
        runner=never_run,
        extra_paths=(target,) if extra else (),
    )["actionlint"]
    assert result["status"] == "not_executed"
    assert result["target_problems"] == {target: "workflow_target_path_invalid"}


@pytest.mark.parametrize("problem", ["outside", "dangling", "directory"])
def test_discovery_problem_blocks_selected_native_workflow_checks(tmp_path, problem):
    root, _ = _init_repo(tmp_path, checks=("actionlint", "zizmor"), workflows={})
    prefix = root / ".github/workflows"
    prefix.mkdir(parents=True)
    external = tmp_path / "target.yml"
    alias = prefix / "alias.yml"
    if problem == "outside":
        external.write_bytes(b"name: outside\n")
    elif problem == "directory":
        external.mkdir()
        alias = prefix / "nested"
    _symlink(alias, external, directory=problem == "directory")
    logical = alias.relative_to(root).as_posix()
    plan, expected = _build_plan_and_expected(root, [], full=True)
    assert {row["name"] for row in plan["check_scope"] if logical in row["inputs"]} >= {
        "actionlint",
        "zizmor",
    }
    assert all(
        {"kind": "changed_path", "path": logical} in entry["input_refs"]
        for entry in expected["checks"]
        if entry["name"] in {"actionlint", "zizmor"}
    )

    def runner(argv, cwd, data=None):
        assert not any("actionlint" in arg or "zizmor" in arg for arg in argv)
        return workflow.default_run(argv, cwd, data)

    report = execution.execute(plan, root=root, runner=runner)
    reason = (
        "workflow_target_missing"
        if problem == "dangling"
        else "workflow_target_outside_root"
    )
    assert report["status"] == "blocked" and report["exit_code"] != 0
    for result in (
        row for row in report["checks"] if row["name"] in {"actionlint", "zizmor"}
    ):
        assert result["status"] == "not_executed" and "native_result" not in result
        assert result["target_problems"] == {logical: reason}


def test_approved_external_workflow_uses_project_rules_for_real_tools(tmp_path):
    root, _ = _init_repo(tmp_path, checks=("actionlint", "zizmor"), workflows={})
    (root / ".github/workflows").mkdir(parents=True)
    (root / ".github/actionlint.yaml").write_text(
        "self-hosted-runner:\n  labels: [eci-review-runner]\n",
        encoding="utf-8",
    )
    alias = ".github/workflows/outside.yml"
    external = tmp_path / "outside.yml"
    content = UNPINNED_VALID_ACTIONLINT_WORKFLOW.replace(
        "ubuntu-latest", "eci-review-runner"
    ).encode()
    external.write_bytes(content)
    _symlink(root / alias, external)
    digest = hashlib.sha256(content).hexdigest()
    plan, _ = _build_plan_and_expected(root, [alias])
    report = execution.execute(
        plan,
        root=root,
        admitted_external_targets={
            alias: {
                "path": str(external),
                "sha256": digest,
                "policy_scope": "event_external",
            }
        },
        workflow_source_authority=_external_authority(
            plan, alias, external, digest, {"actionlint", "zizmor"}
        ),
    )
    results = {item["name"]: item for item in report["checks"]}
    assert results["actionlint"]["native_result"]["native_exit_code"] == 0, results[
        "actionlint"
    ]
    assert results["zizmor"]["native_result"]["status"] == "failed", results["zizmor"]
    assert any("unpinned" in str(finding) for finding in results["zizmor"]["findings"])
    for name in ("actionlint", "zizmor"):
        observed = results[name]["native_result"]["evidence"]["target_sources"]
        assert observed[0]["path"] == alias and observed[0]["sha256"] == digest


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_consumer_view_rebases_internal_absolute_symlinks_with_overlay(tmp_path, kind):
    root, _ = _init_repo(
        tmp_path,
        checks=("shellcheck",),
        workflows={},
        shells={
            "scripts/original.sh": "#!/bin/sh\necho original\n",
        },
    )
    alias = "scripts/alias.sh"
    _symlink(root / alias, root / "scripts/original.sh")
    plan, _ = _build_plan_and_expected(root, [alias])
    producer = tmp_path / "producer"
    generated = "scripts/generated/from-producer.sh"
    (producer / generated).parent.mkdir(parents=True)
    content = b"#!/bin/sh\necho generated\n"
    (producer / generated).write_bytes(content)
    path = generated if kind == "file" else "scripts/generated"
    temporary, view = execution.build_consumer_view(root, [(producer, path, kind)])
    with temporary:
        assert (view / alias).resolve() == view / "scripts/original.sh"
        assert (view / generated).read_bytes() == content
        spec = next(row for row in plan["check_scope"] if row["name"] == "shellcheck")
        result = execution.run_host_check(
            execution._rebased_consumer_plan(plan, root),
            spec,
            root=view,
            artifact_paths=(generated,),
        )
        assert result["status"] == "passed", (result.get("reason"), result)
        inputs = {entry["path"]: entry["sha256"] for entry in result["target_sources"]}
        assert inputs == {
            alias: hashlib.sha256(
                (root / "scripts/original.sh").read_bytes()
            ).hexdigest(),
            generated: hashlib.sha256(content).hexdigest(),
        }
        assert not (root / generated).exists()


def test_directory_scan_guard_reports_resolved_bytes_and_blocks_escape(tmp_path):
    root = tmp_path / "repo"
    wf = ".github/workflows/test.yml"
    (root / wf).parent.mkdir(parents=True)
    content = b"name: test\n"
    (root / wf).write_bytes(content)
    policy = workflow.actionlint_policy()
    observed = workflow.repository_scan_targets(root, policy)
    assert workflow.repository_scan_refusal(policy, observed) is None
    assert observed["targets"] == [
        {
            "path": wf,
            "resolved_path": wf,
            "sha256": hashlib.sha256(content).hexdigest(),
            "policy_scope": "repo_target_tree",
        }
    ]
    external = tmp_path / "outside.yml"
    external.write_bytes(content)
    logical = ".github/workflows/outside.yml"
    _symlink(root / logical, external)
    refused = workflow.repository_scan_refusal(
        policy, workflow.repository_scan_targets(root, policy)
    )
    assert refused["outcome"] == "not_executed" and refused["exit_code"] is None
    assert refused["target_problems"] == {logical: "workflow_target_outside_root"}


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
