"""PSScriptAnalyzer check for the Windows runner in the reusable Workflow.

PowerShell scripts are analysed on a Windows runner rather than inside the
Linux Dagger container, because the rules and the environment they depend on
are PowerShell-specific and a Windows job is the only place they are honest.
This module therefore runs on the runner itself and is invoked the same way the
existing dependency-security job invokes its helper.

Two signals, deliberately kept apart:

- the analyzer process status only proves the invocation survived, because
  PowerShell exits zero even when rules were violated;
- the diagnostic objects the analyzer returned decide findings, absence of
  scripts, and execution failure.

Nothing here rewrites a script, executes one, or installs an automatic fix.

Verification status — NOT VERIFIED against the real tools:

1. No PSScriptAnalyzer run has been observed. Which default rules actually fire
   on a given script is a property of the analyzer, not of this module, and the
   committed fixtures assume three specific rules do.
2. The analyzer's own report shape is read through the JSON Lines contract that
   `powershell_invoke.ps1` writes; that pairing is unexercised end to end.
3. The Windows runner job has never executed, so module preparation, the pinned
   uv invocation and the checkout move are all unproven.

The gate fails closed: an unreadable envelope, a missing script set or a failed
process exits non-zero and writes a failure bundle rather than a clean result.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

PSSCRIPTANALYZER_VERSION = "1.9.0"
POWERSHELL_SUFFIXES = (".ps1", ".psm1")
MODES = ("off", "report", "enforce")
MAX_DIAGNOSTICS = 100
DEFAULT_TIMEOUT = 600


class AnalysisError(Exception):
    """A failure that must never be reported as a clean analysis."""


def _bounded(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit] + "[truncated]"


def _text(name: str, value: str, limit: int = 512) -> str:
    if (
        not isinstance(value, str)
        or len(value) > limit
        or any(char in value for char in "\0\r\n")
    ):
        raise AnalysisError(f"{name} must be a bounded single-line string")
    return value


def _relative_path(name: str, value: str) -> str:
    value = _text(name, value)
    if not value.strip():
        raise AnalysisError(f"{name} must not be empty")
    normalised = value.replace("\\", "/")
    if (
        value.startswith("-")
        or PurePosixPath(normalised).is_absolute()
        or ".." in PurePosixPath(normalised).parts
        or re.match(r"^[A-Za-z]:", value)
    ):
        raise AnalysisError(f"{name} must be a relative workspace path")
    return value


def paths_policy(paths: list[str], settings: str = "", severity: str = "") -> dict:
    if not 1 <= len(paths) <= 32:
        raise AnalysisError("paths must contain between 1 and 32 entries")
    if severity not in ("", "Error", "Warning", "Information"):
        raise AnalysisError("severity must be empty, Error, Warning or Information")
    return {
        "schema_version": 1,
        "pinned_version": PSSCRIPTANALYZER_VERSION,
        "paths": [_relative_path("paths", item) for item in paths],
        "settings": _relative_path("settings", settings) if settings else "",
        "severity": severity,
    }


def select_scripts(workspace: Path, paths: list[str]) -> list[Path]:
    """Collect .ps1/.psm1 files from explicit paths, or report the gap."""
    selected: list[Path] = []
    for entry in paths:
        target = workspace / entry
        if target.is_dir():
            selected += [
                item
                for item in sorted(target.rglob("*"))
                if item.is_file() and item.suffix.lower() in POWERSHELL_SUFFIXES
            ]
        elif target.is_file():
            selected.append(target)
        else:
            raise AnalysisError(f"input path not found: {entry}")
    # De-duplicate while keeping a stable order.
    seen: set[Path] = set()
    unique = []
    for item in selected:
        resolved = item.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(item)
    return unique


def invocation_script(helper_root: Path) -> Path:
    script = helper_root / ".dagger/src/engineering_ci/powershell_invoke.ps1"
    if not script.is_file():
        raise AnalysisError(f"invocation script not found under {helper_root}")
    return script


def _run_powershell(
    arguments: list[str], cwd: Path, timeout: int
) -> subprocess.CompletedProcess:
    executable = "pwsh.exe" if os.name == "nt" else "pwsh"
    try:
        return subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", *arguments],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise AnalysisError(f"PowerShell is unavailable: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AnalysisError(f"PowerShell timed out after {timeout}s") from exc


def _installed_module(timeout: int) -> Path | None:
    """Use an already-installed analyzer when its version matches the pin.

    PowerShell performs its own discovery here, so this respects the platform
    rather than re-implementing a module search. A different installed version
    is ignored, because the pin is what the evidence refers to.
    """
    command = (
        "$ErrorActionPreference='Stop';"
        f"$m = Get-Module -ListAvailable PSScriptAnalyzer | "
        f"Where-Object {{ $_.Version -eq '{PSSCRIPTANALYZER_VERSION}' }} | "
        "Select-Object -First 1; if ($m) { $m.ModuleBase }"
    )
    result = _run_powershell(["-Command", command], Path.cwd(), timeout)
    candidate = result.stdout.strip()
    if result.returncode == 0 and candidate and Path(candidate).is_dir():
        return Path(candidate)
    return None


def prepare_module(module_root: Path, timeout: int) -> tuple[Path, str]:
    """Resolve the pinned analyzer, downloading only when it is absent.

    Kept outside the consumer's checkout so analysis never writes into the tree
    it is inspecting. Returns the module directory and how it was obtained, so
    the evidence records whether anything was fetched at all.
    """
    installed = _installed_module(timeout)
    if installed is not None:
        return installed, "preinstalled"
    downloaded = module_root / f"PSScriptAnalyzer/{PSSCRIPTANALYZER_VERSION}"
    if (downloaded / "PSScriptAnalyzer.psd1").is_file():
        return downloaded, "cached"
    module_root.mkdir(parents=True, exist_ok=True)
    command = (
        "$ErrorActionPreference='Stop';"
        "Save-Module -Name PSScriptAnalyzer "
        f"-RequiredVersion {PSSCRIPTANALYZER_VERSION} "
        f"-Path '{module_root}' -Force"
    )
    result = _run_powershell(["-Command", command], module_root.parent, timeout)
    if result.returncode != 0 or not (downloaded / "PSScriptAnalyzer.psd1").is_file():
        raise AnalysisError(
            f"PSScriptAnalyzer {PSSCRIPTANALYZER_VERSION} could not be prepared: "
            f"{_bounded(result.stderr.strip() or result.stdout.strip(), 400)}"
        )
    return downloaded, "downloaded"


def _records(raw: str) -> tuple[dict, list[dict]]:
    lines = [line for line in raw.splitlines() if line.strip()]
    if not lines:
        raise AnalysisError("the analyzer returned no diagnostic envelope")
    try:
        envelope = json.loads(lines[0])
        diagnostics = [json.loads(line) for line in lines[1:]]
    except ValueError as exc:
        raise AnalysisError(
            f"the analyzer output is not valid JSON ({type(exc).__name__})"
        )
    if not isinstance(envelope, dict):
        raise AnalysisError("the analyzer envelope is not an object")
    return envelope, [item for item in diagnostics if isinstance(item, dict)]


def analyse(
    workspace: Path,
    helper_root: Path,
    policy: dict,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """Run one analysis and describe what the analyzer actually returned."""
    scripts = select_scripts(workspace, policy["paths"])
    if not scripts:
        return {
            "schema_version": 1,
            "tool": "psscriptanalyzer",
            "pinned_version": PSSCRIPTANALYZER_VERSION,
            "observed_version": None,
            "policy": {
                key: value
                for key, value in policy.items()
                if key not in {"schema_version", "pinned_version"}
            },
            "files_scanned": 0,
            "outcome": "not_executed",
            "reason": "no_applicable_scripts",
            "diagnostic_count": 0,
            "diagnostics": [],
            "omitted_diagnostics": 0,
            "limitations": [
                (
                    "No .ps1 or .psm1 file matched the requested paths; nothing "
                    "was analysed and no pass is established"
                )
            ],
        }
    module_root, module_source = prepare_module(
        Path(tempfile.gettempdir()) / "engineering-ci-pssa", timeout
    )
    arguments = [
        "-File",
        str(invocation_script(helper_root)),
        "-ModuleRoot",
        str(module_root),
        "-Version",
        policy["pinned_version"],
        "-Path",
        *[str(item) for item in scripts],
    ]
    if policy["settings"]:
        arguments += ["-Settings", policy["settings"]]
    if policy["severity"]:
        arguments += ["-Severity", policy["severity"]]
    result = _run_powershell(arguments, workspace, timeout)
    envelope, records = _records(result.stdout)
    if envelope.get("status") != "ok":
        raise AnalysisError(
            f"the analyzer failed: {_bounded(str(envelope.get('error') or ''), 400)}"
        )
    if result.returncode != 0:
        raise AnalysisError(
            f"the analyzer process exited {result.returncode}: "
            f"{_bounded(result.stderr.strip(), 400)}"
        )
    diagnostics = [
        {
            "rule": str(item.get("rule") or ""),
            "severity": str(item.get("severity") or ""),
            "message": str(item.get("message") or ""),
            "script": str(item.get("script") or ""),
            "line": item.get("line"),
            "column": item.get("column"),
        }
        for item in records
    ]
    return {
        "schema_version": 1,
        "tool": "psscriptanalyzer",
        "pinned_version": policy["pinned_version"],
        "observed_version": envelope.get("psscriptanalyzer_version"),
        "module_source": module_source,
        "policy": {
            key: value
            for key, value in policy.items()
            if key not in {"schema_version", "pinned_version"}
        },
        "files_scanned": envelope.get("files_scanned", len(scripts)),
        "outcome": "findings" if diagnostics else "clean",
        "reason": "rule_violations_reported" if diagnostics else "no_rule_violations",
        "diagnostic_count": len(diagnostics),
        "diagnostics": diagnostics[:MAX_DIAGNOSTICS],
        "omitted_diagnostics": max(0, len(diagnostics) - MAX_DIAGNOSTICS),
        "limitations": [],
    }


def summary(report: dict, mode: str) -> str:
    """Bounded presentation; the gate reads the report, never this text."""

    def cell(value, limit=300):
        return " ".join(str(value).split())[:limit]

    lines = [
        f"## PowerShell Analysis — {cell(report['outcome'].upper())}",
        "",
        f"Mode: **{mode}**",
        f"Files scanned: **{report.get('files_scanned', 0)}**",
        f"Analyzer: {report['pinned_version']}"
        + (
            f" (observed {cell(report['observed_version'])})"
            if report.get("observed_version")
            else ""
        )
        + (
            f", module source: {cell(report['module_source'])}"
            if report.get("module_source")
            else ""
        ),
        "",
    ]
    if report.get("limitations"):
        lines += ["Limitation: " + cell(item, 500) for item in report["limitations"]]
        lines += [""]
    if report.get("diagnostics"):
        lines += [
            "| Rule | Severity | Location | Message |",
            "| --- | --- | --- | --- |",
        ]
        for item in report["diagnostics"]:
            lines.append(
                f"| {cell(item['rule'], 120)} | {cell(item['severity'], 40)} | "
                f"{cell(Path(item['script']).name)}:{item.get('line') or '?'} | "
                f"{cell(item['message'], 240)} |"
            )
        if report.get("omitted_diagnostics"):
            lines += [
                "",
                f"{report['omitted_diagnostics']} further diagnostics omitted.",
            ]
    return "\n".join(lines) + "\n"


def write_bundle(directory: Path, report: dict, mode: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (directory / "summary.md").write_text(summary(report, mode), encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--helper-root", default=".")
    parser.add_argument("--paths", default=".")
    parser.add_argument("--mode", default="off", choices=MODES)
    parser.add_argument("--settings", default="")
    parser.add_argument("--severity", default="")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    if args.mode == "off":
        print("PowerShell analysis is off; no script was analysed")
        return 0
    workspace = Path(args.workspace).resolve()
    try:
        policy = paths_policy(
            [item for item in args.paths.split(",") if item.strip()],
            args.settings,
            args.severity,
        )
        report = analyse(workspace, Path(args.helper_root).resolve(), policy)
    except AnalysisError as exc:
        # A tool that cannot run must not be reported as a clean analysis.
        write_bundle(
            Path(args.out),
            {
                "schema_version": 1,
                "tool": "psscriptanalyzer",
                "pinned_version": PSSCRIPTANALYZER_VERSION,
                "outcome": "failure",
                "reason": "psscriptanalyzer_execution_error",
                "diagnostic_count": 0,
                "diagnostics": [],
                "omitted_diagnostics": 0,
                "limitations": [str(exc)],
            },
            args.mode,
        )
        print(f"PowerShell analysis failed: {exc}", file=sys.stderr)
        return 1

    write_bundle(Path(args.out), report, args.mode)
    # Enforce blocks on anything that is not a clean analysis, so an absent or
    # unusable scope can never be read as a passing check.
    return 1 if args.mode == "enforce" and report["outcome"] != "clean" else 0


if __name__ == "__main__":
    raise SystemExit(main())
