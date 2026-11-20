"""#63 T1-b local → Actions controlled evidence handoff.

Builds and verifies an immutable handoff package that carries local Hook/CLI
native evidence into an Actions/ECI consumer. Verification requires separate
issuer, execution-boundary and producer-authorization proofs. A local
self-declared ``trusted`` flag, identity signature alone, or Artifact upload
success never elevates coverage.

This module does **not**:

- run Checks or rewrite Native Results;
- admit same-execution evidence (that remains T1-a ``admit_evidence``);
- admit cross-Run reuse (that remains T2 ``admit_historical_reuse``);
- decide #61 Required/Release status;
- claim a production trust root — fixture verifiers are not production authority.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime

HANDOFF_KIND = "engineering-ci-local-actions-handoff"
HANDOFF_VERSION = 1
PACKAGE_STATUSES = frozenset({"candidate", "verified", "rejected", "unverified"})
EXECUTION_BOUNDARY_KINDS = frozenset(
    {
        "controlled_actions_producer",
        "controlled_local_environment",
        "uncontrolled_developer_workstation",
    }
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA256_REF_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_EVIDENCE_REF_FIELDS = frozenset({"name", "version", "path", "sha256"})
_CONSUMER_SCOPE_REQUIRED = frozenset({"action", "target_ref", "phase", "check_ids"})
_CONSUMER_SCOPE_OPTIONAL = frozenset(
    {"candidate_digest", "scope_id", "event_ref", "expected_set_ref"}
)


class LocalActionsHandoffError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _digest(value):
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    )


def _text(value, code):
    if not isinstance(value, str) or not value.strip():
        raise LocalActionsHandoffError(code)
    return value


def _flag(value, code):
    if not isinstance(value, bool):
        raise LocalActionsHandoffError(code)
    return value


def _internal_ref(value, code):
    text = _text(value, code)
    if not _SHA256_REF_RE.fullmatch(text):
        raise LocalActionsHandoffError(code)
    return text


def _sha256_hex(value, code):
    text = _text(value, code).lower()
    if not _SHA256_RE.fullmatch(text):
        raise LocalActionsHandoffError(code)
    return text


def _evidence_ref(value, code):
    if not isinstance(value, dict) or set(value) - _EVIDENCE_REF_FIELDS:
        raise LocalActionsHandoffError(code)
    record = {
        "name": _text(value.get("name"), code),
        "version": _text(value.get("version"), code),
        "sha256": _sha256_hex(value.get("sha256"), code),
    }
    if value.get("path") is not None:
        record["path"] = _text(value.get("path"), code)
    return record


def _optional_evidence_ref(value, code):
    if value is None:
        return None
    return _evidence_ref(value, code)


def _datetime(value, code):
    text = _text(value, code)
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise LocalActionsHandoffError(code) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LocalActionsHandoffError(code)
    return parsed


def _optional_datetime(value, code):
    if value is None:
        return None
    return _datetime(value, code)


def _callable_or_none(value, code):
    if value is None:
        return None
    if not callable(value):
        raise LocalActionsHandoffError(code)
    return value


def _normalize_payload(value):
    if not isinstance(value, dict):
        raise LocalActionsHandoffError("handoff_payload_invalid")
    reports = value.get("reports")
    job_receipts = value.get("job_receipts")
    if not isinstance(reports, list) or not reports:
        raise LocalActionsHandoffError("handoff_reports_missing")
    if not isinstance(job_receipts, list) or not job_receipts:
        raise LocalActionsHandoffError("handoff_job_receipts_missing")
    for item in reports + job_receipts:
        if not isinstance(item, dict):
            raise LocalActionsHandoffError("handoff_payload_invalid")
    payload = {
        "reports": copy.deepcopy(reports),
        "job_receipts": copy.deepcopy(job_receipts),
        "result_ids": [],
        "source_digests": [],
        "invocation_ref": _optional_text_ref(
            value.get("invocation_ref"), "handoff_invocation_ref_invalid"
        ),
        "event_payload_ref": _optional_text_ref(
            value.get("event_payload_ref"), "handoff_event_payload_ref_invalid"
        ),
    }
    result_ids = value.get("result_ids") or ()
    if not isinstance(result_ids, (list, tuple)):
        raise LocalActionsHandoffError("handoff_result_ids_invalid")
    payload["result_ids"] = [
        _internal_ref(item, "handoff_result_ids_invalid") for item in result_ids
    ]
    source_digests = value.get("source_digests") or ()
    if not isinstance(source_digests, (list, tuple)):
        raise LocalActionsHandoffError("handoff_source_digests_invalid")
    payload["source_digests"] = [
        _sha256_hex(item, "handoff_source_digests_invalid") for item in source_digests
    ]
    payload["payload_digest"] = _digest(
        {
            "reports": payload["reports"],
            "job_receipts": payload["job_receipts"],
            "result_ids": payload["result_ids"],
            "source_digests": payload["source_digests"],
            "invocation_ref": payload["invocation_ref"],
            "event_payload_ref": payload["event_payload_ref"],
        }
    )
    return payload


def _optional_text_ref(value, code):
    if value is None:
        return None
    return _text(value, code)


def _normalize_execution_boundary(value):
    if not isinstance(value, dict):
        raise LocalActionsHandoffError("handoff_execution_boundary_invalid")
    kind = _text(value.get("kind"), "handoff_execution_boundary_invalid")
    if kind not in EXECUTION_BOUNDARY_KINDS:
        raise LocalActionsHandoffError("handoff_execution_boundary_invalid")
    return {
        "kind": kind,
        "collector_ref": _evidence_ref(
            value.get("collector_ref"), "handoff_collector_ref_invalid"
        ),
        "controlled": _flag(value.get("controlled"), "handoff_controlled_flag_invalid"),
        "self_reported": _flag(
            value.get("self_reported"), "handoff_self_reported_flag_invalid"
        ),
        "proof_ref": _optional_evidence_ref(
            value.get("proof_ref"), "handoff_execution_proof_ref_invalid"
        ),
    }


def _normalize_producer_authorization(value):
    if not isinstance(value, dict):
        raise LocalActionsHandoffError("handoff_producer_authorization_invalid")
    raw_scope = value.get("scope")
    if not isinstance(raw_scope, dict):
        raise LocalActionsHandoffError("handoff_authorization_scope_invalid")
    unknown_scope = set(raw_scope) - _CONSUMER_SCOPE_REQUIRED - _CONSUMER_SCOPE_OPTIONAL
    if unknown_scope:
        raise LocalActionsHandoffError("handoff_authorization_scope_invalid")
    raw_check_ids = raw_scope.get("check_ids")
    if not isinstance(raw_check_ids, (list, tuple)) or not raw_check_ids:
        raise LocalActionsHandoffError("handoff_authorization_scope_invalid")
    normalized_check_ids = []
    for item in raw_check_ids:
        check_id = _text(item, "handoff_authorization_scope_invalid")
        if check_id not in normalized_check_ids:
            normalized_check_ids.append(check_id)
    phase = raw_scope.get("phase")
    if phase is not None:
        phase = _text(phase, "handoff_authorization_scope_invalid")
    scope = {
        "action": _text(
            raw_scope.get("action"),
            "handoff_authorization_scope_invalid",
        ),
        "target_ref": _text(
            raw_scope.get("target_ref"),
            "handoff_authorization_scope_invalid",
        ),
        "phase": phase,
        "check_ids": normalized_check_ids,
    }
    for key in _CONSUMER_SCOPE_OPTIONAL:
        if raw_scope.get(key) is not None:
            scope[key] = _text(
                raw_scope.get(key), "handoff_authorization_scope_invalid"
            )
    record = {
        "authority_ref": _evidence_ref(
            value.get("authority_ref"), "handoff_authority_ref_invalid"
        ),
        "producer_ref": _evidence_ref(
            value.get("producer_ref"), "handoff_producer_ref_invalid"
        ),
        "scope": scope,
        "valid_from": _optional_datetime(
            value.get("valid_from"), "handoff_authorization_valid_from_invalid"
        ),
        "valid_until": _optional_datetime(
            value.get("valid_until"), "handoff_authorization_valid_until_invalid"
        ),
        "revoked_at": _optional_datetime(
            value.get("revoked_at"), "handoff_authorization_revoked_at_invalid"
        ),
    }
    if (
        record["valid_from"] is not None
        and record["valid_until"] is not None
        and record["valid_until"] < record["valid_from"]
    ):
        raise LocalActionsHandoffError("handoff_authorization_window_invalid")
    return record


def build_local_handoff_package(
    *,
    payload,
    issuer_ref,
    execution_boundary,
    producer_authorization,
    transport_ref=None,
    produced_at,
    declared_trusted=False,
):
    """Build a candidate local→Actions handoff package.

    ``declared_trusted`` is recorded only as a negative-path signal. The package
    status remains ``candidate``; Actions must verify before T1-a admission.
    """
    authorization = _normalize_producer_authorization(producer_authorization)
    package = {
        "kind": HANDOFF_KIND,
        "schema_version": HANDOFF_VERSION,
        "status": "candidate",
        "issuer_ref": _evidence_ref(issuer_ref, "handoff_issuer_ref_invalid"),
        "execution_boundary": _normalize_execution_boundary(execution_boundary),
        "producer_authorization": {
            "authority_ref": authorization["authority_ref"],
            "producer_ref": authorization["producer_ref"],
            "scope": copy.deepcopy(authorization["scope"]),
            "valid_from": (
                authorization["valid_from"].isoformat()
                if authorization["valid_from"] is not None
                else None
            ),
            "valid_until": (
                authorization["valid_until"].isoformat()
                if authorization["valid_until"] is not None
                else None
            ),
            "revoked_at": (
                authorization["revoked_at"].isoformat()
                if authorization["revoked_at"] is not None
                else None
            ),
        },
        "transport_ref": _optional_evidence_ref(
            transport_ref, "handoff_transport_ref_invalid"
        ),
        "payload": _normalize_payload(payload),
        "produced_at": _datetime(
            produced_at, "handoff_produced_at_invalid"
        ).isoformat(),
        "declared_trusted": _flag(
            declared_trusted, "handoff_declared_trusted_flag_invalid"
        ),
        "reasons": [],
        "limitations": [
            "handoff_does_not_admit_evidence",
            "handoff_does_not_decide_required_gate",
            "declared_trusted_is_not_authority",
            "fixture_verifiers_are_not_production_trust_roots",
        ],
        "handoff_ref": None,
    }
    if package["declared_trusted"]:
        package["reasons"].append("local_declared_trusted_ignored")
        package["limitations"].append(
            "local_self_declared_trusted_rejected_as_authority"
        )
    package["handoff_ref"] = _digest(
        {key: value for key, value in package.items() if key != "handoff_ref"}
    )
    return package


def validate_local_handoff_package(package):
    """Recompute the handoff digest and enforce envelope invariants."""
    if not isinstance(package, dict):
        raise LocalActionsHandoffError("handoff_package_invalid")
    if package.get("kind") != HANDOFF_KIND:
        raise LocalActionsHandoffError("handoff_kind_invalid")
    if package.get("schema_version") != HANDOFF_VERSION:
        raise LocalActionsHandoffError("handoff_schema_unsupported")
    stored = _internal_ref(package.get("handoff_ref"), "handoff_ref_invalid")
    payload = {key: value for key, value in package.items() if key != "handoff_ref"}
    if _digest(payload) != stored:
        raise LocalActionsHandoffError("handoff_ref_mismatch")
    if package.get("status") not in PACKAGE_STATUSES:
        raise LocalActionsHandoffError("handoff_status_invalid")
    _evidence_ref(package.get("issuer_ref"), "handoff_issuer_ref_invalid")
    boundary = _normalize_execution_boundary(package.get("execution_boundary"))
    if boundary != package.get("execution_boundary"):
        raise LocalActionsHandoffError("handoff_execution_boundary_mismatch")
    authorization = _normalize_producer_authorization(
        package.get("producer_authorization")
    )
    # Datetimes were stored as ISO strings; compare via normalized form rebuilt
    # from the stored authorization fields after round-trip through build.
    rebuilt_auth = {
        "authority_ref": authorization["authority_ref"],
        "producer_ref": authorization["producer_ref"],
        "scope": authorization["scope"],
        "valid_from": (
            authorization["valid_from"].isoformat()
            if authorization["valid_from"] is not None
            else None
        ),
        "valid_until": (
            authorization["valid_until"].isoformat()
            if authorization["valid_until"] is not None
            else None
        ),
        "revoked_at": (
            authorization["revoked_at"].isoformat()
            if authorization["revoked_at"] is not None
            else None
        ),
    }
    stored_auth = package.get("producer_authorization")
    if not isinstance(stored_auth, dict):
        raise LocalActionsHandoffError("handoff_producer_authorization_invalid")
    for key in ("authority_ref", "producer_ref", "scope"):
        if stored_auth.get(key) != rebuilt_auth[key]:
            raise LocalActionsHandoffError("handoff_producer_authorization_mismatch")
    for key in ("valid_from", "valid_until", "revoked_at"):
        raw = stored_auth.get(key)
        expected = rebuilt_auth[key]
        if raw is None and expected is None:
            continue
        if raw is None or expected is None:
            raise LocalActionsHandoffError("handoff_producer_authorization_mismatch")
        if (
            _datetime(raw, "handoff_authorization_window_invalid").isoformat()
            != expected
        ):
            raise LocalActionsHandoffError("handoff_producer_authorization_mismatch")
    normalized_payload = _normalize_payload(package.get("payload"))
    if normalized_payload["payload_digest"] != package.get("payload", {}).get(
        "payload_digest"
    ):
        raise LocalActionsHandoffError("handoff_payload_digest_mismatch")
    _flag(package.get("declared_trusted"), "handoff_declared_trusted_flag_invalid")
    _datetime(package.get("produced_at"), "handoff_produced_at_invalid")
    return package


def _authorization_active_at(authorization, *, instant, expired_code, not_yet_code):
    """Return whether the grant covers ``instant`` (revocation checked separately)."""
    if (
        authorization["valid_from"] is not None
        and instant < authorization["valid_from"]
    ):
        return False, not_yet_code
    if (
        authorization["valid_until"] is not None
        and instant > authorization["valid_until"]
    ):
        return False, expired_code
    return True, None


def _normalize_consumer_scope(value):
    """Independent Actions/#66 consumer Action×Target×Phase×Check set.

    This is never taken from the producer-claimed grant. Empty ``check_ids``
    is invalid: unrestricted materials must not be released without a concrete
    consumer Check set.
    """
    if not isinstance(value, dict):
        raise LocalActionsHandoffError("handoff_consumer_scope_invalid")
    unknown = set(value) - _CONSUMER_SCOPE_REQUIRED - _CONSUMER_SCOPE_OPTIONAL
    if unknown:
        raise LocalActionsHandoffError("handoff_consumer_scope_invalid")
    missing = _CONSUMER_SCOPE_REQUIRED - set(value)
    if missing:
        raise LocalActionsHandoffError("handoff_consumer_scope_incomplete")
    check_ids = value.get("check_ids")
    if not isinstance(check_ids, (list, tuple)) or not check_ids:
        raise LocalActionsHandoffError("handoff_consumer_check_set_missing")
    normalized_checks = []
    for item in check_ids:
        text = _text(item, "handoff_consumer_check_id_invalid")
        if text not in normalized_checks:
            normalized_checks.append(text)
    record = {
        "action": _text(value.get("action"), "handoff_consumer_scope_invalid"),
        "target_ref": _text(value.get("target_ref"), "handoff_consumer_scope_invalid"),
        "phase": _text(value.get("phase"), "handoff_consumer_scope_invalid"),
        "check_ids": normalized_checks,
    }
    for key in _CONSUMER_SCOPE_OPTIONAL:
        if value.get(key) is not None:
            record[key] = _text(value.get(key), "handoff_consumer_scope_invalid")
    return record


def _resolve_authenticated_execution_time(authority_result, package):
    """Extract authenticated execution time from ``execution_authority``.

    Bare ``True`` proves boundary acceptance only — not an authoritative clock.
    Package ``produced_at`` is never used alone as the authorization instant.
    """
    if authority_result is True:
        return None, "execution_time_unverified"
    if authority_result is False or authority_result is None:
        return None, "execution_authority_rejected"
    if not isinstance(authority_result, dict):
        raise LocalActionsHandoffError("handoff_execution_authority_result_invalid")
    if authority_result.get("accepted") is not True:
        return None, "execution_authority_rejected"
    executed_at = authority_result.get("executed_at")
    if executed_at is None:
        return None, "execution_time_unverified"
    executed = _datetime(executed_at, "handoff_executed_at_invalid")
    claimed = _datetime(package["produced_at"], "handoff_produced_at_invalid")
    if executed != claimed:
        return None, "execution_time_mismatch_produced_at"
    return executed, None


def _match_consumer_authorization(authorization, consumer_scope, payload=None):
    """Compare producer grant and payload to the independent consumer scope.

    Returns ``(authorized_check_ids, remaining_work, blocking_reasons)``.
    """
    grant = authorization["scope"]
    blocking = []
    if grant["action"] != consumer_scope["action"]:
        blocking.append("producer_authorization_action_mismatch")
    if grant["target_ref"] != consumer_scope["target_ref"]:
        blocking.append("producer_authorization_target_mismatch")
    grant_phase = grant.get("phase")
    if grant_phase is None or grant_phase != consumer_scope["phase"]:
        blocking.append("producer_authorization_phase_mismatch")
    for field in ("candidate_digest", "scope_id", "event_ref", "expected_set_ref"):
        grant_value = grant.get(field)
        if grant_value is not None and consumer_scope.get(field) != grant_value:
            blocking.append(f"producer_authorization_{field}_mismatch")
    if isinstance(payload, dict):
        consumer_scope_id = consumer_scope.get("scope_id")
        consumer_expected_set = consumer_scope.get("expected_set_ref")
        for job_receipt in payload.get("job_receipts") or ():
            if not isinstance(job_receipt, dict):
                continue
            receipt_scope = job_receipt.get("scope_id")
            if (
                consumer_scope_id is not None
                and isinstance(receipt_scope, str)
                and receipt_scope != consumer_scope_id
                and "handoff_payload_scope_id_mismatch" not in blocking
            ):
                blocking.append("handoff_payload_scope_id_mismatch")
            receipt_expected_set = job_receipt.get("expected_set_ref")
            if (
                consumer_expected_set is not None
                and isinstance(receipt_expected_set, str)
                and receipt_expected_set != consumer_expected_set
                and "handoff_payload_expected_set_mismatch" not in blocking
            ):
                blocking.append("handoff_payload_expected_set_mismatch")

    grant_checks = set(grant["check_ids"])
    authorized = [
        check_id for check_id in consumer_scope["check_ids"] if check_id in grant_checks
    ]
    remaining_work = [
        {
            "check_id": check_id,
            "reason": "producer_authorization_check_not_granted",
        }
        for check_id in consumer_scope["check_ids"]
        if check_id not in grant_checks
    ]
    if not authorized and not blocking:
        blocking.append("producer_authorization_checks_none_granted")
    return authorized, remaining_work, blocking


def _accept_authorization_authority_decision(
    decision, *, handoff_ref, consumer_scope_ref
):
    if decision is True:
        return True
    if not isinstance(decision, dict) or decision.get("accepted") is not True:
        return False
    if "handoff_ref" in decision and decision.get("handoff_ref") != handoff_ref:
        return False
    return not (
        "consumer_scope_ref" in decision
        and decision.get("consumer_scope_ref") != consumer_scope_ref
    )


def _authorization_record(authorization):
    return {
        "authority_ref": authorization["authority_ref"],
        "producer_ref": authorization["producer_ref"],
        "scope": copy.deepcopy(authorization["scope"]),
        "valid_from": (
            authorization["valid_from"].isoformat()
            if authorization["valid_from"] is not None
            else None
        ),
        "valid_until": (
            authorization["valid_until"].isoformat()
            if authorization["valid_until"] is not None
            else None
        ),
        "revoked_at": (
            authorization["revoked_at"].isoformat()
            if authorization["revoked_at"] is not None
            else None
        ),
    }


def verify_local_handoff(
    package,
    *,
    handoff_policy,
    consumer_scope,
    issuer_authority=None,
    execution_authority=None,
    authorization_authority=None,
    evaluated_at,
):
    """Verify a local handoff package at the Actions/ECI boundary.

    ``consumer_scope`` is the independently established Actions/#66
    Action×Target×Phase×Check set. The producer grant is compared to that
    consumer scope; the grant is never treated as the current scope.

    ``execution_authority`` must return ``{"accepted": True, "executed_at": ...}``
    to authenticate execution/collection time. Bare ``True`` leaves execution
    time unverified; package ``produced_at`` alone is not an authorization proof.

    Declared local trust is ignored as authority. Uncontrolled or self-reported
    execution boundaries never verify.
    """
    validate_local_handoff_package(package)
    if not isinstance(handoff_policy, dict):
        raise LocalActionsHandoffError("handoff_policy_invalid")
    allowed_issuers = handoff_policy.get("allowed_issuers")
    allowed_boundaries = handoff_policy.get("allowed_execution_boundaries")
    if not isinstance(allowed_issuers, list) or not allowed_issuers:
        raise LocalActionsHandoffError("handoff_policy_issuers_missing")
    if not isinstance(allowed_boundaries, list) or not allowed_boundaries:
        raise LocalActionsHandoffError("handoff_policy_boundaries_missing")
    consumer = _normalize_consumer_scope(consumer_scope)
    evaluated = _datetime(evaluated_at, "handoff_evaluated_at_invalid")
    issuer_authority = _callable_or_none(
        issuer_authority, "handoff_issuer_authority_invalid"
    )
    execution_authority = _callable_or_none(
        execution_authority, "handoff_execution_authority_invalid"
    )
    authorization_authority = _callable_or_none(
        authorization_authority, "handoff_authorization_authority_invalid"
    )

    # Informational reasons (e.g. declared_trusted) never block verification;
    # blocking reasons decide verified/rejected/unverified.
    informational = []
    blocking = []
    if package.get("declared_trusted"):
        informational.append("local_declared_trusted_ignored")

    issuer = package["issuer_ref"]
    if not any(
        json.dumps(item, sort_keys=True) == json.dumps(issuer, sort_keys=True)
        for item in allowed_issuers
    ):
        blocking.append("handoff_issuer_not_allowed")

    boundary = package["execution_boundary"]
    if boundary["kind"] not in allowed_boundaries:
        blocking.append("handoff_execution_boundary_not_allowed")
    if boundary["kind"] == "uncontrolled_developer_workstation":
        blocking.append("uncontrolled_local_execution")
    if boundary["self_reported"]:
        blocking.append("self_reported_execution_evidence")
    if not boundary["controlled"]:
        blocking.append("execution_boundary_uncontrolled")

    authorization = _normalize_producer_authorization(package["producer_authorization"])
    authorized_checks, remaining_work, scope_blocking = _match_consumer_authorization(
        authorization, consumer, payload=package["payload"]
    )
    blocking.extend(scope_blocking)

    executed_at = None
    if issuer_authority is None:
        blocking.append("issuer_authority_missing")
    elif issuer_authority(package) is not True:
        blocking.append("issuer_authority_rejected")

    if execution_authority is None:
        blocking.append("execution_authority_missing")
    else:
        executed_at, exec_reason = _resolve_authenticated_execution_time(
            execution_authority(package), package
        )
        if exec_reason is not None:
            blocking.append(exec_reason)

    consumer_scope_ref = _digest(consumer)
    if authorization_authority is None:
        blocking.append("authorization_authority_missing")
    else:
        auth_context = {
            "package": package,
            "handoff_ref": package["handoff_ref"],
            "consumer_scope": copy.deepcopy(consumer),
            "consumer_scope_ref": consumer_scope_ref,
            "producer_authorization": _authorization_record(authorization),
            "evaluated_at": evaluated.isoformat(),
            "executed_at": executed_at.isoformat() if executed_at is not None else None,
        }
        if not _accept_authorization_authority_decision(
            authorization_authority(auth_context),
            handoff_ref=package["handoff_ref"],
            consumer_scope_ref=consumer_scope_ref,
        ):
            blocking.append("authorization_authority_rejected")

    if executed_at is not None:
        if executed_at > evaluated:
            blocking.append("execution_time_after_evaluation")
        active_at_execution, exec_auth_reason = _authorization_active_at(
            authorization,
            instant=executed_at,
            expired_code="producer_authorization_expired_at_execution",
            not_yet_code="producer_authorization_not_yet_valid_at_execution",
        )
        if not active_at_execution:
            blocking.append(exec_auth_reason)
        if (
            authorization["revoked_at"] is not None
            and executed_at >= authorization["revoked_at"]
        ):
            blocking.append("producer_authorization_revoked_at_execution")

    active_at_evaluation, eval_auth_reason = _authorization_active_at(
        authorization,
        instant=evaluated,
        expired_code="producer_authorization_expired",
        not_yet_code="producer_authorization_not_yet_valid",
    )
    if not active_at_evaluation and eval_auth_reason not in blocking:
        blocking.append(eval_auth_reason)
    if (
        authorization["revoked_at"] is not None
        and evaluated >= authorization["revoked_at"]
        and "producer_authorization_revoked_at_execution" not in blocking
    ):
        blocking.append("producer_authorization_revoked")

    reasons = informational + blocking
    # Provenance authentication is independent of consumer Check coverage.
    provenance_authenticated = not blocking
    if provenance_authenticated and authorized_checks and not remaining_work:
        consumer_coverage = "complete"
    elif provenance_authenticated and authorized_checks and remaining_work:
        consumer_coverage = "partial"
    else:
        consumer_coverage = "none"

    # Full verify only when provenance is authenticated and every consumer check
    # is granted. Partial coverage must not advertise the whole handoff as
    # verified or release Check-ID-only dicts as T1-a materials.
    verified = provenance_authenticated and consumer_coverage == "complete"
    partial = provenance_authenticated and consumer_coverage == "partial"
    if verified:
        status = "verified"
        provenance_status = "authenticated"
    elif partial:
        status = "partial"
        provenance_status = "authenticated"
        if "producer_authorization_scope_partial" not in reasons:
            reasons.append("producer_authorization_scope_partial")
    elif (
        "issuer_authority_missing" in blocking
        or "execution_authority_missing" in blocking
        or "authorization_authority_missing" in blocking
        or "execution_time_unverified" in blocking
    ):
        status = "unverified"
        provenance_status = "unverified"
    else:
        status = "rejected"
        provenance_status = "rejected"

    materials = None
    admitted_for_t1a = False
    if verified:
        # Only complete coverage with raw Reports/Receipts is T1-a-consumable.
        admitted_for_t1a = True
        materials = {
            "authorized_check_ids": list(authorized_checks),
            "reports": copy.deepcopy(package["payload"]["reports"]),
            "job_receipts": copy.deepcopy(package["payload"]["job_receipts"]),
            "result_ids": list(package["payload"]["result_ids"]),
            "source_digests": list(package["payload"]["source_digests"]),
        }

    if provenance_authenticated:
        effective_remaining = list(remaining_work)
    else:
        grant_checks = set(authorization["scope"]["check_ids"])
        primary_block = blocking[0] if blocking else "handoff_unverified"
        effective_remaining = [
            {
                "check_id": check_id,
                "reason": (
                    primary_block
                    if check_id in grant_checks
                    else "producer_authorization_check_not_granted"
                ),
            }
            for check_id in consumer["check_ids"]
        ]

    result = {
        "kind": HANDOFF_KIND,
        "schema_version": HANDOFF_VERSION,
        "status": status,
        "provenance_status": provenance_status,
        "consumer_coverage": consumer_coverage,
        "handoff_ref": package["handoff_ref"],
        "payload_digest": package["payload"]["payload_digest"],
        "issuer_ref": copy.deepcopy(package["issuer_ref"]),
        "execution_boundary": copy.deepcopy(package["execution_boundary"]),
        "producer_authorization": _authorization_record(authorization),
        "consumer_scope": copy.deepcopy(consumer),
        "evaluated_at": evaluated.isoformat(),
        "executed_at": executed_at.isoformat() if executed_at is not None else None,
        "authorized_check_ids": (
            list(authorized_checks) if provenance_authenticated else []
        ),
        "remaining_work": effective_remaining,
        "admitted_for_t1a": admitted_for_t1a,
        "materials": materials,
        "reasons": reasons,
        "limitations": [
            "handoff_verification_is_not_t1a_admission",
            "handoff_does_not_decide_required_gate",
            "fixture_verifiers_are_not_production_trust_roots",
            "local_and_actions_identities_remain_distinct",
            "produced_at_is_not_authorization_proof",
            "consumer_scope_is_independent_of_producer_grant",
            "check_id_allowlist_is_not_t1a_material",
        ],
        "verification_ref": None,
    }
    if not verified:
        result["limitations"].append("unverified_handoff_must_run_actions_checks")
    if partial:
        result["limitations"].append(
            "partial_authorization_not_ready_for_t1a_without_bound_raw_materials"
        )
    result["verification_ref"] = _digest(
        {key: value for key, value in result.items() if key != "verification_ref"}
    )
    return result
