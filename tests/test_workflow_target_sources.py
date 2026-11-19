"""Source boundaries for the Workflow and Shell files passed to native tools."""

import hashlib
import shutil
import subprocess

import pytest
from test_reconciliation import make_observation, make_routing
from test_workflow_security_checks import (
    UNPINNED_VALID_ACTIONLINT_WORKFLOW,
    _build_plan_and_expected,
    _git,
    _init_repo,
)

from engineering_ci import execution, reconciliation, selection
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


def _reconciled_check(plan, name, native, resolution):
    expected = plan["expected_checks"]
    entry = next(item for item in expected["checks"] if item["name"] == name)
    receipt = reconciliation.reconcile_expected_results(
        expected,
        routing=[make_routing(entry)],
        resolutions=[resolution] if resolution is not None else [],
        observations=[make_observation("job-1", [native])],
    )
    return next(item for item in receipt["entries"] if item["name"] == name)


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


@pytest.mark.parametrize(
    ("snapshot", "has_findings"),
    [("worktree", True), ("commit", True), ("commit", False)],
)
def test_external_source_handoff_reaches_independent_resolution(
    tmp_path, snapshot, has_findings
):
    body = (
        b"#!/bin/sh\necho $UNQUOTED\n" if has_findings else b"#!/bin/sh\necho clean\n"
    )
    outside = tmp_path / "outside.sh"
    outside.write_bytes(body)
    root, _ = _init_repo(
        tmp_path / "dirs",
        workflows={},
        shells={"scripts/ok.sh": "#!/bin/sh\necho ok\n"},
    )
    alias = "scripts/external.sh"
    _symlink(root / alias, outside)
    if snapshot == "commit":
        _git(root, "add", alias)
        _git(root, "commit", "-qm", "record external target link")
    plan, _ = _build_plan_and_expected(root, [alias], snapshot=snapshot)
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
    authority = _external_authority(
        plan, alias, outside, external[alias]["sha256"], {"shellcheck"}
    )
    admitted = execution.execute(
        plan,
        root=root,
        runner=native,
        admitted_external_targets=external,
        workflow_source_authority=authority,
    )
    result = next(item for item in admitted["checks"] if item["name"] == "shellcheck")
    assert admitted["status"] == ("failed" if has_findings else "passed")
    raw = result["native_result"]
    assert raw["status"] == admitted["status"]
    assert raw["native_exit_code"] == (1 if has_findings else 0)
    assert (
        raw["source_ref"]["actual_execution_source"] == "worktree_with_event_external"
    )
    assert alias in raw["source_ref"]["verified_input_scope"]
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
    assert (
        any(finding["rule"] == "SC2086" for finding in result["findings"])
        is has_findings
    )

    def resolve(grant=external, verify=authority):
        return execution.source_resolution_receipt(
            plan,
            "shellcheck",
            root=root,
            admitted_external_targets=grant,
            workflow_source_authority=verify,
        )

    resolved = resolve()
    assert resolved["source_ref"] == raw["source_ref"]
    assert [ref for ref in resolved["input_refs"] if ref["kind"] == "scan_target"] == [
        {"kind": "scan_target", **raw["evidence"]["target_sources"][0]}
    ]
    entry = _reconciled_check(plan, "shellcheck", raw, resolved)
    if snapshot == "worktree":
        assert resolved["source_ref"]["scope_kind"] == "changed_paths"
        assert (entry["binding_status"], entry["reason"]) == (
            "unknown",
            "expected_source_scope_insufficient",
        )
    else:
        assert resolved["source_ref"]["scope_kind"] == "check_input_closure"
        assert resolved["source_ref"]["binding_status"] == "content_digest"
        assert entry["resolved_expected"]["source_ref"] == resolved["source_ref"]
        assert not entry["rejected_results"]
        assert entry["native_satisfaction"]["status"] == (
            "unsatisfied" if has_findings else "satisfied"
        )
        assert (entry["binding_status"] == "matched") is not has_findings

    wrong_grant = {alias: {**external[alias], "sha256": "0" * 64}}
    for grant, verifier in (
        (None, authority),
        (external, None),
        (external, lambda request: False),
        (wrong_grant, authority),
        (
            external,
            _external_authority(
                plan, alias, outside, external[alias]["sha256"], {"actionlint"}
            ),
        ),
    ):
        with pytest.raises(
            execution.ExecutionError, match="workflow_target_outside_root"
        ):
            resolve(grant, verifier)
    unmatched = _reconciled_check(plan, "shellcheck", raw, None)
    assert (unmatched["binding_status"], unmatched["reason"]) == (
        "unknown",
        "resolution_missing",
    )
    outside.write_bytes(b"#!/bin/sh\necho changed\n")
    with pytest.raises(
        execution.ExecutionError, match="workflow_target_digest_mismatch"
    ):
        resolve()


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
        resolved = execution.build_source_resolution(plan, check, root=root)
        assert resolved["source_ref"] == raw["source_ref"]
        assert [
            ref for ref in resolved["input_refs"] if ref["kind"] == "scan_target"
        ] == target_refs
        entry = _reconciled_check(plan, check, raw, resolved)
        assert (entry["binding_status"], entry["reason"]) == (
            "unknown",
            "expected_source_scope_insufficient",
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


def test_git_free_tree_handoff_reaches_independent_resolution(tmp_path):
    path = "scripts/check.sh"
    repo, _ = _init_repo(
        tmp_path / "original",
        checks=("shellcheck",),
        workflows={},
        shells={path: "#!/bin/sh\necho original\n"},
    )
    plan, _ = _build_plan_and_expected(
        repo, [path], snapshot="commit", with_baseline=False
    )
    plan = execution._rebased_consumer_plan(plan, repo)
    git = plan["git"]
    approved_plan = execution._canonical_sha256(plan)
    content_id = git["source_tree_id"]
    directory = tmp_path / "materialized"
    shutil.copytree(repo, directory, ignore=shutil.ignore_patterns(".git"))
    assert selection._resolve_git_dir(directory) is None
    assert selection.directory_content_id(directory) == content_id

    def authority(request):
        return (
            request["kind"] == "tree_materialization"
            and request["plan_ref"] == approved_plan
            and request["check"] == "shellcheck"
            and request["event"]["snapshot"] == "commit"
            and request["event"]["head_commit"] == git["head_commit"]
            and request["event"]["execution_tree"] == git["execution_tree"]
            and request["source"]
            == {"tree": git["execution_tree"], "source_tree_id": content_id}
        )

    def resolve(document, verify=None):
        return execution.build_source_resolution(
            document, "shellcheck", root=directory, workflow_source_authority=verify
        )

    with pytest.raises(
        execution.ExecutionError, match="workflow_target_tree_unverified"
    ):
        resolve(plan)
    report = execution.execute(
        plan, root=directory, workflow_source_authority=authority
    )
    result = next(item for item in report["checks"] if item["name"] == "shellcheck")
    assert result["status"] == "passed", report
    native = result["native_result"]
    assert native["native_exit_code"] == 0
    resolved = resolve(plan, authority)
    assert resolved["source_ref"] == native["source_ref"]
    assert resolved["source_ref"]["actual_execution_source"] == "materialized_directory"
    assert resolved["source_ref"]["materialization_ref"] == f"sha256:{content_id}"
    assert resolved["source_ref"]["binding_status"] == "byte_tree"
    assert resolved["source_ref"]["scope_kind"] == "full_input_closure"
    observed = next(ref for ref in native["input_refs"] if ref["kind"] == "scan_target")
    assert (
        observed["sha256"]
        == hashlib.sha256((directory / path).read_bytes()).hexdigest()
    )
    assert observed in resolved["input_refs"]
    assert (
        _reconciled_check(plan, "shellcheck", native, resolved)["binding_status"]
        == "matched"
    )

    # A plan for a different Git Tree can repeat the Directory's digest, but
    # the runtime grant still binds the original Tree and Plan.
    (repo / path).write_bytes(b"#!/bin/sh\necho different tree\n")
    _git(repo, "commit", "-qam", "create another source tree")
    wrong_tree, _ = _build_plan_and_expected(
        repo, [path], snapshot="commit", with_baseline=False
    )
    wrong_tree = execution._rebased_consumer_plan(wrong_tree, repo)
    assert wrong_tree["git"]["execution_tree"] != git["execution_tree"]
    wrong_tree["git"]["source_tree_id"] = content_id
    with pytest.raises(
        execution.ExecutionError, match="workflow_target_tree_unverified"
    ):
        resolve(wrong_tree, authority)
    denied = execution.execute(
        wrong_tree, root=directory, workflow_source_authority=authority
    )
    denied_check = next(
        item for item in denied["checks"] if item["name"] == "shellcheck"
    )
    assert denied_check["reason"] == "workflow_target_tree_unverified"
    assert "native_result" not in denied_check

    (directory / path).write_bytes(b"#!/bin/sh\necho changed materialization\n")
    with pytest.raises(execution.ExecutionError, match="source_tree_mismatch"):
        resolve(plan, authority)


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
    _git(root, "add", ".github")
    _git(root, "commit", "-qm", "record Workflow link and project rules")
    plan, _ = _build_plan_and_expected(root, [alias], snapshot="commit")
    handoff = {
        "admitted_external_targets": {
            alias: {
                "path": str(external.resolve()),
                "sha256": digest,
                "policy_scope": "event_external",
            }
        },
        "workflow_source_authority": _external_authority(
            plan, alias, external, digest, {"actionlint", "zizmor"}
        ),
    }
    report = execution.execute(plan, root=root, **handoff)
    results = {item["name"]: item for item in report["checks"]}
    assert results["actionlint"]["native_result"]["native_exit_code"] == 0, results[
        "actionlint"
    ]
    assert results["zizmor"]["native_result"]["status"] == "failed", results["zizmor"]
    assert any("unpinned" in str(finding) for finding in results["zizmor"]["findings"])
    for name in ("actionlint", "zizmor"):
        native = results[name]["native_result"]
        observed = native["evidence"]["target_sources"]
        assert observed[0]["path"] == alias and observed[0]["sha256"] == digest
        resolved = execution.build_source_resolution(plan, name, root=root, **handoff)
        assert resolved["source_ref"] == native["source_ref"]
        assert {"kind": "scan_target", **observed[0]} in resolved["input_refs"]
        entry = _reconciled_check(plan, name, native, resolved)
        assert not entry["rejected_results"]
        assert entry["native_satisfaction"]["status"] == (
            "satisfied" if name == "actionlint" else "unsatisfied"
        )
    wrong_expected = next(
        item["expected_check_ref"]
        for item in plan["expected_checks"]["checks"]
        if item["name"] == "zizmor"
    )
    with pytest.raises(
        execution.ExecutionError, match="source_resolution_expected_mismatch"
    ):
        execution.build_source_resolution(
            plan, "actionlint", root=root, expected_check_ref=wrong_expected, **handoff
        )


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
