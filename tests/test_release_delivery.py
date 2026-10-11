"""Real local Git operations and fake gh responses exercise delivery handoffs."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from engineering_ci import checks
from engineering_ci.cli import release as cli
from engineering_ci.execution.models import (
    CollectionIdentity,
    DispatchIdentity,
    TaskRecord,
)
from engineering_ci.execution.store import StateStore
from engineering_ci.release import delivery

REPOSITORY = "dymzz/engineering-ci"
VERSION = "0.4.11"


@pytest.fixture(autouse=True)
def fixture_version(monkeypatch):
    monkeypatch.setattr(cli, "checked_version", lambda: VERSION)


class Repository:
    def __init__(self, directory):
        self.root, self.remote = directory / "checkout", directory / "remote.git"
        self.root.mkdir()
        self.pr = None
        self.calls = []
        self.permission = "WRITE"
        self.failure = None
        self.compare = "identical"
        self.git("init", "--bare", str(self.remote))
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        (self.root / ".gitignore").write_text(".test-tools/\n")
        (self.root / "README.md").write_text("Initial\n")
        self.git("add", ".")
        self.git("commit", "-m", "Initial")
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-u", "origin", "main")
        self.git("switch", "-c", "codex/delivery")
        (self.root / "README.md").write_text("Unified delivery\n")
        self.calls.clear()

    def git(self, *args):
        result = subprocess.run(
            ["git", *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def existing_pr(self, *, state="OPEN"):
        self.pr = {
            "number": 11,
            "url": f"https://github.com/{REPOSITORY}/pull/11",
            "state": state,
            "isDraft": True,
            "headRefOid": self.git("rev-parse", "HEAD"),
            "baseRefName": "main",
            "title": "Existing change",
            "body": "Human context",
            "mergeCommit": {"oid": self.git("rev-parse", "main")},
        }
        return self.pr

    def run(self, args, **kwargs):
        self.calls.append(args)
        if self.failure and args[: len(self.failure)] == self.failure:
            return subprocess.CompletedProcess(
                args, 1, "", "SECRET permission denied (HTTP 403)"
            )
        if args[:3] == ["git", "remote", "get-url"]:
            return subprocess.CompletedProcess(
                args, 0, f"https://github.com/{REPOSITORY}.git\n", ""
            )
        if args[0] == "git":
            kwargs.pop("check", None)
            return subprocess.run(args, check=False, **kwargs)
        assert args[0] == "gh"
        if args[1:3] == ["repo", "view"]:
            assert args[3] == REPOSITORY and "--repo" not in args
            value = {
                "nameWithOwner": REPOSITORY,
                "viewerPermission": self.permission,
                "defaultBranchRef": {"name": "main"},
            }
        elif args[1:3] == ["pr", "list"]:
            if self.pr and self.pr["state"] == "OPEN":
                reference = f"refs/heads/{self.git('branch', '--show-current')}"
                result = subprocess.run(
                    ["git", "--git-dir", str(self.remote), "rev-parse", reference],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if not result.returncode:
                    self.pr["headRefOid"] = result.stdout.strip()
            value = [self.pr.copy()] if self.pr else []
        elif args[1:3] == ["pr", "create"]:
            assert "--draft" in args and "--head" in args
            self.existing_pr()
            self.pr["title"] = args[args.index("--title") + 1]
            self.pr["body"] = Path(args[args.index("--body-file") + 1]).read_text(
                encoding="utf-8"
            )
            return subprocess.CompletedProcess(args, 0, self.pr["url"], "")
        elif args[1:3] == ["pr", "edit"]:
            self.pr["body"] = Path(args[args.index("--body-file") + 1]).read_text(
                encoding="utf-8"
            )
            return subprocess.CompletedProcess(args, 0, self.pr["url"], "")
        elif args[1:3] == ["pr", "view"]:
            value = {
                "headRefOid": self.pr["headRefOid"],
                "mergeable": "MERGEABLE",
                "mergeStateStatus": "CLEAN",
                "statusCheckRollup": [],
            }
        elif args[1] == "api" and args[2].endswith("/commits/main"):
            value = {"sha": self.git("rev-parse", "main")}
        elif args[1] == "api" and "/compare/" in args[2]:
            value = {"status": self.compare}
        else:
            pytest.fail(f"Unexpected gh operation: {args}")
        return subprocess.CompletedProcess(args, 0, json.dumps(value), "")


@pytest.fixture
def repository(tmp_path, monkeypatch):
    repo = Repository(tmp_path)
    monkeypatch.setattr(delivery, "checked_version", lambda: VERSION)
    return repo


@pytest.fixture
def passing_checks(monkeypatch):
    calls = []

    def execute(selectors, *, root, output, reuse=False):
        if (
            reuse
            and output.is_file()
            and [entry["name"] for entry in json.loads(output.read_text())["checks"]]
            == list(checks.PR_CHECKS)
        ):
            return {}, 0
        calls.append(selectors)
        checks.atomic_json(
            output,
            {
                "checks": [
                    {"name": name, "identity": {"sha256": "f" * 64}}
                    for name in checks.PR_CHECKS
                ]
            },
        )
        return {}, 0

    monkeypatch.setattr(checks, "run_checks", execute)
    monkeypatch.setattr(
        checks,
        "inspect",
        lambda *args, **kwargs: (
            {
                "overall_status": "passed",
                "checks": [
                    {"name": name, "passed_for_current_input": True}
                    for name in checks.PR_CHECKS
                ],
            },
            0,
        ),
    )
    return calls


def invoke(repo, **options):
    return delivery.prepare(
        REPOSITORY,
        VERSION,
        checkout=delivery.Checkout(REPOSITORY, root=repo.root, run=repo.run),
        **options,
    )


def test_delivery_checks_commits_pushes_and_creates_draft_with_real_git(
    repository,
    passing_checks,
    capsys,
):
    repo = repository
    assert invoke(repo, include_all=True, message="Unify delivery") == 3
    assert passing_checks == [["pr"]]
    assert repo.git("status", "--porcelain") == ""
    assert repo.pr["headRefOid"] == repo.git("rev-parse", "HEAD")
    assert "Unified delivery" in (repo.root / "README.md").read_text()
    assert "README.md" in repo.pr["body"] and "Unify delivery" in repo.pr["body"]
    assert delivery.START in repo.pr["body"]
    assert not any(
        "--force" in call or call[:3] == ["gh", "pr", "merge"] for call in repo.calls
    )
    output = capsys.readouterr().out
    assert repo.pr["url"] in output and repo.pr["headRefOid"] in output
    assert "publish --repo dymzz/engineering-ci --version v0.4.11" in output
    assert "no reported results yet" in output


def test_repeating_before_human_merge_does_not_execute_checks_or_push(
    repository,
    passing_checks,
    capsys,
):
    invoke(repository, include_all=True, message="Unify delivery")
    repository.calls.clear()
    assert invoke(repository) == 3
    assert passing_checks == [["pr"]]
    assert not any(
        call[:2] in (["git", "push"], ["git", "commit"], ["git", "add"])
        for call in repository.calls
    )
    assert "Awaiting human review" in capsys.readouterr().out


def test_existing_pr_is_updated_without_overwriting_human_context(
    repository, passing_checks
):
    repository.existing_pr()
    assert invoke(repository, include_all=True, message="Finish delivery") == 3
    assert repository.pr["body"].startswith("Human context\n\n")
    assert not any(call[:3] == ["gh", "pr", "create"] for call in repository.calls)
    assert "Finish delivery" in repository.pr["body"]
    assert repository.pr["title"] == "Existing change"


def test_commit_message_continues_into_pr_with_single_line_title(
    repository, passing_checks
):
    message = "统一本地与 PR 检查及发布入口\n\n保留原始检查退出码与发布证据。"
    (repository.root / "new-file.txt").write_text("Included by git add --all\n")
    assert invoke(repository, include_all=True, message=message) == 3
    assert repository.git("log", "-1", "--format=%B") == message
    assert repository.pr["title"] == message.splitlines()[0]
    assert message in repository.pr["body"]
    assert "new-file.txt" in repository.git("ls-tree", "--name-only", "HEAD")


def test_retry_reads_committed_message_instead_of_replacing_it(
    repository, passing_checks
):
    message = "Unify delivery\n\nKeep the reviewed release evidence."
    repository.failure = ["gh", "pr", "create"]
    with pytest.raises(delivery.DeliveryError, match="permission denied"):
        invoke(repository, include_all=True, message=message)
    repository.failure = None
    assert invoke(repository, message="Different retry text") == 3
    assert passing_checks == [["pr"]]
    assert repository.pr["title"] == "Unify delivery"
    assert message in repository.pr["body"]
    assert "Different retry text" not in repository.pr["body"]


def test_unrelated_changes_require_explicit_scope_before_commit(repository):
    (repository.root / "unrelated.txt").write_text("Preserve\n")
    with pytest.raises(delivery.DeliveryError, match="--all"):
        invoke(repository, message="Unify delivery")
    assert (repository.root / "unrelated.txt").read_text() == "Preserve\n"
    assert not any(
        call[:2] in (["git", "add"], ["git", "commit"], ["git", "push"])
        for call in repository.calls
    )


def test_failed_checks_stop_before_staging_and_print_report(
    repository, monkeypatch, capsys
):
    monkeypatch.setattr(checks, "run_checks", lambda *args, **kwargs: ({}, 7))
    assert invoke(repository, include_all=True, message="Unify delivery") == 7
    assert not any(call[:2] == ["git", "add"] for call in repository.calls)
    assert "Stopped at local checks (exit 7)" in capsys.readouterr().out


@pytest.mark.parametrize("failure", [["git", "push"], ["gh", "pr", "create"]])
def test_permission_failure_preserves_commit_and_reports_safe_reason(
    repository, passing_checks, failure
):
    repository.failure = failure
    with pytest.raises(delivery.DeliveryError, match="permission denied") as error:
        invoke(repository, include_all=True, message="Unify delivery")
    assert "SECRET" not in str(error.value)
    assert repository.git("log", "-1", "--format=%s") == "Unify delivery"
    assert repository.git("status", "--porcelain") == ""


def test_missing_write_permission_refuses_before_checks(repository, passing_checks):
    repository.permission = "READ"
    with pytest.raises(delivery.DeliveryError, match="write permission"):
        invoke(repository, include_all=True, message="Unify delivery")
    assert passing_checks == []


def test_status_context_remains_readable_without_write_permission(repository):
    repository.permission = "READ"
    checkout = delivery.Checkout(REPOSITORY, root=repository.root, run=repository.run)
    context = checkout.context(VERSION, validate_version=False)
    assert context["permission"] == "READ" and context["remote_head"] is None


def test_status_context_shows_mismatching_local_version(repository, monkeypatch):
    monkeypatch.setattr(delivery, "checked_version", lambda: "0.4.12")
    checkout = delivery.Checkout(REPOSITORY, root=repository.root, run=repository.run)
    assert (
        checkout.context(VERSION, validate_version=False)["local_version"] == "0.4.12"
    )
    with pytest.raises(delivery.DeliveryError, match="four local metadata"):
        checkout.context(VERSION)


def test_status_handoff_can_show_pr_before_local_commit_is_pushed(repository, capsys):
    repository.existing_pr()
    repository.git("add", ".")
    repository.git("commit", "-m", "Unify delivery")
    checkout = delivery.Checkout(REPOSITORY, root=repository.root, run=repository.run)
    context = checkout.context(VERSION)
    assert delivery.handoff(checkout, context, require_head=False) == 3
    assert "local changes still need delivery" in capsys.readouterr().out
    with pytest.raises(delivery.DeliveryError, match="PR changed"):
        delivery.handoff(checkout, context)


@pytest.mark.parametrize("conclusion", ["SUCCESS", "FAILURE", "SKIPPED", "IN_PROGRESS"])
def test_handoff_recommends_merge_only_for_current_successful_ci(
    repository, passing_checks, capsys, conclusion
):
    invoke(repository, include_all=True, message="Delivered")
    capsys.readouterr()
    original = repository.run

    def run(args, **kwargs):
        result = original(args, **kwargs)
        if args[:3] == ["gh", "pr", "view"]:
            value = json.loads(result.stdout)
            value["statusCheckRollup"] = [
                {"name": name, "conclusion": conclusion} for name in checks.PR_CI_JOBS
            ]
            result.stdout = json.dumps(value)
        return result

    checkout = delivery.Checkout(REPOSITORY, root=repository.root, run=run)
    assert delivery.prepare(REPOSITORY, VERSION, checkout=checkout) == 3
    output = capsys.readouterr().out
    assert ("mark its Draft ready" in output) is (conclusion == "SUCCESS")
    assert checkout.execution_context.review.ci_status == (
        "passed"
        if conclusion == "SUCCESS"
        else "pending"
        if conclusion == "IN_PROGRESS"
        else "failed"
    )
    if conclusion != "SUCCESS":
        assert "Stopped at PR CI" in output


def test_status_with_local_changes_names_delivery_as_primary_stop(repository, capsys):
    repository.existing_pr()
    checkout = delivery.Checkout(REPOSITORY, root=repository.root, run=repository.run)
    assert (
        delivery.handoff(checkout, checkout.context(VERSION), require_head=False) == 3
    )
    output = capsys.readouterr().out
    assert "Stopped at delivery" in output
    assert "Awaiting human review" not in output and "After merge" not in output


def test_merged_pr_can_continue_without_checkout_switch_or_repeated_checks(repository):
    repository.git("add", ".")
    repository.git("commit", "-m", "Unify delivery")
    repository.existing_pr(state="MERGED")
    checkout = delivery.Checkout(REPOSITORY, root=repository.root, run=repository.run)
    assert delivery.prepare(REPOSITORY, VERSION, checkout=checkout) == 0
    assert checkout.publication_sha == repository.git("rev-parse", "main")
    assert not any(
        call[:2] in (["git", "switch"], ["git", "push"]) for call in repository.calls
    )


def test_merged_pr_missing_from_main_is_refused(repository):
    repository.git("add", ".")
    repository.git("commit", "-m", "Unify delivery")
    repository.existing_pr(state="MERGED")
    repository.compare = "diverged"
    with pytest.raises(delivery.DeliveryError, match="does not include"):
        invoke(repository)


def test_changed_inputs_during_checks_cannot_be_committed(
    repository, passing_checks, monkeypatch
):
    original = checks.run_checks

    def execute(*args, **kwargs):
        result = original(*args, **kwargs)
        (repository.root / "README.md").write_text("Concurrent edit\n")
        return result

    monkeypatch.setattr(checks, "run_checks", execute)
    with pytest.raises(delivery.DeliveryError, match="changed during checking"):
        invoke(repository, include_all=True, message="Unify delivery")
    assert not any(call[:2] == ["git", "add"] for call in repository.calls)


def test_commit_cannot_pass_a_replaced_check_receipt_to_push(
    repository, passing_checks
):
    original = repository.run

    def run(args, **kwargs):
        result = original(args, **kwargs)
        if args[:2] == ["git", "commit"]:
            path = repository.root / ".test-tools/checks/pr.json"
            saved = json.loads(path.read_text())
            saved["changed_after_check"] = True
            checks.atomic_json(path, saved)
        return result

    repository.run = run
    with pytest.raises(delivery.DeliveryError, match="receipt changed"):
        invoke(repository, include_all=True, message="Unify delivery")
    assert not any(call[:2] == ["git", "push"] for call in repository.calls)
    assert repository.git("log", "-1", "--format=%s") == "Unify delivery"


@pytest.mark.parametrize("matching", [True, False])
def test_cli_publication_consumes_the_same_merge_context(
    repository, monkeypatch, matching
):
    repository.git("add", ".")
    repository.git("commit", "-m", "Reviewed changes")
    repository.existing_pr(state="MERGED")
    checkout = delivery.Checkout(REPOSITORY, root=repository.root, run=repository.run)
    monkeypatch.setattr(delivery, "Checkout", lambda repository: checkout)
    monkeypatch.setattr(cli, "execution_environment", lambda: 0)
    monkeypatch.setattr(cli.verification, "published", lambda *args: None)
    monkeypatch.setattr(cli, "execution_checkpoint", lambda *args: False)
    monkeypatch.setattr(
        cli,
        "resolve_target",
        lambda *args: {
            "version": VERSION,
            "tag": "v" + VERSION,
            "ref": "main",
            "commit_sha": repository.git("rev-parse", "main") if matching else "b" * 40,
        },
    )
    calls = []

    def entrypoint(args):
        assert checkout.lifecycle.context is checkout.execution_context
        assert checkout.lifecycle.phase == "PUBLICATION_ready"
        assert checkout.execution_context.merge.main_commit == repository.git(
            "rev-parse", "main"
        )
        calls.append(args)
        return 1

    monkeypatch.setattr(cli, "entrypoint", entrypoint)
    assert cli.main(["publish", "--version", VERSION]) == 1
    assert bool(calls) is matching


def test_access_time_updates_do_not_claim_the_input_changed(repository, monkeypatch):
    from types import SimpleNamespace

    stat = Path.stat
    observation = 0

    def observed(path, *args, **kwargs):
        nonlocal observation
        result = stat(path, *args, **kwargs)
        if path != repository.root / "README.md":
            return result
        values = {
            name: getattr(result, name)
            for name in dir(result)
            if name.startswith("st_")
        }
        observation += 1
        values["st_atime_ns"] += observation
        return SimpleNamespace(**values)

    monkeypatch.setattr(Path, "stat", observed)
    checkout = delivery.Checkout(REPOSITORY, root=repository.root, run=repository.run)
    assert checkout.context(VERSION)["files"] == ["README.md"]


def test_printed_continuation_executes_with_literal_arguments(capsys):
    from argparse import Namespace

    purpose = "change with ' quote and $literal"
    cli.continue_command(
        Namespace(
            command="publish",
            repo=REPOSITORY,
            version=VERSION,
            include_all=True,
            message=purpose,
        )
    )
    command = capsys.readouterr().out.removeprefix("Continue: ").strip()
    if sys.platform == "win32":
        args = [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "function uv { ConvertTo-Json -Compress -InputObject @($args) }; "
            + command,
        ]
    else:
        args = ["/bin/sh", "-c", "uv() { printf '%s\\n' \"$@\"; }; " + command]
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    values = (
        json.loads(result.stdout)
        if sys.platform == "win32"
        else result.stdout.splitlines()
    )
    assert values[-2:] == ["-m", purpose]
    assert "--all" in values and "--include-all" not in values
    assert values[:3] == ["run", "--locked", "python"]


@pytest.mark.parametrize("profile", ["pr", "reader-probe"])
def test_delivery_delegates_configured_profile_and_per_check_reuse(
    repository, monkeypatch, profile
):
    path = repository.root / ".test-tools/checks/pr.json"
    calls = []

    def execute(selectors, **kwargs):
        calls.append((selectors, kwargs))
        return {}, 3

    monkeypatch.setattr(checks, "run_checks", execute)
    # Substitute the reader input only; the TOML loader still restricts public targets.
    monkeypatch.setitem(delivery.PIPELINE["delivery"], "checks_profile", profile)
    checkout = delivery.Checkout(REPOSITORY, root=repository.root, run=repository.run)
    assert delivery.current_checks(checkout, path) == 3
    assert calls == [
        ([profile], {"root": repository.root, "output": path, "reuse": True})
    ]


def test_delivery_retains_existing_report_outputs_without_weakening_the_profile(
    repository, monkeypatch
):
    path = repository.root / ".test-tools/checks/pr.json"
    parameters = {"coverage": True, "junit": "diagnostics/junit.xml"}
    checks.atomic_json(
        path,
        {
            "schema_version": 1,
            "profile": "pr",
            "selectors": ["pr"],
            "checks": [
                {"name": name, "identity": {"parameters": parameters}}
                for name in checks.PR_CHECKS
            ],
        },
    )
    calls = []
    monkeypatch.setattr(
        checks,
        "run_checks",
        lambda selectors, **kwargs: calls.append((selectors, kwargs)) or ({}, 3),
    )
    assert (
        delivery.current_checks(
            delivery.Checkout(REPOSITORY, root=repository.root), path
        )
        == 3
    )
    assert calls == [
        (["pr"], {"root": repository.root, "output": path, "reuse": True, **parameters})
    ]


def test_partial_report_does_not_replace_pr_profile(repository, passing_checks):
    path = repository.root / ".test-tools/checks/pr.json"
    checks.atomic_json(path, {"checks": [{"name": "ruff-lint"}]})
    assert (
        delivery.current_checks(
            delivery.Checkout(REPOSITORY, root=repository.root), path
        )
        == 0
    )
    assert passing_checks == [["pr"]]


def test_waiting_for_review_never_dispatches_a_workflow(monkeypatch, capsys):
    monkeypatch.setattr(cli, "execution_environment", lambda: 0)
    monkeypatch.setattr(cli.verification, "published", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "prepare_publication", lambda options: 3)
    monkeypatch.setattr(
        cli, "entrypoint", lambda args: pytest.fail("No Dispatch before human merge")
    )
    assert cli.main(["publish", "--version", VERSION]) == 3


@pytest.mark.parametrize("conclusion", ["SUCCESS", "FAILURE", "SKIPPED", "IN_PROGRESS"])
def test_cli_review_stop_has_no_unconditional_publish_retry(
    repository, passing_checks, monkeypatch, capsys, conclusion
):
    invoke(repository, include_all=True, message="Delivered")
    capsys.readouterr()
    original = repository.run

    def run(args, **kwargs):
        result = original(args, **kwargs)
        if args[:3] == ["gh", "pr", "view"]:
            value = json.loads(result.stdout)
            value["statusCheckRollup"] = [
                {"name": name, "conclusion": conclusion} for name in checks.PR_CI_JOBS
            ]
            result.stdout = json.dumps(value)
        return result

    checkout = delivery.Checkout(REPOSITORY, root=repository.root, run=run)
    monkeypatch.setattr(delivery, "Checkout", lambda repository: checkout)
    monkeypatch.setattr(cli, "execution_environment", lambda: 0)
    monkeypatch.setattr(cli.verification, "published", lambda *args: None)
    monkeypatch.setattr(
        cli, "entrypoint", lambda *args: pytest.fail("No dispatch before merge")
    )
    assert cli.main(["publish", "--version", VERSION, "--all", "-m", "Delivered"]) == 3
    output = capsys.readouterr().out
    assert checkout.lifecycle.phase == "REVIEW_waiting"
    assert not any(line.startswith("Continue:") for line in output.splitlines())
    assert ("Human merge after approval:" in output) is (conclusion == "SUCCESS")
    if conclusion == "SUCCESS":
        assert f"--match-head-commit {repository.git('rev-parse', 'HEAD')}" in output
        assert "After merge, continue:" in output
        assert "repeating publish cannot merge it" in output
    assert passing_checks == [["pr"]]
    assert not any(call[:3] == ["gh", "pr", "merge"] for call in repository.calls)


@pytest.mark.parametrize("flag", ["-m", "--message"])
def test_publish_uses_native_git_option_names(monkeypatch, capsys, flag):
    monkeypatch.setattr(cli, "execution_environment", lambda: 0)
    monkeypatch.setattr(cli.verification, "published", lambda *args, **kwargs: None)
    received = []
    monkeypatch.setattr(
        cli, "prepare_publication", lambda options: received.append(options) or 3
    )
    monkeypatch.setattr(
        cli, "entrypoint", lambda args: pytest.fail("No Dispatch before merge")
    )
    assert (
        cli.main(["publish", "--version", VERSION, "--all", flag, "Unify delivery"])
        == 3
    )
    assert received[0].include_all and received[0].message == "Unify delivery"
    assert "--all -m" in capsys.readouterr().out


def test_stop_error_includes_repository_version_and_continuation(monkeypatch, capsys):
    monkeypatch.setattr(cli, "execution_environment", lambda: 0)
    monkeypatch.setattr(cli.verification, "published", lambda *args, **kwargs: None)

    def fail(options):
        raise delivery.DeliveryError("git push: permission denied")

    monkeypatch.setattr(cli, "prepare_publication", fail)
    assert cli.main(["publish", "--version", VERSION]) == 1
    output = capsys.readouterr()
    assert "permission denied" in output.err
    assert "Continue:" in output.out and REPOSITORY in output.out
    assert "v0.4.11" in output.out and "status" in output.out


def test_version_mismatch_never_recommends_the_same_invalid_request(
    monkeypatch, capsys
):
    monkeypatch.setattr(cli, "execution_environment", lambda: 0)
    monkeypatch.setattr(cli.verification, "published", lambda *args: None)
    monkeypatch.setattr(cli, "checked_version", lambda: "0.4.12")

    def mismatch(options):
        raise delivery.DeliveryError("Requested version differs from local metadata")

    monkeypatch.setattr(cli, "prepare_publication", mismatch)
    monkeypatch.setattr(cli, "entrypoint", lambda *args: pytest.fail("No dispatch"))
    assert cli.main(["publish", "--version", VERSION]) == 1
    output = capsys.readouterr().out
    continuation = [
        line for line in output.splitlines() if line.startswith("Continue:")
    ]
    assert len(continuation) == 1
    assert "--version v0.4.12" in continuation[0]
    assert "v0.4.11" not in continuation[0]
    assert "local metadata version is intended" in output


def test_failed_stage_keeps_shared_context_for_cli_handoff(
    repository, monkeypatch, capsys
):
    checkout = delivery.Checkout(REPOSITORY, root=repository.root, run=repository.run)
    monkeypatch.setattr(delivery, "Checkout", lambda repository: checkout)
    monkeypatch.setattr(cli, "execution_environment", lambda: 0)
    monkeypatch.setattr(cli.verification, "published", lambda *args: None)
    monkeypatch.setattr(cli, "entrypoint", lambda *args: pytest.fail("Not admitted"))
    assert cli.main(["publish", "--version", VERSION]) == 1
    assert "Stopped phase: DELIVERY_pending" in capsys.readouterr().out
    assert checkout.lifecycle.context is checkout.execution_context
    assert not any(call[:2] == ["git", "add"] for call in repository.calls)


@pytest.mark.parametrize("phase", ["PAUSED", "UNKNOWN", "FINISHED"])
def test_execution_handoff_uses_existing_state_and_confirmed_run(
    tmp_path,
    monkeypatch,
    capsys,
    phase,
):
    from engineering_ci.execution import cli as execution_cli

    monkeypatch.setattr(execution_cli, "default_cache_dir", lambda: tmp_path)
    inputs = {"version": "v" + VERSION, "publish": "true", "pytest_scenario": "pass"}
    identity = DispatchIdentity.create(REPOSITORY, "release.yml", "main", inputs)
    state = TaskRecord(
        identity,
        CollectionIdentity.create(tmp_path, no_artifacts=True),
        phase=phase,
        run_id=None if phase == "UNKNOWN" else 45,
        paused_from="RUNNING" if phase == "PAUSED" else None,
        remote_status="completed" if phase == "FINISHED" else None,
        remote_conclusion="failure" if phase == "FINISHED" else None,
        collection_complete=phase == "FINISHED",
    )
    store = StateStore(tmp_path / "runs" / f"{identity.key}.json")
    store.save(state)
    from argparse import Namespace

    cli.execution_checkpoint(
        Namespace(command="publish", repo=REPOSITORY, version=VERSION), inputs, "main"
    )
    output = capsys.readouterr().out
    assert phase in output and str(store.path) in output
    if phase == "UNKNOWN":
        assert "no automatic second POST" in output and "--resume" not in output
    else:
        assert "/actions/runs/45" in output
        assert ("publish" if phase == "FINISHED" else "--resume") in output
        assert "45" in output


def test_status_recovers_run_id_and_live_result_without_execution(
    repository,
    tmp_path,
    monkeypatch,
    capsys,
):
    from argparse import Namespace

    from test_release_verification import API

    from engineering_ci.execution import cli as execution_cli

    api = API()
    api.run["conclusion"] = "failure"
    checkout = delivery.Checkout(REPOSITORY, root=repository.root, run=repository.run)
    monkeypatch.setattr(delivery, "Checkout", lambda repository: checkout)
    monkeypatch.setattr(cli, "GitHub", lambda repository: api)
    monkeypatch.setattr(execution_cli, "default_cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(
        checks,
        "execute",
        lambda *args, **kwargs: pytest.fail("Status cannot execute checks"),
    )
    identity = DispatchIdentity.create(
        REPOSITORY,
        "release.yml",
        "main",
        {
            "version": "v" + VERSION,
            "publish": "true",
            "pytest_scenario": "pass",
        },
    )
    store = StateStore(tmp_path / "cache/runs" / f"{identity.key}.json")
    store.save(
        TaskRecord(
            identity,
            CollectionIdentity.create(tmp_path, no_artifacts=True),
            phase="PAUSED",
            paused_from="RUNNING",
            run_id=123,
        )
    )
    before = store.path.read_bytes()
    assert cli.show_status(Namespace(repo=REPOSITORY, version=VERSION, diff=False)) == 0
    output = capsys.readouterr().out
    assert "completed / failure" in output and "--resume" in output and "123" in output
    assert store.path.read_bytes() == before and not api.writes


def test_status_published_release_reports_original_evidence_without_workflow(
    repository,
    monkeypatch,
    capsys,
):
    from argparse import Namespace

    checkout = delivery.Checkout(REPOSITORY, root=repository.root, run=repository.run)
    monkeypatch.setattr(delivery, "Checkout", lambda repository: checkout)
    monkeypatch.setattr(
        cli.verification,
        "published",
        lambda *args: {
            "status": "published",
            "release_url": f"https://github.com/{REPOSITORY}/releases/tag/v{VERSION}",
            "commit_sha": "a" * 40,
            "manifest": {
                "workflow": {
                    "run_url": f"https://github.com/{REPOSITORY}/actions/runs/123",
                    "run_attempt": 1,
                }
            },
        },
    )
    monkeypatch.setattr(
        cli, "entrypoint", lambda *args: pytest.fail("Status cannot dispatch")
    )
    assert cli.show_status(Namespace(repo=REPOSITORY, version=VERSION, diff=False)) == 0
    output = capsys.readouterr().out
    assert "Completed: published v0.4.11" in output and "attempt: 1" in output


def test_status_corrupt_local_report_still_shows_actual_pr_stop(
    repository,
    tmp_path,
    monkeypatch,
    capsys,
):
    from argparse import Namespace

    from engineering_ci.execution import cli as execution_cli

    repository.existing_pr()
    checkout = delivery.Checkout(REPOSITORY, root=repository.root, run=repository.run)
    monkeypatch.setattr(delivery, "Checkout", lambda repository: checkout)
    monkeypatch.setattr(execution_cli, "default_cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(cli.verification, "published", lambda *args: None)
    path = repository.root / ".test-tools/checks/pr.json"
    path.parent.mkdir(parents=True)
    path.write_text("bad json")
    assert cli.show_status(Namespace(repo=REPOSITORY, version=VERSION, diff=False)) == 0
    output = capsys.readouterr().out
    assert "report unavailable or invalid" in output and "Stopped at delivery" in output
    assert repository.pr["url"] in output


def test_status_shows_missing_tool_without_inventing_commit_message(
    repository, monkeypatch, capsys
):
    from argparse import Namespace

    repository.existing_pr()
    checkout = delivery.Checkout(REPOSITORY, root=repository.root, run=repository.run)
    monkeypatch.setattr(delivery, "Checkout", lambda repository: checkout)
    monkeypatch.setattr(cli.verification, "published", lambda *args: None)
    monkeypatch.setattr(cli, "execution_checkpoint", lambda *args, **kwargs: False)
    path = repository.root / ".test-tools/checks/pr.json"
    checks.atomic_json(path, {})
    monkeypatch.setattr(
        checks,
        "inspect",
        lambda *args, **kwargs: (
            {
                "checks": [
                    {
                        "name": "pytest",
                        "status": "missing_tool",
                        "reason": "uv is required",
                        "freshness": "not_run",
                    }
                ]
            },
            3,
        ),
    )
    assert cli.show_status(Namespace(repo=REPOSITORY, version=VERSION, diff=False)) == 0
    output = capsys.readouterr().out
    assert "Local check pytest [unknown]: missing_tool" in output
    assert "uv is required" in output
    assert "your git commit -m message" in output
    commands = [line for line in output.splitlines() if line.startswith("Continue:")]
    assert commands and all(
        "Existing change" not in line and " -m " not in line for line in commands
    )
    assert not any(
        call[:2] in (["git", "add"], ["git", "commit"], ["git", "push"])
        for call in repository.calls
    )


@pytest.fixture
def saved_publication(tmp_path, monkeypatch):
    from test_release_verification import API

    from engineering_ci.execution import cli as execution_cli

    api = API()
    api.run["conclusion"] = "failure"
    monkeypatch.setattr(cli, "GitHub", lambda repository: api)
    monkeypatch.setattr(execution_cli, "default_cache_dir", lambda: tmp_path / "cache")
    inputs = {"version": "v" + VERSION, "publish": "true", "pytest_scenario": "pass"}
    identity = DispatchIdentity.create(REPOSITORY, "release.yml", "main", inputs)
    store = StateStore(tmp_path / "cache/runs" / f"{identity.key}.json")
    store.save(
        TaskRecord(
            identity,
            CollectionIdentity.create(tmp_path, no_artifacts=True),
            phase="FINISHED",
            run_id=123,
            remote_status="completed",
            remote_conclusion="failure",
            collection_complete=True,
        )
    )
    return api, inputs, store


@pytest.mark.parametrize("mismatch", ["version", "commit"])
def test_old_checkpoint_is_historical_without_pr_merge_advice(
    saved_publication, capsys, mismatch
):
    from argparse import Namespace

    api, inputs, store = saved_publication
    if mismatch == "version":
        api.versions = dict.fromkeys(api.versions, "0.4.9")
    before = store.path.read_bytes()
    assert not cli.execution_checkpoint(
        Namespace(command="publish", repo=REPOSITORY, version=VERSION),
        inputs,
        "main",
        api=api,
        expected_head="b" * 40,
    )
    output = capsys.readouterr().out
    assert "Historical Run" in output
    assert (
        "Continue:" not in output and "complete its development PR first" not in output
    )
    assert store.path.read_bytes() == before and not api.writes


@pytest.mark.parametrize("dirty", [False, True])
def test_merged_pr_status_prioritizes_undelivered_files_over_old_run(
    repository, saved_publication, monkeypatch, capsys, dirty
):
    from argparse import Namespace

    api, _, store = saved_publication
    api.versions = dict.fromkeys(api.versions, "0.4.9")
    if not dirty:
        repository.git("add", ".")
        repository.git("commit", "-m", "Reviewed changes")
    repository.existing_pr(state="MERGED")
    checkout = delivery.Checkout(REPOSITORY, root=repository.root, run=repository.run)
    monkeypatch.setattr(delivery, "Checkout", lambda repository: checkout)
    monkeypatch.setattr(
        cli,
        "resolve_target",
        lambda *args: {
            "tag": "v" + VERSION,
            "commit_sha": repository.git("rev-parse", "main"),
        },
    )
    before = store.path.read_bytes()
    assert cli.show_status(Namespace(repo=REPOSITORY, version=VERSION, diff=False)) == 0
    output = capsys.readouterr().out
    assert "Human merge confirmed" in output and "Historical Run" in output
    if dirty:
        assert "local changes were not included" in output
        assert "follow-up development PR is needed" in output
        assert "Continue:" not in output
    else:
        assert "Remote main includes the reviewed PR" in output
        assert "Next step: request complete validation and publication" in output
        assert "publish --repo dymzz/engineering-ci --version v0.4.11" in output
        assert "--resume" not in output
    assert store.path.read_bytes() == before and not api.writes
    assert not any(
        call[:2] in (["git", "push"], ["git", "commit"], ["git", "add"])
        for call in repository.calls
    )
