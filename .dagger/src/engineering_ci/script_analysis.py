"""Pinned Workflow and PowerShell static analysis through the native tools.

Dagger-independent on purpose: this module only builds argument vectors,
classifies the exit status each tool already produced and normalises the output
it emitted. It never re-runs a check to render a report, never re-implements
actionlint's or PSScriptAnalyzer's rules, and never decides which language a
file is written in beyond its explicit suffix.

Two different signals, deliberately handled differently:

- actionlint reports problems through its process exit status and text output.
- PSScriptAnalyzer reports diagnostics as PowerShell objects and leaves the
  process exit status at zero, so the diagnostics themselves decide the result
  and the process status only proves the invocation survived.

Neither result may become a pass by omission: an absent file, an unsupported
scope, a missing environment and a tool failure are each reported as such.

Verification status — observed against the real tools on 2026-10-31:

1. actionlint 1.7.12 was run. Exit 0 with no output on a clean Workflow set;
   exit 1 with diagnostics reading `<path>:<line>:<col>: <message> [<rule>]`
   for a broken one; exit 3 with `could not read "<path>"` for a path that
   does not exist. The rules it emitted were `syntax-check` and `expression`.
   It exposes no structured output, so `native_json_available` is legitimately
   always false for it.
2. PSScriptAnalyzer 1.9.0 was run. The process exits 0 even when it reports
   violations, and the diagnostics decide the outcome. The analyzer reports
   the files it scanned, so a partial scan is distinguishable from a clean one.
3. actionlint could not read a *directory* argument on the Windows host used
   for that observation, so the scope is enumerated and the files are passed
   explicitly. The Linux container this module runs in is unaffected; the
   explicit form simply behaves the same everywhere.

Still NOT VERIFIED:

- The Windows runner job has never executed. `windows-2025`, the pinned uv
  invocation, moving the helper checkout and the PYTHONPATH wiring are all
  unexercised.
- The Dagger container has not been run for these tools, so the Linux
  behaviour of the pinned actionlint wrapper is inherited from the host
  observation rather than observed directly.

Every classifier here fails closed, so a wrong assumption produces a failing
check that prints the observed value rather than a silent pass.
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

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


def actionlint_arguments(policy: dict, files: list[str] | None = None) -> list[str]:
    """Arguments for one actionlint invocation.

    `files` replaces the declared scope with the exact files that were
    enumerated from it. Passing the enumerated files rather than the scope
    itself makes the covered input set explicit, and makes the invocation
    reproducible on every platform instead of relying on the tool's own
    directory handling.
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
    return args + (policy["scan_paths"] if files is None else files)


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

# The outcomes one script check can reach. `clean` and `findings` are the two
# results of an executed scan. `not_applicable` is a positive proof that the
# declared scope holds nothing of the required kind. `not_executed` and
# `failure` are failures; neither ever authorises a pass.
OUTCOMES = ("clean", "findings", "not_applicable", "not_executed", "failure")

# Only a clean result establishes that the required input set was covered and
# nothing was found in it.
PASSING_OUTCOMES = ("clean",)


def coverage_record(scope: list[str], candidates: list[str], analysed) -> dict:
    """Record the input set a check was meant to cover and what it covered.

    `candidates` are the files the declared scope actually contains.
    `analysed` is how many the tool processed, or None when the tool does not
    report that. An empty scope and an incomplete scan are different facts,
    and neither is a clean result.
    """
    return {
        "scope": list(scope),
        "candidates": len(candidates),
        "analysed": analysed,
    }


def _classify_actionlint(
    exit_code, findings, envelope, limitations, coverage
) -> tuple[str, str]:
    # actionlint reads every file it is given and exits 3 when it cannot read
    # one, so a completed invocation covers exactly the enumerated scope.
    if coverage.get("candidates") == 0:
        return "not_applicable", "no_applicable_inputs"
    if exit_code == 0:
        return "clean", "no_workflow_problems"
    if exit_code == 1 and findings:
        return "findings", "workflow_problems_reported"
    return "failure", "actionlint_execution_error"


def _classify_powershell(
    exit_code, findings, envelope, limitations, coverage
) -> tuple[str, str]:
    # The process status is secondary: PowerShell exits zero even when the
    # analyzer reports violations, so the diagnostics decide the outcome.
    if coverage.get("candidates") == 0:
        return "not_applicable", "no_applicable_inputs"
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
    # Every file the scope contains has to have been analysed. A partial scan
    # is a coverage failure, never a clean result.
    candidates = coverage.get("candidates")
    if candidates is not None and scanned != candidates:
        return "not_executed", "incomplete_input_coverage"
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
    tool: str, pinned_version: str, scan_paths: list[str], outcome: str, reason: str
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
        "coverage": {
            "scope": list(scan_paths),
            "candidates": None,
            "analysed": None,
        },
        "finding_count": 0,
        "findings": [],
        "omitted_findings": 0,
        "limitations": [reason],
        "native_json_available": False,
        "native_json": None,
        "native_truncated": False,
        "stdout": "",
        "stderr": "",
    }


def empty_scope_report(policy: dict, scope: list[str]) -> dict:
    """A declared scope that provably holds nothing of the required kind.

    The tool is not run, so there is no exit status to report. The report
    states the empty scope instead of inventing one, and the empty scope is
    never presented as a clean result.
    """
    report = _shell(
        policy["tool"],
        policy["pinned_version"],
        list(scope),
        "not_applicable",
        "no_applicable_inputs",
    )
    report["policy"] = {
        key: value
        for key, value in policy.items()
        if key not in _REPORT_ONLY_POLICY_KEYS
    }
    report["coverage"] = coverage_record(scope, [], 0)
    return report


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


def script_report(
    policy: dict,
    exit_code: int,
    stdout: str = "",
    stderr: str = "",
    native_raw: str = "",
    coverage: dict | None = None,
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
    covered = coverage or {
        "scope": list(policy.get("scan_paths", [])),
        "candidates": None,
        "analysed": None,
    }
    outcome, reason = CLASSIFIERS[tool](
        exit_code, findings, envelope, limitations, covered
    )
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
        "coverage": covered,
        "finding_count": len(findings),
        "findings": findings[:MAX_FINDINGS],
        "omitted_findings": max(0, len(findings) - MAX_FINDINGS),
        "limitations": limitations,
        "native_json_available": envelope is not None,
        "native_json": envelope,
        "native_truncated": truncated,
        "stdout": _bounded_stream(stdout),
        "stderr": _bounded_stream(stderr),
    }


def enforce(report: dict) -> str:
    """Turn one finished report into a gate decision.

    The report already holds the scanner's own exit status and its own
    diagnostics, so enforcing reads that result instead of running the
    scanner a second time or re-interpreting its native output. Only a clean
    result passes; an empty scope, an incomplete scan, a finding and a tool
    failure all fail, each with the reason the report recorded.
    """
    if report["outcome"] in PASSING_OUTCOMES:
        return f"{report['tool']} {report['pinned_version']}: clean"
    raise RuntimeError(findings_summary(report))


def findings_summary(report: dict) -> str:
    """Compact explanation for a Dagger function that raises on demand."""
    lines = [
        (
            f"{report['tool']} {report['pinned_version']} exit {report['exit_code']} "
            f"({report['outcome']}): {report['reason']}"
        )
    ]
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
