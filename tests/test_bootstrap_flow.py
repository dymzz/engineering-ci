"""Exercise the complete onboarding flow with real temporary Git and fake uv/Dagger/gh."""

import json
import subprocess
from pathlib import Path

import pytest
from test_repository_bootstrap import GitHubStub, git, invoke
from test_repository_bootstrap import destination as git_destination

from engineering_ci.tools import bootstrap
from engineering_ci.tools import bootstrap_templates as template

destination = git_destination


class FlowRunner(GitHubStub):
    def __init__(self, mode="normal"):
        super().__init__()
        self.flow_mode, self.feature_head, self.pr = mode, None, None
        self.pr_creates = 0

    def __call__(self, args, **kwargs):
        root = Path(kwargs.get("cwd") or ".")
        value, code = "", 0
        if args == ["uv", "--version"]:
            value = "uv 0.12.23"
        elif args == ["dagger", "version"]:
            assert kwargs["env"]["FORCE_DARK_MODE"]
            assert kwargs["timeout"] == 30
            if self.flow_mode == "missing-dagger":
                raise FileNotFoundError()
            value = "dagger v" + (
                "0.21.10" if self.flow_mode == "wrong-dagger" else template.ENGINE
            )
        elif args[:2] == ["uv", "lock"]:
            folder = root / ".dagger" if "--project" in args else root
            (folder / "uv.lock").write_text("version = 1\n", encoding="utf-8")
        elif args[:2] == ["uv", "sync"]:
            if self.flow_mode == "sync-fail":
                code = 17
            elif self.flow_mode == "interrupted":
                raise KeyboardInterrupt()
        elif args[:2] == ["dagger", "api"]:
            if self.flow_mode != "missing-sdk":
                sdk = root / ".dagger/sdk"
                sdk.mkdir(parents=True, exist_ok=True)
                (sdk / "pyproject.toml").write_text(
                    '[project]\nname = "dagger-io"\n', encoding="utf-8"
                )
                (root / ".dagger/.gitignore").write_text("sdk/\n", encoding="utf-8")
        elif args[:2] == ["dagger", "check"]:
            if args == ["dagger", "check"]:
                if self.flow_mode == "check-fail":
                    code = 23
                if self.flow_mode == "source-changed":
                    (root / "app.py").write_text(
                        "# edited during check\n", encoding="utf-8"
                    )
        elif (
            args[:2] == ["git", "ls-remote"]
            and args[-1] == "refs/heads/" + template.BRANCH
        ):
            value = self.feature_head + "\t" + args[-1] if self.feature_head else ""
        elif args[:2] == ["git", "push"] and args[-1] == template.BRANCH:
            if self.flow_mode == "push-fail":
                code = 19
            else:
                self.feature_head = git(root, "rev-parse", "HEAD")
        elif args[:3] == ["gh", "pr", "list"]:
            value = json.dumps([self.pr] if self.pr else [])
        elif args[:3] == ["gh", "pr", "create"]:
            self.pr_creates += 1
            self.pr = {
                "number": 7,
                "url": "https://github.com/dymzz/sample-app/pull/7",
                "state": "OPEN",
                "isDraft": True,
                "baseRefName": "main",
                "headRefOid": git(root, "rev-parse", "HEAD"),
            }
            if self.flow_mode == "pr-response-lost":
                code = 1
        else:
            if args[0] in {"uv", "dagger"}:
                pytest.fail("Unexpected real tool invocation")
            result = super().__call__(args, **kwargs)
            if (
                self.flow_mode == "commit-mutation"
                and args[:2] == ["git", "commit"]
                and "--only" not in args
            ):
                (root / "app.py").write_text(
                    "# commit hook changed source\n", encoding="utf-8"
                )
            return result
        self.calls.append(args)
        return subprocess.CompletedProcess(
            args, code, value, "HTTP 503 redacted" if code else ""
        )


def test_setup_runs_native_check_once_and_never_starts_delivery(destination, capsys):
    runner = FlowRunner()
    code, result = invoke(destination, capsys, "--setup", run=runner)
    assert code == 0 and result["status"] == "checked" and result["checks"] == "passed"
    assert runner.calls.count(["dagger", "check"]) == 1
    assert runner.creates == 0 and runner.pr_creates == 0
    assert all(step["status"] == "passed" for step in result["local_setup"]["steps"])
    assert git(destination, "rev-parse", "HEAD") == result["context"]["seed_sha"]


@pytest.mark.parametrize(
    "mode,code,checks",
    [
        ("sync-fail", 17, "not_run"),
        ("missing-sdk", 3, "not_run"),
        ("check-fail", 23, "failed"),
        ("missing-dagger", 2, "not_run"),
        ("wrong-dagger", 2, "not_run"),
        ("source-changed", 3, "stale"),
    ],
)
def test_setup_failures_preserve_native_status_and_block_delivery(
    mode, code, checks, destination, capsys
):
    runner = FlowRunner(mode)
    actual, result = invoke(
        destination, capsys, "--deliver", "-m", "bootstrap", run=runner
    )
    assert actual == code and result["checks"] == checks
    assert runner.feature_head is None and runner.pr_creates == 0
    assert git(destination, "rev-parse", "HEAD") == result["context"]["seed_sha"]
    assert "--deliver" in result["next_actions"][1]["argv"]
    if mode == "missing-sdk":
        step = next(
            step for step in result["local_setup"]["steps"] if step["name"] == "sdk"
        )
        assert step["tool_exit_code"] == 0 and step["exit_code"] == 3


def test_interrupt_retains_actual_steps_and_resume_command(destination, capsys):
    code, result = invoke(destination, capsys, "--setup", run=FlowRunner("interrupted"))
    assert code == 130 and result["checks"] == "not_run"
    steps = result["local_setup"]["steps"]
    assert steps[0]["status"] == "passed" and steps[1]["status"] == "interrupted"
    assert steps[1]["exit_code"] == 130 and steps[2]["status"] == "not_run"
    assert result["next_actions"][1]["argv"][-1] == "--setup"


def test_yes_alone_does_not_install_check_or_deliver(destination, capsys):
    runner = FlowRunner()
    code, result = invoke(destination, capsys, run=runner)
    assert code == 0 and result["checks"] == "not_run"
    assert not any(args[0] in {"uv", "dagger", "gh"} for args in runner.calls)


def test_plan_includes_explicit_delivery_without_invoking_tools(destination, capsys):
    runner = FlowRunner()
    code, result = invoke(destination, capsys, "--deliver", "--plan", run=runner)
    assert (
        code == 0 and result["github_requested"] and result["continuation"] == "deliver"
    )
    assert not runner.calls and not destination.exists()


def test_noninteractive_delivery_requires_explicit_confirmation(
    destination, monkeypatch
):
    monkeypatch.setattr(bootstrap.sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit) as error:
        bootstrap.main(
            ["--repo", "dymzz/sample-app", "--path", str(destination), "--deliver"]
        )
    assert error.value.code == 2 and not destination.exists()


def test_full_flow_commits_real_generated_sdk_and_confirms_draft(destination, capsys):
    runner = FlowRunner()
    code, result = invoke(
        destination, capsys, "--deliver", "-m", "ci: bootstrap project", run=runner
    )
    assert code == 0 and result["status"] == "draft_pr"
    assert runner.calls.count(["dagger", "check"]) == 1
    assert (
        runner.pr_creates == 1
        and runner.feature_head == result["delivery"]["commit_sha"]
    )
    assert result["pr"]["headRefOid"] == git(destination, "rev-parse", "HEAD")
    assert git(destination, "ls-tree", "HEAD", ".dagger/sdk/pyproject.toml")
    assert not git(destination, "status", "--porcelain")
    assert result["stop"]["code"] == "human_review"
    assert not any(args[:3] == ["gh", "pr", "merge"] for args in runner.calls)


@pytest.mark.parametrize("mode,code", [("push-fail", 19), ("pr-response-lost", 1)])
def test_delivery_failure_keeps_passed_checks_and_recovers_existing_commit(
    mode, code, destination, capsys
):
    runner = FlowRunner(mode)
    actual, result = invoke(
        destination, capsys, "--deliver", "-m", "bootstrap", run=runner
    )
    assert actual == code and result["checks"] == "passed"
    if mode == "pr-response-lost":
        assert result["pull_request"] == "unconfirmed"
    head = git(destination, "rev-parse", "HEAD")
    assert result["delivery"]["commit_sha"] == head != result["context"]["seed_sha"]
    count = runner.pr_creates
    runner.flow_mode = "normal"
    assert (
        bootstrap.main(["--resume", str(destination), "--deliver", "--yes"], run=runner)
        == 0
    )
    resumed = json.loads(capsys.readouterr().out)
    assert resumed["delivery"]["commit_sha"] == head
    assert runner.pr_creates == (count if mode == "pr-response-lost" else 1)


def test_commit_hook_mutation_prevents_push(destination, capsys):
    runner = FlowRunner("commit-mutation")
    code, result = invoke(
        destination, capsys, "--deliver", "-m", "bootstrap", run=runner
    )
    assert code == 3 and result["checks"] == "stale"
    assert runner.feature_head is None and runner.pr_creates == 0
    assert any(
        op["name"] == "git-commit" and op["status"] == "passed"
        for op in result["operations"]
    )


def test_interactive_flow_continues_to_draft_with_separate_confirmations(
    destination, monkeypatch, capsys
):
    runner = FlowRunner()
    monkeypatch.setattr(bootstrap.sys.stdin, "isatty", lambda: True)
    answers = iter(["create", "1", "yes", "ci: bootstrap", "yes", "yes", "yes"])
    prompts = []

    def reader(prompt):
        prompts.append(prompt)
        return next(answers)

    assert (
        bootstrap.main(
            ["--repo", "dymzz/sample-app", "--path", str(destination), "--github"],
            reader=reader,
            run=runner,
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "draft_pr"
    assert any("下一阶段" in prompt for prompt in prompts)
    assert any("git commit" in prompt for prompt in prompts)
    assert any("Push" in prompt for prompt in prompts)
    assert any("Draft PR" in prompt for prompt in prompts)


@pytest.mark.parametrize(
    "stop,answers",
    [
        ("git-add", ["no"]),
        ("git-commit", ["yes", "bootstrap", "no"]),
        ("git-push", ["yes", "bootstrap", "yes", "no"]),
        ("draft-pr", ["yes", "bootstrap", "yes", "yes", "no"]),
    ],
)
def test_declining_each_delivery_operation_stops_before_that_write(
    stop, answers, destination, monkeypatch, capsys
):
    runner = FlowRunner()
    monkeypatch.setattr(bootstrap.sys.stdin, "isatty", lambda: True)
    values = iter(["create", *answers])
    assert (
        bootstrap.main(
            [
                "--repo",
                "dymzz/sample-app",
                "--path",
                str(destination),
                "--github",
                "--deliver",
            ],
            run=runner,
            reader=lambda _: next(values),
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "cancelled" and result["step"] == stop
    assert not any(op["name"] == stop for op in result.get("operations", []))
    assert runner.pr_creates == 0
