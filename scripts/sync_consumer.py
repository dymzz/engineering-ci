#!/usr/bin/env python3
"""Synchronize a consumer's workflow and Dagger module to a published commit.

Run from any directory:
  python scripts/sync_consumer.py --consumer ../engineering-ci-smoke
  python scripts/sync_consumer.py --consumer ../engineering-ci-smoke --apply --commit --push

References come from Git directly, not from PowerShell variables.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


class SyncError(Exception):
    """Unsafe or unresolvable synchronization request."""


SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def command(args: list[str], *, cwd: Path, capture: bool = True) -> str:
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
    except FileNotFoundError as exc:
        raise SyncError(f"Command not installed: {args[0]}") from exc
    except subprocess.CalledProcessError as exc:
        msg = (exc.stderr or exc.stdout or "").strip()
        raise SyncError(f"Command failed: {args[0]} {' '.join(args[1:])}\n{msg}") from exc
    return result.stdout.strip() if capture else ""


def git(repo: Path, *args: str) -> str:
    return command(["git", *args], cwd=repo)


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
        raise SyncError("Source has uncommitted changes; commit and push before syncing")
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
        raise SyncError(f"Expected exactly one shared workflow reference; found {len(matches)}")
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
    if not isinstance(manifest, dict) or not isinstance(manifest.get("dependencies"), list):
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
    if not isinstance(dep.get("source"), str) or not dep["source"].startswith(expected_prefix):
        raise SyncError("Dagger dependency has a different source; refusing to rewrite it")
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
        raise SyncError("Consumer must contain .github/workflows/ci.yml and dagger.json")
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
        raise SyncError("--push requires the consumer to be on its configured main branch")
    if args.push and git(consumer, "rev-parse", "HEAD") != remote_branch_sha(consumer, args.branch):
        raise SyncError("Consumer local branch is not synchronized with origin/main")

    workflow_file.write_text(new_workflow, encoding="utf-8", newline="\n")
    dagger_file.write_text(new_dagger, encoding="utf-8", newline="\n")
    print("Updated both refs. Regenerating Dagger SDK...")
    command(["dagger", "develop"], cwd=consumer, capture=False)
    print("Running consumer Dagger checks...")
    command(["dagger", "check"], cwd=consumer, capture=False)
    print("PASS: consumer Dagger checks")

    if args.commit:
        git(consumer, "add", "--", ".github/workflows/ci.yml", "dagger.json", ".dagger")
        git(consumer, "commit", "-m", f"ci: sync {args.upstream} to {sha[:12]}")
        print("Committed verified consumer upgrade")
    if args.push:
        git(consumer, "push", "origin", f"HEAD:refs/heads/{args.branch}")
        print("Pushed consumer; its GitHub Actions push trigger will run automatically")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--consumer", type=Path, required=True, help="Consumer path relative to engineering-ci")
    parser.add_argument("--upstream", default="dymzz/engineering-ci", help="Full GitHub OWNER/REPO")
    parser.add_argument("--branch", default="main", help="Upstream and smoke consumer branch")
    parser.add_argument("--apply", action="store_true", help="Update refs, regenerate SDK, and run Dagger checks")
    parser.add_argument("--commit", action="store_true", help="Commit only after checks pass")
    parser.add_argument("--push", action="store_true", help="Push verified commit (intended for smoke)")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.upstream):
        parser.error("--upstream must be OWNER/REPO")
    if args.push and not args.commit:
        parser.error("--push requires --commit")
    if args.commit and not args.apply:
        parser.error("--commit requires --apply")
    try:
        perform(args, Path(__file__).resolve().parent.parent)
    except SyncError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
