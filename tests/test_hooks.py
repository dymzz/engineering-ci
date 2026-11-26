import base64
import concurrent.futures
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import pytest

from engineering_ci import hooks

SECRET_URL = "https://user:token@example.com/org/repo.git?access_token=topsecret"


def git(root, *args, env=None):
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        env=env,
    ).stdout.strip()


def make_repo(root):
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "test")
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    git(root, "add", "a.txt")
    git(root, "commit", "-qm", "init")
    return root


def make_directory_link(link, target):
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except OSError:
        pass
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if result.returncode == 0:
            return
    pytest.skip("directory symlink/junction is unavailable")


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "test")
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    git(root, "add", "a.txt")
    git(root, "commit", "-qm", "init")
    return root


@pytest.mark.parametrize(
    ("staged", "working", "expected_exit"),
    [("staged \n", "working\n", 2), ("staged\n", "working \n", 0)],
)
def test_pre_commit_checks_captured_tree_with_partial_staging(
    repo, capsys, staged, working, expected_exit
):
    path = repo / "a.txt"
    path.write_text(staged, encoding="utf-8")
    git(repo, "add", "a.txt")
    staged_tree = git(repo, "write-tree")
    path.write_text(working, encoding="utf-8")

    assert hooks.main(["capture", "pre-commit", "--root", str(repo)]) == 0
    invocation_ref = capsys.readouterr().out.strip()
    exit_code = hooks.main(
        [
            "check",
            "pre-commit",
            "--root",
            str(repo),
            "--invocation-ref",
            invocation_ref,
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == report["exit_code"] == expected_exit
    assert report["check"]["exit_code"] == expected_exit
    assert report["source_status"] == "matched"
    assert report["invocation_ref"] == invocation_ref
    assert report["event"]["index"]["tree"] == staged_tree
    assert hooks.load_invocation(report["invocation_path"]) == report["event"]
    assert list(Path(report["invocation_path"]).parent.glob("*.json")) == [
        Path(report["invocation_path"])
    ]
    assert path.read_text(encoding="utf-8") == working


def test_pre_commit_rejects_capture_when_staging_changes_before_the_check(repo):
    path = repo / "a.txt"
    path.write_text("captured\n", encoding="utf-8")
    git(repo, "add", "a.txt")
    event = hooks.capture_pre_commit(repo)
    reference = hooks.persist_event(repo, event)
    path.write_text("new staging\n", encoding="utf-8")
    git(repo, "add", "a.txt")

    report = hooks.check_pre_commit(repo, invocation_ref=reference.invocation_ref)

    assert report["event"] == event
    assert report["exit_code"] == 1
    assert report["source_status"] == "stale"
    assert report["reason"] == "index_changed_during_check"


def test_pre_commit_does_not_recapture_an_unavailable_invocation(repo, capsys):
    invocation_ref = "invocation:" + "0" * 32

    exit_code = hooks.main(
        ["check", "pre-commit", "--root", str(repo), "--invocation-ref", invocation_ref]
    )

    assert exit_code == 2
    assert capsys.readouterr().err.strip() == "invocation_file_unavailable"
    assert not (repo / ".git" / "eci-hooks").exists()


def test_pre_commit_rejects_an_event_tree_different_from_the_actual_index(repo):
    (repo / "a.txt").write_text("staged whitespace \n", encoding="utf-8")
    git(repo, "add", "a.txt")
    event = hooks.capture_pre_commit(repo)
    event["index"]["tree"] = event["base_tree"]
    reference = hooks.persist_event(repo, event)

    report = hooks.check_pre_commit(repo, invocation_ref=reference.invocation_ref)

    assert report["check"]["exit_code"] == 0
    assert report["source_status"] == "stale"
    assert report["exit_code"] == 1


def test_pre_commit_uses_git_attributes_from_staged_tree(repo):
    attributes = repo / ".gitattributes"
    attributes.write_text("*.txt whitespace=-blank-at-eol\n", encoding="utf-8")
    (repo / "a.txt").write_text("staged \n", encoding="utf-8")
    git(repo, "add", ".gitattributes", "a.txt")
    attributes.write_text("*.txt whitespace=blank-at-eol\n", encoding="utf-8")

    report = hooks.check_pre_commit(repo)

    assert report["check"]["status"] == "passed"
    assert report["exit_code"] == 0


def test_pre_commit_treats_tree_oid_as_revision_when_a_path_has_the_same_name(repo):
    base_tree = git(repo, "rev-parse", "HEAD^{tree}")
    (repo / base_tree).touch()
    (repo / "a.txt").write_text("clean staged change\n", encoding="utf-8")
    git(repo, "add", "a.txt")

    report = hooks.check_pre_commit(repo)

    assert report["exit_code"] == 0, report["check"]
    assert report["check"]["status"] == "passed"


def test_pre_commit_checks_initial_index_against_empty_tree(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "a.txt").write_text("first \n", encoding="utf-8")
    git(tmp_path, "add", "a.txt")

    report = hooks.check_pre_commit(tmp_path)

    assert report["source_status"] == "matched"
    assert report["check"]["status"] == "failed"
    assert "trailing whitespace" in report["check"]["stdout"]


def test_pre_commit_blocks_index_change_even_after_clean_native_result(
    repo, monkeypatch
):
    path = repo / "a.txt"
    path.write_text("staged\n", encoding="utf-8")
    git(repo, "add", "a.txt")
    native_check = hooks.hygiene.check_git_whitespace

    def change_index_after_check(*args, **kwargs):
        result = native_check(*args, **kwargs)
        path.write_text("other staging\n", encoding="utf-8")
        git(repo, "add", "a.txt")
        return result

    monkeypatch.setattr(hooks.hygiene, "check_git_whitespace", change_index_after_check)
    report = hooks.check_pre_commit(repo)

    assert report["check"]["exit_code"] == 0
    assert report["source_status"] == "stale"
    assert report["exit_code"] != 0
    assert report["reason"] == "index_changed_during_check"


def write_remote_file(path, value):
    path.write_text(value, encoding="utf-8")
    with suppress(OSError):
        os.chmod(path, 0o600)
    return path


def pre_push_event(repo, *, name="origin", location="origin"):
    before = git(repo, "rev-parse", "HEAD")
    stdin_text = (
        f"refs/heads/main {before} refs/heads/main {'0' * 40}\n"
        f"refs/heads/topic {'f' * 40} refs/heads/topic {before}\n"
        f"refs/heads/gone {'0' * 40} refs/heads/gone {before}\n"
    )
    return hooks.capture_pre_push(repo, name, location, stdin_text)


def test_capture_pre_push_preserves_args_and_redacts_location(repo):
    event = pre_push_event(
        repo,
        location="https://user:secret@example.com/org/repo.git?token=topsecret",
    )
    remote = event["remote"]
    assert remote["name"] == {
        "kind": "alias",
        "value": "origin",
        "sha256": remote["name"]["sha256"],
        "credentials_redacted": False,
    }
    assert remote["location"]["kind"] == "url"
    assert remote["location"]["host"] == "example.com"
    assert remote["location"]["credentials_redacted"] is True
    assert "secret" not in json.dumps(event)
    assert "topsecret" not in json.dumps(event)
    assert remote["identity"].startswith("sha256:")
    assert [ref["ref_action"] for ref in event["refs"]] == [
        "create",
        "update",
        "delete",
    ]


def test_framed_pre_push_keeps_native_arguments_and_order(repo):
    remote_name = "origin"
    remote_location = "C:/a folder with ' quotes and $ signs/repo.git"
    oid = git(repo, "rev-parse", "HEAD")
    zero = "0" * 40
    text = (
        f"refs/heads/main {oid} refs/heads/main {zero}\n"
        f"refs/heads/topic {oid} refs/heads/topic {zero}\n"
    )
    payload = b":".join(
        (
            b"ECI-PRE-PUSH",
            base64.b64encode(remote_name.encode()),
            base64.b64encode(remote_location.encode()),
            text.encode(),
        )
    )
    args = hooks._read_pre_push_frame(io.BytesIO(payload))
    assert args == (remote_name, remote_location, text)
    event = hooks.capture_pre_push(repo, *args)
    assert [ref["local_ref"] for ref in event["refs"]] == [
        "refs/heads/main",
        "refs/heads/topic",
    ]
    with pytest.raises(hooks.HookEventError, match="pre_push_argument_frame_invalid"):
        hooks._read_pre_push_frame(io.BytesIO(text.encode()))


def test_capture_pre_push_redacts_credentialed_name_and_location(repo):
    event = hooks.capture_pre_push(
        repo,
        SECRET_URL,
        SECRET_URL,
        f"refs/heads/main {'a' * 40} refs/heads/main {'0' * 40}\n",
    )
    serialized = json.dumps(event)
    for secret in ("user", "token", "topsecret", "access_token", "org/repo"):
        assert secret not in serialized
    assert event["remote"]["name"]["kind"] == "url"
    assert event["remote"]["location"]["kind"] == "url"
    assert event["remote"]["name"]["credentials_redacted"] is True
    assert event["remote"]["location"]["credentials_redacted"] is True

    reference = hooks.persist_event(repo, event)
    assert event["remote"]["name"]["sha256"].startswith("sha256:")
    persisted = reference.event_path.read_bytes()
    for secret in ("user", "token", "topsecret", "access_token", "org/repo"):
        assert secret.encode() not in persisted


def test_capture_pre_push_redacts_scp_like_and_nonstandard_inputs(repo):
    event = hooks.capture_pre_push(
        repo,
        "gituser@example.com:org/repo.git",
        "ssh://deploy:secret@example.com/org/repo.git?access_token=hidden",
        f"refs/heads/main {'a' * 40} refs/heads/main {'0' * 40}\n",
    )
    serialized = json.dumps(event)
    for secret in (
        "gituser",
        "deploy",
        "secret",
        "access_token",
        "org/repo",
    ):
        assert secret not in serialized
    assert event["remote"]["name"]["kind"] == "scp"
    assert event["remote"]["name"]["credentials_redacted"] is True
    assert event["remote"]["location"]["kind"] == "url"
    assert event["remote"]["location"]["credentials_redacted"] is True


def test_capture_pre_push_accepts_sha256_zero_oid(repo):
    event = hooks.capture_pre_push(
        repo,
        "origin",
        "origin",
        f"refs/heads/main {'a' * 64} refs/heads/main {'0' * 64}\n",
    )
    assert event["refs"][0]["ref_action"] == "create"
    assert len(event["refs"][0]["after"]) == 64


def test_capture_pre_push_rejects_malformed_stdin(repo):
    with pytest.raises(hooks.HookEventError) as raised:
        hooks.capture_pre_push(repo, "origin", "origin", "not-a-tuple\n")
    assert raised.value.code == "pre_push_input_invalid"


def test_capture_pre_commit_uses_actual_index(repo, tmp_path):
    index = tmp_path / "alternate-index"
    env = {"GIT_INDEX_FILE": str(index)}
    subprocess.run(
        ["git", "read-tree", "HEAD"],
        cwd=repo,
        env={**os.environ, **env},
        check=True,
    )
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "a.txt"],
        cwd=repo,
        env={**os.environ, **env},
        check=True,
    )
    event = hooks.capture_pre_commit(repo, environ=env)
    assert event["hook"] == "pre-commit"
    assert event["schema_version"] == hooks.EVENT_SCHEMA_VERSION
    assert event["index"]["tree"] == git(repo, "write-tree", env={**os.environ, **env})
    assert event["index"]["sha256"].startswith("sha256:")
    assert event["index"]["locator"]["kind"] == "absolute"
    assert Path(event["index"]["locator"]["path"]).resolve() == index.resolve()
    assert hooks.resolve_index_path(event, root=repo) == index.resolve()


def test_pre_commit_keeps_index_locator_distinct_for_equal_bytes(tmp_path, monkeypatch):
    worktree = tmp_path / "work"
    worktree.mkdir()
    store = tmp_path / "repo.git"
    git(worktree, "init", "-q", "--separate-git-dir", str(store))
    (worktree / "a.txt").write_bytes(b"staged\n")
    git(worktree, "add", "a.txt")
    default = hooks.capture_pre_commit(worktree)
    reference = hooks.persist_event(worktree, default)
    assert default["index"]["locator"] == {"kind": "git_dir", "path": "index"}
    assert (
        hooks.resolve_index_path(default, root=worktree) == (store / "index").resolve()
    )

    alternate = worktree / "index"
    alternate.write_bytes((store / "index").read_bytes())
    monkeypatch.setenv("GIT_INDEX_FILE", str(alternate))
    explicit = hooks.capture_pre_commit(worktree)
    assert explicit["index"]["locator"] == {"kind": "worktree", "path": "index"}
    assert explicit["index"]["sha256"] == default["index"]["sha256"]
    assert explicit["index"]["tree"] == default["index"]["tree"]
    assert hooks.resolve_index_path(explicit, root=worktree) == alternate.resolve()
    result = hooks.check_pre_commit(worktree, invocation_ref=reference.invocation_ref)
    assert result["reason"] == "index_changed_during_check"
    assert result["exit_code"] != 0


def test_pre_commit_index_change_during_capture_is_refused(repo, monkeypatch):
    real_git = hooks._git
    changed = False

    def racing_git(root, *args, env=None):
        nonlocal changed
        result = real_git(root, *args, env=env)
        if args and args[0] == "write-tree" and not changed:
            changed = True
            (Path(root) / "raced.txt").write_text("raced\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "raced.txt"],
                cwd=root,
                check=True,
                env={**os.environ, **(env or {})},
            )
        return result

    monkeypatch.setattr(hooks, "_git", racing_git)
    with pytest.raises(hooks.HookEventError) as raised:
        hooks.capture_pre_commit(repo)
    assert raised.value.code == "index_changed_during_capture"


def test_persist_event_has_content_and_invocation_identity(repo):
    event = pre_push_event(repo)
    first = hooks.persist_event(repo, event)
    second = hooks.persist_event(repo, event)
    assert first.event_path == second.event_path
    assert first.invocation_ref != second.invocation_ref
    assert first.event_payload_ref == second.event_payload_ref
    assert first.event_path.stem == first.event_payload_ref.split(":", 1)[1]
    assert hashlib.sha256(first.event_path.read_bytes()).hexdigest() == (
        first.event_path.stem
    )
    assert hooks.load_event(first.event_path) == event
    assert first.invocation_path.is_file()
    assert second.invocation_path.is_file()
    invocation = json.loads(first.invocation_path.read_text(encoding="utf-8"))
    assert invocation["invocation_ref"] == first.invocation_ref
    assert invocation["event_payload_ref"] == first.event_payload_ref
    assert invocation["event_file"].endswith(first.event_path.name)
    assert not list(first.event_path.parent.glob("*.tmp"))


def test_persist_event_same_payload_concurrent_writers(repo):
    event = pre_push_event(repo)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        references = list(
            executor.map(lambda _: hooks.persist_event(repo, event), range(8))
        )
    assert len({reference.event_path for reference in references}) == 1
    assert len({reference.invocation_ref for reference in references}) == 8
    assert len({reference.event_payload_ref for reference in references}) == 1
    assert hooks.load_event(references[0].event_path) == event
    assert not list(references[0].event_path.parent.glob("*.tmp"))
    assert len(list(references[0].invocation_path.parent.glob("*.json"))) == 8


def test_load_event_rejects_noncanonical_or_tampered_payload(repo):
    reference = hooks.persist_event(repo, pre_push_event(repo))
    reference.event_path.write_bytes(reference.event_path.read_bytes() + b" ")
    with pytest.raises(hooks.HookEventError, match="event_payload_mismatch"):
        hooks.load_event(reference.event_path)


def test_capture_pre_push_redacts_irregular_scp_like_inputs(repo):
    value = "a@b@example.com:org/repo.git"
    event = hooks.capture_pre_push(
        repo,
        value,
        value,
        f"refs/heads/main {'a' * 40} refs/heads/main {'0' * 40}\n",
    )
    assert event["remote"]["name"]["kind"] == "opaque"
    assert event["remote"]["location"]["kind"] == "opaque"
    serialized = json.dumps(event)
    assert "b@example.com" not in serialized
    assert "org/repo" not in serialized

    malformed = "https://[::1"
    event = hooks.capture_pre_push(
        repo,
        malformed,
        malformed,
        f"refs/heads/main {'a' * 40} refs/heads/main {'0' * 40}\n",
    )
    assert event["remote"]["name"]["kind"] == "opaque"
    assert malformed not in json.dumps(event)


def test_load_event_rejects_filename_content_mismatch(repo):
    first = hooks.persist_event(repo, pre_push_event(repo))
    other_event = hooks.capture_pre_push(
        repo,
        "origin",
        "origin",
        f"refs/heads/other {'a' * 40} refs/heads/other {'0' * 40}\n",
    )
    other = hooks.persist_event(repo, other_event)
    first.event_path.write_bytes(other.event_path.read_bytes())
    with pytest.raises(hooks.HookEventError, match="event_payload_mismatch"):
        hooks.load_event(first.event_path)


def test_load_invocation_verifies_event_reference(repo):
    event = pre_push_event(repo)
    reference = hooks.persist_event(repo, event)
    assert hooks.load_invocation(reference.invocation_path) == event
    other_event = hooks.capture_pre_push(
        repo,
        "origin",
        "origin",
        f"refs/heads/other {'b' * 40} refs/heads/other {'0' * 40}\n",
    )
    other = hooks.persist_event(repo, other_event)
    invocation = json.loads(reference.invocation_path.read_text(encoding="utf-8"))
    invocation["event_payload_ref"] = other.event_payload_ref
    reference.invocation_path.write_bytes(hooks.canonical_event_bytes(invocation))
    with pytest.raises(hooks.HookEventError, match="invocation_event_mismatch"):
        hooks.load_invocation(reference.invocation_path)


def test_load_invocation_limits_event_to_same_repo_and_hook(repo, tmp_path):
    reference = hooks.persist_event(repo, pre_push_event(repo))
    original = json.loads(reference.invocation_path.read_text(encoding="utf-8"))
    other = make_repo(tmp_path / "other")
    other_reference = hooks.persist_event(other, pre_push_event(other))

    invocation = dict(original)
    invocation["event_file"] = str(other_reference.event_path)
    invocation["event_payload_ref"] = other_reference.event_payload_ref
    reference.invocation_path.write_bytes(hooks.canonical_event_bytes(invocation))
    with pytest.raises(hooks.HookEventError, match="invocation_event_outside_store"):
        hooks.load_invocation(reference.invocation_path)

    traversal = os.path.relpath(other_reference.event_path, repo / ".git")
    invocation = dict(original)
    invocation["event_file"] = traversal
    invocation["event_payload_ref"] = other_reference.event_payload_ref
    reference.invocation_path.write_bytes(hooks.canonical_event_bytes(invocation))
    with pytest.raises(hooks.HookEventError, match="invocation_event_outside_store"):
        hooks.load_invocation(reference.invocation_path)

    precommit = hooks.persist_event(repo, hooks.capture_pre_commit(repo))
    invocation = dict(original)
    invocation["event_file"] = precommit.event_path.relative_to(
        repo / ".git"
    ).as_posix()
    invocation["event_payload_ref"] = precommit.event_payload_ref
    reference.invocation_path.write_bytes(hooks.canonical_event_bytes(invocation))
    with pytest.raises(hooks.HookEventError, match="invocation_hook_mismatch"):
        hooks.load_invocation(reference.invocation_path)


def test_load_invocation_rejects_symlinked_event_directory(repo, tmp_path):
    other = make_repo(tmp_path / "other")
    event = pre_push_event(other)
    other_reference = hooks.persist_event(other, event)
    eci_dir = repo / ".git" / "eci-hooks"
    eci_dir.mkdir(parents=True, exist_ok=True)
    make_directory_link(eci_dir / "events", other_reference.event_path.parent)
    invocation_dir = eci_dir / "invocations"
    invocation_dir.mkdir()
    invocation_id = "a" * 32
    invocation = {
        "schema_version": hooks.INVOCATION_SCHEMA_VERSION,
        "kind": hooks.INVOCATION_KIND,
        "invocation_ref": f"invocation:{invocation_id}",
        "event_payload_ref": other_reference.event_payload_ref,
        "event_file": f"eci-hooks/events/{other_reference.event_path.name}",
        "hook": "pre-push",
    }
    invocation_path = invocation_dir / f"{invocation_id}.json"
    invocation_path.write_bytes(hooks.canonical_event_bytes(invocation))
    with pytest.raises(hooks.HookEventError, match="invocation_event_outside_store"):
        hooks.load_invocation(invocation_path)


def test_publish_and_prune_reject_symlinked_event_directory(repo, tmp_path):
    other = make_repo(tmp_path / "other")
    event = pre_push_event(other)
    other_reference = hooks.persist_event(other, event)
    eci_dir = repo / ".git" / "eci-hooks"
    eci_dir.mkdir(parents=True, exist_ok=True)
    make_directory_link(eci_dir / "events", other_reference.event_path.parent)
    original = other_reference.event_path.read_bytes()

    with pytest.raises(hooks.HookEventError, match="hook_storage_outside_repository"):
        hooks.persist_event(repo, event)
    assert other_reference.event_path.read_bytes() == original

    old = time.time() - hooks.EVENT_RETENTION_SECONDS - 60
    os.utime(other_reference.event_path, (old, old))
    with pytest.raises(hooks.HookEventError, match="hook_storage_outside_repository"):
        hooks.prune_events(repo)
    assert other_reference.event_path.exists()


def test_persist_event_cross_process_lock(repo, tmp_path):
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(pre_push_event(repo)), encoding="utf-8")
    start_file = tmp_path / "start"
    script = (
        "import json, sys, time\n"
        "from pathlib import Path\n"
        "from engineering_ci import hooks\n"
        "repo = Path(sys.argv[1])\n"
        "event = json.loads(Path(sys.argv[2]).read_text())\n"
        "start = Path(sys.argv[3])\n"
        "while not start.exists():\n"
        "    time.sleep(0.01)\n"
        "for _ in range(10):\n"
        "    hooks.persist_event(repo, event)\n"
    )
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
    }
    command = [
        sys.executable,
        "-c",
        script,
        str(repo),
        str(event_file),
        str(start_file),
    ]
    first = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
    )
    second = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
    )
    start_file.write_text("go", encoding="utf-8")
    first_out, first_err = first.communicate(timeout=120)
    second_out, second_err = second.communicate(timeout=120)
    assert first.returncode == 0, (first_out, first_err)
    assert second.returncode == 0, (second_out, second_err)
    events = list((repo / ".git" / "eci-hooks" / "events").glob("*.json"))
    invocations = list((repo / ".git" / "eci-hooks" / "invocations").glob("*.json"))
    assert len(events) == 1
    assert len(invocations) == 20
    for invocation in invocations:
        hooks.load_invocation(invocation)
    assert not list((repo / ".git" / "eci-hooks" / "events").glob("*.tmp"))


def test_capture_real_sha256_repository(tmp_path):
    root = tmp_path / "sha256"
    root.mkdir()
    initialized = subprocess.run(
        ["git", "init", "-q", "--object-format=sha256"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if initialized.returncode != 0:
        pytest.skip("Git does not support sha256 object format")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "test")
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    git(root, "add", "a.txt")
    git(root, "commit", "-qm", "init")
    before = git(root, "rev-parse", "HEAD")
    assert len(before) == 64
    zero = "0" * len(before)
    event = hooks.capture_pre_push(
        root,
        "origin",
        "origin",
        f"refs/heads/main {before} refs/heads/main {zero}\n",
    )
    assert event["refs"][0]["before"] == zero
    assert event["refs"][0]["ref_action"] == "create"
    assert len(event["refs"][0]["after"]) == 64
    precommit = hooks.capture_pre_commit(root)
    assert len(precommit["index"]["tree"]) == 64


def test_prune_keeps_event_referenced_by_current_invocation(repo):
    event = pre_push_event(repo)
    first = hooks.persist_event(repo, event)
    old = time.time() - hooks.EVENT_RETENTION_SECONDS - 60
    os.utime(first.event_path, (old, old))
    second = hooks.persist_event(repo, event)
    os.utime(second.event_path, (old, old))
    removed = hooks.prune_events(repo)
    assert removed == 0
    assert second.event_path.exists()
    assert first.invocation_path.exists()
    assert second.invocation_path.exists()

    for path in (
        first.invocation_path,
        second.invocation_path,
        second.event_path,
    ):
        os.utime(path, (old, old))
    removed = hooks.prune_events(repo)
    assert removed == 3
    assert not second.event_path.exists()


def test_prune_events_uses_explicit_retention_boundary(repo):
    reference = hooks.persist_event(repo, pre_push_event(repo))
    old = time.time() - hooks.EVENT_RETENTION_SECONDS - 60
    os.utime(reference.event_path, (old, old))
    os.utime(reference.invocation_path, (old, old))
    removed = hooks.prune_events(repo)
    assert removed == 2
    assert not reference.event_path.exists()
    assert not reference.invocation_path.exists()


def test_cli_capture_redacts_remote_files_and_emits_invocation(repo, tmp_path):
    stdin_file = tmp_path / "stdin.txt"
    stdin_file.write_text(
        f"refs/heads/main {'a' * 40} refs/heads/main {'0' * 40}\n",
        encoding="utf-8",
    )
    name_file = write_remote_file(tmp_path / "remote-name.txt", SECRET_URL)
    location_file = write_remote_file(tmp_path / "remote-location.txt", SECRET_URL)
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "engineering_ci.cli",
            "hooks",
            "capture",
            "pre-push",
            "--root",
            str(repo),
            "--remote-name-file",
            str(name_file),
            "--remote-location-file",
            str(location_file),
            "--stdin-file",
            str(stdin_file),
            "--json",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    for secret in ("user", "token", "topsecret", "access_token"):
        assert secret not in result.stdout
    payload = json.loads(result.stdout)
    assert payload["event"]["remote"]["name"]["kind"] == "url"
    assert payload["event"]["refs"][0]["ref_action"] == "create"
    assert payload["event_payload_ref"].startswith("sha256:")
    assert payload["invocation_ref"].startswith("invocation:")
    event_path = Path(payload["path"])
    invocation_path = Path(payload["invocation_path"])
    assert event_path.is_file()
    assert invocation_path.is_file()
    for secret in ("user", "token", "topsecret", "access_token"):
        assert secret.encode() not in event_path.read_bytes()
    assert name_file.exists()
    assert location_file.exists()
    assert hooks.load_invocation(invocation_path)["remote"]["name"]["kind"] == "url"


def test_cli_default_handoff_identifies_each_invocation(repo, tmp_path):
    stdin_file = tmp_path / "stdin.txt"
    stdin_file.write_text(
        f"refs/heads/main {'a' * 40} refs/heads/main {'0' * 40}\n",
        encoding="utf-8",
    )
    name_file = write_remote_file(tmp_path / "remote-name.txt", "origin")
    location_file = write_remote_file(tmp_path / "remote-location.txt", "origin")
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    command = [
        sys.executable,
        "-m",
        "engineering_ci.cli",
        "hooks",
        "capture",
        "pre-push",
        "--root",
        str(repo),
        "--remote-name-file",
        str(name_file),
        "--remote-location-file",
        str(location_file),
        "--stdin-file",
        str(stdin_file),
    ]
    first = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", env=env, check=False
    )
    second = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", env=env, check=False
    )
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    first_ref = first.stdout.strip()
    second_ref = second.stdout.strip()
    assert first_ref.startswith("invocation:")
    assert second_ref.startswith("invocation:")
    assert first_ref != second_ref
    assert name_file.exists() and location_file.exists()


def _git_sh_env():
    repo_root = Path(__file__).parents[1].resolve()
    env = {
        **os.environ,
        "UV_PROJECT": str(repo_root),
        "PYTHONPATH": str(repo_root / "src"),
    }
    git_bin = shutil.which("git")
    if git_bin:
        git_root = Path(git_bin).resolve().parents[1]
        extra = [
            str(Path(sys.executable).resolve().parent),
            str(git_root / "bin"),
            str(git_root / "usr" / "bin"),
        ]
        env["PATH"] = os.pathsep.join([*extra, env.get("PATH", "")])
    return env


def _copy_lefthook_command(root):
    path = Path("scripts/lefthook-arguments.sh")
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).resolve().parents[1] / path, target)


def test_lefthook_installed_pre_commit_passes_no_stage_fixed_and_isolates_index(
    tmp_path,
):
    repo_root = Path(__file__).parents[1].resolve()
    env = _git_sh_env()
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "hooks@example.invalid")
    git(tmp_path, "config", "user.name", "Hooks Test")
    (tmp_path / "lefthook.yml").write_bytes((repo_root / "lefthook.yml").read_bytes())
    _copy_lefthook_command(tmp_path)

    validate = subprocess.run(
        ["uv", "run", "--locked", "--no-sync", "lefthook", "validate"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert validate.returncode == 0, validate.stderr

    dumped = subprocess.run(
        ["uv", "run", "--locked", "--no-sync", "lefthook", "dump"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert dumped.returncode == 0, dumped.stderr
    assert "--no-stage-fixed" in dumped.stdout
    assert "pre-commit-checks" in dumped.stdout

    install = subprocess.run(
        ["uv", "run", "--locked", "--no-sync", "lefthook", "install", "pre-commit"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert install.returncode == 0, install.stderr
    hook_file = tmp_path / ".git" / "hooks" / "pre-commit"
    hook_text = hook_file.read_text(encoding="utf-8")
    assert 'sh scripts/lefthook-arguments.sh "$@"' in hook_text
    assert 'call_lefthook run "pre-commit" "$@"' in hook_text

    target = tmp_path / "a.txt"
    target.write_text("staged \n", encoding="utf-8")
    git(tmp_path, "add", "a.txt")
    target.write_text("working\n", encoding="utf-8")

    blocked = subprocess.run(
        ["git", "commit", "-m", "first commit blocked by staged whitespace"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    blocked_diag = f"stdout:\n{blocked.stdout}\nstderr:\n{blocked.stderr}"
    assert blocked.returncode != 0, blocked_diag
    assert target.read_text(encoding="utf-8") == "working\n", blocked_diag
    inv_dir = tmp_path / ".git" / "eci-hooks" / "invocations"
    invocations = sorted(inv_dir.glob("*.json"))
    plans = sorted(inv_dir.glob("*.plan"))
    activations = sorted(inv_dir.glob("*.activation"))
    executions = sorted(inv_dir.glob("*.execution"))
    receipts = sorted(inv_dir.glob("*.receipt"))
    assert len(invocations) == 1, blocked_diag
    assert len(plans) == 1, blocked_diag
    assert len(activations) == 1, blocked_diag
    assert len(executions) == 1, blocked_diag
    assert len(receipts) == 1, blocked_diag
    assert (
        len(
            {
                invocations[0].stem,
                plans[0].stem,
                activations[0].stem,
                executions[0].stem,
                receipts[0].stem,
            }
        )
        == 1
    )
    assert isinstance(json.loads(plans[0].read_text(encoding="utf-8")), dict)
    assert isinstance(json.loads(activations[0].read_text(encoding="utf-8")), dict)
    execution_payload = json.loads(executions[0].read_text(encoding="utf-8"))
    assert isinstance(execution_payload, dict), execution_payload
    assert execution_payload["report"]["exit_code"] == 2, execution_payload
    assert execution_payload["report"]["check"]["exit_code"] == 2, execution_payload
    receipt_payload = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert isinstance(receipt_payload, dict), receipt_payload
    assert receipt_payload["exit_code"] == 2, f"{blocked_diag}\n{receipt_payload}"
    if isinstance(receipt_payload.get("receipt"), dict):
        assert receipt_payload["receipt"]["trusted"] is False
        assert receipt_payload["receipt"]["required"] is False
        assert receipt_payload["receipt"]["evidence_admission"] == "not_evaluated"
    lefthook_text = (Path(__file__).parents[1] / "lefthook.yml").read_text(
        encoding="utf-8"
    )
    assert "python -P -m engineering_ci.cli hooks" in lefthook_text
    assert "python -m engineering_ci.cli hooks" not in lefthook_text
    assert "uv run --locked --no-sync engineering-ci hooks" not in lefthook_text

    target.write_text("staged\n", encoding="utf-8")
    git(tmp_path, "add", "a.txt")
    staged_tree = git(tmp_path, "write-tree")
    target.write_text("working \n", encoding="utf-8")

    allowed = subprocess.run(
        ["git", "commit", "-m", "first commit allowed despite dirty worktree"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert allowed.returncode == 0, allowed.stderr + allowed.stdout
    assert target.read_text(encoding="utf-8") == "working \n"
    assert git(tmp_path, "rev-parse", "HEAD^{tree}") == staged_tree
    assert git(tmp_path, "show", "HEAD:a.txt") == "staged"


def test_lefthook_installs_pre_push_and_frames_the_original_git_arguments(
    tmp_path,
):
    repo_root = Path(__file__).parents[1].resolve()
    env = _git_sh_env()

    default_repo = tmp_path / "default_repo"
    default_repo.mkdir()
    git(default_repo, "init", "-q", "-b", "main")
    (default_repo / "lefthook.yml").write_bytes(
        (repo_root / "lefthook.yml").read_bytes()
    )
    _copy_lefthook_command(default_repo)
    install_default = subprocess.run(
        ["uv", "run", "--locked", "--no-sync", "lefthook", "install"],
        cwd=default_repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert install_default.returncode == 0, install_default.stderr
    assert (default_repo / ".git" / "hooks" / "pre-commit").is_file()
    assert (default_repo / ".git" / "hooks" / "pre-push").is_file()

    remote = tmp_path / "remote repo.git"
    subprocess.run(
        ["git", "init", "--bare", "-q", "-b", "main", str(remote)],
        env=env,
        check=True,
    )
    push_repo = tmp_path / "push_repo"
    push_repo.mkdir()
    git(push_repo, "init", "-q", "-b", "main")
    git(push_repo, "config", "user.email", "hooks@example.invalid")
    git(push_repo, "config", "user.name", "Hooks Test")
    repo_yml = (repo_root / "lefthook.yml").read_text(encoding="utf-8")
    enabled_yml = (
        repo_yml.split("\npre-push:\n", 1)[0]
        + "\npre-push:\n  jobs:\n    - name: capture-push\n      use_stdin: true\n"
        + "      run: uv run --locked --no-sync python -P -m engineering_ci.cli hooks capture pre-push --framed-stdin --json\n"
    )
    (push_repo / "lefthook.yml").write_text(enabled_yml, encoding="utf-8")
    _copy_lefthook_command(push_repo)
    (push_repo / "a.txt").write_text("hello\n", encoding="utf-8")
    git(push_repo, "add", "-A")
    git(push_repo, "commit", "-q", "-m", "init")
    install_push = subprocess.run(
        ["uv", "run", "--locked", "--no-sync", "lefthook", "install", "pre-push"],
        cwd=push_repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert install_push.returncode == 0, install_push.stderr
    hook_file = push_repo / ".git" / "hooks" / "pre-push"
    assert hook_file.is_file()
    assert 'sh scripts/lefthook-arguments.sh "$@"' in hook_file.read_text(
        encoding="utf-8"
    )

    pushed = subprocess.run(
        ["git", "push", str(remote), "main"],
        cwd=push_repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert pushed.returncode == 0, pushed.stdout + pushed.stderr
    invocations = sorted(
        (push_repo / ".git" / "eci-hooks" / "invocations").glob("*.json")
    )
    assert len(invocations) == 1, pushed.stdout + pushed.stderr
    event = hooks.load_invocation_binding(invocations[0]).event
    assert event["hook"] == "pre-push"
    assert [ref["remote_ref"] for ref in event["refs"]] == ["refs/heads/main"]
    assert event["remote"]["location"]["kind"] == "path"
