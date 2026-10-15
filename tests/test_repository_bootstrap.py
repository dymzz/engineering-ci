"""Consumer creation uses real local Git and substituted GitHub/network commands."""

import ast
import json
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

from engineering_ci.tools import bootstrap
from engineering_ci.tools import bootstrap_templates as template


@pytest.fixture
def destination(tmp_path, monkeypatch):
    config = tmp_path / "gitconfig"
    config.write_text(
        "[user]\nname = Bootstrap Test\nemail = bootstrap@example.invalid\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in bootstrap.GIT_SELECTORS:
        monkeypatch.delenv(name, raising=False)
    return tmp_path / "sample-app"


def invoke(destination, capsys, *extra, **kwargs):
    args = ["--repo", "dymzz/sample-app", "--path", str(destination), "--yes", *extra]
    code = bootstrap.main(args, **kwargs)
    return code, json.loads(capsys.readouterr().out)


def git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_plan_is_offline_and_does_not_create_directory(destination, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("A plan must not invoke any tool")

    code, result = invoke(destination, capsys, "--plan", "--github", run=forbidden)
    assert code == 0 and result["status"] == "planned"
    assert result["checks"] == "not_run" and result["pull_request"] == "not_created"
    assert not destination.exists()


def test_cancel_has_no_files_or_processes(destination, capsys):
    code = bootstrap.main(
        ["--repo", "dymzz/sample-app", "--path", str(destination)],
        reader=lambda prompt: "cancel",
    )
    assert code == 0 and json.loads(capsys.readouterr().out)["status"] == "cancelled"
    assert not destination.exists()


def test_interactive_inputs_have_a_final_reviewable_confirmation(
    destination, monkeypatch, capsys
):
    monkeypatch.setattr(bootstrap.sys.stdin, "isatty", lambda: True)
    answers = iter(["dymzz/sample-app", str(destination), "no", "create"])
    prompts = []

    def reader(prompt):
        prompts.append(prompt)
        return next(answers)

    assert bootstrap.main([], reader=reader) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(prompts) == 4 and result["remote"] == "not_requested"
    assert git(destination, "branch", "--show-current") == template.BRANCH


def test_real_git_scaffold_commits_only_seed_readme(destination, capsys):
    code, result = invoke(destination, capsys)
    assert code == 0 and result["status"] == "generated"
    assert git(destination, "ls-tree", "--name-only", "HEAD") == "README.md"
    assert git(destination, "rev-parse", "HEAD") == result["context"]["seed_sha"]
    assert git(destination, "branch", "--show-current") == template.BRANCH
    assert git(destination, "remote") == ""
    assert git(destination, "diff", "--cached", "--name-only") == ""
    assert result["checks"] == "not_run"
    assert result["next_actions"][0]["argv"][-1] == "--github"
    assert all(action["argv"][-1] != "merge" for action in result["next_actions"])
    for relative in result["files"]:
        content = (destination / relative).read_text(encoding="utf-8")
        if relative.endswith(".py"):
            ast.parse(content)
        elif relative.endswith(".toml"):
            tomllib.loads(content)
    manifest = json.loads((destination / "dagger.json").read_text())
    workflow = yaml.load(
        (destination / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert manifest["dependencies"][0]["pin"] == template.SHA
    assert workflow["jobs"]["python"]["uses"].endswith("@" + template.SHA)
    assert (
        workflow["jobs"]["python"]["with"]["pytest_check"]
        == manifest["name"] + ":tests"
    )
    assert template.SHA in (
        Path(__file__).resolve().parents[1] / "guides/new-repository.md"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "repo",
    [
        "x/../escape",
        "https://host/a",
        "x/sample;whoami",
        "x/CON",
        "x/upperCase",
        "-x/sample",
    ],
)
def test_invalid_names_are_rejected_without_creating_target(destination, capsys, repo):
    assert bootstrap.main(["--repo=" + repo, "--path", str(destination), "--yes"]) == 3
    capsys.readouterr()
    assert not destination.exists()


def test_existing_directory_is_not_adopted(destination, capsys):
    destination.mkdir()
    sentinel = destination / "important.txt"
    sentinel.write_text("keep", encoding="utf-8")
    code, _ = invoke(destination, capsys)
    assert code == 3 and list(destination.iterdir()) == [sentinel]
    assert sentinel.read_text() == "keep"


def test_explicit_resume_preserves_user_changes(destination, capsys):
    assert invoke(destination, capsys)[0] == 0
    (destination / "app.py").write_text("# user change\n", encoding="utf-8")
    seed = git(destination, "rev-parse", "main")
    assert bootstrap.main(["--resume", str(destination), "--yes"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["preserved_changes"] == ["app.py"]
    assert (destination / "app.py").read_text() == "# user change\n"
    assert git(destination, "rev-parse", "main") == seed


def test_commit_failure_retains_native_exit_and_can_resume(destination, capsys):
    def runner(args, **kwargs):
        if args[:2] == ["git", "commit"]:
            return subprocess.CompletedProcess(
                args, 17, "", "private-token-should-not-leak"
            )
        return subprocess.run(args, check=kwargs.pop("check", False), **kwargs)

    code, result = invoke(destination, capsys, run=runner)
    assert code == 17 and result["step"] == "initial_commit"
    assert "private-token" not in json.dumps(result)
    assert (destination / bootstrap.CHECKPOINT).is_file()
    assert bootstrap.main(["--resume", str(destination), "--yes"]) == 0
    capsys.readouterr()


def test_missing_tool_stops_before_creating_destination(destination, capsys):
    def absent(*args, **kwargs):
        raise FileNotFoundError()

    code, result = invoke(destination, capsys, run=absent)
    assert code == 2 and "missing" in result["reason"]
    assert not destination.exists()
    assert "--resume" not in result["next_actions"][-1]["argv"]
    assert "--path" in result["next_actions"][-1]["argv"]


def test_tampered_checkpoint_is_rejected(destination, capsys):
    assert invoke(destination, capsys)[0] == 0
    marker = destination / bootstrap.CHECKPOINT
    value = json.loads(marker.read_text())
    value["sha"] = "0" * 40
    marker.write_text(json.dumps(value), encoding="utf-8")
    assert bootstrap.main(["--resume", str(destination), "--yes"]) == 3
    assert "baseline differs" in json.loads(capsys.readouterr().out)["reason"]


def test_redirected_git_metadata_is_not_followed(destination, capsys):
    assert invoke(destination, capsys)[0] == 0
    metadata = destination / ".git"
    metadata.rename(destination / "original-git")
    metadata.write_text("gitdir: original-git\n", encoding="utf-8")
    assert bootstrap.main(["--resume", str(destination), "--yes"]) == 3
    assert "redirected Git" in json.loads(capsys.readouterr().out)["reason"]


def test_concurrent_resume_respects_existing_lock(destination, capsys):
    assert invoke(destination, capsys)[0] == 0
    marker = destination / bootstrap.CHECKPOINT
    before = marker.read_bytes()
    with bootstrap.FileLock(destination / ".test-tools/bootstrap.lock", timeout=0):
        assert bootstrap.main(["--resume", str(destination), "--yes"]) == 3
    assert json.loads(capsys.readouterr().out)["status"] == "stopped"
    assert marker.read_bytes() == before


@pytest.mark.parametrize("path", ["relative/project", "../project"])
def test_relative_paths_are_not_interpreted_from_another_checkout(path):
    with pytest.raises(bootstrap.BootstrapError, match="absolute path"):
        bootstrap.plan("dymzz/sample-app", path)


class GitHubStub:
    def __init__(self, mode="normal"):
        self.mode, self.remote, self.remote_head = mode, None, ""
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        if args[:2] == ["git", "ls-remote"]:
            return subprocess.CompletedProcess(args, 0, self.remote_head, "")
        if args[:2] == ["git", "push"]:
            context = json.loads(
                (Path(kwargs["cwd"]) / bootstrap.CHECKPOINT).read_text()
            )
            self.remote_head = context["seed_sha"] + "\trefs/heads/main\n"
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[0] != "gh":
            return subprocess.run(args, check=kwargs.pop("check", False), **kwargs)
        if args[:3] == ["gh", "repo", "create"]:
            if self.mode == "reject":
                return subprocess.CompletedProcess(
                    args, 1, "", "HTTP 422 private-response"
                )
            self.remote = {"id": 42, "private": True, "full_name": "dymzz/sample-app"}
            if self.mode == "lost":
                return subprocess.CompletedProcess(
                    args, 1, "", "connection reset ghp_private-token"
                )
            return subprocess.CompletedProcess(
                args, 0, "https://github.com/dymzz/sample-app", ""
            )
        route = args[2]
        if route == "repos/dymzz/sample-app":
            if self.remote is None:
                return subprocess.CompletedProcess(args, 1, "", "HTTP 404 not found")
            value = self.remote
        elif route == "user":
            value = {"login": "dymzz"}
        elif route.endswith("/releases/tags/" + template.VERSION):
            value = {"draft": False, "tag_name": template.VERSION}
        elif "/git/ref/tags/" in route:
            value = {
                "object": {
                    "type": "commit",
                    "sha": "0" * 40 if self.mode == "tag-changed" else template.SHA,
                }
            }
        elif route.endswith("/actions/permissions/access"):
            value = {"access_level": "none" if self.mode == "no-sharing" else "user"}
        else:
            value = {"private": True, "owner": {"login": "dymzz"}}
        return subprocess.CompletedProcess(args, 0, json.dumps(value), "")

    @property
    def creates(self):
        return sum(args[:3] == ["gh", "repo", "create"] for args in self.calls)


def test_github_creates_once_and_pushes_only_readme_seed(destination, capsys):
    runner = GitHubStub()
    code, result = invoke(destination, capsys, "--github", run=runner)
    assert code == 0 and result["remote"] == "confirmed"
    assert result["context"]["remote_id"] == 42
    assert [args for args in runner.calls if args[:2] == ["git", "push"]] == [
        ["git", "push", "-u", "origin", "main"]
    ]
    assert (
        bootstrap.main(["--resume", str(destination), "--github", "--yes"], run=runner)
        == 0
    )
    capsys.readouterr()
    assert runner.creates == 1


def test_seed_push_with_real_git_to_local_bare_remote(destination, capsys):
    bare = destination.parent / "remote.git"
    git(destination.parent, "init", "--bare", str(bare))

    class LocalPush(GitHubStub):
        def __call__(self, args, **kwargs):
            if args[:2] in (["git", "ls-remote"], ["git", "push"]):
                self.calls.append(args)
                args = [str(bare) if arg == "origin" else arg for arg in args]
                return subprocess.run(args, check=kwargs.pop("check", False), **kwargs)
            return super().__call__(args, **kwargs)

    code, result = invoke(destination, capsys, "--github", run=LocalPush())
    assert code == 0
    assert git(bare, "rev-parse", "main") == result["context"]["seed_sha"]
    assert git(bare, "ls-tree", "--name-only", "main") == "README.md"
    assert git(bare, "for-each-ref", "--format=%(refname)") == "refs/heads/main"


def test_changed_push_destination_blocks_resume(destination, capsys):
    runner = GitHubStub()
    assert invoke(destination, capsys, "--github", run=runner)[0] == 0
    git(
        destination,
        "remote",
        "set-url",
        "--push",
        "origin",
        "https://example.invalid/other.git",
    )
    before = sum(args[:2] == ["git", "push"] for args in runner.calls)
    assert (
        bootstrap.main(["--resume", str(destination), "--github", "--yes"], run=runner)
        == 3
    )
    assert "origin differs" in json.loads(capsys.readouterr().out)["reason"]
    assert sum(args[:2] == ["git", "push"] for args in runner.calls) == before


def test_lost_create_response_requires_explicit_identity_confirmation(
    destination, capsys
):
    runner = GitHubStub("lost")
    code, result = invoke(destination, capsys, "--github", run=runner)
    assert code == 1 and result["context"]["remote_intent"] is True
    assert "ghp_private" not in json.dumps(result)
    args = ["--resume", str(destination), "--github", "--yes"]
    assert bootstrap.main(args, run=runner) == 3
    assert "No second create" in json.loads(capsys.readouterr().out)["reason"]
    assert runner.creates == 1
    assert bootstrap.main([*args, "--confirm-remote-id", "99"], run=runner) == 3
    capsys.readouterr()
    assert bootstrap.main([*args, "--confirm-remote-id", "42"], run=runner) == 0
    capsys.readouterr()
    assert runner.creates == 1


@pytest.mark.parametrize("mode", ["tag-changed", "no-sharing", "already-exists"])
def test_unsafe_remote_preconditions_never_create(mode, destination, capsys):
    runner = GitHubStub(mode)
    if mode == "already-exists":
        runner.remote = {"id": 99, "private": True, "full_name": "dymzz/sample-app"}
    assert invoke(destination, capsys, "--github", run=runner)[0] == 3
    assert runner.creates == 0


def test_http_422_is_explicit_rejection_not_ambiguous_creation(destination, capsys):
    code, result = invoke(destination, capsys, "--github", run=GitHubStub("reject"))
    assert code == 1 and result["http_status"] == 422
    assert result["context"]["remote_intent"] is False
