"""#61 R1 Required Assessment for one staged Action x Target x Phase scope.

This module is the #61 consumer of the complete Required Obligations universe
produced by the authorized Profile/Expected path (#66-B2), the #66-B Expected v2
Check projection, the #66-C structural reconciliation receipt, the #63 trusted
admission projection and genuine non-check evidence from domain verifiers.

It performs one immutable, phase-scoped judgement. It does **not**:

- enumerate policy obligations or re-derive a Profile (that is #66-B2);
- admit trusted evidence, authenticate issuers or prove historical reuse
  (that is #63);
- rebuild structural Binding/Reconciliation (that is #66-C);
- execute checks, publish a GitHub Check, read Rulesets or authorize a Release
  (those are #61 R2-R5).

Four facts are kept independent, because they have different owners:

``verification.authority``
    The current Profile/approval source is verified, effective at the
    assessment time and not revoked.
``verification.check_domain``
    The Expected v2 Check domain is complete and bound to the same scope.
``verification.obligations_universe``
    The authorized universe closes the current Action/Target/Phase/Ref scope and
    enumerates every current Required obligation.
``verification.evidence_satisfaction``
    Every current-due Required obligation has genuine satisfying evidence.

A gate passes only when all four facts hold. Missing, failed, skipped,
cancelled, conflicting, out-of-scope or unverifiable required evidence is never
promoted by a matched structural receipt, a local PASS or a partial coverage
entry.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime

from . import expected_checks, reconciliation

UNIVERSE_KIND = "engineering-ci-required-obligations"
UNIVERSE_VERSION = 1
ADMISSION_KIND = "engineering-ci-evidence-admission"
ADMISSION_VERSION = 1
NON_CHECK_EVIDENCE_KIND = "engineering-ci-non-check-evidence"
NON_CHECK_EVIDENCE_VERSION = 1
ASSESSMENT_KIND = "engineering-ci-required-assessment"
ASSESSMENT_VERSION = 1
ADMISSION_PHASE = reconciliation.EXECUTION_PHASE_ORDER[2]

OBLIGATION_KINDS = frozenset({"check", "non_check"})
APPLICABILITIES = frozenset({"required", "not_applicable", "not_yet_due"})
INSTANCE_STATUSES = frozenset(
    {"satisfied", "not_applicable", "not_yet_due", "unmet", "unverified"}
)
ADMISSION_STATUSES = frozenset({"admitted", "rejected", "unverified"})
SOURCE_KINDS = frozenset({"current_run", "historical_reuse"})
FAILURE_KINDS = frozenset({"native_failure", "conflict"})
ADJUDICATION_DISPOSITIONS = frozenset(
    {"failure_superseded", "conflict_resolved", "unresolved"}
)
AUTHORITY_STATUSES = frozenset({"verified", "unverified"})
CLOSURE_STATUSES = frozenset({"closed", "incomplete", "unverified"})
UNIVERSE_STATUSES = frozenset({"complete", "incomplete", "unknown"})
NON_CHECK_RESULTS = frozenset({"satisfied", "unmet", "unverified"})
GATE_STATUSES = frozenset({"passed", "blocked"})
FACT_STATUSES = frozenset({"verified", "unverified"})
EVIDENCE_FACT_STATUSES = frozenset({"satisfied", "unsatisfied"})
INAPPLICABLE_CHECK_STATES = frozenset({"not_applicable", "no_changes", "no_tests"})
CANDIDATE_IDENTITY_FIELDS = (
    "commit",
    "tree",
    "artifact_digest",
    "source_digest",
)
_EVIDENCE_REF_FIELDS = frozenset({"name", "version", "path", "sha256"})
_EXPECTED_ENTRY_FIELDS = (
    "name",
    "check_id",
    "definition_version",
    "definition_status",
    "event_ref",
    "selection_rule_ref",
    "rule_ref",
    "rule_status",
    "profile_ref",
    "applicability",
    "policy_status",
    "source_ref",
    "input_refs",
    "baseline_ref",
)
_SHA256_REF_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class RequiredAssessmentError(ValueError):
    """A malformed, mismatched or incoherent Required Assessment input.

    ``code`` is the stable public reason. ``native_code`` preserves an
    underlying Expected/Reconciliation rejection where one caused this error.
    """

    def __init__(self, code, *, native_code=None):
        self.code = code
        self.native_code = native_code
        super().__init__(code)


def _digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _dedupe(values):
    seen = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _text(value, code):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or any(character in value for character in "\0\r\n")
    ):
        raise RequiredAssessmentError(code)
    return value


def _optional_text(value, code):
    if value is None:
        return None
    return _text(value, code)


def _flag(value, code):
    if type(value) is not bool:
        raise RequiredAssessmentError(code)
    return value


def _internal_ref(value, code):
    if not isinstance(value, str) or not _SHA256_REF_RE.fullmatch(value):
        raise RequiredAssessmentError(code)
    return value


def _optional_internal_ref(value, code):
    if value is None:
        return None
    return _internal_ref(value, code)


def _evidence_ref(value, code):
    if not isinstance(value, dict) or set(value) - _EVIDENCE_REF_FIELDS:
        raise RequiredAssessmentError(code)
    _text(value.get("name"), code)
    version = value.get("version")
    if not isinstance(version, (str, int)) or isinstance(version, bool):
        raise RequiredAssessmentError(code)
    if not str(version):
        raise RequiredAssessmentError(code)
    sha256 = value.get("sha256")
    if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
        raise RequiredAssessmentError(code)
    if value.get("path") is not None:
        _text(value["path"], code)
    return dict(value)


def _optional_evidence_ref(value, code):
    if value is None:
        return None
    return _evidence_ref(value, code)


def _datetime(value, code):
    if not isinstance(value, str) or not value or len(value) > 64:
        raise RequiredAssessmentError(code)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise RequiredAssessmentError(code) from None
    if parsed.tzinfo is None:
        raise RequiredAssessmentError(code)
    return parsed


def _optional_datetime(value, code):
    if value is None:
        return None
    return _datetime(value, code)


def _obligation_ref(value, code):
    if isinstance(value, str):
        return _text(value, code)
    if isinstance(value, dict) and value:
        return dict(value)
    raise RequiredAssessmentError(code)


def obligation_instance_ref(obligation):
    """Canonical identity for one Required Obligation instance.

    A Check obligation instance is identified by the #63 key
    ``(scope_id, expected_check_ref)``; this identity only distinguishes the
    policy obligation instance across Target/Ref, Action, Phase and Candidate
    identity. The same ``obligation_ref`` in another scope or phase is a
    different instance and must never be merged.
    """
    record = {
        "obligation_ref": obligation.get("obligation_ref"),
        "kind": obligation.get("kind"),
        "target_ref": obligation.get("target_ref"),
        "action": obligation.get("action"),
        "phase": obligation.get("phase"),
        "candidate_digest": obligation.get("candidate_digest"),
        "source_digest": obligation.get("source_digest"),
    }
    return _digest(record)


def _select_expected_scope(expected, ref_scope_id):
    """Return one single-Ref Expected v2 scope, never the aggregate container."""
    if not isinstance(expected, dict):
        raise RequiredAssessmentError("expected_invalid")
    if expected.get("kind") != expected_checks.EXPECTED_KIND:
        raise RequiredAssessmentError("expected_kind_invalid")
    if expected.get("schema_version") != expected_checks.EXPECTED_V2:
        raise RequiredAssessmentError("expected_schema_requires_v2")
    if expected.get("projection_source") != "effective_profile":
        raise RequiredAssessmentError("expected_projection_source_unsupported")
    per_ref = expected.get("per_ref")
    if per_ref is not None:
        if ref_scope_id is None:
            raise RequiredAssessmentError("expected_ref_scope_required")
        if not isinstance(per_ref, (list, tuple)):
            raise RequiredAssessmentError("expected_invalid")
        matches = [
            item
            for item in per_ref
            if isinstance(item, dict) and item.get("scope_id") == ref_scope_id
        ]
        if len(matches) != 1:
            raise RequiredAssessmentError("expected_ref_scope_unknown")
        ref = matches[0]
        scope = {
            "status": ref.get("status"),
            "reason": ref.get("reason"),
            "consumer": expected.get("consumer"),
            "profile_ref": expected.get("profile_ref"),
            "profile_source": expected.get("profile_source"),
            "effective_profile": expected.get("effective_profile"),
            "selection_rule_ref": expected.get("selection_rule_ref"),
            "hygiene_rule_ref": expected.get("hygiene_rule_ref"),
            "sources_rule_ref": expected.get("sources_rule_ref"),
            "event_ref": ref.get("event_ref"),
            "source_ref": ref.get("source"),
            "checks": ref.get("checks", []),
            "non_check_obligations": ref.get("non_check_obligations", []),
            "scope_id": ref.get("scope_id"),
            "expected_set_ref": ref.get("expected_set_ref"),
            "consumer_completeness": "per_ref",
            "per_ref": True,
            "phase": expected.get("phase"),
            "phase_status": expected.get("phase_status"),
            "conflicts": ref.get("conflicts") or [],
            "authority_status": expected.get("authority_status"),
            "trusted_admission_status": expected.get("trusted_admission_status"),
            "ref": {
                key: ref[key]
                for key in ("local_ref", "remote_ref", "ref_action", "source_ref")
                if key in ref
            },
        }
    else:
        if ref_scope_id is not None:
            raise RequiredAssessmentError("expected_ref_scope_unexpected")
        scope = {
            "status": expected.get("status"),
            "reason": expected.get("reason"),
            "consumer": expected.get("consumer"),
            "profile_ref": expected.get("profile_ref"),
            "profile_source": expected.get("profile_source"),
            "effective_profile": expected.get("effective_profile"),
            "selection_rule_ref": expected.get("selection_rule_ref"),
            "hygiene_rule_ref": expected.get("hygiene_rule_ref"),
            "sources_rule_ref": expected.get("sources_rule_ref"),
            "event_ref": expected.get("event_ref"),
            "source_ref": expected.get("source_ref"),
            "checks": expected.get("checks"),
            "non_check_obligations": expected.get("non_check_obligations", []),
            "scope_id": expected.get("scope_id"),
            "expected_set_ref": expected.get("expected_set_ref"),
            "consumer_completeness": expected.get("consumer_completeness"),
            "per_ref": False,
            "phase": expected.get("phase"),
            "phase_status": expected.get("phase_status"),
            "conflicts": expected.get("conflicts") or [],
            "authority_status": expected.get("authority_status"),
            "trusted_admission_status": expected.get("trusted_admission_status"),
            "ref": {},
        }
    if scope["status"] not in expected_checks.STATUSES:
        raise RequiredAssessmentError("expected_status_invalid")
    if not isinstance(scope["checks"], (list, tuple)):
        raise RequiredAssessmentError("expected_checks_invalid")
    if not isinstance(scope["event_ref"], dict):
        raise RequiredAssessmentError("expected_event_invalid")
    if not isinstance(scope["source_ref"], dict):
        raise RequiredAssessmentError("expected_source_invalid")
    if scope["source_ref"].get("snapshot") == "multi":
        raise RequiredAssessmentError("expected_multi_ref_requires_per_ref")
    if not isinstance(scope["selection_rule_ref"], dict):
        raise RequiredAssessmentError("expected_selection_rule_invalid")
    if not isinstance(scope["non_check_obligations"], (list, tuple)):
        raise RequiredAssessmentError("expected_non_check_obligations_invalid")
    return scope


def _verify_expected_scope(scope):
    """Recompute the Expected identities and refuse tampered projections."""
    profile = scope.get("effective_profile")
    if not isinstance(profile, dict):
        raise RequiredAssessmentError("expected_profile_missing")
    try:
        expected_checks.validate_effective_profile(profile)
    except expected_checks.ExpectedError as error:
        raise RequiredAssessmentError(
            "expected_profile_invalid", native_code=error.code
        ) from None
    if scope.get("authority_status") not in {None, "verified", "unverified"}:
        raise RequiredAssessmentError("expected_authority_status_invalid")
    if scope.get("trusted_admission_status", "not_evaluated") != "not_evaluated":
        raise RequiredAssessmentError("expected_trust_claimed")
    profile_phase = profile.get("phase")
    scope_phase = scope.get("phase")
    phase_status = scope.get("phase_status")
    if profile_phase is not None:
        if scope_phase != profile_phase or phase_status not in {
            "unverified",
            "verified",
        }:
            raise RequiredAssessmentError("expected_phase_status_invalid")
    elif scope_phase is not None or phase_status not in {None, "not_applicable"}:
        raise RequiredAssessmentError("expected_phase_status_invalid")

    checks = []
    seen = set()
    for entry in scope["checks"]:
        if not isinstance(entry, dict):
            raise RequiredAssessmentError("expected_check_invalid")
        if any(key not in entry for key in _EXPECTED_ENTRY_FIELDS):
            raise RequiredAssessmentError("expected_check_incomplete")
        ref = _internal_ref(
            entry.get("expected_check_ref"), "expected_check_ref_invalid"
        )
        if ref in seen:
            raise RequiredAssessmentError("expected_check_ref_duplicate")
        seen.add(ref)
        if expected_checks.entry_identity(entry) != ref:
            raise RequiredAssessmentError("expected_check_ref_mismatch")
        if entry.get("event_ref") != scope["event_ref"]:
            raise RequiredAssessmentError("expected_entry_event_mismatch")
        if entry.get("source_ref") != scope["source_ref"]:
            raise RequiredAssessmentError("expected_entry_source_mismatch")
        if entry.get("selection_rule_ref") != scope["selection_rule_ref"]:
            raise RequiredAssessmentError("expected_entry_rule_mismatch")
        if entry.get("profile_ref") != scope["profile_ref"]:
            raise RequiredAssessmentError("expected_entry_profile_mismatch")
        if not isinstance(entry.get("applicability"), dict):
            raise RequiredAssessmentError("expected_applicability_invalid")
        if not isinstance(entry.get("name"), str) or not entry["name"]:
            raise RequiredAssessmentError("expected_check_invalid")
        if not isinstance(entry.get("required_by_obligations", []), list):
            raise RequiredAssessmentError("expected_check_invalid")
        if not isinstance(entry.get("input_refs"), list):
            raise RequiredAssessmentError("expected_input_refs_invalid")
        if entry.get("rule_ref") is not None:
            _evidence_ref(entry["rule_ref"], "expected_rule_ref_invalid")
        checks.append(entry)

    expected_set_ref = expected_checks.set_identity(
        checks,
        scope.get("non_check_obligations") or (),
        schema_version=expected_checks.EXPECTED_V2,
    )
    if expected_set_ref != _internal_ref(
        scope.get("expected_set_ref"), "expected_set_ref_invalid"
    ):
        raise RequiredAssessmentError("expected_set_ref_mismatch")
    computed_scope = expected_checks.scope_identity(
        scope.get("consumer"),
        scope.get("profile_ref"),
        scope.get("profile_source"),
        scope["event_ref"],
        scope["source_ref"],
        scope.get("selection_rule_ref"),
        scope.get("hygiene_rule_ref"),
        scope.get("sources_rule_ref"),
        profile_sha256=profile.get("profile_sha256"),
        evaluated_at=profile.get("evaluated_at"),
    )
    if computed_scope != _internal_ref(
        scope.get("scope_id"), "expected_scope_id_invalid"
    ):
        raise RequiredAssessmentError("expected_scope_id_mismatch")
    return checks


def _normalize_profile(universe):
    profile = universe.get("profile")
    if not isinstance(profile, dict):
        raise RequiredAssessmentError("universe_profile_invalid")
    profile_ref = profile.get("profile_ref")
    if (
        not isinstance(profile_ref, dict)
        or not isinstance(profile_ref.get("name"), str)
        or not profile_ref["name"]
        or not isinstance(profile_ref.get("version"), str)
        or not profile_ref["version"]
    ):
        raise RequiredAssessmentError("universe_profile_invalid")
    return {
        "profile_ref": dict(profile_ref),
        "profile_revision": _text(
            profile.get("profile_revision"), "universe_profile_invalid"
        ),
        "approval_source": _text(
            profile.get("approval_source"), "universe_profile_invalid"
        ),
        "profile_sha256": _internal_ref(
            profile.get("profile_sha256"), "universe_profile_invalid"
        ),
        "approval_digest": _internal_ref(
            profile.get("approval_digest"), "universe_profile_invalid"
        ),
    }


def _normalize_authority(authority):
    if not isinstance(authority, dict):
        raise RequiredAssessmentError("universe_authority_invalid")
    status = authority.get("status")
    if status not in AUTHORITY_STATUSES:
        raise RequiredAssessmentError("universe_authority_invalid")
    effective_at = _datetime(
        authority.get("effective_at"), "universe_authority_invalid"
    )
    valid_from = _optional_datetime(
        authority.get("valid_from"), "universe_authority_invalid"
    )
    valid_until = _optional_datetime(
        authority.get("valid_until"), "universe_authority_invalid"
    )
    if valid_from is not None and valid_from > effective_at:
        raise RequiredAssessmentError("universe_authority_invalid")
    if valid_until is not None and valid_until < (valid_from or effective_at):
        raise RequiredAssessmentError("universe_authority_invalid")
    evaluated_at = _datetime(
        authority.get("evaluated_at"), "universe_authority_invalid"
    )
    if evaluated_at < effective_at or (
        valid_from is not None and evaluated_at < valid_from
    ):
        raise RequiredAssessmentError("universe_authority_invalid")
    return {
        "status": status,
        "source_ref": _evidence_ref(
            authority.get("source_ref"), "universe_authority_invalid"
        ),
        "approved_at": _datetime(
            authority.get("approved_at"), "universe_authority_invalid"
        ),
        "effective_at": effective_at,
        "valid_from": valid_from,
        "valid_until": valid_until,
        "revoked_at": _optional_datetime(
            authority.get("revoked_at"), "universe_authority_invalid"
        ),
        "evaluated_at": evaluated_at,
    }


def _normalize_governance(governance):
    if not isinstance(governance, dict):
        raise RequiredAssessmentError("universe_governance_invalid")
    closure_status = governance.get("closure_status")
    if closure_status not in CLOSURE_STATUSES:
        raise RequiredAssessmentError("universe_governance_invalid")
    domains = governance.get("delegation_domains", [])
    if not isinstance(domains, list):
        raise RequiredAssessmentError("universe_governance_invalid")
    normalized_domains = []
    for domain in domains:
        if not isinstance(domain, dict):
            raise RequiredAssessmentError("universe_governance_invalid")
        included = _flag(domain.get("included"), "universe_governance_invalid")
        domain_closure = domain.get("closure_status")
        if domain_closure not in CLOSURE_STATUSES:
            raise RequiredAssessmentError("universe_governance_invalid")
        normalized_domains.append(
            {
                "domain_ref": _evidence_ref(
                    domain.get("domain_ref"), "universe_governance_invalid"
                ),
                "authority_ref": _evidence_ref(
                    domain.get("authority_ref"), "universe_governance_invalid"
                ),
                "included": included,
                "closure_status": domain_closure,
            }
        )
    for key in ("inheritance", "exclusions"):
        values = governance.get(key, [])
        if not isinstance(values, list):
            raise RequiredAssessmentError("universe_governance_invalid")
        for value in values:
            _evidence_ref(value, "universe_governance_invalid")
    return {
        "closure_status": closure_status,
        "closure_ref": _evidence_ref(
            governance.get("closure_ref"), "universe_governance_invalid"
        ),
        "scope": _optional_text(governance.get("scope"), "universe_governance_invalid"),
        "delegation_domains": normalized_domains,
        "inheritance": list(governance.get("inheritance", [])),
        "exclusions": list(governance.get("exclusions", [])),
    }


def _normalize_candidate(candidate):
    if candidate is None:
        return None
    if not isinstance(candidate, dict):
        raise RequiredAssessmentError("universe_evaluation_invalid")
    normalized = {}
    for key in CANDIDATE_IDENTITY_FIELDS:
        value = candidate.get(key)
        if value is not None:
            normalized[key] = _text(value, "universe_evaluation_invalid")
    if not normalized:
        raise RequiredAssessmentError("universe_evaluation_invalid")
    return normalized


def _normalize_phase_order(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise RequiredAssessmentError("universe_evaluation_invalid")
    phases = value.get("phases")
    if not isinstance(phases, list) or not phases:
        raise RequiredAssessmentError("universe_evaluation_invalid")
    normalized = []
    for phase in phases:
        item = _text(phase, "universe_evaluation_invalid")
        if item in normalized:
            raise RequiredAssessmentError("universe_evaluation_invalid")
        normalized.append(item)
    return {
        "ref": _evidence_ref(value.get("ref"), "universe_evaluation_invalid"),
        "phases": normalized,
    }


def _normalize_evaluation(evaluation):
    if not isinstance(evaluation, dict):
        raise RequiredAssessmentError("universe_evaluation_invalid")
    refs = evaluation.get("refs")
    if not isinstance(refs, list) or not refs:
        raise RequiredAssessmentError("universe_evaluation_invalid")
    normalized_refs = []
    for ref in refs:
        if not isinstance(ref, dict):
            raise RequiredAssessmentError("universe_evaluation_invalid")
        normalized_refs.append(
            {
                "target_ref": _text(
                    ref.get("target_ref"), "universe_evaluation_invalid"
                ),
                "ref_action": _text(
                    ref.get("ref_action"), "universe_evaluation_invalid"
                ),
            }
        )
    return {
        "context": _text(evaluation.get("context"), "universe_evaluation_invalid"),
        "action": _text(evaluation.get("action"), "universe_evaluation_invalid"),
        "target": _text(evaluation.get("target"), "universe_evaluation_invalid"),
        "phase": _text(evaluation.get("phase"), "universe_evaluation_invalid"),
        "phase_order": _normalize_phase_order(evaluation.get("phase_order")),
        "refs": normalized_refs,
        "candidate": _normalize_candidate(evaluation.get("candidate")),
        "evaluated_at": _datetime(
            evaluation.get("evaluated_at"), "universe_evaluation_invalid"
        ),
    }


def _normalize_check_domain(check_domain):
    if not isinstance(check_domain, dict):
        raise RequiredAssessmentError("universe_check_domain_invalid")
    status = check_domain.get("status")
    if status not in expected_checks.STATUSES:
        raise RequiredAssessmentError("universe_check_domain_invalid")
    return {
        "scope_id": _internal_ref(
            check_domain.get("scope_id"), "universe_check_domain_invalid"
        ),
        "expected_set_ref": _internal_ref(
            check_domain.get("expected_set_ref"), "universe_check_domain_invalid"
        ),
        "status": status,
        "consumer_completeness": _optional_text(
            check_domain.get("consumer_completeness"),
            "universe_check_domain_invalid",
        ),
    }


def _normalize_applicability_authorization(value):
    """Normalize a controlled Non-Check inapplicability authorization.

    Target/Action/Phase alone never authorize reuse across Obligation or
    Candidate instances. The fact must bind either the canonical
    ``obligation_instance_ref`` or an explicit ``obligation_ref`` (and, when
    the obligation declares Candidate/Source digests, those digests too).
    Missing instance identity is not a wildcard.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise RequiredAssessmentError("universe_obligation_invalid")
    scope = value.get("scope")
    if not isinstance(scope, dict):
        raise RequiredAssessmentError("universe_obligation_invalid")
    obligation_ref = scope.get("obligation_ref")
    if obligation_ref is not None:
        obligation_ref = _obligation_ref(obligation_ref, "universe_obligation_invalid")
    record = {
        "authority_ref": _evidence_ref(
            value.get("authority_ref"), "universe_obligation_invalid"
        ),
        "rule_ref": _evidence_ref(value.get("rule_ref"), "universe_obligation_invalid"),
        "scope": {
            "target_ref": _text(scope.get("target_ref"), "universe_obligation_invalid"),
            "action": _text(scope.get("action"), "universe_obligation_invalid"),
            "phase": _optional_text(scope.get("phase"), "universe_obligation_invalid"),
            "obligation_ref": obligation_ref,
            "candidate_digest": _optional_internal_ref(
                scope.get("candidate_digest"), "universe_obligation_invalid"
            ),
            "source_digest": _optional_internal_ref(
                scope.get("source_digest"), "universe_obligation_invalid"
            ),
        },
        "obligation_instance_ref": _optional_internal_ref(
            value.get("obligation_instance_ref"), "universe_obligation_invalid"
        ),
        "valid_from": _optional_datetime(
            value.get("valid_from"), "universe_obligation_invalid"
        ),
        "valid_until": _optional_datetime(
            value.get("valid_until"), "universe_obligation_invalid"
        ),
        "revoked_at": _optional_datetime(
            value.get("revoked_at"), "universe_obligation_invalid"
        ),
    }
    if (
        record["valid_from"] is not None
        and record["valid_until"] is not None
        and record["valid_until"] < record["valid_from"]
    ):
        raise RequiredAssessmentError("universe_obligation_invalid")
    return record


def _normalize_obligation(obligation):
    if not isinstance(obligation, dict):
        raise RequiredAssessmentError("universe_obligation_invalid")
    kind = obligation.get("kind")
    if kind not in OBLIGATION_KINDS:
        raise RequiredAssessmentError("universe_obligation_invalid")
    applicability = obligation.get("applicability")
    if applicability not in APPLICABILITIES:
        raise RequiredAssessmentError("universe_obligation_invalid")
    record = {
        "obligation_ref": _obligation_ref(
            obligation.get("obligation_ref"), "universe_obligation_invalid"
        ),
        "kind": kind,
        "target_ref": _text(
            obligation.get("target_ref"), "universe_obligation_invalid"
        ),
        "action": _text(obligation.get("action"), "universe_obligation_invalid"),
        "phase": _optional_text(obligation.get("phase"), "universe_obligation_invalid"),
        "candidate_digest": _optional_internal_ref(
            obligation.get("candidate_digest"), "universe_obligation_invalid"
        ),
        "source_digest": _optional_internal_ref(
            obligation.get("source_digest"), "universe_obligation_invalid"
        ),
        "applicability": applicability,
        "reason": _optional_text(
            obligation.get("reason"), "universe_obligation_invalid"
        ),
        "basis_ref": _optional_evidence_ref(
            obligation.get("applicability_basis_ref"),
            "universe_obligation_invalid",
        ),
        "applicability_authorization": _normalize_applicability_authorization(
            obligation.get("applicability_authorization")
        ),
        "due_phase": _optional_text(
            obligation.get("due_phase"), "universe_obligation_invalid"
        ),
        "due_at": _optional_datetime(
            obligation.get("due_at"), "universe_obligation_invalid"
        ),
        "expected_check_refs": [],
        "check_names": [],
        "required_subject_fields": [],
        "verifier_ref": None,
        "verifier_authorization_ref": None,
        "verification_rule_ref": None,
    }
    subject_fields = obligation.get("required_subject_fields", [])
    if not isinstance(subject_fields, list):
        raise RequiredAssessmentError("universe_obligation_invalid")
    for field in subject_fields:
        value = _text(field, "universe_obligation_invalid")
        if (
            value not in CANDIDATE_IDENTITY_FIELDS
            or value in record["required_subject_fields"]
        ):
            raise RequiredAssessmentError("universe_obligation_invalid")
        record["required_subject_fields"].append(value)
    refs = obligation.get("expected_check_refs", [])
    if not isinstance(refs, list):
        raise RequiredAssessmentError("universe_obligation_invalid")
    seen = set()
    for ref in refs:
        value = _internal_ref(ref, "universe_obligation_invalid")
        if value not in seen:
            seen.add(value)
            record["expected_check_refs"].append(value)
    names = obligation.get("check_names", [])
    if not isinstance(names, list):
        raise RequiredAssessmentError("universe_obligation_invalid")
    record["check_names"] = [
        _text(name, "universe_obligation_invalid") for name in names
    ]
    if kind == "check":
        if not record["expected_check_refs"] or record["required_subject_fields"]:
            raise RequiredAssessmentError("universe_obligation_invalid")
    else:
        if record["expected_check_refs"]:
            raise RequiredAssessmentError("universe_obligation_invalid")
        record["verifier_ref"] = _optional_evidence_ref(
            obligation.get("verifier_ref"), "universe_obligation_invalid"
        )
        record["verifier_authorization_ref"] = _optional_evidence_ref(
            obligation.get("verifier_authorization_ref"),
            "universe_obligation_invalid",
        )
        record["verification_rule_ref"] = _optional_evidence_ref(
            obligation.get("verification_rule_ref"), "universe_obligation_invalid"
        )
    record["instance_ref"] = obligation_instance_ref(record)
    return record


def _validate_universe(universe):
    """Validate shape/digests and normalize the authorized universe projection."""
    if not isinstance(universe, dict):
        raise RequiredAssessmentError("universe_invalid")
    if universe.get("kind") != UNIVERSE_KIND:
        raise RequiredAssessmentError("universe_kind_invalid")
    if universe.get("schema_version") != UNIVERSE_VERSION:
        raise RequiredAssessmentError("universe_schema_unsupported")
    normalized = {
        "profile": _normalize_profile(universe),
        "authority": _normalize_authority(universe.get("authority")),
        "governance_domain": _normalize_governance(universe.get("governance_domain")),
        "evaluation": _normalize_evaluation(universe.get("evaluation")),
        "check_domain": _normalize_check_domain(universe.get("check_domain")),
        "contract_sample": _flag(
            universe.get("contract_sample", False), "universe_invalid"
        ),
    }
    status = universe.get("universe_status")
    if status not in UNIVERSE_STATUSES:
        raise RequiredAssessmentError("universe_invalid")
    normalized["universe_status"] = status
    normalized["reason"] = _optional_text(universe.get("reason"), "universe_invalid")
    obligations = universe.get("obligations")
    if not isinstance(obligations, list):
        raise RequiredAssessmentError("universe_invalid")
    normalized_obligations = []
    seen = set()
    for obligation in obligations:
        record = _normalize_obligation(obligation)
        if record["instance_ref"] in seen:
            raise RequiredAssessmentError("universe_obligation_duplicate")
        seen.add(record["instance_ref"])
        normalized_obligations.append(record)
    normalized["obligations"] = normalized_obligations
    stored_ref = _internal_ref(universe.get("universe_ref"), "universe_ref_invalid")
    payload = {key: value for key, value in universe.items() if key != "universe_ref"}
    if _digest(payload) != stored_ref:
        raise RequiredAssessmentError("universe_ref_mismatch")
    normalized["universe_ref"] = stored_ref
    return normalized


def _phase_is_current(obligation, current_phase):
    return obligation["phase"] is None or obligation["phase"] == current_phase


def _phase_position(phase, current_phase, phase_order):
    """Classify a declared phase against the authorized phase order.

    A different phase is not automatically a future phase: without an
    authorized order proving the relative position, the result is ``unknown``
    and the caller must fail closed instead of deferring the obligation.
    """
    if phase is None or phase == current_phase:
        return "current"
    if phase_order is None:
        return "unknown"
    phases = phase_order["phases"]
    if current_phase not in phases or phase not in phases:
        return "unknown"
    if phases.index(phase) > phases.index(current_phase):
        return "future"
    return "past"


def _normalized_source_digest(value):
    if not isinstance(value, str) or not value:
        return None
    if _SHA256_REF_RE.fullmatch(value):
        return value
    if _SHA256_RE.fullmatch(value):
        return "sha256:" + value
    return None


def _expected_event_refs(event_ref):
    refs = set()
    for key in ("base_ref", "head_ref", "remote_ref", "local_ref"):
        value = event_ref.get(key)
        if isinstance(value, str) and value:
            refs.add(value)
    references = event_ref.get("references")
    if isinstance(references, list):
        for ref in references:
            if not isinstance(ref, dict):
                continue
            for key in ("remote_ref", "local_ref"):
                value = ref.get(key)
                if isinstance(value, str) and value:
                    refs.add(value)
    return refs


def _target_in_scope(target, patterns):
    for pattern in patterns:
        if pattern == target:
            return True
        if (
            isinstance(pattern, str)
            and pattern.endswith("/*")
            and target.startswith(pattern[:-1])
        ):
            return True
    return False


def _scope_ref_binding(scope):
    """The actual (target_ref, ref_action) scope of the selected Expected scope."""
    if scope.get("per_ref"):
        ref_meta = scope.get("ref") or {}
        return (
            ref_meta.get("remote_ref")
            or ref_meta.get("local_ref")
            or ref_meta.get("source_ref"),
            ref_meta.get("ref_action"),
        )
    event = scope["event_ref"]
    return event.get("base_ref") or event.get("head_ref"), event.get("ref_action")


def _authorized_check_waivers(scope):
    """Recompute the authorized (check_name, obligation_ref) waiver map.

    This reuses the existing #66-B2 EffectiveProfile waiver evaluation for the
    actual Target/Action/Phase scope; R1 does not invent a parallel policy.
    """
    target_ref, ref_action = _scope_ref_binding(scope)
    try:
        *_, waivers, _ = expected_checks._parse_profile_obligations(
            scope["effective_profile"],
            hygiene_rule=scope.get("hygiene_rule_ref"),
            sources_rule=scope.get("sources_rule_ref"),
            selection_rule=scope.get("selection_rule_ref"),
            weaver_rule=scope.get("selection_rule_ref"),
            target_ref=target_ref,
            ref_action=ref_action,
        )
    except expected_checks.ExpectedError as error:
        raise RequiredAssessmentError(
            "expected_profile_invalid", native_code=error.code
        ) from None
    return waivers


def _cross_check_universe(universe, scope, checks):
    """Cross-check the universe against the Expected Check domain.

    Returns stable reason codes for semantic gaps in the supplied universe.
    These fail closed but never erase an independently proven per-Check fact.
    """
    reasons = []
    profile = scope["effective_profile"]
    profile_binding = {
        "profile_ref": profile.get("profile_ref"),
        "profile_revision": profile.get("profile_revision"),
        "approval_source": profile.get("approval_source"),
        "profile_sha256": profile.get("profile_sha256"),
        "approval_digest": (profile.get("approval_binding") or {}).get(
            "approval_digest"
        ),
    }
    if universe["profile"] != profile_binding:
        reasons.append("profile_binding_mismatch")
    check_domain = universe["check_domain"]
    if check_domain["scope_id"] != scope["scope_id"]:
        reasons.append("check_domain_scope_mismatch")
    if check_domain["expected_set_ref"] != scope["expected_set_ref"]:
        reasons.append("check_domain_expected_set_mismatch")
    if check_domain["status"] != scope["status"]:
        reasons.append("check_domain_status_mismatch")
    declared = check_domain.get("consumer_completeness")
    if (
        declared is not None
        and scope.get("consumer_completeness") is not None
        and declared != scope["consumer_completeness"]
    ):
        reasons.append("check_domain_completeness_mismatch")

    evaluation = universe["evaluation"]
    refs = {item["target_ref"] for item in evaluation["refs"]}
    candidate = evaluation.get("candidate") or {}
    candidate_values = set(candidate.values())
    expected_refs = {entry["expected_check_ref"]: entry for entry in checks}
    current_phase = evaluation["phase"]

    # Bind the evaluation input to the authorized Profile, Expected Event and
    # checked Source. A self-consistent hash over a different evaluation scope
    # is not the same evaluation.
    if evaluation["context"] != profile.get("context"):
        reasons.append("evaluation_context_mismatch")
    if evaluation["action"] != profile.get("action"):
        reasons.append("evaluation_action_mismatch")
    targets = profile.get("targets")
    if evaluation["target"] != profile.get("target") or (
        isinstance(targets, list)
        and targets
        and not _target_in_scope(evaluation["target"], targets)
    ):
        reasons.append("evaluation_target_mismatch")
    if profile.get("phase") is not None and evaluation["phase"] != profile["phase"]:
        reasons.append("evaluation_phase_mismatch")
    event_refs = _expected_event_refs(scope["event_ref"])
    if event_refs:
        for item in evaluation["refs"]:
            if item["target_ref"] not in event_refs:
                reasons.append("evaluation_ref_not_in_expected_event")
    event_action = scope["event_ref"].get("ref_action")
    if event_action is not None:
        for item in evaluation["refs"]:
            if item["ref_action"] != event_action:
                reasons.append("evaluation_ref_action_mismatch")
    scope_target_ref, scope_ref_action = _scope_ref_binding(scope)
    if scope.get("per_ref"):
        exact = [
            item
            for item in evaluation["refs"]
            if item["target_ref"] == scope_target_ref
            and item["ref_action"] == scope_ref_action
        ]
        target_matches = [
            item
            for item in evaluation["refs"]
            if item["target_ref"] == scope_target_ref
        ]
        if len(evaluation["refs"]) != 1 or len(exact) != 1:
            reasons.append(
                "evaluation_ref_action_mismatch"
                if target_matches and not exact
                else "evaluation_ref_scope_mismatch"
            )
    expected_source_digest = _normalized_source_digest(
        scope["source_ref"].get("content_digest")
    )

    # Membership/closure is reconciled against the authorized Profile's
    # Action x Target x Phase applicable scope (_obligation_matches_scope):
    # only obligations explicitly scoped by Profile to a provably future phase
    # may appear as future-only in the universe without a current-phase
    # instance. An obligation covering the current phase (including one with no
    # phases[] restriction) cannot be satisfied solely by a future instance.
    phase_order = evaluation.get("phase_order")
    profile_current_check_keys = set()
    profile_current_non_check_keys = set()
    profile_other_phase_check_keys = set()
    profile_other_phase_non_check_keys = set()
    for raw_ob in profile.get("required_obligations", ()):
        if not isinstance(raw_ob, dict) or raw_ob.get("obligation_ref") is None:
            continue
        ob_key = _canonical(raw_ob["obligation_ref"])
        ob_kind = raw_ob.get("kind", "check")
        if expected_checks._obligation_matches_scope(
            raw_ob,
            target_ref=scope_target_ref,
            ref_action=scope_ref_action,
            phase=current_phase,
        ):
            if ob_kind == "non_check":
                profile_current_non_check_keys.add(ob_key)
            else:
                profile_current_check_keys.add(ob_key)
        elif isinstance(raw_ob.get("phases"), list) and any(
            expected_checks._obligation_matches_scope(
                raw_ob,
                target_ref=scope_target_ref,
                ref_action=scope_ref_action,
                phase=declared_phase,
            )
            and _phase_position(declared_phase, current_phase, phase_order) == "future"
            for declared_phase in raw_ob["phases"]
        ):
            if ob_kind == "non_check":
                profile_other_phase_non_check_keys.add(ob_key)
            else:
                profile_other_phase_check_keys.add(ob_key)
    profile_future_only_check_keys = (
        profile_other_phase_check_keys - profile_current_check_keys
    )
    profile_future_only_non_check_keys = (
        profile_other_phase_non_check_keys - profile_current_non_check_keys
    )

    required_by_ref = {
        ref: {_canonical(value) for value in entry.get("required_by_obligations") or ()}
        for ref, entry in expected_refs.items()
    }
    universe_associations = {}
    current_universe_associations = {}
    universe_non_check_keys = set()
    current_universe_non_check_keys = set()
    non_check_expected_keys = {
        _canonical(item.get("obligation_ref"))
        for item in scope["non_check_obligations"]
        if isinstance(item, dict) and item.get("status") == "required"
    }
    for obligation in universe["obligations"]:
        if obligation["target_ref"] not in refs:
            reasons.append("obligation_target_out_of_scope")
        if obligation["action"] != evaluation["action"]:
            reasons.append("obligation_action_out_of_scope")
        if obligation["candidate_digest"] is not None:
            if not candidate_values:
                reasons.append("obligation_candidate_identity_missing")
            elif obligation["candidate_digest"] not in candidate_values:
                reasons.append("obligation_candidate_out_of_scope")
        if obligation["source_digest"] is not None:
            candidate_source = candidate.get("source_digest")
            if expected_source_digest is None or candidate_source is None:
                reasons.append("obligation_source_identity_missing")
            elif (
                obligation["source_digest"] != candidate_source
                or obligation["source_digest"] != expected_source_digest
            ):
                reasons.append("obligation_source_out_of_scope")
        current = _phase_is_current(obligation, current_phase)
        if obligation["kind"] == "check":
            key = _canonical(obligation["obligation_ref"])
            for ref in obligation["expected_check_refs"]:
                entry = expected_refs.get(ref)
                if entry is None:
                    # Only a current Required Check instance must exist in this
                    # Expected domain; a future/inapplicable instance may fall
                    # outside the current projection.
                    if current and obligation["applicability"] == "required":
                        reasons.append("universe_check_instance_unknown")
                    continue
                universe_associations.setdefault(ref, set()).add(key)
                if current:
                    current_universe_associations.setdefault(ref, set()).add(key)
                    if (
                        obligation["applicability"] == "required"
                        and key not in required_by_ref[ref]
                    ):
                        reasons.append("universe_obligation_mapping_mismatch")
        else:
            key = _canonical(obligation["obligation_ref"])
            universe_non_check_keys.add(key)
            if current:
                current_universe_non_check_keys.add(key)
            else:
                continue
            if (
                obligation["applicability"] == "required"
                and key not in non_check_expected_keys
            ):
                reasons.append("non_check_obligation_not_in_expected")
    for ref, expected_keys in required_by_ref.items():
        if not expected_keys:
            continue
        present = universe_associations.get(ref, set())
        if not present:
            reasons.append("required_check_omitted_by_universe")
            continue
        if expected_keys - present:
            reasons.append("required_check_association_unresolved")
            continue
        present_current = current_universe_associations.get(ref, set())
        missing_current = {
            key
            for key in expected_keys
            if key not in profile_future_only_check_keys and key not in present_current
        }
        if missing_current:
            reasons.append("required_check_omitted_by_universe")
    current_check_keys_in_universe = {
        key for keys in current_universe_associations.values() for key in keys
    }
    if profile_current_check_keys - current_check_keys_in_universe:
        reasons.append("required_check_omitted_by_universe")
    for _key in non_check_expected_keys - universe_non_check_keys:
        reasons.append("non_check_obligation_omitted_by_universe")
    missing_current_non_check = (
        {
            key
            for key in non_check_expected_keys
            if key not in profile_future_only_non_check_keys
        }
        | profile_current_non_check_keys
    ) - current_universe_non_check_keys
    if missing_current_non_check:
        reasons.append("non_check_obligation_omitted_by_universe")
    return _dedupe(reasons)


def _applicable_to(value, code):
    if not isinstance(value, dict):
        raise RequiredAssessmentError(code)
    return {
        "target_ref": _text(value.get("target_ref"), code),
        "phase": _text(value.get("phase"), code),
        "action": _text(value.get("action"), code),
        "candidate_digest": _optional_internal_ref(value.get("candidate_digest"), code),
        "rule_ref": _optional_evidence_ref(value.get("rule_ref"), code),
        "profile_sha256": _optional_internal_ref(value.get("profile_sha256"), code),
    }


def _applicable_to_matches(applicable_to, *, universe, entry, scope, current_phase):
    """Return a stable reason code when an admission fact is out of scope."""
    evaluation = universe["evaluation"]
    refs = {item["target_ref"] for item in evaluation["refs"]}
    if applicable_to["target_ref"] not in refs:
        return "admission_target_out_of_scope"
    if applicable_to["phase"] != current_phase:
        return "admission_phase_mismatch"
    if applicable_to["action"] != evaluation["action"]:
        return "admission_action_mismatch"
    candidate_values = set((evaluation.get("candidate") or {}).values())
    if (
        applicable_to["candidate_digest"] is not None
        and applicable_to["candidate_digest"] not in candidate_values
    ):
        return "admission_candidate_mismatch"
    if applicable_to["rule_ref"] is not None and (
        entry.get("rule_ref") is None
        or _canonical(applicable_to["rule_ref"]) != _canonical(entry["rule_ref"])
    ):
        return "admission_rule_mismatch"
    profile_sha256 = (scope["effective_profile"] or {}).get("profile_sha256")
    if (
        applicable_to["profile_sha256"] is not None
        and applicable_to["profile_sha256"] != profile_sha256
    ):
        return "admission_profile_mismatch"
    return None


def _origin_trace(value, code, *, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, dict):
        raise RequiredAssessmentError(code)
    return {
        "run_ref": _text(value.get("run_ref"), code),
        "attempt_ref": _text(value.get("attempt_ref"), code),
        "runner_ref": _text(value.get("runner_ref"), code),
        "result_origin": _text(value.get("result_origin"), code),
    }


def _coverage_entry(key, value):
    code = "admission_coverage_invalid"
    if not isinstance(value, dict):
        raise RequiredAssessmentError(code)
    scope_id = _internal_ref(value.get("scope_id"), code)
    expected_check_ref = _internal_ref(value.get("expected_check_ref"), code)
    if key != f"{scope_id}/{expected_check_ref}":
        raise RequiredAssessmentError("admission_instance_key_invalid")
    admission_status = value.get("admission_status")
    if admission_status not in ADMISSION_STATUSES:
        raise RequiredAssessmentError(code)
    source_kind = value.get("source_kind")
    if source_kind is not None and source_kind not in SOURCE_KINDS:
        raise RequiredAssessmentError(code)
    record = {
        "scope_id": scope_id,
        "expected_check_ref": expected_check_ref,
        "check_name": _optional_text(value.get("check_name"), code),
        "admission_status": admission_status,
        "source_kind": source_kind,
        "witness_result_id": _optional_internal_ref(
            value.get("witness_result_id"), code
        ),
        "raw_result_verified": _flag(value.get("raw_result_verified", False), code),
        "issuer_verified": _flag(value.get("issuer_verified", False), code),
        "origin_trace": _origin_trace(value.get("origin_trace"), code, required=False),
        "applicable_to": _applicable_to(value.get("applicable_to"), code),
        "receipt_binding_status": _optional_text(
            value.get("receipt_binding_status"), code
        ),
        "equivalence_basis": value.get("equivalence_basis"),
        "reason": _optional_text(value.get("reason"), code),
    }
    if admission_status == "admitted":
        if record["source_kind"] is None:
            raise RequiredAssessmentError(code)
        if record["witness_result_id"] is None:
            raise RequiredAssessmentError(code)
        if record["origin_trace"] is None:
            raise RequiredAssessmentError(code)
        if record["source_kind"] == "historical_reuse" and not (
            isinstance(record["equivalence_basis"], dict)
            and record["equivalence_basis"]
        ):
            raise RequiredAssessmentError("admission_equivalence_missing")
    if (
        record["receipt_binding_status"] is not None
        and record["receipt_binding_status"] not in reconciliation.ENTRY_STATUSES
    ):
        raise RequiredAssessmentError(code)
    return record


def _failure_entry(value):
    code = "admission_failure_invalid"
    if not isinstance(value, dict):
        raise RequiredAssessmentError(code)
    failure_kind = value.get("failure_kind")
    if failure_kind not in FAILURE_KINDS:
        raise RequiredAssessmentError(code)
    issued_at = value.get("issued_at")
    _optional_datetime(issued_at, code)
    return {
        "result_id": _internal_ref(value.get("result_id"), code),
        "failure_kind": failure_kind,
        "native_status": _optional_text(value.get("native_status"), code),
        "origin_trace": _origin_trace(value.get("origin_trace"), code, required=False),
        "applicable_to": _applicable_to(value.get("applicable_to"), code),
        "issued_at": _optional_text(issued_at, code),
        "reason": _optional_text(value.get("reason"), code),
    }


def _adjudication_entry(value, *, scope_id, expected_refs):
    code = "admission_adjudication_invalid"
    if not isinstance(value, dict):
        raise RequiredAssessmentError(code)
    entry_scope = _internal_ref(value.get("scope_id"), code)
    entry_ref = _internal_ref(value.get("expected_check_ref"), code)
    if entry_scope != scope_id or entry_ref not in expected_refs:
        raise RequiredAssessmentError(code)
    disposition = value.get("disposition")
    if disposition not in ADJUDICATION_DISPOSITIONS:
        raise RequiredAssessmentError(code)
    applies_to = value.get("applies_to_result_ids")
    if applies_to is None:
        applies_to = []
    if not isinstance(applies_to, list):
        raise RequiredAssessmentError(code)
    normalized_applies = [_internal_ref(result_id, code) for result_id in applies_to]
    decided_at = _datetime(value.get("decided_at"), code)
    valid_until = _optional_datetime(value.get("valid_until"), code)
    if valid_until is not None and valid_until < decided_at:
        raise RequiredAssessmentError(code)
    if disposition != "unresolved" and not normalized_applies:
        raise RequiredAssessmentError(code)
    return {
        "scope_id": entry_scope,
        "expected_check_ref": entry_ref,
        "disposition": disposition,
        "authority_ref": _evidence_ref(value.get("authority_ref"), code),
        "policy_ref": _evidence_ref(value.get("policy_ref"), code),
        "decided_at": decided_at,
        "valid_until": valid_until,
        "revoked_at": _optional_datetime(value.get("revoked_at"), code),
        "applies_to_result_ids": normalized_applies,
        "reason": _optional_text(value.get("reason"), code),
    }



def project_t1_t2_gate_admission(
    source,
    *,
    expected,
    current_authorization,
    source_authority,
    receipt=None,
    contract_sample=False,
    ref_scope_id=None,
):
    """Project an independently authenticated #63 T1-a/T2 result to R1.

    This is a scope-preserving data contract, not a source authenticator.
    The caller supplies an independent verifier of the original, complete
    admission object.  Native failure, missing proof and remaining work never
    become admitted coverage solely because a digest can be computed.
    """
    from . import admission as evidence_admission

    code = "admission_projection_invalid"
    if not isinstance(source, dict):
        raise RequiredAssessmentError(code)
    kind = source.get("kind")
    if kind not in {
        evidence_admission.ADMISSION_KIND,
        evidence_admission.HISTORICAL_REUSE_KIND,
    } or source.get("schema_version") != 1:
        raise RequiredAssessmentError("admission_projection_kind_invalid")
    if source.get("evidence_admission") != "evaluated" or (
        source.get("required_gate") != "not_evaluated"
    ):
        raise RequiredAssessmentError(code)

    scope = _select_expected_scope(expected, ref_scope_id)
    checks = _verify_expected_scope(scope)
    scope_id = scope["scope_id"]
    expected_set_ref = scope["expected_set_ref"]
    if (
        source.get("scope_id") != scope_id
        or source.get("expected_set_ref") != expected_set_ref
    ):
        raise RequiredAssessmentError("admission_scope_mismatch")

    if not isinstance(current_authorization, dict):
        raise RequiredAssessmentError("admission_projection_context_invalid")
    authorization = {
        "target_ref": _text(
            current_authorization.get("target_ref"),
            "admission_projection_context_invalid",
        ),
        "phase": _text(
            current_authorization.get("phase"),
            "admission_projection_context_invalid",
        ),
        "action": _text(
            current_authorization.get("action"),
            "admission_projection_context_invalid",
        ),
        "candidate_digest": _optional_internal_ref(
            current_authorization.get("candidate_digest"),
            "admission_projection_context_invalid",
        ),
    }
    historical = kind == evidence_admission.HISTORICAL_REUSE_KIND
    if historical:
        declared = source.get("current_authorization")
        if not isinstance(declared, dict) or any(
            declared.get(k) != authorization[k] for k in authorization
        ):
            raise RequiredAssessmentError("admission_authorization_mismatch")
        expected_profile = (scope.get("effective_profile") or {}).get(
            "profile_sha256"
        )
        if declared.get("profile_sha256") != expected_profile:
            raise RequiredAssessmentError("admission_authorization_mismatch")
        if source.get("receipt_ref") is not None:
            raise RequiredAssessmentError(code)
    elif receipt is not None:
        if not isinstance(receipt, dict) or (
            source.get("receipt_ref") != receipt.get("receipt_ref")
        ):
            raise RequiredAssessmentError("admission_receipt_scope_mismatch")

    if not callable(source_authority):
        raise RequiredAssessmentError("admission_source_authority_missing")
    verification = {
        "source_admission": copy.deepcopy(source),
        "current_authorization": copy.deepcopy(authorization),
        "scope_id": scope_id,
        "expected_set_ref": expected_set_ref,
    }
    try:
        authenticated = source_authority(verification)
    except (KeyError, TypeError, ValueError, RuntimeError):
        raise RequiredAssessmentError("admission_source_authority_error") from None
    if authenticated is not True:
        raise RequiredAssessmentError("admission_source_authority_unverified")

    source_entries = source.get("entries")
    source_remaining = source.get("remaining_work")
    if not isinstance(source_remaining, list):
        raise RequiredAssessmentError("admission_projection_remaining_invalid")
    indexed_remaining = {}
    for item in source_remaining:
        if not isinstance(item, dict):
            raise RequiredAssessmentError("admission_projection_remaining_invalid")
        ref = item.get("expected_check_ref")
        if (
            item.get("scope_id") != scope_id
            or not isinstance(ref, str)
            or ref in indexed_remaining
        ):
            raise RequiredAssessmentError("admission_projection_remaining_invalid")
        indexed_remaining[ref] = item
    if not isinstance(source_entries, list):
        raise RequiredAssessmentError(code)
    check_by_ref = {check["expected_check_ref"]: check for check in checks}
    indexed = {}
    for entry in source_entries:
        if not isinstance(entry, dict):
            raise RequiredAssessmentError(code)
        ref = entry.get("expected_check_ref")
        if (
            ref not in check_by_ref
            or ref in indexed
            or entry.get("scope_id") != scope_id
        ):
            raise RequiredAssessmentError("admission_projection_entry_mismatch")
        indexed[ref] = entry
    if set(indexed) != set(check_by_ref):
        raise RequiredAssessmentError("admission_projection_entry_mismatch")

    raw_coverage = source.get("accepted_coverage_by_instance")
    if not isinstance(raw_coverage, dict) or set(raw_coverage) != {
        f"{scope_id}/{ref}" for ref in check_by_ref
    }:
        raise RequiredAssessmentError("admission_projection_coverage_mismatch")

    source_ref = _digest(source)
    profile_sha256 = (scope.get("effective_profile") or {}).get("profile_sha256")
    coverage = {}
    failures = {}
    remaining = []
    for ref, check in check_by_ref.items():
        entry = indexed[ref]
        key = f"{scope_id}/{ref}"
        record = raw_coverage[key]
        if not isinstance(record, dict) or (
            record.get("admission_status") != entry.get("status")
        ):
            raise RequiredAssessmentError("admission_projection_coverage_mismatch")
        admitted = entry.get("admitted") is True
        if admitted != (entry.get("status") == "admitted"):
            raise RequiredAssessmentError("admission_projection_coverage_mismatch")
        if admitted and (
            entry.get("origin_status") != "verified"
            or entry.get("obligation_status") != "satisfied"
        ):
            raise RequiredAssessmentError("admission_projection_coverage_mismatch")
        ids = entry.get("result_ids")
        if (
            not isinstance(ids, list)
            or any(not isinstance(value, str) for value in ids)
            or len(ids) != len(set(ids))
        ):
            raise RequiredAssessmentError("admission_projection_result_invalid")
        if record.get("result_ids") != ids:
            raise RequiredAssessmentError("admission_projection_coverage_mismatch")
        if admitted and ref in indexed_remaining:
            raise RequiredAssessmentError("admission_admitted_but_remaining")
        applicable_to = {
            **authorization,
            "rule_ref": copy.deepcopy(check.get("rule_ref")),
            "profile_sha256": profile_sha256,
        }
        witness = None
        trace = None
        basis = None
        if historical:
            origins = entry.get("result_origins")
            if not isinstance(origins, list):
                raise RequiredAssessmentError("admission_projection_origin_invalid")
            by_id = {}
            for origin in origins:
                if not isinstance(origin, dict):
                    raise RequiredAssessmentError(
                        "admission_projection_origin_invalid"
                    )
                result_id = _internal_ref(
                    origin.get("result_id"),
                    "admission_projection_origin_invalid",
                )
                if result_id in by_id:
                    raise RequiredAssessmentError(
                        "admission_projection_origin_invalid"
                    )
                by_id[result_id] = origin
            basis = entry.get("equivalence_basis")
            if admitted and (
                not ids
                or set(ids) != set(by_id)
                or not isinstance(basis, dict)
                or basis.get("verified") is not True
                or basis.get("check_satisfaction_status") != "satisfied"
            ):
                raise RequiredAssessmentError("admission_equivalence_missing")
            if admitted:
                witness = sorted(ids)[0]
                trace = _origin_trace(
                    by_id[witness], "admission_projection_origin_invalid"
                )
        else:
            facts = entry.get("facts")
            if not isinstance(facts, list):
                raise RequiredAssessmentError("admission_projection_result_invalid")
            selected = {}
            for fact in facts:
                if not isinstance(fact, dict):
                    raise RequiredAssessmentError(
                        "admission_projection_result_invalid"
                    )
                result_id = _internal_ref(
                    fact.get("result_id"), "admission_projection_result_invalid"
                )
                if fact.get("selected") is True:
                    if result_id in selected:
                        raise RequiredAssessmentError(
                            "admission_projection_result_invalid"
                        )
                    selected[result_id] = fact
                if (
                    fact.get("status") == "failed"
                    and fact.get("selected") is True
                    and fact.get("integrity_status") == "verified"
                    and fact.get("issuer_status") == "verified"
                ):
                    failures.setdefault(key, []).append(
                        {
                            "result_id": result_id,
                            "failure_kind": "native_failure",
                            "native_status": "failed",
                            "origin_trace": _origin_trace(
                                fact, "admission_projection_origin_invalid"
                            ),
                            "applicable_to": copy.deepcopy(applicable_to),
                            "issued_at": None,
                            "reason": "authenticated_native_failure",
                        }
                    )
            if admitted:
                if (
                    not ids
                    or set(ids) != set(selected)
                    or entry.get("integrity_status") != "verified"
                    or entry.get("resolution_status") != "verified"
                    or any(
                        fact.get("status") != "passed"
                        or fact.get("integrity_status") != "verified"
                        or fact.get("issuer_status") != "verified"
                        or fact.get("implementation_status") != "approved"
                        for fact in selected.values()
                    )
                ):
                    raise RequiredAssessmentError(
                        "admission_projection_result_invalid"
                    )
                witness = sorted(ids)[0]
                trace = _origin_trace(
                    selected[witness], "admission_projection_origin_invalid"
                )

        reasons = entry.get("reasons") or []
        if not isinstance(reasons, list) or any(
            not isinstance(reason, str) for reason in reasons
        ):
            raise RequiredAssessmentError(code)
        result = {
            "scope_id": scope_id,
            "expected_check_ref": ref,
            "check_name": check.get("name"),
            "admission_status": "admitted" if admitted else "unverified",
            "source_kind": "historical_reuse"
            if historical and admitted
            else "current_run"
            if admitted
            else None,
            "witness_result_id": witness,
            "raw_result_verified": admitted,
            "issuer_verified": admitted,
            "origin_trace": trace,
            "applicable_to": applicable_to,
            "receipt_binding_status": (
                (next(
                    (
                        row.get("binding_status")
                        for row in (receipt or {}).get("entries", [])
                        if row.get("expected_check_ref") == ref
                    ),
                    None,
                ))
                if not historical
                else None
            ),
            "equivalence_basis": copy.deepcopy(basis) if admitted else None,
            "reason": reasons[0] if reasons else None,
        }
        coverage[key] = result
        if not admitted and entry.get("obligation_status") != "not_applicable":
            native_remaining = indexed_remaining.get(ref) or {}
            native_reason = native_remaining.get("reason")
            if native_reason is not None and not isinstance(native_reason, str):
                raise RequiredAssessmentError("admission_projection_remaining_invalid")
            remaining.append(
                {
                    "scope_id": scope_id,
                    "expected_check_ref": ref,
                    "reason": native_reason or reasons[0]
                    if reasons or native_reason
                    else "coverage_not_admitted",
                }
            )
    if set(indexed_remaining) - set(check_by_ref):
        raise RequiredAssessmentError("admission_projection_remaining_invalid")
    projection = {
        "kind": ADMISSION_KIND,
        "schema_version": ADMISSION_VERSION,
        "phase": ADMISSION_PHASE,
        "scope_id": scope_id,
        "expected_set_ref": expected_set_ref,
        "source_admission_ref": source_ref,
        "expected_completeness": scope.get("consumer_completeness"),
        "contract_sample": _flag(
            contract_sample, "admission_projection_sample_invalid"
        ) or _flag(source.get("contract_sample", False), "admission_projection_sample_invalid"),
        "all_observations_reconciled": False,
        "accepted_coverage_by_instance": coverage,
        "authenticated_failures_by_instance": failures,
        "adjudications": [],
        "remaining_work": remaining,
        "required_gate_ready": False,
        "required_gate_block_reason": "required_assessment_not_evaluated",
    }
    projection["admission_ref"] = _digest(projection)
    return projection



def _validate_admission(admission, scope, checks):
    """Validate the #63 trusted admission projection for this exact scope."""
    if not isinstance(admission, dict):
        raise RequiredAssessmentError("admission_invalid")
    if admission.get("kind") != ADMISSION_KIND:
        raise RequiredAssessmentError("admission_kind_invalid")
    if admission.get("schema_version") != ADMISSION_VERSION:
        raise RequiredAssessmentError("admission_schema_unsupported")
    if admission.get("phase") != ADMISSION_PHASE:
        raise RequiredAssessmentError("admission_phase_invalid")
    if (
        _internal_ref(admission.get("scope_id"), "admission_scope_invalid")
        != scope["scope_id"]
    ):
        raise RequiredAssessmentError("admission_scope_mismatch")
    if (
        _internal_ref(admission.get("expected_set_ref"), "admission_scope_invalid")
        != scope["expected_set_ref"]
    ):
        raise RequiredAssessmentError("admission_scope_mismatch")
    stored_ref = _internal_ref(admission.get("admission_ref"), "admission_ref_invalid")
    payload = {key: value for key, value in admission.items() if key != "admission_ref"}
    if _digest(payload) != stored_ref:
        raise RequiredAssessmentError("admission_ref_mismatch")

    expected_refs = {entry["expected_check_ref"] for entry in checks}
    coverage = {}
    raw_coverage = admission.get("accepted_coverage_by_instance", {})
    if not isinstance(raw_coverage, dict):
        raise RequiredAssessmentError("admission_coverage_invalid")
    for key, value in raw_coverage.items():
        if not isinstance(key, str):
            raise RequiredAssessmentError("admission_coverage_invalid")
        record = _coverage_entry(key, value)
        if record["scope_id"] != scope["scope_id"]:
            raise RequiredAssessmentError("admission_scope_mismatch")
        if record["expected_check_ref"] not in expected_refs:
            raise RequiredAssessmentError("admission_check_instance_unknown")
        coverage[record["expected_check_ref"]] = record

    failures = {}
    raw_failures = admission.get("authenticated_failures_by_instance", {})
    if not isinstance(raw_failures, dict):
        raise RequiredAssessmentError("admission_failure_invalid")
    for key, values in raw_failures.items():
        if not isinstance(key, str) or not isinstance(values, list):
            raise RequiredAssessmentError("admission_failure_invalid")
        entry_scope, _, entry_ref = key.partition("/")
        entry_scope = _internal_ref(entry_scope, "admission_instance_key_invalid")
        entry_ref = _internal_ref(entry_ref, "admission_instance_key_invalid")
        if entry_scope != scope["scope_id"] or entry_ref not in expected_refs:
            raise RequiredAssessmentError("admission_scope_mismatch")
        failures.setdefault(entry_ref, []).extend(
            _failure_entry(value) for value in values
        )

    adjudications = {}
    raw_adjudications = admission.get("adjudications", [])
    if not isinstance(raw_adjudications, list):
        raise RequiredAssessmentError("admission_adjudication_invalid")
    for value in raw_adjudications:
        record = _adjudication_entry(
            value, scope_id=scope["scope_id"], expected_refs=expected_refs
        )
        adjudications.setdefault(record["expected_check_ref"], []).append(record)

    remaining = {}
    raw_remaining = admission.get("remaining_work", [])
    if not isinstance(raw_remaining, list):
        raise RequiredAssessmentError("admission_remaining_invalid")
    for value in raw_remaining:
        if not isinstance(value, dict):
            raise RequiredAssessmentError("admission_remaining_invalid")
        entry_scope = _internal_ref(
            value.get("scope_id"), "admission_remaining_invalid"
        )
        entry_ref = _internal_ref(
            value.get("expected_check_ref"), "admission_remaining_invalid"
        )
        if entry_scope != scope["scope_id"] or entry_ref not in expected_refs:
            raise RequiredAssessmentError("admission_scope_mismatch")
        remaining[entry_ref] = _optional_text(
            value.get("reason"), "admission_remaining_invalid"
        )

    return {
        "admission_ref": stored_ref,
        "contract_sample": _flag(
            admission.get("contract_sample", False), "admission_invalid"
        ),
        "expected_completeness": _optional_text(
            admission.get("expected_completeness"), "admission_invalid"
        ),
        "all_observations_reconciled": _flag(
            admission.get("all_observations_reconciled", False), "admission_invalid"
        ),
        "coverage": coverage,
        "failures": failures,
        "adjudications": adjudications,
        "remaining": remaining,
    }


def _validate_receipt(receipt, scope):
    """Validate the #66-C structural receipt and index its entries by instance."""
    try:
        reconciliation.validate_receipt(receipt)
    except reconciliation.ReconciliationError as error:
        raise RequiredAssessmentError(
            "receipt_invalid", native_code=error.code
        ) from None
    if receipt.get("scope_id") != scope["scope_id"]:
        raise RequiredAssessmentError("receipt_scope_mismatch")
    if receipt.get("expected_set_ref") != scope["expected_set_ref"]:
        raise RequiredAssessmentError("receipt_scope_mismatch")
    entries = {}
    for entry in receipt.get("entries") or []:
        if not isinstance(entry, dict):
            raise RequiredAssessmentError("receipt_entry_invalid")
        ref = _internal_ref(entry.get("expected_check_ref"), "receipt_entry_invalid")
        if ref in entries:
            raise RequiredAssessmentError("receipt_entry_duplicate")
        entries[ref] = entry
    return {
        "receipt_ref": _internal_ref(receipt.get("receipt_ref"), "receipt_ref_invalid"),
        "status": receipt.get("status"),
        "expected_completeness": receipt.get("expected_completeness"),
        "all_observations_reconciled": bool(receipt.get("all_observations_reconciled")),
        "entries": entries,
    }


def _subject_ref(value, code):
    if not isinstance(value, dict):
        raise RequiredAssessmentError(code)
    record = {}
    for key in CANDIDATE_IDENTITY_FIELDS:
        field = value.get(key)
        if field is not None:
            record[key] = _text(field, code)
    if not record:
        raise RequiredAssessmentError(code)
    return record


def _validate_non_check_entry(value):
    code = "non_check_evidence_invalid"
    if not isinstance(value, dict):
        raise RequiredAssessmentError(code)
    record = {
        "obligation_ref": _obligation_ref(value.get("obligation_ref"), code),
        "kind": "non_check",
        "target_ref": _text(value.get("target_ref"), code),
        "action": _text(value.get("action"), code),
        "phase": _optional_text(value.get("phase"), code),
        "candidate_digest": _optional_internal_ref(value.get("candidate_digest"), code),
        "source_digest": _optional_internal_ref(value.get("source_digest"), code),
        "verifier_ref": _evidence_ref(value.get("verifier_ref"), code),
        "verifier_authorization_ref": _evidence_ref(
            value.get("verifier_authorization_ref"), code
        ),
        "verification_rule_ref": _evidence_ref(
            value.get("verification_rule_ref"), code
        ),
        "result": value.get("result"),
        "reason": _optional_text(value.get("reason"), code),
        "evaluated_at": _datetime(value.get("evaluated_at"), code),
        "valid_until": _optional_datetime(value.get("valid_until"), code),
        "revoked_at": _optional_datetime(value.get("revoked_at"), code),
        "subject_ref": _subject_ref(value.get("subject_ref"), code),
        "evidence_ref": _evidence_ref(value.get("evidence_ref"), code),
    }
    if record["result"] not in NON_CHECK_RESULTS:
        raise RequiredAssessmentError(code)
    record["instance_ref"] = obligation_instance_ref(record)
    declared_ref = _optional_internal_ref(value.get("instance_ref"), code)
    if declared_ref is not None and declared_ref != record["instance_ref"]:
        raise RequiredAssessmentError("non_check_instance_ref_mismatch")
    return record


def _validate_non_check_evidence(document):
    if not isinstance(document, dict):
        raise RequiredAssessmentError("non_check_evidence_invalid")
    if document.get("kind") != NON_CHECK_EVIDENCE_KIND:
        raise RequiredAssessmentError("non_check_evidence_kind_invalid")
    if document.get("schema_version") != NON_CHECK_EVIDENCE_VERSION:
        raise RequiredAssessmentError("non_check_evidence_schema_unsupported")
    entries = document.get("entries")
    if not isinstance(entries, list):
        raise RequiredAssessmentError("non_check_evidence_invalid")
    return {
        "entries": [_validate_non_check_entry(value) for value in entries],
        "contract_sample": _flag(
            document.get("contract_sample", False), "non_check_evidence_invalid"
        ),
    }


def _authority_fact(universe, evaluated_at):
    """Return (verified, reason codes) for the Profile authority fact."""
    authority = universe["authority"]
    reasons = []
    if authority["status"] != "verified":
        reasons.append("profile_authority_unverified")
    if evaluated_at < authority["evaluated_at"]:
        reasons.append("authority_evaluated_after_assessment")
    if evaluated_at < authority["effective_at"]:
        reasons.append("profile_not_yet_effective")
    if authority["valid_from"] is not None and evaluated_at < authority["valid_from"]:
        reasons.append("profile_not_yet_effective")
    if authority["valid_until"] is not None and evaluated_at > authority["valid_until"]:
        reasons.append("profile_expired")
    if authority["revoked_at"] is not None:
        reasons.append("profile_revoked")
    if (
        universe["profile"]["approval_source"]
        not in expected_checks.AUTHORITATIVE_APPROVAL_SOURCES
    ):
        reasons.append("profile_approval_source_untrusted")
    reasons = _dedupe(reasons)
    return not reasons, reasons


def _check_domain_fact(scope, checks):
    reasons = []
    if scope["status"] != "complete":
        reasons.append("expected_check_domain_incomplete")
    if scope.get("conflicts"):
        reasons.append("expected_check_conflicts")
    if scope.get("authority_status") != "verified":
        reasons.append("expected_authority_unverified")
    if scope.get("phase") is not None:
        # #66-B2 currently produces only phase_status="unverified" because no
        # trusted lifecycle Phase Producer exists yet. Self-reported "verified"
        # strings without an authenticated Phase Producer proof contract must
        # fail closed rather than granting phase authorization.
        reasons.append(
            "expected_phase_unverified"
            if scope.get("phase_status") == "unverified"
            else "expected_phase_proof_unverified"
        )
    if scope["status"] == "complete":
        expected_label = (
            "per_ref" if scope.get("per_ref") else "expected_domain_complete"
        )
        if scope.get("consumer_completeness") != expected_label:
            reasons.append("expected_consumer_completeness_unproven")
    for entry in checks:
        if not entry.get("required_by_obligations"):
            continue
        if entry.get("definition_status") != "resolved":
            reasons.append("expected_check_definition_unresolved")
        if entry.get("rule_status") != "resolved":
            reasons.append("expected_check_rule_unresolved")
    reasons = _dedupe(reasons)
    return not reasons, reasons


def _universe_fact(universe, cross_check_reasons):
    reasons = list(cross_check_reasons)
    if universe["universe_status"] != "complete":
        reasons.append(f"universe_{universe['universe_status']}")
    governance = universe["governance_domain"]
    if governance["closure_status"] != "closed":
        reasons.append("governance_domain_not_closed")
    if any(
        not domain["included"] or domain["closure_status"] != "closed"
        for domain in governance["delegation_domains"]
    ):
        reasons.append("governance_domain_delegation_incomplete")
    phase_order = universe["evaluation"].get("phase_order")
    if phase_order is not None and (
        universe["evaluation"]["phase"] not in phase_order["phases"]
    ):
        reasons.append("phase_order_incomplete")
    reasons = _dedupe(reasons)
    return not reasons, reasons


def _check_inapplicability_status(obligation, *, check_waivers, expected_checks_by_ref):
    """A Check waiver is authorized only by the EffectiveProfile evaluation."""
    if check_waivers is None or expected_checks_by_ref is None:
        return "unverified", ["applicability_authorization_unproven"]
    ob_key = _canonical(obligation["obligation_ref"])
    for ref in obligation["expected_check_refs"]:
        entry = expected_checks_by_ref.get(ref)
        if entry is None:
            return "unverified", ["applicability_check_instance_unresolved"]
        if ob_key not in check_waivers.get(entry.get("name"), set()):
            return "unverified", ["applicability_unauthorized"]
    return "not_applicable", []


def _non_check_inapplicability_status(obligation, evaluated_at):
    """A Non-Check waiver requires a controlled, instance-bound authorization.

    Shared Rule/Authority/Target/Action/Phase never authorize a different
    ``obligation_ref`` or Candidate/Source. Bind either
    ``obligation_instance_ref`` or an explicit ``obligation_ref`` (plus any
    Candidate/Source digests the obligation declares).
    """
    authorization = obligation["applicability_authorization"]
    if authorization is None:
        return "unverified", ["applicability_authorization_missing"]
    if _canonical(authorization["authority_ref"]) != _canonical(
        obligation["basis_ref"]
    ):
        return "unverified", ["applicability_authorization_mismatch"]
    if obligation["verification_rule_ref"] is None or (
        _canonical(authorization["rule_ref"])
        != _canonical(obligation["verification_rule_ref"])
    ):
        return "unverified", ["applicability_rule_mismatch"]
    scope = authorization["scope"]
    if (
        scope["target_ref"] != obligation["target_ref"]
        or scope["action"] != obligation["action"]
        or scope["phase"] != obligation["phase"]
    ):
        return "unverified", ["applicability_scope_mismatch"]
    auth_instance_ref = authorization.get("obligation_instance_ref")
    auth_obligation_ref = scope.get("obligation_ref")
    if auth_instance_ref is None and auth_obligation_ref is None:
        return "unverified", ["applicability_authorization_instance_unbound"]
    expected_instance_ref = obligation_instance_ref(obligation)
    if auth_instance_ref is not None and auth_instance_ref != expected_instance_ref:
        return "unverified", ["applicability_authorization_instance_mismatch"]
    if auth_obligation_ref is not None and (
        _canonical(auth_obligation_ref) != _canonical(obligation["obligation_ref"])
    ):
        return "unverified", ["applicability_authorization_obligation_mismatch"]
    auth_candidate = scope.get("candidate_digest")
    auth_source = scope.get("source_digest")
    if auth_instance_ref is None:
        # obligation_ref coverage still must bind Candidate/Source when the
        # obligation instance declares them; omission is not a wildcard.
        if obligation["candidate_digest"] is not None:
            if auth_candidate is None:
                return "unverified", ["applicability_authorization_candidate_unbound"]
            if auth_candidate != obligation["candidate_digest"]:
                return "unverified", ["applicability_authorization_candidate_mismatch"]
        elif auth_candidate is not None:
            return "unverified", ["applicability_authorization_candidate_mismatch"]
        if obligation["source_digest"] is not None:
            if auth_source is None:
                return "unverified", ["applicability_authorization_source_unbound"]
            if auth_source != obligation["source_digest"]:
                return "unverified", ["applicability_authorization_source_mismatch"]
        elif auth_source is not None:
            return "unverified", ["applicability_authorization_source_mismatch"]
    else:
        if (
            auth_candidate is not None
            and auth_candidate != obligation["candidate_digest"]
        ):
            return "unverified", ["applicability_authorization_candidate_mismatch"]
        if auth_source is not None and auth_source != obligation["source_digest"]:
            return "unverified", ["applicability_authorization_source_mismatch"]
    if authorization["revoked_at"] is not None:
        return "unverified", ["applicability_authorization_revoked"]
    if (
        authorization["valid_from"] is not None
        and evaluated_at < authorization["valid_from"]
    ):
        return "unverified", ["applicability_authorization_not_yet_valid"]
    if (
        authorization["valid_until"] is not None
        and evaluated_at > authorization["valid_until"]
    ):
        return "unverified", ["applicability_authorization_expired"]
    return "not_applicable", []


def _applicability_status(
    obligation,
    *,
    current_phase,
    phase_order,
    evaluated_at,
    check_waivers=None,
    expected_checks_by_ref=None,
):
    """Evaluate the declared applicability; never defer a current Required.

    A phase different from the current phase is only "future" when the
    authorized phase order proves it. A past, unknown or unavailable order
    keeps the obligation ``unverified`` instead of silently deferring it. An
    inapplicability is only accepted when the authorized Profile/domain
    authorization covers this exact instance; a basis reference alone is not
    authorization.
    """
    if obligation["applicability"] == "not_applicable":
        if obligation["basis_ref"] is None:
            return "unverified", ["applicability_basis_missing"]
        if obligation["kind"] == "check":
            return _check_inapplicability_status(
                obligation,
                check_waivers=check_waivers,
                expected_checks_by_ref=expected_checks_by_ref,
            )
        return _non_check_inapplicability_status(obligation, evaluated_at)
    due_at = obligation["due_at"]
    if obligation["applicability"] == "not_yet_due":
        if obligation["basis_ref"] is None:
            return "unverified", ["applicability_basis_missing"]
        if due_at is not None and due_at <= evaluated_at:
            return "unverified", ["not_yet_due_time_elapsed"]
        phase_values = [
            value
            for value in (obligation["phase"], obligation["due_phase"])
            if value is not None
        ]
        if phase_values:
            positions = [
                _phase_position(value, current_phase, phase_order)
                for value in phase_values
            ]
            if "past" in positions:
                return "unverified", ["not_yet_due_phase_already_passed"]
            if "current" in positions:
                return "unverified", ["not_yet_due_phase_not_future"]
            if "unknown" in positions:
                return "unverified", ["not_yet_due_phase_unproven"]
            return "not_yet_due", ["not_yet_due_future_phase"]
        if due_at is None:
            return "unverified", ["not_yet_due_due_point_missing"]
        return "not_yet_due", ["not_yet_due_future_due_point"]
    position = _phase_position(obligation["phase"], current_phase, phase_order)
    if position == "current":
        return "required", []
    if position == "future":
        return "not_yet_due", ["phase_after_current"]
    if position == "past":
        return "unverified", ["phase_already_passed"]
    return "unverified", ["phase_position_unproven"]


def _effective_adjudication(adjudications, result_id, evaluated_at):
    for adjudication in adjudications:
        if adjudication["disposition"] == "unresolved":
            continue
        if result_id not in adjudication["applies_to_result_ids"]:
            continue
        if adjudication["decided_at"] > evaluated_at:
            continue
        if (
            adjudication["valid_until"] is not None
            and adjudication["valid_until"] < evaluated_at
        ):
            continue
        if adjudication["revoked_at"] is not None:
            continue
        return adjudication
    return None


def _in_scope_failures(
    failures,
    adjudications,
    *,
    universe,
    entry,
    scope,
    phase,
    evaluated_at,
):
    """Mark each failure in/out of scope and apply the effective adjudication."""
    annotated = []
    reasons = []
    for failure in failures:
        record = dict(failure)
        record["in_scope"] = (
            _applicable_to_matches(
                failure["applicable_to"],
                universe=universe,
                entry=entry,
                scope=scope,
                current_phase=phase,
            )
            is None
        )
        if not record["in_scope"]:
            annotated.append(record)
            continue
        adjudication = _effective_adjudication(
            adjudications, failure["result_id"], evaluated_at
        )
        if adjudication is None:
            reasons.append(
                "authenticated_native_failure"
                if failure["failure_kind"] == "native_failure"
                else "authenticated_conflict_unresolved"
            )
        else:
            record["adjudication"] = {
                "disposition": adjudication["disposition"],
                "authority_ref": adjudication["authority_ref"],
                "policy_ref": adjudication["policy_ref"],
            }
        annotated.append(record)
    return annotated, _dedupe(reasons)


def _structural_fact(receipt, ref):
    if receipt is None:
        return {"status": "not_supplied", "receipt_ref": None}
    entry = receipt["entries"].get(ref)
    if entry is None:
        return {"status": "entry_missing", "receipt_ref": receipt["receipt_ref"]}
    return {
        "status": "recorded",
        "receipt_ref": receipt["receipt_ref"],
        "receipt_status": receipt["status"],
        "binding_status": entry.get("binding_status"),
        "native_status": entry.get("native_status"),
        "selected_result_ids": list(entry.get("selected_result_ids") or []),
    }


def _receipt_problems(receipt, ref, coverage):
    if coverage["admission_status"] != "admitted":
        return []
    if coverage["source_kind"] != "current_run":
        return []
    if receipt is None:
        return ["receipt_missing_for_current_run"]
    entry = receipt["entries"].get(ref)
    if entry is None:
        return ["receipt_entry_missing"]
    reasons = []
    if entry.get("binding_status") != "matched":
        reasons.append("receipt_entry_not_matched")
    if coverage["witness_result_id"] not in (entry.get("selected_result_ids") or []):
        reasons.append("receipt_witness_missing")
    declared = coverage.get("receipt_binding_status")
    if declared is not None and declared != entry.get("binding_status"):
        reasons.append("receipt_binding_status_mismatch")
    return reasons


def _coverage_problems(coverage):
    if coverage["admission_status"] == "rejected":
        return ["admission_coverage_rejected"]
    if coverage["admission_status"] == "unverified":
        return ["admission_coverage_unverified"]
    reasons = []
    if not coverage["issuer_verified"]:
        reasons.append("admission_issuer_unverified")
    if not coverage["raw_result_verified"]:
        reasons.append("admission_raw_result_unverified")
    return reasons


def _required_check_status(reasons, coverage):
    if coverage is not None and coverage["admission_status"] == "rejected":
        return "unmet"
    if "authenticated_native_failure" in reasons:
        return "unmet"
    if reasons:
        return "unverified"
    if coverage is not None and coverage["admission_status"] == "admitted":
        return "satisfied"
    return "unverified"


def _check_instance_row(
    *,
    entry,
    referencing,
    scope,
    universe,
    admission,
    receipt,
    evaluated_at,
    check_waivers=None,
    expected_checks_by_ref=None,
):
    ref = entry["expected_check_ref"]
    phase = universe["evaluation"]["phase"]
    phase_order = universe["evaluation"].get("phase_order")
    evaluations = [
        (
            item,
            *_applicability_status(
                item,
                current_phase=phase,
                phase_order=phase_order,
                evaluated_at=evaluated_at,
                check_waivers=check_waivers,
                expected_checks_by_ref=expected_checks_by_ref,
            ),
        )
        for item in referencing
    ]
    required_now = [item for item, status, _ in evaluations if status == "required"]
    future_reasons = [
        reasons for _item, status, reasons in evaluations if status == "not_yet_due"
    ]
    problematic = [
        reasons for _item, status, reasons in evaluations if status == "unverified"
    ]
    inapplicable_reasons = [
        reasons for _item, status, reasons in evaluations if status == "not_applicable"
    ]
    structural = _structural_fact(receipt, ref)
    if not required_now:
        if problematic:
            status = "unverified"
            reasons = _dedupe(
                [reason for item_reasons in problematic for reason in item_reasons]
            )
        elif future_reasons:
            status = "not_yet_due"
            reasons = _dedupe(
                [reason for item_reasons in future_reasons for reason in item_reasons]
            )
        elif inapplicable_reasons:
            status = "not_applicable"
            reasons = _dedupe(
                [
                    reason
                    for item_reasons in inapplicable_reasons
                    for reason in item_reasons
                ]
            )
        else:
            status = "unverified"
            reasons = ["obligation_applicability_unresolved"]
        return _instance_row(
            entry,
            scope_id=scope["scope_id"],
            status=status,
            reasons=reasons,
            referencing=referencing,
            structural=structural,
            coverage=None,
            failures=[],
        )

    reasons = []
    if entry.get("applicability", {}).get("status") in INAPPLICABLE_CHECK_STATES:
        reasons.append("check_inapplicable_for_required_obligation")
    if entry.get("definition_status") != "resolved":
        reasons.append("check_definition_unresolved")
    if entry.get("rule_status") != "resolved":
        reasons.append("check_rule_unresolved")
    coverage = None
    failures = []
    if admission is None:
        reasons.append("trusted_admission_missing")
    else:
        coverage = admission["coverage"].get(ref)
        if coverage is None:
            failures, failure_reasons = _in_scope_failures(
                admission["failures"].get(ref) or [],
                admission["adjudications"].get(ref) or [],
                universe=universe,
                entry=entry,
                scope=scope,
                phase=phase,
                evaluated_at=evaluated_at,
            )
            reasons.extend(failure_reasons)
            if not failure_reasons:
                reasons.append(
                    "admission_remaining_work"
                    if ref in admission["remaining"]
                    else "admission_coverage_missing"
                )
        else:
            structural = _structural_fact(receipt, ref)
            scope_problem = _applicable_to_matches(
                coverage["applicable_to"],
                universe=universe,
                entry=entry,
                scope=scope,
                current_phase=phase,
            )
            if scope_problem is not None:
                reasons.append(scope_problem)
            reasons.extend(_coverage_problems(coverage))
            if (
                coverage["admission_status"] == "admitted"
                and ref in admission["remaining"]
            ):
                reasons.append("admission_admitted_but_remaining")
            reasons.extend(_receipt_problems(receipt, ref, coverage))
            failures, failure_reasons = _in_scope_failures(
                admission["failures"].get(ref) or [],
                admission["adjudications"].get(ref) or [],
                universe=universe,
                entry=entry,
                scope=scope,
                phase=phase,
                evaluated_at=evaluated_at,
            )
            reasons.extend(failure_reasons)
    reasons = _dedupe(reasons)
    return _instance_row(
        entry,
        scope_id=scope["scope_id"],
        status=_required_check_status(reasons, coverage),
        reasons=reasons,
        referencing=referencing,
        structural=structural,
        coverage=coverage,
        failures=failures,
    )


def _instance_row(
    entry,
    *,
    scope_id,
    status,
    reasons,
    referencing,
    structural,
    coverage,
    failures,
):
    if status not in INSTANCE_STATUSES:
        raise RequiredAssessmentError("assessment_instance_status_invalid")
    admission_view = None
    if coverage is not None:
        admission_view = {
            "status": coverage["admission_status"],
            "source_kind": coverage["source_kind"],
            "witness_result_id": coverage["witness_result_id"],
            "origin_trace": coverage["origin_trace"],
            "receipt_binding_status": coverage["receipt_binding_status"],
            "reason": coverage["reason"],
        }
    evidence_refs = []
    if coverage is not None and coverage["witness_result_id"] is not None:
        evidence_refs.append(coverage["witness_result_id"])
    return {
        "scope_id": scope_id,
        "expected_check_ref": entry["expected_check_ref"],
        "check_name": entry.get("name"),
        "status": status,
        "reasons": _dedupe(reasons),
        "applicability": dict(entry.get("applicability") or {}),
        "definition_status": entry.get("definition_status"),
        "rule_status": entry.get("rule_status"),
        "obligation_instance_refs": [item["instance_ref"] for item in referencing],
        "admission": admission_view,
        "structural": structural,
        "authenticated_failures": failures,
        "evidence_refs": evidence_refs,
    }


def _non_check_evidence_usable(evidence, obligation, universe, evaluated_at):
    """Return a reason code when one evidence record cannot support the instance.

    The authorized obligation declares which candidate subject fields the
    verifier must have actually checked. A source/commit-level obligation may
    require only the commit identity; an artifact-level obligation must bind the
    actual artifact digest. Matching any single field is never enough.
    """
    if (
        evidence["verifier_ref"] != obligation["verifier_ref"]
        or evidence["verifier_authorization_ref"]
        != obligation["verifier_authorization_ref"]
        or evidence["verification_rule_ref"] != obligation["verification_rule_ref"]
    ):
        return "non_check_verifier_mismatch"
    if evidence["evaluated_at"] > evaluated_at:
        return "non_check_evidence_not_yet_valid"
    if evidence["valid_until"] is not None and evidence["valid_until"] < evaluated_at:
        return "non_check_evidence_expired"
    if evidence["revoked_at"] is not None:
        return "non_check_evidence_revoked"
    candidate = universe["evaluation"].get("candidate") or {}
    for field in obligation["required_subject_fields"]:
        if candidate.get(field) is None:
            return "non_check_subject_identity_missing"
        if evidence["subject_ref"].get(field) is None:
            return "non_check_subject_incomplete"
        if evidence["subject_ref"][field] != candidate[field]:
            return "non_check_subject_mismatch"
    for key, value in evidence["subject_ref"].items():
        if key in candidate and candidate[key] != value:
            return "non_check_subject_mismatch"
    return None


def _obligation_row(
    obligation,
    *,
    status,
    reasons,
    check_instance_refs=None,
    evidence_refs=None,
):
    if status not in INSTANCE_STATUSES:
        raise RequiredAssessmentError("assessment_instance_status_invalid")
    return {
        "instance_ref": obligation["instance_ref"],
        "obligation_ref": obligation["obligation_ref"],
        "kind": obligation["kind"],
        "target_ref": obligation["target_ref"],
        "action": obligation["action"],
        "phase": obligation["phase"],
        "candidate_digest": obligation["candidate_digest"],
        "source_digest": obligation["source_digest"],
        "applicability": obligation["applicability"],
        "required_subject_fields": list(obligation["required_subject_fields"]),
        "status": status,
        "reasons": _dedupe(reasons),
        "check_instances": list(check_instance_refs or ()),
        "evidence_refs": list(evidence_refs or ()),
    }


def _non_check_obligation_row(
    obligation,
    *,
    universe,
    evaluated_at,
    evidence_by_instance,
):
    phase = universe["evaluation"]["phase"]
    phase_order = universe["evaluation"].get("phase_order")
    status, reasons = _applicability_status(
        obligation,
        current_phase=phase,
        phase_order=phase_order,
        evaluated_at=evaluated_at,
    )
    if status != "required":
        return _obligation_row(obligation, status=status, reasons=reasons)
    if (
        obligation["verifier_ref"] is None
        or obligation["verifier_authorization_ref"] is None
        or obligation["verification_rule_ref"] is None
    ):
        return _obligation_row(
            obligation, status="unverified", reasons=["non_check_verifier_unbound"]
        )
    if not obligation["required_subject_fields"]:
        return _obligation_row(
            obligation,
            status="unverified",
            reasons=["non_check_subject_requirement_missing"],
        )
    entries = evidence_by_instance.get(obligation["instance_ref"], [])
    if not entries:
        return _obligation_row(
            obligation, status="unverified", reasons=["non_check_evidence_missing"]
        )
    usable = []
    unusable_reasons = []
    for evidence in entries:
        problem = _non_check_evidence_usable(
            evidence, obligation, universe, evaluated_at
        )
        if problem is None:
            usable.append(evidence)
        else:
            unusable_reasons.append(problem)
    if not usable:
        return _obligation_row(
            obligation,
            status="unverified",
            reasons=_dedupe(unusable_reasons) or ["non_check_evidence_unusable"],
        )
    results = {evidence["result"] for evidence in usable}
    evidence_refs = [evidence["evidence_ref"] for evidence in usable]
    if results == {"satisfied"}:
        return _obligation_row(
            obligation,
            status="satisfied",
            reasons=[],
            evidence_refs=evidence_refs,
        )
    if results == {"unmet"}:
        return _obligation_row(
            obligation,
            status="unmet",
            reasons=["non_check_verified_unmet"],
            evidence_refs=evidence_refs,
        )
    if "unverified" in results:
        reasons = _dedupe(
            [
                evidence.get("reason") or "non_check_evidence_unverified"
                for evidence in usable
                if evidence["result"] == "unverified"
            ]
        )
        return _obligation_row(
            obligation,
            status="unverified",
            reasons=reasons,
            evidence_refs=evidence_refs,
        )
    return _obligation_row(
        obligation,
        status="unverified",
        reasons=["non_check_evidence_conflict"],
        evidence_refs=evidence_refs,
    )


def _check_obligation_row(
    obligation,
    *,
    check_rows_by_ref,
    evaluated_at,
    phase,
    phase_order,
    check_waivers=None,
    expected_checks_by_ref=None,
):
    status, reasons = _applicability_status(
        obligation,
        current_phase=phase,
        phase_order=phase_order,
        evaluated_at=evaluated_at,
        check_waivers=check_waivers,
        expected_checks_by_ref=expected_checks_by_ref,
    )
    if status != "required":
        return _obligation_row(obligation, status=status, reasons=reasons)
    rows = [
        check_rows_by_ref[ref]
        for ref in obligation["expected_check_refs"]
        if ref in check_rows_by_ref
    ]
    if len(rows) != len(obligation["expected_check_refs"]):
        return _obligation_row(
            obligation,
            status="unverified",
            reasons=["obligation_check_instance_unresolved"],
            check_instance_refs=[row["expected_check_ref"] for row in rows],
        )
    statuses = {row["status"] for row in rows}
    if statuses == {"satisfied"}:
        status = "satisfied"
    elif "unmet" in statuses:
        status = "unmet"
    else:
        status = "unverified"
    evidence_refs = [ref for row in rows for ref in row.get("evidence_refs", [])]
    return _obligation_row(
        obligation,
        status=status,
        reasons=_dedupe([reason for row in rows for reason in row["reasons"]]),
        check_instance_refs=list(obligation["expected_check_refs"]),
        evidence_refs=evidence_refs,
    )


def _summary(obligations, check_instances):
    def counts(rows):
        result = {"total": len(rows)}
        for status in sorted(INSTANCE_STATUSES):
            result[status] = sum(row["status"] == status for row in rows)
        return result

    return {
        "obligations": counts(obligations),
        "check_instances": counts(check_instances),
    }


def _assemble(
    *,
    scope,
    universe,
    admission,
    receipt,
    obligations,
    check_instances,
    evaluated_at,
    verification,
    cross_check_reasons,
    contract_sample,
    ignored_evidence,
    authority_verified,
    check_domain_verified,
    universe_verified,
    evidence_satisfied,
):
    blocked_reasons = []
    if not authority_verified:
        blocked_reasons.extend(
            f"authority:{reason}" for reason in verification["authority"]["reasons"]
        )
    if not check_domain_verified:
        blocked_reasons.extend(
            f"check_domain:{reason}"
            for reason in verification["check_domain"]["reasons"]
        )
    if not universe_verified:
        blocked_reasons.extend(
            f"obligations_universe:{reason}"
            for reason in verification["obligations_universe"]["reasons"]
        )
    if not evidence_satisfied:
        for row in obligations:
            if row["status"] in {"unmet", "unverified"}:
                blocked_reasons.extend(
                    f"obligation:{row['instance_ref']}:{reason}"
                    for reason in row["reasons"]
                )
        for row in check_instances:
            if row["status"] in {"unmet", "unverified"}:
                blocked_reasons.extend(
                    f"check_instance:{row['expected_check_ref']}:{reason}"
                    for reason in row["reasons"]
                )
    blocked_reasons = _dedupe(blocked_reasons)
    status = (
        "passed"
        if authority_verified
        and check_domain_verified
        and universe_verified
        and evidence_satisfied
        else "blocked"
    )
    if status == "passed" and blocked_reasons:
        raise RequiredAssessmentError("assessment_gate_inconsistent")
    if status == "blocked" and not blocked_reasons:
        raise RequiredAssessmentError("assessment_gate_inconsistent")
    limitations = [
        "assessment_does_not_publish_github_check",
        "assessment_does_not_read_server_rulesets",
        "assessment_does_not_authorize_release_publication",
        "trusted_admission_consumed_from_upstream_contract",
    ]
    if contract_sample:
        limitations.append("contract_sample_inputs_not_production_admission")
    if admission is None:
        limitations.append("trusted_admission_not_supplied")
    if not evidence_satisfied:
        limitations.append("evidence_satisfaction_incomplete")
    authority = universe["authority"]
    assessment = {
        "kind": ASSESSMENT_KIND,
        "schema_version": ASSESSMENT_VERSION,
        "status": status,
        "consumer": scope.get("consumer"),
        "context": universe["evaluation"]["context"],
        "action": universe["evaluation"]["action"],
        "target": universe["evaluation"]["target"],
        "phase": universe["evaluation"]["phase"],
        "refs": universe["evaluation"]["refs"],
        "candidate": universe["evaluation"].get("candidate"),
        "evaluated_at": evaluated_at.isoformat(),
        "inputs": {
            "profile": dict(universe["profile"]),
            "authority": {
                "status": authority["status"],
                "source_ref": authority["source_ref"],
                "approved_at": authority["approved_at"].isoformat(),
                "effective_at": authority["effective_at"].isoformat(),
                "valid_from": (
                    authority["valid_from"].isoformat()
                    if authority["valid_from"] is not None
                    else None
                ),
                "valid_until": (
                    authority["valid_until"].isoformat()
                    if authority["valid_until"] is not None
                    else None
                ),
                "revoked_at": (
                    authority["revoked_at"].isoformat()
                    if authority["revoked_at"] is not None
                    else None
                ),
                "evaluated_at": authority["evaluated_at"].isoformat(),
            },
            "governance_domain": {
                "closure_status": universe["governance_domain"]["closure_status"],
                "closure_ref": universe["governance_domain"]["closure_ref"],
                "scope": universe["governance_domain"]["scope"],
            },
            "check_domain": dict(universe["check_domain"]),
            "universe_status": universe["universe_status"],
            "universe_reason": universe["reason"],
            "universe_ref": universe["universe_ref"],
            "admission_ref": (
                admission["admission_ref"] if admission is not None else None
            ),
            "receipt_ref": (receipt["receipt_ref"] if receipt is not None else None),
            "contract_sample": contract_sample,
        },
        "verification": verification,
        "cross_check_reasons": cross_check_reasons,
        "obligations": obligations,
        "check_instances": check_instances,
        "summary": _summary(obligations, check_instances),
        "non_check_evidence_ignored": ignored_evidence,
        "blocked_reasons": blocked_reasons,
        "limitations": limitations,
        "assessment_ref": None,
    }
    assessment["assessment_ref"] = _digest(
        {key: value for key, value in assessment.items() if key != "assessment_ref"}
    )
    return assessment


def assess_required_obligations(
    *,
    universe,
    expected,
    evaluated_at,
    admission=None,
    receipt=None,
    non_check_evidence=None,
    ref_scope_id=None,
    admission_source_authority=None,
    admission_current_authorization=None,
    admission_contract_sample=False,
):
    """Assess the complete current Required Obligations for one staged scope.

    ``universe`` is the authorized #66-B2 obligations projection (Profile
    identity, authority window, governance-domain closure, evaluation input,
    Check domain identity and the obligation instances). ``expected`` is the
    #66-B v2 Check projection; pass ``ref_scope_id`` for one child of a multi-ref
    container. ``admission`` is the #63 trusted admission projection for this
    exact scope, ``receipt`` is the #66-C structural receipt, and
    ``non_check_evidence`` is the normalized domain evidence document.

    The returned Assessment is additive and versioned. Contract samples may be
    passed to verify the aggregation logic, but they are labeled and never claim
    a production trusted chain, a GitHub Check, a server Rule or a Release
    authorization.
    """
    assessed_at = _datetime(evaluated_at, "assessment_evaluated_at_invalid")
    scope = _select_expected_scope(expected, ref_scope_id)
    checks = _verify_expected_scope(scope)
    normalized_universe = _validate_universe(universe)
    if (
        isinstance(admission, dict)
        and admission.get("phase") is None
        and admission.get("kind")
        in {
            "engineering-ci-evidence-admission",
            "engineering-ci-historical-reuse-admission",
        }
    ):
        admission = project_t1_t2_gate_admission(
            admission,
            expected=expected,
            current_authorization=admission_current_authorization,
            source_authority=admission_source_authority,
            receipt=receipt,
            contract_sample=admission_contract_sample,
            ref_scope_id=ref_scope_id,
        )
    admission_facts = (
        _validate_admission(admission, scope, checks) if admission is not None else None
    )
    receipt_facts = _validate_receipt(receipt, scope) if receipt is not None else None
    evidence_facts = (
        _validate_non_check_evidence(non_check_evidence)
        if non_check_evidence is not None
        else None
    )

    cross_check_reasons = _cross_check_universe(normalized_universe, scope, checks)
    authority_verified, authority_reasons = _authority_fact(
        normalized_universe, assessed_at
    )
    check_domain_verified, check_domain_reasons = _check_domain_fact(scope, checks)
    universe_verified, universe_reasons = _universe_fact(
        normalized_universe, cross_check_reasons
    )

    phase = normalized_universe["evaluation"]["phase"]
    checks_by_ref = {entry["expected_check_ref"]: entry for entry in checks}
    check_waivers = _authorized_check_waivers(scope)
    referencing_by_ref = {ref: [] for ref in checks_by_ref}
    for obligation in normalized_universe["obligations"]:
        for ref in obligation["expected_check_refs"]:
            if ref in referencing_by_ref:
                referencing_by_ref[ref].append(obligation)

    check_rows = []
    check_rows_by_ref = {}
    for ref, entry in checks_by_ref.items():
        referencing = referencing_by_ref.get(ref, [])
        if not referencing:
            continue
        row = _check_instance_row(
            entry=entry,
            referencing=referencing,
            scope=scope,
            universe=normalized_universe,
            admission=admission_facts,
            receipt=receipt_facts,
            evaluated_at=assessed_at,
            check_waivers=check_waivers,
            expected_checks_by_ref=checks_by_ref,
        )
        check_rows.append(row)
        check_rows_by_ref[ref] = row

    required_checks = [
        entry["expected_check_ref"]
        for entry in checks
        if entry.get("required_by_obligations")
        and entry["expected_check_ref"] not in check_rows_by_ref
    ]
    for ref in sorted(required_checks):
        row = _instance_row(
            checks_by_ref[ref],
            scope_id=scope["scope_id"],
            status="unverified",
            reasons=["required_check_omitted_by_universe"],
            referencing=[],
            structural=_structural_fact(receipt_facts, ref),
            coverage=None,
            failures=[],
        )
        check_rows.append(row)
        check_rows_by_ref[ref] = row

    evidence_by_instance = {}
    ignored_evidence = 0
    if evidence_facts is not None:
        universe_instances = {
            obligation["instance_ref"]
            for obligation in normalized_universe["obligations"]
            if obligation["kind"] == "non_check"
        }
        for evidence in evidence_facts["entries"]:
            if evidence["instance_ref"] in universe_instances:
                evidence_by_instance.setdefault(evidence["instance_ref"], []).append(
                    evidence
                )
            else:
                ignored_evidence += 1

    obligation_rows = []
    phase_order = normalized_universe["evaluation"].get("phase_order")
    for obligation in normalized_universe["obligations"]:
        if obligation["kind"] == "check":
            row = _check_obligation_row(
                obligation,
                check_rows_by_ref=check_rows_by_ref,
                evaluated_at=assessed_at,
                phase=phase,
                phase_order=phase_order,
                check_waivers=check_waivers,
                expected_checks_by_ref=checks_by_ref,
            )
        else:
            row = _non_check_obligation_row(
                obligation,
                universe=normalized_universe,
                evaluated_at=assessed_at,
                evidence_by_instance=evidence_by_instance,
            )
        obligation_rows.append(row)

    evidence_reasons = []
    for row in obligation_rows:
        if row["status"] == "unmet":
            evidence_reasons.append("obligation_unmet")
        elif row["status"] == "unverified":
            evidence_reasons.append("obligation_unverified")
    for row in check_rows:
        if row["status"] in {"unmet", "unverified"}:
            evidence_reasons.append("check_instance_unsatisfied")
    evidence_reasons = _dedupe(evidence_reasons)
    evidence_satisfied = not evidence_reasons
    verification = {
        "authority": {
            "status": "verified" if authority_verified else "unverified",
            "reasons": authority_reasons,
        },
        "check_domain": {
            "status": "verified" if check_domain_verified else "unverified",
            "reasons": check_domain_reasons,
        },
        "obligations_universe": {
            "status": "verified" if universe_verified else "unverified",
            "reasons": universe_reasons,
        },
        "evidence_satisfaction": {
            "status": "satisfied" if evidence_satisfied else "unsatisfied",
            "reasons": evidence_reasons,
        },
    }
    contract_sample = bool(
        normalized_universe["contract_sample"]
        or (admission_facts is not None and admission_facts["contract_sample"])
        or (evidence_facts is not None and evidence_facts["contract_sample"])
    )
    return _assemble(
        scope=scope,
        universe=normalized_universe,
        admission=admission_facts,
        receipt=receipt_facts,
        obligations=obligation_rows,
        check_instances=check_rows,
        evaluated_at=assessed_at,
        verification=verification,
        cross_check_reasons=cross_check_reasons,
        contract_sample=contract_sample,
        ignored_evidence=ignored_evidence,
        authority_verified=authority_verified,
        check_domain_verified=check_domain_verified,
        universe_verified=universe_verified,
        evidence_satisfied=evidence_satisfied,
    )


def validate_assessment(assessment):
    """Validate an Assessment, recompute its ref and enforce gate consistency."""
    if not isinstance(assessment, dict):
        raise RequiredAssessmentError("assessment_invalid")
    if assessment.get("kind") != ASSESSMENT_KIND:
        raise RequiredAssessmentError("assessment_kind_invalid")
    if assessment.get("schema_version") != ASSESSMENT_VERSION:
        raise RequiredAssessmentError("assessment_schema_unsupported")
    stored_ref = _internal_ref(
        assessment.get("assessment_ref"), "assessment_ref_invalid"
    )
    payload = {
        key: value for key, value in assessment.items() if key != "assessment_ref"
    }
    if _digest(payload) != stored_ref:
        raise RequiredAssessmentError("assessment_ref_mismatch")
    status = assessment.get("status")
    if status not in GATE_STATUSES:
        raise RequiredAssessmentError("assessment_status_invalid")
    evaluated_at = _datetime(assessment.get("evaluated_at"), "assessment_invalid")
    inputs = assessment.get("inputs")
    if not isinstance(inputs, dict):
        raise RequiredAssessmentError("assessment_inputs_invalid")
    check_domain_inputs = inputs.get("check_domain")
    authority_inputs = inputs.get("authority")
    if not isinstance(check_domain_inputs, dict) or not isinstance(
        authority_inputs, dict
    ):
        raise RequiredAssessmentError("assessment_inputs_invalid")
    scope_id = _internal_ref(
        check_domain_inputs.get("scope_id"), "assessment_inputs_invalid"
    )
    _internal_ref(
        check_domain_inputs.get("expected_set_ref"), "assessment_inputs_invalid"
    )
    _internal_ref(inputs.get("universe_ref"), "assessment_inputs_invalid")
    for key in ("admission_ref", "receipt_ref"):
        if inputs.get(key) is not None:
            _internal_ref(inputs[key], "assessment_inputs_invalid")
    if inputs.get("universe_status") not in UNIVERSE_STATUSES:
        raise RequiredAssessmentError("assessment_inputs_invalid")
    _flag(inputs.get("contract_sample"), "assessment_inputs_invalid")
    if not isinstance(assessment.get("limitations"), list):
        raise RequiredAssessmentError("assessment_invalid")
    verification = assessment.get("verification")
    if not isinstance(verification, dict) or set(verification) != {
        "authority",
        "check_domain",
        "obligations_universe",
        "evidence_satisfaction",
    }:
        raise RequiredAssessmentError("assessment_verification_invalid")
    for key in ("authority", "check_domain", "obligations_universe"):
        fact = verification[key]
        if not isinstance(fact, dict) or fact.get("status") not in FACT_STATUSES:
            raise RequiredAssessmentError("assessment_verification_invalid")
        if not isinstance(fact.get("reasons"), list):
            raise RequiredAssessmentError("assessment_verification_invalid")
        if fact["status"] == "verified" and fact["reasons"]:
            raise RequiredAssessmentError("assessment_verification_invalid")
    evidence_fact = verification["evidence_satisfaction"]
    if (
        not isinstance(evidence_fact, dict)
        or evidence_fact.get("status") not in EVIDENCE_FACT_STATUSES
    ):
        raise RequiredAssessmentError("assessment_verification_invalid")
    if evidence_fact["status"] == "satisfied" and evidence_fact.get("reasons"):
        raise RequiredAssessmentError("assessment_verification_invalid")
    obligations = assessment.get("obligations")
    check_instances = assessment.get("check_instances")
    if not isinstance(obligations, list) or not isinstance(check_instances, list):
        raise RequiredAssessmentError("assessment_invalid")
    seen_instances = set()
    for row in obligations:
        if not isinstance(row, dict):
            raise RequiredAssessmentError("assessment_invalid")
        instance_ref = _internal_ref(
            row.get("instance_ref"), "assessment_obligation_invalid"
        )
        if instance_ref in seen_instances:
            raise RequiredAssessmentError("assessment_instance_duplicate")
        if obligation_instance_ref(row) != instance_ref:
            raise RequiredAssessmentError("assessment_instance_ref_mismatch")
        seen_instances.add(instance_ref)
        row_status = row.get("status")
        applicability = row.get("applicability")
        if row_status not in INSTANCE_STATUSES or applicability not in APPLICABILITIES:
            raise RequiredAssessmentError("assessment_obligation_invalid")
        if row.get("kind") not in OBLIGATION_KINDS:
            raise RequiredAssessmentError("assessment_obligation_invalid")
        if not isinstance(row.get("reasons"), list):
            raise RequiredAssessmentError("assessment_obligation_invalid")
        if applicability == "not_applicable" and row_status not in {
            "not_applicable",
            "unverified",
        }:
            raise RequiredAssessmentError("assessment_obligation_invalid")
        if applicability == "not_yet_due" and row_status not in {
            "not_yet_due",
            "unverified",
        }:
            raise RequiredAssessmentError("assessment_obligation_invalid")
        if (
            row_status == "not_yet_due"
            and applicability == "required"
            and row.get("phase") == assessment.get("phase")
        ):
            raise RequiredAssessmentError("assessment_not_yet_due_unproven")
    check_refs = set()
    for row in check_instances:
        if not isinstance(row, dict):
            raise RequiredAssessmentError("assessment_invalid")
        ref = _internal_ref(
            row.get("expected_check_ref"), "assessment_check_instance_invalid"
        )
        if ref in check_refs:
            raise RequiredAssessmentError("assessment_check_instance_duplicate")
        check_refs.add(ref)
        if row.get("status") not in INSTANCE_STATUSES:
            raise RequiredAssessmentError("assessment_check_instance_invalid")
        if not isinstance(row.get("reasons"), list):
            raise RequiredAssessmentError("assessment_check_instance_invalid")
        if row.get("scope_id") != scope_id:
            raise RequiredAssessmentError("assessment_check_instance_scope_mismatch")
        obligation_refs = row.get("obligation_instance_refs")
        if not isinstance(obligation_refs, list):
            raise RequiredAssessmentError("assessment_check_instance_invalid")
        for instance_ref in obligation_refs:
            if instance_ref not in seen_instances:
                raise RequiredAssessmentError(
                    "assessment_check_instance_obligation_unknown"
                )
    for row in obligations:
        if row["kind"] != "check":
            continue
        check_ref_values = row.get("check_instances")
        if not isinstance(check_ref_values, list):
            raise RequiredAssessmentError("assessment_obligation_invalid")
        for check_ref in check_ref_values:
            if check_ref not in check_refs:
                raise RequiredAssessmentError(
                    "assessment_obligation_check_instance_unknown"
                )
    summary = assessment.get("summary")
    if not isinstance(summary, dict):
        raise RequiredAssessmentError("assessment_summary_invalid")
    if summary != _summary(obligations, check_instances):
        raise RequiredAssessmentError("assessment_summary_mismatch")
    current_due_bad = any(
        row["status"] in {"unmet", "unverified"}
        for row in obligations
        if row["status"] not in {"not_applicable", "not_yet_due"}
    ) or any(row["status"] in {"unmet", "unverified"} for row in check_instances)
    expected_evidence = "unsatisfied" if current_due_bad else "satisfied"
    if evidence_fact["status"] != expected_evidence:
        raise RequiredAssessmentError("assessment_verification_inconsistent")
    if verification["check_domain"]["status"] == "verified" and (
        check_domain_inputs.get("status") != "complete"
    ):
        raise RequiredAssessmentError("assessment_verification_inconsistent")
    if verification["obligations_universe"]["status"] == "verified" and (
        inputs.get("universe_status") != "complete"
    ):
        raise RequiredAssessmentError("assessment_verification_inconsistent")
    if verification["authority"]["status"] == "verified":
        if authority_inputs.get("status") != "verified":
            raise RequiredAssessmentError("assessment_verification_inconsistent")
        if authority_inputs.get("revoked_at") is not None:
            raise RequiredAssessmentError("assessment_verification_inconsistent")
        valid_until = _optional_datetime(
            authority_inputs.get("valid_until"), "assessment_inputs_invalid"
        )
        if valid_until is not None and evaluated_at > valid_until:
            raise RequiredAssessmentError("assessment_verification_inconsistent")
    facts_good = (
        verification["authority"]["status"] == "verified"
        and verification["check_domain"]["status"] == "verified"
        and verification["obligations_universe"]["status"] == "verified"
        and verification["evidence_satisfaction"]["status"] == "satisfied"
    )
    if status == "passed" and (not facts_good or current_due_bad):
        raise RequiredAssessmentError("assessment_gate_inconsistent")
    blocked_reasons = assessment.get("blocked_reasons")
    if not isinstance(blocked_reasons, list):
        raise RequiredAssessmentError("assessment_invalid")
    if status == "passed" and blocked_reasons:
        raise RequiredAssessmentError("assessment_gate_inconsistent")
    if status == "blocked" and not blocked_reasons:
        raise RequiredAssessmentError("assessment_gate_inconsistent")
    return assessment
