"""Exercise ECI through Lefthook installed in isolated Git repositories."""

import json
import os
import subprocess
from pathlib import Path

import pytest
from test_hooks import _copy_lefthook_command, _git_sh_env, git
from test_reconciliation import _attach_effective_profile_policy

from engineering_ci import (
    check_contract,
    dagger_plan,
    expected_checks,
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
    for name in ("ci-selection.toml", "ci-hygiene.toml", *cfg.push.effective_profiles):
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
