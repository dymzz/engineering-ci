"""Connect the current checkout, existing PR and human merge to publication."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from .. import checks
from .._paths import ROOT
from .context import ExecutionContext, ReviewEvidence
from .pipeline import DeliveryPipeline, current_checks
from .pipeline import EvidenceError as DeliveryError
from .version import checked_version

WAITING_FOR_REVIEW = 3
START = "<!-- engineering-ci-delivery:start -->"
END = "<!-- engineering-ci-delivery:end -->"
PR_FIELDS = "number,url,state,isDraft,headRefOid,baseRefName,title,mergeCommit,body"
__all__ = ["current_checks"]


class Checkout:
    def __init__(self, repository, *, root=ROOT, run=subprocess.run):
        self.repository, self.root, self.run = repository, root.resolve(), run

    def command(self, *args, json_result=False):
        result = self.run(
            list(args),
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=180,
            env={
                **os.environ,
                "GIT_TERMINAL_PROMPT": "0",
                "GCM_INTERACTIVE": "never",
                "GH_PROMPT_DISABLED": "1",
            },
        )
        if result.returncode:
            # Raw stderr may contain credential-bearing URLs or API responses.
            reason = "operation failed"
            for pattern, description in (
                (
                    r"permission denied|403|not permitted|access.*denied",
                    "permission denied",
                ),
                (r"401|authentication|not logged|login", "authentication required"),
                (
                    r"non-fast-forward|fetch first|rejected",
                    "remote branch rejected the update",
                ),
                (r"could not resolve|timed out|connection|tls", "network unavailable"),
            ):
                if re.search(pattern, result.stderr or "", re.IGNORECASE):
                    reason = description
                    break
            raise DeliveryError(
                f"{' '.join(args[:3])}: {reason} (exit {result.returncode}); "
                "resolve it in this checkout; no account or workspace was changed"
            )
        if json_result:
            return json.loads(result.stdout)
        return result.stdout if "-z" in args else result.stdout.strip()

    def gh(self, *args, json_result=False):
        return self.command(
            "gh", *args, "--repo", self.repository, json_result=json_result
        )

    def context(self, version, *, validate_version=True):
        """Read actual files, branch, permissions and PR without writing anything."""
        try:
            local_version = checked_version()
        except ValueError:
            if validate_version:
                raise
            local_version = "inconsistent"
        if validate_version and local_version != version:
            raise DeliveryError(
                f"Requested v{version} differs from the four local metadata fields (v{local_version}); "
                "use the current version explicitly, or deliberately update the metadata"
            )
        if (
            Path(self.command("git", "rev-parse", "--show-toplevel")).resolve()
            != self.root
        ):
            raise DeliveryError("The command must use the engineering-ci checkout")
        branch = self.command("git", "symbolic-ref", "--quiet", "--short", "HEAD")
        head = self.command("git", "rev-parse", "HEAD")
        if not re.fullmatch(r"[0-9a-f]{40}", head):
            raise DeliveryError("Local commit identity unavailable")
        for args in (("origin",), ("--push", "origin")):
            remote = self.command("git", "remote", "get-url", *args)
            allowed = (
                f"https://github.com/{self.repository}",
                f"git@github.com:{self.repository}",
                f"ssh://git@github.com/{self.repository}",
            )
            if remote.removesuffix(".git") not in allowed:
                raise DeliveryError(
                    "origin does not match --repo; choose the repository explicitly"
                )
        conflicts = self.command("git", "diff", "--name-only", "--diff-filter=U")
        if validate_version and conflicts:
            raise DeliveryError("Resolve the existing merge conflicts before delivery")
        info = self.command(
            "gh",
            "repo",
            "view",
            self.repository,
            "--json",
            "nameWithOwner,viewerPermission,defaultBranchRef",
            json_result=True,
        )
        if (
            info["nameWithOwner"] != self.repository
            or info["defaultBranchRef"]["name"] != "main"
        ):
            raise DeliveryError(
                "Repository or default branch differs from the Release Workflow"
            )
        if validate_version and info["viewerPermission"] not in {
            "WRITE",
            "MAINTAIN",
            "ADMIN",
        }:
            raise DeliveryError(
                "The current gh account has no repository write permission"
            )
        remote = self.command(
            "git", "ls-remote", "--heads", "origin", f"refs/heads/{branch}"
        )
        remote_head = remote.split()[0] if remote else None
        if remote_head and not re.fullmatch(r"[0-9a-f]{40}", remote_head):
            raise DeliveryError("Remote branch commit identity unavailable")
        files = set(
            self.command("git", "diff", "--name-only", "-z", "HEAD").split("\0")
        )
        files.update(
            self.command(
                "git", "ls-files", "--others", "--exclude-standard", "-z"
            ).split("\0")
        )
        files.discard("")
        prs = (
            []
            if branch == "main"
            else self.gh(
                "pr",
                "list",
                "--head",
                branch,
                "--base",
                "main",
                "--state",
                "all",
                "--limit",
                "100",
                "--json",
                PR_FIELDS,
                json_result=True,
            )
        )
        opened = [pr for pr in prs if pr["state"] == "OPEN"]
        merged = [
            pr for pr in prs if pr["state"] == "MERGED" and pr["headRefOid"] == head
        ]
        if len(opened) > 1 or len(merged) > 1:
            raise DeliveryError(
                "More than one matching PR; select the intended PR manually"
            )
        pr = next(iter(opened or merged), None)
        if not pr and any(
            pr["state"] == "CLOSED" and pr["headRefOid"] == head for pr in prs
        ):
            raise DeliveryError(
                "The PR for this commit is closed; decide whether to reopen it"
            )
        contents = []
        for name in sorted(files):
            path = self.root / name
            if path.is_symlink():
                raise DeliveryError(
                    "Changed symlinks require manual review before delivery"
                )
            before = path.stat() if path.exists() else None
            digest = hashlib.sha256(path.read_bytes()).hexdigest() if before else None
            if before:
                after = path.stat()
                fields = ("st_size", "st_mtime_ns", "st_ctime_ns", "st_ino", "st_mode")
                # Reading may update access time; that is not a content mutation.
                if any(getattr(before, key) != getattr(after, key) for key in fields):
                    raise DeliveryError(
                        f"Input {name} changed while reading delivery context"
                    )
            contents.append((name, digest))
        return {
            "repository": self.repository,
            "version": version,
            "branch": branch,
            "head": head,
            "files": sorted(files),
            "pr": pr,
            "working_digest": checks.digest(contents),
            "local_version": local_version,
            "conflicts": conflicts,
            "permission": info["viewerPermission"],
            "remote_head": remote_head,
        }

    def show(self, context, *, diff=False):
        print(f"Release v{context['version']}: {self.repository}")
        if context["local_version"] != context["version"]:
            print(
                f"Local metadata: {context['local_version']}; it does not match the requested release"
            )
        print(
            f"Checkout: {self.root}; branch: {context['branch']}; commit: {context['head']}"
        )
        print(f"GitHub repository permission: {context['permission']}")
        print(
            f"Remote branch commit: {context['remote_head'] or 'not pushed / branch deleted'}"
        )
        if context["remote_head"] != context["head"]:
            print(
                "Local and remote branch commits differ; this is not a confirmed push of the current commit."
            )
        if context["conflicts"]:
            print(f"Stopped at unresolved conflicts:\n{context['conflicts']}")
        pr = context["pr"]
        if pr:
            print(f"PR #{pr['number']}: {pr['title']} ({pr['state']}); {pr['url']}")
        else:
            print("PR: none for the current branch")
        print(f"Uncommitted files: {len(context['files'])}")
        for path in context["files"]:
            print(f"  {json.dumps(path, ensure_ascii=False)}")
        if context["files"]:
            print(self.command("git", "diff", "--stat", "HEAD"))
        if diff:
            print(self.command("git", "diff", "HEAD"))
            print(
                "Untracked files above are not included in git diff; inspect them before --all."
            )

    def merged_commit(self, pr):
        """Publication must include the reviewed merge, without changing checkout."""
        head = pr.get("mergeCommit", {}).get("oid")
        if not head or not re.fullmatch(r"[0-9a-f]{40}", head):
            raise DeliveryError("Merged PR commit identity unavailable")
        current = self.main_commit()
        result = self.command(
            "gh",
            "api",
            f"repos/{self.repository}/compare/{head}...{current}",
            json_result=True,
        )
        if result.get("status") not in {"ahead", "identical"}:
            raise DeliveryError("Remote main does not include the merged PR")
        return current

    def main_commit(self):
        value = self.command(
            "gh", "api", f"repos/{self.repository}/commits/main", json_result=True
        )
        head = value.get("sha")
        if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head):
            raise DeliveryError("Remote main commit identity unavailable")
        return head


def pr_body(checkout, context, message, path):
    """Generate factual review context and preserve the human-written PR body."""
    commits = checkout.command("git", "log", "--format=%h %s", "origin/main..HEAD")
    changed = checkout.command("git", "diff", "--stat", "origin/main...HEAD")
    section = (
        f"{START}\n## Delivery context\n\n"
        f"{message}\n\nVersion: `v{context['version']}`\n\n"
        f"Head: `{context['head']}`\n\n"
        f"Local PR checks: passed for the current inputs (`{path.name}`). "
        "The actual PR Workflow and human review remain required.\n\n"
        f"Commits:\n```text\n{commits}\n```\n\n"
        f"Changed files:\n```text\n{changed}\n```\n{END}"
    )
    body = context["pr"].get("body", "") if context["pr"] else ""
    if START in body or END in body:
        if (
            body.count(START) != 1
            or body.count(END) != 1
            or body.index(END) < body.index(START)
        ):
            raise DeliveryError(
                "Existing PR delivery section is malformed; review its body manually"
            )
        return body[: body.index(START)] + section + body[body.index(END) + len(END) :]
    return body.rstrip() + ("\n\n" if body else "") + section


def handoff(checkout, context, *, require_head=True, execution=None):
    """Show actual PR checks and the next human action without merging it."""
    pr = context["pr"]
    state = checkout.gh(
        "pr",
        "view",
        str(pr["number"]),
        "--json",
        "headRefOid,statusCheckRollup,mergeable,mergeStateStatus",
        json_result=True,
    )
    if require_head and state["headRefOid"] != context["head"]:
        raise DeliveryError("PR changed before review handoff; inspect its new head")
    pending = bool(context["files"]) or state["headRefOid"] != context["head"]
    if pending:
        print(f"Stopped at delivery: local changes still need delivery to {pr['url']}")
    else:
        print(f"Awaiting human review: {pr['url']}; required CI is inspected below")
    print(f"PR commit: {state['headRefOid']}; merge state: {state['mergeStateStatus']}")
    if state["headRefOid"] != context["head"]:
        print(
            f"Current PR commit: {state['headRefOid']}; local changes still need delivery"
        )
    entries = state.get("statusCheckRollup") or []
    if not entries:
        print("PR CI: no reported results yet; it is not recorded as passed")
    for item in entries:
        print(
            f"PR CI: {item.get('name') or item.get('context')}: "
            f"{item.get('conclusion') or item.get('state') or item.get('status')}; "
            f"{item.get('detailsUrl') or item.get('targetUrl') or ''}"
        )
    conclusions = {
        item.get("name") or item.get("context"): item.get("conclusion")
        or item.get("state")
        or item.get("status")
        for item in entries
    }
    required = [conclusions.get(name) for name in checks.PR_CI_JOBS]
    passed = all(value == "SUCCESS" for value in required)
    failed = any(
        value
        in {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "SKIPPED", "ACTION_REQUIRED"}
        for value in required
    )
    if execution is not None and not pending:
        execution.review = ReviewEvidence(
            pr["number"],
            pr["url"],
            state["headRefOid"],
            "passed" if passed else "failed" if failed else "pending",
        )
    if not pending and passed and state["mergeable"] == "MERGEABLE":
        print(
            "Review the linked PR, mark its Draft ready when appropriate, and merge it in GitHub."
        )
        print(
            f"After merge, continue: uv run --locked python scripts/release.py publish "
            f"--repo {checkout.repository} --version v{context['version']}"
        )
    elif not pending:
        if failed:
            print(
                "Stopped at PR CI: required checks failed or were skipped. Fix the candidate branch and push the update; no merge recommendation."
            )
        else:
            print(
                "Stopped at PR CI: required results or conflict resolution are pending; keep the Draft open."
            )
            print(
                f"Only after required CI passes and human merge, continue: uv run --locked python scripts/release.py publish --repo {checkout.repository} --version v{context['version']}"
            )
    print("This command did not request any merge, Tag, Release or new validation Run.")
    return WAITING_FOR_REVIEW


def prepare(
    repository, version, *, message=None, include_all=False, report=None, checkout=None
):
    """Advance delivery with predecessor evidence, stopping at human review."""
    from .lifecycle import ReleaseLifecycle

    checkout = checkout or Checkout(repository)
    context = ExecutionContext(checkout.root, checkout.context(version))
    lifecycle = ReleaseLifecycle(context)
    pipeline = DeliveryPipeline(context, checkout, report)
    checkout.execution_context, checkout.lifecycle = context, lifecycle
    facts = context.facts
    checkout.show(facts)
    print(f"Delivery phase: {lifecycle.phase}")
    if lifecycle.phase == "BLOCKED_main":
        raise DeliveryError(
            "main has uncommitted changes; deliver them on a feature branch"
        )
    if lifecycle.phase == "BLOCKED_merged":
        raise DeliveryError(
            f"PR #{facts['pr']['number']} is merged; the listed local changes were not included. "
            "Preserve them and deliver a follow-up development PR before publication"
        )
    if lifecycle.phase == "BLOCKED_identity":
        raise DeliveryError(
            "Resolve the requested version or existing conflicts before delivery"
        )
    if lifecycle.phase == "REVIEW_merged":
        checkout.publication_sha = pipeline.merged()
        lifecycle.advance("confirm_merge")
        print(
            "Human merge / main identity confirmed; continuing to complete validation and Release publication"
        )
        return 0
    if lifecycle.phase == "REVIEW_waiting":
        return handoff(checkout, facts, execution=context)
    if facts["files"] and (not include_all or not message):
        raise DeliveryError(
            "Review the listed files, then repeat publish with --all (git add --all) and "
            "-m (git commit -m); no files were staged, committed or pushed"
        )
    lifecycle.advance("start_checks")
    code = pipeline.checked()
    if code:
        print(f"Stopped at local checks (exit {code}); report: {pipeline.report}")
        print(
            "No commit or push was performed. Resolve the reported result; matching evidence is retained on the next publish."
        )
        return code
    lifecycle.advance("checks_passed")
    print(f"Completed: local PR checks; report: {pipeline.report}")
    # Refresh PR diff context; leave the user's checkout intact.
    checkout.command("git", "fetch", "origin", "main")
    if checkout.context(version) != facts:
        raise DeliveryError(
            "Checkout or PR changed during checking; inspect the current context"
        )
    lifecycle.advance("start_commit")
    if facts["files"]:
        checkout.command("git", "add", "--all", "--", ".")
        checkout.command("git", "commit", "-m", message)
        facts = checkout.context(version)
        if facts["files"]:
            raise DeliveryError("Files changed during commit; stopped before push")
        print(f"Completed: commit {facts['head']}")
    pipeline.committed(facts)
    lifecycle.advance("committed")
    lifecycle.advance("start_push")
    checkout.command("git", "push", "--set-upstream", "origin", "HEAD")
    pipeline.pushed(checkout.context(version))
    lifecycle.advance("pushed")
    facts = context.facts
    print(f"Completed: push {facts['branch']}")
    message = checkout.command("git", "log", "-1", "--format=%B")
    subject = checkout.command("git", "log", "-1", "--format=%s")
    body = pr_body(checkout, facts, message, pipeline.report)
    body_file = pipeline.report.parent / "pr-body.md"
    body_file.write_text(body, encoding="utf-8", newline="\n")
    pr = facts["pr"]
    if pr:
        checkout.gh("pr", "edit", str(pr["number"]), "--body-file", str(body_file))
    else:
        checkout.gh(
            "pr",
            "create",
            "--base",
            "main",
            "--head",
            facts["branch"],
            "--draft",
            "--title",
            subject,
            "--body-file",
            str(body_file),
        )
    pipeline.reviewed(checkout.context(version))
    lifecycle.advance("open_review")
    return handoff(checkout, context.facts, execution=context)
