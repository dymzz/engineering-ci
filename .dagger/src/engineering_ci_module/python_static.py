"""Pinned Python static-analysis scanners driven through their native CLIs.

Dagger-independent on purpose: this module only builds argument vectors,
classifies the exit code the tool already produced and normalises the output
that tool emitted. It never re-runs a scan to render a report and never
re-implements a scanner's diagnostics.

A non-zero status counts as a finding only where the tool's own semantics say
so; a crash, a configuration error and an input error stay failures and never
turn into a pass.

Verification status of the tables below:

All four tools were executed against their pinned versions, and every code in
these tables was observed directly rather than taken from documentation:

- vulture: 0 clean, 3 dead code, 1 unparsable file or missing path.
- pyright: 0 clean, 1 type errors, 3 unparsable config, 4 missing path, and a
  `filesAnalyzed` of 0 under the tool's own exclude rules.
- deptry: 0 clean, 1 with a structured report, 2 invalid input. deptry also
  exits 1 from an uncaught exception, which is why the report is required.
- pylint: 0 clean, 8 for the R0801 refactor message, with 1/2/32 reserved for
  fatal, error and usage failures. A duplicate group yields exactly one R0801
  that names both members inside its message.

Every table also fails closed: a code path that did not behave as described
becomes a `failure`, never a pass, so an unconfirmed reading cannot let a broken
scanner through.
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

SCANNER_VERSIONS = {
    "vulture": "2.16",
    "pyright": "1.1.414",
    "deptry": "0.25.1",
    "pylint": "4.1.3",
}

MAX_NATIVE_BYTES = 1024 * 1024
MAX_STREAM_CHARS = 8000
MAX_FINDINGS = 100
MAX_SUMMARY_FINDINGS = 10

# Pyright reports an unresolved import as an ordinary diagnostic, but type
# checking is then incomplete. Keep it separately so a caller can state the
# limit instead of presenting a partial run as a clean one.
PYRIGHT_IMPORT_RULES = frozenset(
    {
        "reportMissingImports",
        "reportMissingModuleSource",
        "reportMissingTypeStubs",
    }
)

# pylint encodes the emitted message categories in the exit status. 1/2/32 are
# fatal, error and usage error; 4/8/16 are warning, refactor and convention.
_PYLINT_FINDING_BITS = 4 | 8 | 16
_PYLINT_FAILURE_BITS = 1 | 2 | 32

_PYRIGHT_FAILURES = {
    2: "fatal_error",
    3: "configuration_error",
    4: "invalid_input",
}

_VULTURE_LINE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+): (?P<message>.+?)"
    r"(?: \((?P<confidence>\d+)% confidence\))?$"
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


def _integer(name: str, value: int, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer between {low} and {high}")
    return value


def _tool(name: str) -> tuple[str, str]:
    if name not in SCANNER_VERSIONS:
        raise ValueError(f"Unknown static-analysis scanner: {name}")
    return name, SCANNER_VERSIONS[name]


def _policy(tool: str, **options) -> dict:
    name, version = _tool(tool)
    return {
        "schema_version": 1,
        "tool": name,
        "pinned_version": version,
        **options,
    }


def vulture_policy(
    scan_path: str = ".",
    min_confidence: int = 0,
    exclude: str = "",
    whitelist_path: str = "",
) -> dict:
    """Vulture reports suspected dead code; an extra path acts as its whitelist."""
    return _policy(
        "vulture",
        scan_path=_relative_path("scan_path", scan_path),
        min_confidence=_integer("min_confidence", min_confidence, 0, 100),
        exclude=_text("exclude", exclude),
        whitelist_path=(
            _relative_path("whitelist_path", whitelist_path) if whitelist_path else ""
        ),
    )


def pyright_policy(
    scan_path: str = ".",
    config: str = "",
    level: str = "",
    python_path: str = "",
    python_version: str = "",
) -> dict:
    """Pyright keeps its own config discovery; only explicit inputs are added."""
    if level not in ("", "error", "warning", "information"):
        raise ValueError("level must be empty, error, warning or information")
    if python_path:
        python_path = _text("python_path", python_path)
        # Absolute is expected here: it names a path inside the check container.
        if not python_path.startswith("/") or ".." in PurePosixPath(python_path).parts:
            raise ValueError("python_path must be an absolute container path")
    return _policy(
        "pyright",
        scan_path=_relative_path("scan_path", scan_path),
        config=_relative_path("config", config) if config else "",
        level=level,
        python_path=python_path,
        python_version=_text("python_version", python_version)
        if python_version
        else "",
    )


def deptry_policy(
    scan_path: str = "src",
    config: str = "",
    extend_exclude: str = "",
    per_rule_ignores: str = "",
) -> dict:
    """deptry compares declared dependencies against the imported modules."""
    return _policy(
        "deptry",
        scan_path=_relative_path("scan_path", scan_path),
        config=_relative_path("config", config) if config else "",
        extend_exclude=_text("extend_exclude", extend_exclude),
        per_rule_ignores=_text("per_rule_ignores", per_rule_ignores),
    )


def pylint_policy(
    scan_paths: list[str] | None = None,
    min_similarity_lines: int = 4,
    rcfile: str = "",
    ignore_paths: list[str] | None = None,
) -> dict:
    """Only R0801 duplicate-code is enabled; Ruff already covers the rest."""
    targets = list(scan_paths if scan_paths is not None else ["src"])
    ignored = list(ignore_paths if ignore_paths is not None else [])
    if not 1 <= len(targets) <= 32:
        raise ValueError("scan_paths must contain between 1 and 32 entries")
    if len(ignored) > 32:
        raise ValueError("ignore_paths must contain at most 32 entries")
    return _policy(
        "pylint",
        scan_paths=[_relative_path("scan_paths", item) for item in targets],
        min_similarity_lines=_integer(
            "min_similarity_lines", min_similarity_lines, 1, 100
        ),
        rcfile=_relative_path("rcfile", rcfile) if rcfile else "",
        ignore_paths=[_relative_path("ignore_paths", item) for item in ignored],
    )


def runner_prefix(tool: str, pinned_version: str) -> list[str]:
    """Run a pinned scanner in its own environment.

    uvx never touches the consumer's environment or lockfile, and the shared
    uv cache keeps repeat runs off the network.
    """
    name, version = _tool(tool)
    if pinned_version != version:
        raise ValueError(f"{name} is pinned to {version}")
    return ["uvx", "--from", f"{name}=={version}", name]


def vulture_arguments(policy: dict, native_output: str = "") -> list[str]:
    args = ["--min-confidence", str(policy["min_confidence"])]
    if policy["exclude"]:
        args += ["--exclude", policy["exclude"]]
    if policy["whitelist_path"]:
        args.append(policy["whitelist_path"])
    return args + [policy["scan_path"]]


def pyright_arguments(policy: dict, native_output: str = "") -> list[str]:
    # --outputjson moves the native report onto stdout, so one execution still
    # yields the diagnostics, the counts and the tool's own version string.
    args = ["--outputjson"]
    if policy["config"]:
        args += ["--project", policy["config"]]
    if policy["level"]:
        args += ["--level", policy["level"]]
    if policy["python_path"]:
        args += ["--pythonpath", policy["python_path"]]
    if policy["python_version"]:
        args += ["--pythonversion", policy["python_version"]]
    return args + [policy["scan_path"]]


def deptry_arguments(policy: dict, native_output: str = "") -> list[str]:
    args = ["--no-ansi"]
    if policy["config"]:
        args += ["--config", policy["config"]]
    if policy["extend_exclude"]:
        args += ["--extend-exclude", policy["extend_exclude"]]
    if policy["per_rule_ignores"]:
        args += ["--per-rule-ignores", policy["per_rule_ignores"]]
    if native_output:
        # deptry only writes its structured report to a file; the same run
        # still produced the text diagnostics on stderr.
        args += ["--json-output", native_output]
    return args + [policy["scan_path"]]


def pylint_arguments(policy: dict, native_output: str = "") -> list[str]:
    args = [
        "--disable=all",
        "--enable=duplicate-code",
        "--min-similarity-lines",
        str(policy["min_similarity_lines"]),
        "--persistent=no",
        # Findings without the structured report would drop the native
        # diagnostics, so the same execution must emit them as JSON.
        "--output-format=json",
    ]
    if policy["rcfile"]:
        args.append(f"--rcfile={policy['rcfile']}")
    if policy["ignore_paths"]:
        args += ["--ignore-paths", ",".join(policy["ignore_paths"])]
    return args + policy["scan_paths"]


ARGUMENTS = {
    "vulture": vulture_arguments,
    "pyright": pyright_arguments,
    "deptry": deptry_arguments,
    "pylint": pylint_arguments,
}


def _bounded_stream(text: str) -> str:
    return text if len(text) <= MAX_STREAM_CHARS else text[-MAX_STREAM_CHARS:]


def _load_native(raw: str) -> tuple[object | None, str | None]:
    if not raw.strip():
        return None, None
    if len(raw.encode("utf-8")) > MAX_NATIVE_BYTES:
        return None, "native structured output exceeds the 1 MiB report limit"
    try:
        return json.loads(raw), None
    except ValueError as exc:
        return None, f"native output is not valid JSON ({type(exc).__name__})"


def _vulture_facts(stdout: str) -> tuple[list[dict], list[str], None]:
    findings = []
    for line in stdout.splitlines():
        match = _VULTURE_LINE.match(line.strip())
        if not match:
            continue
        confidence = match.group("confidence")
        findings.append(
            {
                "rule": "unused-code",
                "path": match.group("path"),
                "line": int(match.group("line")),
                "message": match.group("message"),
                "confidence": int(confidence) if confidence else None,
            }
        )
    return findings, [], None


def _pyright_facts(payload: object) -> tuple[list[dict], list[str], str | None]:
    if not isinstance(payload, dict):
        return [], [], None
    limitations, findings, unresolved = [], [], 0
    for item in payload.get("generalDiagnostics") or []:
        if not isinstance(item, dict):
            continue
        start = (item.get("range") or {}).get("start") or {}
        findings.append(
            {
                "rule": item.get("rule") or item.get("category") or "diagnostic",
                "path": item.get("file") or "",
                "line": start.get("line", 0) + 1,
                "message": item.get("message") or "",
                "severity": item.get("severity") or "",
            }
        )
        if item.get("rule") in PYRIGHT_IMPORT_RULES:
            unresolved += 1
    summary = payload.get("summary")
    if isinstance(summary, dict) and summary.get("filesAnalyzed") == 0:
        limitations.append(
            "pyright analyzed 0 files; the requested scope holds no applicable "
            "Python source under pyright's own exclude rules"
        )
    if unresolved:
        limitations.append(
            f"{unresolved} import(s) did not resolve in the check environment; "
            "type results for those modules are incomplete"
        )
    return findings, limitations, payload.get("version")


def _deptry_facts(payload: object) -> tuple[list[dict], list[str], None]:
    if not isinstance(payload, list):
        return [], [], None
    findings = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        error, location = item.get("error") or {}, item.get("location") or {}
        findings.append(
            {
                "rule": error.get("code") or "",
                "path": location.get("file") or "",
                "line": location.get("line"),
                "message": error.get("message") or "",
                "module": item.get("module") or "",
            }
        )
    return findings, [], None


def _pylint_facts(payload: object) -> tuple[list[dict], list[str], None]:
    if not isinstance(payload, list):
        return [], [], None
    findings = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        findings.append(
            {
                "rule": item.get("message-id") or "",
                "path": item.get("path") or "",
                "line": item.get("line"),
                "message": item.get("message") or "",
                "symbol": item.get("symbol") or "",
            }
        )
    return findings, [], None


FACTS = {
    "vulture": _vulture_facts,
    "pyright": _pyright_facts,
    "deptry": _deptry_facts,
    "pylint": _pylint_facts,
}


def _classify_vulture(exit_code, findings, native, limitations) -> tuple[str, str]:
    if exit_code == 0:
        return "clean", "no_dead_code_found"
    if exit_code == 3:
        return "findings", "dead_code_found"
    # 1 covers an unparsable file and a missing path; neither is a finding.
    return "failure", "vulture_execution_error"


def _classify_pyright(exit_code, findings, native, limitations) -> tuple[str, str]:
    if exit_code == 0:
        if any("analyzed 0 files" in item for item in limitations):
            return "not_executed", "no_applicable_source"
        return "clean", "no_type_errors"
    if exit_code == 1:
        return "findings", "type_errors_reported"
    return "failure", _PYRIGHT_FAILURES.get(exit_code, "pyright_execution_error")


def _classify_deptry(exit_code, findings, native, limitations) -> tuple[str, str]:
    if exit_code == 0:
        return "clean", "no_dependency_issues"
    if exit_code == 1 and isinstance(native, list):
        # deptry also exits 1 from an uncaught exception; only its structured
        # report proves the run reached the point where issues are collected.
        return "findings", "dependency_issues_found"
    if exit_code == 2:
        return "failure", "invalid_input"
    return "failure", "deptry_execution_error"


def _classify_pylint(exit_code, findings, native, limitations) -> tuple[str, str]:
    if exit_code == 0:
        return "clean", "no_duplicate_code"
    if exit_code & _PYLINT_FAILURE_BITS:
        return "failure", "pylint_execution_error"
    if exit_code & _PYLINT_FINDING_BITS:
        # Findings without the structured report would drop the native
        # diagnostics, so treat missing output as a failure instead.
        if isinstance(native, list):
            return "findings", "duplicate_code_found"
        return "failure", "pylint_report_unavailable"
    return "failure", "unknown_pylint_exit_code"


CLASSIFIERS = {
    "vulture": _classify_vulture,
    "pyright": _classify_pyright,
    "deptry": _classify_deptry,
    "pylint": _classify_pylint,
}


def static_report(
    policy: dict,
    exit_code: int,
    stdout: str = "",
    stderr: str = "",
    native_raw: str = "",
) -> dict:
    """Normalise one finished scan. Nothing here starts a second execution."""
    if type(exit_code) is not int or not 0 <= exit_code <= 255:
        raise ValueError("Invalid original scanner exit code")
    tool = policy["tool"]
    if tool not in CLASSIFIERS:
        raise ValueError(f"Unknown static-analysis scanner: {tool}")
    native, native_note = _load_native(native_raw)
    findings, limitations, observed = FACTS[tool](
        native if tool != "vulture" else stdout
    )
    if native_note:
        limitations.append(native_note)
    outcome, reason = CLASSIFIERS[tool](exit_code, findings, native, limitations)
    truncated = len(native_raw.encode("utf-8")) > MAX_NATIVE_BYTES
    return {
        "schema_version": 1,
        "tool": tool,
        "pinned_version": policy["pinned_version"],
        "observed_version": observed,
        "policy": {
            key: value
            for key, value in policy.items()
            if key not in {"schema_version", "tool", "pinned_version"}
        },
        "exit_code": exit_code,
        "outcome": outcome,
        "reason": reason,
        "finding_count": len(findings),
        "findings": findings[:MAX_FINDINGS],
        "omitted_findings": max(0, len(findings) - MAX_FINDINGS),
        "limitations": limitations,
        "native_json_available": native is not None,
        "native_json": None if truncated else native,
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
