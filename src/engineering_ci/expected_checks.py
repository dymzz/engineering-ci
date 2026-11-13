"""Versioned Expected Check projection for #66-B.

Expected Checks are derived from the existing Selection/Plan inputs and the
published Check identity contract. This first slice intentionally never claims
a complete Consumer Profile: without authoritative Profile/rules sources the
projection is ``partial`` or ``unknown``. Required policy and Job routing are
outside this module.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import check_contract

EXPECTED_VERSION = 1
EXPECTED_KIND = "engineering-ci-expected-checks"
SELECTION_RULE_VERSION = 1
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


def _event_ref(changeset):
    references = sorted(
        (dict(item) for item in changeset.references),
        key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
    )
    return {
        "base_ref": changeset.base_ref,
        "head_ref": changeset.head_ref,
        "base_commit": changeset.base_commit,
        "head_commit": changeset.head_commit,
        "references": references,
    }


def _source_ref(changeset):
    declared = changeset.execution_tree or changeset.tree or changeset.index_tree
    if changeset.snapshot == "worktree":
        status = "unknown"
    elif declared:
        status = "identity_only"
    else:
        status = "unknown"
    source = {
        "snapshot": changeset.snapshot,
        "declared_target_tree": declared,
        "actual_execution_source": "worktree",
        "verified_input_scope": sorted(changeset.paths),
        "scope_kind": "changed_paths",
        "binding_status": status,
    }
    if changeset.index_tree:
        source["index_tree"] = changeset.index_tree
    if changeset.head_commit:
        source["head_commit"] = changeset.head_commit
    return source


def _selection_rule_ref(config_digest):
    return {
        "name": "engineering-ci.selection",
        "version": str(SELECTION_RULE_VERSION),
        "sha256": config_digest,
    }


def entry_identity(entry):
    payload = {
        key: value for key, value in entry.items() if key != "expected_check_ref"
    }
    return _digest(payload)


def scope_identity(consumer, profile_ref, profile_source, event_ref, source_ref):
    return _digest(
        {
            "consumer": consumer,
            "profile_ref": profile_ref,
            "profile_source": profile_source,
            "event_ref": event_ref,
            "source_ref": source_ref,
        }
    )


def derive_expected_checks(
    changeset,
    check_scope,
    *,
    config_path,
    consumer=None,
    profile_ref=None,
    profile_source=None,
):
    """Derive a partial Expected projection; never claims Profile completeness."""
    config_digest = _file_digest(config_path)
    selection_rule = _selection_rule_ref(config_digest)
    event_ref = _event_ref(changeset)
    source_ref = _source_ref(changeset)
    checks = []
    missing = []
    for item in check_scope:
        name = item.get("name")
        definition = check_contract.definition_for(name)
        if definition is None:
            missing.append(str(name))
        entry = {
            "name": name,
            "check_id": definition.check_id if definition else None,
            "definition_version": definition.definition_version if definition else None,
            "definition_status": "resolved" if definition else "unknown",
            "event_ref": json.loads(json.dumps(event_ref)),
            "selection_rule_ref": selection_rule,
            "rule_ref": None,
            "rule_status": "unknown",
            "profile_ref": profile_ref,
            "applicability": {
                "status": item.get("status", "unknown"),
                "reason": item.get("reason"),
            },
            "policy_status": "unknown",
            "source_ref": json.loads(json.dumps(source_ref)),
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
        entry["expected_check_ref"] = entry_identity(entry)
        checks.append(entry)

    if not checks:
        status = "unknown"
        reason = "no_expected_checks"
        definitions_status = "unknown"
    else:
        status = "partial"
        reason = "consumer_profile_not_resolved"
        definitions_status = "partial" if missing else "requested_names_complete"
    return {
        "kind": EXPECTED_KIND,
        "schema_version": EXPECTED_VERSION,
        "status": status,
        "reason": reason,
        "scope_id": scope_identity(
            consumer, profile_ref, profile_source, event_ref, source_ref
        ),
        "expected_set_ref": _digest(checks),
        "definitions_status": definitions_status,
        "applicability_status": "partial" if missing else "requested_scope",
        "consumer": consumer,
        "profile_ref": profile_ref,
        "profile_source": profile_source,
        "consumer_completeness": "not_claimed",
        "event_ref": event_ref,
        "source_ref": source_ref,
        "checks": checks,
        "missing": missing,
        "conflicts": [],
    }


def validate_projection(expected, git):
    """Recompute identities and cross-check the projection against Plan git."""
    if not isinstance(expected, dict):
        raise ExpectedError("expected_checks_invalid")
    checks = expected.get("checks")
    if not isinstance(checks, list):
        raise ExpectedError("expected_checks_invalid")
    for item in checks:
        if not isinstance(item, dict):
            raise ExpectedError("expected_check_invalid")
        if "job_refs" in item or "job_ref" in item:
            raise ExpectedError("expected_routing_forbidden")
        if item.get("expected_check_ref") != entry_identity(item):
            raise ExpectedError("expected_check_ref_mismatch")
    if expected.get("expected_set_ref") != _digest(checks):
        raise ExpectedError("expected_set_ref_mismatch")

    source = expected.get("source_ref")
    try:
        check_contract.validate_source_ref(source)
    except check_contract.CheckContractError as error:
        raise ExpectedError(error.code) from None
    if git.get("snapshot") is not None and source.get("snapshot") != git["snapshot"]:
        raise ExpectedError("expected_source_mismatch")
    plan_trees = {
        value
        for value in (
            git.get("execution_tree"),
            git.get("tree"),
            git.get("index_tree"),
        )
        if value
    }
    declared = source.get("declared_target_tree")
    if declared and plan_trees and declared not in plan_trees:
        raise ExpectedError("expected_source_mismatch")
    event = expected.get("event_ref")
    if not isinstance(event, dict):
        raise ExpectedError("expected_event_invalid")
    for key in ("base_ref", "head_ref", "base_commit", "head_commit"):
        if event.get(key) != git.get(key):
            raise ExpectedError("expected_event_mismatch")
    expected_scope = scope_identity(
        expected.get("consumer"),
        expected.get("profile_ref"),
        expected.get("profile_source"),
        expected.get("event_ref"),
        expected.get("source_ref"),
    )
    if expected.get("scope_id") != expected_scope:
        raise ExpectedError("expected_scope_id_mismatch")
    return expected
