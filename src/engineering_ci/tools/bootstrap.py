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
from ..github_cli import ApiError, require_api_success
from . import bootstrap_templates as template

CHECKPOINT = ".test-tools/bootstrap.json"
GIT_SELECTORS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")


class BootstrapError(Exception):
    def __init__(self, reason, code=3, *, step=None, http_status=None, observed=None):
        super().__init__(reason)
        self.code, self.step = code, step
        self.http_status = http_status
        self.observed = observed


def plan(repository, path):
    """Accept portable names and an explicit absolute destination."""
    if not re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/[a-z][a-z0-9]*(?:-[a-z0-9]+)*",
        repository,
    ):
        raise BootstrapError("Use OWNER/lowercase-kebab-name (no URLs or shell text)")
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
    path = destination(path)
    return {
        "schema_version": 1,
        "kind": "engineering-ci-bootstrap",
        "repository": repository,
        "path": str(path),
        "version": template.VERSION,
        "sha": template.SHA,
        "seed_sha": None,
        "remote_intent": False,
        "remote_id": None,
    }


def destination(path):
    """Keep explicit Windows/Linux paths and reject redirected destinations."""
    path = Path(path).expanduser()
    if not path.is_absolute():
        raise BootstrapError(
            "Destination must be an explicit absolute path for this OS"
        )
    if any(part.is_symlink() for part in [path, *path.parents]):
        raise BootstrapError("Destination must not be redirected through a symlink")
    return path.resolve()


def owned(root, relative):
    """Reject redirected files before reading or writing the owned scaffold."""
    path = root / relative
    if not path.resolve().is_relative_to(root) or any(
        p.is_symlink()
        for p in [path, *path.parents]
        if p != root and p.is_relative_to(root)
    ):
        raise BootstrapError("Scaffold path is a symlink or escapes the destination")
    return path


def checkpoint(root):
    """Load only this tool's bounded recovery context, never arbitrary commands."""
    path = owned(root, CHECKPOINT)
    if path.stat().st_size > 8192:
        raise BootstrapError("Bootstrap checkpoint is oversized")
    value = json.loads(path.read_text(encoding="utf-8"))
    expected = plan(value["repository"], root)
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
            and (
                type(value["remote_id"]) is not int
                or value["remote_id"] <= 0
                or not value["remote_intent"]
            )
        )
        or (
            value["seed_sha"] is not None
            and not re.fullmatch(r"[a-f0-9]{40}", value["seed_sha"])
        )
    ):
        raise BootstrapError("Invalid bootstrap recovery facts")
    return value


class Commands:
    def __init__(self, run=subprocess.run):
        self.run = run
        self.step = "preflight"

    def execute(self, args, cwd, *, allowed=(0,)):
        """Use argv without a shell; retain the native exit and safe diagnostics."""
        try:
            result = self.run(
                args,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=120,
            )
        except FileNotFoundError:
            raise BootstrapError(
                f"Required tool is missing: {args[0]}", 2, step=self.step
            ) from None
        except (OSError, subprocess.TimeoutExpired):
            raise BootstrapError(
                f"{args[0]} is unavailable or timed out", step=self.step
            ) from None
        if result.returncode not in allowed:
            status = re.search(r"HTTP (\d{3})", result.stderr or "")
            suffix = f"; HTTP {status[1]}" if status else ""
            raise BootstrapError(
                f"{args[0]} failed (exit {result.returncode}{suffix}); raw responses are not saved",
                result.returncode if result.returncode > 0 else 128 - result.returncode,
                step=self.step,
                http_status=int(status[1]) if status else None,
            )
        return result

    def git(self, root, *args, allowed=(0,)):
        return self.execute(["git", *args], root, allowed=allowed).stdout.strip()

    def api(self, path, *, missing=False):
        result = self.execute(["gh", "api", path], None, allowed=(0, 1))
        try:
            require_api_success(result)
        except ApiError as error:
            if missing and error.status == 404:
                return None
            raise BootstrapError(
                str(error),
                result.returncode or 3,
                step=self.step,
                http_status=error.status,
            ) from None
        if len(result.stdout) > 1024 * 1024:
            raise BootstrapError("GitHub metadata exceeds limit", step=self.step)
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise BootstrapError(
                "GitHub did not return repository metadata", step=self.step
            )
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


def github(context, commands, *, confirm_remote_id=None):
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
    if context["remote_id"] is None:
        if remote is not None:
            raise BootstrapError(
                "Remote repository already exists; refusing automatic adoption",
                step=commands.step,
            )
        context["remote_intent"] = True
        save(root, context)  # Durable intent before the side-effecting gh operation.
        try:
            commands.execute(["gh", "repo", "create", repo, "--private"], root)
        except BootstrapError as error:
            if error.http_status in {400, 401, 403, 404, 422}:
                context["remote_intent"] = (
                    False  # Explicit rejection is not an ambiguous creation.
                )
                save(root, context)
            raise
        remote = commands.api(f"repos/{repo}")
        if (
            remote.get("full_name", "").lower() != repo.lower()
            or remote.get("private") is not True
            or type(remote.get("id")) is not int
        ):
            raise BootstrapError(
                "Created repository identity is unconfirmed", step=commands.step
            )
        context["remote_id"] = remote["id"]
        save(root, context)
    if (
        not remote
        or remote.get("id") != context["remote_id"]
        or remote.get("private") is not True
        or remote.get("full_name", "").lower() != repo.lower()
    ):
        raise BootstrapError(
            "Remote repository identity or visibility changed", step=commands.step
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
    remote_head = commands.git(root, "ls-remote", "origin", "refs/heads/main")
    if remote_head:
        if remote_head.split() != [context["seed_sha"], "refs/heads/main"]:
            raise BootstrapError(
                "Remote main differs; no force-push will be performed",
                step=commands.step,
            )
    else:
        commands.git(root, "push", "-u", "origin", "main")
    confirmed = commands.git(root, "ls-remote", "origin", "refs/heads/main").split()
    if confirmed != [context["seed_sha"], "refs/heads/main"]:
        raise BootstrapError("Seed push could not be confirmed", step=commands.step)


def next_actions(context, remote):
    """Return usable argv without claiming checks, SDK generation or PR creation."""
    root = context["path"]
    actions = []
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
    for args in (
        ["uv", "lock"],
        ["uv", "sync", "--locked"],
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
        ["uv", "lock", "--project", ".dagger"],
        ["dagger", "check", "-l", "--format", "cli"],
        ["dagger", "check"],
    ):
        actions.append({"cwd": root, "argv": args})
    return actions


def ask(prompt):
    print(prompt, file=sys.stderr, end="", flush=True)
    return input().strip()


def main(argv=None, *, run=subprocess.run, reader=ask):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="OWNER/lowercase-kebab-name")
    parser.add_argument("--path", type=Path, help="Explicit absolute new directory")
    parser.add_argument(
        "--resume", type=Path, help="Resume only this tool's existing scaffold"
    )
    parser.add_argument(
        "--github",
        action="store_true",
        help="Explicitly create a private remote and push the seed main",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Accept the requested local/--github actions without prompts",
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
    args = parser.parse_args(argv)
    if args.resume and (args.repo or args.path):
        parser.error("--resume cannot change --repo or --path")
    if args.confirm_remote_id is not None and (
        not args.resume or not args.github or args.confirm_remote_id <= 0
    ):
        parser.error(
            "--confirm-remote-id requires --resume, --github and a positive ID"
        )
    context, commands = None, Commands(run)
    try:
        if args.resume:
            root = destination(args.resume)
            context = checkpoint(root)
        else:
            if (args.yes or args.plan or not sys.stdin.isatty()) and (
                not args.repo or not args.path
            ):
                parser.error("Non-interactive use requires --repo and --path")
            repository = args.repo or reader("GitHub 仓库（OWNER/name）: ")
            target_path = args.path or reader("新仓绝对路径: ")
            context = plan(repository, target_path)
            root = Path(context["path"])
            if root.exists():
                raise BootstrapError(
                    "Destination already exists; only explicit --resume of this tool's checkpoint is supported"
                )
            if not root.parent.is_dir():
                raise BootstrapError(
                    "Destination parent does not exist; choose or create it explicitly"
                )
        result = {
            "status": "planned",
            "exit_code": 0,
            "context": context,
            "files": sorted(template.files(context["repository"])),
            "github_requested": args.github,
            "checks": "not_run",
            "pull_request": "not_created",
        }
        if args.plan:
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        if not args.yes and not args.github and sys.stdin.isatty():
            args.github = (
                reader(
                    "同时创建 GitHub 私有仓库并 Push 初始 main？输入 yes 确认 [默认否]: "
                )
                == "yes"
            )
            result["github_requested"] = args.github
        print(
            f"目标: {context['repository']} → {root}\n基线: {template.VERSION} @ {template.SHA}\n远端创建: {args.github}; SDK/检查/PR 在生成后显式执行。",
            file=sys.stderr,
        )
        print(
            "本地 Git: README-only main → codex/bootstrap-ci；文件:\n  "
            + "\n  ".join(result["files"]),
            file=sys.stderr,
        )
        if (
            not args.yes
            and reader("确认上述操作请输入 create（其他输入取消）: ") != "create"
        ):
            print(json.dumps({"status": "cancelled", "exit_code": 0}))
            return 0
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
            commands.step = "local_scaffold"
            preserved = local(context, commands)
            if args.github:
                github(context, commands, confirm_remote_id=args.confirm_remote_id)
            result.update(
                status="generated",
                context=context,
                preserved_changes=preserved,
                remote="confirmed" if args.github else "not_requested",
                checkpoint=str(owned(root, CHECKPOINT)),
                next_actions=next_actions(context, args.github),
            )
        print(
            "骨架已生成，检查尚未运行。按 next_actions 逐项执行；失败时先停下。PR/合并步骤见 guides/new-repository.md。",
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
            "status": "stopped",
            "exit_code": code,
            "step": getattr(error, "step", None) or commands.step,
            "reason": str(error)
            if isinstance(error, BootstrapError)
            else "Operation interrupted or local context unavailable; preserve the destination",
            "context": context,
            "checks": "not_run",
            "pull_request": "not_created",
        }
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
