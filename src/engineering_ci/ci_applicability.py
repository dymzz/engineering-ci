"""Decide which of this repository's existing CI checks apply to a candidate.

Three layers stay separate and are never merged into one verdict:

1. the GitHub Workflow trigger, which only starts the workflow;
2. the candidate's cumulative change set, which decides the planned scope;
3. the planned Job/step scope, not an observed execution fact.

The decision is read-only and fail-closed: it never runs a check, never selects
pytest targets, never admits prior evidence (#63) and never produces a Required
or Release verdict (#61). An incomplete change set, an unrecognized event or an
unsupported invocation escalates to every configured check instead of skipping
work. Only the cumulative change set decides this invocation; reusing an earlier
Run/Attempt result stays an unproven #63 decision and is recorded as such.

The check-input contract itself lives in the shared
``engineering_ci.ci_check_inputs`` module, which the Dagger module also consumes
for its container source excludes.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import sys
from importlib import metadata
from pathlib import Path

from . import ci_check_inputs as check_inputs
from . import selection

SCHEMA_VERSION = 1
KIND = "engineering-ci-ci-applicability"
WORKFLOW = ".github/workflows/ci.yml"
MAX_ENTRIES = 256


class ApplicabilityError(ValueError):
    """The invocation input cannot produce a decision."""


def _version():
    try:
        return metadata.version("engineering-ci")
    except metadata.PackageNotFoundError:
        return "0+uninstalled"


def _digest_text():
    try:
        source = inspect.getsource(check_inputs)
    except (OSError, TypeError):
        return None
    return "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest()


def _bounded(values):
    ordered = sorted(set(values))
    return ordered[:MAX_ENTRIES], len(ordered)


def _paths_of(changes):
    # Git reports repository-relative POSIX paths. A literal backslash on POSIX
    # is part of the filename, so identity is preserved instead of normalised.
    return sorted(set(changes.paths or ()))


def _matches(check, path):
    if check_inputs.is_source_excluded_path(path):
        return False
    return check_inputs.matches_check(path, check)


def _check_result(check, paths):
    matched = [path for path in paths if _matches(check, path)]
    if not matched:
        reason = "source_excluded_changes" if check.source_scope else "no_check_inputs"
        return _not_applicable(check, reason)
    reason = (
        "repository_source_inputs" if check.source_scope else "check_inputs_changed"
    )
    listed, count = _bounded(matched)
    return {
        "name": check.name,
        "job": check.job,
        "dagger_check": check.dagger_check,
        "status": "applicable",
        "reason": reason,
        "input_count": count,
        "inputs": listed,
        "inputs_truncated": count > len(listed),
    }


def _applicable(check, reason, paths):
    listed, count = _bounded(paths)
    return {
        "name": check.name,
        "job": check.job,
        "dagger_check": check.dagger_check,
        "status": "applicable",
        "reason": reason,
        "input_count": count,
        "inputs": listed,
        "inputs_truncated": count > len(listed),
    }


def _not_applicable(check, reason):
    return {
        "name": check.name,
        "job": check.job,
        "dagger_check": check.dagger_check,
        "status": "not_applicable",
        "reason": reason,
        "input_count": 0,
        "inputs": [],
        "inputs_truncated": False,
    }


def decide(changes, *, full=False, full_reason=None):
    """Return the planned scope for one cumulative change set.

    ``changes`` may be ``None`` only for an explicit full run that has no
    concrete base to compare against (for example a Release ``workflow_call``).
    """
    reason = full_reason or "explicit_full_verification"
    paths = _paths_of(changes) if changes is not None else []
    checks = check_inputs.CI_CHECKS
    if full:
        return (
            [_applicable(check, reason, paths) for check in checks],
            "escalated",
            reason,
        )
    if changes is None or not changes.complete:
        return (
            [_applicable(check, "changeset_incomplete", paths) for check in checks],
            "escalated",
            "changeset_incomplete",
        )
    if not paths:
        return (
            [_not_applicable(check, "no_changed_paths") for check in checks],
            "not_applicable",
            "no_changed_paths",
        )
    if any(check_inputs.is_toolchain_path(path) for path in paths):
        return (
            [_applicable(check, "toolchain_inputs", paths) for check in checks],
            "applicable",
            "toolchain_inputs",
        )
    self_verification = [
        path for path in paths if check_inputs.is_self_verification_path(path)
    ]
    if self_verification:
        return (
            [
                _applicable(check, "self_verification_inputs", self_verification)
                for check in checks
            ],
            "applicable",
            "self_verification_inputs",
        )
    results = [_check_result(check, paths) for check in checks]
    applicable = [check for check in results if check["status"] == "applicable"]
    status = "applicable" if applicable else "not_applicable"
    reason = "applicable_checks_present" if applicable else "no_applicable_checks"
    return results, status, reason


def build_report(
    changes,
    *,
    event,
    full=False,
    full_reason=None,
    requested_base=None,
    requested_head=None,
    previous_commit=None,
):
    """Build the planned-scope report; never claims a check ran."""
    checks, candidate_status, candidate_reason = decide(
        changes, full=full, full_reason=full_reason
    )
    paths = _paths_of(changes) if changes is not None else []
    deleted = changes.deleted_paths if changes is not None else ()
    listed, count = _bounded(paths)
    deleted_listed, deleted_count = _bounded(deleted)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "generator": {
            "name": "engineering-ci",
            "version": _version(),
            "contract": {
                "kind": check_inputs.KIND,
                "version": check_inputs.CONTRACT_VERSION,
                "module": "engineering_ci.ci_check_inputs",
                "source_sha256": _digest_text(),
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
            "requested_base": requested_base,
            "requested_head": requested_head,
            "base_commit": changes.base_commit if changes is not None else None,
            "head_commit": changes.head_commit if changes is not None else None,
            "tree": changes.tree if changes is not None else None,
            "identity_status": (
                "bound_to_change_set"
                if changes is not None and changes.complete
                else "not_bound_to_change_set"
            ),
            "changed_path_count": count,
            "changed_paths": listed,
            "changed_paths_truncated": count > len(listed),
            "deleted_path_count": deleted_count,
            "deleted_paths": deleted_listed,
            "status": candidate_status,
            "reason": candidate_reason,
            "applicable_checks": [
                check["name"] for check in checks if check["status"] == "applicable"
            ],
        },
        "layer_3_planned_scope": {
            "claim": "planned_scope_only",
            "checks": checks,
            "note": (
                "A skipped native step records not_applicable here; no exit code, "
                "artifact or PASS is fabricated for it. Actual Job/step/native "
                "results are observed by the existing Actions/Dagger authorities."
            ),
        },
        "execution_observation": {
            "status": "not_observed_in_this_report",
            "authority": ["#52", "#75", "#86"],
            "note": (
                "This report is produced before any Job starts. It cannot prove a "
                "Job/step began, passed, failed, skipped or was cancelled; that "
                "remains the original Run/Job/Attempt and native Result evidence."
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
        for check in report["layer_3_planned_scope"]["checks"]
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
    for check in report["layer_3_planned_scope"]["checks"]:
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
    for check in report["layer_3_planned_scope"]["checks"]:
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
            "Decide the planned native-check scope for a cumulative candidate; "
            "read-only and fail-closed"
        ),
    )
    parser.add_argument("--root", default=".")
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
    try:
        changes = _changes(root, args)
        report = build_report(
            changes,
            event=args.event,
            full=args.full,
            full_reason=args.full_reason,
            requested_base=args.base,
            requested_head=args.head,
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
