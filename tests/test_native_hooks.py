"""Exercise ECI through Lefthook installed in isolated Git repositories."""

import json
import os
import shutil
import subprocess
import venv
from pathlib import Path

import pytest
from test_hooks import _copy_lefthook_command, _git_sh_env, git
from test_reconciliation import _attach_effective_profile_policy

from engineering_ci import (
    check_contract,
    cli,
    dagger_plan,
    expected_checks,
    git_source,
    hooks,
    selection,
)

ROOT = Path(__file__).resolve().parents[1]
LEFTHOOK = ("uv", "run", "--locked", "--no-sync", "lefthook")
INDEX_SELECTION = (
    'schema_version = 1\nchecks = ["hygiene"]\nstrict_modules = false\n'
    'ignored_files = ["ci-selection.toml", "ci-hygiene.toml", '
    '"ci-effective-profile.json", "tach.toml", "lefthook.yml"]\n'
)


@pytest.fixture
def hook_env():
    env = _git_sh_env()
    env.pop("LEFTHOOK", None)
    env.pop("LEFTHOOK_BIN", None)
    env.pop("LEFTHOOK_EXCLUDE", None)
    return env


def _diagnostic(result):
    return f"{result.args} exited {result.returncode}\n{result.stdout}\n{result.stderr}"


def _run(root, env, *args, exit_code=0, timeout=60):
    result = subprocess.run(
        args,
        cwd=root,
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
    )
    assert result.returncode == exit_code, _diagnostic(result)
    return result


def _init(root, env, *, config=None):
    root.mkdir()
    _run(root, env, "git", "init", "-q", "-b", "main", "--template=")
    git(root, "config", "user.email", "hooks@example.invalid", env=env)
    git(root, "config", "user.name", "Native Hook Fixture", env=env)
    git(root, "config", "core.autocrlf", "false", env=env)
    git(root, "config", "core.hooksPath", str(root / ".git/hooks"), env=env)
    if config is None:
        config = (ROOT / "lefthook.yml").read_text(encoding="utf-8")
    (root / "lefthook.yml").write_text(config, encoding="utf-8", newline="\n")
    _copy_lefthook_command(root)


def test_lefthook_fallback_preserves_empty_and_extra_arguments(tmp_path, hook_env):
    root = tmp_path / "argv"
    _init(
        root,
        hook_env,
        config=(
            "lefthook: sh scripts/lefthook-arguments.sh\n"
            "post-checkout:\n  jobs:\n    - name: sample\n      run: true\n"
        ),
    )
    _run(root, hook_env, *LEFTHOOK, "install", "post-checkout")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_bytes(b'#!/bin/sh\nprintf "<%s>\\n" "$@"\n')
    uv.chmod(0o755)
    env = {**hook_env, "PATH": os.pathsep.join([str(bin_dir), hook_env["PATH"]])}
    args = ("path with spaces *", "", "third", "fourth", "fifth")
    result = _run(
        root,
        env,
        "git",
        "hook",
        "run",
        "post-checkout",
        "--",
        *args,
    )
    forwarded = ("run", "--locked", "--no-sync", "lefthook", "run", "post-checkout")
    assert result.stderr.splitlines() == [f"<{arg}>" for arg in (*forwarded, *args)]


def _source(root, module, checks):
    (root / "ci-hygiene.toml").write_bytes(
        f"schema_version = 1\nchecks = {json.dumps(checks)}\n".encode()
    )
    (root / "tach.toml").write_bytes(
        f'source_roots = ["src"]\n[[modules]]\npath = "{module}"\ndepends_on = []\n'.encode()
    )
    (root / "src").mkdir()
    return root / "src" / f"{module}.py"


def _approve_profiles(root, selection_config, approvals):
    config = root / "ci-selection.toml"
    blocks = []
    for filename, context, action, target, checks in approvals:
        profile = expected_checks.build_effective_profile(
            profile_ref=check_contract.profile_ref("eci/native-hook-fixture", "1.0.0"),
            profile_revision=filename,
            approval_source="governance-signed",
            effective_at="2026-11-16T00:00:00Z",
            evaluated_at="2026-11-16T00:00:00Z",
            context=context,
            action=action,
            target=target,
            declared_checks=list(checks),
            required_obligations=[
                {
                    "obligation_ref": f"req-{name}",
                    "check_names": [name],
                    "target_refs": [target],
                    "allow_inapplicable": name == "forwarding",
                }
                for name in checks
            ],
            domain_complete=True,
            authority_verified=True,
        )
        config.write_bytes(selection_config.encode())
        _attach_effective_profile_policy(config, profile)
        blocks.append(
            config.read_text(encoding="utf-8").split("[effective_profile]", 1)[1]
        )
        (root / filename).write_bytes(json.dumps(profile).encode())
    config.write_bytes(
        (
            selection_config
            + "".join(f"\n[[effective_profile]]{block}" for block in blocks)
        ).encode()
    )


def _invocations(root, env):
    return (
        Path(git(root, "rev-parse", "--absolute-git-dir", env=env))
        / "eci-hooks/invocations"
    )


def _new_invocation(directory, result, previous=()):
    new = set(directory.glob("*.json")).difference(previous)
    assert len(new) == 1, f"{new}\n{_diagnostic(result)}"
    return hooks.load_invocation_binding(new.pop())


def _artifact(binding, suffix):
    return json.loads(binding.invocation_path.with_suffix(suffix).read_bytes())


def _require_overlapping_jobs(directory, env):
    # A rendezvous in the actual Check Job processes detects serialization
    # without using elapsed time as evidence. The real checks still run.
    directory.mkdir()
    (directory / "sitecustomize.py").write_text(
        """import os
import sys
import time
from pathlib import Path

if sys.argv[1:4] == ["hooks", "run", "pre-commit"]:
    from engineering_ci import hook_checks

    original = hook_checks.run_checks
    jobs = {"check-whitespace", "check-forwarding"}

    def run(root, binding, *, job_ref):
        if job_ref in jobs:
            attempt = Path(os.environ["ECI_TEST_JOB_RENDEZVOUS"]) / binding.invocation_path.stem
            attempt.mkdir(exist_ok=True)
            (attempt / (job_ref + ".entered")).touch()
            peer, = jobs - {job_ref}
            deadline = time.monotonic() + 15
            while not (attempt / (peer + ".entered")).is_file():
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"{peer} did not enter while {job_ref} was active")
                time.sleep(0.05)
            (attempt / (job_ref + ".overlapped")).touch()
        return original(root, binding, job_ref=job_ref)

    hook_checks.run_checks = run
""",
        encoding="utf-8",
        newline="\n",
    )
    env["ECI_TEST_JOB_RENDEZVOUS"] = str(directory)
    env["PYTHONPATH"] = os.pathsep.join((str(directory), env["PYTHONPATH"]))


@pytest.mark.parametrize(
    ("excluded", "whitespace_result", "exit_code"),
    [
        pytest.param(None, "matched", 0, id="overlapping-jobs"),
        pytest.param(None, "insufficient", 1, id="failed-job-still-reconciles"),
        pytest.param(
            "check-whitespace", "unknown", 1, id="skipped-job-still-reconciles"
        ),
        pytest.param("reconcile-index", "matched", 1, id="missing-final-receipt"),
    ],
)
def test_installed_lefthook_reconciles_independent_jobs(
    tmp_path, hook_env, excluded, whitespace_result, exit_code
):
    root = tmp_path / "repo"
    rendezvous = tmp_path / "overlap"
    _init(root, hook_env)
    checks = ("whitespace", "forwarding")
    source = _source(root, "initial", checks)
    source.write_bytes(
        b"VALUE = 1 \n" if whitespace_result == "insufficient" else b"VALUE = 1\n"
    )
    _approve_profiles(
        root,
        INDEX_SELECTION,
        [("ci-effective-profile.json", "index", "index", "HEAD", checks)],
    )
    # Stage the source and policy under test. The command script is fixture setup.
    git(
        root,
        "add",
        "ci-effective-profile.json",
        "ci-hygiene.toml",
        "ci-selection.toml",
        "lefthook.yml",
        "src",
        "tach.toml",
        env=hook_env,
    )
    _run(root, hook_env, *LEFTHOOK, "install", "pre-commit")
    if excluded:
        hook_env["LEFTHOOK_EXCLUDE"] = excluded
    if exit_code == 0:
        _require_overlapping_jobs(rendezvous, hook_env)

    commit = _run(
        root,
        hook_env,
        "git",
        "commit",
        "-m",
        "Check staged source",
        exit_code=exit_code,
    )
    binding = _new_invocation(_invocations(root, hook_env), commit)
    assert binding.hook == "pre-commit"
    jobs = {"check-whitespace", "check-forwarding"}
    if excluded == "check-whitespace":
        assert not binding.invocation_path.with_suffix(
            ".activation-check-whitespace"
        ).exists()
        assert not binding.invocation_path.with_suffix(
            ".execution-check-whitespace"
        ).exists()
        jobs.remove(excluded)
    for job in jobs:
        activation = _artifact(binding, f".activation-{job}")
        assert activation["invocation_ref"] == binding.invocation_ref
        execution = _artifact(binding, f".execution-{job}")
        assert execution["invocation_ref"] == binding.invocation_ref
        failed = job == "check-whitespace" and whitespace_result == "insufficient"
        report = execution["report"]
        assert report["exit_code"] == (1 if failed else 0)
        if job == "check-whitespace":
            assert report["checks"][0]["native_result"]["native_exit_code"] == (
                2 if failed else 0
            )
    if excluded == "reconcile-index":
        assert "hook_final_result_missing" in _diagnostic(commit)
        assert not binding.invocation_path.with_suffix(".receipt").exists()
    else:
        receipt = _artifact(binding, ".receipt")
        assert receipt["expected_invocation_ref"] == binding.invocation_ref
        assert receipt["trusted"] is False
        assert receipt["required"] is False
        assert receipt["evidence_admission"] == "not_evaluated"
        assert receipt["required_gate"] == "not_evaluated"
        assert all(
            entry["scheduler_ref"] is None and entry["effective_config_ref"] is None
            for entry in receipt["entries"]
        )
        assert {
            entry["name"]: (entry["binding_status"], entry["job_refs"])
            for entry in receipt["entries"]
        } == {
            "whitespace": (whitespace_result, ["check-whitespace"]),
            "forwarding": ("matched", ["check-forwarding"]),
        }
    if exit_code == 0:
        attempt = rendezvous / binding.invocation_path.stem
        assert {path.stem for path in attempt.glob("*.overlapped")} == jobs


def test_installed_lefthook_captures_linked_worktree_index_and_push_refs(
    tmp_path, hook_env
):
    root = tmp_path / "primary"
    linked = tmp_path / "linked worktree"
    remote = tmp_path / "remote repository.git"
    _init(root, hook_env)
    checks = ("whitespace",)
    source = _source(root, "sample", checks)
    source.write_bytes(b"VALUE = 1\n")
    _approve_profiles(
        root,
        INDEX_SELECTION + '\n[push]\nnew_ref_baseline = "refs/heads/baseline"\n'
        'effective_profiles = ["push-main.json", "push-topic.json"]\n',
        [
            ("ci-effective-profile.json", "index", "index", "HEAD", checks),
            ("push-main.json", "push", "update", "refs/heads/main", checks),
            ("push-topic.json", "push", "create", "refs/heads/topic", checks),
        ],
    )
    git(root, "add", "--all", env=hook_env)
    git(root, "commit", "-qm", "Baseline", env=hook_env)
    baseline = git(root, "rev-parse", "HEAD", env=hook_env)
    git(root, "branch", "baseline", env=hook_env)
    git(root, "init", "--bare", "-q", "--template=", str(remote), env=hook_env)
    git(root, "remote", "add", "origin", str(remote), env=hook_env)
    git(root, "push", "-q", "origin", "baseline:main", env=hook_env)

    source.write_bytes(b"VALUE = 2\n")
    git(root, "commit", "-qam", "Main target", env=hook_env)
    main = git(root, "rev-parse", "HEAD", env=hook_env)
    git(root, "switch", "-qc", "topic", "baseline", env=hook_env)
    source.write_bytes(b"VALUE = 3\n")
    git(root, "commit", "-qam", "Topic target", env=hook_env)
    topic = git(root, "rev-parse", "HEAD", env=hook_env)
    git(root, "worktree", "add", "-qb", "linked", str(linked), "baseline", env=hook_env)
    _run(root, hook_env, *LEFTHOOK, "install")

    linked_source = linked / source.relative_to(root)
    linked_source.write_bytes(b"VALUE = 4\n")
    git(linked, "add", "src/sample.py", env=hook_env)
    dirty_bytes = b"VALUE = 5 \n"
    linked_source.write_bytes(dirty_bytes)
    commit = _run(linked, hook_env, "git", "commit", "-m", "Linked Index")
    directory = _invocations(linked, hook_env)
    index = _new_invocation(directory, commit)
    receipt = _artifact(index, ".receipt")
    assert index.hook == "pre-commit"
    assert receipt["expected_invocation_ref"] == index.invocation_ref
    assert receipt["status"] == "matched"
    assert git(linked, "show", "HEAD:src/sample.py", env=hook_env) == "VALUE = 4"

    push = _run(linked, hook_env, "git", "push", "origin", "main:main", "topic:topic")
    binding = _new_invocation(directory, push, [index.invocation_path])
    assert binding.hook == "pre-push"
    assert binding.event["remote"]["name"]["value"] == "origin"
    outcome = _artifact(binding, ".receipt")
    prepared = _artifact(binding, ".plan")
    assert outcome["invocation_ref"] == binding.invocation_ref
    assert outcome["plan_ref"] == prepared["plan_ref"]
    expected = {"refs/heads/main": main, "refs/heads/topic": topic}
    assert {
        ref["remote_ref"]: ref["after"] for ref in binding.event["refs"]
    } == expected
    for native_ref, plan, receipt in zip(
        binding.event["refs"], prepared["plan"], outcome["receipts"], strict=True
    ):
        assert plan["git"]["base_commit"] == baseline
        assert plan["git"]["head_commit"] == native_ref["after"]
        assert receipt["status"] == "matched"
        assert receipt["expected_invocation_ref"] == binding.invocation_ref
        assert (
            git(remote, "rev-parse", native_ref["remote_ref"], env=hook_env)
            == native_ref["after"]
        )

    assert linked_source.read_bytes() == dirty_bytes
    assert not list(_invocations(root, hook_env).glob("*.json"))


def test_default_pre_push_uses_the_repository_profiles(tmp_path, hook_env):
    root = tmp_path / "repo"
    remote = tmp_path / "local remote.git"
    upstream = tmp_path / "upstream.git"
    _init(root, hook_env)
    source = root / "sample.py"
    source.write_bytes(b"VALUE = 1\n")
    (root / "tests").mkdir()
    (root / "tests/test_example.py").write_bytes(
        b"def test_example():\n    assert True\n"
    )
    git(root, "add", "--all", env=hook_env)
    git(root, "commit", "-qm", "Baseline before the push policy", env=hook_env)
    baseline = git(root, "rev-parse", "HEAD", env=hook_env)
    git(root, "init", "--bare", "-q", "--template=", str(remote), env=hook_env)
    git(root, "remote", "add", "origin", str(remote), env=hook_env)
    git(root, "push", "-q", "origin", "main", env=hook_env)

    cfg = selection.load_config(ROOT / "ci-selection.toml")
    for name in (
        "ci-selection.toml",
        "ci-hygiene.toml",
        ".github/zizmor.yml",
        *cfg.push.effective_profiles,
    ):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes().replace(b"\r\n", b"\n"))
    git(root, "add", "--all", env=hook_env)
    git(root, "commit", "-qm", "Adopt the repository push policy", env=hook_env)
    policy_base = git(root, "rev-parse", "HEAD", env=hook_env)
    source.write_bytes(b"VALUE = 2\n")
    git(root, "commit", "-qam", "Main update", env=hook_env)
    main = git(root, "rev-parse", "HEAD", env=hook_env)
    git(root, "switch", "-qc", "feature/topic", policy_base, env=hook_env)
    source.write_bytes(b"VALUE = 3\n")
    git(root, "commit", "-qam", "Feature update", env=hook_env)
    topic = git(root, "rev-parse", "HEAD", env=hook_env)
    git(root, "tag", "-a", "candidate", "-m", "Annotated candidate", env=hook_env)
    tag = git(root, "rev-parse", "refs/tags/candidate", env=hook_env)
    git(root, "switch", "-qc", "rejected", env=hook_env)
    source.write_bytes(b"VALUE = 4 \n")
    git(root, "commit", "-qam", "Commit with trailing whitespace", env=hook_env)
    git(root, "switch", "-q", "feature/topic", env=hook_env)
    git(root, "init", "--bare", "-q", "--template=", str(upstream), env=hook_env)
    git(root, "remote", "add", "upstream", str(upstream), env=hook_env)
    git(root, "push", "-qu", "upstream", "feature/topic", env=hook_env)
    git(root, "config", "push.default", "simple", env=hook_env)
    git(root, "config", "remote.pushDefault", "upstream", env=hook_env)
    assert git(root, "diff", "--name-only", "HEAD", "@{push}", env=hook_env) == ""
    _run(root, hook_env, *LEFTHOOK, "install")
    directory = _invocations(root, hook_env)

    def push(*refspecs, env=hook_env, exit_code=0):
        previous = set(directory.glob("*.json"))
        result = _run(
            root, env, "git", "push", "origin", *refspecs, exit_code=exit_code
        )
        return _new_invocation(directory, result, previous), result

    binding, _ = push("main:main", "feature/topic:feature/topic", "refs/tags/candidate")
    prepared = _artifact(binding, ".plan")
    receipts = _artifact(binding, ".receipt")["receipts"]
    assert prepared["dagger_module"] is None
    assert {
        plan["git"]["head_ref"]: (plan["git"]["ref_action"], plan["git"]["head_commit"])
        for plan in prepared["plan"]
    } == {
        "refs/heads/main": ("update", main),
        "refs/heads/feature/topic": ("create", topic),
        "refs/tags/candidate": ("create", topic),
    }
    for plan, receipt in zip(prepared["plan"], receipts, strict=True):
        assert plan["git"]["base_commit"] == baseline
        assert plan["expected_checks"]["authority_status"] == "verified"
        assert plan["selection"]["tests"] == []
        assert receipt["status"] == "matched"
        assert receipt["expected_invocation_ref"] == binding.invocation_ref
        assert receipt["trusted"] is False and receipt["required"] is False
    assert git(remote, "rev-parse", "refs/heads/main", env=hook_env) == main
    assert git(remote, "rev-parse", "refs/heads/feature/topic", env=hook_env) == topic
    assert git(remote, "rev-parse", "refs/tags/candidate", env=hook_env) == tag

    binding, _ = push("rejected:feature/topic", exit_code=1)
    receipt = _artifact(binding, ".receipt")["receipts"][0]
    assert receipt["entries"][0]["binding_status"] == "insufficient"
    report = _artifact(binding, ".execution-0")["report"]
    assert report["checks"][0]["native_result"]["native_exit_code"] == 2
    assert git(remote, "rev-parse", "refs/heads/feature/topic", env=hook_env) == topic

    binding, _ = push("+main:feature/topic", ":refs/tags/candidate")
    plans = _artifact(binding, ".plan")["plan"]
    assert {plan["git"]["ref_action"] for plan in plans} == {"force_update", "delete"}
    deleted = next(plan for plan in plans if plan["git"]["ref_action"] == "delete")
    assert deleted["git"]["non_check_obligations"] == ["ref_deletion"]
    assert deleted["git"]["execution_tree"] is None
    assert [r["status"] for r in _artifact(binding, ".receipt")["receipts"]] == [
        "matched",
        "matched",
    ]
    assert git(remote, "rev-parse", "refs/heads/feature/topic", env=hook_env) == main
    assert not git(remote, "tag", "--list", "candidate", env=hook_env)

    excluded = {**hook_env, "LEFTHOOK_EXCLUDE": "reconcile-push"}
    binding, result = push("main:next", env=excluded, exit_code=1)
    assert "hook_final_result_missing" in result.stderr
    assert not binding.invocation_path.with_suffix(".receipt").exists()
    assert not git(remote, "branch", "--list", "next", env=hook_env)


def test_repository_profiles_keep_forwarding_advisory_nonblocking(tmp_path, hook_env):
    """Index/Push: substantive→re-export stays advisory, displays findings, exits 0."""
    root = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    _init(root, hook_env)
    cfg = selection.load_config(ROOT / "ci-selection.toml")
    for name in (
        "ci-selection.toml",
        "ci-hygiene.toml",
        "ci-effective-profile.json",
        ".github/zizmor.yml",
        *cfg.push.effective_profiles,
    ):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes().replace(b"\r\n", b"\n"))
    pkg = root / "src/pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_bytes(b"")
    (pkg / "other.py").write_bytes(b"VALUE = 1\n")
    (pkg / "sub.py").write_bytes(b"VALUE = 1\n")
    (root / "tests").mkdir()
    (root / "tests/test_example.py").write_bytes(
        b"def test_example():\n    assert True\n"
    )
    git(root, "add", "--all", env=hook_env)
    git(root, "commit", "-qm", "Baseline with substantive module", env=hook_env)
    baseline = git(root, "rev-parse", "HEAD", env=hook_env)
    git(root, "init", "--bare", "-q", "--template=", str(remote), env=hook_env)
    git(root, "remote", "add", "origin", str(remote), env=hook_env)
    git(root, "push", "-q", "origin", "main", env=hook_env)
    _run(root, hook_env, *LEFTHOOK, "install")
    directory = _invocations(root, hook_env)

    (pkg / "sub.py").write_bytes(b"VALUE = 2\n")
    git(root, "add", "src/pkg/sub.py", env=hook_env)
    ordinary = _run(root, hook_env, "git", "commit", "-m", "Ordinary substantive edit")
    ordinary_binding = _new_invocation(directory, ordinary)
    ordinary_receipt = _artifact(ordinary_binding, ".receipt")
    assert ordinary_receipt["status"] == "matched"
    assert (
        ordinary_receipt["trusted"] is False and ordinary_receipt["required"] is False
    )
    by_name = {entry["name"]: entry for entry in ordinary_receipt["entries"]}
    assert by_name["whitespace"]["binding_status"] == "matched"
    assert by_name["forwarding"]["binding_status"] == "matched"
    assert by_name["forwarding"]["native_status"] == "passed"

    (pkg / "sub.py").write_bytes(b"from .other import VALUE\n")
    git(root, "add", "src/pkg/sub.py", env=hook_env)
    advisory = _run(
        root, hook_env, "git", "commit", "-m", "Convert module to pure re-export"
    )
    advisory_text = _diagnostic(advisory)
    assert "src/pkg/sub.py" in advisory_text
    assert "pure re-export" in advisory_text
    assert "forwarding: advisory" in advisory_text
    adv_binding = _new_invocation(
        directory, advisory, [ordinary_binding.invocation_path]
    )
    adv_receipt = _artifact(adv_binding, ".receipt")
    assert adv_receipt["status"] == "matched"
    assert adv_receipt["trusted"] is False and adv_receipt["required"] is False
    adv_by = {entry["name"]: entry for entry in adv_receipt["entries"]}
    assert adv_by["forwarding"]["binding_status"] == "matched"
    assert adv_by["forwarding"]["native_status"] == "advisory"
    assert adv_by["forwarding"]["native_satisfaction"]["status"] == "satisfied"
    assert adv_by["forwarding"]["native_satisfaction"]["reason"] == "native_advisory"
    assert adv_by["forwarding"]["selected_results"][0]["status"] == "advisory"
    fwd_exec = _artifact(adv_binding, ".execution-check-forwarding")
    fwd_check = next(
        item for item in fwd_exec["report"]["checks"] if item["name"] == "forwarding"
    )
    assert fwd_check["status"] == "advisory"
    assert any("src/pkg/sub.py" in item for item in fwd_check["findings"])
    head = git(root, "rev-parse", "HEAD", env=hook_env)

    push = _run(root, hook_env, "git", "push", "origin", "main")
    push_text = _diagnostic(push)
    assert "src/pkg/sub.py" in push_text
    assert "pure re-export" in push_text
    push_binding = _new_invocation(
        directory, push, [ordinary_binding.invocation_path, adv_binding.invocation_path]
    )
    push_plan = _artifact(push_binding, ".plan")
    push_outcome = _artifact(push_binding, ".receipt")
    assert len(push_outcome["receipts"]) == 1
    push_receipt = push_outcome["receipts"][0]
    assert push_receipt["status"] == "matched"
    assert push_receipt["trusted"] is False and push_receipt["required"] is False
    assert push_plan["plan"][0]["git"]["base_commit"] == baseline
    assert push_plan["plan"][0]["git"]["head_commit"] == head
    push_by = {entry["name"]: entry for entry in push_receipt["entries"]}
    assert push_by["forwarding"]["binding_status"] == "matched"
    assert push_by["forwarding"]["native_status"] == "advisory"
    assert push_by["forwarding"]["native_satisfaction"]["reason"] == "native_advisory"
    assert push_by["forwarding"]["selected_results"][0]["status"] == "advisory"
    assert git(remote, "rev-parse", "refs/heads/main", env=hook_env) == head

    (pkg / "sub.py").write_bytes(b"VALUE = 3 \n")
    git(root, "add", "src/pkg/sub.py", env=hook_env)
    blocked = _run(
        root,
        hook_env,
        "git",
        "commit",
        "-m",
        "Trailing whitespace must still fail",
        exit_code=1,
    )
    blocked_binding = _new_invocation(
        directory,
        blocked,
        [
            ordinary_binding.invocation_path,
            adv_binding.invocation_path,
            push_binding.invocation_path,
        ],
    )
    blocked_receipt = _artifact(blocked_binding, ".receipt")
    blocked_by = {entry["name"]: entry for entry in blocked_receipt["entries"]}
    assert blocked_by["whitespace"]["binding_status"] == "insufficient"
    assert "whitespace" in _diagnostic(blocked)


def test_installed_lefthook_uses_pinned_dagger_module(tmp_path, hook_env):
    pin = os.environ.get("ECI_TEST_DAGGER_MODULE")
    if not pin:
        pytest.skip(
            "Set ECI_TEST_DAGGER_MODULE to a published ECI module commit to exercise Dagger"
        )

    root = tmp_path / "consumer"
    assert dagger_plan.module_reference(pin, root=root) == pin
    config = (
        (ROOT / "lefthook.yml")
        .read_text(encoding="utf-8")
        .replace("--dagger-module .", f"--dagger-module {pin}")
    )
    _init(root, hook_env, config=config)
    for relative in ("pyproject.toml", "uv.lock", "tests/test_example.py"):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(
            (ROOT / "tests/fixtures/python-app" / relative)
            .read_bytes()
            .replace(b"\r\n", b"\n")
        )
    (root / "dagger-module.toml").write_bytes(
        b'name = "consumer"\nsource = ".dagger"\nengineVersion = "v1.0.0-beta.16"\n'
    )
    (root / "dagger.toml").write_bytes(
        b'[modules.consumer]\nsource = "."\nentrypoint = true\n'
    )
    source = _source(root, "example", ("whitespace",))
    source.write_bytes(b"ANSWER = 42\n")
    _approve_profiles(
        root,
        'schema_version = 1\nframework = "pytest"\nstrict_modules = false\ncoverage_required = false\n'
        'escalation_files = ["pyproject.toml"]\n'
        'ignored_files = ["ci-selection.toml", "ci-effective-profile.json", "lefthook.yml", "dagger.toml", "dagger-module.toml", "uv.lock"]\n',
        [
            (
                "ci-effective-profile.json",
                "index",
                "index",
                "HEAD",
                ("pytest", "whitespace"),
            )
        ],
    )
    git(root, "add", "--all", env=hook_env)
    _run(root, hook_env, *LEFTHOOK, "install", "pre-commit")
    directory = _invocations(root, hook_env)

    def commit_case(message, *, git_exit, native_exit, whitespace, base_tree=None):
        previous = set(directory.glob("*.json"))
        commit = _run(
            root,
            hook_env,
            "git",
            "commit",
            "-m",
            message,
            exit_code=git_exit,
            timeout=300,
        )
        binding = _new_invocation(directory, commit, previous)
        prepared = _artifact(binding, ".plan")
        receipt = _artifact(binding, ".receipt")
        bundle = binding.invocation_path.with_suffix(".dagger")
        report = json.loads((bundle / "report.json").read_bytes())
        assert prepared["dagger_module"] == pin
        assert dagger_plan.source_bound(prepared["plan"], report)
        for key in ("run_ref", "attempt_ref"):
            assert report["execution_identity"][key] == prepared[key]
        native = next(
            check["native_result"]
            for check in report["checks"]
            if check["name"] == "whitespace"
        )
        assert (
            report["exit_code"]
            == int((bundle / "exit-code.txt").read_bytes())
            == native["native_exit_code"]
            == native_exit
        )
        comparison = next(
            ref
            for ref in native["input_refs"]
            if ref["kind"] == "git_whitespace_comparison"
        )
        assert comparison["target_tree"] == prepared["plan"]["git"]["index_tree"]
        if base_tree is not None:
            assert comparison["base_tree"] == base_tree
        assert receipt["expected_invocation_ref"] == binding.invocation_ref
        assert {
            entry["name"]: entry["binding_status"] for entry in receipt["entries"]
        } == {
            "pytest": "matched",
            "whitespace": whitespace,
        }

    commit_case(
        "First native Dagger check", git_exit=0, native_exit=0, whitespace="matched"
    )
    head = git(root, "rev-parse", "HEAD", env=hook_env)
    baseline = git(root, "rev-parse", "HEAD^{tree}", env=hook_env)
    source.write_bytes(b"ANSWER = 43 \n")
    test = root / "tests/test_example.py"
    with test.open("ab") as stream:
        stream.write(b"\n\ndef test_subtraction():\n    assert 2 - 1 == 1\n")
    git(root, "add", "src/example.py", "tests/test_example.py", env=hook_env)
    source.write_bytes(b"ANSWER = 43\n")
    commit_case(
        "Native Dagger rejects staged whitespace",
        git_exit=1,
        native_exit=2,
        whitespace="insufficient",
        base_tree=baseline,
    )
    assert git(root, "rev-parse", "HEAD", env=hook_env) == head
    assert source.read_bytes() == b"ANSWER = 43\n"


def test_pre_push_and_pre_commit_reconcile_deleted_paths(tmp_path, hook_env):
    root = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    _init(root, hook_env)
    checks = ("whitespace",)
    keep = _source(root, "keep", checks)
    keep.write_bytes(b"VALUE = 1\n")
    (root / "tach.toml").write_bytes(
        b'source_roots = ["src"]\n'
        + b"".join(
            f'[[modules]]\npath = "{name}"\ndepends_on = []\n'.encode()
            for name in (
                "keep",
                "obsoleted",
                "pure_deleted",
                "upstream_deleted",
                "deleted_upstream_mod",
            )
        )
    )
    mixed_deleted = root / "src/obsoleted.py"
    mixed_deleted.write_bytes(b"LEGACY = 1\n")
    pure_deleted = root / "src/pure_deleted.py"
    pure_deleted.write_bytes(b"GONE = 1\n")
    upstream_only = root / "src/upstream_deleted.py"
    upstream_only.write_bytes(b"UPSTREAM = 1\n")
    _approve_profiles(
        root,
        INDEX_SELECTION + "\n[push]\n"
        "select_tests = false\n"
        'effective_profiles = ["push-update.json", "push-force.json"]\n',
        [
            ("ci-effective-profile.json", "index", "index", "HEAD", checks),
            ("push-update.json", "push", "update", "refs/heads/*", checks),
            ("push-force.json", "push", "force_update", "refs/heads/*", checks),
        ],
    )
    git(root, "add", "--all", env=hook_env)
    git(root, "commit", "-qm", "baseline", env=hook_env)
    baseline = git(root, "rev-parse", "HEAD", env=hook_env)
    git(root, "init", "--bare", "-q", "--template=", str(remote), env=hook_env)
    git(root, "remote", "add", "origin", str(remote), env=hook_env)

    # Seed remote `feature` with an upstream-only file so force-pushing a
    # rebased history that never had it exercises deletions across force_update.
    git(root, "switch", "-qc", "feature", baseline, env=hook_env)
    remote_extra = root / "src/deleted_upstream_mod.py"
    remote_extra.write_bytes(b"EXTRA = 1\n")
    git(root, "add", "src/deleted_upstream_mod.py", env=hook_env)
    git(root, "commit", "-qm", "remote extra file", env=hook_env)
    git(root, "push", "-q", "origin", "main", "feature", env=hook_env)
    git(root, "switch", "-q", "main", env=hook_env)

    _run(root, hook_env, *LEFTHOOK, "install")
    directory = _invocations(root, hook_env)

    def push(*refspecs):
        previous = set(directory.glob("*.json"))
        result = _run(root, hook_env, "git", "push", "origin", *refspecs)
        return _new_invocation(directory, result, previous)

    # 1. Mixed commit & push (file deleted + existing file modified).
    git(root, "rm", "-q", "src/obsoleted.py", env=hook_env)
    keep.write_bytes(b"VALUE = 2\n")
    git(root, "add", "src/keep.py", env=hook_env)
    previous = set(directory.glob("*.json"))
    commit_mixed = _run(root, hook_env, "git", "commit", "-m", "delete and modify")
    index_binding = _new_invocation(directory, commit_mixed, previous)
    assert _artifact(index_binding, ".receipt")["status"] == "matched"
    push_mixed = push("main:main")
    mixed_receipt = _artifact(push_mixed, ".receipt")["receipts"][0]
    assert mixed_receipt["status"] == "matched"
    mixed_oid = git(root, "rev-parse", "HEAD", env=hook_env)
    assert git(remote, "rev-parse", "refs/heads/main", env=hook_env) == mixed_oid

    # 2. Pure-deletion commit & push (no modified/added live files).
    git(root, "rm", "-q", "src/pure_deleted.py", env=hook_env)
    previous = set(directory.glob("*.json"))
    commit_pure = _run(root, hook_env, "git", "commit", "-m", "pure deletion")
    pure_index = _new_invocation(directory, commit_pure, previous)
    assert _artifact(pure_index, ".receipt")["status"] == "matched"
    push_pure = push("main:main")
    pure_receipt = _artifact(push_pure, ".receipt")["receipts"][0]
    assert pure_receipt["status"] == "matched"
    pure_oid = git(root, "rev-parse", "HEAD", env=hook_env)
    assert git(remote, "rev-parse", "refs/heads/main", env=hook_env) == pure_oid

    # 3. Force-push replacing upstream branch that contains extra deleted files.
    git(root, "rm", "-q", "src/upstream_deleted.py", env=hook_env)
    keep.write_bytes(b"VALUE = 3\n")
    git(root, "add", "src/keep.py", env=hook_env)
    _run(root, hook_env, "git", "commit", "-m", "force update target")
    rebased_oid = git(root, "rev-parse", "HEAD", env=hook_env)
    push_force = push("+main:feature")
    force_plan = _artifact(push_force, ".plan")["plan"][0]
    assert force_plan["git"]["ref_action"] == "force_update"
    assert set(force_plan["git"]["deleted_paths"]) == {
        "src/deleted_upstream_mod.py",
        "src/obsoleted.py",
        "src/pure_deleted.py",
        "src/upstream_deleted.py",
    }
    force_receipt = _artifact(push_force, ".receipt")["receipts"][0]
    assert force_receipt["status"] == "matched"
    assert git(remote, "rev-parse", "refs/heads/feature", env=hook_env) == rebased_oid


def _create_test_venv(path, *pth_entries):
    venv.create(path, with_pip=False)
    if os.name == "nt":
        site_packages = path / "Lib" / "site-packages"
    else:
        candidates = sorted((path / "lib").glob("python*/site-packages"))
        assert candidates, f"site-packages missing under {path}"
        site_packages = candidates[0]
    if pth_entries:
        (site_packages / "engineering_ci_test.pth").write_text(
            "\n".join(str(entry) for entry in pth_entries) + "\n",
            encoding="utf-8",
        )
    return site_packages


def _clean_uv_env(hook_env, *, extra_path=None):
    env = {
        key: value
        for key, value in hook_env.items()
        if key.upper()
        not in {"UV_PROJECT", "UV_PROJECT_ENVIRONMENT", "PYTHONPATH", "VIRTUAL_ENV"}
    }
    if extra_path is not None:
        env["PATH"] = os.pathsep.join([str(extra_path), env.get("PATH", "")])
    return env


def test_lefthook_arguments_reuses_primary_venv_and_binds_linked_worktree_src(
    tmp_path, hook_env
):
    primary = tmp_path / "primary repo"
    linked = tmp_path / "linked worktree"
    _init(primary, hook_env)
    (primary / "src/engineering_ci/cli").mkdir(parents=True)
    (primary / "src/engineering_ci/__init__.py").write_bytes(b'MARKER = "primary"\n')
    (primary / "src/engineering_ci/cli/__init__.py").write_bytes(b"")
    git(primary, "add", "--all", env=hook_env)
    git(primary, "commit", "-qm", "baseline", env=hook_env)
    _create_test_venv(primary / ".venv", primary / "src")

    git(primary, "worktree", "add", "-qb", "linked", str(linked), "HEAD", env=hook_env)
    (linked / "src/engineering_ci/__init__.py").write_bytes(b'MARKER = "linked"\n')

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv_stub = bin_dir / "uv"
    uv_stub.write_bytes(
        b"#!/bin/sh\n"
        b'if [ "${1:-}" = "sync" ]; then\n'
        b'  if [ "${UV_STUB_FAIL_SYNC_CHECK:-}" = "1" ]; then\n'
        b'    printf "stub sync check failed (UV_PROJECT=%s)\\n" "${UV_PROJECT:-}" >&2\n'
        b"    exit 1\n"
        b"  fi\n"
        b"  exit 0\n"
        b"fi\n"
        b'printf "UV_PROJECT_ENVIRONMENT=%s\\n" "${UV_PROJECT_ENVIRONMENT:-}"\n'
        b'printf "PYTHONPATH=%s\\n" "${PYTHONPATH:-}"\n'
        b'if [ -n "${UV_PROJECT_ENVIRONMENT:-}" ] && [ -d "$UV_PROJECT_ENVIRONMENT" ]; then\n'
        b'  if [ -x "$UV_PROJECT_ENVIRONMENT/Scripts/python.exe" ]; then\n'
        b'    py="$UV_PROJECT_ENVIRONMENT/Scripts/python.exe"\n'
        b"  else\n"
        b'    py="$UV_PROJECT_ENVIRONMENT/bin/python"\n'
        b"  fi\n"
        b'  marker=$("$py" -P -c "import engineering_ci; print(engineering_ci.MARKER)" </dev/null)\n'
        b'  printf "MARKER=%s\\n" "$marker"\n'
        b"fi\n"
        b'if [ "$6" = "pre-push" ]; then\n'
        b'  printf "STDIN=%s\\n" "$(cat)"\n'
        b"fi\n"
    )
    uv_stub.chmod(0o755)
    base_env = _clean_uv_env(hook_env, extra_path=bin_dir)

    def invoke(cwd, env, *args, stdin_text=None):
        sh_bin = shutil.which("sh", path=env.get("PATH"))
        assert sh_bin, "POSIX sh executable not found in hook PATH"
        result = subprocess.run(
            [sh_bin, "scripts/lefthook-arguments.sh", *args],
            cwd=cwd,
            env=env,
            input=stdin_text,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=60,
        )
        assert result.returncode == 0, _diagnostic(result)
        return dict(
            line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
        )

    # 1. Fresh linked worktree without .venv defaults to primary/.venv, binds linked/src, preserves pre-push stdin.
    push_stdin = "refs/heads/main aaaa refs/heads/main bbbb\n"
    fresh = invoke(
        linked,
        base_env,
        "run",
        "pre-push",
        "origin",
        "https://example.invalid/repo.git",
        stdin_text=push_stdin,
    )
    assert (
        Path(fresh["UV_PROJECT_ENVIRONMENT"]).resolve() == (primary / ".venv").resolve()
    )
    assert Path(fresh["PYTHONPATH"]).resolve() == (linked / "src").resolve()
    assert fresh["MARKER"] == "linked"
    assert fresh["STDIN"].startswith("ECI-PRE-PUSH:")
    assert push_stdin.strip() in fresh["STDIN"]
    assert not (linked / ".venv").exists()

    # 2. A leftover linked/.venv (even populated) is NOT silently preferred over the default unified primary/.venv.
    _create_test_venv(linked / ".venv", primary / "src")
    stale_leftover = invoke(linked, base_env, "run", "pre-commit")
    assert (
        Path(stale_leftover["UV_PROJECT_ENVIRONMENT"]).resolve()
        == (primary / ".venv").resolve()
    )
    assert Path(stale_leftover["PYTHONPATH"]).resolve() == (linked / "src").resolve()
    assert stale_leftover["MARKER"] == "linked"

    # 3. Explicit UV_PROJECT_ENVIRONMENT=linked/.venv respects explicit isolated selection and binds linked/src.
    explicit_local = invoke(
        linked,
        {**base_env, "UV_PROJECT_ENVIRONMENT": str(linked / ".venv")},
        "run",
        "pre-commit",
    )
    assert (
        Path(explicit_local["UV_PROJECT_ENVIRONMENT"]).resolve()
        == (linked / ".venv").resolve()
    )
    assert Path(explicit_local["PYTHONPATH"]).resolve() == (linked / "src").resolve()
    assert explicit_local["MARKER"] == "linked"

    # 4. Explicit non-primary shared UV_PROJECT_ENVIRONMENT still binds linked/src independently.
    external_venv = tmp_path / "external shared venv"
    _create_test_venv(external_venv, primary / "src")
    explicit_external = invoke(
        linked,
        {**base_env, "UV_PROJECT_ENVIRONMENT": str(external_venv)},
        "run",
        "pre-commit",
    )
    assert (
        Path(explicit_external["UV_PROJECT_ENVIRONMENT"]).resolve()
        == external_venv.resolve()
    )
    assert Path(explicit_external["PYTHONPATH"]).resolve() == (linked / "src").resolve()
    assert explicit_external["MARKER"] == "linked"

    # 5. Explicit UV_PROJECT keeps UV_PROJECT_ENVIRONMENT unset while still binding linked/src independently.
    explicit_project = invoke(
        linked, {**base_env, "UV_PROJECT": str(primary)}, "run", "pre-commit"
    )
    assert explicit_project["UV_PROJECT_ENVIRONMENT"] == ""
    assert Path(explicit_project["PYTHONPATH"]).resolve() == (linked / "src").resolve()

    # 6. Pre-existing PYTHONPATH is preserved behind linked/src using platform path separator.
    extra_py = str((tmp_path / "extra_py").resolve())
    with_pypath = invoke(
        linked, {**base_env, "PYTHONPATH": extra_py}, "run", "pre-commit"
    )
    entries = with_pypath["PYTHONPATH"].split(os.pathsep)
    assert Path(entries[0]).resolve() == (linked / "src").resolve()
    assert Path(entries[1]).resolve() == Path(extra_py).resolve()
    assert with_pypath["MARKER"] == "linked"

    # 7. --separate-git-dir layout: strict registered_worktree stays False without core.worktree,
    #    while shared_git_common_dir proves the weaker common-dir relation for CLI diagnostics;
    #    the shell wrapper only auto-resolves primary/.venv once core.worktree is explicitly configured.
    sep_git = tmp_path / "separate.git"
    sep_primary = tmp_path / "separate primary"
    sep_linked = tmp_path / "separate linked"
    _run(
        tmp_path,
        hook_env,
        "git",
        "init",
        "-q",
        "-b",
        "main",
        "--template=",
        "--separate-git-dir",
        str(sep_git),
        str(sep_primary),
    )
    git(sep_primary, "config", "user.email", "hooks@example.invalid", env=hook_env)
    git(sep_primary, "config", "user.name", "Separate Git Dir", env=hook_env)
    _copy_lefthook_command(sep_primary)
    (sep_primary / "src/engineering_ci/cli").mkdir(parents=True)
    (sep_primary / "src/engineering_ci/__init__.py").write_bytes(
        b'MARKER = "sep-primary"\n'
    )
    (sep_primary / "src/engineering_ci/cli/__init__.py").write_bytes(b"")
    git(sep_primary, "add", "--all", env=hook_env)
    git(sep_primary, "commit", "-qm", "sep baseline", env=hook_env)
    _create_test_venv(sep_primary / ".venv", sep_primary / "src")
    git(
        sep_primary,
        "worktree",
        "add",
        "-qb",
        "sep-wt",
        str(sep_linked),
        "HEAD",
        env=hook_env,
    )
    (sep_linked / "src/engineering_ci/__init__.py").write_bytes(
        b'MARKER = "sep-linked"\n'
    )
    assert not git_source.registered_worktree(
        sep_primary.resolve(), sep_linked.resolve()
    )
    assert git_source.shared_git_common_dir(sep_primary.resolve(), sep_linked.resolve())
    assert git_source.registered_worktree(primary.resolve(), linked.resolve())
    # Without core.worktree, Git stores no back-pointer from sep.git to sep_primary,
    # so the wrapper does not guess a wrong parent .venv while still binding sep_linked/src.
    sep_unconfigured = invoke(sep_linked, base_env, "run", "pre-commit")
    assert sep_unconfigured["UV_PROJECT_ENVIRONMENT"] == ""
    assert (
        Path(sep_unconfigured["PYTHONPATH"]).resolve() == (sep_linked / "src").resolve()
    )
    # With core.worktree configured on sep.git, the primary checkout .venv resolves automatically.
    _run(
        tmp_path,
        hook_env,
        "git",
        f"--git-dir={sep_git}",
        "config",
        "core.worktree",
        str(sep_primary),
    )
    sep_out = invoke(sep_linked, base_env, "run", "pre-commit")
    assert (
        Path(sep_out["UV_PROJECT_ENVIRONMENT"]).resolve()
        == (sep_primary / ".venv").resolve()
    )
    assert Path(sep_out["PYTHONPATH"]).resolve() == (sep_linked / "src").resolve()
    assert sep_out["MARKER"] == "sep-linked"

    # 8. Bare repo linked worktree does not fabricate a parent .venv path.
    bare_repo = tmp_path / "bare.git"
    bare_wt = tmp_path / "bare linked"
    git(tmp_path, "init", "--bare", "-q", "-b", "main", str(bare_repo), env=hook_env)
    git(primary, "push", "-q", str(bare_repo), "main", env=hook_env)
    git(
        bare_repo,
        "worktree",
        "add",
        "-qb",
        "bare-wt",
        str(bare_wt),
        "main",
        env=hook_env,
    )
    bare_out = invoke(bare_wt, base_env, "run", "pre-commit")
    assert bare_out["UV_PROJECT_ENVIRONMENT"] == ""
    assert Path(bare_out["PYTHONPATH"]).resolve() == (bare_wt / "src").resolve()

    # 9. Consumer linked worktree (no src/engineering_ci/cli) defaults to primary/.venv without setting PYTHONPATH.
    consumer = tmp_path / "consumer primary"
    consumer_linked = tmp_path / "consumer linked"
    _init(consumer, hook_env)
    (consumer / "app.py").write_bytes(b"VALUE = 1\n")
    git(consumer, "add", "--all", env=hook_env)
    git(consumer, "commit", "-qm", "consumer baseline", env=hook_env)
    _create_test_venv(consumer / ".venv", primary / "src")
    git(
        consumer,
        "worktree",
        "add",
        "-qb",
        "consumer-wt",
        str(consumer_linked),
        "HEAD",
        env=hook_env,
    )
    consumer_out = invoke(consumer_linked, base_env, "run", "pre-commit")
    assert (
        Path(consumer_out["UV_PROJECT_ENVIRONMENT"]).resolve()
        == (consumer / ".venv").resolve()
    )
    assert consumer_out["PYTHONPATH"] == ""
    assert consumer_out["MARKER"] == "primary"

    # 10. Runtime check & diagnostic report the active project's uv.lock (respecting explicit UV_PROJECT).
    (linked / "pyproject.toml").write_text(
        "[project]\nname='linked-cwd'\n", encoding="utf-8"
    )
    (linked / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    managed_project = tmp_path / "explicit managed project"
    managed_project.mkdir()
    (managed_project / "pyproject.toml").write_text(
        "[project]\nname='managed-project'\n", encoding="utf-8"
    )
    (managed_project / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    sh_bin = shutil.which("sh", path=base_env.get("PATH"))
    assert sh_bin
    for env_override, expected_lock, expected_env in (
        ({}, linked / "uv.lock", primary / ".venv"),
        (
            {"UV_PROJECT": str(managed_project)},
            managed_project / "uv.lock",
            managed_project / ".venv",
        ),
        (
            {
                "UV_PROJECT": str(managed_project),
                "UV_PROJECT_ENVIRONMENT": str(external_venv),
            },
            managed_project / "uv.lock",
            external_venv,
        ),
    ):
        failed = subprocess.run(
            [sh_bin, "scripts/lefthook-arguments.sh", "run", "pre-commit"],
            cwd=linked,
            env={**base_env, **env_override, "UV_STUB_FAIL_SYNC_CHECK": "1"},
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=60,
        )
        assert failed.returncode == 1
        diag_line = next(
            line
            for line in failed.stderr.splitlines()
            if line.startswith("engineering-ci hook: ")
        )
        lock_and_env = diag_line.split("does not satisfy ", 1)[1]
        reported_lock, reported_env = lock_and_env.rsplit(": ", 1)
        assert Path(reported_lock).resolve() == expected_lock.resolve()
        assert Path(reported_env).resolve() == expected_env.resolve()


def test_linked_worktree_hooks_succeed_without_local_venv_or_uv_env(tmp_path, hook_env):
    """Joint native uv acceptance: shared venv, candidate Hook & CLI/Verify, and dependency drift."""
    root = tmp_path / "primary"
    linked = tmp_path / "linked candidate"
    remote = tmp_path / "remote.git"
    base_yml = (ROOT / "lefthook.yml").read_text(encoding="utf-8")
    fast_verify_yml = (
        base_yml.split("\nverify:\n", 1)[0]
        + "\nverify:\n  jobs:\n    - name: smoke\n      run: printf 'verify-ok\\n'\n"
    )
    _init(root, hook_env, config=fast_verify_yml)
    for relative in ("pyproject.toml", "uv.lock", ".python-version", "README.md"):
        (root / relative).write_bytes((ROOT / relative).read_bytes())
    shutil.copytree(
        ROOT / "src" / "engineering_ci",
        root / "src" / "engineering_ci",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    checks = ("whitespace",)
    (root / "ci-hygiene.toml").write_bytes(
        b'schema_version = 1\nchecks = ["whitespace"]\n'
    )
    _approve_profiles(
        root,
        INDEX_SELECTION
        + "\n[commit]\nselect_tests = false\n"
        + '\n[push]\nselect_tests = false\nnew_ref_baseline = "refs/heads/baseline"\n'
        + 'effective_profiles = ["push-topic.json"]\n',
        [
            ("ci-effective-profile.json", "index", "index", "HEAD", checks),
            ("push-topic.json", "push", "create", "refs/heads/topic", checks),
        ],
    )
    git(root, "add", "--all", env=hook_env)
    git(root, "commit", "-qm", "baseline", env=hook_env)
    git(root, "branch", "baseline", env=hook_env)
    git(root, "init", "--bare", "-q", "--template=", str(remote), env=hook_env)
    git(root, "remote", "add", "origin", str(remote), env=hook_env)

    # Sync a real native uv environment into primary/.venv (editable install pointing at primary/src).
    clean_env = _clean_uv_env(hook_env)
    sync_primary = subprocess.run(
        ["uv", "sync", "--locked", "--group", "dev", "--offline"],
        cwd=root,
        env=clean_env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
    )
    if sync_primary.returncode != 0:
        _run(root, clean_env, "uv", "sync", "--locked", "--group", "dev", timeout=120)
    _run(root, clean_env, *LEFTHOOK, "install")

    git(root, "worktree", "add", "-qb", "topic", str(linked), "baseline", env=clean_env)
    assert not (linked / ".venv").exists()

    # Make linked worktree's candidate CLI/Verify and Hook implementation distinguishable from primary.
    verify_py = linked / "src" / "engineering_ci" / "cli" / "verify.py"
    verify_text = verify_py.read_text(encoding="utf-8")
    verify_anchor = "Measure an explicit Lefthook verify invocation"
    assert verify_anchor in verify_text
    verify_exec_anchor = "def _verify(out):\n"
    assert verify_exec_anchor in verify_text
    verify_py.write_text(
        verify_text.replace(
            verify_anchor,
            f"CANDIDATE_VERIFY_LINKED_MARKER: {verify_anchor}",
            1,
        ).replace(
            verify_exec_anchor,
            verify_exec_anchor
            + '    print(f"CANDIDATE_VERIFY_SOURCE={Path(__file__).resolve()}", file=sys.stderr)\n',
            1,
        ),
        encoding="utf-8",
        newline="\n",
    )
    assert "CANDIDATE_VERIFY_LINKED_MARKER" in verify_py.read_text(encoding="utf-8")

    hook_checks_py = linked / "src" / "engineering_ci" / "hook_checks.py"
    hc_text = hook_checks_py.read_text(encoding="utf-8")
    marker_anchor = "def print_result(outcome, invocation_ref):\n"
    assert marker_anchor in hc_text
    hook_checks_py.write_text(
        hc_text.replace(
            marker_anchor,
            marker_anchor
            + '    print(f"CANDIDATE_HOOK_SOURCE={Path(__file__).resolve()}", file=sys.stderr)\n',
            1,
        ),
        encoding="utf-8",
        newline="\n",
    )
    git(
        linked,
        "add",
        "src/engineering_ci/cli/verify.py",
        "src/engineering_ci/hook_checks.py",
        env=clean_env,
    )

    # 1. Real pre-commit & pre-push in linked worktree with NO local .venv and NO uv/PYTHONPATH overrides.
    expected_hook_source = str(hook_checks_py.resolve())
    commit = _run(linked, clean_env, "git", "commit", "-m", "candidate hook and verify")
    assert expected_hook_source in _diagnostic(commit)
    directory = _invocations(linked, clean_env)
    index = _new_invocation(directory, commit)
    assert _artifact(index, ".receipt")["status"] == "matched"
    assert not (linked / ".venv").exists()

    push = _run(linked, clean_env, "git", "push", "origin", "topic:topic")
    assert expected_hook_source in _diagnostic(push)
    binding = _new_invocation(directory, push, [index.invocation_path])
    assert _artifact(binding, ".receipt")["receipts"][0]["status"] == "matched"
    assert not (linked / ".venv").exists()

    # 2. Direct CLI / Verify against the SAME native shared primary/.venv (including cross-version).
    shared_only_env = {**clean_env, "UV_PROJECT_ENVIRONMENT": str(root / ".venv")}
    # Without PYTHONPATH, editable CLI from primary/.venv diagnoses the registered worktree mismatch.
    refused_cli = _run(
        linked,
        shared_only_env,
        "uv",
        "run",
        "--locked",
        "--no-sync",
        "engineering-ci",
        "verify",
        "--help",
        exit_code=2,
    )
    assert "is registered worktree" in refused_cli.stderr
    assert "refusing to execute another checkout's CLI" in refused_cli.stderr
    assert "PYTHONPATH=" in refused_cli.stderr

    # Distinct diagnostic wording when two checkouts share --git-common-dir without worktree registration.
    sep_git = tmp_path / "cli_sep.git"
    sep_primary = tmp_path / "cli_sep_primary"
    sep_linked = tmp_path / "cli_sep_linked"
    _run(
        tmp_path,
        hook_env,
        "git",
        "init",
        "-q",
        "-b",
        "main",
        "--template=",
        "--separate-git-dir",
        str(sep_git),
        str(sep_primary),
    )
    git(sep_primary, "config", "user.email", "hooks@example.invalid", env=hook_env)
    git(sep_primary, "config", "user.name", "Sep Guard Test", env=hook_env)
    (sep_primary / "src/engineering_ci/cli").mkdir(parents=True)
    (sep_primary / "src/engineering_ci/cli/__init__.py").write_bytes(b"")
    git(sep_primary, "add", "--all", env=hook_env)
    git(sep_primary, "commit", "-qm", "sep", env=hook_env)
    git(
        sep_primary,
        "worktree",
        "add",
        "-qb",
        "sep-wt",
        str(sep_linked),
        "HEAD",
        env=hook_env,
    )
    assert not git_source.registered_worktree(
        sep_primary.resolve(), sep_linked.resolve()
    )
    assert git_source.shared_git_common_dir(sep_primary.resolve(), sep_linked.resolve())
    guard_out = subprocess.run(
        [
            "python"
            if os.name != "nt"
            else str(root / ".venv" / "Scripts" / "python.exe"),
            "-P",
            "-c",
            (
                "import sys; from pathlib import Path; "
                "from engineering_ci import cli, git_source; "
                "p, l = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(); "
                "assert not git_source.registered_worktree(p, l); "
                "assert git_source.shared_git_common_dir(p, l); "
                "print('COMMON_DIR_PROVEN')"
            ),
            str(sep_primary),
            str(sep_linked),
        ],
        cwd=linked,
        env={**clean_env, "PYTHONPATH": str(linked / "src")},
        capture_output=True,
        text=True,
        check=True,
    )
    assert "COMMON_DIR_PROVEN" in guard_out.stdout
    # Directly verify cli._check_worktree_source_binding distinguishes shared_git_common_dir wording.
    orig_file = cli.__file__
    orig_cwd = Path.cwd()
    try:
        cli.__file__ = str(
            sep_primary / "src" / "engineering_ci" / "cli" / "__init__.py"
        )
        os.chdir(sep_linked)
        msg = cli._check_worktree_source_binding()
        assert msg is not None and "shares Git common directory" in msg
    finally:
        os.chdir(orig_cwd)
        cli.__file__ = orig_file

    # With PYTHONPATH bound to linked/src, both `engineering-ci` and `python -P -m engineering_ci.cli`
    # execute the actual linked candidate verify implementation and produce a real verify-timing.json receipt.
    shared_candidate_env = {**shared_only_env, "PYTHONPATH": str(linked / "src")}
    expected_verify_source = str(verify_py.resolve())
    verify_out_dir = linked / ".artifacts" / "verify-cli"
    direct_cli = _run(
        linked,
        shared_candidate_env,
        "uv",
        "run",
        "--locked",
        "--no-sync",
        "engineering-ci",
        "verify",
        "--out",
        str(verify_out_dir),
    )
    assert expected_verify_source in direct_cli.stderr
    receipt_file = Path(direct_cli.stdout.strip().splitlines()[-1])
    assert receipt_file.name == "verify-timing.json" and receipt_file.is_file()
    assert json.loads(receipt_file.read_text(encoding="utf-8"))["native_exit_code"] == 0

    direct_module = _run(
        linked,
        shared_candidate_env,
        "uv",
        "run",
        "--locked",
        "--no-sync",
        "python",
        "-P",
        "-m",
        "engineering_ci.cli",
        "verify",
        "--help",
    )
    assert "CANDIDATE_VERIFY_LINKED_MARKER" in direct_module.stdout

    # Cross-version evidence: simulate primary/.venv holding an older main CLI without the guard.
    # Without PYTHONPATH, the legacy editable CLI executes primary's code (guard is diagnostic-only for
    # versions containing it); WITH PYTHONPATH=linked/src, both CLI and module still execute linked's candidate.
    primary_cli_init = root / "src" / "engineering_ci" / "cli" / "__init__.py"
    saved_cli_init = primary_cli_init.read_bytes()
    try:
        primary_cli_init.write_text(
            'def main(argv=None):\n    print("LEGACY_PRIMARY_CLI")\n    return 0\n',
            encoding="utf-8",
            newline="\n",
        )
        legacy_without_pypath = _run(
            linked,
            shared_only_env,
            "uv",
            "run",
            "--locked",
            "--no-sync",
            "engineering-ci",
            "verify",
            "--help",
        )
        assert "LEGACY_PRIMARY_CLI" in legacy_without_pypath.stdout

        cross_version_cli = _run(
            linked,
            shared_candidate_env,
            "uv",
            "run",
            "--locked",
            "--no-sync",
            "engineering-ci",
            "verify",
            "--help",
        )
        assert "CANDIDATE_VERIFY_LINKED_MARKER" in cross_version_cli.stdout
    finally:
        primary_cli_init.write_bytes(saved_cli_init)

    # Primary checkout itself continues running primary's unmodified CLI.
    primary_cli = _run(
        root,
        clean_env,
        "uv",
        "run",
        "--locked",
        "--no-sync",
        "engineering-ci",
        "verify",
        "--help",
    )
    assert "CANDIDATE_VERIFY_LINKED_MARKER" not in primary_cli.stdout

    # 3. Missing shared environment fails closed with accurate --inexact diagnostics and creates no empty .venv.
    missing_env = tmp_path / "missing_shared_venv"
    blocked_missing = _run(
        linked,
        {**clean_env, "UV_PROJECT_ENVIRONMENT": str(missing_env)},
        "git",
        "commit",
        "--allow-empty",
        "-m",
        "must fail with missing env",
        exit_code=1,
    )
    assert "uv sync --locked --inexact --group dev" in blocked_missing.stderr
    assert "--no-install-project --inexact --group dev" in blocked_missing.stderr
    assert not missing_env.exists()

    # 4. Lifecycle dependency check & --inexact semantics on the SAME native primary/.venv:
    #    (a) Missing unused dev tool (`ruff`) does NOT block a README/whitespace-only commit;
    #    (b) Missing runtime dependency (`protobuf`) while top-level `import engineering_ci` still works
    #        IS blocked by native `uv sync --locked --check --no-install-project --no-dev --inexact --offline`;
    #    (c) Restoring with `uv sync --locked --no-install-project --inexact --group dev` preserves
    #        compatible superset packages AND primary/.venv's editable primary/src binding, then unblocks commit.
    primary_python = (
        root / ".venv" / "Scripts" / "python.exe"
        if os.name == "nt"
        else root / ".venv" / "bin" / "python"
    )
    _run(
        root,
        clean_env,
        "uv",
        "pip",
        "uninstall",
        "--python",
        str(primary_python),
        "ruff",
    )
    sample_note = linked / "README.md"
    sample_note.write_bytes(sample_note.read_bytes() + b"\nLinked worktree doc edit.\n")
    git(linked, "add", "README.md", env=clean_env)
    readme_commit = _run(
        linked, clean_env, "git", "commit", "-m", "readme allowed without ruff"
    )
    readme_binding = _new_invocation(
        directory, readme_commit, [index.invocation_path, binding.invocation_path]
    )
    assert _artifact(readme_binding, ".receipt")["status"] == "matched"
    assert not (linked / ".venv").exists()

    _run(
        root,
        clean_env,
        "uv",
        "pip",
        "uninstall",
        "--python",
        str(primary_python),
        "protobuf",
    )
    # Prove top-level `import engineering_ci` still succeeds when runtime `protobuf` is absent.
    _run(
        linked,
        clean_env,
        str(primary_python),
        "-I",
        "-c",
        "import engineering_ci",
    )
    sample_note.write_bytes(sample_note.read_bytes() + b"Needs runtime env.\n")
    git(linked, "add", "README.md", env=clean_env)
    blocked_runtime = _run(
        linked,
        clean_env,
        "git",
        "commit",
        "-m",
        "blocked by missing runtime dep",
        exit_code=1,
    )
    runtime_diag = _diagnostic(blocked_runtime)
    assert "protobuf" in runtime_diag
    assert "uv sync --locked --inexact --group dev" in runtime_diag
    assert "--no-install-project --inexact --group dev" in runtime_diag
    assert not (linked / ".venv").exists()

    # Drop a superset package marker into primary/.venv site-packages and restore via --no-install-project --inexact.
    site_packages = (
        root / ".venv" / "Lib" / "site-packages"
        if os.name == "nt"
        else next((root / ".venv" / "lib").glob("python*/site-packages"))
    )
    superset_dist = site_packages / "superset_branch_pkg-0.1.0.dist-info"
    superset_dist.mkdir()
    (superset_dist / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: superset-branch-pkg\nVersion: 0.1.0\n",
        encoding="utf-8",
    )
    (superset_dist / "RECORD").write_text("", encoding="utf-8")

    resync = subprocess.run(
        [
            "uv",
            "sync",
            "--locked",
            "--no-install-project",
            "--inexact",
            "--group",
            "dev",
            "--offline",
        ],
        cwd=linked,
        env=shared_only_env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
    )
    if resync.returncode != 0:
        _run(
            linked,
            shared_only_env,
            "uv",
            "sync",
            "--locked",
            "--no-install-project",
            "--inexact",
            "--group",
            "dev",
            timeout=120,
        )

    def _assert_shared_venv_intact():
        assert superset_dist.is_dir()
        editable_origin = _run(
            root,
            clean_env,
            str(primary_python),
            "-I",
            "-c",
            "import engineering_ci; print(engineering_ci.__file__)",
        )
        assert (
            Path(editable_origin.stdout.strip())
            .resolve()
            .is_relative_to((root / "src").resolve())
        )

    _assert_shared_venv_intact()

    # Documented non-hook commands from common-tasks.md / cli-reference.md in the linked worktree
    # run cleanly with `--no-sync` without overwriting primary/.venv's editable binding or superset deps.
    for rel in (".dagger/pyproject.toml", ".dagger/uv.lock", "scripts/release.py"):
        dest = linked / rel
        dest.parent.mkdir(exist_ok=True)
        dest.write_bytes((ROOT / rel).read_bytes())

    lefthook_ver = _run(
        linked,
        shared_only_env,
        "uv",
        "run",
        "--locked",
        "--no-sync",
        "lefthook",
        "version",
    )
    assert "2.2.2" in lefthook_ver.stdout
    refused_version = _run(
        linked,
        shared_only_env,
        "uv",
        "run",
        "--locked",
        "--no-sync",
        "engineering-ci",
        "version",
        "show",
        exit_code=2,
    )
    assert "refusing to execute another checkout's CLI" in refused_version.stderr
    cli_version = _run(
        linked,
        shared_candidate_env,
        "uv",
        "run",
        "--locked",
        "--no-sync",
        "engineering-ci",
        "version",
        "show",
    )
    module_version = _run(
        linked,
        shared_candidate_env,
        "uv",
        "run",
        "--locked",
        "--no-sync",
        "python",
        "-P",
        "-m",
        "engineering_ci.cli",
        "version",
        "show",
    )
    assert cli_version.stdout.strip()
    assert cli_version.stdout.strip() == module_version.stdout.strip()
    _run(
        linked,
        shared_only_env,
        "uv",
        "run",
        "--locked",
        "--no-sync",
        "python",
        "scripts/release.py",
        "--help",
    )
    _assert_shared_venv_intact()

    admitted_drift = _run(
        linked, clean_env, "git", "commit", "-m", "admitted after inexact uv sync"
    )
    assert expected_hook_source in _diagnostic(admitted_drift)
    drift_binding = _new_invocation(
        directory,
        admitted_drift,
        [
            index.invocation_path,
            binding.invocation_path,
            readme_binding.invocation_path,
        ],
    )
    assert _artifact(drift_binding, ".receipt")["status"] == "matched"
    assert not (linked / ".venv").exists()
