"""Versioned Check identity and Native Result contract.

This module is the public boundary for #66 consumers. It does not execute
checks, discover tools or grant Required/trusted status. It defines stable
check identities, rule/profile references and result instances so a result can
be associated with the exact Check and source that produced it.

Tool identities (Runtime tool_id/group_id/access_id) are not Check identities.
A missing check_id is refused rather than inferred from a display name.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

CONTRACT_VERSION = 1
CHECK_CONTRACT_KIND = "engineering-ci-check-contract"
RESULT_KIND = "engineering-ci-native-check-result"
CHECK_ID_PATTERN = re.compile(r"^eci/[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)+$")
STATUSES = frozenset(
    {"passed", "failed", "advisory", "not_applicable", "not_executed", "unknown"}
)
ORIGINS = frozenset({"host", "dagger", "actions", "direct_tool", "unknown"})

# Built-in identity table for the checks currently produced by this package.
# This is not a runtime registry: consumers cannot register arbitrary names, and
# an unknown name is refused rather than used as an identity.
CHECK_IDS = MappingProxyType(
    {
        "whitespace": "eci/hygiene/whitespace",
        "forwarding": "eci/hygiene/forwarding",
        "docs": "eci/hygiene/docs",
        "vulture": "eci/hygiene/vulture",
        "tach": "eci/hygiene/tach",
        "tach_external": "eci/hygiene/tach-external",
        "source": "eci/sources/reconcile",
    }
)


class CheckContractError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CheckDefinition:
    check_id: str
    definition_version: int = 1
    tool_id: str | None = None
    access_id: str | None = None
    rule_ref: dict | None = None
    profile_ref: dict | None = None


def _text(value, name):
    if not isinstance(value, str) or not value:
        raise CheckContractError(f"{name}_invalid")
    return value


def _sha256(value, name):
    if value is not None and (
        not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
    ):
        raise CheckContractError(f"{name}_invalid")
    return value


def check_id_for(name):
    """Return the stable Check ID for a built-in display name, or None."""
    return CHECK_IDS.get(name)


def definition_for(name):
    check_id = check_id_for(name)
    return CheckDefinition(check_id) if check_id else None


def _version(value, name):
    if type(value) is int and value >= 1:
        return str(value)
    return _text(value, name)


def rule_ref(name, version, *, path=None, digest=None):
    """A versioned consumer rule reference; not a Required/trust decision."""
    ref = {
        "name": _text(name, "rule_name"),
        "version": _version(version, "rule_version"),
    }
    if path is not None:
        ref["path"] = _text(path, "rule_path")
    if digest is not None:
        ref["sha256"] = _sha256(digest, "rule_sha256")
    return ref


def profile_ref(name, version):
    return {
        "name": _text(name, "profile_name"),
        "version": _version(version, "profile_version"),
    }


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_definition(definition):
    if isinstance(definition, dict):
        definition = CheckDefinition(**definition)
    if not isinstance(definition, CheckDefinition):
        raise CheckContractError("check_definition_invalid")
    if not CHECK_ID_PATTERN.fullmatch(definition.check_id):
        raise CheckContractError("check_id_invalid")
    if (
        type(definition.definition_version) is not int
        or definition.definition_version < 1
    ):
        raise CheckContractError("check_version_invalid")
    for value in (definition.tool_id, definition.access_id):
        if value is not None:
            _text(value, "tool_identity")
    for ref in (definition.rule_ref, definition.profile_ref):
        if ref is not None:
            if not isinstance(ref, dict):
                raise CheckContractError("check_reference_invalid")
            _text(ref.get("name"), "reference_name")
            _version(ref.get("version"), "reference_version")
    return definition


def source_ref(
    snapshot,
    *,
    tree=None,
    head_commit=None,
    index_tree=None,
    content_digest=None,
):
    """Describe the checked source identity; binding status is explicit."""
    result = {"snapshot": _text(snapshot, "snapshot")}
    for key, value in (
        ("tree", tree),
        ("head_commit", head_commit),
        ("index_tree", index_tree),
        ("content_digest", content_digest),
    ):
        if value is not None:
            result[key] = _text(value, key)
    if "tree" in result or "content_digest" in result:
        result["binding_status"] = "byte_tree" if "tree" in result else "content_digest"
    elif "head_commit" in result or "index_tree" in result:
        result["binding_status"] = "identity_only"
    else:
        result["binding_status"] = "unknown"
    return result


def native_result(
    definition,
    *,
    source,
    status,
    result_origin,
    result_instance,
    native_exit_code=None,
    input_refs=(),
    artifact_refs=(),
    coverage=None,
    rule_ref=None,
    profile_ref=None,
    reason=None,
    runner_ref=None,
):
    """Create one immutable Native Result with a deterministic result_id."""
    definition = validate_definition(definition)
    if status not in STATUSES:
        raise CheckContractError("result_status_invalid")
    if result_origin not in ORIGINS:
        raise CheckContractError("result_origin_invalid")
    _text(result_instance, "result_instance")
    if not isinstance(source, dict) or not source:
        raise CheckContractError("source_ref_invalid")
    _text(source.get("snapshot"), "snapshot")
    for key in ("tree", "head_commit", "index_tree", "content_digest"):
        if source.get(key) is not None:
            _text(source[key], key)
    if source.get("binding_status") not in {
        "byte_tree",
        "content_digest",
        "identity_only",
        "unknown",
    }:
        raise CheckContractError("source_binding_invalid")
    if not isinstance(input_refs, (list, tuple)) or any(
        not isinstance(item, dict) for item in input_refs
    ):
        raise CheckContractError("input_refs_invalid")
    if not isinstance(artifact_refs, (list, tuple)) or any(
        not isinstance(item, str) for item in artifact_refs
    ):
        raise CheckContractError("artifact_refs_invalid")
    if native_exit_code is not None and type(native_exit_code) is not int:
        raise CheckContractError("native_exit_code_invalid")
    payload = {
        "kind": RESULT_KIND,
        "schema_version": CONTRACT_VERSION,
        "check": {
            "check_id": definition.check_id,
            "definition_version": definition.definition_version,
            "tool_id": definition.tool_id,
            "access_id": definition.access_id,
            "rule_ref": rule_ref if rule_ref is not None else definition.rule_ref,
            "profile_ref": profile_ref
            if profile_ref is not None
            else definition.profile_ref,
        },
        "source_ref": source,
        "input_refs": list(input_refs),
        "status": status,
        "native_exit_code": native_exit_code,
        "result_origin": result_origin,
        "runner_ref": runner_ref,
        "result_instance": result_instance,
        "artifact_refs": list(artifact_refs),
        "coverage": coverage,
        "reason": reason,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["result_id"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
    return payload


def validate_result(result):
    if not isinstance(result, dict) or result.get("kind") != RESULT_KIND:
        raise CheckContractError("result_invalid")
    if result.get("schema_version") != CONTRACT_VERSION:
        raise CheckContractError("result_schema_unsupported")
    check = result.get("check")
    if not isinstance(check, dict):
        raise CheckContractError("result_check_invalid")
    check_id = check.get("check_id")
    if not isinstance(check_id, str) or not CHECK_ID_PATTERN.fullmatch(check_id):
        raise CheckContractError("result_check_id_invalid")
    if type(check.get("definition_version")) is not int:
        raise CheckContractError("result_check_version_invalid")
    status = result.get("status")
    if status not in STATUSES:
        raise CheckContractError("result_status_invalid")
    if not isinstance(result.get("source_ref"), dict):
        raise CheckContractError("result_source_invalid")
    if not isinstance(result.get("result_id"), str):
        raise CheckContractError("result_id_invalid")
    return result


def validate_result_set(results):
    """Reject duplicate result IDs and ambiguous Check definitions."""
    if not isinstance(results, (list, tuple)):
        raise CheckContractError("result_set_invalid")
    seen = set()
    definitions = {}
    for result in results:
        validate_result(result)
        result_id = result["result_id"]
        if result_id in seen:
            raise CheckContractError("result_id_duplicate")
        seen.add(result_id)
        check = result["check"]
        prior = definitions.setdefault(
            check["check_id"], (check["definition_version"], check.get("rule_ref"))
        )
        if prior != (check["definition_version"], check.get("rule_ref")):
            raise CheckContractError("check_definition_conflict")
    return True
