import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from engineering_ci import hooks


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


def test_capture_pre_push_preserves_argv_stdin_and_redacts_credentials(repo):
    before = git(repo, "rev-parse", "HEAD")
    stdin_text = (
        f"refs/heads/main {before} refs/heads/main {'0' * 40}\n"
        f"refs/heads/topic {'f' * 40} refs/heads/topic {before}\n"
        f"refs/heads/gone {'0' * 40} refs/heads/gone {before}\n"
    )
    event = hooks.capture_pre_push(
        repo,
        "origin",
        "https://user:secret@example.com/org/repo.git?token=topsecret",
        stdin_text,
    )
    remote = event["remote"]
    assert remote["name"] == "origin"
    assert "secret" not in json.dumps(event)
    assert "topsecret" not in json.dumps(event)
    assert remote["location"]["kind"] == "url"
    assert remote["location"]["credentials_redacted"] is True
    assert remote["location"]["sha256"].startswith("sha256:")
    assert [ref["ref_action"] for ref in event["refs"]] == [
        "create",
        "update",
        "delete",
    ]
    assert event["refs"][0]["before"] == "0" * 40
    assert event["refs"][2]["after"] == "0" * 40


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
    assert event["index"]["tree"] == git(
        repo, "write-tree", env={**__import__("os").environ, **env}
    )
    assert event["index"]["sha256"].startswith("sha256:")
    assert "alternate-index" in event["index"]["file"]


def test_persist_event_is_content_addressed(repo):
    event = hooks.capture_pre_push(
        repo,
        "origin",
        "origin",
        f"refs/heads/main {'a' * 40} refs/heads/main {'b' * 40}\n",
    )
    first = hooks.persist_event(repo, event)
    second = hooks.persist_event(repo, event)
    assert first == second
    assert first.parent.name == "events"
    assert first.parent.parent.name == "eci-hooks"
    assert json.loads(first.read_text(encoding="utf-8")) == event


def test_cli_capture_writes_content_addressed_event(repo, tmp_path):
    stdin_file = tmp_path / "stdin.txt"
    stdin_file.write_text(
        f"refs/heads/main {'a' * 40} refs/heads/main {'0' * 40}\n",
        encoding="utf-8",
    )
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
            "--remote-name",
            "origin",
            "--remote-location",
            "https://example.com/org/repo.git",
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
    payload = json.loads(result.stdout)
    assert payload["event"]["remote"]["location"]["kind"] == "url"
    assert payload["event"]["refs"][0]["ref_action"] == "create"
    assert Path(payload["path"]).is_file()
