"""Synchronize a consumer's workflow and Dagger module to a published commit.

Run from any directory:
  python -m engineering_ci.tools.consumer --consumer ../engineering-ci-smoke
  python -m engineering_ci.tools.consumer --consumer ../engineering-ci-smoke --apply --commit --push

References come from Git directly, not from PowerShell variables.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

from .. import git_source
from .._paths import ROOT


class SyncError(Exception):
    """Unsafe or unresolvable synchronization request."""


SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _resolved_optional_path(raw: str | os.PathLike[str] | None) -> Path | None:
    if not raw or not str(raw).strip():
        return None
    try:
        return Path(raw).resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _is_eci_owned_path(raw_path: str, *, source: Path, consumer: Path) -> bool:
    """Return whether ``raw_path`` points inside ECI's checkout family rather than ``consumer``."""
    candidate = _resolved_optional_path(raw_path)
    if candidate is None:
        return False
    if candidate == consumer or candidate.is_relative_to(consumer):
        return False
    if candidate == source or candidate.is_relative_to(source):
        return True
    checkout = git_source.enclosing_git_checkout(candidate)
    if checkout is None:
        return False
    return checkout == source or git_source.shared_git_common_dir(source, checkout)


def _matching_env_keys(env: Mapping[str, str], target: str) -> list[str]:
    if os.name == "nt":
        upper = target.upper()
        return [key for key in env if key.upper() == upper]
    return [target] if target in env else []


def consumer_subprocess_env(
    source: Path,
    consumer: Path,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Strip inherited ECI session env bindings while preserving intentional consumer targets."""
    env = dict(os.environ if base_env is None else base_env)
    src_root = source.resolve()
    cons_root = consumer.resolve()
    stripped_envs: list[Path] = []
    kept_uv_env: Path | None = None
    active_prefix = (
        _resolved_optional_path(sys.prefix)
        if sys.prefix != getattr(sys, "base_prefix", sys.prefix)
        else None
    )

    for target in ("UV_PROJECT", "UV_PROJECT_ENVIRONMENT"):
        for key in _matching_env_keys(env, target):
            val = env.get(key)
            if val and _is_eci_owned_path(val, source=src_root, consumer=cons_root):
                env.pop(key, None)
                if target == "UV_PROJECT_ENVIRONMENT":
                    resolved_env = _resolved_optional_path(val)
                    if resolved_env is not None and resolved_env not in stripped_envs:
                        stripped_envs.append(resolved_env)
            elif target == "UV_PROJECT_ENVIRONMENT" and val:
                kept_uv_env = _resolved_optional_path(val)

    for key in _matching_env_keys(env, "VIRTUAL_ENV"):
        venv_val = env.get(key)
        if not venv_val:
            continue
        resolved_venv = _resolved_optional_path(venv_val)
        implicit_out_of_repo_eci = (
            active_prefix is not None
            and resolved_venv is not None
            and resolved_venv == active_prefix
            and not (
                resolved_venv == cons_root or resolved_venv.is_relative_to(cons_root)
            )
            and resolved_venv != kept_uv_env
        )
        if (
            _is_eci_owned_path(venv_val, source=src_root, consumer=cons_root)
            or (resolved_venv is not None and resolved_venv in stripped_envs)
            or implicit_out_of_repo_eci
        ):
            env.pop(key, None)
            if resolved_venv is not None and resolved_venv not in stripped_envs:
                stripped_envs.append(resolved_venv)

    for key in _matching_env_keys(env, "PYTHONPATH"):
        pypath = env.get(key)
        if pypath is None:
            continue
        kept_entries = [
            item
            for item in pypath.split(os.pathsep)
            if item
            and not _is_eci_owned_path(item, source=src_root, consumer=cons_root)
        ]
        if kept_entries:
            env[key] = os.pathsep.join(kept_entries)
        else:
            env.pop(key, None)

    if stripped_envs:
        stripped_bin_dirs = {
            sub.resolve()
            for venv_dir in stripped_envs
            for sub in (venv_dir / "Scripts", venv_dir / "bin")
        }
        for key in _matching_env_keys(env, "PATH"):
            raw_path = env.get(key)
            if not raw_path:
                continue
            kept_path = [
                item
                for item in raw_path.split(os.pathsep)
                if item and _resolved_optional_path(item) not in stripped_bin_dirs
            ]
            env[key] = os.pathsep.join(kept_path)

    return env


def command(
    args: list[str],
    *,
    cwd: Path,
    capture: bool = True,
    env: Mapping[str, str] | None = None,
) -> str:
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            env=env,
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
    except FileNotFoundError as exc:
        raise SyncError(f"Command not installed: {args[0]}") from exc
    except subprocess.CalledProcessError as exc:
        msg = (exc.stderr or exc.stdout or "").strip()
        raise SyncError(
            f"Command failed: {args[0]} {' '.join(args[1:])}\n{msg}"
        ) from exc
    return result.stdout.strip() if capture else ""


def git(repo: Path, *args: str, env: Mapping[str, str] | None = None) -> str:
    return command(["git", *args], cwd=repo, env=env)


def remote_branch_sha(repo: Path, branch: str) -> str:
    output = git(repo, "ls-remote", "origin", f"refs/heads/{branch}")
    fields = output.split()
    if len(fields) != 2 or fields[1] != f"refs/heads/{branch}":
        raise SyncError(f"Remote origin/{branch} does not exist")
    sha = fields[0]
    if not SHA_PATTERN.fullmatch(sha):
        raise SyncError(f"Invalid remote SHA for origin/{branch}")
    return sha


def published_head(repo: Path, branch: str) -> str:
    active = git(repo, "branch", "--show-current")
    if active != branch:
        raise SyncError(f"Source branch must be {branch!r}; currently {active!r}")
    if git(repo, "status", "--porcelain"):
        raise SyncError(
            "Source has uncommitted changes; commit and push before syncing"
        )
    local_sha = git(repo, "rev-parse", "HEAD")
    remote_sha = remote_branch_sha(repo, branch)
    if local_sha != remote_sha:
        raise SyncError(
            f"Source HEAD ({local_sha[:12]}) differs from origin/{branch} "
            f"({remote_sha[:12]}). Push or pull first."
        )
    return local_sha


def update_references(
    workflow_text: str, dagger_text: str, upstream: str, sha: str
) -> tuple[str, str, list[str]]:
    if not SHA_PATTERN.fullmatch(sha):
        raise SyncError("Expected a full 40-character lowercase Git commit SHA")
    workflow_prefix = f"{upstream}/.github/workflows/reusable-python-ci.yml@"
    pattern = re.compile(
        r"(?m)^(?P<indent>[ \t]*uses:[ \t]*)"
        + re.escape(workflow_prefix)
        + r"(?P<old>[^ \t#\r\n]+)(?P<suffix>[ \t]*(?:\#.*)?)$"
    )
    matches = list(pattern.finditer(workflow_text))
    if len(matches) != 1:
        raise SyncError(
            f"Expected exactly one shared workflow reference; found {len(matches)}"
        )
    old_workflow = matches[0].group("old")
    changed_workflow = pattern.sub(
        lambda m: m.group("indent") + workflow_prefix + sha + m.group("suffix"),
        workflow_text,
        count=1,
    )

    try:
        manifest = json.loads(dagger_text)
    except json.JSONDecodeError as exc:
        raise SyncError(f"Invalid consumer dagger.json: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(
        manifest.get("dependencies"), list
    ):
        raise SyncError("dagger.json must include a dependencies array")
    module_name = upstream.rsplit("/", 1)[-1]
    matching = [
        dep
        for dep in manifest["dependencies"]
        if isinstance(dep, dict) and dep.get("name") == module_name
    ]
    if len(matching) != 1:
        raise SyncError(f"Expected exactly one Dagger dependency called {module_name}")
    dep = matching[0]
    expected_prefix = f"github.com/{upstream}@"
    if not isinstance(dep.get("source"), str) or not dep["source"].startswith(
        expected_prefix
    ):
        raise SyncError(
            "Dagger dependency has a different source; refusing to rewrite it"
        )
    old_module = dep["source"][len(expected_prefix) :]
    dep["source"] = expected_prefix + sha
    dep["pin"] = sha
    changed_dagger = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    summary = [
        f"GitHub Actions: {old_workflow} -> {sha}",
        f"Dagger module:  {old_module} -> {sha}",
    ]
    return changed_workflow, changed_dagger, summary


def git_root(repo: Path) -> Path:
    """Normalize Git's slash-separated path for Windows and Unix hosts."""
    return Path(git(repo, "rev-parse", "--show-toplevel")).resolve()


def perform(args: argparse.Namespace, source: Path) -> None:
    source = source.resolve()
    consumer = (source / args.consumer).resolve()
    if not consumer.is_dir():
        raise SyncError(f"Consumer directory does not exist: {consumer}")
    if git_root(source) != source:
        raise SyncError("Script is not in the root of the source Git repository")
    if git_root(consumer) != consumer:
        raise SyncError("Consumer must be its own Git repository root")
    if source == consumer:
        raise SyncError("Source and consumer must be different repositories")
    sha = published_head(source, args.branch)

    workflow_file = consumer / ".github/workflows/ci.yml"
    dagger_file = consumer / "dagger.json"
    if not workflow_file.is_file() or not dagger_file.is_file():
        raise SyncError(
            "Consumer must contain .github/workflows/ci.yml and dagger.json"
        )
    old_workflow = workflow_file.read_text(encoding="utf-8")
    old_dagger = dagger_file.read_text(encoding="utf-8")
    new_workflow, new_dagger, summary = update_references(
        old_workflow, old_dagger, args.upstream, sha
    )
    changes = old_workflow != new_workflow or old_dagger != new_dagger
    print(f"Published source commit: {sha}")
    for line in summary:
        print(line)
    if not changes:
        print("No changes: consumer already uses this commit.")
        return
    if not args.apply:
        print("Dry run; use --apply to regenerate the SDK and run Dagger checks.")
        return
    if git(consumer, "status", "--porcelain"):
        raise SyncError("Consumer has uncommitted changes; refusing to overwrite them")
    if args.push and git(consumer, "branch", "--show-current") != args.branch:
        raise SyncError(
            "--push requires the consumer to be on its configured main branch"
        )
    if args.push and git(consumer, "rev-parse", "HEAD") != remote_branch_sha(
        consumer, args.branch
    ):
        raise SyncError("Consumer local branch is not synchronized with origin/main")

    child_env = consumer_subprocess_env(source, consumer)
    workflow_file.write_text(new_workflow, encoding="utf-8", newline="\n")
    dagger_file.write_text(new_dagger, encoding="utf-8", newline="\n")
    print("Updated both refs. Regenerating Dagger SDK...")
    command(["dagger", "develop"], cwd=consumer, capture=False, env=child_env)
    print("Running consumer Dagger checks...")
    command(["dagger", "check"], cwd=consumer, capture=False, env=child_env)
    print("PASS: consumer Dagger checks")

    if args.commit:
        git(
            consumer,
            "add",
            "--",
            ".github/workflows/ci.yml",
            "dagger.json",
            ".dagger",
            env=child_env,
        )
        git(
            consumer,
            "commit",
            "-m",
            f"ci: sync {args.upstream} to {sha[:12]}",
            env=child_env,
        )
        print("Committed verified consumer upgrade")
    if args.push:
        git(
            consumer,
            "push",
            "origin",
            f"HEAD:refs/heads/{args.branch}",
            env=child_env,
        )
        print("Pushed consumer; its GitHub Actions push trigger will run automatically")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--consumer",
        type=Path,
        required=True,
        help="Consumer path relative to engineering-ci",
    )
    parser.add_argument(
        "--upstream", default="dymzz/engineering-ci", help="Full GitHub OWNER/REPO"
    )
    parser.add_argument(
        "--branch", default="main", help="Upstream and smoke consumer branch"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Update refs, regenerate SDK, and run Dagger checks",
    )
    parser.add_argument(
        "--commit", action="store_true", help="Commit only after checks pass"
    )
    parser.add_argument(
        "--push", action="store_true", help="Push verified commit (intended for smoke)"
    )
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.upstream):
        parser.error("--upstream must be OWNER/REPO")
    if args.push and not args.commit:
        parser.error("--push requires --commit")
    if args.commit and not args.apply:
        parser.error("--commit requires --apply")
    try:
        perform(args, ROOT)
    except SyncError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
