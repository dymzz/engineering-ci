"""Versioned Expected Check projection for #66-B.

This module derives scheduler-independent Expected Checks from the existing
Selection/Plan inputs. It deliberately does not claim a consumer Profile is
complete: without an authoritative Profile/rule source the result stays
``partial`` or ``unknown``. Required policy remains outside this module.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import check_contract, plan_contract

EXPECTED_VERSION = 1
EXPECTED_KIND = "engineering-ci-expected-checks"
STATUSES = frozenset({"complete", "partial", "unknown"})


class ExpectedError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _file_digest(path):
    try:
        return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _source_ref(changeset):
    tree = changeset.execution_tree or changeset.tree
    source = {
        "kind": "git",
        "snapshot": changeset.snapshot,
        "tree": tree,
        "index_tree": changeset.index_tree,
        "index_file": changeset.index_file,
        "base_commit": changeset.base_commit,
        "head_commit": changeset.head_commit,
        "changed_paths": list(changeset.paths),
    }
    if tree and changeset.snapshot in {"commit", "push", "pr_merge"}:
        source["binding_status"] = "tree_identity"
    elif changeset.snapshot == "index" and changeset.index_tree:
        source["binding_status"] = "index_identity"
    elif changeset.snapshot == "worktree":
        source["binding_status"] = "worktree_declared"
    else:
        source["binding_status"] = "unknown"
    return source


def _rule_ref(config_path, config_digest):
    return {
        "name": "engineering-ci.selection",
        "version": str(plan_contract.PLAN_VERSION),
        "path": str(config_path),
        "sha256": config_digest,
    }


def derive_expected_checks(
    changeset,
    check_scope,
    *,
    config_path,
    consumer=None,
    profile_ref=None,
    profile_source=None,
):
    """Derive versioned Expected Checks without claiming Profile completeness.

    ``profile_source`` is reserved for an authoritative Profile receipt. Until
    one is supplied, the set is explicitly ``partial`` and never a legal empty
    complete set.
    """
    config_digest = _file_digest(config_path)
    rule_ref = _rule_ref(config_path, config_digest)
    source_ref = _source_ref(changeset)
    checks = []
    missing = []
    for item in check_scope:
        name = item.get("name")
        definition = check_contract.definition_for(name)
        if definition is None:
            missing.append(str(name))
            checks.append(
                {
                    "expected_check_ref": None,
                    "check_id": None,
                    "definition_version": None,
                    "definition_status": "unknown",
                    "name": name,
                    "rule_ref": rule_ref,
                    "profile_ref": profile_ref,
                    "applicability": {
                        "status": item.get("status", "unknown"),
                        "reason": item.get("reason"),
                    },
                    "policy_status": "unknown",
                    "source_ref": source_ref,
                    "input_refs": [
                        {"kind": "changed_path", "path": path}
                        for path in item.get("inputs", [])
                    ],
                    "baseline_ref": (
                        {"base_commit": changeset.base_commit}
                        if changeset.base_commit
                        else None
                    ),
                }
            )
            continue
        entry = {
            "expected_check_ref": None,
            "check_id": definition.check_id,
            "definition_version": definition.definition_version,
            "definition_status": "resolved",
            "name": name,
            "rule_ref": rule_ref,
            "profile_ref": profile_ref,
            "applicability": {
                "status": item.get("status", "unknown"),
                "reason": item.get("reason"),
            },
            "policy_status": "unknown",
            "source_ref": source_ref,
            "input_refs": [
                {"kind": "changed_path", "path": path}
                for path in item.get("inputs", [])
            ],
            "baseline_ref": (
                {"base_commit": changeset.base_commit}
                if changeset.base_commit
                else None
            ),
        }
        entry["expected_check_ref"] = _digest(entry)
        checks.append(entry)

    definitions_status = (
        "unknown"
        if missing and not checks
        else "partial"
        if missing
        else "requested_names_complete"
    )
    profile_complete = bool(profile_source and profile_ref)
    status = "unknown" if not checks else "complete" if profile_complete else "partial"
    if status == "unknown":
        reason = "no_expected_checks_or_profile"
    elif status == "partial":
        reason = "consumer_profile_not_resolved"
    else:
        reason = None
    scope = {
        "consumer": consumer,
        "profile_ref": profile_ref,
        "profile_source": profile_source,
        "event_ref": {
            "base_ref": changeset.base_ref,
            "head_ref": changeset.head_ref,
            "base_commit": changeset.base_commit,
            "head_commit": changeset.head_commit,
        },
        "source_ref": source_ref,
    }
    return {
        "kind": EXPECTED_KIND,
        "schema_version": EXPECTED_VERSION,
        "status": status,
        "reason": reason,
        "scope_id": _digest(scope),
        "expected_set_ref": _digest(checks),
        "definitions_status": definitions_status,
        "applicability_status": "partial" if missing else "requested_scope",
        "consumer": consumer,
        "profile_ref": profile_ref,
        "profile_source": profile_source,
        "consumer_completeness": "not_claimed",
        "event_ref": scope["event_ref"],
        "source_ref": source_ref,
        "checks": checks,
        "missing": missing,
        "conflicts": [],
    }
