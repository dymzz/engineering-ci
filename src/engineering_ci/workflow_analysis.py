"""Pinned Workflow, ShellCheck and zizmor static analysis domain logic.

Dagger-independent on purpose: this module builds controlled argument vectors,
verifies pinned tool invocations, parses native outputs and normalises findings
and composite Workflow/inline-Shell semantics without inventing per-script clean
receipts or bypassing Check identities.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path, PurePosixPath

# Single authority for pinned workflow/shell/security tool versions.
ACTIONLINT_WRAPPER = "1.7.12.25"
ACTIONLINT_VERSION = "1.7.12"
SHELLCHECK_WRAPPER = "0.11.0.1"
SHELLCHECK_VERSION = "0.11.0"
ZIZMOR_VERSION = "1.32.0"
PSSCRIPTANALYZER_VERSION = "1.9.0"
# MCR publishes no 7.6 tag; 7.5 is the newest pinned minor available.
POWERSHELL_IMAGE = "mcr.microsoft.com/powershell:7.5-ubuntu-24.04"

MAX_NATIVE_BYTES = 1024 * 1024
MAX_STREAM_CHARS = 8000
MAX_FINDINGS = 100
MAX_SUMMARY_FINDINGS = 10

WORKFLOW_SUFFIXES = (".yml", ".yaml")
SHELL_SUFFIXES = (".sh", ".bash", ".ksh", ".dash")
POWERSHELL_SUFFIXES = (".ps1", ".psm1")
WORKFLOW_CONFIG_FILES = frozenset({".github/actionlint.yaml", ".github/actionlint.yml"})
ZIZMOR_CONFIG_FILES = frozenset(
    {".zizmor.yml", ".zizmor.yaml", ".github/zizmor.yml", ".github/zizmor.yaml"}
)

_SKIP_DISCOVERY_PARTS = frozenset({".git", ".venv", ".tmp", "__pycache__"})
_TARGET_STAGING_PREFIX = ".eci-workflow-"

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
    if suffix in SHELL_SUFFIXES:
        return "shellcheck"
    if suffix in POWERSHELL_SUFFIXES:
        return "psscriptanalyzer"
    return "unsupported"


def _norm_rel_path(path: str) -> str:
    norm = path.replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    return norm.lstrip("/")


def _matches_prefix(path: str, prefix: str) -> bool:
    norm = _norm_rel_path(prefix)
    if norm.endswith("/"):
        return path.startswith(norm)
    return path == norm or path.startswith(norm + "/")


def is_workflow_file(
    path: str,
    workflow_prefixes: tuple[str, ...] = (".github/workflows/",),
) -> bool:
    norm = _norm_rel_path(path)
    if not norm.lower().endswith(WORKFLOW_SUFFIXES):
        return False
    return any(_matches_prefix(norm, prefix) for prefix in workflow_prefixes)


def is_workflow_config(path: str, configured: str | None = None) -> bool:
    norm = _norm_rel_path(path)
    if configured and norm == _norm_rel_path(configured):
        return True
    return norm in WORKFLOW_CONFIG_FILES


def is_zizmor_config(path: str, configured: str | None = None) -> bool:
    norm = _norm_rel_path(path)
    if configured and norm == _norm_rel_path(configured):
        return True
    return norm in ZIZMOR_CONFIG_FILES


def is_shellcheck_config(path: str, configured: str | None = None) -> bool:
    if not configured:
        return False
    return _norm_rel_path(path) == _norm_rel_path(configured)


def is_shell_file(
    path: str,
    shell_prefixes: tuple[str, ...] = (),
) -> bool:
    norm = _norm_rel_path(path)
    if not norm.lower().endswith(SHELL_SUFFIXES):
        return False
    if not shell_prefixes:
        return True
    return any(_matches_prefix(norm, prefix) for prefix in shell_prefixes)


def discover_workflow_files(
    root: Path,
    workflow_prefixes: tuple[str, ...] = (".github/workflows/",),
    *,
    problems: dict | None = None,
    admitted_external_targets: dict | None = None,
) -> list[str]:
    return _discover_targets(
        root,
        workflow_prefixes,
        lambda path: is_workflow_file(path, workflow_prefixes),
        problems=problems,
        admitted_external_targets=admitted_external_targets,
    )


def discover_shell_files(
    root: Path,
    shell_prefixes: tuple[str, ...] = (),
    *,
    problems: dict | None = None,
    admitted_external_targets: dict | None = None,
) -> list[str]:
    return _discover_targets(
        root,
        shell_prefixes,
        lambda path: is_shell_file(path, shell_prefixes),
        problems=problems,
        admitted_external_targets=admitted_external_targets,
    )


def _skip_target_part(part):
    name = part.casefold()
    return name in _SKIP_DISCOVERY_PARTS or name.startswith(_TARGET_STAGING_PREFIX)


def _target_path(path: str) -> str:
    path = _relative_path("workflow target", path).replace("\\", "/")
    relative = PurePosixPath(path).as_posix()
    if (
        relative.startswith("-")
        or ":" in path
        or any(_skip_target_part(part) for part in PurePosixPath(path).parts)
    ):
        raise ValueError("invalid workflow target path")
    return relative


def _inspect_target(root, path, admitted, *, hash_bytes=True):
    """Resolve a target and read bytes only within the supplied Source scope."""
    try:
        logical = _target_path(path)
    except ValueError:
        return None, "workflow_target_path_invalid"
    try:
        target = (root / logical).resolve(strict=True)
        internal = target.is_relative_to(root)
        resolved = target.relative_to(root).as_posix() if internal else str(target)
        if internal:
            _target_path(resolved)
        grant = (
            None
            if internal
            else _admitted_external_entry(admitted, logical, target, exact_path=True)
        )
        if not internal and grant is None:
            return None, "workflow_target_outside_root"
        if not target.is_file():
            return None, "workflow_target_not_file"
        digest = _file_sha256(target) if hash_bytes or grant is not None else None
        if grant is not None and digest != grant["sha256"]:
            return None, "workflow_target_digest_mismatch"
    except FileNotFoundError:
        return None, "workflow_target_missing"
    except (OSError, RuntimeError, ValueError):
        return None, "workflow_target_unreadable"
    return {
        "path": logical,
        "resolved_path": resolved,
        "sha256": digest,
        "policy_scope": "repo_target_tree" if internal else "event_external",
    }, None


def _file_sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _discover_targets(root, prefixes, matches, *, problems, admitted_external_targets):
    base = Path(root).resolve()
    if not base.is_dir():
        return []
    found = set()

    def inspect(path):
        if matches(path):
            record, reason = _inspect_target(
                base, path, admitted_external_targets, hash_bytes=False
            )
            if record is not None:
                found.add(path)
            elif problems is not None:
                problems[path] = reason

    for prefix in prefixes or (".",):
        try:
            relative = _target_path(prefix)
        except ValueError:
            if problems is not None:
                problems[prefix] = "workflow_target_path_invalid"
            continue
        candidate = base / relative
        if matches(relative):
            inspect(relative)
            continue
        try:
            directory = candidate.resolve(strict=True)
            if not directory.is_relative_to(base):
                if problems is not None:
                    problems[relative] = "workflow_target_outside_root"
                continue
            _target_path(directory.relative_to(base).as_posix())
            if not directory.is_dir():
                continue
        except FileNotFoundError:
            if candidate.is_symlink() and problems is not None:
                problems[relative] = "workflow_target_missing"
            continue
        except (OSError, RuntimeError, ValueError):
            if problems is not None:
                problems[relative] = "workflow_target_unreadable"
            continue

        def walk_error(_error, relative=relative):
            if problems is not None:
                problems[relative] = "workflow_target_unreadable"

        for current, dirs, files in os.walk(directory, onerror=walk_error):
            parent = Path(current)
            alias = Path(relative) / parent.relative_to(directory)
            descend = []
            for name in dirs:
                child = parent / name
                if _skip_target_part(name):
                    continue
                if not child.is_symlink() and not child.is_junction():
                    descend.append(name)
                    continue
                if problems is not None:
                    try:
                        inside = child.resolve(strict=True).is_relative_to(base)
                        reason = (
                            "workflow_target_directory_alias"
                            if inside
                            else "workflow_target_outside_root"
                        )
                    except (OSError, RuntimeError):
                        reason = "workflow_target_unreadable"
                    problems[(alias / name).as_posix()] = reason
            dirs[:] = descend
            for name in files:
                inspect((alias / name).as_posix())
    return sorted(found)


def repository_scan_targets(root, policy):
    """Check explicit paths against the Directory mounted for a native scan."""
    root = Path(root).resolve()
    problems = {}
    paths = set()
    suffixes = SHELL_SUFFIXES if policy["tool"] == "shellcheck" else WORKFLOW_SUFFIXES
    for path in policy["scan_paths"]:
        try:
            logical = _target_path(path)
            target = (root / logical).resolve(strict=True)
        except FileNotFoundError:
            problems[path] = "workflow_target_missing"
            continue
        except (OSError, ValueError, RuntimeError):
            problems[path] = "workflow_target_path_invalid"
            continue
        if not target.is_relative_to(root):
            problems[path] = "workflow_target_outside_root"
        elif target.is_dir():
            paths.update(
                _discover_targets(
                    root,
                    (logical,),
                    lambda candidate: candidate.lower().endswith(suffixes),
                    problems=problems,
                    admitted_external_targets=None,
                )
            )
        else:
            paths.add(logical)
    sources = []
    for path in sorted(paths):
        target, reason = _inspect_target(root, path, None)
        if reason:
            problems[path] = reason
        else:
            sources.append(target)
    config_arg = {
        "actionlint": "actionlint_config",
        "shellcheck": "shellcheck_config",
        "zizmor": "zizmor_config",
    }[policy["tool"]]
    configs = workflow_config_problems(
        root=root,
        check_names=(policy["tool"],),
        **{config_arg: policy.get("config", "")},
    )
    return {
        "targets": sources,
        "problems": problems,
        "config_problem": configs.get(policy["tool"]),
    }


def repository_scan_refusal(policy, observed):
    reason = observed["config_problem"] or next(
        iter(observed["problems"].values()), None
    )
    if reason is None and observed["targets"]:
        return None
    return {
        **_shell(
            policy["tool"],
            policy["pinned_version"],
            policy["scan_paths"],
            "not_executed",
            reason or "no_scan_targets",
        ),
        "target_problems": observed["problems"],
    }


def actionlint_policy(
    scan_paths: list[str] | None = None,
    config: str = "",
    *,
    shellcheck: bool = False,
    shellcheck_executable: str = "",
    json_format: bool = False,
    debug: bool = False,
) -> dict:
    """Workflow files only; opt-in ShellCheck integration is explicit."""
    if type(shellcheck) is not bool:
        raise ValueError("shellcheck must be a bool")
    if type(json_format) is not bool:
        raise ValueError("json_format must be a bool")
    if type(debug) is not bool:
        raise ValueError("debug must be a bool")
    sc_exe = (
        _text("shellcheck_executable", shellcheck_executable)
        if shellcheck_executable
        else ""
    )
    return {
        "schema_version": 1,
        "tool": "actionlint",
        "pinned_version": ACTIONLINT_VERSION,
        "wrapper_version": ACTIONLINT_WRAPPER,
        "scan_paths": path_list("scan_paths", scan_paths, [".github/workflows"]),
        "config": _relative_path("config", config) if config else "",
        "shellcheck": shellcheck or bool(sc_exe),
        "shellcheck_executable": sc_exe,
        "shellcheck_pinned_version": SHELLCHECK_VERSION
        if (shellcheck or sc_exe)
        else None,
        "json_format": json_format,
        "debug": debug,
    }


def shellcheck_policy(
    scan_paths: list[str] | None = None,
    severity: str = "",
    shell: str = "",
    config: str = "",
) -> dict:
    """Standalone shell scripts checked directly by pinned ShellCheck."""
    if severity not in ("", "error", "warning", "info", "style"):
        raise ValueError("severity must be empty, error, warning, info or style")
    if shell not in ("", "sh", "bash", "dash", "ksh"):
        raise ValueError("shell must be empty, sh, bash, dash or ksh")
    return {
        "schema_version": 1,
        "tool": "shellcheck",
        "pinned_version": SHELLCHECK_VERSION,
        "wrapper_version": SHELLCHECK_WRAPPER,
        "scan_paths": path_list("scan_paths", scan_paths, ["scripts"]),
        "severity": severity,
        "shell": shell,
        "config": _relative_path("config", config) if config else "",
    }


def zizmor_policy(
    scan_paths: list[str] | None = None,
    config: str = "",
    persona: str = "",
    offline: bool = True,
) -> dict:
    """GitHub Actions security audit via pinned zizmor."""
    if persona not in ("", "regular", "pedantic", "auditor"):
        raise ValueError("persona must be empty, regular, pedantic or auditor")
    if type(offline) is not bool:
        raise ValueError("offline must be a bool")
    return {
        "schema_version": 1,
        "tool": "zizmor",
        "pinned_version": ZIZMOR_VERSION,
        "scan_paths": path_list("scan_paths", scan_paths, [".github/workflows"]),
        "config": _relative_path("config", config) if config else "",
        "persona": persona,
        "offline": offline,
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
    """Run pinned Workflow/Shell/Actions checkers in isolated environments."""
    if tool == "actionlint":
        return ["uvx", "--from", f"actionlint-py=={ACTIONLINT_WRAPPER}", "actionlint"]
    if tool == "actionlint+shellcheck":
        return [
            "uvx",
            "--from",
            f"actionlint-py=={ACTIONLINT_WRAPPER}",
            "--with",
            f"shellcheck-py=={SHELLCHECK_WRAPPER}",
            "actionlint",
        ]
    if tool == "shellcheck":
        return [
            "uvx",
            "--from",
            f"shellcheck-py=={SHELLCHECK_WRAPPER}",
            "shellcheck",
        ]
    if tool == "zizmor":
        return ["uvx", "--from", f"zizmor=={ZIZMOR_VERSION}", "zizmor"]
    raise ValueError(f"{tool} does not run through uvx")


def actionlint_arguments(policy: dict) -> list[str]:
    args = ["-no-color"]
    sc_exe = policy.get("shellcheck_executable") or ""
    if sc_exe:
        args.append(f"-shellcheck={sc_exe}")
    elif not policy.get("shellcheck"):
        # Optional integration stays explicitly off unless ShellCheck is enabled
        # and verified.
        args.append("-shellcheck=")
    args.append("-pyflakes=")
    if policy.get("json_format"):
        args.extend(["-format", "{{json .}}"])
    if policy.get("debug"):
        args.append("-debug")
    if policy["config"]:
        args.append(f"-config-file={policy['config']}")
    return args + policy["scan_paths"]


def shellcheck_arguments(policy: dict) -> list[str]:
    args = ["--format=json1"]
    if policy.get("config"):
        args.append(f"--rcfile={policy['config']}")
    else:
        args.append("--norc")
    if policy.get("severity"):
        args.extend(["--severity", policy["severity"]])
    if policy.get("shell"):
        args.extend(["--shell", policy["shell"]])
    return args + policy["scan_paths"]


def zizmor_arguments(policy: dict) -> list[str]:
    args = ["--format", "json"]
    if policy.get("offline", True):
        args.append("--offline")
    if policy.get("config"):
        args.extend(["-c", policy["config"]])
    else:
        args.append("--no-config")
    if policy.get("persona"):
        args.append(f"--persona={policy['persona']}")
    return args + policy["scan_paths"]


def powershell_arguments(policy: dict) -> list[str]:
    """Arguments for the generated invocation script, not for pwsh itself."""
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
    """Name the environment this check cannot honour, or None when it can."""
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


def split_actionlint_diagnostics(findings: list[dict]) -> tuple[list[dict], list[dict]]:
    """Separate actionlint's own rules from embedded ShellCheck diagnostics."""
    workflow_only: list[dict] = []
    shellcheck_only: list[dict] = []
    for item in findings:
        if item.get("source_tool") == "shellcheck" or item.get("rule") == "shellcheck":
            shellcheck_only.append(item)
        else:
            workflow_only.append(item)
    return workflow_only, shellcheck_only


def _actionlint_facts(stdout: str, raw: str, *, json_format: bool = False):
    candidate = raw if raw.strip() else stdout
    stripped = candidate.lstrip()
    if json_format or stripped.startswith("["):
        if not stripped:
            return [], None, None, ["actionlint produced no JSON output"]
        try:
            parsed = json.loads(stripped)
        except ValueError:
            return [], None, None, ["actionlint JSON output is not valid JSON"]
        if not isinstance(parsed, list):
            return [], None, None, ["actionlint JSON output is not a list"]
        findings = []
        for item in parsed:
            if not isinstance(item, dict):
                return [], None, None, ["actionlint JSON item is not an object"]
            rule = str(item.get("kind") or "")
            findings.append(
                {
                    "rule": rule,
                    "source_tool": "shellcheck"
                    if rule == "shellcheck"
                    else "actionlint",
                    "path": str(item.get("filepath") or ""),
                    "line": item.get("line"),
                    "column": item.get("column"),
                    "message": str(item.get("message") or ""),
                }
            )
        return findings, {"format": "actionlint-json", "items": parsed}, None, []
    findings = []
    for line in candidate.splitlines():
        match = _ACTIONLINT_LINE.match(line.strip())
        if not match:
            continue
        rule = match.group("rule") or ""
        findings.append(
            {
                "rule": rule,
                "source_tool": "shellcheck" if rule == "shellcheck" else "actionlint",
                "path": match.group("path"),
                "line": int(match.group("line")),
                "message": match.group("message"),
            }
        )
    limitations = (
        ["actionlint text output contains no recognizable diagnostics"]
        if candidate.strip() and not findings
        else []
    )
    return findings, None, None, limitations


def _shellcheck_facts(stdout: str, raw: str):
    payload = raw if raw.strip() else stdout
    if not payload.strip():
        return [], None, None, ["shellcheck produced no JSON output"]
    try:
        envelope = json.loads(payload)
    except ValueError:
        return [], None, None, ["shellcheck produced invalid JSON"]
    if not isinstance(envelope, dict) or not isinstance(envelope.get("comments"), list):
        return [], None, None, ["shellcheck JSON payload is missing comments list"]
    findings = []
    for item in envelope["comments"]:
        if not isinstance(item, dict):
            return [], None, None, ["shellcheck comment entry is not an object"]
        code = item.get("code")
        findings.append(
            {
                "rule": f"SC{code}" if code is not None else "",
                "code": code,
                "severity": str(item.get("level") or ""),
                "path": str(item.get("file") or ""),
                "line": item.get("line"),
                "column": item.get("column"),
                "message": str(item.get("message") or ""),
            }
        )
    return findings, envelope, None, []


def _zizmor_location(item: dict) -> tuple[str, int | None]:
    locations = item.get("locations")
    if not isinstance(locations, list) or not locations:
        return "", None
    primary = next(
        (
            loc
            for loc in locations
            if isinstance(loc, dict) and loc.get("kind") == "Primary"
        ),
        locations[0],
    )
    if not isinstance(primary, dict):
        return "", None
    symbolic = primary.get("symbolic") or {}
    key = symbolic.get("key") or {}
    local = key.get("Local") or {}
    path = str(local.get("given_path") or local.get("verbatim_path") or "")
    concrete = primary.get("concrete") or {}
    location = concrete.get("location") or {}
    start_point = location.get("start_point") or {}
    row = start_point.get("row")
    line = row + 1 if type(row) is int else None
    return path, line


def _zizmor_facts(stdout: str, raw: str):
    payload = raw if raw.strip() else stdout
    if not payload.strip():
        return [], None, None, ["zizmor produced no JSON output"]
    try:
        parsed = json.loads(payload)
    except ValueError:
        return [], None, None, ["zizmor produced invalid JSON"]
    if not isinstance(parsed, list):
        return [], None, None, ["zizmor JSON output is not a list"]
    findings = []
    for item in parsed:
        if not isinstance(item, dict) or not isinstance(item.get("ident"), str):
            return [], None, None, ["zizmor finding entry is invalid"]
        if item.get("ignored") is True:
            continue
        path, line = _zizmor_location(item)
        determinations = item.get("determinations") or {}
        findings.append(
            {
                "rule": item["ident"],
                "severity": str(determinations.get("severity") or ""),
                "confidence": str(determinations.get("confidence") or ""),
                "persona": str(determinations.get("persona") or ""),
                "path": path,
                "line": line,
                "message": str(item.get("desc") or ""),
                "url": str(item.get("url") or ""),
            }
        )
    return findings, {"format": "zizmor-json-v1", "items": parsed}, None, []


def _powershell_facts(_stdout: str, raw: str):
    """Read the JSON Lines records the invocation script already printed."""
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
    "shellcheck": _shellcheck_facts,
    "zizmor": _zizmor_facts,
    "psscriptanalyzer": _powershell_facts,
}


def _classify_actionlint(exit_code, findings, envelope, limitations) -> tuple[str, str]:
    if limitations:
        return "failure", "actionlint_output_invalid"
    if exit_code == 0 and findings:
        return "failure", "actionlint_exit_status_inconsistent"
    if exit_code == 0 and not findings:
        return "clean", "no_workflow_problems"
    if exit_code == 1 and findings:
        return "findings", "workflow_problems_reported"
    return "failure", "actionlint_execution_error"


def _classify_shellcheck(exit_code, findings, envelope, limitations) -> tuple[str, str]:
    if limitations or not isinstance(envelope, dict):
        return "failure", "shellcheck_execution_error"
    if exit_code == 0 and not findings:
        return "clean", "no_shell_problems"
    if exit_code == 1 and findings:
        return "findings", "shell_problems_reported"
    return "failure", "shellcheck_execution_error"


def _classify_zizmor(exit_code, findings, envelope, limitations) -> tuple[str, str]:
    if limitations or not isinstance(envelope, dict):
        return "failure", "zizmor_execution_error"
    if findings and exit_code != 127:
        return "findings", "actions_security_findings_reported"
    if exit_code == 0 and not findings:
        return "clean", "no_security_findings"
    return "failure", "zizmor_execution_error"


def _classify_powershell(exit_code, findings, envelope, limitations) -> tuple[str, str]:
    if not isinstance(envelope, dict):
        return "failure", "psscriptanalyzer_report_unavailable"
    if envelope.get("status") != "ok":
        return "failure", "psscriptanalyzer_execution_error"
    if exit_code != 0:
        return "failure", "powershell_invocation_error"
    if findings:
        return "findings", "rule_violations_reported"
    scanned = envelope.get("files_scanned")
    if type(scanned) is not int or scanned <= 0:
        return "not_executed", "no_applicable_scripts"
    return "clean", "no_rule_violations"


CLASSIFIERS = {
    "actionlint": _classify_actionlint,
    "shellcheck": _classify_shellcheck,
    "zizmor": _classify_zizmor,
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
        "native_json_available": False,
        "native_json": None,
        "native_truncated": False,
        "stdout": "",
        "stderr": "",
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


def repository_scan_filter_refusal(
    policy: dict,
    observed: dict,
    raw_observed: dict,
) -> dict | None:
    """Refuse explicitly when source filtering hides scan targets or problems."""
    raw_targets = raw_observed.get("targets") or []
    filtered_targets = observed.get("targets") or []
    filtered_paths = {
        entry["path"]
        for entry in filtered_targets
        if isinstance(entry, dict) and entry.get("path")
    }
    excluded_targets = [
        entry
        for entry in raw_targets
        if isinstance(entry, dict) and entry.get("path") not in filtered_paths
    ]
    raw_problems = raw_observed.get("problems") or {}
    filtered_problems = observed.get("problems") or {}
    excluded_problems = {
        path: problem
        for path, problem in raw_problems.items()
        if filtered_problems.get(path) != problem
    }
    raw_config_problem = raw_observed.get("config_problem")
    filtered_config_problem = observed.get("config_problem")
    excluded_config_problem = (
        raw_config_problem
        if raw_config_problem and raw_config_problem != filtered_config_problem
        else None
    )
    if (
        not excluded_targets
        and not excluded_problems
        and excluded_config_problem is None
    ):
        return None
    report = repository_scan_refusal(policy, raw_observed) or _shell(
        policy["tool"],
        policy["pinned_version"],
        list(policy["scan_paths"]),
        "not_executed",
        "source_filter_excluded_scan_targets",
    )
    hidden_reasons = []
    if excluded_config_problem:
        hidden_reasons.append(excluded_config_problem)
    hidden_reasons.extend(excluded_problems.values())
    report["reason"] = "source_filter_excluded_scan_targets"
    report["limitations"] = [
        "source_filter_excluded_scan_targets",
        *(
            r
            for r in hidden_reasons
            if r and r != "source_filter_excluded_scan_targets"
        ),
    ]
    report["target_sources"] = list(filtered_targets)
    report["excluded_targets"] = excluded_targets
    report["target_problems"] = {**filtered_problems, **excluded_problems}
    if excluded_problems:
        report["excluded_problems"] = excluded_problems
    if raw_config_problem:
        report["config_problem"] = raw_config_problem
    return report


def script_report(
    policy: dict,
    exit_code: int,
    stdout: str = "",
    stderr: str = "",
    native_raw: str = "",
) -> dict:
    """Normalise one finished check. Nothing here starts a second execution."""
    if type(exit_code) is not int or not 0 <= exit_code <= 255:
        raise ValueError("Invalid original tool exit code")
    tool = policy["tool"]
    if tool not in CLASSIFIERS:
        raise ValueError(f"Unknown script-analysis tool: {tool}")
    actionlint_json = tool == "actionlint" and policy.get("json_format", False)
    raw_source = native_raw or (
        stdout if tool in {"shellcheck", "zizmor"} or actionlint_json else ""
    )
    truncated = len(raw_source.encode("utf-8")) > MAX_NATIVE_BYTES
    if actionlint_json and truncated:
        findings, envelope, observed, limitations = [], None, None, []
    elif tool == "actionlint":
        findings, envelope, observed, limitations = _actionlint_facts(
            stdout, "" if truncated else native_raw, json_format=actionlint_json
        )
    else:
        findings, envelope, observed, limitations = FACTS[tool](
            stdout, "" if truncated else native_raw
        )
    if truncated:
        limitations = [*limitations, "native structured output exceeds the 1 MiB limit"]
    outcome, reason = CLASSIFIERS[tool](exit_code, findings, envelope, limitations)
    report = {
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
        "native_json_available": envelope is not None,
        "native_json": envelope,
        "native_truncated": truncated,
        "stdout": _bounded_stream(stdout),
        "stderr": _bounded_stream(stderr),
    }
    if tool == "actionlint":
        workflow_only, shellcheck_only = split_actionlint_diagnostics(findings)
        report["shellcheck_enabled"] = bool(policy.get("shellcheck"))
        report["actionlint_findings"] = workflow_only[:MAX_FINDINGS]
        report["shellcheck_findings"] = shellcheck_only[:MAX_FINDINGS]
    return report


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


def default_run(argv: list[str], root: Path, data: str | None = None):
    clean_env = {k: v for k, v in os.environ.items() if k != "SHELLCHECK_OPTS"}
    try:
        return subprocess.run(
            [str(item) for item in argv],
            cwd=root,
            env=clean_env,
            input=data,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            argv, 127, "", f"Command not found: {argv[0]}"
        )


def _invoke(runner, argv: list[str], root: Path):
    fn = runner or default_run
    try:
        return fn(list(argv), root)
    except TypeError:
        return fn(list(argv), root, None)


def _parse_version(text: str, expected: str) -> str | None:
    if re.search(rf"(?<![\d.]){re.escape(expected)}(?![\d.])", text):
        return expected
    match = re.search(r"\b(\d+\.\d+\.\d+)\b", text)
    return match.group(1) if match else None


def verify_tool_version(
    prefix: list[str],
    version_args: list[str],
    expected_version: str,
    *,
    root: Path,
    runner=None,
) -> tuple[bool, str | None, str]:
    """Run `<tool> --version/-version` once and verify pinned version matches."""
    completed = _invoke(runner, [*prefix, *version_args], root)
    combined = (
        (getattr(completed, "stdout", "") or "")
        + "\n"
        + (getattr(completed, "stderr", "") or "")
    )
    if completed.returncode != 0:
        return False, None, combined.strip() or f"exit {completed.returncode}"
    observed = _parse_version(combined, expected_version)
    if observed != expected_version:
        return False, observed, combined.strip() or "version_mismatch"
    return True, observed, ""


def _resolve_tool_prefix(tool: str) -> tuple[list[str], str]:
    """Return pinned runner argv and display executable name."""
    if tool == "actionlint":
        if (
            shutil.which("actionlint")
            and os.environ.get("ECI_PREFER_PATH_TOOLS") == "1"
        ):
            return ["actionlint"], "actionlint"
        return runner_prefix("actionlint"), "uvx:actionlint"
    if tool == "shellcheck":
        if (
            shutil.which("shellcheck")
            and os.environ.get("ECI_PREFER_PATH_TOOLS") == "1"
        ):
            return ["shellcheck"], "shellcheck"
        return runner_prefix("shellcheck"), "uvx:shellcheck"
    if tool == "actionlint+shellcheck":
        return runner_prefix("actionlint+shellcheck"), "uvx:actionlint+shellcheck"
    if tool == "zizmor":
        if shutil.which("zizmor") and os.environ.get("ECI_PREFER_PATH_TOOLS") == "1":
            return ["zizmor"], "zizmor"
        return runner_prefix("zizmor"), "uvx:zizmor"
    raise ValueError(f"Unsupported tool: {tool}")


def _locate_shellcheck_executable(root: Path, runner=None) -> str | None:
    """Resolve a concrete ShellCheck binary for Actionlint ``-shellcheck=``.

    Actionlint on Windows can hang when inline ShellCheck is enabled but no
    executable path is passed (implicit PATH discovery). Always supply an
    absolute path from the same pinned runner environment used for ShellCheck.
    """
    prefix, _ = _resolve_tool_prefix("shellcheck")
    if len(prefix) == 1:
        found = shutil.which(prefix[0])
        if found:
            return found
        return prefix[0] if Path(prefix[0]).exists() else None
    locate_argv = [
        *prefix[:-1],
        "python",
        "-c",
        "import shutil; print(shutil.which('shellcheck') or '')",
    ]
    completed = _invoke(runner, locate_argv, root)
    lines = (getattr(completed, "stdout", "") or "").strip().splitlines()
    candidate = lines[-1].strip() if lines else ""
    if candidate:
        return candidate
    return shutil.which("shellcheck")


def actionlint_inline_shellcheck_host_block() -> str | None:
    """Refuse Actionlint inline ShellCheck on hosts where pinned Actionlint deadlocks.

    actionlint 1.7.12 writes each ``run:`` script into ShellCheck's stdin pipe
    before starting the child. Scripts larger than the kernel pipe buffer
    deadlock with no ShellCheck process (rhysd/actionlint#650 / #702). The fix
    shipped in actionlint 1.8.0; actionlint-py has no 1.8 wrapper yet, so
    Windows hosts keep Actionlint itself and standalone ShellCheck, and skip
    only the inline integration.
    """
    if os.name == "nt":
        return "actionlint_shellcheck_stdin_deadlock_on_windows"
    return None


def _report_to_status(report: dict, failure_reason: str) -> str:
    outcome = report["outcome"]
    if outcome == "clean":
        return "passed"
    if outcome == "not_executed":
        return "not_executed"
    return "failed"


def _parse_actionlint_shellcheck_debug(raw_stderr: str) -> dict:
    """Extract ShellCheck execution evidence from `actionlint -debug` stderr."""
    lines = (raw_stderr or "").splitlines()
    disabled = any('Rule "shellcheck" was disabled' in line for line in lines)
    rule_ran = any("[Linter] shellcheck found " in line for line in lines)
    invoked_steps = sum(
        1 for line in lines if "[shellcheck]" in line and "Run shellcheck for " in line
    )
    return {
        "rule_ran": rule_ran,
        "disabled": disabled,
        "invoked_steps": invoked_steps,
    }


WORKFLOW_MANAGED_CHECKS = frozenset(
    {"actionlint", "workflow_shellcheck", "shellcheck", "zizmor"}
)


def _is_hex64(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _admitted_external_entry(
    admitted_entries: dict | None,
    rel_path: str,
    resolved_target: Path,
    *,
    exact_path: bool = False,
) -> dict | None:
    if not isinstance(admitted_entries, dict):
        return None
    declared = admitted_entries.get(rel_path)
    if (
        not isinstance(declared, dict)
        or declared.get("policy_scope") != "event_external"
    ):
        return None
    raw_path = declared.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return None
    declared_path = Path(raw_path)
    if not declared_path.is_absolute():
        return None
    try:
        if (
            declared_path if exact_path else declared_path.resolve()
        ) != resolved_target:
            return None
    except OSError:
        return None
    if not _is_hex64(declared.get("sha256")):
        return None
    return declared


def _authorized_external_targets(entries, check_name, authorize):
    """Accept only exact target claims authenticated for this Check."""
    admitted = {}
    if not callable(authorize) or not isinstance(entries, dict):
        return admitted
    for logical, claim in entries.items():
        if not isinstance(logical, str) or not isinstance(claim, dict):
            continue
        resolved = claim.get("path")
        if not isinstance(resolved, str) or not _admitted_external_entry(
            entries, logical, Path(resolved), exact_path=True
        ):
            continue
        request = {
            "path": logical,
            "resolved_path": resolved,
            "sha256": claim["sha256"],
        }
        if authorize(check_name, request) is True:
            admitted[logical] = dict(claim)
    return admitted


def _inspect_config_file(
    root: Path | None,
    rel_path: str,
    *,
    admitted_external_configs: dict | None = None,
) -> dict:
    """Return structured verification state for one rule config file; never fabricate hashes."""
    if root is None:
        return {
            "path": rel_path,
            "status": "unknown",
            "sha256": None,
            "reason": "workflow_config_root_missing",
        }
    resolved_root = Path(root).resolve()
    raw = Path(rel_path)
    candidate = raw if raw.is_absolute() else (resolved_root / raw)
    if candidate.is_symlink() and not candidate.exists():
        # Distinguish a broken symlink inside repo from outside_root: check parent/link target if resolvable.
        try:
            raw_target = Path(os.readlink(candidate))
            target_resolved = (
                raw_target.resolve()
                if raw_target.is_absolute()
                else (candidate.parent / raw_target).resolve()
            )
            if not target_resolved.is_relative_to(resolved_root):
                return {
                    "path": rel_path,
                    "status": "outside_root",
                    "sha256": None,
                    "reason": "workflow_config_outside_root",
                }
        except OSError:
            pass
        return {
            "path": rel_path,
            "status": "missing",
            "sha256": None,
            "reason": "workflow_config_missing",
        }
    try:
        target = candidate.resolve()
    except OSError:
        return {
            "path": rel_path,
            "status": "unreadable",
            "sha256": None,
            "reason": "workflow_config_unreadable",
        }
    try:
        in_root = target.is_relative_to(resolved_root)
    except OSError:
        return {
            "path": rel_path,
            "status": "unknown",
            "sha256": None,
            "reason": "workflow_config_unverified",
        }
    if not in_root or raw.is_absolute():
        admitted = _admitted_external_entry(admitted_external_configs, rel_path, target)
        if admitted is None or not target.is_file():
            return {
                "path": rel_path,
                "status": "outside_root",
                "sha256": None,
                "reason": "workflow_config_outside_root",
            }
        try:
            digest_a = hashlib.sha256(target.read_bytes()).hexdigest()
            digest_b = hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError:
            return {
                "path": rel_path,
                "status": "unreadable",
                "sha256": None,
                "reason": "workflow_config_unreadable",
            }
        if digest_a != digest_b or digest_a != admitted["sha256"]:
            return {
                "path": rel_path,
                "status": "digest_mismatch",
                "sha256": None,
                "reason": "workflow_config_digest_mismatch",
            }
        return {
            "path": rel_path,
            "status": "verified_external",
            "policy_scope": "event_external",
            "sha256": digest_a,
        }
    if not target.is_file():
        return {
            "path": rel_path,
            "status": "missing",
            "sha256": None,
            "reason": "workflow_config_missing",
        }
    try:
        digest_a = hashlib.sha256(target.read_bytes()).hexdigest()
        digest_b = hashlib.sha256(target.read_bytes()).hexdigest()
    except OSError:
        return {
            "path": rel_path,
            "status": "unreadable",
            "sha256": None,
            "reason": "workflow_config_unreadable",
        }
    if digest_a != digest_b:
        return {
            "path": rel_path,
            "status": "changed_during_read",
            "sha256": None,
            "reason": "workflow_config_changed_during_read",
        }
    return {
        "path": rel_path,
        "status": "verified",
        "policy_scope": "repo_target_tree",
        "sha256": digest_a,
    }


def _verified_digest_from_record(entry: object) -> str | None:
    if isinstance(entry, str):
        return entry if _is_hex64(entry) else None
    if (
        isinstance(entry, dict)
        and entry.get("status") in {"verified", "verified_external"}
        and _is_hex64(entry.get("sha256"))
    ):
        return str(entry["sha256"])
    return None


def _problem_reason_from_record(entry: object) -> str:
    if isinstance(entry, dict) and entry.get("reason"):
        return str(entry["reason"])
    mapping = {
        "outside_root": "workflow_config_outside_root",
        "missing": "workflow_config_missing",
        "unreadable": "workflow_config_unreadable",
        "unknown": "workflow_config_unverified",
    }
    if isinstance(entry, str) and entry in mapping:
        return mapping[entry]
    return "workflow_config_unverified"


def _actionlint_config_paths(
    root: Path | None,
    actionlint_config: str = "",
    *,
    file_digests: dict | None = None,
) -> list[str]:
    if actionlint_config:
        return [actionlint_config]
    if root is not None:
        resolved_root = Path(root).resolve()
        found = []
        for p in sorted(WORKFLOW_CONFIG_FILES):
            candidate = resolved_root / p
            if candidate.is_file() or candidate.is_symlink():
                found.append(p)
        return found
    if isinstance(file_digests, dict):
        return [p for p in sorted(WORKFLOW_CONFIG_FILES) if p in file_digests]
    return []


def _check_config_paths(
    name: str,
    wf_cfg=None,
    *,
    root: Path | None = None,
    file_digests: dict | None = None,
    actionlint_config: str = "",
    shellcheck_config: str = "",
    zizmor_config: str = "",
) -> list[str]:
    if wf_cfg is not None:
        actionlint_config = str(getattr(wf_cfg, "actionlint_config", "") or "")
        shellcheck_config = str(getattr(wf_cfg, "shellcheck_config", "") or "")
        zizmor_config = str(getattr(wf_cfg, "zizmor_config", "") or "")
    if name in {"actionlint", "workflow_shellcheck"}:
        return _actionlint_config_paths(
            root, actionlint_config, file_digests=file_digests
        )
    if name == "shellcheck":
        return [shellcheck_config] if shellcheck_config else []
    if name == "zizmor":
        return [zizmor_config] if zizmor_config else []
    return []


def workflow_config_digests(
    root: Path | None,
    workflow_checks=None,
    *,
    actionlint_config: str = "",
    shellcheck_config: str = "",
    zizmor_config: str = "",
    admitted_external_configs: dict | None = None,
) -> dict[str, dict]:
    """Inspect active workflow/shell/zizmor config files with explicit verification status."""
    if root is None:
        return {}
    if workflow_checks is not None:
        actionlint_config = str(getattr(workflow_checks, "actionlint_config", "") or "")
        shellcheck_config = str(getattr(workflow_checks, "shellcheck_config", "") or "")
        zizmor_config = str(getattr(workflow_checks, "zizmor_config", "") or "")
    resolved_root = Path(root).resolve()
    candidates: set[str] = set(
        _actionlint_config_paths(resolved_root, actionlint_config)
    )
    if shellcheck_config:
        candidates.add(shellcheck_config)
    if zizmor_config:
        candidates.add(zizmor_config)
    return {
        rel_path: _inspect_config_file(
            resolved_root,
            rel_path,
            admitted_external_configs=admitted_external_configs,
        )
        for rel_path in sorted(candidates)
    }


def workflow_config_problems(
    selection_config=None,
    *,
    root: Path | None = None,
    file_digests: dict | None = None,
    check_names=None,
    actionlint_config: str = "",
    shellcheck_config: str = "",
    zizmor_config: str = "",
    admitted_external_configs: dict | None = None,
) -> dict[str, str]:
    """Return {check_name: reason} for any workflow/script check whose config is unverified."""
    wf_cfg = (
        getattr(selection_config, "workflow_checks", None)
        if selection_config is not None
        else None
    )
    names = (
        tuple(check_names)
        if check_names is not None
        else sorted(WORKFLOW_MANAGED_CHECKS)
    )
    problems: dict[str, str] = {}
    for name in names:
        if name not in WORKFLOW_MANAGED_CHECKS:
            continue
        cfg_paths = _check_config_paths(
            name,
            wf_cfg,
            root=root,
            file_digests=file_digests,
            actionlint_config=actionlint_config,
            shellcheck_config=shellcheck_config,
            zizmor_config=zizmor_config,
        )
        for rel_path in sorted(set(cfg_paths)):
            if isinstance(file_digests, dict) and rel_path in file_digests:
                record = file_digests[rel_path]
            else:
                record = _inspect_config_file(
                    root,
                    rel_path,
                    admitted_external_configs=admitted_external_configs,
                )
            if _verified_digest_from_record(record) is None:
                problems[name] = _problem_reason_from_record(record)
                break
    return problems


def effective_check_rule_ref(
    name: str,
    *,
    selection_rule_ref: dict | None,
    selection_config=None,
    root: Path | None = None,
    file_digests: dict | None = None,
    admitted_external_configs: dict | None = None,
) -> dict | None:
    """Bind per-check effective rule_ref to verified config bytes; return None if unverified."""
    if (
        name not in WORKFLOW_MANAGED_CHECKS
        or not isinstance(selection_rule_ref, dict)
        or selection_config is None
    ):
        return selection_rule_ref
    wf_cfg = getattr(selection_config, "workflow_checks", None)
    if wf_cfg is None:
        return selection_rule_ref
    if name == "actionlint":
        options: dict[str, object] = {"actionlint_config": wf_cfg.actionlint_config}
    elif name == "workflow_shellcheck":
        options = {
            "actionlint_config": wf_cfg.actionlint_config,
            "shellcheck_inline": bool(wf_cfg.shellcheck_inline),
        }
    elif name == "shellcheck":
        options = {"shellcheck_config": wf_cfg.shellcheck_config}
    else:
        options = {
            "zizmor_config": wf_cfg.zizmor_config,
            "zizmor_persona": wf_cfg.zizmor_persona,
            "zizmor_offline": bool(wf_cfg.zizmor_offline),
        }
    cfg_paths = _check_config_paths(name, wf_cfg, root=root, file_digests=file_digests)
    config_entries = []
    for rel_path in sorted(set(cfg_paths)):
        if isinstance(file_digests, dict) and rel_path in file_digests:
            record = file_digests[rel_path]
        else:
            record = _inspect_config_file(
                root,
                rel_path,
                admitted_external_configs=admitted_external_configs,
            )
        file_sha = _verified_digest_from_record(record)
        if file_sha is None:
            return None
        config_entries.append({"path": rel_path, "sha256": file_sha})
    payload = {
        "check": name,
        "selection_rule_sha256": str(selection_rule_ref.get("sha256") or ""),
        "options": options,
        "config_files": config_entries,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    from engineering_ci import check_contract

    return check_contract.rule_ref(
        selection_rule_ref["name"],
        selection_rule_ref["version"],
        path=selection_rule_ref.get("path"),
        digest=digest,
    )


def effective_workflow_rule_refs(
    selection_rule_ref: dict | None,
    selection_config=None,
    *,
    root: Path | None = None,
    file_digests: dict | None = None,
    admitted_external_configs: dict | None = None,
) -> dict[str, dict]:
    """Compute per-check effective rule_ref map for all workflow/script checks."""
    if not isinstance(selection_rule_ref, dict) or selection_config is None:
        return {}
    resolved: dict[str, dict] = {}
    for check_name in sorted(WORKFLOW_MANAGED_CHECKS):
        rule = effective_check_rule_ref(
            check_name,
            selection_rule_ref=selection_rule_ref,
            selection_config=selection_config,
            root=root,
            file_digests=file_digests,
            admitted_external_configs=admitted_external_configs,
        )
        if rule is not None:
            resolved[check_name] = rule
    return resolved


def run_workflow_script_suite(
    selected_by_name: dict[str, dict],
    *,
    root: Path,
    runner=None,
    workflow_prefixes: tuple[str, ...] = (".github/workflows/",),
    shell_prefixes: tuple[str, ...] = (),
    actionlint_config: str = "",
    shellcheck_config: str = "",
    zizmor_config: str = "",
    zizmor_persona: str = "",
    zizmor_offline: bool = True,
    shellcheck_inline: bool = True,
    rule_ref: dict | None = None,
    rule_refs: dict[str, dict | None] | None = None,
    extra_paths: tuple[str, ...] = (),
    generated_input_refs: tuple[dict, ...] = (),
    admission_fn=None,
    shared_cache: dict | None = None,
    inline_shellcheck_requested: bool = False,
    inline_shellcheck_inputs: tuple[str, ...] | None = None,
    admitted_external_configs: dict | None = None,
    admitted_external_targets: dict | None = None,
    authorize_external_target=None,
    validate_target_source=None,
) -> dict[str, dict]:
    """Run selected checks on resolved targets.

    The runtime authority must approve the exact external claim for the Check
    before discovery or execution may read its bytes.
    """
    root = Path(root).resolve()
    config_problems = workflow_config_problems(
        root=root,
        actionlint_config=actionlint_config,
        shellcheck_config=shellcheck_config,
        zizmor_config=zizmor_config,
        admitted_external_configs=admitted_external_configs,
    )
    cache = shared_cache if shared_cache is not None else {}
    extras = {"workflow": [], "shell": []}
    for path in extra_paths:
        if not isinstance(path, str):
            continue
        absolute = PurePosixPath(path.replace("\\", "/")).is_absolute() or re.match(
            r"^[A-Za-z]:", path
        )
        if is_workflow_file(path, workflow_prefixes) or (
            absolute and target_kind(path) == "actionlint"
        ):
            extras["workflow"].append(path)
        if is_shell_file(path, shell_prefixes) or (
            absolute and target_kind(path) == "shellcheck"
        ):
            extras["shell"].append(path)
    target_sources = {}
    target_problems = {}

    def target_files(check_name: str, *, kind: str, declared=None) -> list[str]:
        sel = selected_by_name.get(check_name) or {}
        claim_check = (
            "workflow_shellcheck" if check_name == "_inline_shellcheck" else check_name
        )
        admitted = _authorized_external_targets(
            admitted_external_targets, claim_check, authorize_external_target
        )
        if declared is None:
            declared = sel.get("inputs")
        problems = {}
        if not isinstance(declared, (list, tuple)) or (
            sel.get("status") == "unknown" and not declared
        ):
            discover, prefixes = (
                (discover_workflow_files, workflow_prefixes)
                if kind == "workflow"
                else (discover_shell_files, shell_prefixes)
            )
            declared = discover(
                root,
                prefixes,
                problems=problems,
                admitted_external_targets=admitted,
            )
        records = {}
        for path in (*declared, *extras[kind]):
            record, reason = _inspect_target(root, path, admitted)
            if record is not None and validate_target_source is not None:
                reason = validate_target_source(record, check_name=claim_check)
            if reason is None:
                records[record["path"]] = record
            else:
                problems[path] = reason
        if problems:
            target_problems[check_name] = problems
            return []
        paths = sorted(records)
        inputs = tuple(records[path] for path in paths)
        if target_sources.setdefault(tuple(paths), inputs) != inputs:
            target_problems[check_name] = {
                path: "workflow_target_changed_before_execution" for path in paths
            }
            return []
        return paths

    def unchanged(sources):
        admitted = {
            source["path"]: {**source, "path": source["resolved_path"]}
            for source in sources
            if source["policy_scope"] == "event_external"
        }
        return all(
            _inspect_target(root, source["path"], admitted)[0] == source
            for source in sources
        )

    def scan_targets(policy, arguments, prefix, paths):
        sources = target_sources[tuple(paths)]
        if not unchanged(sources):
            return None, "workflow_target_changed_before_execution", {}
        argv = arguments(policy)[: -len(paths)]
        locations = {}
        completed = None
        try:
            # Keep staged paths under the intended project so actionlint finds its
            # Git root, project configuration and repository-relative actions.
            with tempfile.TemporaryDirectory(
                prefix=_TARGET_STAGING_PREFIX, dir=root
            ) as folder:
                staged_root = Path(folder)
                for source in sources:
                    content = (root / source["resolved_path"]).read_bytes()
                    if hashlib.sha256(content).hexdigest() != source["sha256"]:
                        return None, "workflow_target_changed_before_execution", {}
                    staged = staged_root / source["path"]
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    staged.write_bytes(content)
                    relative = staged.relative_to(root).as_posix()
                    locations[relative] = source["path"]
                    locations[staged.as_posix()] = source["path"]
                    argv.append(relative)
                completed = _invoke(runner, [*prefix, *argv], root)
                staged_current = all(
                    _file_sha256(staged_root / source["path"]) == source["sha256"]
                    for source in sources
                )
        except OSError:
            if completed is None:
                return None, "workflow_target_materialization_failed", {}
            staged_current = False
        current = staged_current and unchanged(sources)
        return (
            completed,
            None if current else "workflow_target_changed_during_execution",
            locations,
        )

    def logical_findings(report, locations):
        for field in ("findings", "actionlint_findings", "shellcheck_findings"):
            for finding in report.get(field, ()):
                path = finding.get("path")
                if isinstance(path, str):
                    finding["path"] = locations.get(
                        path.replace("\\", "/").removeprefix("./"), path
                    )

    def target_refusal(check_name, access_id, reason):
        return {
            "name": check_name,
            "selected": selected_by_name[check_name].get("status"),
            "status": "not_executed",
            "exit_code": None,
            "reason": reason,
            "execution_started": False,
            "execution_kind": "not_started",
            "access_id": access_id,
        }

    def admit_tools(
        check_name: str,
        requirements: tuple[tuple[str, str, str], ...],
        *,
        selected_status: str | None = None,
    ) -> tuple[dict | None, dict[str, dict]]:
        if admission_fn is None:
            return None, {}
        decisions: dict[str, dict] = {}
        first_denied = None
        for tool_name, access_id, version in requirements:
            decision = admission_fn(tool_name, access_id, version)
            decisions[tool_name] = decision
            if not decision.get("allowed") and first_denied is None:
                first_denied = decision
        if first_denied is None:
            return None, decisions
        sel_val = (
            selected_status
            if selected_status is not None
            else selected_by_name.get(check_name, {}).get("status")
        )
        return (
            {
                "name": check_name,
                "selected": sel_val,
                "status": "not_executed",
                "exit_code": None,
                "reason": first_denied.get("reason") or "policy_admission_denied",
                "execution_started": False,
                "execution_kind": "not_started",
                "admission": first_denied,
                "tool_admissions": decisions,
                **({"rule_ref": rule_ref} if rule_ref is not None else {}),
                **(
                    {"generated_input_refs": [dict(r) for r in generated_input_refs]}
                    if generated_input_refs
                    else {}
                ),
            },
            decisions,
        )

    # Admit all active checks and composite tool dependencies BEFORE any cache
    # lookup or subprocess invocation so denied tools never run preflight or reuse
    # cached receipts from an earlier allowed run.
    admissions: dict[str, tuple[dict | None, dict[str, dict]]] = {}
    if "actionlint" in selected_by_name:
        admissions["actionlint"] = admit_tools(
            "actionlint",
            (("actionlint", "actionlint/check", ACTIONLINT_VERSION),),
        )
    if "workflow_shellcheck" in selected_by_name:
        admissions["workflow_shellcheck"] = admit_tools(
            "workflow_shellcheck",
            (
                ("actionlint", "actionlint/check", ACTIONLINT_VERSION),
                ("shellcheck", "shellcheck/check", SHELLCHECK_VERSION),
            ),
        )
    elif inline_shellcheck_requested and shellcheck_inline:
        admissions["_inline_shellcheck"] = admit_tools(
            "workflow_shellcheck",
            (
                ("actionlint", "actionlint/check", ACTIONLINT_VERSION),
                ("shellcheck", "shellcheck/check", SHELLCHECK_VERSION),
            ),
            selected_status="selected",
        )
    if "shellcheck" in selected_by_name:
        admissions["shellcheck"] = admit_tools(
            "shellcheck",
            (("shellcheck", "shellcheck/check", SHELLCHECK_VERSION),),
        )
    if "zizmor" in selected_by_name:
        admissions["zizmor"] = admit_tools(
            "zizmor",
            (("zizmor", "zizmor/check", ZIZMOR_VERSION),),
        )

    def check_shellcheck_ready() -> tuple[bool, str | None, str, str]:
        with cache.setdefault("_lock", threading.Lock()):
            if "sc_ready" in cache:
                return cache["sc_ready"]
            sc_prefix, _ = _resolve_tool_prefix("shellcheck")
            ok, obs_ver, detail = verify_tool_version(
                sc_prefix, ["--version"], SHELLCHECK_VERSION, root=root, runner=runner
            )
            executable = ""
            if ok:
                located = _locate_shellcheck_executable(root, runner=runner)
                if located:
                    executable = located
                else:
                    ok = False
                    detail = detail or "shellcheck_executable_unresolved"
            cache["sc_ready"] = (ok, obs_ver, detail, executable)
            return cache["sc_ready"]

    def ensure_actionlint_run(
        target_workflows: tuple[str, ...], want_inline_shellcheck: bool
    ) -> dict:
        sources = target_sources[target_workflows]
        cache_key = (
            "actionlint",
            root,
            tuple(tuple(source.values()) for source in sources),
            bool(want_inline_shellcheck),
            actionlint_config,
        )
        lock = cache.setdefault("_lock", threading.Lock())
        stale = {
            "prepared": False,
            "reason": "workflow_target_changed_before_execution",
            "detail": "",
        }
        with lock:
            if cache_key in cache:
                return cache[cache_key] if unchanged(sources) else stale
        sc_ok = False
        sc_ver = None
        sc_detail = ""
        sc_executable = ""
        use_shellcheck = False
        host_block = (
            actionlint_inline_shellcheck_host_block()
            if want_inline_shellcheck
            else None
        )
        if want_inline_shellcheck and host_block is None:
            sc_ok, sc_ver, sc_detail, sc_executable = check_shellcheck_ready()
            use_shellcheck = sc_ok and bool(sc_executable)
        elif want_inline_shellcheck and host_block is not None:
            sc_ok = False
            sc_detail = host_block
        with lock:
            if cache_key in cache:
                return cache[cache_key] if unchanged(sources) else stale
            prefix, exe_label = _resolve_tool_prefix(
                "actionlint+shellcheck" if use_shellcheck else "actionlint"
            )
            ok_al, obs_al, err_al = verify_tool_version(
                prefix, ["-version"], ACTIONLINT_VERSION, root=root, runner=runner
            )
            if not ok_al:
                res = {
                    "prepared": False,
                    "reason": "actionlint_unprepared"
                    if obs_al is None
                    else "actionlint_version_mismatch",
                    "detail": err_al,
                    "shellcheck_prepared": sc_ok if want_inline_shellcheck else None,
                    "shellcheck_version": sc_ver,
                    "shellcheck_detail": sc_detail,
                    "executable": exe_label,
                    "tool_version": obs_al,
                }
                cache[cache_key] = res
                return res
            policy = actionlint_policy(
                list(target_workflows),
                config=actionlint_config,
                shellcheck=use_shellcheck,
                shellcheck_executable=sc_executable if use_shellcheck else "",
                json_format=True,
                debug=use_shellcheck,
            )
            completed, source_problem, locations = scan_targets(
                policy, actionlint_arguments, prefix, target_workflows
            )
            if completed is None:
                return {**stale, "reason": source_problem}
            raw_stdout = getattr(completed, "stdout", "") or ""
            raw_stderr = getattr(completed, "stderr", "") or ""
            sc_debug = (
                _parse_actionlint_shellcheck_debug(raw_stderr)
                if use_shellcheck
                else None
            )
            rep = script_report(
                policy,
                completed.returncode,
                stdout=raw_stdout,
                stderr=raw_stderr,
                native_raw=raw_stdout,
            )
            logical_findings(rep, locations)
            res = {
                "prepared": True,
                "report": rep,
                "executable": exe_label,
                "tool_version": obs_al,
                "shellcheck_requested": want_inline_shellcheck,
                "shellcheck_prepared": sc_ok if want_inline_shellcheck else None,
                "shellcheck_version": sc_ver,
                "shellcheck_detail": sc_detail,
                "shellcheck_enabled": use_shellcheck,
                "shellcheck_debug": sc_debug,
                "target_source_problem": source_problem,
            }
            if source_problem is None:
                cache[cache_key] = res
            return res

    out: dict[str, dict] = {}
    al_workflows = (
        target_files("actionlint", kind="workflow")
        if "actionlint" in selected_by_name
        else []
    )
    wf_sc_workflows = (
        target_files("workflow_shellcheck", kind="workflow")
        if "workflow_shellcheck" in selected_by_name
        else []
    )
    sc_shells = (
        target_files("shellcheck", kind="shell")
        if "shellcheck" in selected_by_name
        else []
    )
    zz_workflows = (
        target_files("zizmor", kind="workflow") if "zizmor" in selected_by_name else []
    )

    inline_adm_key = (
        "workflow_shellcheck"
        if "workflow_shellcheck" in selected_by_name
        else "_inline_shellcheck"
    )
    inline_blocked = (
        admissions[inline_adm_key][0] if inline_adm_key in admissions else None
    )
    inline_target_tuple: tuple[str, ...] | None = None
    if "workflow_shellcheck" in selected_by_name:
        inline_target_tuple = tuple(wf_sc_workflows)
    elif inline_shellcheck_inputs is not None:
        inline_target_tuple = tuple(
            target_files(
                "_inline_shellcheck", kind="workflow", declared=inline_shellcheck_inputs
            )
        )
    want_inline_for_al = (
        bool(shellcheck_inline)
        and ("workflow_shellcheck" in selected_by_name or inline_shellcheck_requested)
        and inline_blocked is None
        and inline_adm_key not in target_problems
        and (inline_target_tuple is None or inline_target_tuple == tuple(al_workflows))
        and actionlint_inline_shellcheck_host_block() is None
    )

    if "actionlint" in selected_by_name:
        sel = selected_by_name["actionlint"]
        blocked, _ = admissions["actionlint"]
        cfg_problem = config_problems.get("actionlint")
        if cfg_problem is not None:
            out["actionlint"] = {
                "name": "actionlint",
                "selected": sel.get("status"),
                "status": "not_executed",
                "exit_code": None,
                "reason": cfg_problem,
                "execution_started": False,
                "execution_kind": "not_started",
                "access_id": "actionlint/check",
            }
        elif blocked is not None:
            out["actionlint"] = blocked
        elif "actionlint" in target_problems:
            out["actionlint"] = target_refusal(
                "actionlint",
                "actionlint/check",
                next(iter(target_problems["actionlint"].values())),
            )
        elif not al_workflows:
            out["actionlint"] = {
                "name": "actionlint",
                "selected": sel.get("status"),
                "status": "not_applicable",
                "reason": "no_workflow_files",
                "access_id": "actionlint/check",
                **({"rule_ref": rule_ref} if rule_ref is not None else {}),
            }
        else:
            al_target_tuple = tuple(al_workflows)
            al_run = ensure_actionlint_run(al_target_tuple, want_inline_for_al)
            if not al_run["prepared"]:
                out["actionlint"] = {
                    "name": "actionlint",
                    "selected": sel.get("status"),
                    "status": "not_executed",
                    "exit_code": None,
                    "reason": al_run["reason"],
                    "stderr": al_run["detail"],
                    "execution_started": False,
                    "execution_kind": "not_started",
                    "access_id": "actionlint/check",
                    **({"rule_ref": rule_ref} if rule_ref is not None else {}),
                }
            else:
                rep = al_run["report"]
                # If the combined actionlint+shellcheck invocation only failed due to
                # inline shellcheck findings (zero actionlint findings), invoke
                # actionlint independently with -shellcheck= so actionlint gets its
                # own genuine native exit code and verdict.
                if (
                    al_run["shellcheck_enabled"]
                    and not al_run["target_source_problem"]
                    and rep["exit_code"] != 0
                    and not rep["actionlint_findings"]
                    and rep["shellcheck_findings"]
                ):
                    indep_run = ensure_actionlint_run(al_target_tuple, False)
                    if not indep_run["prepared"]:
                        out["actionlint"] = {
                            "name": "actionlint",
                            "selected": sel.get("status"),
                            "status": "not_executed",
                            "exit_code": None,
                            "reason": indep_run["reason"],
                            "stderr": indep_run["detail"],
                            "execution_started": False,
                            "execution_kind": "not_started",
                            "access_id": "actionlint/check",
                            **({"rule_ref": rule_ref} if rule_ref is not None else {}),
                        }
                        rep = None
                    else:
                        al_run = indep_run
                        rep = indep_run["report"]
                if rep is not None:
                    status = _report_to_status(rep, "workflow_problems_reported")
                    # The process exited successfully, but its output does not
                    # establish a verdict. Preserve both facts in the Result.
                    if status == "failed" and rep["exit_code"] == 0:
                        status = "unknown"
                    reported_findings = (
                        rep["actionlint_findings"]
                        if rep["actionlint_findings"]
                        else ([] if rep["shellcheck_findings"] else rep["findings"])
                    )
                    out["actionlint"] = {
                        "name": "actionlint",
                        "selected": sel.get("status"),
                        "status": status,
                        "exit_code": rep["exit_code"],
                        "reason": None if status == "passed" else rep["reason"],
                        "stdout": rep["stdout"],
                        "stderr": rep["stderr"],
                        "findings": reported_findings,
                        "limitations": rep["limitations"],
                        "native_truncated": rep["native_truncated"],
                        "actionlint_findings": rep["actionlint_findings"],
                        "shellcheck_findings": rep["shellcheck_findings"],
                        "shellcheck_enabled": rep["shellcheck_enabled"],
                        "shellcheck_version": al_run["shellcheck_version"],
                        "native_json": rep["native_json"],
                        "executable": al_run["executable"],
                        "tool_version": al_run["tool_version"],
                        "access_id": "actionlint/check",
                        "input_paths": list(al_workflows),
                        "target_source_problem": al_run["target_source_problem"],
                        "coverage": {
                            "workflows_scanned": len(al_workflows),
                            "workflow_files": list(al_workflows),
                            "shellcheck_inline_enabled": bool(
                                rep["shellcheck_enabled"]
                            ),
                        },
                        **({"rule_ref": rule_ref} if rule_ref is not None else {}),
                        **(
                            {
                                "generated_input_refs": [
                                    dict(r) for r in generated_input_refs
                                ]
                            }
                            if generated_input_refs
                            else {}
                        ),
                    }

    if "workflow_shellcheck" in selected_by_name:
        sel = selected_by_name["workflow_shellcheck"]
        blocked, _ = admissions["workflow_shellcheck"]
        cfg_problem = config_problems.get("workflow_shellcheck")
        if cfg_problem is not None:
            out["workflow_shellcheck"] = {
                "name": "workflow_shellcheck",
                "selected": sel.get("status"),
                "status": "not_executed",
                "exit_code": None,
                "reason": cfg_problem,
                "execution_started": False,
                "execution_kind": "not_started",
                "access_id": "actionlint/shellcheck-inline",
            }
        elif blocked is not None:
            out["workflow_shellcheck"] = blocked
        elif not shellcheck_inline:
            out["workflow_shellcheck"] = {
                "name": "workflow_shellcheck",
                "selected": sel.get("status"),
                "status": "not_applicable",
                "reason": "shellcheck_inline_disabled",
                "access_id": "actionlint/shellcheck-inline",
                **({"rule_ref": rule_ref} if rule_ref is not None else {}),
            }
        elif (host_block := actionlint_inline_shellcheck_host_block()) is not None:
            out["workflow_shellcheck"] = {
                "name": "workflow_shellcheck",
                "selected": sel.get("status"),
                "status": "not_applicable",
                "reason": host_block,
                "access_id": "actionlint/shellcheck-inline",
                **({"rule_ref": rule_ref} if rule_ref is not None else {}),
            }
        elif "workflow_shellcheck" in target_problems:
            out["workflow_shellcheck"] = target_refusal(
                "workflow_shellcheck",
                "actionlint/shellcheck-inline",
                next(iter(target_problems["workflow_shellcheck"].values())),
            )
        elif not wf_sc_workflows:
            out["workflow_shellcheck"] = {
                "name": "workflow_shellcheck",
                "selected": sel.get("status"),
                "status": "not_applicable",
                "reason": "no_workflow_files",
                "access_id": "actionlint/shellcheck-inline",
                **({"rule_ref": rule_ref} if rule_ref is not None else {}),
            }
        else:
            sc_ok, sc_ver, sc_detail, _sc_exe = check_shellcheck_ready()
            if not sc_ok:
                out["workflow_shellcheck"] = {
                    "name": "workflow_shellcheck",
                    "selected": sel.get("status"),
                    "status": "not_executed",
                    "exit_code": None,
                    "reason": "shellcheck_unprepared"
                    if sc_ver is None
                    else "shellcheck_version_mismatch",
                    "stderr": sc_detail,
                    "execution_started": False,
                    "execution_kind": "not_started",
                    "access_id": "actionlint/shellcheck-inline",
                    **({"rule_ref": rule_ref} if rule_ref is not None else {}),
                }
            else:
                al_run = ensure_actionlint_run(tuple(wf_sc_workflows), True)
                if not al_run["prepared"]:
                    out["workflow_shellcheck"] = {
                        "name": "workflow_shellcheck",
                        "selected": sel.get("status"),
                        "status": "not_executed",
                        "exit_code": None,
                        "reason": al_run["reason"],
                        "stderr": al_run["detail"],
                        "execution_started": False,
                        "execution_kind": "not_started",
                        "access_id": "actionlint/shellcheck-inline",
                        **({"rule_ref": rule_ref} if rule_ref is not None else {}),
                    }
                elif not al_run["shellcheck_enabled"]:
                    out["workflow_shellcheck"] = {
                        "name": "workflow_shellcheck",
                        "selected": sel.get("status"),
                        "status": "not_executed",
                        "exit_code": None,
                        "reason": "shellcheck_disabled_in_actionlint",
                        "execution_started": False,
                        "execution_kind": "not_started",
                        "access_id": "actionlint/shellcheck-inline",
                        **({"rule_ref": rule_ref} if rule_ref is not None else {}),
                    }
                else:
                    al_rep = al_run["report"]
                    al_dbg = al_run.get("shellcheck_debug") or {}
                    _, inline_findings = split_actionlint_diagnostics(
                        al_rep["findings"]
                    )
                    if inline_findings:
                        inline_status = (
                            "unknown" if al_rep["exit_code"] == 0 else "failed"
                        )
                        inline_reason = (
                            "actionlint_exit_status_inconsistent"
                            if inline_status == "unknown"
                            else "shellcheck_findings"
                        )
                        inline_exit_code = al_rep["exit_code"]
                        inline_exe = al_run["executable"]
                    elif al_dbg.get("disabled"):
                        inline_status = "not_executed"
                        inline_reason = "shellcheck_disabled_in_actionlint"
                        inline_exit_code = None
                        inline_exe = None
                    elif al_rep["outcome"] != "clean":
                        # An unusable actionlint verdict without inline findings
                        # cannot establish an independent ShellCheck verdict.
                        inline_status = "not_executed"
                        inline_reason = (
                            al_rep["reason"]
                            if al_rep["reason"] == "actionlint_output_invalid"
                            else "actionlint_failed_without_shellcheck_verdict"
                        )
                        inline_exit_code = None
                        inline_exe = None
                    elif not al_dbg.get("rule_ran"):
                        inline_status = "not_executed"
                        inline_reason = "shellcheck_execution_unproven"
                        inline_exit_code = None
                        inline_exe = None
                    elif int(al_dbg.get("invoked_steps") or 0) <= 0:
                        inline_status = "not_applicable"
                        inline_reason = "no_applicable_inline_shell_steps"
                        inline_exit_code = al_rep["exit_code"]
                        inline_exe = al_run["executable"]
                    else:
                        inline_status = "passed"
                        inline_reason = None
                        inline_exit_code = al_rep["exit_code"]
                        inline_exe = al_run["executable"]
                    out["workflow_shellcheck"] = {
                        "name": "workflow_shellcheck",
                        "selected": sel.get("status"),
                        "status": inline_status,
                        "exit_code": inline_exit_code,
                        "reason": inline_reason,
                        "stdout": al_rep["stdout"],
                        "stderr": al_rep["stderr"],
                        "findings": inline_findings[:MAX_FINDINGS],
                        "limitations": al_rep["limitations"],
                        "native_truncated": al_rep["native_truncated"],
                        "native_json": al_rep["native_json"],
                        "executable": inline_exe,
                        "tool_version": sc_ver if inline_exe else None,
                        "access_id": "actionlint/shellcheck-inline",
                        "input_paths": list(wf_sc_workflows),
                        "target_source_problem": al_run["target_source_problem"],
                        "actionlint_exit_code": al_rep["exit_code"],
                        "shared_actionlint": bool("actionlint" in out),
                        "coverage": {
                            "workflow_inline_shell": inline_status == "passed",
                            "workflow_inline_status": inline_status,
                            "inline_shell_steps_invoked": int(
                                al_dbg.get("invoked_steps") or 0
                            ),
                            "workflow_files": list(wf_sc_workflows),
                            "per_script_clean_receipts_available": False,
                        },
                        **({"rule_ref": rule_ref} if rule_ref is not None else {}),
                        **(
                            {
                                "generated_input_refs": [
                                    dict(r) for r in generated_input_refs
                                ]
                            }
                            if generated_input_refs
                            else {}
                        ),
                    }

    if "shellcheck" in selected_by_name:
        sel = selected_by_name["shellcheck"]
        blocked, _ = admissions["shellcheck"]
        cfg_problem = config_problems.get("shellcheck")
        if cfg_problem is not None:
            out["shellcheck"] = {
                "name": "shellcheck",
                "selected": sel.get("status"),
                "status": "not_executed",
                "exit_code": None,
                "reason": cfg_problem,
                "execution_started": False,
                "execution_kind": "not_started",
                "access_id": "shellcheck/check",
            }
        elif blocked is not None:
            out["shellcheck"] = blocked
        elif "shellcheck" in target_problems:
            out["shellcheck"] = target_refusal(
                "shellcheck",
                "shellcheck/check",
                next(iter(target_problems["shellcheck"].values())),
            )
        elif not sc_shells:
            out["shellcheck"] = {
                "name": "shellcheck",
                "selected": sel.get("status"),
                "status": "not_applicable",
                "reason": "no_shell_files",
                "access_id": "shellcheck/check",
                **({"rule_ref": rule_ref} if rule_ref is not None else {}),
            }
        else:
            sc_ok, sc_ver, sc_detail, _sc_exe = check_shellcheck_ready()
            if not sc_ok:
                out["shellcheck"] = {
                    "name": "shellcheck",
                    "selected": sel.get("status"),
                    "status": "not_executed",
                    "exit_code": None,
                    "reason": "shellcheck_unprepared"
                    if sc_ver is None
                    else "shellcheck_version_mismatch",
                    "stderr": sc_detail,
                    "execution_started": False,
                    "execution_kind": "not_started",
                    "access_id": "shellcheck/check",
                    **({"rule_ref": rule_ref} if rule_ref is not None else {}),
                }
            if sc_ok:
                sc_prefix, standalone_exe = _resolve_tool_prefix("shellcheck")
                sc_pol = shellcheck_policy(sc_shells, config=shellcheck_config)
                sc_completed, sc_problem, locations = scan_targets(
                    sc_pol, shellcheck_arguments, sc_prefix, sc_shells
                )
                if sc_completed is None:
                    out["shellcheck"] = target_refusal(
                        "shellcheck", "shellcheck/check", sc_problem
                    )
            if sc_ok and sc_completed is not None:
                sc_stdout = getattr(sc_completed, "stdout", "") or ""
                sc_stderr = getattr(sc_completed, "stderr", "") or ""
                standalone_rep = script_report(
                    sc_pol,
                    sc_completed.returncode,
                    stdout=sc_stdout,
                    stderr=sc_stderr,
                    native_raw=sc_stdout,
                )
                logical_findings(standalone_rep, locations)
                if standalone_rep["outcome"] == "clean":
                    standalone_status = "passed"
                    standalone_reason = None
                elif standalone_rep["outcome"] == "findings":
                    standalone_status = "failed"
                    standalone_reason = "shellcheck_findings"
                else:
                    standalone_status = "failed"
                    standalone_reason = "shellcheck_execution_error"
                out["shellcheck"] = {
                    "name": "shellcheck",
                    "selected": sel.get("status"),
                    "status": standalone_status,
                    "exit_code": standalone_rep["exit_code"],
                    "reason": standalone_reason,
                    "stdout": standalone_rep["stdout"],
                    "stderr": standalone_rep["stderr"],
                    "findings": standalone_rep["findings"],
                    "native_json": standalone_rep["native_json"],
                    "executable": standalone_exe,
                    "tool_version": sc_ver,
                    "access_id": "shellcheck/check",
                    "input_paths": list(sc_shells),
                    "target_source_problem": sc_problem,
                    "coverage": {
                        "standalone_shell": standalone_status == "passed",
                        "standalone_status": standalone_status,
                        "standalone_files": list(sc_shells),
                        "per_script_clean_receipts_available": False,
                    },
                    **({"rule_ref": rule_ref} if rule_ref is not None else {}),
                    **(
                        {
                            "generated_input_refs": [
                                dict(r) for r in generated_input_refs
                            ]
                        }
                        if generated_input_refs
                        else {}
                    ),
                }

    if "zizmor" in selected_by_name:
        sel = selected_by_name["zizmor"]
        blocked, _ = admissions["zizmor"]
        cfg_problem = config_problems.get("zizmor")
        if cfg_problem is not None:
            out["zizmor"] = {
                "name": "zizmor",
                "selected": sel.get("status"),
                "status": "not_executed",
                "exit_code": None,
                "reason": cfg_problem,
                "execution_started": False,
                "execution_kind": "not_started",
                "access_id": "zizmor/check",
            }
        elif blocked is not None:
            out["zizmor"] = blocked
        elif "zizmor" in target_problems:
            out["zizmor"] = target_refusal(
                "zizmor", "zizmor/check", next(iter(target_problems["zizmor"].values()))
            )
        elif not zz_workflows:
            out["zizmor"] = {
                "name": "zizmor",
                "selected": sel.get("status"),
                "status": "not_applicable",
                "reason": "no_workflow_files",
                "access_id": "zizmor/check",
                **({"rule_ref": rule_ref} if rule_ref is not None else {}),
            }
        else:
            zz_prefix, zz_exe = _resolve_tool_prefix("zizmor")
            ok_zz, obs_zz, err_zz = verify_tool_version(
                zz_prefix, ["--version"], ZIZMOR_VERSION, root=root, runner=runner
            )
            if not ok_zz:
                out["zizmor"] = {
                    "name": "zizmor",
                    "selected": sel.get("status"),
                    "status": "not_executed",
                    "exit_code": None,
                    "reason": "zizmor_unprepared"
                    if obs_zz is None
                    else "zizmor_version_mismatch",
                    "stderr": err_zz,
                    "execution_started": False,
                    "execution_kind": "not_started",
                    "access_id": "zizmor/check",
                    **({"rule_ref": rule_ref} if rule_ref is not None else {}),
                }
            if ok_zz:
                zz_pol = zizmor_policy(
                    zz_workflows,
                    config=zizmor_config,
                    persona=zizmor_persona,
                    offline=zizmor_offline,
                )
                zz_completed, zz_problem, locations = scan_targets(
                    zz_pol, zizmor_arguments, zz_prefix, zz_workflows
                )
                if zz_completed is None:
                    out["zizmor"] = target_refusal("zizmor", "zizmor/check", zz_problem)
            if ok_zz and zz_completed is not None:
                zz_stdout = getattr(zz_completed, "stdout", "") or ""
                zz_stderr = getattr(zz_completed, "stderr", "") or ""
                zz_rep = script_report(
                    zz_pol,
                    zz_completed.returncode,
                    stdout=zz_stdout,
                    stderr=zz_stderr,
                    native_raw=zz_stdout,
                )
                logical_findings(zz_rep, locations)
                status = _report_to_status(zz_rep, "actions_security_findings_reported")
                out["zizmor"] = {
                    "name": "zizmor",
                    "selected": sel.get("status"),
                    "status": status,
                    "exit_code": zz_rep["exit_code"],
                    "reason": None if status == "passed" else zz_rep["reason"],
                    "stdout": zz_rep["stdout"],
                    "stderr": zz_rep["stderr"],
                    "findings": zz_rep["findings"],
                    "native_json": zz_rep["native_json"],
                    "executable": zz_exe,
                    "tool_version": obs_zz,
                    "access_id": "zizmor/check",
                    "input_paths": list(zz_workflows),
                    "target_source_problem": zz_problem,
                    "coverage": {
                        "workflows_scanned": len(zz_workflows),
                        "workflow_files": list(zz_workflows),
                        "offline": bool(zizmor_offline),
                    },
                    **({"rule_ref": rule_ref} if rule_ref is not None else {}),
                    **(
                        {
                            "generated_input_refs": [
                                dict(r) for r in generated_input_refs
                            ]
                        }
                        if generated_input_refs
                        else {}
                    ),
                }

    for check_name, entry in out.items():
        if check_name in target_problems:
            entry["target_problems"] = target_problems[check_name]
        paths = entry.get("input_paths")
        if paths:
            entry["target_sources"] = list(target_sources[tuple(paths)])
        if entry.get("target_source_problem") is None:
            entry.pop("target_source_problem", None)
        elif entry["status"] != "failed":
            entry["native_status"] = entry["status"]
            entry["status"] = "unknown"
            entry["reason"] = entry["target_source_problem"]
        if check_name in config_problems:
            entry.pop("rule_ref", None)
            continue
        resolved_rule = (
            rule_refs.get(check_name) if isinstance(rule_refs, dict) else rule_ref
        )
        if resolved_rule is not None:
            entry["rule_ref"] = resolved_rule
        else:
            entry.pop("rule_ref", None)
    return out
