"""Pinned Workflow and PowerShell static analysis through the native tools.

Dagger-independent on purpose: this module only builds argument vectors,
enumerates the inputs a scan has to cover, classifies the exit status each tool
already produced and normalises the output it emitted. It never re-runs a check
to render a report, never re-implements actionlint's or PSScriptAnalyzer's
rules, and never decides which language a file is written in beyond its
explicit suffix.

Two different signals, deliberately handled differently:

- actionlint reports problems through its process exit status and text output.
- PSScriptAnalyzer reports diagnostics as PowerShell objects and leaves the
  process exit status at zero, so the diagnostics themselves decide the result
  and the process status only proves the invocation survived.

Neither result may become a pass by omission: an absent file, an unsupported
scope, a missing environment and a tool failure are each reported as such.

Input coverage is stated, not assumed. The required inputs of a scan are
enumerated from the declared scope, and the enumerated files are the files the
scanner is given. A scope that does not exist, or that holds no analysable
input, is reported as not applicable instead of as a clean run over nothing.

Verification status:

1. actionlint's exit-code semantics and text output format are observed by the
   `fixture_workflow_analysis` Check, which runs the pinned tool against
   committed clean and broken Workflows and asserts both outcomes. The
   diagnostic line format this module parses is the one that check observed:
   `<path>:<line>[:<col>]: <message> [<rule>]`. actionlint exposes no
   structured output, so `native_json_available` is legitimately always false
   for it.
2. No PSScriptAnalyzer run has been observed. Which default rules actually fire
   on a given script is a property of the analyzer, not of this module.
3. The Windows runner job has never executed. `windows-2025`, the pinned uv
   invocation, moving the helper checkout and the PYTHONPATH wiring are all
   unexercised.

Every classifier here fails closed, so a wrong assumption produces a failing
check that prints the observed value rather than a silent pass. The two
unobserved items may not be described as verified until a real run says so.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath

# The PyPI distribution is a thin wrapper that pins the Go tool's own release.
ACTIONLINT_WRAPPER = "1.7.12.25"
ACTIONLINT_VERSION = "1.7.12"
PSSCRIPTANALYZER_VERSION = "1.9.0"
# MCR publishes no 7.6 tag; 7.5 is the newest pinned minor available.
POWERSHELL_IMAGE = "mcr.microsoft.com/powershell:7.5-ubuntu-24.04"

MAX_NATIVE_BYTES = 1024 * 1024
MAX_STREAM_CHARS = 8000
MAX_FINDINGS = 100
MAX_SUMMARY_FINDINGS = 10

WORKFLOW_SUFFIXES = (".yml", ".yaml")
POWERSHELL_SUFFIXES = (".ps1", ".psm1")
# actionlint reads its own configuration from these names. They are tool
# configuration, not Workflow inputs, so they are never part of the scope a
# required scan has to cover.
ACTIONLINT_CONFIG_NAMES = ("actionlint.yaml", "actionlint.yml")

_ACTIONLINT_LINE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+)(?:[:-]\d+)?(?:[:-]\d+)?: "
    r"(?P<message>.*?)(?: \[(?P<rule>[a-z][a-z0-9-]*)\])?$"
)


def _text(name: str, value: str, limit: int = 512) -> str:
    if (
        not isinstance(value, str)
        or len(value) > limit
        or any(char in value for char in "\0\r\n")
    ):
        raise ValueError(f"{name} must be a bounded single-line string")
    return value


def _relative_path(name: str, value: str) -> str:
    """Reject anything that could escape the workspace or become a flag."""
    value = _text(name, value)
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
    normalised = value.replace("\\", "/")
    if (
        value.startswith("-")
        or PurePosixPath(normalised).is_absolute()
        or ".." in PurePosixPath(normalised).parts
        or re.match(r"^[A-Za-z]:", value)
    ):
        raise ValueError(f"{name} must be a relative workspace path")
    return value


def path_list(name: str, values: list[str] | None, default: list[str]) -> list[str]:
    items = list(default if values is None else values)
    if not 1 <= len(items) <= 32:
        raise ValueError(f"{name} must contain between 1 and 32 entries")
    return [_relative_path(name, item) for item in items]


def target_kind(scan_path: str) -> str:
    """Select a tool from the explicit suffix, never from file contents."""
    suffix = PurePosixPath(scan_path.replace("\\", "/")).suffix.lower()
    if suffix in WORKFLOW_SUFFIXES:
        return "actionlint"
    if suffix in POWERSHELL_SUFFIXES:
        return "psscriptanalyzer"
    return "unsupported"


def select_workflows(root: Path, paths: list[str]) -> dict:
    """Enumerate the Workflow files a required scan has to cover.

    The scope is read exactly as the scanner reads it, so the enumerated inputs
    are the coverage the report can claim. A scope that does not exist stays
    absent instead of becoming an empty pass, and a scope that holds no
    analysable Workflow is reported as present but not applicable.

    Only the declared suffix selects a file, so a directory that also holds
    unrelated files contributes just its Workflows.
    """
    root = Path(root).resolve()
    present = False
    found: list[str] = []
    for entry in paths:
        target = root / entry
        if target.is_dir():
            present = True
            for item in sorted(target.rglob("*")):
                if not item.is_file() or item.name.lower() in ACTIONLINT_CONFIG_NAMES:
                    continue
                if item.suffix.lower() in WORKFLOW_SUFFIXES:
                    found.append(item.relative_to(root).as_posix())
        elif target.is_file():
            present = True
            if target.suffix.lower() in WORKFLOW_SUFFIXES:
                found.append(target.relative_to(root).as_posix())
    inputs = sorted(set(found))
    return {
        "present": present,
        "inputs": inputs,
        "input_count": len(inputs),
        "applicable": present and bool(inputs),
    }


def coverage(scope: list[str], selected: dict) -> dict:
    """Describe what a required scan had to cover and what it did cover."""
    return {
        "scope": list(scope),
        "present": selected["present"],
        "inputs": selected["inputs"],
        "input_count": selected["input_count"],
        "applicable": selected["applicable"],
    }


def actionlint_policy(
    scan_paths: list[str] | None = None,
    config: str = "",
) -> dict:
    """Workflow files only; the tool reads its own configuration."""
    return {
        "schema_version": 1,
        "tool": "actionlint",
        "pinned_version": ACTIONLINT_VERSION,
        "wrapper_version": ACTIONLINT_WRAPPER,
        "scan_paths": path_list("scan_paths", scan_paths, [".github/workflows"]),
        "config": _relative_path("config", config) if config else "",
    }


def powershell_policy(
    scan_paths: list[str] | None = None,
    platform: str = "any",
    target_powershell_version: str = "",
    settings: str = "",
    severity: str = "",
) -> dict:
    """PowerShell scripts only, analysed without executing or rewriting them."""
    if platform not in ("any", "windows"):
        raise ValueError("platform must be any or windows")
    if severity not in ("", "Error", "Warning", "Information"):
        raise ValueError("severity must be empty, Error, Warning or Information")
    return {
        "schema_version": 1,
        "tool": "psscriptanalyzer",
        "pinned_version": PSSCRIPTANALYZER_VERSION,
        "image": POWERSHELL_IMAGE,
        "scan_paths": path_list("scan_paths", scan_paths, ["."]),
        "platform": platform,
        "target_powershell_version": (
            _text("target_powershell_version", target_powershell_version)
            if target_powershell_version
            else ""
        ),
        "settings": _relative_path("settings", settings) if settings else "",
        "severity": severity,
    }


def runner_prefix(tool: str) -> list[str]:
    """Run the pinned Workflow checker in its own environment.

    Same isolation as the Python scanners: uvx never touches the consumer's
    own dependencies or lockfile.
    """
    if tool != "actionlint":
        raise ValueError(f"{tool} does not run through uvx")
    return ["uvx", "--from", f"actionlint-py=={ACTIONLINT_WRAPPER}", "actionlint"]


def actionlint_arguments(policy: dict, inputs: list[str] | None = None) -> list[str]:
    """Arguments for the pinned Workflow checker.

    The scanner is given the enumerated inputs rather than the scope itself,
    so a clean result means every required input was analysed. The scope stays
    the fallback when no enumeration was made.
    """
    args = [
        "-no-color",
        # Optional integrations stay explicitly off so the checked scope never
        # changes with whatever happens to be installed in the image.
        "-shellcheck=",
        "-pyflakes=",
    ]
    if policy["config"]:
        args.append(f"-config-file={policy['config']}")
    return args + list(policy["scan_paths"] if inputs is None else inputs)


def powershell_arguments(policy: dict) -> list[str]:
    """Arguments for the generated invocation script, not for pwsh itself.

    `TargetPowerShellVersions` is a PSScriptAnalyzer settings property rather
    than an Invoke-ScriptAnalyzer parameter, so a target version reaches the
    analyzer only through the consumer's own Settings file.
    """
    args = [
        "-Version",
        policy["pinned_version"],
        "-Path",
        *policy["scan_paths"],
    ]
    if policy["settings"]:
        args += ["-Settings", policy["settings"]]
    if policy["severity"]:
        args += ["-Severity", policy["severity"]]
    return args


def environment_gap(policy: dict) -> str | None:
    """Name the environment this check cannot honour, or None when it can.

    PowerShell 7 runs cross-platform, which is the environment this check
    provides. A script that declares Windows-only requirements is reported as
    not executed with the reason spelled out, because scheduling a Windows
    runner job belongs to the reusable Workflow integration.
    """
    if policy["platform"] == "windows":
        return (
            "Windows-only requirements were declared; this check runs in "
            "cross-platform PowerShell 7 and cannot honour them. Scheduling a "
            "Windows runner job belongs to the reusable Workflow integration."
        )
    target = policy["target_powershell_version"]
    if target:
        major = target.split(".", 1)[0]
        if major.isdigit() and int(major) < 7:
            return (
                f"Target PowerShell {target} is Windows PowerShell; this check "
                "runs in cross-platform PowerShell 7. Use the consumer's "
                "PSScriptAnalyzer Settings to express version compatibility."
            )
    return None


def _bounded_stream(text: str) -> str:
    return text if len(text) <= MAX_STREAM_CHARS else text[-MAX_STREAM_CHARS:]


def _actionlint_facts(stdout: str, _raw: str):
    findings = []
    for line in stdout.splitlines():
        match = _ACTIONLINT_LINE.match(line.strip())
        if not match:
            continue
        findings.append(
            {
                "rule": match.group("rule") or "",
                "path": match.group("path"),
                "line": int(match.group("line")),
                "message": match.group("message"),
            }
        )
    return findings, None, None, []


def _powershell_facts(_stdout: str, raw: str):
    """Read the JSON Lines records the invocation script already printed.

    The first record is the envelope; every following record is one
    diagnostic. Reading them separately avoids the single-document array
    collapse that would otherwise hide a lone diagnostic.
    """
    lines = [line for line in raw.splitlines() if line.strip()]
    if not lines:
        return [], None, None, ["PowerShell invocation produced no diagnostic envelope"]
    try:
        envelope = json.loads(lines[0])
    except ValueError:
        return (
            [],
            None,
            None,
            ["PowerShell invocation produced an unreadable diagnostic envelope"],
        )
    if not isinstance(envelope, dict):
        return [], None, None, ["PowerShell invocation envelope is not an object"]
    findings = []
    for line in lines[1:]:
        try:
            item = json.loads(line)
        except ValueError:
            return (
                [],
                None,
                None,
                ["A PowerShell diagnostic record is not valid JSON"],
            )
        if not isinstance(item, dict):
            return (
                [],
                None,
                None,
                ["A PowerShell diagnostic record is not an object"],
            )
        findings.append(
            {
                "rule": item.get("rule") or "",
                "path": item.get("script") or "",
                "line": item.get("line"),
                "message": item.get("message") or "",
                "severity": item.get("severity") or "",
            }
        )
    observed = envelope.get("psscriptanalyzer_version")
    return findings, envelope, observed if isinstance(observed, str) else None, []


FACTS = {
    "actionlint": _actionlint_facts,
    "psscriptanalyzer": _powershell_facts,
}


def _classify_actionlint(exit_code, findings, envelope, limitations) -> tuple[str, str]:
    if exit_code == 0:
        return "clean", "no_workflow_problems"
    if exit_code == 1 and findings:
        return "findings", "workflow_problems_reported"
    return "failure", "actionlint_execution_error"


def _classify_powershell(exit_code, findings, envelope, limitations) -> tuple[str, str]:
    # The process status is secondary: PowerShell exits zero even when the
    # analyzer reports violations, so the diagnostics decide the outcome.
    if not isinstance(envelope, dict):
        return "failure", "psscriptanalyzer_report_unavailable"
    if envelope.get("status") != "ok":
        return "failure", "psscriptanalyzer_execution_error"
    if exit_code != 0:
        return "failure", "powershell_invocation_error"
    if findings:
        return "findings", "rule_violations_reported"
    # A clean result requires positive proof that scripts were analysed; an
    # absent or unusable count never authorises a pass.
    scanned = envelope.get("files_scanned")
    if type(scanned) is not int or scanned <= 0:
        return "not_executed", "no_applicable_scripts"
    return "clean", "no_rule_violations"


CLASSIFIERS = {
    "actionlint": _classify_actionlint,
    "psscriptanalyzer": _classify_powershell,
}

_REPORT_ONLY_POLICY_KEYS = {
    "schema_version",
    "tool",
    "pinned_version",
    "wrapper_version",
    "image",
}


def _shell(
    tool: str,
    pinned_version: str | None,
    scan_paths: list[str],
    outcome: str,
    reason: str,
    covered: dict | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "tool": tool,
        "pinned_version": pinned_version,
        "observed_version": None,
        "policy": {"scan_paths": scan_paths},
        "exit_code": None,
        "outcome": outcome,
        "reason": reason,
        "finding_count": 0,
        "findings": [],
        "omitted_findings": 0,
        "limitations": [reason],
        "coverage": covered or _no_coverage(scan_paths),
        "native_json_available": False,
        "native_json": None,
        "native_truncated": False,
        "stdout": "",
        "stderr": "",
    }


def _no_coverage(scan_paths: list[str]) -> dict:
    """Coverage for a scope this module did not analyse at all."""
    return {
        "scope": list(scan_paths),
        "present": False,
        "inputs": [],
        "input_count": 0,
        "applicable": False,
    }


def unsupported_report(scan_path: str, reason: str) -> dict:
    """A scope this issue deliberately does not cover, stated rather than passed."""
    return _shell("none", None, [scan_path], "not_executed", reason)


def deferred_report(scan_path: str, reason: str) -> dict:
    """A scope another execution location owns, stated rather than passed."""
    return _shell(
        "psscriptanalyzer",
        PSSCRIPTANALYZER_VERSION,
        [scan_path],
        "not_executed",
        reason,
    )


def not_applicable_report(policy: dict, covered: dict, reason: str) -> dict:
    """A required scope that holds nothing to analyse, stated rather than passed.

    The scanner is not started at all in this case: a scan over an empty scope
    would report a clean result over nothing and hide the missing coverage.
    """
    report = _shell(
        policy["tool"],
        policy["pinned_version"],
        list(policy.get("scan_paths", [])),
        "not_executed",
        reason,
        covered,
    )
    report["policy"] = {
        key: value
        for key, value in policy.items()
        if key not in _REPORT_ONLY_POLICY_KEYS
    }
    return report


def script_report(
    policy: dict,
    exit_code: int,
    stdout: str = "",
    stderr: str = "",
    native_raw: str = "",
    covered: dict | None = None,
) -> dict:
    """Normalise one finished check. Nothing here starts a second execution."""
    if type(exit_code) is not int or not 0 <= exit_code <= 255:
        raise ValueError("Invalid original tool exit code")
    tool = policy["tool"]
    if tool not in CLASSIFIERS:
        raise ValueError(f"Unknown script-analysis tool: {tool}")
    truncated = len(native_raw.encode("utf-8")) > MAX_NATIVE_BYTES
    findings, envelope, observed, limitations = FACTS[tool](
        stdout, "" if truncated else native_raw
    )
    if truncated:
        limitations = [*limitations, "native structured output exceeds the 1 MiB limit"]
    outcome, reason = CLASSIFIERS[tool](exit_code, findings, envelope, limitations)
    return {
        "schema_version": 1,
        "tool": tool,
        "pinned_version": policy["pinned_version"],
        "observed_version": observed,
        "policy": {
            key: value
            for key, value in policy.items()
            if key not in _REPORT_ONLY_POLICY_KEYS
        },
        "exit_code": exit_code,
        "outcome": outcome,
        "reason": reason,
        "finding_count": len(findings),
        "findings": findings[:MAX_FINDINGS],
        "omitted_findings": max(0, len(findings) - MAX_FINDINGS),
        "limitations": limitations,
        "coverage": covered or _no_coverage(list(policy.get("scan_paths", []))),
        "native_json_available": envelope is not None,
        "native_json": envelope,
        "native_truncated": truncated,
        "stdout": _bounded_stream(stdout),
        "stderr": _bounded_stream(stderr),
    }


def findings_summary(report: dict) -> str:
    """Compact explanation for a Dagger function that raises on demand."""
    lines = [
        (
            f"{report['tool']} {report['pinned_version']} exit {report['exit_code']} "
            f"({report['outcome']}): {report['reason']}"
        )
    ]
    covered = report.get("coverage")
    if covered:
        lines.append(
            "coverage: "
            f"{covered['input_count']} input(s) in {', '.join(covered['scope']) or '-'} "
            f"(applicable: {covered['applicable']})"
        )
    for item in report["findings"][:MAX_SUMMARY_FINDINGS]:
        location = f"{item.get('path') or '?'}:{item.get('line') or '?'}"
        rule = f" [{item['rule']}]" if item.get("rule") else ""
        lines.append(f"- {location}{rule} {item.get('message') or ''}".rstrip())
    if report["omitted_findings"]:
        lines.append(f"{report['omitted_findings']} additional findings omitted")
    lines += [f"limitation: {item}" for item in report["limitations"]]
    if not report["findings"]:
        tail = (report["stderr"] or report["stdout"] or "no diagnostics")[-1500:]
        lines.append(tail)
    return "\n".join(lines)


def main(argv=None) -> int:
    """Enumerate the inputs a required scan has to cover.

    The enumeration runs inside the same container the scanner reads, so the
    scope it reports is exactly the scope the scanner will see.
    """
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    scope = sub.add_parser("scope")
    scope.add_argument("--root", required=True)
    scope.add_argument("--path", action="append", required=True)
    args = parser.parse_args(argv)
    if args.command == "scope":
        selected = select_workflows(Path(args.root), list(args.path))
        print(json.dumps(selected, ensure_ascii=False, sort_keys=True))
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
