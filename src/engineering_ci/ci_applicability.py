"""Decide which of this repository's existing CI checks apply to a candidate.

Three layers stay separate and are never merged into one verdict:

1. the GitHub Workflow trigger, which only starts the workflow;
2. the candidate's cumulative change set, which decides the applicable checks;
3. the Job/step execution scope recorded by the workflow.

The decision is read-only and fail-closed: it never runs a check, never selects
pytest targets, never admits prior evidence (#63) and never produces a Required
or Release verdict (#61). An incomplete change set, an unreadable policy or an
unsupported event escalates to every configured check instead of skipping work.
Only the cumulative change set decides this invocation; reusing an earlier
Run/Attempt result stays an unproven #63 decision and is recorded as such.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from . import selection

SCHEMA_VERSION = 1
KIND = "engineering-ci-ci-applicability"
WORKFLOW = ".github/workflows/ci.yml"
MAX_ENTRIES = 256
MAX_TEXT = 512
_CHECK_NAME = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


class ApplicabilityError(ValueError):
    """The policy or the invocation input cannot produce a decision."""


@dataclass(frozen=True)
class CheckPolicy:
    """One existing native check and the inputs that make it applicable."""

    name: str
    job: str
    dagger_check: str
    suffixes: tuple[str, ...] = ()
    files: frozenset[str] = frozenset()
    prefixes: tuple[str, ...] = ()
    source_scope: bool = False

    @property
    def github_output(self):
        return self.name.replace("-", "_")


@dataclass(frozen=True)
class Config:
    schema_version: int
    checks: tuple[CheckPolicy, ...]
    toolchain_files: frozenset[str]
    toolchain_prefixes: tuple[str, ...]
    documentation_files: frozenset[str]
    documentation_prefixes: tuple[str, ...]
    documentation_markdown_prefixes: tuple[str, ...]


def _text(value, name):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_TEXT
        or any(character in value for character in "\0\r\n")
    ):
        raise ApplicabilityError(f"{name}_invalid")
    return value


def _normalized_path(value, name, *, prefix=False):
    text = _text(value, name).replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    trailing = text.endswith("/")
    if trailing and not prefix:
        raise ApplicabilityError(f"{name}_invalid")
    body = text[:-1] if trailing else text
    if not body or body.startswith("/") or ":" in body.split("/", 1)[0]:
        raise ApplicabilityError(f"{name}_invalid")
    if any(part in {"", ".", ".."} for part in body.split("/")):
        raise ApplicabilityError(f"{name}_invalid")
    return text


def _text_list(value, name, *, prefix=False):
    if not isinstance(value, list) or len(value) > MAX_ENTRIES:
        raise ApplicabilityError(f"{name}_invalid")
    texts = [_normalized_path(item, name, prefix=prefix) for item in value]
    if len(set(texts)) != len(texts):
        raise ApplicabilityError(f"{name}_invalid")
    return tuple(texts)


def _bool(value, name):
    if type(value) is not bool:
        raise ApplicabilityError(f"{name}_invalid")
    return value


def _section(value, name, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ApplicabilityError(f"config_{name}_invalid")
    return value


def load_config(path):
    """Read one strict policy file; unknown or missing keys are refused."""
    path = Path(path)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ApplicabilityError("config_unreadable") from error
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise ApplicabilityError("config_invalid_toml") from error
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version",
        "toolchain",
        "documentation",
        "check",
    }:
        raise ApplicabilityError("config_keys_invalid")
    if raw["schema_version"] != SCHEMA_VERSION:
        raise ApplicabilityError("config_schema_version_unsupported")
    toolchain = _section(raw["toolchain"], "toolchain", ("files", "prefixes"))
    documentation = _section(
        raw["documentation"],
        "documentation",
        ("files", "prefixes", "markdown_prefixes"),
    )
    entries = raw["check"]
    if not isinstance(entries, list) or not entries or len(entries) > MAX_ENTRIES:
        raise ApplicabilityError("config_check_invalid")
    checks = tuple(_check_policy(entry, index) for index, entry in enumerate(entries))
    names = [check.name for check in checks]
    outputs = [check.github_output for check in checks]
    if len(set(names)) != len(names) or len(set(outputs)) != len(outputs):
        raise ApplicabilityError("config_check_duplicate")
    return Config(
        schema_version=SCHEMA_VERSION,
        checks=checks,
        toolchain_files=frozenset(_text_list(toolchain["files"], "toolchain.files")),
        toolchain_prefixes=_text_list(
            toolchain["prefixes"], "toolchain.prefixes", prefix=True
        ),
        documentation_files=frozenset(
            _text_list(documentation["files"], "documentation.files")
        ),
        documentation_prefixes=_text_list(
            documentation["prefixes"], "documentation.prefixes", prefix=True
        ),
        documentation_markdown_prefixes=_text_list(
            documentation["markdown_prefixes"],
            "documentation.markdown_prefixes",
            prefix=True,
        ),
    )


def _check_policy(value, index):
    name = f"check[{index}]"
    allowed = {
        "name",
        "job",
        "dagger_check",
        "suffixes",
        "files",
        "prefixes",
        "source_scope",
    }
    if not isinstance(value, dict) or set(value) - allowed:
        raise ApplicabilityError(f"{name}_invalid")
    if not {"name", "job", "dagger_check"} <= set(value):
        raise ApplicabilityError(f"{name}_invalid")
    check_name = _text(value["name"], f"{name}.name")
    if not _CHECK_NAME.fullmatch(check_name):
        raise ApplicabilityError(f"{name}_name_invalid")
    job = _text(value["job"], f"{name}.job")
    dagger_check = _text(value["dagger_check"], f"{name}.dagger_check")
    if not _IDENTIFIER.fullmatch(job) or not _IDENTIFIER.fullmatch(dagger_check):
        raise ApplicabilityError(f"{name}_invalid")
    source_scope = _bool(value.get("source_scope", False), f"{name}.source_scope")
    suffixes = _text_list(value.get("suffixes", []), f"{name}.suffixes")
    if any(not suffix.startswith(".") or "/" in suffix for suffix in suffixes):
        raise ApplicabilityError(f"{name}_suffixes_invalid")
    files = frozenset(_text_list(value.get("files", []), f"{name}.files"))
    prefixes = _text_list(value.get("prefixes", []), f"{name}.prefixes", prefix=True)
    if source_scope and (suffixes or files or prefixes):
        raise ApplicabilityError(f"{name}_scope_ambiguous")
    if not source_scope and not (suffixes or files or prefixes):
        raise ApplicabilityError(f"{name}_inputs_missing")
    return CheckPolicy(
        name=check_name,
        job=job,
        dagger_check=dagger_check,
        suffixes=suffixes,
        files=files,
        prefixes=prefixes,
        source_scope=source_scope,
    )


def _violates_any(path, files, prefixes, suffixes=()):
    return (
        path in files
        or any(path.startswith(prefix) for prefix in prefixes)
        or any(path.endswith(suffix) for suffix in suffixes)
    )


def _is_toolchain_path(path, config):
    return _violates_any(path, config.toolchain_files, config.toolchain_prefixes)


def is_documentation_path(path, config):
    """Whether the path is excluded from the Dagger repository source.

    Only Markdown under the configured documentation prefixes is excluded, so a
    schema or script under ``docs/`` keeps the native checks applicable.
    """
    path = path.replace("\\", "/")
    return (
        path in config.documentation_files
        or any(path.startswith(prefix) for prefix in config.documentation_prefixes)
        or (
            path.endswith(".md")
            and any(
                path.startswith(prefix)
                for prefix in config.documentation_markdown_prefixes
            )
        )
    )


def _matches_path_rule(path, check):
    return _violates_any(path, check.files, check.prefixes, check.suffixes)


def _bounded(values):
    ordered = sorted(set(values))
    return ordered[:MAX_ENTRIES], len(ordered)


def _version():
    try:
        return metadata.version("engineering-ci")
    except metadata.PackageNotFoundError:
        return "0+uninstalled"


def _digest(path):
    try:
        return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _check_result(check, paths, config):
    if check.source_scope:
        matched = [path for path in paths if not is_documentation_path(path, config)]
    else:
        matched = [path for path in paths if _matches_path_rule(path, check)]
    inputs, count = _bounded(matched)
    applicable = bool(matched)
    if applicable:
        reason = (
            "repository_source_inputs" if check.source_scope else "check_inputs_changed"
        )
    else:
        reason = (
            "documentation_only_changes" if check.source_scope else "no_check_inputs"
        )
    return {
        "name": check.name,
        "job": check.job,
        "dagger_check": check.dagger_check,
        "status": "applicable" if applicable else "not_applicable",
        "reason": reason,
        "input_count": count,
        "inputs": inputs,
        "inputs_truncated": count > len(inputs),
    }


def _paths_of(changes):
    return sorted({path.replace("\\", "/") for path in (changes.paths or ())})


def decide(changes, config, *, full=False, full_reason=None):
    """Return the three-layer decision for one change set.

    ``changes`` may be ``None`` only for an explicit full run that has no
    concrete base to compare against (for example a Release ``workflow_call``).
    """
    reason = full_reason or "explicit_full_verification"
    paths = _paths_of(changes) if changes is not None else []
    listed, count = _bounded(paths)
    if full:
        checks = [
            {
                "name": check.name,
                "job": check.job,
                "dagger_check": check.dagger_check,
                "status": "applicable",
                "reason": reason,
                "input_count": count,
                "inputs": listed,
                "inputs_truncated": count > len(listed),
            }
            for check in config.checks
        ]
        return checks, "escalated", reason
    if changes is None or not changes.complete:
        checks = [
            {
                "name": check.name,
                "job": check.job,
                "dagger_check": check.dagger_check,
                "status": "applicable",
                "reason": "changeset_incomplete",
                "input_count": count,
                "inputs": listed,
                "inputs_truncated": count > len(listed),
            }
            for check in config.checks
        ]
        return checks, "escalated", "changeset_incomplete"
    if not paths:
        checks = [
            {
                "name": check.name,
                "job": check.job,
                "dagger_check": check.dagger_check,
                "status": "not_applicable",
                "reason": "no_changed_paths",
                "input_count": 0,
                "inputs": [],
                "inputs_truncated": False,
            }
            for check in config.checks
        ]
        return checks, "not_applicable", "no_changed_paths"
    toolchain = [path for path in paths if _is_toolchain_path(path, config)]
    if toolchain:
        listed_toolchain, toolchain_count = _bounded(toolchain)
        checks = [
            {
                "name": check.name,
                "job": check.job,
                "dagger_check": check.dagger_check,
                "status": "applicable",
                "reason": "toolchain_inputs",
                "input_count": toolchain_count,
                "inputs": listed_toolchain,
                "inputs_truncated": toolchain_count > len(listed_toolchain),
            }
            for check in config.checks
        ]
        return checks, "applicable", "toolchain_inputs"
    checks = [_check_result(check, paths, config) for check in config.checks]
    applicable = [check for check in checks if check["status"] == "applicable"]
    status = "applicable" if applicable else "not_applicable"
    reason = "applicable_checks_present" if applicable else "no_applicable_checks"
    return checks, status, reason


def build_report(
    changes,
    config,
    *,
    config_path,
    event,
    full=False,
    full_reason=None,
    previous_commit=None,
):
    """Build the structured applicability report; never claims a check ran."""
    checks, candidate_status, candidate_reason = decide(
        changes, config, full=full, full_reason=full_reason
    )
    paths = _paths_of(changes) if changes is not None else []
    listed, count = _bounded(paths)
    deleted, deleted_count = (
        _bounded(changes.deleted_paths or ()) if changes is not None else ([], 0)
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "generator": {
            "name": "engineering-ci",
            "version": _version(),
            "policy": {
                "path": str(config_path).replace("\\", "/"),
                "sha256": _digest(config_path),
            },
        },
        "layer_1_trigger": {
            "workflow": WORKFLOW,
            "event": event,
            "full_requested": bool(full),
            "full_reason": full_reason if full else None,
        },
        "layer_2_candidate": {
            "snapshot": changes.snapshot if changes is not None else None,
            "complete": changes.complete if changes is not None else None,
            "error": changes.error if changes is not None else None,
            "base_commit": changes.base_commit if changes is not None else None,
            "head_commit": changes.head_commit if changes is not None else None,
            "tree": changes.tree if changes is not None else None,
            "changed_path_count": count,
            "changed_paths": listed,
            "changed_paths_truncated": count > len(listed),
            "deleted_path_count": deleted_count,
            "deleted_paths": deleted,
            "status": candidate_status,
            "reason": candidate_reason,
            "applicable_checks": [
                check["name"] for check in checks if check["status"] == "applicable"
            ],
        },
        "layer_3_execution": {
            "checks": checks,
            "note": (
                "A skipped native step records not_applicable here; no exit code, "
                "artifact or PASS is fabricated for it."
            ),
        },
        "increment": {
            "status": "unavailable",
            "reason": "trusted_evidence_admission_unavailable",
            "previous_commit": previous_commit,
            "owner": "#63",
            "note": (
                "Only the cumulative change set decides this invocation. Reusing "
                "an earlier Run/Attempt result requires #63 admission and is not "
                "claimed by this report."
            ),
        },
        "summary": [
            f"{check['name']}: {check['status']} ({check['reason']})"
            for check in checks
        ],
    }


def _write_json(path, report):
    Path(path).write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_github_output(path, report):
    lines = [
        f"{check['name'].replace('-', '_')}="
        f"{'true' if check['status'] == 'applicable' else 'false'}"
        for check in report["layer_3_execution"]["checks"]
    ]
    lines.append(f"candidate_status={report['layer_2_candidate']['status']}")
    lines.append("applicability=recorded")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _summary_markdown(report):
    candidate = report["layer_2_candidate"]
    lines = [
        "### CI applicability",
        "",
        f"- Event: `{report['layer_1_trigger']['event']}`",
        f"- Candidate: `{candidate['status']}` ({candidate['reason']})",
        f"- Changed paths: {candidate['changed_path_count']}",
        (
            f"- Increment evidence: `{report['increment']['status']}` "
            f"({report['increment']['reason']}, owner {report['increment']['owner']})"
        ),
        "",
        "| Check | Job | Status | Reason | Inputs |",
        "| --- | --- | --- | --- | ---: |",
    ]
    for check in report["layer_3_execution"]["checks"]:
        lines.append(
            f"| `{check['name']}` | `{check['job']}` | {check['status']} "
            f"| `{check['reason']}` | {check['input_count']} |"
        )
    lines.append("")
    return "\n".join(lines)


def _print_summary(report):
    candidate = report["layer_2_candidate"]
    print(f"event: {report['layer_1_trigger']['event']}")
    print(f"candidate: {candidate['status']} ({candidate['reason']})")
    for check in report["layer_3_execution"]["checks"]:
        print(
            f"{check['name']}: {check['status']} "
            f"({check['reason']}, {check['input_count']} inputs)"
        )


def _changes(root, args):
    if args.full and not args.base and not args.before:
        return None
    if args.snapshot == "push":
        if not args.before or not args.after:
            raise ApplicabilityError("push_commits_required")
        return selection.changeset(
            root,
            base=args.before,
            head=args.after,
            mode="push",
            before=args.before,
            after=args.after,
        )
    if not args.base:
        raise ApplicabilityError("base_commit_required")
    return selection.changeset(
        root, base=args.base, head=args.head or "HEAD", mode="commit"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="engineering-ci ci-applicability",
        description=(
            "Decide which existing CI checks apply to a cumulative candidate; "
            "read-only and fail-closed"
        ),
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--config", default="ci-applicability.toml")
    parser.add_argument("--event", default="local")
    parser.add_argument("--snapshot", choices=("commit", "push"), default="commit")
    parser.add_argument("--base")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--before")
    parser.add_argument("--after")
    parser.add_argument("--previous-commit")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--full-reason")
    parser.add_argument("--output")
    parser.add_argument("--github-output")
    parser.add_argument("--summary")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.root)
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = root / config_path
    try:
        config = load_config(config_path)
        changes = _changes(root, args)
        report = build_report(
            changes,
            config,
            config_path=config_path,
            event=args.event,
            full=args.full,
            full_reason=args.full_reason,
            previous_commit=args.previous_commit,
        )
    except (ApplicabilityError, selection.SelectionError, OSError) as error:
        print(f"ci-applicability: {error}", file=sys.stderr)
        return 2
    if args.output:
        _write_json(args.output, report)
    if args.github_output:
        _write_github_output(args.github_output, report)
    if args.summary:
        with Path(args.summary).open("a", encoding="utf-8") as handle:
            handle.write(_summary_markdown(report))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        _print_summary(report)
    return 0
