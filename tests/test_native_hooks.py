"""Exercise ECI through Lefthook installed in isolated Git repositories."""

import json
import os
import subprocess
from pathlib import Path

import pytest
from test_hooks import _git_sh_env, git
from test_reconciliation import _attach_effective_profile_policy

from engineering_ci import check_contract, dagger_plan, expected_checks, hooks

ROOT = Path(__file__).resolve().parents[1]
LEFTHOOK = ("uv", "run", "--locked", "--no-sync", "lefthook")
INDEX_SELECTION = (
    'schema_version = 1\nchecks = ["hygiene"]\nstrict_modules = false\n'
    'ignored_files = ["ci-selection.toml", "ci-hygiene.toml", '
    '"ci-effective-profile.json", "tach.toml", "lefthook.yml"]\n'
)
PRE_PUSH = """
pre-push:
  jobs:
    - name: capture-refs
      use_stdin: true
      run: |
        invocation=$(uv run --locked --no-sync python -P -m engineering_ci.cli hooks capture pre-push --framed-stdin) || exit $?
        ECI_HOOK_INVOCATION_REF="$invocation" uv run --locked --no-sync lefthook run pre-push-checks --no-auto-install || exit $?
        uv run --locked --no-sync python -P -m engineering_ci.cli hooks consume pre-push --invocation-ref "$invocation"
"""


@pytest.fixture
def hook_env():
    env = _git_sh_env()
    env.pop("LEFTHOOK", None)
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
    git(root, "add", "--all", env=hook_env)
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
    config = (ROOT / "lefthook.yml").read_text(encoding="utf-8") + PRE_PUSH
    _init(root, hook_env, config=config)
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
