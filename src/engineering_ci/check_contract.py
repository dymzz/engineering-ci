"""Versioned Check identity and Native Result contract.

This module is the public boundary for #66 consumers. It does not execute
checks, discover tools or grant Required/trusted status. It defines stable
check identities, rule/profile references and result instances so a result can
be associated with the exact Check and source that produced it.

Tool identities (Runtime tool_id/group_id/access_id) are not Check identities.
A missing check_id is refused rather than inferred from a display name. A
declared tool requirement describes which native entries can implement a
Check; a result records the entry that actually ran. A ToolAsset ``tool_id`` is
only recorded when a verified asset identity exists, never derived from an
executable, Hook Job or display name.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType

CONTRACT_VERSION = 2
SUPPORTED_RESULT_VERSIONS = frozenset({1, 2})
CHECK_CONTRACT_KIND = "engineering-ci-check-contract"
RESULT_KIND = "engineering-ci-native-check-result"
SATISFACTION_KIND = "engineering-ci-check-satisfaction"
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
class ToolIdentity:
    """One native tool/entry that can implement (part of) a Check.

    ``access_id`` is the native entry identity ECI itself controls. ``tool_id``
    is only filled with a verified ToolAsset identity or another verified asset
    record; it is never inferred from an executable, Hook Job or display name.
    """

    access_id: str
    tool_id: str | None = None
    version: str | None = None
    artifact_sha256: str | None = None


@dataclass(frozen=True)
class ToolRequirement:
    """One required native-tool group; alternatives are allowed implementations.

    Every requirement of a Check must be satisfied. ``after`` records a real
    prerequisite between requirements: the satisfying result must reference the
    prerequisite result it consumed, so the order is evidenced rather than
    presumed.
    """

    requirement_id: str
    alternatives: tuple[ToolIdentity, ...]
    after: tuple[str, ...] = ()


@dataclass(frozen=True)
class CheckDefinition:
    check_id: str
    definition_version: int = 1
    tool_id: str | None = None
    access_id: str | None = None
    rule_ref: dict | None = None
    profile_ref: dict | None = None
    requires_baseline: bool = False
    tool_requirements: tuple[ToolRequirement, ...] = ()


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


_LOCAL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_TOOL_ACCESS_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)+$")
_TOOL_REF_FIELDS = ("access_id", "tool_id", "version", "artifact_sha256")


def validate_tool_identity(identity):
    """Validate one declared tool alternative, refusing unverifiable names."""
    if isinstance(identity, dict):
        try:
            identity = ToolIdentity(**identity)
        except TypeError:
            raise CheckContractError("tool_identity_invalid") from None
    if not isinstance(identity, ToolIdentity):
        raise CheckContractError("tool_identity_invalid")
    if not isinstance(identity.access_id, str) or not _TOOL_ACCESS_PATTERN.fullmatch(
        identity.access_id
    ):
        raise CheckContractError("tool_access_id_invalid")
    if identity.tool_id is not None and (
        not isinstance(identity.tool_id, str)
        or not _LOCAL_ID_PATTERN.fullmatch(identity.tool_id)
    ):
        raise CheckContractError("tool_id_invalid")
    if identity.version is not None:
        _text(identity.version, "tool_version")
    _sha256(identity.artifact_sha256, "tool_artifact_sha256")
    return identity


def tool_ref(
    access_id,
    *,
    tool_id=None,
    version=None,
    artifact_sha256=None,
    executable=None,
):
    """Record the native entry that actually ran; evidence, not a declaration."""
    ref = {
        "access_id": access_id,
        "tool_id": tool_id,
        "version": version,
        "artifact_sha256": artifact_sha256,
    }
    if executable is not None:
        ref["executable"] = _text(executable, "tool_executable")
    return validate_tool_ref(ref)


def validate_tool_ref(ref):
    if not isinstance(ref, dict):
        raise CheckContractError("result_tool_invalid")
    if set(ref) - {*_TOOL_REF_FIELDS, "executable"}:
        raise CheckContractError("result_tool_invalid")
    validate_tool_identity({key: ref.get(key) for key in _TOOL_REF_FIELDS})
    if ref.get("executable") is not None:
        _text(ref["executable"], "tool_executable")
    return ref


def _coerce_requirement(value):
    if isinstance(value, ToolRequirement):
        return value
    if isinstance(value, dict):
        try:
            return ToolRequirement(
                requirement_id=value["requirement_id"],
                alternatives=tuple(value["alternatives"]),
                after=tuple(value.get("after") or ()),
            )
        except (KeyError, TypeError):
            raise CheckContractError("tool_requirement_invalid") from None
    raise CheckContractError("tool_requirement_invalid")


def _ensure_acyclic(requirements):
    graph = {item.requirement_id: tuple(item.after) for item in requirements}
    state = {}

    def visit(node):
        if state.get(node) == 1:
            raise CheckContractError("tool_requirement_cycle")
        if state.get(node) == 2:
            return
        state[node] = 1
        for prerequisite in graph[node]:
            visit(prerequisite)
        state[node] = 2

    for node in graph:
        visit(node)


def validate_tool_requirements(requirements):
    """Validate the controlled requirement groups of one Check definition."""
    if isinstance(requirements, dict) or not isinstance(requirements, (list, tuple)):
        raise CheckContractError("tool_requirements_invalid")
    items = [_coerce_requirement(item) for item in requirements]
    seen_ids = []
    for item in items:
        if not isinstance(item.requirement_id, str) or not _LOCAL_ID_PATTERN.fullmatch(
            item.requirement_id
        ):
            raise CheckContractError("tool_requirement_id_invalid")
        if item.requirement_id in seen_ids:
            raise CheckContractError("tool_requirement_duplicate")
        seen_ids.append(item.requirement_id)
        if not isinstance(item.alternatives, (list, tuple)) or not item.alternatives:
            raise CheckContractError("tool_alternatives_missing")
        if not isinstance(item.after, (list, tuple)) or any(
            not isinstance(entry, str) for entry in item.after
        ):
            raise CheckContractError("tool_requirement_prerequisite_invalid")
    known = set(seen_ids)
    for item in items:
        for prerequisite in item.after:
            if prerequisite == item.requirement_id:
                raise CheckContractError("tool_requirement_self_prerequisite")
            if prerequisite not in known:
                raise CheckContractError("tool_requirement_unknown_prerequisite")
    _ensure_acyclic(items)
    seen_access = set()
    normalized = []
    for item in items:
        alternatives = []
        for alternative in item.alternatives:
            alternative = validate_tool_identity(alternative)
            if alternative.access_id in seen_access:
                raise CheckContractError("tool_alternative_duplicate")
            seen_access.add(alternative.access_id)
            alternatives.append(alternative)
        normalized.append(
            ToolRequirement(
                item.requirement_id,
                tuple(alternatives),
                tuple(item.after),
            )
        )
    return tuple(normalized)


def check_id_for(name):
    """Return the stable Check ID for a built-in display name, or None."""
    return CHECK_IDS.get(name)


def definition_for(name):
    check_id = check_id_for(name)
    return _BUILTIN_BY_ID.get(check_id) if check_id else None


# Exact old→new checks consume the old source as native input. Other checks
# may use changed-path selection, but do not intrinsically inspect old bytes.
#
# Native entries are declared here, not inferred at runtime: an alternative's
# ``access_id`` is the entry ECI itself invokes. ``tool_id`` stays empty until a
# verified ToolAsset/asset record exists for that tool (external adapters).
_TOOL_REQUIREMENTS = {
    # ``git ls-files`` determines the checked document set and feeds Lychee.
    "docs": (
        ToolRequirement("inventory", (ToolIdentity("git/ls-files"),)),
        ToolRequirement("links", (ToolIdentity("lychee/docs"),), after=("inventory",)),
    ),
    "tach": (ToolRequirement("module-graph", (ToolIdentity("tach/check"),)),),
    "tach_external": (
        ToolRequirement("external-modules", (ToolIdentity("tach/check-external"),)),
    ),
    "vulture": (ToolRequirement("dead-code", (ToolIdentity("vulture/dead-code"),)),),
    "whitespace": (ToolRequirement("diff-hygiene", (ToolIdentity("git/diff-check"),)),),
}
_BUILTIN_DEFINITIONS = tuple(
    CheckDefinition(
        check_id,
        requires_baseline=name in {"whitespace", "forwarding"},
        tool_requirements=_TOOL_REQUIREMENTS.get(name, ()),
    )
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
    # Use the same reference validation as validate_result(), including digest.
    _validate_reference(definition.rule_ref, "rule_ref")
    _validate_reference(definition.profile_ref, "profile_ref")
    return replace(
        definition,
        tool_requirements=validate_tool_requirements(definition.tool_requirements),
    )


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


def _has_resolved_base_commit(ref):
    if not isinstance(ref, dict):
        return False
    commit = ref.get("base_commit")
    return isinstance(commit, str) and bool(
        re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", commit)
    )


def _select_declared_tool(definition, tool):
    matched = [
        (requirement, alternative)
        for requirement in definition.tool_requirements
        for alternative in requirement.alternatives
        if alternative.access_id == tool["access_id"]
    ]
    if not matched:
        raise CheckContractError("result_tool_undeclared")
    for requirement, alternative in matched:
        # A ToolAsset identity is never invented: it must be declared and equal.
        if tool.get("tool_id") != alternative.tool_id:
            continue
        # A declared version/artifact is a constraint; an undeclared one may
        # be recorded as observed evidence (for example a pinned Dagger tool).
        if any(
            declared is not None and tool.get(key) != declared
            for key, declared in (
                ("version", alternative.version),
                ("artifact_sha256", alternative.artifact_sha256),
            )
        ):
            continue
        return requirement, alternative
    raise CheckContractError("result_tool_mismatch")


def _validated_result_tool(definition, tool, result_origin):
    """Check observed tool evidence against the controlled declaration.

    A tool-bound Check may omit the observation (the native fact is preserved),
    but then it cannot satisfy the Check. A tool-less Check must not claim one.
    """
    if tool is not None:
        tool = validate_tool_ref(tool)
    if result_origin == "direct_tool" and (tool is None or not tool.get("tool_id")):
        raise CheckContractError("direct_tool_identity_missing")
    if not definition.tool_requirements:
        if tool is not None:
            raise CheckContractError("result_tool_unexpected")
        return None
    if tool is None:
        return None
    _select_declared_tool(definition, tool)
    return tool


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
    tool=None,
):
    """Create one immutable Native Result.

    ``content_id`` identifies the result content excluding the run instance;
    ``result_id`` additionally binds the run instance. ``tool`` records the
    native entry that actually ran; it is not copied from the definition.
    """
    definition = validate_definition(definition)
    registered = _registered_definition(
        definition.check_id, definition.definition_version
    )
    if definition.requires_baseline != registered.requires_baseline:
        raise CheckContractError("result_check_semantics_mismatch")
    if definition.tool_requirements != registered.tool_requirements:
        raise CheckContractError("result_tool_requirements_mismatch")
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
    # The exit status of a completed process cannot become a skipped Check.
    # A successful probe may establish non-applicability with native evidence.
    if status == "not_executed" and native_exit_code is not None:
        raise CheckContractError("result_nonexecuted_exit_inconsistent")
    if status == "not_applicable" and native_exit_code not in (None, 0):
        raise CheckContractError("result_not_applicable_exit_inconsistent")
    if status == "not_applicable" and native_exit_code == 0 and not evidence:
        raise CheckContractError("result_not_applicable_evidence_missing")
    _validate_baseline(baseline_ref)
    tool = _validated_result_tool(definition, tool, result_origin)
    if (
        registered.requires_baseline
        and status in {"passed", "failed", "advisory"}
        and not _has_resolved_base_commit(baseline_ref)
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
        "tool": tool,
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
    # A public constructor must never emit a result its public validator rejects.
    validate_result(payload)
    return payload


def validate_result(result):
    """Recompute identity and reject mutated/inconsistent native results."""
    if not isinstance(result, dict) or result.get("kind") != RESULT_KIND:
        raise CheckContractError("result_invalid")
    schema_version = result.get("schema_version")
    if schema_version not in SUPPORTED_RESULT_VERSIONS:
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
    if status == "not_executed" and exit_code is not None:
        raise CheckContractError("result_nonexecuted_exit_inconsistent")
    if status == "not_applicable" and exit_code not in (None, 0):
        raise CheckContractError("result_not_applicable_exit_inconsistent")
    if status == "not_applicable" and exit_code == 0 and not evidence:
        raise CheckContractError("result_not_applicable_evidence_missing")
    _validate_baseline(result.get("baseline_ref"))
    if (
        builtin.requires_baseline
        and status in {"passed", "failed", "advisory"}
        and not _has_resolved_base_commit(result.get("baseline_ref"))
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
    if (
        result.get("result_origin") == "direct_tool"
        and schema_version == 1
        and (not check.get("tool_id") or not check.get("access_id"))
    ):
        raise CheckContractError("direct_tool_identity_missing")
    if schema_version == 1:
        if "tool" in result:
            raise CheckContractError("result_schema_tool_unsupported")
    else:
        if "tool" not in result:
            raise CheckContractError("result_tool_missing")
        _validated_result_tool(builtin, result.get("tool"), result.get("result_origin"))
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


EXPECTED_REQUIRED = ("check_id", "definition_version", "rule_ref", "source_ref")


def validate_expected_instance(expected):
    """Validate one supplied Check expectation, not a consumer-wide set.

    The shape and input claims must be sufficient for a local native match.
    Profile authenticity, complete Expected enumeration, evidence admission
    and Required Gate remain with their actual #66/#63/#61 producers.
    """
    if not isinstance(expected, dict):
        raise CheckContractError("expected_invalid")
    if any(key not in expected for key in EXPECTED_REQUIRED):
        raise CheckContractError("expected_incomplete")
    definition = _registered_definition(
        expected["check_id"], expected["definition_version"]
    )
    rule = expected.get("rule_ref")
    if not isinstance(rule, dict) or not rule.get("sha256"):
        raise CheckContractError("expected_rule_unproven")
    _validate_reference(rule, "rule_ref")
    source = expected.get("source_ref")
    validate_source_ref(source)
    if source["binding_status"] not in {"byte_tree", "content_digest"}:
        raise CheckContractError("expected_source_unproven")
    if (
        source["scope_kind"]
        not in {
            "check_input_closure",
            "full_input_closure",
        }
        or not source["verified_input_scope"]
    ):
        raise CheckContractError("expected_source_scope_insufficient")
    if "input_refs" in expected:
        refs = expected["input_refs"]
        if not isinstance(refs, list) or any(
            not isinstance(item, dict) for item in refs
        ):
            raise CheckContractError("expected_input_refs_invalid")
    if expected.get("profile_ref") is not None:
        _validate_reference(expected["profile_ref"], "profile_ref")
    baseline = expected.get("baseline_ref")
    _validate_baseline(baseline)
    if (
        definition.requires_baseline or expected.get("comparison")
    ) and not _has_resolved_base_commit(baseline):
        raise CheckContractError("expected_baseline_missing")
    return expected


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
    builtin = _registered_definition(check["check_id"], check["definition_version"])
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


def result_covers_expected(result, expected):
    """Single supplied Check's native satisfaction, NOT ExpectedSet completeness.

    The result must be valid and match a scoped supplied Expected instance.
    The caller still needs #66 to establish completeness/identity of the whole
    Expected collection and #63/#61 to decide trust and Required policy.
    """
    validate_result(result)
    validate_expected_instance(expected)
    # One result cannot stand for a Check that requires several native tools;
    # multi-tool satisfaction stays with evaluate_check_satisfaction().
    definition = _registered_definition(
        result["check"]["check_id"], result["check"]["definition_version"]
    )
    if len(definition.tool_requirements) > 1:
        return False
    if not result_matches_expected(result, expected):
        return False
    # Compare explicit input dependencies, not just the outer Source identity.
    # Extra observed inputs are allowed; missing or different declared inputs
    # cannot satisfy the supplied Check expectation.
    if "input_refs" in expected and any(
        ref not in result["input_refs"] for ref in expected["input_refs"]
    ):
        return False
    if "coverage" in expected and expected["coverage"] != result.get("coverage"):
        return False
    if result["status"] != "passed":
        return False
    kind = _execution_kind(result["check"]["check_id"])
    if kind == "process":
        return result["native_exit_code"] == 0 and bool(result.get("evidence"))
    if kind == "internal":
        return bool(result.get("evidence"))
    return False


def _requirement_order(requirements):
    graph = {item.requirement_id: tuple(item.after) for item in requirements}
    ordered = []
    state = {}

    def visit(node):
        if state.get(node) == 2:
            return
        if state.get(node) == 1:
            raise CheckContractError("tool_requirement_cycle")
        state[node] = 1
        for prerequisite in graph[node]:
            visit(prerequisite)
        state[node] = 2
        ordered.append(node)

    for node in graph:
        visit(node)
    by_id = {item.requirement_id: item for item in requirements}
    return [by_id[node] for node in ordered]


def _result_matches_requirement(requirement, result):
    tool = result.get("tool")
    if not isinstance(tool, dict):
        return False
    for alternative in requirement.alternatives:
        if alternative.access_id != tool.get("access_id"):
            continue
        if tool.get("tool_id") != alternative.tool_id:
            continue
        if any(
            declared is not None and tool.get(key) != declared
            for key, declared in (
                ("version", alternative.version),
                ("artifact_sha256", alternative.artifact_sha256),
            )
        ):
            continue
        return True
    return False


def _consumed_result_ids(result):
    refs = result.get("input_refs") or ()
    return {
        ref.get("result_id")
        for ref in refs
        if isinstance(ref, dict) and ref.get("kind") == "check_result"
    }


def evaluate_check_satisfaction(definition, results):
    """Native satisfaction of one Check from the supplied results.

    Every declared requirement must be satisfied. An alternative satisfies its
    requirement only when its own Native Result passed; a requirement that
    declares ``after`` is satisfied only when the dependent result references
    the prerequisite ``result_id`` it consumed. This is not an ExpectedSet,
    Evidence Admission or Required decision.
    """
    definition = validate_definition(definition)
    registered = _registered_definition(
        definition.check_id, definition.definition_version
    )
    if definition.tool_requirements != registered.tool_requirements:
        raise CheckContractError("result_tool_requirements_mismatch")
    if not isinstance(results, (list, tuple)):
        raise CheckContractError("satisfaction_results_invalid")
    observed = []
    for result in results:
        validate_result(result)
        check = result["check"]
        if check["check_id"] != definition.check_id:
            raise CheckContractError("satisfaction_check_mismatch")
        if check["definition_version"] != definition.definition_version:
            raise CheckContractError("satisfaction_definition_version_mismatch")
        observed.append(result)
    decision = {
        "kind": SATISFACTION_KIND,
        "schema_version": CONTRACT_VERSION,
        "check_id": definition.check_id,
        "definition_version": definition.definition_version,
        "consumer_scope": "supplied_results_only",
        "required": False,
        "trusted": False,
        "result_ids": [item["result_id"] for item in observed],
    }
    if not definition.tool_requirements:
        return {
            **decision,
            "status": "not_applicable",
            "reason": "no_tool_requirements",
            "requirements": [],
        }
    satisfied = {}
    entries = []
    for requirement in _requirement_order(definition.tool_requirements):
        candidates = [
            item for item in observed if _result_matches_requirement(requirement, item)
        ]
        passed = sorted(
            (item for item in candidates if item["status"] == "passed"),
            key=lambda item: item["result_id"],
        )
        entry = {
            "requirement_id": requirement.requirement_id,
            "after": list(requirement.after),
            "alternatives": [
                alternative.access_id for alternative in requirement.alternatives
            ],
            "observed": [item["result_id"] for item in candidates],
            "satisfied_by": None,
            "status": "unsatisfied",
            "reason": "result_missing" if not candidates else "result_not_passed",
        }
        selected = None
        for candidate in passed:
            blocker = None
            for prerequisite in requirement.after:
                prerequisite_entry = satisfied[prerequisite]
                if prerequisite_entry["status"] != "satisfied":
                    blocker = "prerequisite_unmet"
                    break
                if prerequisite_entry["satisfied_by"] not in _consumed_result_ids(
                    candidate
                ):
                    blocker = "prerequisite_unevidenced"
                    break
            if blocker is None:
                selected = candidate
                break
            entry["reason"] = blocker
        if selected is not None:
            entry.update(
                status="satisfied",
                reason="native_pass",
                satisfied_by=selected["result_id"],
            )
        satisfied[requirement.requirement_id] = entry
        entries.append(entry)
    unmet = [item for item in entries if item["status"] != "satisfied"]
    return {
        **decision,
        "status": "unsatisfied" if unmet else "satisfied",
        "reason": unmet[0]["reason"] if unmet else "all_requirements_satisfied",
        "requirements": entries,
    }


def _definition_payload(definition):
    return {
        "check_id": definition.check_id,
        "definition_version": definition.definition_version,
        "tool_id": definition.tool_id,
        "access_id": definition.access_id,
        "rule_ref": definition.rule_ref,
        "profile_ref": definition.profile_ref,
        "requires_baseline": definition.requires_baseline,
        "tool_requirements": [
            {
                "requirement_id": item.requirement_id,
                "alternatives": [
                    {
                        "access_id": alternative.access_id,
                        "tool_id": alternative.tool_id,
                        "version": alternative.version,
                        "artifact_sha256": alternative.artifact_sha256,
                    }
                    for alternative in item.alternatives
                ],
                "after": list(item.after),
            }
            for item in definition.tool_requirements
        ],
    }


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
            definitions.append(_definition_payload(definition))
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
