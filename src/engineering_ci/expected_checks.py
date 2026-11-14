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
import re
import tomllib
from datetime import datetime
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
AUTHORITATIVE_APPROVAL_SOURCES = frozenset(
    {"org-governance", "project-governance", "governance-signed"}
)
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")
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


def set_identity(checks, non_check_obligations=(), *, schema_version=EXPECTED_VERSION):
    non_check = list(non_check_obligations or ())
    if schema_version == EXPECTED_VERSION and not non_check:
        return _digest(checks)
    return _digest({"checks": checks, "non_check_obligations": non_check})


def _parse_iso_datetime(value):
    if not isinstance(value, str) or not value:
        raise ExpectedError("effective_profile_time_invalid")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise ExpectedError("effective_profile_time_invalid") from None
    if parsed.tzinfo is None:
        raise ExpectedError("effective_profile_time_invalid")
    return parsed


def _approval_digest(profile):
    """Canonical digest over the governance-approved definition and domain fields."""
    payload = {
        "profile_ref": profile.get("profile_ref"),
        "profile_revision": profile.get("profile_revision"),
        "approval_source": profile.get("approval_source"),
        "effective_at": profile.get("effective_at"),
        "context": profile.get("context"),
        "action": profile.get("action"),
        "target": profile.get("target"),
        "required_obligations": profile.get("required_obligations", []),
        "advisory_checks": profile.get("advisory_checks", []),
        "allowed_inapplicability": profile.get("allowed_inapplicability", []),
        "domain_complete": bool(profile.get("domain_complete", False)),
        "authority_verified": bool(profile.get("authority_verified", False)),
    }
    for optional in ("valid_from", "valid_until", "declared_checks", "targets"):
        if optional in profile:
            payload[optional] = profile[optional]
    return _digest(payload)


def profile_digest(profile):
    """Canonical digest over all EffectiveProfile authority, completeness, and evaluation fields."""
    payload = {
        "profile_ref": profile.get("profile_ref"),
        "profile_revision": profile.get("profile_revision"),
        "approval_source": profile.get("approval_source"),
        "effective_at": profile.get("effective_at"),
        "evaluated_at": profile.get("evaluated_at"),
        "context": profile.get("context"),
        "action": profile.get("action"),
        "target": profile.get("target"),
        "required_obligations": profile.get("required_obligations", []),
        "advisory_checks": profile.get("advisory_checks", []),
        "allowed_inapplicability": profile.get("allowed_inapplicability", []),
        "domain_complete": bool(profile.get("domain_complete", False)),
        "authority_verified": bool(profile.get("authority_verified", False)),
    }
    for optional in (
        "valid_from",
        "valid_until",
        "declared_checks",
        "targets",
        "approval_binding",
    ):
        if optional in profile:
            payload[optional] = profile[optional]
    return _digest(payload)


def build_effective_profile(
    *,
    profile_ref,
    profile_revision,
    approval_source,
    effective_at,
    evaluated_at,
    context,
    action,
    target,
    targets=None,
    valid_from=None,
    valid_until=None,
    required_obligations=(),
    advisory_checks=(),
    allowed_inapplicability=(),
    declared_checks=None,
    domain_complete=False,
    authority_verified=True,
):
    """Construct an EffectiveProfile carrying verifiable authority binding and digest."""
    profile = {
        "profile_ref": dict(profile_ref),
        "profile_revision": profile_revision,
        "approval_source": approval_source,
        "authority_verified": bool(authority_verified),
        "effective_at": effective_at,
        "evaluated_at": evaluated_at,
        "context": context,
        "action": action,
        "target": target,
        "required_obligations": list(required_obligations),
        "advisory_checks": list(advisory_checks),
        "allowed_inapplicability": list(allowed_inapplicability),
        "domain_complete": bool(domain_complete),
    }
    if targets is not None:
        profile["targets"] = list(targets)
    if valid_from is not None:
        profile["valid_from"] = valid_from
    if valid_until is not None:
        profile["valid_until"] = valid_until
    if declared_checks is not None:
        profile["declared_checks"] = list(declared_checks)
    profile["approval_binding"] = {
        "approval_source": approval_source,
        "profile_revision": profile_revision,
        "approval_digest": _approval_digest(profile),
    }
    profile["profile_sha256"] = profile_digest(profile)
    return profile


def load_effective_profile(path, *, git=None):
    """Load and validate an authoritative EffectiveProfile from JSON or TOML."""
    resolved = Path(path)
    try:
        raw = resolved.read_bytes()
    except OSError:
        raise ExpectedError("effective_profile_missing") from None
    try:
        text = raw.decode("utf-8")
        if resolved.suffix.lower() == ".toml":
            loaded = tomllib.loads(text)
        else:
            loaded = json.loads(text)
    except (ValueError, UnicodeError):
        raise ExpectedError("effective_profile_invalid") from None
    return validate_effective_profile(loaded, git=git)


def effective_profile_identity(path=None, profile=None, *, root=None):
    """Bind an EffectiveProfile and optional on-disk source into Plan inputs."""
    raw_sha = None
    stored_path = None
    if path is not None:
        resolved = Path(path).resolve()
        try:
            raw_sha = hashlib.sha256(resolved.read_bytes()).hexdigest()
        except OSError:
            raise ExpectedError("effective_profile_missing") from None
        stored_path = str(resolved)
        if root is not None:
            base = Path(root).resolve()
            if resolved.is_relative_to(base):
                stored_path = resolved.relative_to(base).as_posix()
        if profile is None:
            profile = load_effective_profile(resolved)
    if not isinstance(profile, dict):
        raise ExpectedError("effective_profile_invalid")
    validate_effective_profile(profile)
    identity = {
        "effective_profile_ref": {
            "name": profile["profile_ref"]["name"],
            "version": profile["profile_ref"]["version"],
            "sha256": profile["profile_sha256"],
        },
        "profile_sha256": profile["profile_sha256"],
        "approval_digest": profile["approval_binding"]["approval_digest"],
        "profile_ref": dict(profile["profile_ref"]),
        "profile_revision": profile["profile_revision"],
        "approval_source": profile["approval_source"],
        "effective_at": profile["effective_at"],
        "evaluated_at": profile["evaluated_at"],
        "context": profile["context"],
        "action": profile["action"],
        "target": profile["target"],
        "domain_complete": bool(profile.get("domain_complete", False)),
        "authority_verified": bool(profile.get("authority_verified", False)),
    }
    if "declared_checks" in profile:
        identity["declared_checks"] = list(profile["declared_checks"])
    if "targets" in profile:
        identity["targets"] = list(profile["targets"])
    if stored_path is not None:
        identity["path"] = stored_path
        identity["sha256"] = raw_sha
    return identity


def scope_identity(
    consumer,
    profile_ref,
    profile_source,
    event_ref,
    source_ref,
    selection_rule_ref=None,
    hygiene_rule_ref=None,
    sources_rule_ref=None,
    profile_sha256=None,
    evaluated_at=None,
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
    if profile_sha256 is not None:
        payload["profile_sha256"] = profile_sha256
    if evaluated_at is not None:
        payload["evaluated_at"] = evaluated_at
    return _digest(payload)


def _git_from_changeset(changeset):
    return {
        "snapshot": changeset.snapshot,
        "base_ref": changeset.base_ref,
        "head_ref": changeset.head_ref,
        "base_commit": changeset.base_commit,
        "head_commit": changeset.head_commit,
        "ref_action": getattr(changeset, "ref_action", None),
        "references": list(changeset.references),
        "non_check_obligations": list(getattr(changeset, "non_check_obligations", ())),
    }


def _match_profile_event(profile, git):
    snapshot = git.get("snapshot") or "worktree"
    refs = git.get("references") or []
    if snapshot in {"push", "multi"}:
        expected_context = "push"
        if snapshot == "push":
            expected_actions = {git.get("ref_action") or "update"}
            expected_targets = {
                t
                for t in (
                    (refs[0].get("remote_ref") if refs else None)
                    or git.get("head_ref"),
                )
                if t
            }
        else:
            ref_actions = {r.get("ref_action") for r in refs if r.get("ref_action")}
            expected_actions = ref_actions if len(ref_actions) == 1 else {"multi"}
            expected_targets = {
                r.get("remote_ref") for r in refs if r.get("remote_ref")
            }
    elif snapshot == "pr_merge":
        expected_context = "pr"
        expected_actions = {"merge"}
        expected_targets = {t for t in (git.get("base_ref"), git.get("head_ref")) if t}
    else:
        expected_context = snapshot
        expected_actions = {snapshot}
        expected_targets = {git.get("head_ref") or snapshot}
    if profile.get("context") != expected_context:
        raise ExpectedError("effective_profile_event_mismatch")
    if profile.get("action") not in expected_actions:
        raise ExpectedError("effective_profile_event_mismatch")
    declared_targets = profile.get("targets")
    if declared_targets is not None:
        if not isinstance(declared_targets, list) or any(
            not isinstance(t, str) or not t for t in declared_targets
        ):
            raise ExpectedError("effective_profile_scope_missing")
        allowed_targets = set(declared_targets)
    else:
        allowed_targets = {profile.get("target")}
    if not expected_targets or not (expected_targets <= allowed_targets):
        raise ExpectedError("effective_profile_event_mismatch")


def validate_effective_profile(profile, *, git=None):
    """Validate an authoritative EffectiveProfile payload for Schema v2."""
    if not isinstance(profile, dict):
        raise ExpectedError("effective_profile_invalid")
    profile_ref = profile.get("profile_ref")
    if (
        not isinstance(profile_ref, dict)
        or not isinstance(profile_ref.get("name"), str)
        or not profile_ref.get("name")
        or not isinstance(profile_ref.get("version"), str)
        or not profile_ref.get("version")
    ):
        raise ExpectedError("effective_profile_ref_invalid")
    for field in ("profile_revision", "approval_source", "effective_at"):
        value = profile.get(field)
        if not isinstance(value, str) or not value:
            raise ExpectedError("effective_profile_authority_missing")
    for field in ("context", "action", "target"):
        value = profile.get(field)
        if not isinstance(value, str) or not value:
            raise ExpectedError("effective_profile_scope_missing")
    if (
        profile.get("approval_source") not in AUTHORITATIVE_APPROVAL_SOURCES
        or profile.get("authority_verified") is not True
    ):
        raise ExpectedError("effective_profile_authority_untrusted")
    binding = profile.get("approval_binding")
    if (
        not isinstance(binding, dict)
        or binding.get("approval_source") != profile.get("approval_source")
        or binding.get("profile_revision") != profile.get("profile_revision")
    ):
        raise ExpectedError("effective_profile_authority_untrusted")
    if binding.get("approval_digest") != _approval_digest(profile):
        raise ExpectedError("effective_profile_digest_mismatch")
    digest = profile.get("profile_sha256")
    if (
        not isinstance(digest, str)
        or not _SHA256_RE.fullmatch(digest)
        or digest != profile_digest(profile)
    ):
        raise ExpectedError("effective_profile_digest_mismatch")

    effective_dt = _parse_iso_datetime(profile["effective_at"])
    valid_from_dt = (
        _parse_iso_datetime(profile["valid_from"])
        if profile.get("valid_from") is not None
        else effective_dt
    )
    valid_until_dt = (
        _parse_iso_datetime(profile["valid_until"])
        if profile.get("valid_until") is not None
        else None
    )
    if valid_from_dt > effective_dt:
        raise ExpectedError("effective_profile_time_invalid")
    if valid_until_dt is not None and valid_until_dt < valid_from_dt:
        raise ExpectedError("effective_profile_time_invalid")
    eval_dt = _parse_iso_datetime(profile.get("evaluated_at"))
    if eval_dt < effective_dt or eval_dt < valid_from_dt:
        raise ExpectedError("effective_profile_not_active")
    if valid_until_dt is not None and eval_dt > valid_until_dt:
        raise ExpectedError("effective_profile_not_active")

    required = profile.get("required_obligations", [])
    advisory = profile.get("advisory_checks", [])
    if not isinstance(required, list) or not isinstance(advisory, list):
        raise ExpectedError("effective_profile_obligations_invalid")
    if "declared_checks" in profile:
        declared = profile["declared_checks"]
        if not isinstance(declared, list) or any(
            not isinstance(item, str) or not item for item in declared
        ):
            raise ExpectedError("effective_profile_declared_checks_invalid")
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
        for opt_list in ("target_refs", "actions"):
            if opt_list in item:
                vals = item[opt_list]
                if not isinstance(vals, list) or any(
                    not isinstance(v, str) or not v for v in vals
                ):
                    raise ExpectedError("effective_profile_obligations_invalid")
    for item in advisory:
        if isinstance(item, dict):
            for opt_list in ("target_refs", "actions"):
                if opt_list in item:
                    vals = item[opt_list]
                    if not isinstance(vals, list) or any(
                        not isinstance(v, str) or not v for v in vals
                    ):
                        raise ExpectedError("effective_profile_obligations_invalid")
    if git is not None:
        _match_profile_event(profile, git)
    return profile


def _target_matches(target_ref, allowed_targets):
    for pattern in allowed_targets:
        if pattern == target_ref:
            return True
        if pattern.endswith("/*") and target_ref.startswith(pattern[:-1]):
            return True
    return False


def _obligation_matches_scope(item, *, target_ref=None, ref_action=None):
    if not isinstance(item, dict):
        return True
    target_refs = item.get("target_refs")
    if (
        target_ref is not None
        and target_refs
        and not _target_matches(str(target_ref), {str(t) for t in target_refs})
    ):
        return False
    actions = item.get("actions")
    return not (
        ref_action is not None
        and actions
        and str(ref_action) not in {str(a) for a in actions}
    )


def _parse_profile_obligations(
    effective_profile,
    *,
    hygiene_rule=None,
    sources_rule=None,
    selection_rule=None,
    weaver_rule=None,
    target_ref=None,
    ref_action=None,
):
    obligations_by_check = {}
    required_by_check = {}
    advisory_by_check = {}
    explicit_rules = {}
    global_allow_inapp = set(effective_profile.get("allowed_inapplicability") or ())
    waived_obligations_by_check = {}
    profile_non_check = []

    for ob in effective_profile.get("required_obligations", []):
        if not _obligation_matches_scope(
            ob, target_ref=target_ref, ref_action=ref_action
        ):
            continue
        ob_ref = ob["obligation_ref"]
        kind = ob.get("kind", "check")
        if kind == "non_check":
            profile_non_check.append(
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
                explicit_rules.setdefault(name, []).append(ob["rule_ref"])
            if ob.get("allow_inapplicable") or name in global_allow_inapp:
                waived_obligations_by_check.setdefault(name, set()).add(
                    _canon_key(ob_ref)
                )

    for adv in effective_profile.get("advisory_checks", []):
        if isinstance(adv, dict) and not _obligation_matches_scope(
            adv, target_ref=target_ref, ref_action=ref_action
        ):
            continue
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
        if adv_rule is not None:
            explicit_rules.setdefault(name, []).append(adv_rule)

    declared_names = set(effective_profile.get("declared_checks") or ())
    rule_by_check = {}
    rule_conflicts_by_check = {}
    all_names = (
        set(explicit_rules)
        | _HYGIENE_MANAGED
        | {"source"}
        | set(required_by_check)
        | set(advisory_by_check)
        | declared_names
    )
    for name in sorted(all_names):
        distinct = list(
            {_canon_key(r): r for r in explicit_rules.get(name, [])}.values()
        )
        config_rule = (
            hygiene_rule
            if name in _HYGIENE_MANAGED
            else sources_rule
            if name == "source"
            else None
        )
        if len(distinct) > 1:
            rule_conflicts_by_check[name] = distinct
        elif len(distinct) == 1:
            chosen = distinct[0]
            if config_rule is not None and _canon_key(chosen) != _canon_key(
                config_rule
            ):
                rule_conflicts_by_check[name] = [chosen, config_rule]
            else:
                rule_by_check[name] = chosen
        elif config_rule is not None:
            rule_by_check[name] = config_rule

    for name in list(obligations_by_check):
        obligations_by_check[name] = sorted(
            {_canon_key(item): item for item in obligations_by_check[name]}.values(),
            key=_canon_key,
        )
    for name in list(required_by_check):
        required_by_check[name] = sorted(required_by_check[name], key=_canon_key)
    for name in list(advisory_by_check):
        advisory_by_check[name] = sorted(advisory_by_check[name], key=_canon_key)

    return (
        obligations_by_check,
        required_by_check,
        advisory_by_check,
        rule_by_check,
        rule_conflicts_by_check,
        waived_obligations_by_check,
        profile_non_check,
    )


def _configured_domain_names(
    scope_by_name, hygiene_config, sources_rule, *, is_delete=False
):
    if is_delete:
        return set()
    configured = set()
    if hygiene_config is not None:
        configured.update(hygiene.effective_checks(hygiene_config))
    else:
        configured.update(n for n in scope_by_name if n in _HYGIENE_MANAGED)
    if sources_rule is not None:
        configured.add("source")
    for name, item in scope_by_name.items():
        if name in _HYGIENE_MANAGED or name == "source":
            continue
        if item.get("status") not in {"not_applicable", "no_tests", "no_changes"}:
            configured.add(name)
    return configured


def _expected_non_check_obligations(
    changeset_non_check, profile_non_check=(), *, source_ref=None
):
    result = [dict(item) for item in profile_non_check]
    for ob_name in changeset_non_check or ():
        entry = {
            "obligation_ref": f"eci/{ob_name}",
            "kind": ob_name,
            "status": "required",
            "reason": "remote_ref_deleted" if ob_name == "ref_deletion" else ob_name,
        }
        if source_ref is not None:
            entry["source_ref"] = source_ref
        result.append(entry)
    return result


def _ref_event_and_source(ref):
    raw_before = ref.get("raw_before") or ref.get("before") or ""
    raw_after = ref.get("raw_after") or ref.get("after") or ""
    ref_action = ref.get("ref_action")
    object_type = ref.get("object_type")
    force_push = bool(ref.get("force_push", False))
    baseline_status = ref.get("baseline_status")
    ref_paths = tuple(sorted(ref.get("paths") or ()))
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
    return source_label, baseline_obj, ref_event, ref_source


def _recompute_ref_projection(
    ref,
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
    source_prefixes=None,
    weaver_prefixes=None,
    effective_profile=None,
    root=None,
):
    schema_version = EXPECTED_V2 if effective_profile is not None else EXPECTED_VERSION
    ref_action = ref.get("ref_action")
    object_type = ref.get("object_type")
    force_push = bool(ref.get("force_push", False))
    baseline_status = ref.get("baseline_status")
    ref_complete = bool(ref.get("complete", False))
    ref_paths = tuple(sorted(ref.get("paths") or ()))
    ref_deleted = tuple(sorted(ref.get("deleted_paths") or ()))
    target_ref = ref.get("remote_ref") or ref.get("local_ref")
    source_label, baseline_obj, ref_event, ref_source = _ref_event_and_source(ref)

    if effective_profile is not None:
        (
            obligations_by_check,
            required_by_check,
            advisory_by_check,
            rule_by_check,
            rule_conflicts_by_check,
            waived_obligations_by_check,
            profile_non_check,
        ) = _parse_profile_obligations(
            effective_profile,
            hygiene_rule=hygiene_rule,
            sources_rule=sources_rule,
            selection_rule=selection_rule,
            weaver_rule=selection_rule,
            target_ref=target_ref,
            ref_action=ref_action,
        )
        hygiene_enabled = (
            frozenset(hygiene.effective_checks(hygiene_config))
            if hygiene_config is not None
            else frozenset(scope_by_name)
        )
        declared_checks = effective_profile.get("declared_checks")
        if declared_checks is not None:
            check_names = list(declared_checks)
            for name, item in scope_by_name.items():
                if (
                    item.get("status")
                    not in {"not_applicable", "no_tests", "no_changes"}
                    and name not in check_names
                ):
                    check_names.append(name)
        else:
            check_names = list(scope_by_name)
        for name in list(required_by_check) + list(advisory_by_check):
            if name not in check_names:
                check_names.append(name)
    else:
        obligations_by_check = {}
        required_by_check = {}
        advisory_by_check = {}
        rule_by_check = {}
        rule_conflicts_by_check = {}
        waived_obligations_by_check = {}
        profile_non_check = ()
        hygiene_enabled = frozenset()
        check_names = [
            item.get("name")
            for item in scope_by_name.values()
            if item.get("name") is not None
        ]

    pseudo_changes = SimpleNamespace(
        paths=ref_paths,
        deleted_paths=ref_deleted,
        deleted_paths_resolved=True,
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
            root=root,
            full=False,
            code_prefixes=code_prefixes,
        )
        if hygiene_config is not None
        else {}
    )
    src_pfxs = tuple(source_prefixes or code_prefixes or ("src/", "scripts/", "tests/"))
    wvr_pfxs = dict(weaver_prefixes or {})

    ref_checks = []
    ref_conflicts = []
    for name in check_names:
        if (
            effective_profile is not None
            and ref_action == "delete"
            and name in scope_by_name
            and name not in required_by_check
            and name not in advisory_by_check
        ):
            continue
        definition = check_contract.definition_for(name)
        req_obs = list(required_by_check.get(name, []))
        adv_obs = list(advisory_by_check.get(name, []))
        disabled_by_hygiene = (
            effective_profile is not None
            and bool(req_obs)
            and hygiene_config is not None
            and name in _HYGIENE_MANAGED
            and name not in hygiene_enabled
        )
        if disabled_by_hygiene:
            ref_conflicts.append(
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
        elif name in hygiene_app:
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
        elif name == "source" and "source" in scope_by_name:
            src_inputs = [
                p for p in ref_paths if p.startswith(src_pfxs) or p == "ci-sources.toml"
            ]
            app_status = "selected" if src_inputs else "not_applicable"
            app_reason = "source_inputs_changed" if src_inputs else "no_source_changes"
            app_inputs = src_inputs
        elif str(name).startswith("weaver_") and name in scope_by_name:
            origin = scope_by_name.get(name, {})
            if name in wvr_pfxs:
                pfxs = tuple(wvr_pfxs[name])
                w_inputs = [p for p in ref_paths if p.startswith(pfxs)]
            else:
                w_inputs = [p for p in origin.get("inputs", ()) if p in ref_paths]
            if w_inputs:
                app_status = "selected"
                app_reason = origin.get("reason") or "semantic_conventions_changed"
                app_inputs = w_inputs
            else:
                app_status = "not_applicable"
                app_reason = "no_changes"
                app_inputs = []
        else:
            origin = scope_by_name.get(name, {})
            app_inputs = [p for p in origin.get("inputs", ()) if p in ref_paths]
            if (
                origin.get("status") == "selected"
                and not app_inputs
                and origin.get("inputs")
            ):
                app_status = "not_applicable"
                app_reason = "no_changes"
            else:
                app_status = origin.get("status", "unknown")
                app_reason = origin.get("reason")

        unwaived_req_obs = [
            ob_ref
            for ob_ref in req_obs
            if _canon_key(ob_ref) not in waived_obligations_by_check.get(name, set())
        ]
        has_inapp_conflict = (
            effective_profile is not None
            and app_status == "not_applicable"
            and bool(unwaived_req_obs)
        )
        if has_inapp_conflict:
            ref_conflicts.append(
                {
                    "kind": "inapplicable_required_obligation",
                    "check": name,
                    "obligation_refs": unwaived_req_obs,
                    "reason": app_reason or "not_applicable_without_profile_waiver",
                }
            )
        has_rule_conflict = name in rule_conflicts_by_check
        if has_rule_conflict:
            ref_conflicts.append(
                {
                    "kind": "conflicting_rule_requirements",
                    "check": name,
                    "obligation_refs": req_obs + adv_obs,
                    "rules": rule_conflicts_by_check[name],
                    "reason": "conflicting_rules_for_same_check",
                }
            )
            rule_ref = None
            rule_status = "conflict"
        else:
            rule_ref = rule_by_check.get(name)
            rule_status = "resolved" if rule_ref is not None else "unknown"
        entry = {
            "name": name,
            "check_id": definition.check_id if definition else None,
            "definition_version": (
                definition.definition_version if definition else None
            ),
            "definition_status": "resolved" if definition else "unknown",
            "event_ref": json.loads(json.dumps(ref_event)),
            "selection_rule_ref": selection_rule,
            "rule_ref": rule_ref,
            "rule_status": rule_status,
            "profile_ref": profile_ref,
            "applicability": {"status": app_status, "reason": app_reason},
            "policy_status": (
                "conflict"
                if (disabled_by_hygiene or has_rule_conflict or has_inapp_conflict)
                else "resolved"
                if (effective_profile is not None and (req_obs or adv_obs))
                else "advisory_unmapped"
                if effective_profile is not None
                else "unknown"
            ),
            "source_ref": json.loads(json.dumps(ref_source)),
            "input_refs": [{"kind": "changed_path", "path": p} for p in app_inputs],
            "baseline_ref": json.loads(json.dumps(baseline_obj)),
        }
        if effective_profile is not None:
            entry["obligation_refs"] = list(obligations_by_check.get(name, []))
            entry["required_by_obligations"] = req_obs
            entry["advisory_for_obligations"] = adv_obs
        entry["expected_check_ref"] = entry_identity(entry)
        ref_checks.append(entry)

    non_check = _expected_non_check_obligations(
        ref.get("non_check_obligations") or (),
        profile_non_check,
        source_ref=source_label,
    )
    configured_domain = _configured_domain_names(
        scope_by_name,
        hygiene_config,
        sources_rule,
        is_delete=(ref_action == "delete"),
    )
    declared_checks = (
        effective_profile.get("declared_checks")
        if effective_profile is not None
        else None
    )
    declared_complete = isinstance(declared_checks, list) and (
        configured_domain <= set(declared_checks)
    )
    if (not ref_complete and ref_action != "delete") or any(
        item["applicability"]["status"] in {"unknown", "incomplete"}
        for item in ref_checks
    ):
        ref_status = "unknown"
    elif ref_conflicts:
        ref_status = "partial"
    elif (
        effective_profile is not None
        and effective_profile.get("domain_complete")
        and declared_complete
        and all(
            item["definition_status"] == "resolved"
            and item["rule_status"] == "resolved"
            and item["applicability"]["status"] not in {"unknown", "incomplete"}
            for item in ref_checks
        )
    ):
        ref_status = "complete"
    else:
        ref_status = (
            "partial" if (ref_checks or effective_profile is not None) else "unknown"
        )

    return {
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
            hygiene_rule if effective_profile is not None else None,
            sources_rule if effective_profile is not None else None,
            profile_sha256=(
                effective_profile.get("profile_sha256")
                if effective_profile is not None
                else None
            ),
            evaluated_at=(
                effective_profile.get("evaluated_at")
                if effective_profile is not None
                else None
            ),
        ),
        "expected_set_ref": set_identity(
            ref_checks, non_check, schema_version=schema_version
        ),
        "checks": ref_checks,
        "non_check_obligations": non_check,
        "conflicts": ref_conflicts,
        "status": ref_status,
    }


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
    source_prefixes=None,
    weaver_prefixes=None,
    effective_profile=None,
):
    if changeset.snapshot != "multi" or not changeset.references:
        return None
    return [
        _recompute_ref_projection(
            ref,
            scope_by_name=scope_by_name,
            selection_rule=selection_rule,
            hygiene_rule=hygiene_rule,
            sources_rule=sources_rule,
            consumer=consumer,
            profile_ref=profile_ref,
            profile_source=profile_source,
            hygiene_config=hygiene_config,
            code_prefixes=code_prefixes,
            source_prefixes=source_prefixes,
            weaver_prefixes=weaver_prefixes,
            effective_profile=effective_profile,
            root=changeset.root,
        )
        for ref in changeset.references
    ]


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
    validate_effective_profile(effective_profile, git=_git_from_changeset(changeset))
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
    target_ref = getattr(changeset, "base_ref", None) or getattr(
        changeset, "head_ref", None
    )
    ref_action = getattr(changeset, "ref_action", None)
    (
        obligations_by_check,
        required_by_check,
        advisory_by_check,
        rule_by_check,
        rule_conflicts_by_check,
        waived_obligations_by_check,
        profile_non_check,
    ) = _parse_profile_obligations(
        effective_profile,
        hygiene_rule=hygiene_rule,
        sources_rule=sources_rule,
        selection_rule=selection_rule,
        weaver_rule=selection_rule,
        target_ref=target_ref if changeset.snapshot != "multi" else None,
        ref_action=ref_action if changeset.snapshot != "multi" else None,
    )
    non_check_obligations = _expected_non_check_obligations(
        getattr(changeset, "non_check_obligations", ()),
        profile_non_check,
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
    unresolved_rules = False
    is_delete = ref_action == "delete"
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
        req_obs = list(required_by_check.get(name, []))
        adv_obs = list(advisory_by_check.get(name, []))
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

        if app_status in {"unknown", "incomplete"}:
            unresolved_applicability = True
        unwaived_req_obs = [
            ob_ref
            for ob_ref in req_obs
            if _canon_key(ob_ref) not in waived_obligations_by_check.get(name, set())
        ]
        has_inapp_conflict = app_status == "not_applicable" and bool(unwaived_req_obs)
        if has_inapp_conflict:
            conflicts.append(
                {
                    "kind": "inapplicable_required_obligation",
                    "check": name,
                    "obligation_refs": unwaived_req_obs,
                    "reason": app_reason or "not_applicable_without_profile_waiver",
                }
            )

        has_rule_conflict = name in rule_conflicts_by_check
        if has_rule_conflict:
            conflicts.append(
                {
                    "kind": "conflicting_rule_requirements",
                    "check": name,
                    "obligation_refs": req_obs + adv_obs,
                    "rules": rule_conflicts_by_check[name],
                    "reason": "conflicting_rules_for_same_check",
                }
            )
            rule_ref = None
            rule_status = "conflict"
            unresolved_rules = True
        else:
            rule_ref = rule_by_check.get(name)
            rule_status = "resolved" if rule_ref is not None else "unknown"
            if rule_status != "resolved":
                unresolved_rules = True

        all_obs = list(obligations_by_check.get(name, []))
        policy_status = (
            "conflict"
            if (disabled_by_hygiene or has_rule_conflict or has_inapp_conflict)
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

    configured_domain = _configured_domain_names(
        scope_by_name, hygiene_config, sources_rule, is_delete=is_delete
    )
    domain_complete_flag = bool(effective_profile.get("domain_complete", False))
    missing_declared_domain = not isinstance(declared_checks, list)
    declared_domain_incomplete = isinstance(declared_checks, list) and not (
        configured_domain <= set(declared_checks)
    )
    domain_complete = (
        domain_complete_flag
        and not missing_declared_domain
        and not declared_domain_incomplete
    )
    per_ref_incomplete = bool(
        per_ref and any(item.get("status") != "complete" for item in per_ref)
    )
    if conflicts:
        status = "partial"
        reason = "profile_hygiene_or_rule_conflict"
        consumer_completeness = "incomplete_conflict"
    elif unresolved_applicability or (
        not getattr(changeset, "complete", True) and not is_delete
    ):
        status = "unknown"
        reason = "applicability_or_changeset_incomplete"
        consumer_completeness = "unknown"
    elif not domain_complete or missing or unresolved_rules or per_ref_incomplete:
        status = "partial"
        reason = (
            "check_rule_unresolved"
            if unresolved_rules
            else "declared_domain_incomplete"
            if (domain_complete_flag and declared_domain_incomplete)
            else "expected_domain_or_definitions_incomplete"
        )
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
            profile_sha256=effective_profile.get("profile_sha256"),
            evaluated_at=effective_profile.get("evaluated_at"),
        ),
        "selection_rule_ref": selection_rule,
        "hygiene_rule_ref": hygiene_rule,
        "sources_rule_ref": sources_rule,
        "expected_set_ref": set_identity(
            checks, non_check_obligations, schema_version=EXPECTED_V2
        ),
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
    source_prefixes=None,
    weaver_prefixes=None,
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
        source_prefixes=source_prefixes,
        weaver_prefixes=weaver_prefixes,
        effective_profile=effective_profile,
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

    non_check = _expected_non_check_obligations(
        getattr(changeset, "non_check_obligations", ())
    )
    if not checks and not non_check:
        status = "unknown"
        reason = "no_expected_checks"
        definitions_status = "unknown"
    elif not checks and non_check:
        status = "partial"
        reason = "consumer_profile_not_resolved"
        definitions_status = "requested_names_complete"
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
        "expected_set_ref": set_identity(
            checks, non_check, schema_version=EXPECTED_VERSION
        ),
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


def _validate_profile_check_mappings(
    checks,
    *,
    declared_checks=None,
    obligations_by_check,
    required_by_check,
    advisory_by_check,
    rule_conflicts_by_check,
    waived_obligations_by_check=None,
    conflicts,
    is_delete=False,
):
    present_names = {item.get("name") for item in checks}
    expected_names = set(required_by_check) | set(advisory_by_check)
    if not (expected_names <= present_names):
        raise ExpectedError("expected_obligation_mapping_mismatch")
    if (
        isinstance(declared_checks, list)
        and not is_delete
        and not (set(declared_checks) <= present_names)
    ):
        raise ExpectedError("expected_declared_check_missing")
    waived_map = waived_obligations_by_check or {}
    recorded_inapp = {
        c.get("check"): c.get("obligation_refs")
        for c in (conflicts or [])
        if isinstance(c, dict) and c.get("kind") == "inapplicable_required_obligation"
    }
    for item in checks:
        name = item.get("name")
        req_obs = required_by_check.get(name, [])
        if item.get("required_by_obligations") != req_obs:
            raise ExpectedError("expected_obligation_mapping_mismatch")
        if item.get("advisory_for_obligations") != advisory_by_check.get(name, []):
            raise ExpectedError("expected_obligation_mapping_mismatch")
        if item.get("obligation_refs") != obligations_by_check.get(name, []):
            raise ExpectedError("expected_obligation_mapping_mismatch")
        if name in rule_conflicts_by_check and (
            item.get("rule_ref") is not None
            or item.get("rule_status") != "conflict"
            or item.get("policy_status") != "conflict"
        ):
            raise ExpectedError("expected_rule_conflict_unrecorded")
        unwaived_req_obs = [
            ob_ref
            for ob_ref in req_obs
            if _canon_key(ob_ref) not in waived_map.get(name, set())
        ]
        if (
            item.get("applicability", {}).get("status") == "not_applicable"
            and unwaived_req_obs
            and (
                item.get("policy_status") != "conflict"
                or recorded_inapp.get(name) != unwaived_req_obs
            )
        ):
            raise ExpectedError("expected_inapplicable_obligation_unrecorded")
    recorded_rule_conflicts = {
        c.get("check")
        for c in (conflicts or [])
        if isinstance(c, dict) and c.get("kind") == "conflicting_rule_requirements"
    }
    if not (set(rule_conflicts_by_check) <= recorded_rule_conflicts):
        raise ExpectedError("expected_rule_conflict_unrecorded")


def _hygiene_config_for_validation(plan):
    if not isinstance(plan, dict):
        return None
    hygiene_input = (plan.get("inputs") or {}).get("hygiene")
    if isinstance(hygiene_input, dict) and isinstance(
        hygiene_input.get("normalized"), dict
    ):
        try:
            return hygiene.config_from_identity(hygiene_input)
        except hygiene.HygieneError as error:
            raise ExpectedError(error.code) from None
    return hygiene.default_config()


def _validate_per_ref(expected, git, check_scope=None, plan=None):
    refs = git.get("references") or []
    per_ref = expected.get("per_ref")
    if git.get("snapshot") != "multi" and per_ref is None:
        return
    if not isinstance(per_ref, list) or len(per_ref) != len(refs):
        raise ExpectedError("expected_per_ref_mismatch")
    schema_version = expected.get("schema_version", EXPECTED_VERSION)
    profile = (
        expected.get("effective_profile") if schema_version == EXPECTED_V2 else None
    )
    scope_by_name = {
        item.get("name"): item
        for item in (check_scope or [])
        if isinstance(item, dict) and item.get("name")
    }
    hygiene_config = _hygiene_config_for_validation(plan)
    selection_block = (plan or {}).get("selection") if isinstance(plan, dict) else None
    code_prefixes = (
        selection_block.get("code_prefixes")
        if isinstance(selection_block, dict)
        else None
    )
    source_prefixes = (
        selection_block.get("source_prefixes")
        if isinstance(selection_block, dict)
        else None
    )
    weaver_prefixes = (
        selection_block.get("weaver_prefixes")
        if isinstance(selection_block, dict)
        else None
    )

    for plan_ref, proj_ref in zip(refs, per_ref, strict=True):
        if not isinstance(proj_ref, dict):
            raise ExpectedError("expected_per_ref_invalid")
        ref_checks = proj_ref.get("checks")
        if not isinstance(ref_checks, list):
            raise ExpectedError("expected_per_ref_invalid")
        (
            source_label,
            expected_baseline,
            expected_ref_event,
            expected_ref_source,
        ) = _ref_event_and_source(plan_ref)
        ref_action = plan_ref.get("ref_action")
        if (
            proj_ref.get("source_ref") != source_label
            or proj_ref.get("local_ref") != plan_ref.get("local_ref")
            or proj_ref.get("remote_ref") != plan_ref.get("remote_ref")
            or proj_ref.get("ref_action") != ref_action
            or proj_ref.get("object_type") != plan_ref.get("object_type")
            or proj_ref.get("force_push") != bool(plan_ref.get("force_push", False))
            or proj_ref.get("baseline_status") != plan_ref.get("baseline_status")
            or proj_ref.get("baseline_ref") != expected_baseline
        ):
            raise ExpectedError("expected_baseline_mismatch")
        if proj_ref.get("event_ref") != expected_ref_event:
            raise ExpectedError("expected_per_ref_event_mismatch")
        if proj_ref.get("source") != expected_ref_source:
            raise ExpectedError("expected_per_ref_source_mismatch")
        expected_scope = scope_identity(
            expected.get("consumer"),
            expected.get("profile_ref"),
            expected.get("profile_source"),
            expected_ref_event,
            expected_ref_source,
            expected.get("selection_rule_ref"),
            expected.get("hygiene_rule_ref"),
            expected.get("sources_rule_ref"),
            profile_sha256=profile.get("profile_sha256") if profile else None,
            evaluated_at=profile.get("evaluated_at") if profile else None,
        )
        if proj_ref.get("scope_id") != expected_scope:
            raise ExpectedError("expected_per_ref_scope_id_mismatch")

        target_ref = plan_ref.get("remote_ref") or plan_ref.get("local_ref")
        if schema_version == EXPECTED_V2 and profile is not None:
            (
                obligations_by_check,
                required_by_check,
                advisory_by_check,
                _rule_by_check,
                rule_conflicts_by_check,
                waived_obligations_by_check,
                profile_non_check,
            ) = _parse_profile_obligations(
                profile,
                hygiene_rule=expected.get("hygiene_rule_ref"),
                sources_rule=expected.get("sources_rule_ref"),
                selection_rule=expected.get("selection_rule_ref"),
                weaver_rule=expected.get("selection_rule_ref"),
                target_ref=target_ref,
                ref_action=ref_action,
            )
        else:
            obligations_by_check = {}
            required_by_check = {}
            advisory_by_check = {}
            rule_conflicts_by_check = {}
            waived_obligations_by_check = {}
            profile_non_check = ()

        plan_ref_obs = list(plan_ref.get("non_check_obligations") or ())
        if ref_action == "delete" and "ref_deletion" not in plan_ref_obs:
            raise ExpectedError("expected_per_ref_obligations_mismatch")
        expected_non_check = _expected_non_check_obligations(
            plan_ref_obs,
            profile_non_check,
            source_ref=source_label,
        )
        actual_non_check = proj_ref.get("non_check_obligations")
        if (
            not isinstance(actual_non_check, list)
            or actual_non_check != expected_non_check
        ):
            raise ExpectedError("expected_per_ref_obligations_mismatch")

        ref_paths = set(plan_ref.get("paths") or ())
        for item in ref_checks:
            if not isinstance(item, dict):
                raise ExpectedError("expected_check_invalid")
            if "job_refs" in item or "job_ref" in item:
                raise ExpectedError("expected_routing_forbidden")
            if item.get("expected_check_ref") != entry_identity(item):
                raise ExpectedError("expected_check_ref_mismatch")
            if item.get("baseline_ref") != expected_baseline:
                raise ExpectedError("expected_baseline_mismatch")
            if item.get("event_ref") != expected_ref_event:
                raise ExpectedError("expected_per_ref_event_mismatch")
            if item.get("source_ref") != expected_ref_source:
                raise ExpectedError("expected_per_ref_source_mismatch")
            item_paths = {
                ref.get("path")
                for ref in item.get("input_refs", [])
                if ref.get("kind") == "changed_path"
            }
            if not (item_paths <= ref_paths):
                raise ExpectedError("expected_per_ref_input_mismatch")
            if ref_action == "delete" and (
                item.get("applicability", {}).get("status") == "selected"
            ):
                raise ExpectedError("expected_per_ref_applicability_mismatch")

        if (
            not plan_ref.get("complete")
            and ref_action != "delete"
            and proj_ref.get("status") != "unknown"
        ):
            raise ExpectedError("expected_per_ref_status_invalid")

        if schema_version == EXPECTED_V2 and profile is not None:
            _validate_profile_check_mappings(
                ref_checks,
                declared_checks=profile.get("declared_checks"),
                obligations_by_check=obligations_by_check,
                required_by_check=required_by_check,
                advisory_by_check=advisory_by_check,
                rule_conflicts_by_check=rule_conflicts_by_check,
                waived_obligations_by_check=waived_obligations_by_check,
                conflicts=proj_ref.get("conflicts") or [],
                is_delete=(ref_action == "delete"),
            )

        if check_scope is not None:
            recomputed = _recompute_ref_projection(
                plan_ref,
                scope_by_name=scope_by_name,
                selection_rule=expected.get("selection_rule_ref"),
                hygiene_rule=expected.get("hygiene_rule_ref"),
                sources_rule=expected.get("sources_rule_ref"),
                consumer=expected.get("consumer"),
                profile_ref=expected.get("profile_ref"),
                profile_source=expected.get("profile_source"),
                hygiene_config=hygiene_config or hygiene.default_config(),
                code_prefixes=code_prefixes,
                source_prefixes=source_prefixes,
                weaver_prefixes=weaver_prefixes,
                effective_profile=profile,
            )
            recomputed_by_name = {c["name"]: c for c in recomputed["checks"]}
            actual_names = [c.get("name") for c in ref_checks]
            expected_names = [c["name"] for c in recomputed["checks"]]
            if actual_names != expected_names:
                raise ExpectedError("expected_per_ref_applicability_mismatch")
            for item in ref_checks:
                exp_item = recomputed_by_name[item["name"]]
                if item.get("applicability") != exp_item.get("applicability"):
                    raise ExpectedError("expected_per_ref_applicability_mismatch")
                if item.get("input_refs") != exp_item.get("input_refs"):
                    raise ExpectedError("expected_per_ref_input_mismatch")
                if (
                    item.get("policy_status") != exp_item.get("policy_status")
                    or item.get("rule_ref") != exp_item.get("rule_ref")
                    or item.get("rule_status") != exp_item.get("rule_status")
                ):
                    raise ExpectedError("expected_per_ref_conflicts_mismatch")
            if (proj_ref.get("conflicts") or []) != recomputed["conflicts"]:
                raise ExpectedError("expected_per_ref_conflicts_mismatch")
            if proj_ref.get("status") != recomputed["status"]:
                raise ExpectedError("expected_per_ref_status_invalid")

        if proj_ref.get("expected_set_ref") != set_identity(
            ref_checks, actual_non_check, schema_version=schema_version
        ):
            raise ExpectedError("expected_set_ref_mismatch")


def validate_projection(expected, git, check_scope=None, *, plan=None):
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
    profile = None
    profile_non_check = ()
    if projection_source == "check_scope":
        if check_scope is None:
            raise ExpectedError("expected_check_scope_missing")
        scope_names = [str(item.get("name")) for item in check_scope]
        expected_names = [str(item.get("name")) for item in checks]
        if len(scope_names) != len(set(scope_names)) or sorted(scope_names) != sorted(
            expected_names
        ):
            raise ExpectedError("expected_scope_mismatch")
        git_non_check = git.get("non_check_obligations") or []
        if git_non_check or git.get("ref_action") == "delete":
            expected_non_check = _expected_non_check_obligations(git_non_check)
            if expected.get("non_check_obligations") != expected_non_check:
                raise ExpectedError("expected_non_check_obligations_mismatch")
    else:
        profile = validate_effective_profile(expected.get("effective_profile"), git=git)
        if isinstance(plan, dict):
            prof_input = (plan.get("inputs") or {}).get("effective_profile")
            gen_sha = (plan.get("generator") or {}).get("effective_profile_sha256")
            if (
                not isinstance(prof_input, dict)
                or (prof_input.get("effective_profile_ref") or {}).get("sha256")
                != profile.get("profile_sha256")
                or prof_input.get("profile_ref") != profile.get("profile_ref")
                or prof_input.get("profile_revision") != profile.get("profile_revision")
                or prof_input.get("approval_source") != profile.get("approval_source")
                or prof_input.get("evaluated_at") != profile.get("evaluated_at")
                or prof_input.get("domain_complete")
                != bool(profile.get("domain_complete", False))
                or prof_input.get("authority_verified")
                != bool(profile.get("authority_verified", False))
                or gen_sha != profile.get("profile_sha256")
            ):
                raise ExpectedError("effective_profile_input_mismatch")
        target_ref = (
            (git.get("base_ref") or git.get("head_ref"))
            if git.get("snapshot") != "multi"
            else None
        )
        ref_action = git.get("ref_action") if git.get("snapshot") != "multi" else None
        (
            obligations_by_check,
            required_by_check,
            advisory_by_check,
            _rule_by_check,
            rule_conflicts_by_check,
            waived_obligations_by_check,
            profile_non_check,
        ) = _parse_profile_obligations(
            profile,
            hygiene_rule=expected.get("hygiene_rule_ref"),
            sources_rule=expected.get("sources_rule_ref"),
            selection_rule=expected.get("selection_rule_ref"),
            weaver_rule=expected.get("selection_rule_ref"),
            target_ref=target_ref,
            ref_action=ref_action,
        )
        expected_non_check = _expected_non_check_obligations(
            git.get("non_check_obligations") or (),
            profile_non_check,
        )
        if git.get("ref_action") == "delete" and "ref_deletion" not in (
            git.get("non_check_obligations") or ()
        ):
            raise ExpectedError("expected_non_check_obligations_mismatch")
        if expected.get("non_check_obligations") != expected_non_check:
            raise ExpectedError("expected_non_check_obligations_mismatch")
        is_delete = git.get("ref_action") == "delete"
        _validate_profile_check_mappings(
            checks,
            declared_checks=profile.get("declared_checks"),
            obligations_by_check=obligations_by_check,
            required_by_check=required_by_check,
            advisory_by_check=advisory_by_check,
            rule_conflicts_by_check=rule_conflicts_by_check,
            waived_obligations_by_check=waived_obligations_by_check,
            conflicts=expected.get("conflicts") or [],
            is_delete=is_delete,
        )
        scope_by_name = {
            str(item.get("name")): item
            for item in (check_scope or [])
            if isinstance(item, dict) and item.get("name")
        }
        hygiene_config = _hygiene_config_for_validation(plan)
        if check_scope is not None:
            present_names = {str(item.get("name")) for item in checks}
            for name, scope_item in scope_by_name.items():
                if (
                    not is_delete
                    and scope_item.get("status")
                    not in {"not_applicable", "no_tests", "no_changes"}
                    and name not in present_names
                ):
                    raise ExpectedError("expected_scope_mismatch")
            for item in checks:
                name = str(item.get("name"))
                origin = scope_by_name.get(name)
                app = item.get("applicability") or {}
                if origin is not None and app.get("status") != "config_conflict":
                    if app.get("status") != origin.get("status") or app.get(
                        "reason"
                    ) != origin.get("reason"):
                        raise ExpectedError("expected_applicability_mismatch")
                    expected_inputs = [
                        ref.get("path")
                        for ref in item.get("input_refs", [])
                        if ref.get("kind") == "changed_path"
                    ]
                    if expected_inputs != list(origin.get("inputs", [])):
                        raise ExpectedError("expected_input_mismatch")
        configured_domain = _configured_domain_names(
            scope_by_name,
            hygiene_config,
            expected.get("sources_rule_ref"),
            is_delete=is_delete,
        )
        declared_checks = profile.get("declared_checks")
        declared_complete = isinstance(declared_checks, list) and (
            configured_domain <= set(declared_checks)
        )
        per_ref_list = expected.get("per_ref")
        per_ref_incomplete = bool(
            isinstance(per_ref_list, list)
            and any(
                isinstance(r, dict) and r.get("status") != "complete"
                for r in per_ref_list
            )
        )
        if expected.get("status") == "complete" and (
            not profile.get("domain_complete")
            or not declared_complete
            or expected.get("missing")
            or expected.get("conflicts")
            or per_ref_incomplete
            or any(
                item.get("definition_status") != "resolved"
                or item.get("rule_status") != "resolved"
                or item.get("applicability", {}).get("status")
                in {"unknown", "incomplete"}
                for item in checks
            )
        ):
            raise ExpectedError("expected_complete_unproven")

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
    if expected.get("expected_set_ref") != set_identity(
        checks,
        expected.get("non_check_obligations") or (),
        schema_version=schema_version,
    ):
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

    _validate_per_ref(expected, git, check_scope=check_scope, plan=plan)
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
        profile_sha256=profile.get("profile_sha256") if profile else None,
        evaluated_at=profile.get("evaluated_at") if profile else None,
    )
    if expected.get("scope_id") != expected_scope:
        raise ExpectedError("expected_scope_id_mismatch")
    return expected
