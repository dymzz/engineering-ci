"""Versioned Expected Check projection for #66-B.

Schema v1 (``projection_source="check_scope"``) derives a partial projection
from local Selection/CheckScope inputs and intentionally never claims
Profile completeness.

Schema v2 (``projection_source="effective_profile"``) accepts an authoritative
``EffectiveProfile``, preserves M:N Obligation <-> Check mappings and non-check
obligations, detects local Hygiene-vs-Profile conflicts without silent drop,
and evaluates expected-enumeration completeness independently of post-execution
observed input closure.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from . import check_contract, hygiene

EXPECTED_VERSION = 1
EXPECTED_V2 = 2
EXPECTED_VERSIONS = frozenset({EXPECTED_VERSION, EXPECTED_V2})
EXPECTED_KIND = "engineering-ci-expected-checks"
SELECTION_RULE_VERSION = 1
STATUSES = frozenset({"complete", "partial", "unknown"})
PROJECTION_SOURCES = frozenset({"check_scope", "effective_profile"})
OBSERVED_CLOSURE_STATUSES = frozenset({"not_observed", "observed", "complete"})
_HYGIENE_MANAGED = frozenset(
    {"whitespace", "forwarding", "docs", "vulture", "tach", "tach_external"}
)


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


def _canon_key(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _event_ref(changeset):
    references = sorted(
        (dict(item) for item in changeset.references),
        key=_canon_key,
    )
    event = {
        "base_ref": changeset.base_ref,
        "head_ref": changeset.head_ref,
        "base_commit": changeset.base_commit,
        "head_commit": changeset.head_commit,
        "references": references,
    }
    if getattr(changeset, "ref_action", None) is not None:
        event["ref_action"] = changeset.ref_action
    if getattr(changeset, "object_type", None) is not None:
        event["object_type"] = changeset.object_type
    if getattr(changeset, "force_push", False):
        event["force_push"] = True
    if getattr(changeset, "baseline_status", None) is not None:
        event["baseline_status"] = changeset.baseline_status
    return event


def _source_ref(changeset, content_binding=None):
    declared = changeset.execution_tree or changeset.tree or changeset.index_tree
    source = {
        "snapshot": changeset.snapshot,
        "declared_target_tree": declared,
        "actual_execution_source": "unknown",
        "verified_input_scope": sorted(changeset.paths),
        "scope_kind": "changed_paths",
        "binding_status": "unknown",
    }
    if changeset.index_tree:
        source["index_tree"] = changeset.index_tree
    if changeset.head_commit:
        source["head_commit"] = changeset.head_commit
    if (
        isinstance(content_binding, dict)
        and content_binding.get("digest")
        and source["verified_input_scope"]
    ):
        source["content_digest"] = content_binding["digest"]
        source["binding_status"] = "content_digest"
    elif getattr(changeset, "ref_action", None) == "delete":
        source["binding_status"] = "unknown"
    elif changeset.snapshot != "worktree" and declared:
        source["binding_status"] = "identity_only"
    return source


def _selection_rule_ref(config_digest):
    return {
        "name": "engineering-ci.selection",
        "version": str(SELECTION_RULE_VERSION),
        "sha256": config_digest,
    }


def _baseline_ref_for_changes(changeset):
    ref_action = getattr(changeset, "ref_action", None)
    baseline_status = getattr(changeset, "baseline_status", None)
    if ref_action in {"create", "delete", "force_update"} or baseline_status in {
        "policy",
        "not_applicable",
        "old_object_unavailable",
        "unknown",
    }:
        return {
            "base_commit": changeset.base_commit,
            "head_commit": changeset.head_commit,
            "baseline_status": baseline_status,
            "ref_action": ref_action,
        }
    return {"base_commit": changeset.base_commit} if changeset.base_commit else None


def entry_identity(entry):
    payload = {
        key: value for key, value in entry.items() if key != "expected_check_ref"
    }
    return _digest(payload)


def scope_identity(
    consumer,
    profile_ref,
    profile_source,
    event_ref,
    source_ref,
    selection_rule_ref=None,
    hygiene_rule_ref=None,
    sources_rule_ref=None,
):
    payload = {
        "consumer": consumer,
        "profile_ref": profile_ref,
        "profile_source": profile_source,
        "event_ref": event_ref,
        "source_ref": source_ref,
        "selection_rule_ref": selection_rule_ref,
    }
    if hygiene_rule_ref is not None:
        payload["hygiene_rule_ref"] = hygiene_rule_ref
    if sources_rule_ref is not None:
        payload["sources_rule_ref"] = sources_rule_ref
    return _digest(payload)


def validate_effective_profile(profile):
    """Validate an authoritative EffectiveProfile payload for Schema v2."""
    if not isinstance(profile, dict):
        raise ExpectedError("effective_profile_invalid")
    profile_ref = profile.get("profile_ref")
    if not isinstance(profile_ref, dict) or not profile_ref:
        raise ExpectedError("effective_profile_ref_invalid")
    for field in ("profile_revision", "approval_source", "effective_at"):
        value = profile.get(field)
        if not isinstance(value, str) or not value:
            raise ExpectedError("effective_profile_authority_missing")
    for field in ("context", "action", "target"):
        value = profile.get(field)
        if not isinstance(value, str) or not value:
            raise ExpectedError("effective_profile_scope_missing")
    required = profile.get("required_obligations", [])
    advisory = profile.get("advisory_checks", [])
    if not isinstance(required, list) or not isinstance(advisory, list):
        raise ExpectedError("effective_profile_obligations_invalid")
    for item in required:
        if not isinstance(item, dict):
            raise ExpectedError("effective_profile_obligations_invalid")
        ob_ref = item.get("obligation_ref")
        if not (isinstance(ob_ref, str) and ob_ref) and not (
            isinstance(ob_ref, dict) and ob_ref
        ):
            raise ExpectedError("effective_profile_obligation_ref_invalid")
        kind = item.get("kind", "check")
        if kind not in {"check", "non_check"}:
            raise ExpectedError("effective_profile_obligation_kind_invalid")
        if kind == "check":
            names = item.get("check_names")
            if (
                not isinstance(names, list)
                or not names
                or any(not isinstance(n, str) or not n for n in names)
            ):
                raise ExpectedError("effective_profile_check_names_invalid")
    return profile


def _derive_per_ref_projections(
    changeset,
    *,
    scope_by_name,
    selection_rule,
    hygiene_rule,
    sources_rule,
    consumer,
    profile_ref,
    profile_source,
    hygiene_config,
    code_prefixes,
):
    if changeset.snapshot != "multi" or not changeset.references:
        return None
    check_names = [
        item.get("name")
        for item in scope_by_name.values()
        if item.get("name") is not None
    ]
    projections = []
    for ref in changeset.references:
        raw_before = ref.get("raw_before") or ref.get("before") or ""
        raw_after = ref.get("raw_after") or ref.get("after") or ""
        ref_action = ref.get("ref_action")
        object_type = ref.get("object_type")
        force_push = bool(ref.get("force_push", False))
        baseline_status = ref.get("baseline_status")
        ref_complete = bool(ref.get("complete", False))
        ref_paths = tuple(sorted(ref.get("paths") or ()))
        ref_deleted = tuple(sorted(ref.get("deleted_paths") or ()))
        source_label = (
            ref.get("source_ref")
            or ref.get("remote_ref")
            or ref.get("local_ref")
            or str(raw_after or "")
        )
        baseline_obj = {
            "base_commit": ref.get("before"),
            "head_commit": ref.get("after"),
            "raw_before": ref.get("raw_before"),
            "raw_after": ref.get("raw_after"),
            "baseline_status": baseline_status,
            "baseline_ref": ref.get("baseline_ref"),
        }
        ref_event = {
            "base_ref": str(raw_before),
            "head_ref": str(source_label),
            "base_commit": ref.get("before"),
            "head_commit": ref.get("after"),
            "local_ref": ref.get("local_ref"),
            "remote_ref": ref.get("remote_ref"),
            "ref_action": ref_action,
            "object_type": object_type,
            "force_push": force_push,
            "baseline_status": baseline_status,
            "references": [dict(ref)],
        }
        ref_source = {
            "snapshot": "push",
            "declared_target_tree": ref.get("tree"),
            "actual_execution_source": "unknown",
            "verified_input_scope": list(ref_paths),
            "scope_kind": "changed_paths",
            "binding_status": (
                "unknown"
                if ref_action == "delete"
                else "identity_only"
                if ref.get("tree")
                else "unknown"
            ),
            "source_ref": source_label,
        }
        if ref.get("after"):
            ref_source["head_commit"] = ref["after"]
        pseudo_changes = SimpleNamespace(
            paths=ref_paths,
            deleted_paths=ref_deleted,
            complete=ref_complete,
            snapshot="push",
            tree=ref.get("tree"),
            execution_tree=ref.get("tree"),
            ref_action=ref_action,
        )
        hygiene_app = (
            hygiene.check_applicability(
                pseudo_changes,
                hygiene_config,
                root=changeset.root,
                full=False,
                code_prefixes=code_prefixes,
            )
            if hygiene_config is not None
            else {}
        )
        ref_checks = []
        for name in check_names:
            definition = check_contract.definition_for(name)
            if name in hygiene_app:
                app_status = hygiene_app[name]["status"]
                app_reason = hygiene_app[name].get("reason")
                app_inputs = list(hygiene_app[name].get("inputs", ()))
            elif ref_action == "delete":
                app_status = "not_applicable"
                app_reason = "remote_ref_deleted"
                app_inputs = []
            elif not ref_complete:
                app_status = "unknown"
                app_reason = ref.get("error") or "ref_incomplete"
                app_inputs = []
            else:
                origin = scope_by_name.get(name, {})
                app_status = origin.get("status", "unknown")
                app_reason = origin.get("reason")
                app_inputs = [p for p in origin.get("inputs", ()) if p in ref_paths]
            entry = {
                "name": name,
                "check_id": definition.check_id if definition else None,
                "definition_version": (
                    definition.definition_version if definition else None
                ),
                "definition_status": "resolved" if definition else "unknown",
                "event_ref": json.loads(json.dumps(ref_event)),
                "selection_rule_ref": selection_rule,
                "rule_ref": None,
                "rule_status": "unknown",
                "profile_ref": profile_ref,
                "applicability": {"status": app_status, "reason": app_reason},
                "policy_status": "unknown",
                "source_ref": json.loads(json.dumps(ref_source)),
                "input_refs": [{"kind": "changed_path", "path": p} for p in app_inputs],
                "baseline_ref": json.loads(json.dumps(baseline_obj)),
            }
            entry["expected_check_ref"] = entry_identity(entry)
            ref_checks.append(entry)
        non_check = [
            {
                "obligation_ref": ob,
                "kind": ob,
                "status": "required",
                "source_ref": source_label,
                "reason": "remote_ref_deleted" if ob == "ref_deletion" else ob,
            }
            for ob in (ref.get("non_check_obligations") or ())
        ]
        projections.append(
            {
                "source_ref": source_label,
                "local_ref": ref.get("local_ref"),
                "remote_ref": ref.get("remote_ref"),
                "ref_action": ref_action,
                "object_type": object_type,
                "force_push": force_push,
                "baseline_status": baseline_status,
                "baseline_ref": baseline_obj,
                "event_ref": ref_event,
                "source": ref_source,
                "scope_id": scope_identity(
                    consumer,
                    profile_ref,
                    profile_source,
                    ref_event,
                    ref_source,
                    selection_rule,
                    hygiene_rule,
                    sources_rule,
                ),
                "expected_set_ref": _digest(ref_checks),
                "checks": ref_checks,
                "non_check_obligations": non_check,
                "status": (
                    "unknown"
                    if not ref_complete
                    else "partial"
                    if ref_checks
                    else "unknown"
                ),
            }
        )
    return projections


def _derive_v2_from_profile(
    changeset,
    check_scope,
    *,
    effective_profile,
    selection_rule,
    hygiene_config,
    hygiene_identity,
    sources_identity,
    consumer,
    content_binding,
    per_ref,
):
    validate_effective_profile(effective_profile)
    profile_ref = effective_profile["profile_ref"]
    profile_source = effective_profile.get("approval_source")
    event_ref = _event_ref(changeset)
    source_ref = _source_ref(changeset, content_binding)
    hygiene_rule = (hygiene_identity or {}).get("effective_check_config_ref")
    sources_rule = (sources_identity or {}).get("effective_check_config_ref")
    scope_by_name = {
        str(item.get("name")): item for item in check_scope if item.get("name")
    }
    hygiene_enabled = (
        frozenset(hygiene.effective_checks(hygiene_config))
        if hygiene_config is not None
        else frozenset(scope_by_name)
    )

    obligations_by_check = {}
    required_by_check = {}
    advisory_by_check = {}
    rule_by_check = {}
    allow_inapp_by_check = set(effective_profile.get("allowed_inapplicability") or ())
    non_check_obligations = []

    for ob in effective_profile.get("required_obligations", []):
        ob_ref = ob["obligation_ref"]
        kind = ob.get("kind", "check")
        if kind == "non_check":
            non_check_obligations.append(
                {
                    "obligation_ref": ob_ref,
                    "kind": ob.get("obligation_kind", "non_check"),
                    "status": "required",
                    "reason": ob.get("reason") or "required_by_effective_profile",
                }
            )
            continue
        for name in ob["check_names"]:
            obligations_by_check.setdefault(name, []).append(
                {"obligation_ref": ob_ref, "role": "required"}
            )
            required_by_check.setdefault(name, []).append(ob_ref)
            if ob.get("rule_ref") is not None:
                rule_by_check[name] = ob["rule_ref"]
            if ob.get("allow_inapplicable"):
                allow_inapp_by_check.add(name)

    for adv in effective_profile.get("advisory_checks", []):
        if isinstance(adv, str):
            name, ob_ref, adv_rule = adv, None, None
        else:
            name = adv.get("name")
            ob_ref = adv.get("obligation_ref")
            adv_rule = adv.get("rule_ref")
        if not name:
            continue
        if ob_ref is not None:
            obligations_by_check.setdefault(name, []).append(
                {"obligation_ref": ob_ref, "role": "advisory"}
            )
            advisory_by_check.setdefault(name, []).append(ob_ref)
        else:
            obligations_by_check.setdefault(name, [])
        if adv_rule is not None and name not in rule_by_check:
            rule_by_check[name] = adv_rule

    for ob_name in getattr(changeset, "non_check_obligations", ()):
        non_check_obligations.append(
            {
                "obligation_ref": f"eci/{ob_name}",
                "kind": ob_name,
                "status": "required",
                "reason": "remote_ref_deleted"
                if ob_name == "ref_deletion"
                else ob_name,
            }
        )

    declared_checks = effective_profile.get("declared_checks")
    if declared_checks is not None:
        ordered_names = list(declared_checks)
        for name, item in scope_by_name.items():
            if (
                item.get("status") not in {"not_applicable", "no_tests", "no_changes"}
                and name not in ordered_names
            ):
                ordered_names.append(name)
    else:
        ordered_names = list(scope_by_name)
    for name in list(required_by_check) + list(advisory_by_check):
        if name not in ordered_names:
            ordered_names.append(name)

    checks = []
    missing = []
    conflicts = []
    unresolved_applicability = False
    is_delete = getattr(changeset, "ref_action", None) == "delete"
    for name in ordered_names:
        if (
            is_delete
            and name in scope_by_name
            and name not in required_by_check
            and name not in advisory_by_check
        ):
            continue
        definition = check_contract.definition_for(name)
        if definition is None:
            missing.append(str(name))
        scope_item = scope_by_name.get(name)
        req_obs = sorted(required_by_check.get(name, []), key=_canon_key)
        adv_obs = sorted(advisory_by_check.get(name, []), key=_canon_key)
        disabled_by_hygiene = (
            bool(req_obs)
            and hygiene_config is not None
            and name in _HYGIENE_MANAGED
            and name not in hygiene_enabled
        )
        if disabled_by_hygiene:
            conflicts.append(
                {
                    "kind": "hygiene_profile_conflict",
                    "check": name,
                    "obligation_refs": req_obs,
                    "reason": "required_by_effective_profile_disabled_by_hygiene_config",
                }
            )
            app_status = "config_conflict"
            app_reason = "disabled_by_local_hygiene_config"
            app_inputs = []
        elif scope_item is not None:
            app_status = scope_item.get("status", "unknown")
            app_reason = scope_item.get("reason")
            app_inputs = list(scope_item.get("inputs", ()))
        elif is_delete:
            app_status = "not_applicable"
            app_reason = "remote_ref_deleted"
            app_inputs = []
        else:
            app_status = "unknown"
            app_reason = "check_scope_missing"
            app_inputs = []
            unresolved_applicability = True

        if app_status == "unknown":
            unresolved_applicability = True
        if (
            app_status == "not_applicable"
            and req_obs
            and name not in allow_inapp_by_check
        ):
            conflicts.append(
                {
                    "kind": "inapplicable_required_obligation",
                    "check": name,
                    "obligation_refs": req_obs,
                    "reason": app_reason or "not_applicable_without_profile_waiver",
                }
            )

        rule_ref = rule_by_check.get(name)
        if rule_ref is None and name in _HYGIENE_MANAGED and hygiene_rule is not None:
            rule_ref = hygiene_rule
        elif rule_ref is None and name == "source" and sources_rule is not None:
            rule_ref = sources_rule
        rule_status = "resolved" if rule_ref is not None else "unknown"

        all_obs = sorted(
            {
                _canon_key(item): item for item in obligations_by_check.get(name, [])
            }.values(),
            key=_canon_key,
        )
        policy_status = (
            "conflict"
            if disabled_by_hygiene
            else "resolved"
            if (req_obs or adv_obs)
            else "advisory_unmapped"
        )
        entry = {
            "name": name,
            "check_id": definition.check_id if definition else None,
            "definition_version": definition.definition_version if definition else None,
            "definition_status": "resolved" if definition else "unknown",
            "event_ref": json.loads(json.dumps(event_ref)),
            "selection_rule_ref": selection_rule,
            "rule_ref": rule_ref,
            "rule_status": rule_status,
            "profile_ref": profile_ref,
            "obligation_refs": all_obs,
            "required_by_obligations": req_obs,
            "advisory_for_obligations": adv_obs,
            "applicability": {"status": app_status, "reason": app_reason},
            "policy_status": policy_status,
            "source_ref": json.loads(json.dumps(source_ref)),
            "input_refs": [
                {"kind": "changed_path", "path": path} for path in app_inputs
            ],
            "baseline_ref": _baseline_ref_for_changes(changeset),
        }
        entry["expected_check_ref"] = entry_identity(entry)
        checks.append(entry)

    domain_complete = bool(effective_profile.get("domain_complete", False))
    if conflicts:
        status = "partial"
        reason = "profile_hygiene_conflict"
        consumer_completeness = "incomplete_conflict"
    elif unresolved_applicability or (
        not getattr(changeset, "complete", True) and not is_delete
    ):
        status = "unknown"
        reason = "applicability_or_changeset_incomplete"
        consumer_completeness = "unknown"
    elif not domain_complete or missing:
        status = "partial"
        reason = "expected_domain_or_definitions_incomplete"
        consumer_completeness = "incomplete_profile"
    else:
        status = "complete"
        reason = "domain_complete_empty" if not checks else "expected_domain_complete"
        consumer_completeness = "expected_domain_complete"

    definitions_status = (
        "partial"
        if missing
        else "domain_complete"
        if domain_complete
        else "requested_names_complete"
    )
    applicability_status = (
        "unknown"
        if unresolved_applicability
        else "domain_complete"
        if domain_complete and not conflicts
        else "partial"
    )
    projection = {
        "kind": EXPECTED_KIND,
        "schema_version": EXPECTED_V2,
        "projection_source": "effective_profile",
        "status": status,
        "reason": reason,
        "scope_id": scope_identity(
            consumer,
            profile_ref,
            profile_source,
            event_ref,
            source_ref,
            selection_rule,
            hygiene_rule,
            sources_rule,
        ),
        "selection_rule_ref": selection_rule,
        "hygiene_rule_ref": hygiene_rule,
        "sources_rule_ref": sources_rule,
        "expected_set_ref": _digest(checks),
        "definitions_status": definitions_status,
        "applicability_status": applicability_status,
        "consumer": consumer,
        "profile_ref": profile_ref,
        "profile_source": profile_source,
        "effective_profile": json.loads(json.dumps(effective_profile)),
        "consumer_completeness": consumer_completeness,
        "observed_input_closure_status": "not_observed",
        "event_ref": event_ref,
        "source_ref": source_ref,
        "checks": checks,
        "non_check_obligations": non_check_obligations,
        "missing": missing,
        "conflicts": conflicts,
    }
    if per_ref is not None:
        projection["per_ref"] = per_ref
    return projection


def derive_expected_checks(
    changeset,
    check_scope,
    *,
    config_path,
    consumer=None,
    profile_ref=None,
    profile_source=None,
    content_binding=None,
    hygiene_config=None,
    hygiene_identity=None,
    sources_identity=None,
    effective_profile=None,
    code_prefixes=None,
):
    """Derive Expected Checks projection (Schema v1 partial, or Schema v2 with EffectiveProfile)."""
    config_digest = _file_digest(config_path)
    selection_rule = _selection_rule_ref(config_digest)
    hygiene_rule = (hygiene_identity or {}).get("effective_check_config_ref")
    sources_rule = (sources_identity or {}).get("effective_check_config_ref")
    scope_by_name = {item.get("name"): item for item in check_scope}
    per_ref = _derive_per_ref_projections(
        changeset,
        scope_by_name=scope_by_name,
        selection_rule=selection_rule,
        hygiene_rule=hygiene_rule,
        sources_rule=sources_rule,
        consumer=consumer,
        profile_ref=profile_ref or (effective_profile or {}).get("profile_ref"),
        profile_source=profile_source
        or (effective_profile or {}).get("approval_source"),
        hygiene_config=hygiene_config,
        code_prefixes=code_prefixes,
    )
    if effective_profile is not None:
        return _derive_v2_from_profile(
            changeset,
            check_scope,
            effective_profile=effective_profile,
            selection_rule=selection_rule,
            hygiene_config=hygiene_config,
            hygiene_identity=hygiene_identity,
            sources_identity=sources_identity,
            consumer=consumer,
            content_binding=content_binding,
            per_ref=per_ref,
        )

    event_ref = _event_ref(changeset)
    source_ref = _source_ref(changeset, content_binding)
    baseline = _baseline_ref_for_changes(changeset)
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
            "baseline_ref": json.loads(json.dumps(baseline))
            if baseline is not None
            else None,
        }
        entry["expected_check_ref"] = entry_identity(entry)
        checks.append(entry)

    non_check = [
        {
            "obligation_ref": f"eci/{ob}",
            "kind": ob,
            "status": "required",
            "reason": "remote_ref_deleted" if ob == "ref_deletion" else ob,
        }
        for ob in getattr(changeset, "non_check_obligations", ())
    ]
    if not checks:
        status = "unknown"
        reason = "no_expected_checks"
        definitions_status = "unknown"
    else:
        status = "partial"
        reason = "consumer_profile_not_resolved"
        definitions_status = "partial" if missing else "requested_names_complete"
    projection = {
        "kind": EXPECTED_KIND,
        "schema_version": EXPECTED_VERSION,
        "projection_source": "check_scope",
        "status": status,
        "reason": reason,
        "scope_id": scope_identity(
            consumer,
            profile_ref,
            profile_source,
            event_ref,
            source_ref,
            selection_rule,
        ),
        "selection_rule_ref": selection_rule,
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
    if non_check:
        projection["non_check_obligations"] = non_check
    if per_ref is not None:
        projection["per_ref"] = per_ref
    return projection


def _validate_per_ref(expected, git):
    refs = git.get("references") or []
    per_ref = expected.get("per_ref")
    if git.get("snapshot") == "multi" or per_ref is not None:
        if not isinstance(per_ref, list) or len(per_ref) != len(refs):
            raise ExpectedError("expected_per_ref_mismatch")
        for plan_ref, proj_ref in zip(refs, per_ref, strict=True):
            if not isinstance(proj_ref, dict):
                raise ExpectedError("expected_per_ref_invalid")
            ref_checks = proj_ref.get("checks")
            if not isinstance(ref_checks, list):
                raise ExpectedError("expected_per_ref_invalid")
            for item in ref_checks:
                if not isinstance(item, dict):
                    raise ExpectedError("expected_check_invalid")
                if "job_refs" in item or "job_ref" in item:
                    raise ExpectedError("expected_routing_forbidden")
                if item.get("expected_check_ref") != entry_identity(item):
                    raise ExpectedError("expected_check_ref_mismatch")
                baseline = item.get("baseline_ref")
                expected_baseline = {
                    "base_commit": plan_ref.get("before"),
                    "head_commit": plan_ref.get("after"),
                    "raw_before": plan_ref.get("raw_before"),
                    "raw_after": plan_ref.get("raw_after"),
                    "baseline_status": plan_ref.get("baseline_status"),
                    "baseline_ref": plan_ref.get("baseline_ref"),
                }
                if baseline != expected_baseline:
                    raise ExpectedError("expected_baseline_mismatch")
            if proj_ref.get("expected_set_ref") != _digest(ref_checks):
                raise ExpectedError("expected_set_ref_mismatch")


def validate_projection(expected, git, check_scope=None):
    """Recompute identities and cross-check the projection against Plan git."""
    if not isinstance(expected, dict):
        raise ExpectedError("expected_checks_invalid")
    schema_version = expected.get("schema_version", EXPECTED_VERSION)
    if schema_version not in EXPECTED_VERSIONS:
        raise ExpectedError("expected_checks_schema_unsupported")
    projection_source = expected.get("projection_source")
    if projection_source not in PROJECTION_SOURCES:
        raise ExpectedError("expected_projection_source_unsupported")
    if schema_version == EXPECTED_VERSION and projection_source != "check_scope":
        raise ExpectedError("expected_projection_source_unsupported")
    if schema_version == EXPECTED_V2 and projection_source != "effective_profile":
        raise ExpectedError("expected_projection_source_unsupported")

    checks = expected.get("checks")
    if not isinstance(checks, list):
        raise ExpectedError("expected_checks_invalid")
    if projection_source == "check_scope":
        if check_scope is None:
            raise ExpectedError("expected_check_scope_missing")
        scope_names = [str(item.get("name")) for item in check_scope]
        expected_names = [str(item.get("name")) for item in checks]
        if len(scope_names) != len(set(scope_names)) or sorted(scope_names) != sorted(
            expected_names
        ):
            raise ExpectedError("expected_scope_mismatch")
    else:
        validate_effective_profile(expected.get("effective_profile"))

    for item in checks:
        if not isinstance(item, dict):
            raise ExpectedError("expected_check_invalid")
        if "job_refs" in item or "job_ref" in item:
            raise ExpectedError("expected_routing_forbidden")
        if item.get("expected_check_ref") != entry_identity(item):
            raise ExpectedError("expected_check_ref_mismatch")
        if schema_version == EXPECTED_V2 and not isinstance(
            item.get("obligation_refs"), list
        ):
            raise ExpectedError("expected_obligation_refs_invalid")
    if expected.get("expected_set_ref") != _digest(checks):
        raise ExpectedError("expected_set_ref_mismatch")

    source = expected.get("source_ref")
    try:
        check_contract.validate_source_ref(source)
    except check_contract.CheckContractError as error:
        raise ExpectedError(error.code) from None
    if git.get("snapshot") is not None and source.get("snapshot") != git["snapshot"]:
        raise ExpectedError("expected_source_mismatch")
    snapshot = git.get("snapshot")
    declared = source.get("declared_target_tree")
    if snapshot == "index":
        target = git.get("execution_tree") or git.get("index_tree")
        if not target or declared != target:
            raise ExpectedError("expected_source_mismatch")
    elif snapshot in {"commit", "push"}:
        target = git.get("execution_tree") or git.get("tree")
        if target and declared != target:
            raise ExpectedError("expected_source_mismatch")
    elif snapshot == "pr_merge":
        target = git.get("merge_tree") or git.get("execution_tree")
        if target and declared != target:
            raise ExpectedError("expected_source_mismatch")
    elif snapshot == "worktree":
        target = git.get("tree")
        if target and declared != target:
            raise ExpectedError("expected_source_mismatch")
    content = git.get("content_binding")
    if (
        isinstance(content, dict)
        and content.get("digest")
        and source.get("content_digest") != content["digest"]
    ):
        raise ExpectedError("expected_content_binding_mismatch")
    changed = sorted(git.get("changed_paths") or [])
    scope_paths = sorted(source.get("verified_input_scope") or [])
    if scope_paths != changed:
        raise ExpectedError("expected_source_scope_mismatch")
    if isinstance(content, dict) and isinstance(content.get("paths"), dict):
        binding_paths = sorted(content["paths"])
        if binding_paths != scope_paths:
            raise ExpectedError("expected_source_scope_mismatch")

    _validate_per_ref(expected, git)
    if snapshot != "multi":
        ref_action = git.get("ref_action")
        baseline_status = git.get("baseline_status")
        if ref_action in {"create", "delete", "force_update"} or baseline_status in {
            "policy",
            "not_applicable",
            "old_object_unavailable",
            "unknown",
        }:
            expected_baseline = {
                "base_commit": git.get("base_commit"),
                "head_commit": git.get("head_commit"),
                "baseline_status": baseline_status,
                "ref_action": ref_action,
            }
        else:
            expected_baseline = (
                {"base_commit": git["base_commit"]} if git.get("base_commit") else None
            )
        for item in checks:
            if item.get("baseline_ref") != expected_baseline:
                raise ExpectedError("expected_baseline_mismatch")

    if projection_source == "check_scope" and check_scope is not None:
        scope_by_name = {item.get("name"): item for item in check_scope}
        for item in checks:
            origin = scope_by_name.get(item.get("name"))
            if origin is None:
                raise ExpectedError("expected_scope_mismatch")
            if item.get("applicability", {}).get("status") != origin.get("status"):
                raise ExpectedError("expected_applicability_mismatch")
            if item.get("applicability", {}).get("reason") != origin.get("reason"):
                raise ExpectedError("expected_applicability_mismatch")
            expected_inputs = [
                ref.get("path")
                for ref in item.get("input_refs", [])
                if ref.get("kind") == "changed_path"
            ]
            if expected_inputs != list(origin.get("inputs", [])):
                raise ExpectedError("expected_input_mismatch")

    event = expected.get("event_ref")
    if not isinstance(event, dict):
        raise ExpectedError("expected_event_invalid")
    for key in ("base_ref", "head_ref", "base_commit", "head_commit"):
        if event.get(key) != git.get(key):
            raise ExpectedError("expected_event_mismatch")
    expected_refs = sorted(
        (dict(item) for item in event.get("references", [])),
        key=_canon_key,
    )
    plan_refs = sorted(
        (dict(item) for item in (git.get("references") or [])),
        key=_canon_key,
    )
    if expected_refs != plan_refs:
        raise ExpectedError("expected_event_mismatch")
    for item in checks:
        if item.get("event_ref") != event:
            raise ExpectedError("expected_entry_event_mismatch")
        if item.get("source_ref") != source:
            raise ExpectedError("expected_entry_source_mismatch")
        if item.get("profile_ref") != expected.get("profile_ref"):
            raise ExpectedError("expected_entry_profile_mismatch")
        if item.get("selection_rule_ref") != expected.get("selection_rule_ref"):
            raise ExpectedError("expected_entry_rule_mismatch")
    expected_scope = scope_identity(
        expected.get("consumer"),
        expected.get("profile_ref"),
        expected.get("profile_source"),
        expected.get("event_ref"),
        expected.get("source_ref"),
        expected.get("selection_rule_ref"),
        expected.get("hygiene_rule_ref"),
        expected.get("sources_rule_ref"),
    )
    if expected.get("scope_id") != expected_scope:
        raise ExpectedError("expected_scope_id_mismatch")
    return expected
