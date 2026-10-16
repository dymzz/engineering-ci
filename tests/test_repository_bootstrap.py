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
    answers = iter([str(destination), "dymzz/sample-app", "no", "create", "q"])
    prompts = []

    def reader(prompt):
        prompts.append(prompt)
        return next(answers)

    assert bootstrap.main([], reader=reader) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(prompts) == 5 and result["remote"] == "not_requested"
    assert "1/2" in prompts[0] and "路径" in prompts[0]
    assert "2/2" in prompts[1] and "OWNER/name" in prompts[1]
    assert git(destination, "branch", "--show-current") == template.BRANCH


@pytest.mark.parametrize("quote", ["", "'", '"'])
def test_copy_as_path_accepts_quotes_spaces_and_surrounding_whitespace(
    destination, capsys, quote
):
    target = destination.with_name("sample app")
    value = f"  {quote}{target}{quote}  "
    code = bootstrap.main(["--repo", "dymzz/sample-app", "--path", value, "--plan"])
    result = json.loads(capsys.readouterr().out)
    assert code == 0 and Path(result["context"]["path"]) == target.resolve()
    assert not target.exists()


def test_interactive_validation_retries_only_the_wrong_field(
    destination, monkeypatch, capsys
):
    monkeypatch.setattr(bootstrap.sys.stdin, "isatty", lambda: True)
    answers = iter(
        [
            "relative/project",
            str(destination),
            str(destination),  # A local path is not a GitHub OWNER/name.
            "dymzz/sample-app",
            "no",
            "create",
            "q",
        ]
    )
    prompts = []

    def reader(prompt):
        if "下一阶段" not in prompt:
            assert not destination.exists()  # No mutation before final confirmation.
        prompts.append(prompt)
        return next(answers)

    assert bootstrap.main([], reader=reader) == 0
    captured = capsys.readouterr()
    assert "absolute path" in captured.err and "GitHub OWNER" in captured.err
    assert ["1/2" in prompt for prompt in prompts[:4]] == [True, True, False, False]
    assert json.loads(captured.out)["status"] == "generated"


@pytest.mark.parametrize(
    "field", ["destination", "repository_name", "remote_choice", "confirmation"]
)
def test_eof_identifies_the_actual_prompt_without_creating_files(
    destination, monkeypatch, capsys, field
):
    monkeypatch.setattr(bootstrap.sys.stdin, "isatty", lambda: True)
    answers = iter([str(destination), "dymzz/sample-app", "no"])

    def reader(prompt):
        if (
            (field == "destination" and "1/2" in prompt)
            or (field == "repository_name" and "2/2" in prompt)
            or (field == "remote_choice" and "yes" in prompt)
            or (field == "confirmation" and "create" in prompt)
        ):
            raise EOFError()
        return next(answers)

    assert bootstrap.main([], reader=reader) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["step"] == field and "Interactive input ended" in result["reason"]
    assert not destination.exists()


def test_quoted_resume_path_uses_original_checkpoint(destination, capsys):
    assert invoke(destination, capsys)[0] == 0
    assert bootstrap.main(["--resume", f'"{destination}"', "--yes"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "generated"


def test_resume_without_tool_checkpoint_has_actionable_error(destination, capsys):
    destination.mkdir()
    assert bootstrap.main(["--resume", str(destination), "--yes"]) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["step"] == "resume_context"
    assert "No bootstrap checkpoint" in result["reason"]
    assert not list(destination.iterdir())


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
        self.backups = {}
        self.fetch_head = None
        self.lost_overwrite = False

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        if args[:2] == ["git", "ls-remote"]:
            ref = args[-1]
            value = (
                self.remote_head
                if ref == "refs/heads/main"
                else (
                    self.backups[ref] + "\t" + ref + "\n" if ref in self.backups else ""
                )
            )
            return subprocess.CompletedProcess(args, 0, value, "")
        if args[:2] == ["git", "fetch"]:
            self.fetch_head = self.remote_head.split()[0]
            return subprocess.CompletedProcess(args, 0, "", "")
        if (
            args[:2] == ["git", "rev-parse"]
            and args[-1] == "FETCH_HEAD"
            and self.fetch_head
        ):
            return subprocess.CompletedProcess(args, 0, self.fetch_head, "")
        if args[:2] == ["git", "push"]:
            context = json.loads(
                (Path(kwargs["cwd"]) / bootstrap.CHECKPOINT).read_text()
            )
            if any(arg.startswith("--force-with-lease=") for arg in args):
                sha, ref = args[-1].split(":", 1)
                if ref != "refs/heads/main":
                    if self.mode == "backup-fail":
                        return subprocess.CompletedProcess(args, 1, "", "backup denied")
                    self.backups[ref] = sha
                    return subprocess.CompletedProcess(args, 0, "", "")
                if self.mode == "race":
                    self.remote_head = "c" * 40 + "\trefs/heads/main\n"
                    return subprocess.CompletedProcess(args, 1, "", "stale info")
                if self.mode == "protected":
                    return subprocess.CompletedProcess(args, 1, "", "protected branch")
                assert args[2].endswith(":" + self.remote_head.split()[0])
            self.remote_head = context["seed_sha"] + "\trefs/heads/main\n"
            if self.mode == "overwrite-lost" and not self.lost_overwrite:
                self.lost_overwrite = True
                return subprocess.CompletedProcess(args, 1, "", "connection reset")
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


def existing_remote(mode="normal", *, head="a" * 40):
    runner = GitHubStub(mode)
    runner.remote = {
        "id": 99,
        "full_name": "dymzz/sample-app",
        "private": True,
        "permissions": {"push": True},
        "default_branch": "main",
    }
    runner.remote_head = head + "\trefs/heads/main\n" if head else ""
    return runner


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
        runner = existing_remote()
    assert invoke(destination, capsys, "--github", run=runner)[0] == 3
    assert runner.creates == 0


def test_http_422_is_explicit_rejection_not_ambiguous_creation(destination, capsys):
    code, result = invoke(destination, capsys, "--github", run=GitHubStub("reject"))
    assert code == 1 and result["http_status"] == 422
    assert result["context"]["remote_intent"] is False


def test_existing_remote_offers_explicit_alternatives_without_writes(
    destination, capsys
):
    runner = existing_remote()
    code, result = invoke(destination, capsys, "--github", run=runner)
    assert code == 3 and result["remote_result"] == "existing_requires_selection"
    assert result["observed_remote"]["main_sha"] == "a" * 40
    assert runner.creates == 0 and not [
        a for a in runner.calls if a[:2] == ["git", "push"]
    ]
    assert any("--expected-main-sha" in a["argv"] for a in result["next_actions"])


@pytest.mark.parametrize("head", [None, "a" * 40])
def test_connect_preserves_existing_history_and_reports_merge(
    head, destination, capsys
):
    runner = existing_remote(head=head)
    code, result = invoke(
        destination, capsys, "--github", "--existing-remote", "connect", run=runner
    )
    assert code == 0 and runner.creates == 0
    assert result["context"]["remote_mode"] == "connect"
    assert result["context"]["remote_intent"] is False
    assert result["remote_details"]["requires_history_merge"] == bool(head)
    if head:
        assert runner.remote_head.startswith(head)
        assert result["next_actions"][1]["argv"][:2] == ["git", "merge"]
        assert not [a for a in runner.calls if a[:2] == ["git", "push"]]


@pytest.mark.parametrize("answer", ["q", "1", "2", "confirm-overwrite"])
def test_existing_interactive_choices_require_specific_overwrite_confirmation(
    answer, destination, monkeypatch, capsys
):
    assert invoke(destination, capsys)[0] == 0
    runner = existing_remote()
    monkeypatch.setattr(bootstrap.sys.stdin, "isatty", lambda: True)
    answers = iter(
        [
            "create",
            "2" if answer == "confirm-overwrite" else answer,
            "overwrite dymzz/sample-app " + "a" * 12
            if answer == "confirm-overwrite"
            else "yes",
            "q",
        ]
    )
    code = bootstrap.main(
        ["--resume", str(destination), "--github"],
        run=runner,
        reader=lambda _: next(answers),
    )
    result = json.loads(capsys.readouterr().out)
    assert code == 0 and runner.creates == 0
    if answer in {"q", "2"}:
        assert result["status"] == "cancelled" and runner.remote_head.startswith(
            "a" * 40
        )
        assert not runner.backups
    elif answer == "1":
        assert result["remote_details"]["mode"] == "connect"
    else:
        assert result["remote_details"]["backup_sha"] == "a" * 40
        assert runner.remote_head.startswith(result["context"]["seed_sha"])


@pytest.mark.parametrize("expected", [None, "b" * 40])
def test_overwrite_without_matching_expected_sha_never_pushes(
    expected, destination, capsys
):
    runner = existing_remote()
    args = ["--github", "--existing-remote", "overwrite"]
    if expected:
        args.extend(["--expected-main-sha", expected])
    assert invoke(destination, capsys, *args, run=runner)[0] == 3
    assert not [a for a in runner.calls if a[:2] == ["git", "push"]]


@pytest.mark.parametrize(
    "mode", ["normal", "backup-fail", "protected", "race", "overwrite-lost"]
)
def test_overwrite_backup_lease_failure_and_resume(mode, destination, capsys):
    runner = existing_remote(mode)
    args = [
        "--github",
        "--existing-remote",
        "overwrite",
        "--expected-main-sha",
        "a" * 40,
    ]
    code, result = invoke(destination, capsys, *args, run=runner)
    assert code == (0 if mode == "normal" else 1)
    assert result["context"]["remote_mode"] == "overwrite"
    assert runner.creates == 0
    pushes = [a for a in runner.calls if a[:2] == ["git", "push"]]
    if mode == "backup-fail":
        assert len(pushes) == 1 and runner.remote_head.startswith("a" * 40)
        return
    assert runner.backups[bootstrap.backup_ref("a" * 40)] == "a" * 40
    assert pushes[-1][2] == "--force-with-lease=refs/heads/main:" + "a" * 40
    if mode == "protected":
        assert runner.remote_head.startswith("a" * 40)
    if mode == "race":
        assert runner.remote_head.startswith("c" * 40)
    if mode in {"normal", "overwrite-lost"}:
        count = len(pushes)
        assert (
            bootstrap.main(
                ["--resume", str(destination), "--github", "--yes"], run=runner
            )
            == 0
        )
        resumed = json.loads(capsys.readouterr().out)
        assert resumed["remote_details"]["backup_sha"] == "a" * 40
        assert len([a for a in runner.calls if a[:2] == ["git", "push"]]) == count


def test_legacy_checkpoint_migrates_without_losing_creation_uncertainty(
    destination, capsys
):
    runner = GitHubStub("lost")
    assert invoke(destination, capsys, "--github", run=runner)[0] == 1
    marker = destination / bootstrap.CHECKPOINT
    old = json.loads(marker.read_text())
    old["schema_version"] = 1
    del old["remote_mode"], old["overwrite_main_sha"]
    marker.write_text(json.dumps(old), encoding="utf-8")
    assert (
        bootstrap.main(["--resume", str(destination), "--github", "--yes"], run=runner)
        == 3
    )
    result = json.loads(capsys.readouterr().out)
    assert "No second create" in result["reason"] and runner.creates == 1
    assert json.loads(marker.read_text())["schema_version"] == 2


def test_legacy_unbound_checkpoint_can_connect_existing_remote(destination, capsys):
    assert invoke(destination, capsys)[0] == 0
    marker = destination / bootstrap.CHECKPOINT
    old = json.loads(marker.read_text())
    old["schema_version"] = 1
    del old["remote_mode"], old["overwrite_main_sha"]
    marker.write_text(json.dumps(old), encoding="utf-8")
    assert (
        bootstrap.main(
            [
                "--resume",
                str(destination),
                "--github",
                "--existing-remote",
                "connect",
                "--yes",
            ],
            run=existing_remote(),
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["context"]["schema_version"] == 2
    assert result["context"]["seed_sha"] == old["seed_sha"]
    assert result["context"]["remote_mode"] == "connect"


def test_existing_policy_never_creates_a_missing_repository(destination, capsys):
    runner = GitHubStub()
    code, result = invoke(
        destination, capsys, "--github", "--existing-remote", "connect", run=runner
    )
    assert code == 3 and "no create was attempted" in result["reason"]
    assert runner.creates == 0


@pytest.mark.parametrize("saved", ["a" * 40, "b" * 40])
def test_backup_ref_is_reused_only_if_immutable_identity_matches(
    saved, destination, capsys
):
    runner = existing_remote()
    runner.backups[bootstrap.backup_ref("a" * 40)] = saved
    code, _ = invoke(
        destination,
        capsys,
        "--github",
        "--existing-remote",
        "overwrite",
        "--expected-main-sha",
        "a" * 40,
        run=runner,
    )
    assert code == (0 if saved == "a" * 40 else 3)
    pushes = [args for args in runner.calls if args[:2] == ["git", "push"]]
    assert len(pushes) == (1 if saved == "a" * 40 else 0)
    assert runner.backups[bootstrap.backup_ref("a" * 40)] == saved


@pytest.mark.parametrize("property", ["permission", "visibility", "owner"])
def test_existing_repository_identity_and_permission_are_checked(
    property, destination, capsys
):
    runner = existing_remote()
    if property == "permission":
        runner.remote["permissions"]["push"] = False
    elif property == "visibility":
        runner.remote["private"] = False
    else:
        runner.remote["full_name"] = "other/sample-app"
    assert (
        invoke(
            destination, capsys, "--github", "--existing-remote", "connect", run=runner
        )[0]
        == 3
    )
    assert not [a for a in runner.calls if a[:2] == ["git", "push"]]


@pytest.mark.parametrize("advance_main", [False, True])
def test_real_git_existing_main_is_backed_up_before_overwrite(
    advance_main, destination, capsys
):
    bare, prior = destination.parent / "remote.git", destination.parent / "prior"
    prior.mkdir()
    git(prior, "init", "-b", "main")
    (prior / "README.md").write_text("existing history\n", encoding="utf-8")
    git(prior, "add", "README.md")
    git(prior, "commit", "-m", "existing main")
    old = git(prior, "rev-parse", "HEAD")
    git(destination.parent, "init", "--bare", str(bare))
    git(prior, "push", str(bare), "main", "main:refs/heads/keep-this")
    (prior / "concurrent.txt").write_text("later remote work\n", encoding="utf-8")
    git(prior, "add", "concurrent.txt")
    git(prior, "commit", "-m", "later main")
    newer = git(prior, "rev-parse", "HEAD")
    git(prior, "push", str(bare), "HEAD:refs/heads/concurrent")

    class RealGit(GitHubStub):
        def __call__(self, args, **kwargs):
            if args[:2] in (["git", "ls-remote"], ["git", "push"], ["git", "fetch"]):
                self.calls.append(args)
                if advance_main and args[:3] == [
                    "git",
                    "push",
                    "--force-with-lease=refs/heads/main:" + old,
                ]:
                    git(bare, "update-ref", "refs/heads/main", newer, old)
                args = [
                    str(bare)
                    if a in {"origin", "https://github.com/dymzz/sample-app.git"}
                    else a
                    for a in args
                ]
                return subprocess.run(args, check=kwargs.pop("check", False), **kwargs)
            return super().__call__(args, **kwargs)

    runner = RealGit()
    runner.remote = existing_remote().remote
    code, result = invoke(
        destination,
        capsys,
        "--github",
        "--existing-remote",
        "overwrite",
        "--expected-main-sha",
        old,
        run=runner,
    )
    assert code == (1 if advance_main else 0)
    assert git(bare, "rev-parse", "main") == (
        newer if advance_main else result["context"]["seed_sha"]
    )
    assert git(bare, "rev-parse", bootstrap.backup_ref(old)) == old
    assert git(bare, "rev-parse", "keep-this") == old
