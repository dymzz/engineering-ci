"""Interactively scaffold a new consumer; resume only the explicitly owned target."""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from filelock import FileLock, Timeout

from .._paths import ROOT
from ..execution.store import atomic_json
from . import bootstrap_templates as template
from .bootstrap_common import BootstrapError, Commands, owned
from .bootstrap_flow import continue_project, setup_steps

CHECKPOINT = ".test-tools/bootstrap.json"
GIT_SELECTORS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")


def validate_repository(repository):
    """Validate GitHub OWNER/name separately from the local project directory."""
    if not re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/[a-z][a-z0-9]*(?:-[a-z0-9]+)*",
        repository,
    ):
        raise BootstrapError(
            "Use GitHub OWNER/lowercase-kebab-name, e.g. dymzz/my-python-app; "
            "a local filesystem path belongs in the directory prompt"
        )
    name = repository.split("/")[1]
    if len(name) > 63 or name.upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        raise BootstrapError("Repository name is not portable across Windows and Linux")
    return repository


def plan(repository, path):
    """Accept portable names and an explicit absolute destination."""
    repository = validate_repository(repository)
    path = destination(path)
    return {
        "schema_version": 2,
        "kind": "engineering-ci-bootstrap",
        "repository": repository,
        "path": str(path),
        "version": template.VERSION,
        "sha": template.SHA,
        "seed_sha": None,
        "remote_intent": False,
        "remote_id": None,
        "remote_mode": None,
        "overwrite_main_sha": None,
    }


def destination(path):
    """Keep explicit Windows/Linux paths and reject redirected destinations."""
    value = os.fspath(path).strip()
    if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
        value = value[1:-1]
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise BootstrapError(
            "Destination must be an explicit absolute path for this OS"
        )
    if any(part.is_symlink() for part in [path, *path.parents]):
        raise BootstrapError("Destination must not be redirected through a symlink")
    return path.resolve()


def new_destination(path):
    """Reject existing projects before asking for GitHub details or creating files."""
    root = destination(path)
    if root.exists():
        raise BootstrapError(
            "Destination already exists; no files were changed. "
            "Choose a nonexistent project directory; --resume only accepts this "
            "tool's .test-tools/bootstrap.json, not an arbitrary existing project"
        )
    if not root.parent.is_dir():
        raise BootstrapError(
            "Destination parent does not exist; choose or create it explicitly"
        )
    return root


def read_field(prompt, reader, validate):
    """Explain invalid interactive input and retry that field without side effects."""
    while True:
        try:
            return validate(reader(prompt))
        except BootstrapError as error:
            print(
                f"输入未通过：{error}\n请重新输入，或按 Ctrl+C 取消。", file=sys.stderr
            )


def checkpoint(root):
    """Load only this tool's bounded recovery context, never arbitrary commands."""
    path = owned(root, CHECKPOINT)
    if not path.is_file():
        raise BootstrapError(
            "No bootstrap checkpoint at .test-tools/bootstrap.json; "
            "--resume cannot adopt an existing project"
        )
    if path.stat().st_size > 8192:
        raise BootstrapError("Bootstrap checkpoint is oversized")
    value = json.loads(path.read_text(encoding="utf-8"))
    expected = plan(value["repository"], root)
    if type(value.get("schema_version")) is int and value["schema_version"] == 1:
        if set(value) != set(expected) - {"remote_mode", "overwrite_main_sha"}:
            raise BootstrapError("Invalid legacy bootstrap checkpoint")
        value.update(
            schema_version=2,
            remote_mode="create" if value["remote_intent"] else None,
            overwrite_main_sha=None,
        )
    if set(value) != set(expected) or any(
        value[key] != expected[key]
        for key in ("schema_version", "kind", "path", "version", "sha")
    ):
        raise BootstrapError("Checkpoint identity or template baseline differs")
    if (
        type(value["schema_version"]) is not int
        or type(value["remote_intent"]) is not bool
        or (
            value["remote_id"] is not None
            and (type(value["remote_id"]) is not int or value["remote_id"] <= 0)
        )
        or value["remote_mode"] not in {None, "create", "connect", "overwrite"}
        or value["remote_intent"] != (value["remote_mode"] == "create")
        or (value["remote_mode"] is None and value["remote_id"] is not None)
        or (
            value["remote_mode"] in {"connect", "overwrite"}
            and value["remote_id"] is None
        )
        or (
            value["remote_mode"] == "overwrite"
            and (
                not isinstance(value["overwrite_main_sha"], str)
                or not re.fullmatch(r"[a-f0-9]{40}", value["overwrite_main_sha"])
            )
        )
        or (
            value["remote_mode"] != "overwrite"
            and value["overwrite_main_sha"] is not None
        )
        or (
            value["seed_sha"] is not None
            and not re.fullmatch(r"[a-f0-9]{40}", value["seed_sha"])
        )
    ):
        raise BootstrapError("Invalid bootstrap recovery facts")
    return value


def save(root, context):
    atomic_json(owned(root, CHECKPOINT), context)


def local(context, commands):
    """Initialize native Git and preserve existing files during explicit recovery."""
    root = Path(context["path"])
    if (root / ".git").is_symlink() or (root / ".git").is_file():
        raise BootstrapError(
            "Destination has redirected Git metadata; inspect manually"
        )
    if not (root / ".git").exists():
        commands.git(root, "init", "--object-format=sha1", "-b", "main")
    if Path(commands.git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise BootstrapError("Destination is not its own Git repository")
    changed = []
    for relative, content in template.files(context["repository"]).items():
        path = owned(root, relative)
        if path.exists():
            if path.read_text(encoding="utf-8") != content:
                changed.append(relative)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
    commands.step = "initial_commit"
    head = commands.git(root, "rev-parse", "--verify", "HEAD", allowed=(0, 128))
    if not re.fullmatch(r"[a-f0-9]{40}", head):
        if set(commands.git(root, "diff", "--cached", "--name-only").splitlines()) - {
            "README.md"
        }:
            raise BootstrapError(
                "Index already contains staged files; inspect before initial commit"
            )
        commands.git(root, "add", "--", "README.md")
        commands.git(
            root,
            "commit",
            "-m",
            "chore: initialize repository",
            "--only",
            "--",
            "README.md",
        )
        head = commands.git(root, "rev-parse", "HEAD")
    if context["seed_sha"] is None:
        if commands.git(root, "ls-tree", "--name-only", head) != "README.md":
            raise BootstrapError("Cannot identify the original README-only seed commit")
        context["seed_sha"] = head
        save(root, context)
    if commands.git(root, "rev-parse", "main") != context["seed_sha"]:
        raise BootstrapError("Local main changed; inspect before remote initialization")
    branch = commands.git(root, "branch", "--show-current")
    if branch == "main":
        exists = commands.git(root, "branch", "--list", template.BRANCH)
        commands.git(
            root, "switch", *([template.BRANCH] if exists else ["-c", template.BRANCH])
        )
    elif branch != template.BRANCH:
        raise BootstrapError(
            "Unexpected branch; manually select codex/bootstrap-ci before resuming"
        )
    return changed


def remote_ref(root, commands, remote, ref):
    """Read one actual Git ref; reject ambiguous or malformed responses."""
    value = commands.git(root, "ls-remote", remote, ref).split()
    if not value:
        return None
    if (
        len(value) != 2
        or value[1] != ref
        or not re.fullmatch(r"[a-f0-9]{40}", value[0])
    ):
        raise BootstrapError("Remote Git ref response is invalid", step=commands.step)
    return value[0]


def validate_remote(context, remote, *, push=False):
    """Bind private repository identity without copying raw API responses."""
    if (
        not remote
        or remote.get("private") is not True
        or remote.get("full_name", "").lower() != context["repository"].lower()
        or type(remote.get("id")) is not int
        or remote["id"] <= 0
        or (context["remote_id"] is not None and remote["id"] != context["remote_id"])
    ):
        raise BootstrapError("Remote repository identity or visibility changed")
    if push and remote.get("permissions", {}).get("push") is not True:
        raise BootstrapError("Existing repository does not grant push permission")


def backup_ref(sha):
    return f"refs/heads/codex/bootstrap-backup/{sha}"


def choose_existing(context, remote, commands, policy, expected_sha, reader):
    """Require a concrete choice before connecting or replacing an existing main."""
    commands.step = "github_existing"
    validate_remote(context, remote, push=True)
    if remote.get("default_branch", "main") != "main":
        raise BootstrapError(
            "Existing default branch is not main; inspect before connecting"
        )
    root, repo = Path(context["path"]), context["repository"]
    head = remote_ref(
        root, commands, f"https://github.com/{repo}.git", "refs/heads/main"
    )
    observed = {
        "id": remote["id"],
        "full_name": repo,
        "private": True,
        "main_sha": head,
        "seed_sha": context["seed_sha"],
    }
    if reader and policy is None:
        print(
            f"已有私有仓库: {repo} (ID {remote['id']})\n远端 main: {head or '空'}\n本地 README 初始提交: {context['seed_sha']}",
            file=sys.stderr,
        )
        choices = {
            "1": "connect",
            "connect": "connect",
            "2": "overwrite",
            "overwrite": "overwrite",
            "": "cancel",
            "q": "cancel",
        }
        while policy is None:
            choice = reader(
                "选择：1 连接并保留历史 / 2 覆盖 main（先备份） / q 取消 [q]: "
            )
            policy = choices.get(choice.lower())
    if policy is None:
        raise BootstrapError(
            "Remote repository already exists; select --existing-remote connect or overwrite explicitly",
            observed=observed,
        )
    if policy == "cancel":
        raise BootstrapError(
            "Remote action cancelled; local scaffold was preserved",
            0,
            observed=observed,
        )
    if policy == "overwrite":
        if head is None:
            raise BootstrapError(
                "Remote main is empty; choose connect to initialize it",
                observed=observed,
            )
        if expected_sha is None and reader is None:
            raise BootstrapError(
                "Overwrite requires --expected-main-sha matching the observed full SHA",
                observed=observed,
            )
        if expected_sha is not None and expected_sha != head:
            raise BootstrapError(
                "Remote main differs from the supplied expected SHA; no overwrite attempted",
                observed=observed,
            )
        expected_sha = head
        if reader:
            confirmation = f"overwrite {repo} {head[:12]}"
            print(
                f"覆盖仅替换 main：{head} → {context['seed_sha']}（仅 README 的初始提交）。\n先备份旧 main 到 {backup_ref(head)}；后续 CI/业务文件仍需单独提交 PR。",
                file=sys.stderr,
            )
            if reader(f"确认上述覆盖请输入 {confirmation}: ") != confirmation:
                raise BootstrapError(
                    "Overwrite cancelled; remote history was preserved",
                    0,
                    observed=observed,
                )
        if head == context["seed_sha"]:
            policy, expected_sha = "connect", None
    context.update(
        remote_id=remote["id"],
        remote_mode=policy,
        remote_intent=False,
        overwrite_main_sha=expected_sha if policy == "overwrite" else None,
    )
    save(root, context)


def overwrite_main(context, commands, head):
    """Back up the exact old main, then replace it using Git's compare-and-swap lease."""
    root, repo = Path(context["path"]), context["repository"]
    old, seed = context["overwrite_main_sha"], context["seed_sha"]
    ref = backup_ref(old)
    commands.step = "backup_main"
    remote = commands.api(f"repos/{repo}")
    validate_remote(context, remote, push=True)
    saved = remote_ref(root, commands, "origin", ref)
    if head == seed:
        if saved != old:
            raise BootstrapError(
                "Main matches the seed but its required backup cannot be verified"
            )
        return ref
    if head != old:
        raise BootstrapError(
            "Remote main changed since confirmation; no overwrite attempted"
        )
    if saved is not None and saved != old:
        raise BootstrapError(
            "Backup branch already points to a different commit; refusing to overwrite it"
        )
    if saved is None:
        commands.git(root, "fetch", "--no-tags", "origin", "refs/heads/main")
        if commands.git(root, "rev-parse", "--verify", "FETCH_HEAD") != old:
            raise BootstrapError(
                "Remote main changed while fetching; no overwrite attempted"
            )
        commands.git(
            root, "push", f"--force-with-lease={ref}:", "origin", f"{old}:{ref}"
        )
    if remote_ref(root, commands, "origin", ref) != old:
        raise BootstrapError("Backup could not be verified; main was not overwritten")
    commands.step = "overwrite_main"
    validate_remote(context, commands.api(f"repos/{repo}"), push=True)
    commands.git(
        root,
        "push",
        f"--force-with-lease=refs/heads/main:{old}",
        "origin",
        f"{seed}:refs/heads/main",
    )
    return ref


def github(
    context,
    commands,
    *,
    confirm_remote_id=None,
    existing_policy=None,
    expected_main_sha=None,
    reader=None,
):
    """Confirm actual access and pin; create once, then push only the seed main."""
    root, repo = Path(context["path"]), context["repository"]
    commands.step = "github_preflight"
    commands.api(
        "user"
    )  # Existing authentication only; never sign in or switch accounts.
    upstream = commands.api(f"repos/{template.UPSTREAM}")
    if upstream.get("private"):
        if repo.split("/")[0].lower() != upstream["owner"]["login"].lower():
            raise BootstrapError(
                "Private shared Workflow requires the same repository owner"
            )
        access = commands.api(f"repos/{template.UPSTREAM}/actions/permissions/access")
        if access.get("access_level") not in {"user", "organization", "enterprise"}:
            raise BootstrapError(
                "Upstream Workflow sharing is disabled; owner decision required"
            )
    release = commands.api(
        f"repos/{template.UPSTREAM}/releases/tags/{template.VERSION}"
    )
    if release.get("draft") is not False or release.get("tag_name") != template.VERSION:
        raise BootstrapError("Template baseline is not a published Release")
    reference = commands.api(
        f"repos/{template.UPSTREAM}/git/ref/tags/{template.VERSION}"
    )["object"]
    for _ in range(4):
        if reference["type"] != "tag":
            break
        reference = commands.api(
            f"repos/{template.UPSTREAM}/git/tags/{reference['sha']}"
        )["object"]
    if reference.get("type") != "commit" or reference.get("sha") != template.SHA:
        raise BootstrapError("Published Tag does not match the template's verified SHA")
    commands.step = "github_create"
    remote = commands.api(f"repos/{repo}", missing=True)
    if context["remote_intent"] and context["remote_id"] is None:
        if confirm_remote_id is None:
            raise BootstrapError(
                "Remote creation was attempted but not confirmed; inspect gh api repos/OWNER/NAME, then explicitly --confirm-remote-id ID. No second create was sent",
                step=commands.step,
                observed={
                    key: remote.get(key)
                    for key in ("id", "full_name", "private", "html_url")
                }
                if remote
                else None,
            )
        if (
            not remote
            or remote.get("id") != confirm_remote_id
            or remote.get("private") is not True
            or remote.get("full_name", "").lower() != repo.lower()
        ):
            raise BootstrapError(
                "Explicit remote ID does not match the intended private repository",
                step=commands.step,
            )
        context["remote_id"] = confirm_remote_id
        save(root, context)
    elif confirm_remote_id is not None:
        raise BootstrapError(
            "Remote ID confirmation is only for an unconfirmed creation intent",
            step=commands.step,
        )
    if existing_policy and context["remote_mode"] == "create":
        raise BootstrapError(
            "Creation recovery cannot be changed into existing-repository adoption"
        )
    if existing_policy and remote is None:
        raise BootstrapError(
            "Existing-repository policy requested but the remote is absent; no create was attempted"
        )
    if (
        context["remote_mode"] in {"connect", "overwrite"}
        and existing_policy
        and (
            existing_policy != context["remote_mode"]
            or (
                expected_main_sha is not None
                and expected_main_sha != context["overwrite_main_sha"]
            )
        )
    ):
        choose_existing(
            context, remote, commands, existing_policy, expected_main_sha, reader
        )
    if context["remote_id"] is None and remote is not None:
        choose_existing(
            context, remote, commands, existing_policy, expected_main_sha, reader
        )
    if context["remote_id"] is None:
        context["remote_intent"] = True
        context["remote_mode"] = "create"
        save(root, context)  # Durable intent before the side-effecting gh operation.
        try:
            commands.execute(["gh", "repo", "create", repo, "--private"], root)
        except BootstrapError as error:
            if error.http_status in {400, 401, 403, 404, 422}:
                context["remote_intent"] = (
                    False  # Explicit rejection is not an ambiguous creation.
                )
                context["remote_mode"] = None
                save(root, context)
            raise
        remote = commands.api(f"repos/{repo}")
        validate_remote(context, remote)
        context["remote_id"] = remote["id"]
        save(root, context)
    validate_remote(
        context, remote, push=context["remote_mode"] in {"connect", "overwrite"}
    )
    commands.step = "push_seed"
    expected = f"https://github.com/{repo}.git"
    remotes = commands.git(root, "remote").splitlines()
    if remotes and remotes != ["origin"]:
        raise BootstrapError(
            "Unexpected Git remotes; inspect manually", step=commands.step
        )
    if not remotes:
        commands.git(root, "remote", "add", "origin", expected)
    if (
        commands.git(root, "remote", "get-url", "origin") != expected
        or commands.git(root, "remote", "get-url", "--push", "origin") != expected
    ):
        raise BootstrapError(
            "origin differs from the confirmed repository", step=commands.step
        )
    remote_head = remote_ref(root, commands, "origin", "refs/heads/main")
    backup = None
    if context["remote_mode"] == "overwrite":
        backup = overwrite_main(context, commands, remote_head)
    elif remote_head:
        if context["remote_mode"] != "connect" and remote_head != context["seed_sha"]:
            raise BootstrapError(
                "Remote main differs; no force-push will be performed",
                step=commands.step,
            )
    else:
        commands.git(root, "push", "-u", "origin", "main")
    confirmed = remote_ref(root, commands, "origin", "refs/heads/main")
    if confirmed is None or (
        context["remote_mode"] != "connect" and confirmed != context["seed_sha"]
    ):
        raise BootstrapError("Seed push could not be confirmed", step=commands.step)
    if (
        backup
        and remote_ref(root, commands, "origin", backup)
        != context["overwrite_main_sha"]
    ):
        raise BootstrapError(
            "Main was updated but its backup is unavailable; inspect the recorded recovery ref"
        )
    return {
        "mode": context["remote_mode"],
        "main_sha": confirmed,
        "backup_ref": backup,
        "backup_sha": context["overwrite_main_sha"] if backup else None,
        "requires_history_merge": context["remote_mode"] == "connect"
        and confirmed != context["seed_sha"],
    }


def next_actions(context, remote, details=None):
    """Return usable argv without claiming checks, SDK generation or PR creation."""
    root = context["path"]
    actions = []
    if details and details["requires_history_merge"]:
        actions.extend(
            [
                {"cwd": root, "argv": ["git", "fetch", "origin", "refs/heads/main"]},
                {
                    "cwd": root,
                    "argv": [
                        "git",
                        "merge",
                        "--allow-unrelated-histories",
                        "--no-edit",
                        "FETCH_HEAD",
                    ],
                    "manual_review_required": True,
                    "reason": "Preserve existing main history; resolve any conflicts before checks and the PR",
                },
            ]
        )
    if not remote:
        actions.append(
            {
                "cwd": str(ROOT),
                "argv": [
                    "uv",
                    "run",
                    "--locked",
                    "python",
                    "-m",
                    "engineering_ci.tools.bootstrap",
                    "--resume",
                    root,
                    "--github",
                ],
            }
        )
    for _, args in setup_steps():
        actions.append({"cwd": root, "argv": args})
    return actions


def ask(prompt):
    print(prompt, file=sys.stderr, end="", flush=True)
    return input().strip()


def failure_reason(error, step):
    """Describe the failure category and actual stop point without raw responses."""
    if isinstance(error, BootstrapError):
        return str(error)
    if isinstance(error, EOFError):
        return f"Interactive input ended at {step}; confirmation was not completed"
    if isinstance(error, KeyboardInterrupt):
        return f"Cancelled at {step}; preserve any existing files"
    if isinstance(error, OSError):
        return f"Local operation failed at {step} ({type(error).__name__}, errno={error.errno}); preserve the destination"
    return f"Invalid data or unavailable context at {step} ({type(error).__name__}); preserve the destination"


def main(argv=None, *, run=subprocess.run, reader=ask):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="OWNER/lowercase-kebab-name")
    parser.add_argument("--path", help="Explicit absolute new directory")
    parser.add_argument("--resume", help="Resume only this tool's existing scaffold")
    parser.add_argument(
        "--github",
        action="store_true",
        help="Create or explicitly connect to a private remote; existing history needs a separate choice",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm explicitly requested operations, including --setup/--deliver, without prompts",
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Print the plan without invoking tools or writing files",
    )
    parser.add_argument(
        "--confirm-remote-id",
        type=int,
        help="After manual inspection, bind an unconfirmed creation to this exact repository ID",
    )
    parser.add_argument(
        "--existing-remote",
        choices=("connect", "overwrite"),
        help="Explicit action for an existing private repository",
    )
    parser.add_argument(
        "--expected-main-sha",
        help="Full observed main SHA required for non-interactive overwrite",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Install dependencies, generate SDK and run native dagger check",
    )
    parser.add_argument(
        "--deliver",
        action="store_true",
        help="Continue through local checks, commit, normal Push and Draft PR; never merge",
    )
    parser.add_argument(
        "-m", "--message", help="git commit -m message for explicit --deliver"
    )
    args = parser.parse_args(argv)
    if args.resume and (args.repo or args.path):
        parser.error("--resume cannot change --repo or --path")
    if args.confirm_remote_id is not None and (
        not args.resume or not args.github or args.confirm_remote_id <= 0
    ):
        parser.error(
            "--confirm-remote-id requires --resume, --github and a positive ID"
        )
    if args.existing_remote and (not args.github or args.confirm_remote_id is not None):
        parser.error(
            "--existing-remote requires --github and cannot replace uncertain-creation confirmation"
        )
    if args.expected_main_sha is not None and (
        args.existing_remote != "overwrite"
        or not re.fullmatch(r"[a-f0-9]{40}", args.expected_main_sha)
    ):
        parser.error(
            "--expected-main-sha requires --existing-remote overwrite and a full lowercase SHA"
        )
    if args.message is not None and not args.deliver:
        parser.error("-m/--message requires --deliver")
    if args.setup and args.deliver:
        parser.error("Choose --setup or --deliver; --deliver already includes setup")
    if (
        (args.setup or args.deliver)
        and not args.plan
        and not args.yes
        and not sys.stdin.isatty()
    ):
        parser.error("Non-interactive --setup/--deliver requires explicit --yes")
    context, commands = None, Commands(run)
    progress = {}
    try:
        if args.resume:
            commands.step = "resume_context"
            root = destination(args.resume)
            context = checkpoint(root)
        else:
            if (args.yes or args.plan or not sys.stdin.isatty()) and (
                not args.repo or not args.path
            ):
                parser.error("Non-interactive use requires --repo and --path")
            commands.step = "destination"
            root = (
                new_destination(args.path)
                if args.path is not None
                else read_field(
                    "1/2 新仓绝对路径（含新文件夹名，可带引号）: ",
                    reader,
                    new_destination,
                )
            )
            commands.step = "repository_name"
            repository = (
                validate_repository(args.repo)
                if args.repo is not None
                else read_field(
                    "2/2 GitHub 仓库名（OWNER/name，例如 dymzz/my-python-app；不是路径）: ",
                    reader,
                    validate_repository,
                )
            )
            context = plan(repository, root)
        result = {
            "status": "planned",
            "exit_code": 0,
            "context": context,
            "files": sorted(template.files(context["repository"])),
            "github_requested": args.github or args.deliver,
            "continuation": "deliver"
            if args.deliver
            else "setup"
            if args.setup
            else None,
            "checks": "not_run",
            "pull_request": "not_created",
        }
        progress = result
        if args.plan:
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        if (
            not args.yes
            and not args.github
            and not (args.setup or args.deliver)
            and sys.stdin.isatty()
        ):
            commands.step = "remote_choice"
            args.github = (
                reader(
                    "连接 GitHub 私有仓库（不存在则创建，已存在另行选择）？输入 yes 确认 [默认否]: "
                )
                == "yes"
            )
            result["github_requested"] = args.github
        print(
            f"目标: {context['repository']} → {root}\n基线: {template.VERSION} @ {template.SHA}\nGitHub 操作: {result['github_requested']}（已有仓库会单独确认）；SDK/检查/PR 范围: {result['continuation'] or '生成后交互选择'}。",
            file=sys.stderr,
        )
        print(
            "本地 Git: README-only main → codex/bootstrap-ci；文件:\n  "
            + "\n  ".join(result["files"]),
            file=sys.stderr,
        )
        commands.step = "confirmation"
        if (
            not args.yes
            and reader("确认上述操作请输入 create（其他输入取消）: ") != "create"
        ):
            print(json.dumps({"status": "cancelled", "exit_code": 0}))
            return 0
        commands.step = "preflight"
        if any(os.environ.get(key) for key in GIT_SELECTORS):
            raise BootstrapError(
                "Inherited Git repository selectors are set; clear them explicitly first"
            )
        commands.execute(["git", "--version"], root.parent)
        if not args.resume:
            root.mkdir(exist_ok=False)
            save(root, context)
        with FileLock(owned(root, ".test-tools/bootstrap.lock"), timeout=0):
            context = checkpoint(root)
            save(
                root, context
            )  # Persist a validated v1 migration under the existing lock.
            commands.step = "local_scaffold"
            preserved = local(context, commands)
            remote_details = None
            if args.github:
                remote_details = github(
                    context,
                    commands,
                    confirm_remote_id=args.confirm_remote_id,
                    existing_policy=args.existing_remote,
                    expected_main_sha=args.expected_main_sha,
                    reader=reader if not args.yes and sys.stdin.isatty() else None,
                )
            result.update(
                status="generated",
                context=context,
                preserved_changes=preserved,
                remote="confirmed" if args.github else "not_requested",
                remote_details=remote_details,
                checkpoint=str(owned(root, CHECKPOINT)),
                next_actions=next_actions(context, args.github, remote_details),
            )
            mode = "deliver" if args.deliver else "setup" if args.setup else None
            if not mode and not args.yes and sys.stdin.isatty():
                commands.step = "continuation_choice"
                print(
                    "骨架已保存，后续尚未执行。请选择继续范围：\n  1 连接 GitHub → SDK/检查 → 分别确认 Commit、Push、Draft PR\n  2 只准备 SDK 并运行本地 Dagger 检查\n  q 保存当前结果并退出",
                    file=sys.stderr,
                )
                while True:
                    choice = reader("下一阶段（1/2/q，须明确选择）: ").lower()
                    if choice in {"1", "2", "q"}:
                        mode = {"1": "deliver", "2": "setup", "q": None}[choice]
                        break
            if mode:
                result["continuation"] = mode

                def connect():
                    details = remote_details
                    if details is None:
                        details = github(
                            context,
                            commands,
                            reader=reader
                            if not args.yes and sys.stdin.isatty()
                            else None,
                            confirm_remote_id=args.confirm_remote_id,
                            existing_policy=args.existing_remote,
                            expected_main_sha=args.expected_main_sha,
                        )
                    result.update(
                        github_requested=True,
                        remote="confirmed",
                        remote_details=details,
                    )
                    return details

                result["next_actions"] = [
                    {
                        "cwd": str(ROOT),
                        "argv": [
                            "uv",
                            "run",
                            "--locked",
                            "python",
                            "-m",
                            "engineering_ci.tools.bootstrap",
                            "--resume",
                            context["path"],
                            "--" + mode,
                        ],
                    }
                ]
                continue_project(
                    context,
                    commands,
                    result,
                    mode,
                    reader if not args.yes and sys.stdin.isatty() else None,
                    args.message,
                    connect,
                )
        print(
            f"停点: {result.get('stop', {}).get('code', 'generation_only')}；本地检查: {result['checks']}；PR: {result.get('pr', {}).get('url', result['pull_request'])}\n恢复入口: uv run --locked python -m engineering_ci.tools.bootstrap --resume {context['path']}\n已保存的生成状态不代表检查或远端 CI 通过。",
            file=sys.stderr,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (
        BootstrapError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        Timeout,
        EOFError,
        KeyboardInterrupt,
    ) as error:
        code = (
            130 if isinstance(error, KeyboardInterrupt) else getattr(error, "code", 3)
        )
        result = {
            "status": "cancelled" if code == 0 else "stopped",
            "exit_code": code,
            "step": getattr(error, "step", None) or commands.step,
            "reason": failure_reason(error, commands.step),
            "context": context,
            "checks": progress.get("checks", "not_run"),
            "pull_request": progress.get("pull_request", "not_created"),
        }
        for key in (
            "local_setup",
            "operations",
            "delivery",
            "pr",
            "remote_details",
            "remote",
            "github_requested",
            "checkpoint",
            "continuation",
        ):
            if key in progress:
                result[key] = progress[key]
        if "local_setup" in result and result["local_setup"]["status"] == "running":
            result["local_setup"]["status"] = (
                "interrupted" if code == 130 else "incomplete"
            )
        if getattr(error, "http_status", None):
            result["http_status"] = error.http_status
        if getattr(error, "observed", None):
            result["observed_remote"] = error.observed
        if context:
            resumable = (Path(context["path"]) / CHECKPOINT).is_file()
            result["next_actions"] = [
                {
                    "argv": [
                        "gh",
                        "repo",
                        "view",
                        context["repository"],
                        "--json",
                        "nameWithOwner,url,viewerPermission",
                    ]
                },
                {
                    "cwd": str(ROOT),
                    "argv": [
                        "uv",
                        "run",
                        "--locked",
                        "python",
                        "-m",
                        "engineering_ci.tools.bootstrap",
                        *(
                            ["--resume", context["path"]]
                            if resumable
                            else [
                                "--repo",
                                context["repository"],
                                "--path",
                                context["path"],
                            ]
                        ),
                        *(["--github"] if args.github else []),
                    ],
                },
            ]
            if progress.get("continuation") and progress.get("next_actions"):
                result["next_actions"][1] = progress["next_actions"][0]
            observed = getattr(error, "observed", None)
            if observed and "main_sha" in observed and context["remote_mode"] is None:
                result["remote_result"] = "existing_requires_selection"
                base = [
                    "uv",
                    "run",
                    "--locked",
                    "python",
                    "-m",
                    "engineering_ci.tools.bootstrap",
                    "--resume",
                    context["path"],
                    "--github",
                    "--existing-remote",
                ]
                result["next_actions"].append(
                    {
                        "cwd": str(ROOT),
                        "manual_confirmation_required": True,
                        "argv": [*base, "connect"],
                    }
                )
                if observed["main_sha"]:
                    result["next_actions"].append(
                        {
                            "cwd": str(ROOT),
                            "manual_confirmation_required": True,
                            "argv": [
                                *base,
                                "overwrite",
                                "--expected-main-sha",
                                observed["main_sha"],
                            ],
                        }
                    )
            if context["remote_mode"] == "overwrite":
                result["remote_recovery"] = {
                    "backup_ref": backup_ref(context["overwrite_main_sha"]),
                    "original_main_sha": context["overwrite_main_sha"],
                    "intended_main_sha": context["seed_sha"],
                }
            if context["remote_intent"] and context["remote_id"] is None:
                result["remote_result"] = "unconfirmed"
                result["next_actions"][0]["argv"] = [
                    "gh",
                    "api",
                    f"repos/{context['repository']}",
                    "--jq",
                    "{id,full_name,private,html_url}",
                ]
                if (
                    getattr(error, "observed", None)
                    and type(error.observed.get("id")) is int
                ):
                    result["next_actions"].append(
                        {
                            "cwd": str(ROOT),
                            "manual_confirmation_required": True,
                            "argv": [
                                "uv",
                                "run",
                                "--locked",
                                "python",
                                "-m",
                                "engineering_ci.tools.bootstrap",
                                "--resume",
                                context["path"],
                                "--github",
                                "--confirm-remote-id",
                                str(error.observed["id"]),
                            ],
                        }
                    )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return code


if __name__ == "__main__":
    raise SystemExit(main())
