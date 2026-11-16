"""Trivy native filesystem scans with sanitized public evidence.

This slice executes Trivy and returns only controlled, non-content fields. It
does not convert raw scan output into an SBOM yet and does not decide a final
Required/Release policy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path

TRIVY_VERSION = "0.76.1"
TRIVY_WINDOWS_X64_SHA256 = (
    "94618922c9eb324d7099bf217b01504bfc1f87a0d46d6008cb24ec89b1d1a4bb"
)
_SEVERITIES = frozenset({"UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"})
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_SCANNERS = frozenset({"vuln", "misconfig", "secret"})


class TrivyError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def find_binary(environ=None):
    base = os.environ if environ is None else environ
    configured = base.get("TRIVY_BIN")
    if configured:
        path = Path(configured)
        if path.is_file():
            return path
        raise TrivyError("trivy_binary_missing")
    path_value = base.get("PATH", "")
    for directory in path_value.split(os.pathsep):
        if not directory:
            continue
        candidate = Path(directory) / ("trivy.exe" if os.name == "nt" else "trivy")
        if candidate.is_file():
            return candidate
    raise TrivyError("trivy_binary_missing")


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _verify_binary(binary):
    machine = platform.machine().lower()
    if not (os.name == "nt" and machine in {"amd64", "x86_64"}):
        raise TrivyError("trivy_asset_unverified")
    digest = _sha256(binary)
    if digest != "sha256:" + TRIVY_WINDOWS_X64_SHA256:
        raise TrivyError("trivy_asset_mismatch")
    result = subprocess.run(
        [str(binary), "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    version = None
    for line in result.stdout.splitlines():
        if line.strip().startswith("Version:"):
            version = line.split(":", 1)[1].strip()
            break
    if result.returncode != 0 or version != TRIVY_VERSION:
        raise TrivyError("trivy_binary_version_mismatch")
    return {
        "access_id": "trivy/scan",
        "version": TRIVY_VERSION,
        "artifact_sha256": digest,
        "platform": f"{platform.system().lower()}-{machine}",
        "path": str(binary),
    }


def _hash_text(value):
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _target_digest(path):
    path = Path(path)
    if path.is_symlink():
        raise TrivyError("trivy_target_symlink")
    if path.is_file():
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256()
    for item in sorted(
        path.rglob("*"), key=lambda value: value.relative_to(path).as_posix()
    ):
        if item.is_symlink():
            raise TrivyError("trivy_target_symlink")
        rel = item.relative_to(path).as_posix()
        if item.is_dir():
            digest.update(b"D" + rel.encode("utf-8") + b"\0")
        elif item.is_file():
            digest.update(b"F" + rel.encode("utf-8") + b"\0")
            digest.update(hashlib.sha256(item.read_bytes()).digest())
    return "sha256:" + digest.hexdigest()


def _severity(value):
    return value if value in _SEVERITIES else "UNKNOWN"


def _sanitize(report):
    if not isinstance(report, dict):
        raise TrivyError("trivy_report_invalid")
    if (
        report.get("SchemaVersion") != 2
        or not isinstance(report.get("Trivy"), dict)
        or report["Trivy"].get("Version") != TRIVY_VERSION
        or report.get("ArtifactType") != "filesystem"
    ):
        raise TrivyError("trivy_report_invalid")
    results = report.get("Results", [])
    if not isinstance(results, list):
        raise TrivyError("trivy_report_invalid")
    findings = []
    misconfig_summary = {"fail": 0, "pass": 0, "exception": 0}
    for result in results:
        if not isinstance(result, dict):
            raise TrivyError("trivy_report_invalid")
        target = result.get("Target")
        if not isinstance(target, str):
            raise TrivyError("trivy_report_invalid")
        target_hash = _hash_text(target)
        for item in result.get("Secrets") or ():
            if not isinstance(item, dict) or not isinstance(item.get("RuleID"), str):
                raise TrivyError("trivy_report_invalid")
            start = item.get("StartLine")
            if type(start) is not int or start < 1:
                raise TrivyError("trivy_report_invalid")
            finding = {
                "kind": "secret",
                "rule_sha256": _hash_text(item["RuleID"]),
                "severity": _severity(item.get("Severity")),
                "target_sha256": target_hash,
                "start_line": start,
                "redacted": True,
            }
            end = item.get("EndLine")
            if type(end) is int and end >= start:
                finding["end_line"] = end
            findings.append(finding)
        for item in result.get("Vulnerabilities") or ():
            if not isinstance(item, dict) or not isinstance(
                item.get("VulnerabilityID"), str
            ):
                raise TrivyError("trivy_report_invalid")
            vuln_id = item["VulnerabilityID"]
            if not _SAFE_ID.fullmatch(vuln_id):
                raise TrivyError("trivy_report_invalid")
            finding = {
                "kind": "vulnerability",
                "vulnerability_sha256": _hash_text(vuln_id),
                "severity": _severity(item.get("Severity")),
                "target_sha256": target_hash,
                "redacted": True,
            }
            package = item.get("PkgName")
            if isinstance(package, str):
                finding["package_sha256"] = _hash_text(package)
            findings.append(finding)
        for item in result.get("Misconfigurations") or ():
            if not isinstance(item, dict) or not isinstance(item.get("ID"), str):
                raise TrivyError("trivy_report_invalid")
            status = item.get("Status")
            if status not in {"FAIL", "PASS", "EXCEPTION"}:
                raise TrivyError("trivy_report_invalid")
            misconfig_summary[status.lower()] += 1
            if status != "FAIL":
                continue
            findings.append(
                {
                    "kind": "misconfiguration",
                    "rule_sha256": _hash_text(item["ID"]),
                    "severity": _severity(item.get("Severity")),
                    "target_sha256": target_hash,
                    "redacted": True,
                }
            )
    return findings, misconfig_summary


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
        return None
    path = Path(result.stdout.strip())
    if not path.is_absolute():
        path = Path(root) / path
    return path.resolve()


def scan_filesystem(
    root,
    *,
    target=".",
    scanners=("secret",),
    config_path=None,
    exit_code=None,
    environ=None,
    timeout=180,
):
    root = Path(root).resolve()
    raw_target = root / target
    if raw_target.is_symlink():
        raise TrivyError("trivy_target_symlink")
    target_path = raw_target.resolve()
    try:
        relative_target = target_path.relative_to(root).as_posix()
    except ValueError:
        raise TrivyError("trivy_target_outside_root") from None
    if not target_path.exists():
        raise TrivyError("trivy_target_missing")
    selected = tuple(dict.fromkeys(scanners))
    if not selected or any(item not in _SCANNERS for item in selected):
        raise TrivyError("trivy_scanners_invalid")
    before_digest = _target_digest(target_path)
    binary = find_binary(environ)
    tool = _verify_binary(binary)
    config = Path(config_path) if config_path else None
    if config is not None:
        config = config if config.is_absolute() else (root / config).resolve()
        if not config.is_file():
            raise TrivyError("trivy_config_missing")
    config_before = _sha256(config) if config is not None else None
    base_env = os.environ if environ is None else environ
    ignored_env = sorted(key for key in base_env if key.startswith("TRIVY_"))
    child_env = {
        key: value for key, value in base_env.items() if not key.startswith("TRIVY_")
    }
    with tempfile.TemporaryDirectory(
        prefix="eci-trivy-", dir=_git_dir(root)
    ) as temporary:
        report_path = Path(temporary) / "report.json"
        command = [
            str(binary),
            "fs",
            str(target_path),
            "--scanners",
            ",".join(selected),
            "--format",
            "json",
            "--output",
            str(report_path),
            "--quiet",
            "--no-progress",
            "--config",
            str(config) if config is not None else "",
            "--ignorefile",
            "",
            "--secret-config",
            "",
        ]
        if exit_code is not None and (type(exit_code) is not int or exit_code < 0):
            raise TrivyError("trivy_exit_code_invalid")
        effective_exit = 0 if exit_code is None else exit_code
        command.extend(["--exit-code", str(effective_exit)])
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
                env=child_env,
            )
        except FileNotFoundError:
            raise TrivyError("trivy_binary_missing") from None
        except subprocess.TimeoutExpired:
            raise TrivyError("trivy_timeout") from None
        if not report_path.is_file():
            raise TrivyError("trivy_report_missing")
        raw = report_path.read_bytes()
        try:
            report = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError):
            raise TrivyError("trivy_report_invalid") from None
        findings, misconfig_summary = _sanitize(report)

    after_digest = _target_digest(target_path)
    config_after = _sha256(config) if config is not None else None
    if completed.returncode == 0:
        status = "findings" if findings else "passed"
        reason = "security_findings" if findings else None
    elif findings and completed.returncode == effective_exit:
        status = "findings"
        reason = "security_findings"
    else:
        status = "error"
        reason = "trivy_scan_failed"
    if before_digest != after_digest:
        status = "error"
        reason = "trivy_target_changed"
    if config_before != config_after:
        status = "error"
        reason = "trivy_config_changed"
    return {
        "status": status,
        "reason": reason,
        "native_exit_code": completed.returncode,
        "findings": findings,
        "report_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "target": {
            "path": relative_target,
            "input_sha256": before_digest,
            "after_sha256": after_digest,
        },
        "tool": tool,
        "scanners": list(selected),
        "coverage": {
            "artifact_type": report.get("ArtifactType"),
            "results_present": "Results" in report,
            "process_terminated": True,
            "report_valid": True,
            "scan_completed": status in {"passed", "findings"},
            "rule_closure_verified": False,
        },
        "misconfig_summary": misconfig_summary,
        "policy": {
            "config_sha256": config_before,
            "config_after_sha256": config_after,
            "config_scope": "explicit-file-only",
            "effective_exit_code": effective_exit,
            "exit_code_source": "cli" if exit_code is not None else "default-zero",
            "rule_closure_verified": False,
            "implicit_config": False,
            "implicit_ignore": False,
            "implicit_secret_config": False,
            "env_isolated": True,
            "ignored_env_keys": ignored_env,
        },
        "db": {
            "updated_at": (report.get("Metadata") or {}).get("DBUpdatedAt"),
            "version": (report.get("Metadata") or {}).get("DBVersion"),
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run Trivy filesystem scans")
    subparsers = parser.add_subparsers(dest="command", required=True)
    scan = subparsers.add_parser("scan")
    scan.add_argument("--root", default=".")
    scan.add_argument("--target", default=".")
    scan.add_argument("--scanners", default="secret")
    scan.add_argument("--config")
    scan.add_argument("--exit-code", type=int)
    scan.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = scan_filesystem(
            Path(args.root),
            target=args.target,
            scanners=tuple(item for item in args.scanners.split(",") if item),
            config_path=args.config,
            exit_code=args.exit_code,
        )
    except TrivyError as error:
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
