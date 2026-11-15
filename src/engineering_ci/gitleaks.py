"""Gitleaks Git-history execution with sanitized public output.

The raw report is never returned. Only diagnostic fields can survive, and every
known Secret/Match value is removed from those fields and from stdout/stderr
before they enter a public result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

GITLEAKS_VERSION = "8.30.1"
GITLEAKS_WINDOWS_X64_SHA256 = (
    "17157e2ee8b76fc8b1d8bee607a250e34b8a8023c8bc81822d4b5ee4d78fcb7c"
)
_SAFE_RULE = re.compile(r"^[A-Za-z0-9._-]+$")
_SAFE_COMMIT = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
_SAFE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_REVISION_RANGE = re.compile(r"^[^.\s]+\.\.[^.\s]+$")


class GitleaksError(ValueError):
    """The Gitleaks invocation cannot produce a trustworthy native result."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _git(root, *args):
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _git_dir(root):
    result = _git(root, "rev-parse", "--git-dir")
    if result.returncode != 0:
        raise GitleaksError("gitleaks_git_directory_unavailable")
    path = Path(result.stdout.strip())
    if not path.is_absolute():
        path = Path(root) / path
    return path.resolve()


def find_binary(environ=None):
    environ = os.environ if environ is None else environ
    configured = environ.get("GITLEAKS_BIN")
    if configured:
        path = Path(configured)
        if path.is_file():
            return path
        raise GitleaksError("gitleaks_binary_missing")
    found = shutil.which("gitleaks")
    if not found:
        raise GitleaksError("gitleaks_binary_missing")
    return Path(found)


def _verify_binary(binary, environ):
    version = subprocess.run(
        [str(binary), "version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if version.returncode != 0 or version.stdout.strip().splitlines()[:1] != [
        GITLEAKS_VERSION
    ]:
        raise GitleaksError("gitleaks_binary_version_mismatch")
    digest = _sha256(binary)
    machine = platform.machine().lower()
    windows_x64 = os.name == "nt" and machine in {"amd64", "x86_64"}
    if not windows_x64:
        raise GitleaksError("gitleaks_asset_unverified")
    if digest != "sha256:" + GITLEAKS_WINDOWS_X64_SHA256:
        raise GitleaksError("gitleaks_asset_mismatch")
    return {
        "access_id": "gitleaks/git",
        "version": GITLEAKS_VERSION,
        "artifact_sha256": digest,
        "platform": f"{platform.system().lower()}-{machine}",
        "path": str(binary),
    }


def _sanitize(report):
    if not isinstance(report, list):
        raise GitleaksError("gitleaks_report_invalid")
    findings = []
    for item in report:
        if not isinstance(item, dict):
            raise GitleaksError("gitleaks_report_invalid")
        rule = item.get("RuleID")
        commit = item.get("Commit")
        start = item.get("StartLine")
        if (
            not isinstance(rule, str)
            or not _SAFE_RULE.fullmatch(rule)
            or not isinstance(commit, str)
            or not _SAFE_COMMIT.fullmatch(commit)
            or type(start) is not int
            or start < 1
        ):
            raise GitleaksError("gitleaks_report_invalid")
        finding = {
            "RuleID": rule,
            "Commit": commit,
            "StartLine": start,
        }
        end = item.get("EndLine")
        if type(end) is int and end >= start:
            finding["EndLine"] = end
        date = item.get("Date")
        if isinstance(date, str) and _SAFE_DATE.fullmatch(date):
            finding["Date"] = date
        finding["redacted"] = True
        findings.append(finding)
    return findings


def _resolve_scope(root, revision_range, all_history):
    shallow = (
        _git(root, "rev-parse", "--is-shallow-repository").stdout.strip() == "true"
    )
    if all_history:
        count = _git(root, "rev-list", "--count", "--all")
        if count.returncode != 0:
            raise GitleaksError("gitleaks_history_unavailable")
        return {
            "mode": "all-history",
            "revision_range": None,
            "old_commit": None,
            "new_commit": None,
            "commit_count": int(count.stdout.strip()),
            "shallow": shallow,
            "complete": not shallow,
            "reason": "shallow_repository" if shallow else None,
        }

    if not isinstance(revision_range, str) or not _REVISION_RANGE.fullmatch(
        revision_range
    ):
        raise GitleaksError("gitleaks_revision_invalid")
    old, new = revision_range.split("..", 1)
    old_commit = _git(root, "rev-parse", "--verify", f"{old}^{{commit}}")
    new_commit = _git(root, "rev-parse", "--verify", f"{new}^{{commit}}")
    if old_commit.returncode != 0 or new_commit.returncode != 0:
        raise GitleaksError("gitleaks_revision_unavailable")
    old_sha = old_commit.stdout.strip()
    new_sha = new_commit.stdout.strip()
    ancestor = _git(root, "merge-base", "--is-ancestor", old_sha, new_sha)
    count = _git(root, "rev-list", "--count", f"{old_sha}..{new_sha}")
    if count.returncode != 0:
        raise GitleaksError("gitleaks_history_unavailable")
    reachable = ancestor.returncode == 0
    complete = reachable and not shallow
    reason = None
    if not reachable:
        reason = "non_ancestor_range"
    elif shallow:
        reason = "shallow_repository"
    return {
        "mode": "revision-range",
        "revision_range": revision_range,
        "old_commit": old_sha,
        "new_commit": new_sha,
        "commit_count": int(count.stdout.strip()),
        "shallow": shallow,
        "complete": complete,
        "reason": reason,
    }


def scan_git_history(
    root,
    *,
    revision_range=None,
    all_history=False,
    config_path=None,
    environ=None,
    timeout=120,
):
    """Run Gitleaks over one explicit Git revision scope."""
    root = Path(root)
    environ = os.environ if environ is None else environ
    if bool(revision_range) == bool(all_history):
        raise GitleaksError("gitleaks_scope_required")
    coverage = _resolve_scope(root, revision_range, all_history)
    binary = find_binary(environ)
    tool = _verify_binary(binary, environ)
    config_path = Path(config_path) if config_path else None
    if config_path is not None:
        if not config_path.is_absolute():
            config_path = (root / config_path).resolve()
        if not config_path.is_file():
            raise GitleaksError("gitleaks_config_missing")
    with tempfile.TemporaryDirectory(
        prefix="eci-gitleaks-", dir=_git_dir(root)
    ) as temporary:
        report_path = Path(temporary) / "gitleaks.json"
        if revision_range and coverage["old_commit"] and coverage["new_commit"]:
            scan_range = f"{coverage['old_commit']}..{coverage['new_commit']}"
        else:
            scan_range = "--all"
        command = [
            str(binary),
            "git",
            str(root),
            "--no-banner",
            "--no-color",
            "--redact",
            "--report-format",
            "json",
            "--report-path",
            str(report_path),
            f"--log-opts={scan_range}",
        ]
        if config_path is not None:
            command.extend(["--config", str(config_path)])
        try:
            completed = subprocess.run(
                command,
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=timeout,
            )
        except FileNotFoundError:
            raise GitleaksError("gitleaks_binary_missing") from None
        except subprocess.TimeoutExpired:
            raise GitleaksError("gitleaks_timeout") from None
        if not report_path.is_file():
            raise GitleaksError("gitleaks_report_missing")
        raw = report_path.read_bytes()
        try:
            report = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError):
            raise GitleaksError("gitleaks_report_invalid") from None
        findings = _sanitize(report)

    if completed.returncode == 0 and not findings:
        native_status = "passed"
        reason = None
    elif completed.returncode == 1 and findings:
        native_status = "failed"
        reason = "secret_findings"
    elif completed.returncode == 0:
        native_status = "error"
        reason = "gitleaks_exit_inconsistent"
    elif completed.returncode == 1:
        native_status = "error"
        reason = "gitleaks_findings_missing"
    else:
        native_status = "error"
        reason = "gitleaks_scan_failed"

    if native_status == "error":
        coverage["complete"] = False
        coverage["reason"] = "scan_incomplete"
        status = "error"
    elif not coverage["complete"]:
        status = "insufficient"
        reason = coverage["reason"] or "coverage_incomplete"
    else:
        status = native_status

    return {
        "status": status,
        "reason": reason,
        "native_status": native_status,
        "native_exit_code": completed.returncode,
        "findings": findings,
        "report_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "tool": tool,
        "coverage": coverage,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run Gitleaks over Git history")
    subparsers = parser.add_subparsers(dest="command", required=True)
    scan = subparsers.add_parser("scan")
    scan.add_argument("--root", default=".")
    scan.add_argument("--revision-range")
    scan.add_argument("--all-history", action="store_true")
    scan.add_argument("--config")
    scan.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = scan_git_history(
            Path(args.root),
            revision_range=args.revision_range,
            all_history=args.all_history,
            config_path=args.config,
        )
    except GitleaksError as error:
        print(error.code, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(
            f"{result['status']} exit={result['native_exit_code']} "
            f"findings={len(result['findings'])}"
        )
    return 0 if result["status"] == "passed" else 1
