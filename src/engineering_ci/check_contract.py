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
PROCESS_CHECK_IDS = frozenset(
    {
        "eci/hygiene/whitespace",
        "eci/hygiene/docs",
        "eci/hygiene/vulture",
        "eci/hygiene/tach",
        "eci/hygiene/tach-external",
    }
)
INTERNAL_CHECK_IDS = frozenset({"eci/hygiene/forwarding", "eci/sources/reconcile"})


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
    requires_baseline: bool = False


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
    return _BUILTIN_BY_ID.get(check_id) if check_id else None


# Exact old→new checks consume the old source as native input. Other checks
# may use changed-path selection, but do not intrinsically inspect old bytes.
_BUILTIN_DEFINITIONS = tuple(
    CheckDefinition(check_id, requires_baseline=name in {"whitespace", "forwarding"})
    for name, check_id in CHECK_IDS.items()
)
_BUILTIN_BY_ID = MappingProxyType(
    {definition.check_id: definition for definition in _BUILTIN_DEFINITIONS}
)


def _registered_definition(check_id, version):
    definition = _BUILTIN_BY_ID.get(check_id)
    if definition is None:
        raise CheckContractError("result_check_unknown")
    if version != definition.definition_version:
        raise CheckContractError("result_check_version_mismatch")
    return definition


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
    if type(definition.requires_baseline) is not bool:
        raise CheckContractError("check_baseline_requirement_invalid")
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
    declared_target_tree=None,
    actual_execution_source=None,
    head_commit=None,
    index_tree=None,
    content_digest=None,
    verified_scope=(),
    binding_status=None,
    scope_kind="changed_paths",
    materialization_ref=None,
):
    """Describe checked source levels without inferring byte binding.

    ``declared_target_tree`` is what the plan intended; it is not proof a tool
    read those bytes. ``byte_tree`` requires an explicit verified scope from a
    materializer; a plain Tree field is never enough.
    """
    result = {"snapshot": _text(snapshot, "snapshot")}
    if declared_target_tree is not None:
        result["declared_target_tree"] = _text(
            declared_target_tree, "declared_target_tree"
        )
    if actual_execution_source is not None:
        result["actual_execution_source"] = _text(
            actual_execution_source, "actual_execution_source"
        )
    for key, value in (
        ("head_commit", head_commit),
        ("index_tree", index_tree),
        ("content_digest", content_digest),
    ):
        if value is not None:
            result[key] = _text(value, key)
    scope = sorted({_text(item, "verified_scope") for item in verified_scope})
    result["verified_input_scope"] = scope
    if scope_kind not in {
        "changed_paths",
        "check_input_closure",
        "full_input_closure",
    }:
        raise CheckContractError("source_scope_kind_invalid")
    result["scope_kind"] = scope_kind
    if materialization_ref is not None:
        result["materialization_ref"] = _text(
            materialization_ref, "materialization_ref"
        )
    if binding_status is None:
        if content_digest is not None and scope:
            binding_status = "content_digest"
        elif declared_target_tree or head_commit or index_tree:
            binding_status = "identity_only"
        else:
            binding_status = "unknown"
    if not isinstance(binding_status, str) or binding_status not in {
        "byte_tree",
        "content_digest",
        "identity_only",
        "unknown",
    }:
        raise CheckContractError("source_binding_invalid")
    if binding_status == "byte_tree" and not (
        declared_target_tree
        and scope
        and scope_kind == "full_input_closure"
        and materialization_ref
    ):
        raise CheckContractError("source_byte_binding_unproven")
    if binding_status == "content_digest" and not (content_digest and scope):
        raise CheckContractError("source_content_binding_unproven")
    result["binding_status"] = binding_status
    return result


def validate_source_ref(source):
    if not isinstance(source, dict):
        raise CheckContractError("source_ref_invalid")
    _text(source.get("snapshot"), "snapshot")
    for key in (
        "declared_target_tree",
        "actual_execution_source",
        "head_commit",
        "index_tree",
        "content_digest",
    ):
        if source.get(key) is not None:
            _text(source[key], key)
    scope = source.get("verified_input_scope")
    if not isinstance(scope, list) or any(
        not isinstance(item, str) or not item for item in scope
    ):
        raise CheckContractError("source_scope_invalid")
    scope_kind = source.get("scope_kind")
    if scope_kind not in {
        "changed_paths",
        "check_input_closure",
        "full_input_closure",
    }:
        raise CheckContractError("source_scope_kind_invalid")
    if source.get("materialization_ref") is not None:
        _text(source["materialization_ref"], "materialization_ref")
    status = source.get("binding_status")
    if status not in {"byte_tree", "content_digest", "identity_only", "unknown"}:
        raise CheckContractError("source_binding_invalid")
    if status == "byte_tree" and not (
        source.get("declared_target_tree")
        and scope
        and scope_kind == "full_input_closure"
        and source.get("materialization_ref")
    ):
        raise CheckContractError("source_byte_binding_unproven")
    if status == "content_digest" and not (source.get("content_digest") and scope):
        raise CheckContractError("source_content_binding_unproven")
    return source


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _digest(value):
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _validate_reference(ref, name):
    if ref is None:
        return
    if not isinstance(ref, dict):
        raise CheckContractError(f"{name}_invalid")
    _text(ref.get("name"), f"{name}_name")
    _version(ref.get("version"), f"{name}_version")
    if ref.get("sha256") is not None:
        _sha256(ref["sha256"], f"{name}_sha256")


def _execution_kind(check_id):
    if check_id in PROCESS_CHECK_IDS:
        return "process"
    if check_id in INTERNAL_CHECK_IDS:
        return "internal"
    return "unknown"


def _validate_baseline(ref):
    if ref is None:
        return
    if not isinstance(ref, dict):
        raise CheckContractError("baseline_ref_invalid")
    present = [
        key
        for key in ("base_commit", "base_ref", "base_tree", "digest")
        if ref.get(key) is not None
    ]
    if not present:
        raise CheckContractError("baseline_ref_invalid")
    for key in present:
        _text(ref[key], key)


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
    evidence=None,
    reason=None,
    runner_ref=None,
    baseline_ref=None,
):
    """Create one immutable Native Result.

    ``content_id`` identifies the result content excluding the run instance;
    ``result_id`` additionally binds the run instance.
    """
    definition = validate_definition(definition)
    registered = _registered_definition(
        definition.check_id, definition.definition_version
    )
    if definition.requires_baseline != registered.requires_baseline:
        raise CheckContractError("result_check_semantics_mismatch")
    if status not in STATUSES:
        raise CheckContractError("result_status_invalid")
    if result_origin not in ORIGINS:
        raise CheckContractError("result_origin_invalid")
    _text(result_instance, "result_instance")
    validate_source_ref(source)
    _validate_reference(rule_ref, "rule_ref")
    _validate_reference(profile_ref, "profile_ref")
    if not isinstance(input_refs, (list, tuple)) or any(
        not isinstance(item, dict) for item in input_refs
    ):
        raise CheckContractError("input_refs_invalid")
    if not isinstance(artifact_refs, (list, tuple)) or any(
        not isinstance(item, str) for item in artifact_refs
    ):
        raise CheckContractError("artifact_refs_invalid")
    if evidence is not None and not isinstance(evidence, dict):
        raise CheckContractError("evidence_invalid")
    if native_exit_code is not None and type(native_exit_code) is not int:
        raise CheckContractError("native_exit_code_invalid")
    if status == "passed" and native_exit_code not in (None, 0):
        raise CheckContractError("result_exit_inconsistent")
    if status == "failed" and native_exit_code == 0:
        raise CheckContractError("result_exit_inconsistent")
    _validate_baseline(baseline_ref)
    if (
        registered.requires_baseline
        and status in {"passed", "failed", "advisory"}
        and baseline_ref is None
    ):
        raise CheckContractError("result_baseline_missing")
    kind = _execution_kind(definition.check_id)
    if status in {"passed", "failed"}:
        if kind == "process":
            if native_exit_code is None:
                raise CheckContractError("result_native_exit_missing")
            if not evidence:
                raise CheckContractError("result_evidence_missing")
        elif kind == "internal" and not evidence:
            raise CheckContractError("result_evidence_missing")
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
        "evidence": evidence,
        "baseline_ref": baseline_ref,
        "reason": reason,
    }
    content = {key: value for key, value in payload.items() if key != "result_instance"}
    payload["content_id"] = _digest(content)
    payload["result_id"] = _digest(payload)
    return payload


def validate_result(result):
    """Recompute identity and reject mutated/inconsistent native results."""
    if not isinstance(result, dict) or result.get("kind") != RESULT_KIND:
        raise CheckContractError("result_invalid")
    if result.get("schema_version") != CONTRACT_VERSION:
        raise CheckContractError("result_schema_unsupported")
    check = result.get("check")
    if not isinstance(check, dict):
        raise CheckContractError("result_check_invalid")
    check_id = check.get("check_id")
    if check_id not in set(CHECK_IDS.values()):
        raise CheckContractError("result_check_unknown")
    version = check.get("definition_version")
    if type(version) is not int or version < 1:
        raise CheckContractError("result_check_version_invalid")
    builtin = _registered_definition(check_id, version)
    _validate_reference(check.get("rule_ref"), "rule_ref")
    _validate_reference(check.get("profile_ref"), "profile_ref")
    status = result.get("status")
    if status not in STATUSES:
        raise CheckContractError("result_status_invalid")
    if result.get("result_origin") not in ORIGINS:
        raise CheckContractError("result_origin_invalid")
    _text(result.get("result_instance"), "result_instance")
    validate_source_ref(result.get("source_ref"))
    if not isinstance(result.get("input_refs"), list) or any(
        not isinstance(item, dict) for item in result["input_refs"]
    ):
        raise CheckContractError("input_refs_invalid")
    if not isinstance(result.get("artifact_refs"), list) or any(
        not isinstance(item, str) for item in result["artifact_refs"]
    ):
        raise CheckContractError("artifact_refs_invalid")
    evidence = result.get("evidence")
    if evidence is not None and not isinstance(evidence, dict):
        raise CheckContractError("evidence_invalid")
    exit_code = result.get("native_exit_code")
    if exit_code is not None and type(exit_code) is not int:
        raise CheckContractError("native_exit_code_invalid")
    if status == "passed" and exit_code not in (None, 0):
        raise CheckContractError("result_exit_inconsistent")
    if status == "failed" and exit_code == 0:
        raise CheckContractError("result_exit_inconsistent")
    _validate_baseline(result.get("baseline_ref"))
    if (
        builtin.requires_baseline
        and status in {"passed", "failed", "advisory"}
        and result.get("baseline_ref") is None
    ):
        raise CheckContractError("result_baseline_missing")
    kind = _execution_kind(check_id)
    if status in {"passed", "failed"}:
        if kind == "process":
            if exit_code is None:
                raise CheckContractError("result_native_exit_missing")
            if not evidence:
                raise CheckContractError("result_evidence_missing")
        elif kind == "internal" and not evidence:
            raise CheckContractError("result_evidence_missing")
    if result.get("result_origin") == "direct_tool" and (
        not check.get("tool_id") or not check.get("access_id")
    ):
        raise CheckContractError("direct_tool_identity_missing")
    content_id = result.get("content_id")
    result_id = result.get("result_id")
    if not isinstance(content_id, str) or not content_id.startswith("sha256:"):
        raise CheckContractError("content_id_invalid")
    if not isinstance(result_id, str) or not result_id.startswith("sha256:"):
        raise CheckContractError("result_id_invalid")
    payload = {
        key: value
        for key, value in result.items()
        if key not in {"content_id", "result_id"}
    }
    content = {key: value for key, value in payload.items() if key != "result_instance"}
    if _digest(content) != content_id:
        raise CheckContractError("content_id_mismatch")
    expected = dict(payload)
    expected["content_id"] = content_id
    if _digest(expected) != result_id:
        raise CheckContractError("result_id_mismatch")
    return result


def validate_result_set(results):
    """Validate each Result; distinct Ref/rule/profile instances may coexist."""
    if not isinstance(results, (list, tuple)):
        raise CheckContractError("result_set_invalid")
    seen = set()
    for result in results:
        validate_result(result)
        if result["result_id"] in seen:
            raise CheckContractError("result_id_duplicate")
        seen.add(result["result_id"])
    return True


def result_matches_expected(result, expected):
    """Candidate identity matching only, not Check coverage or Required PASS.

    For a check that natively reads old→new inputs, an assertion lacking the
    actual comparison baseline is too ambiguous even for safe association.
    The #66 Expected/Assessment consumer owns complete scope and satisfaction.
    """
    validate_result(result)
    if not isinstance(expected, dict):
        raise CheckContractError("expected_invalid")
    check = result["check"]
    builtin = _registered_definition(
        check["check_id"], check["definition_version"]
    )
    if builtin.requires_baseline and expected.get("baseline_ref") is None:
        return False
    comparisons = {
        "check_id": check["check_id"],
        "definition_version": check["definition_version"],
        "rule_ref": check.get("rule_ref"),
        "profile_ref": check.get("profile_ref"),
        "source_ref": result["source_ref"],
        "baseline_ref": result.get("baseline_ref"),
    }
    for key, actual in comparisons.items():
        if key in expected and expected[key] != actual:
            return False
    return True


def resolve_definitions(required=None):
    """Resolve an explicit request list; never claim a consumer-wide set."""
    if required is None:
        return {
            "kind": "engineering-ci-check-definition-set",
            "schema_version": CONTRACT_VERSION,
            "source": "check_contract.builtin.v1",
            "scope": "requested_names",
            "consumer_completeness": "not_claimed",
            "status": "unknown",
            "reason": "required_scope_missing",
            "definitions": [],
            "missing": [],
            "conflicts": [],
        }
    required = list(required)
    definitions = []
    missing = []
    seen = set()
    for item in required:
        definition = None
        if item in CHECK_IDS:
            definition = definition_for(item)
        else:
            definition = next(
                (value for value in _BUILTIN_DEFINITIONS if value.check_id == item),
                None,
            )
        if definition is None:
            missing.append(str(item))
            continue
        if definition.check_id not in seen:
            definitions.append(
                {
                    "check_id": definition.check_id,
                    "definition_version": definition.definition_version,
                    "tool_id": definition.tool_id,
                    "access_id": definition.access_id,
                    "rule_ref": definition.rule_ref,
                    "profile_ref": definition.profile_ref,
                    "requires_baseline": definition.requires_baseline,
                }
            )
            seen.add(definition.check_id)
    if not required:
        status, reason = "unknown", "empty_request_not_proof"
    elif missing and definitions:
        status, reason = "partial", "required_definitions_missing"
    elif missing:
        status, reason = "unknown", "required_definitions_missing"
    else:
        status, reason = "complete", "requested_names_complete"
    return {
        "kind": "engineering-ci-check-definition-set",
        "schema_version": CONTRACT_VERSION,
        "source": "check_contract.builtin.v1",
        "scope": "requested_names",
        "consumer_completeness": "not_claimed",
        "status": status,
        "reason": reason,
        "definitions": definitions,
        "missing": missing,
        "conflicts": [],
    }
