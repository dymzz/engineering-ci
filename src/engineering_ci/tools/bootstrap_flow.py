"""Continue consumer onboarding using native uv, Dagger, Git and GitHub commands."""

import hashlib
import json
import os
import re
import sys
from pathlib import Path

from . import bootstrap_templates as template
from .bootstrap_common import BootstrapError, owned


def setup_steps():
    """Keep printed next actions and executed setup commands in one definition."""
    return (
        ("root-lock", ["uv", "lock"]),
        ("root-sync", ["uv", "sync", "--locked"]),
        (
            "sdk",
            [
                "dagger",
                "api",
                "call",
                "--no-load-module",
                "module-source",
                "--ref-string",
                ".",
                "generated-context-directory",
                "directory",
                "--path",
                ".dagger",
                "export",
                "--path",
                ".dagger",
            ],
        ),
        ("module-lock", ["uv", "lock", "--project", ".dagger"]),
        ("check-list", ["dagger", "check", "-l", "--format", "cli"]),
        ("dagger-check", ["dagger", "check"]),
    )


def accept(reader, prompt):
    """Batch execution has an explicit flag grant; interactive writes ask separately."""
    return reader is None or reader(prompt).lower() == "yes"


def record(result, name, action):
    """Preserve completed operations and the original failure code on every stop."""
    item = {"name": name, "status": "running", "exit_code": None}
    result.setdefault("operations", []).append(item)
    try:
        value = action()
    except BootstrapError as error:
        item.update(status="failed", exit_code=error.code)
        raise
    except KeyboardInterrupt:
        item.update(status="interrupted", exit_code=130)
        raise
    item.update(status="passed", exit_code=0)
    return value


def sdk_files(root):
    """Include generated SDK source, even when the generator ignores its directory."""
    from ..checks import IGNORED_DIRS

    sdk = owned(root, ".dagger/sdk")
    paths = []
    for directory, dirs, files in os.walk(sdk, followlinks=False):
        dirs[:] = sorted(name for name in dirs if name not in IGNORED_DIRS)
        for name in dirs:
            owned(root, (Path(directory) / name).relative_to(root))
        for name in files:
            if not name.endswith((".pyc", ".pyo")) and not name.startswith(".env"):
                paths.append((Path(directory) / name).relative_to(root).as_posix())
    return paths


def inputs(root, commands, tools):
    """Bind this invocation to actual Git source and environment, without saving content."""
    from ..checks import environment_identity

    raw = commands.execute(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], root
    ).stdout
    files = []
    for relative in sorted(set(filter(None, raw.split("\0"))) | set(sdk_files(root))):
        path = owned(root, relative)
        if not path.exists():
            files.append((relative, None))
            continue
        before = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        after = path.stat()

        def observation(info):
            return (info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode)

        if observation(before) != observation(after):
            raise BootstrapError("Project inputs changed while reading; rerun checks")
        files.append((relative, digest.hexdigest(), observation(after)))
    transport = {
        key: value
        for key, value in os.environ.items()
        if key.startswith(("DAGGER_", "DOCKER_"))
    }
    value = [files, tools, environment_identity(os.environ), transport]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def tool_versions(root, commands):
    """Use the existing version parser and refuse a mismatched Dagger CLI."""
    from ..toolchain import dagger_cli_version

    commands.step = "setup-preflight"
    uv = commands.execute(["uv", "--version"], root).stdout
    match = re.search(r"\buv (\d+\.\d+\.\d+)", uv)
    if not match:
        raise BootstrapError("Unable to identify the uv version", 2)
    dagger = dagger_cli_version(
        commands.execute(["dagger", "version"], root, timeout=30).stdout
    )
    if dagger != template.ENGINE:
        raise BootstrapError(
            f"Consumer requires Dagger {template.ENGINE}; found {dagger}. Update this terminal's PATH",
            2,
        )
    return {"uv": match[1], "dagger": dagger}


def require_current(root, commands, result):
    """Reject source, configuration or tool changes after the actual check."""
    setup = result["local_setup"]
    step = commands.step
    now = tool_versions(root, commands)
    commands.step = step
    if setup.get("input_sha256") != inputs(root, commands, now):
        result["checks"] = "stale"
        raise BootstrapError(
            "Project inputs/tools/environment changed after checks; no further delivery operation was allowed"
        )


def align_history(root, commands, result, details, reader):
    """Delegate history integration and conflict handling to native Git."""
    commands.step = "history-merge"
    if commands.git(root, "rev-parse", "-q", "--verify", "MERGE_HEAD", allowed=(0, 1)):
        raise BootstrapError(
            "Git merge is unfinished; resolve conflicts and git merge --continue before resuming"
        )
    if not details["requires_history_merge"]:
        return
    commands.git(root, "fetch", "origin", "refs/heads/main")
    ancestor = commands.execute(
        ["git", "merge-base", "--is-ancestor", "FETCH_HEAD", "HEAD"],
        root,
        allowed=(0, 1),
    )
    if ancestor.returncode == 0:
        return
    if not accept(
        reader,
        "需要合并已有 main 历史。执行 git merge --allow-unrelated-histories --no-edit FETCH_HEAD？输入 yes 确认: ",
    ):
        raise BootstrapError(
            "Stopped before history merge; local files and remote history were preserved",
            0,
        )
    record(
        result,
        "history-merge",
        lambda: commands.execute(
            ["git", "merge", "--allow-unrelated-histories", "--no-edit", "FETCH_HEAD"],
            root,
            stream=True,
            timeout=None,
        ),
    )


def prepare(root, commands, result):
    """Generate real dependencies/SDK, then invoke Dagger's checks exactly once."""
    result["local_setup"] = {
        "status": "running",
        "steps": [
            {"name": name, "argv": args, "status": "not_run", "exit_code": None}
            for name, args in setup_steps()
        ],
    }
    setup = result["local_setup"]
    setup["tools"] = tool_versions(root, commands)
    for item in setup["steps"]:
        commands.step = item["name"]
        print(
            f"[{item['name']}] {' '.join(item['argv'])}\n  cwd: {root}",
            file=sys.stderr,
            flush=True,
        )
        if item["name"] == "dagger-check":
            setup["input_sha256"] = inputs(root, commands, setup["tools"])
        item["status"] = "running"
        try:
            execution = commands.execute(item["argv"], root, stream=True, timeout=None)
            item["tool_exit_code"] = execution.returncode
            if (
                item["name"] == "sdk"
                and not owned(root, ".dagger/sdk/pyproject.toml").is_file()
            ):
                raise BootstrapError(
                    "Dagger returned success but generated SDK is missing"
                )
        except BootstrapError as error:
            item.update(status="failed", exit_code=error.code)
            setup["status"] = "failed"
            if item["name"] == "dagger-check":
                result["checks"] = "failed"
            raise
        except KeyboardInterrupt:
            item.update(status="interrupted", exit_code=130)
            setup["status"] = "interrupted"
            raise
        item.update(status="passed", exit_code=0)
    result["checks"] = "passed"
    require_current(root, commands, result)
    setup["status"] = "passed"
    print("本地 Dagger 检查通过；报告/Artifact 不另行运行 pytest。", file=sys.stderr)


def pull_requests(root, repo, commands):
    """Query the exact branch before creating or recovering a Draft PR."""
    value = json.loads(
        commands.execute(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repo,
                "--head",
                template.BRANCH,
                "--base",
                "main",
                "--state",
                "all",
                "--json",
                "number,url,state,isDraft,headRefOid,baseRefName",
            ],
            root,
        ).stdout
    )
    if (
        not isinstance(value, list)
        or len(value) > 1
        or any(not isinstance(pr, dict) for pr in value)
    ):
        raise BootstrapError(
            "PR query is ambiguous; inspect the existing branch before continuing"
        )
    if value and value[0].get("state") != "OPEN":
        raise BootstrapError(
            "This bootstrap branch already has a closed/merged PR; preserve local work and choose follow-up delivery"
        )
    return value[0] if value else None


def deliver(root, context, commands, result, reader, message):
    """Confirm commit, normal Push and Draft creation independently; never merge."""
    repo = context["repository"]
    commands.step = "delivery-preflight"
    if commands.git(root, "branch", "--show-current") != template.BRANCH:
        raise BootstrapError(
            "Current branch changed; no delivery operation was allowed"
        )
    pr = pull_requests(root, repo, commands)
    changes = commands.git(root, "status", "--short")
    if changes:
        print("待交付文件（只在目标新仓内）:\n" + changes, file=sys.stderr)
        commands.step = "git-add"
        if not accept(reader, "暂存上述改动及生成 SDK：git add --all？输入 yes 确认: "):
            raise BootstrapError(
                "Stopped before git add; generated files were preserved", 0
            )
        require_current(root, commands, result)
        commands.step = "git-add"
        record(result, "git-add", lambda: commands.git(root, "add", "--all"))
        sdk = sdk_files(root)
        if sdk:
            record(
                result,
                "git-add-sdk",
                lambda: commands.execute(
                    [
                        "git",
                        "add",
                        "-f",
                        "--pathspec-from-file=-",
                        "--pathspec-file-nul",
                    ],
                    root,
                    input="\0".join(sdk) + "\0",
                ),
            )
        print(commands.git(root, "diff", "--cached", "--stat"), file=sys.stderr)
        print(
            f'可在另一终端查看完整差异：git -C "{root}" diff --cached', file=sys.stderr
        )
        commands.step = "git-commit"
        message = message or (reader("git commit -m 的提交信息: ") if reader else None)
        if not message or not message.strip():
            raise BootstrapError(
                "Commit message is required; provide -m/--message or enter it interactively"
            )
        if not accept(reader, "审查暂存差异后执行 git commit？输入 yes 确认: "):
            raise BootstrapError(
                "Stopped before git commit; staged files were preserved", 0
            )
        require_current(root, commands, result)
        commands.step = "git-commit"
        record(
            result,
            "git-commit",
            lambda: commands.execute(
                ["git", "commit", "-m", message], root, stream=True, timeout=None
            ),
        )
    head = commands.git(root, "rev-parse", "HEAD")
    result["delivery"] = {"commit_sha": head, "branch": template.BRANCH}
    require_current(root, commands, result)
    if commands.git(root, "status", "--porcelain"):
        result["checks"] = "stale"
        raise BootstrapError(
            "The checked working tree does not match the commit; inspect git status before delivery"
        )
    commands.step = "git-push"
    if commands.git(
        root, "ls-remote", "origin", "refs/heads/" + template.BRANCH
    ).split() != [head, "refs/heads/" + template.BRANCH]:
        if not accept(
            reader, f"Push {repo}@{template.BRANCH} ({head[:12]})？输入 yes 确认: "
        ):
            raise BootstrapError(
                "Stopped before git push; the local commit was preserved", 0
            )
        require_current(root, commands, result)
        commands.step = "git-push"
        record(
            result,
            "git-push",
            lambda: commands.execute(
                ["git", "push", "-u", "origin", template.BRANCH],
                root,
                stream=True,
                timeout=None,
            ),
        )
    if commands.git(
        root, "ls-remote", "origin", "refs/heads/" + template.BRANCH
    ).split() != [head, "refs/heads/" + template.BRANCH]:
        raise BootstrapError(
            "Remote feature head could not be confirmed; no PR was created"
        )
    require_current(root, commands, result)
    commands.step = "draft-pr"
    pr = pull_requests(root, repo, commands) or pr
    if not pr:
        if not accept(
            reader, f"为 {repo}@{head[:12]} 创建 Draft PR（base main）？输入 yes 确认: "
        ):
            raise BootstrapError(
                "Stopped before Draft PR creation; the pushed branch was preserved", 0
            )
        require_current(root, commands, result)
        commands.step = "draft-pr"
        body = owned(root, ".test-tools/bootstrap-pr-body.md")
        body.write_text(
            f"接入 engineering-ci {template.VERSION}，固定 Workflow 和 Dagger 模块为 {template.SHA}。\n\n本地执行 uv/SDK 准备及原生 dagger check 通过；源码摘要 {result['local_setup']['input_sha256']}。远端 PR CI 尚未确认，需审查后决定合并。\n",
            encoding="utf-8",
        )
        result["pull_request"] = "unconfirmed"
        record(
            result,
            "draft-pr",
            lambda: commands.execute(
                [
                    "gh",
                    "pr",
                    "create",
                    "--repo",
                    repo,
                    "--base",
                    "main",
                    "--head",
                    template.BRANCH,
                    "--draft",
                    "--title",
                    "ci: 接入共享 Python 检查",
                    "--body-file",
                    str(body),
                ],
                root,
            ),
        )
        pr = pull_requests(root, repo, commands)
    if not pr or pr.get("headRefOid") != head or pr.get("baseRefName") != "main":
        raise BootstrapError(
            "PR head/base could not be confirmed; query the existing branch before retrying"
        )
    if (
        type(pr.get("number")) is not int
        or pr["number"] <= 0
        or not isinstance(pr.get("url"), str)
        or type(pr.get("isDraft")) is not bool
        or pr.get("url", "").lower()
        != f"https://github.com/{repo}/pull/{pr['number']}".lower()
    ):
        raise BootstrapError("PR identity is invalid")
    result.update(
        status="draft_pr" if pr.get("isDraft") else "pr_review",
        pull_request="confirmed",
        remote_ci="not_inspected",
        pr=pr,
        stop={
            "code": "human_review",
            "reason": "Review this PR and its actual CI; no merge or release was requested",
        },
    )
    result["next_actions"] = [
        {
            "cwd": str(root),
            "argv": [
                "gh",
                "pr",
                "checks",
                str(pr["number"]),
                "--repo",
                repo,
                "--watch",
            ],
        }
    ]
    print(
        f"已停在 PR 审查：{pr['url']}\n提交: {head}\n远端 CI 尚需查询；未合并、未发布。",
        file=sys.stderr,
    )


def continue_project(context, commands, result, mode, reader, message, connect):
    """Run the selected fixed onboarding sequence; Dagger owns check scheduling/cache."""
    root = Path(context["path"])
    if mode == "deliver":
        details = connect()
        align_history(root, commands, result, details, reader)
    prepare(root, commands, result)
    if mode == "deliver":
        deliver(root, context, commands, result, reader, message)
    else:
        result.update(
            status="checked",
            stop={
                "code": "local_checks_complete",
                "reason": "Local checks passed; GitHub delivery was not requested",
            },
        )
