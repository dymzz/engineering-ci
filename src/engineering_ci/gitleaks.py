"""Gitleaks Git-history execution with a sanitized native result.

The raw report is never returned. Only the fields needed for diagnosis and
identity are retained; ``Secret``/``Match`` and similar content fields are
dropped before the result can reach a report or artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

GITLEAKS_VERSION = "8.30.1"
GITLEAKS_WINDOWS_X64_SHA256 = (
    "17157e2ee8b76fc8b1d8bee607a250e34b8a8023c8bc81822d4b5ee4d78fcb7c"
)
_FINDING_FIELDS = (
    "RuleID",
    "Description",
    "File",
    "StartLine",
    "EndLine",
    "Commit",
    "Author",
    "Email",
    "Date",
    "Fingerprint",
    "Link",
)


class GitleaksError(ValueError):
    """The Gitleaks invocation cannot produce a trustworthy native result."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


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


def _sanitize(report):
    findings = []
    for item in report if isinstance(report, list) else ():
        if not isinstance(item, dict):
            continue
        finding = {key: item[key] for key in _FINDING_FIELDS if key in item}
        finding["redacted"] = True
        findings.append(finding)
    return findings


def _git_dir(root):
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise GitleaksError("gitleaks_git_directory_unavailable")
    path = Path(result.stdout.strip())
    if not path.is_absolute():
        path = Path(root) / path
    return path.resolve()


def scan_git_history(
    root,
    *,
    revision_range=None,
    all_history=False,
    config_path=None,
    environ=None,
    timeout=120,
):
    """Run Gitleaks over one explicit Git revision scope.

    ``revision_range`` is an ``old..new`` (or equivalent) Git log range.
    ``all_history=True`` is a separate coverage mode and must be requested
    explicitly; it is never inferred from an empty range.
    """
    root = Path(root)
    if bool(revision_range) == bool(all_history):
        raise GitleaksError("gitleaks_scope_required")
    if revision_range and not isinstance(revision_range, str):
        raise GitleaksError("gitleaks_scope_invalid")
    binary = find_binary(environ)
    config_path = Path(config_path) if config_path else None
    if config_path is not None and not config_path.is_file():
        raise GitleaksError("gitleaks_config_missing")
    with tempfile.TemporaryDirectory(
        prefix="eci-gitleaks-", dir=_git_dir(root)
    ) as temporary:
        report_path = Path(temporary) / "gitleaks.json"
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
        ]
        command.append(f"--log-opts={'--all' if all_history else revision_range}")
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
        if report_path.is_file():
            raw = report_path.read_bytes()
            try:
                report = json.loads(raw.decode("utf-8"))
            except (UnicodeError, ValueError):
                raise GitleaksError("gitleaks_report_invalid") from None
            findings = _sanitize(report)
            report_sha256 = "sha256:" + hashlib.sha256(raw).hexdigest()
        else:
            findings = []
            report_sha256 = None
    if completed.returncode == 0:
        status = "passed"
    elif completed.returncode == 1:
        status = "failed"
    else:
        status = "error"
    return {
        "status": status,
        "native_exit_code": completed.returncode,
        "findings": findings,
        "report_sha256": report_sha256,
        "tool": {
            "access_id": "gitleaks.git",
            "version": GITLEAKS_VERSION,
            "path": str(binary),
        },
        "coverage": {
            "mode": "all-history" if all_history else "revision-range",
            "revision_range": None if all_history else revision_range,
            "complete": status in {"passed", "failed"},
        },
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
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
