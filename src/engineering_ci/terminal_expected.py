"""#52-D4 Terminal Check Expected → post_completion Plan binding.

Binds one authorized #66-B2 ``weaver_terminal`` Check Expected
(pre-execution ``scope_id`` / ``expected_check_ref``; phase from the parent
Expected v2 / Profile projection) onto an existing
``selection.post_completion_plan`` consumption closure, using a **separate**
completed Run/Attempt/Terminal proof — never by stuffing ``run_id`` back into
the Expected entry.

This module does **not**:

- invent a Check Expected when #66-B2 / authority is missing;
- confuse Weaver Span Expectations JSON with Check Expected;
- reimplement ``post_completion_plan`` / ``validate_post_completion_binding``;
- produce Native Results, #66-C Receipts, #63 Admission or #61 Required;
- implement a #66-B2 Phase Producer;
- wire ``release.yml`` or production orchestration.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from . import check_contract, expected_checks, selection

KIND = "engineering-ci-terminal-expected-binding"
VERSION = 1
TERMINAL_CHECK_ID = "eci/weaver/terminal"
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA256_REF_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_INFORMATIONAL = frozenset({"contract_sample_inputs_not_production_terminal"})
INPUT_CLOSURE_STATES = frozenset({"verified", "unverified", "failed"})
# Post-hoc Run fields must not ride on the pre-execution Expected identity.
_POST_HOC_ENTRY_FIELDS = frozenset({"run_id", "run_attempt", "terminal_run"})


class TerminalExpectedError(ValueError):
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
        raise TerminalExpectedError(code)
    return value


def _flag(value, code):
    if not isinstance(value, bool):
        raise TerminalExpectedError(code)
    return value


def _internal_ref(value, code):
    text = _text(value, code)
    if not _SHA256_REF_RE.fullmatch(text):
        raise TerminalExpectedError(code)
    return text


def _closure_digest(value, code):
    """Post-completion Plan closure uses bare hex via selection._stable_digest."""
    text = _text(value, code)
    if _HEX64_RE.fullmatch(text) or _SHA256_REF_RE.fullmatch(text):
        return text
    raise TerminalExpectedError(code)


def _callable_or_none(value, code):
    if value is None:
        return None
    if not callable(value):
        raise TerminalExpectedError(code)
    return value


def _parent_fields(parent):
    """Deterministic parent Expected v2 / projection facts (no Phase Producer)."""
    if not isinstance(parent, dict):
        raise TerminalExpectedError("terminal_expected_parent_invalid")
    scope_id = parent.get("scope_id")
    if scope_id is None:
        raise TerminalExpectedError("terminal_expected_scope_id_missing")
    scope_id = _text(scope_id, "terminal_expected_scope_id_invalid")
    phase = parent.get("phase")
    phase_status = parent.get("phase_status")
    status = parent.get("status")
    schema_version = parent.get("schema_version")
    projection_source = parent.get("projection_source")
    return {
        "scope_id": scope_id,
        "phase": phase,
        "phase_status": phase_status,
        "status": status,
        "schema_version": schema_version,
        "projection_source": projection_source,
        "checks": parent.get("checks"),
        "parent": parent,
    }


def _extract_entry(expected):
    """Accept a parent Expected v2 scope, or a bare entry plus parent metadata.

    Real #66-B2 Check Entries do **not** carry ``run_id`` / ``run_attempt``.
    Phase and ``scope_id`` live on the authorized parent projection.
    """
    if not isinstance(expected, dict):
        raise TerminalExpectedError("terminal_expected_invalid")

    if expected.get("check_id") == TERMINAL_CHECK_ID and "checks" not in expected:
        # Bare entry — caller must supply parent projection via completed binding
        # path; extract returns entry with no parent proof yet.
        entry = copy.deepcopy(expected)
        return entry, None

    checks = expected.get("checks")
    if not isinstance(checks, list):
        raise TerminalExpectedError("terminal_expected_missing")
    matches = [
        item
        for item in checks
        if isinstance(item, dict) and item.get("check_id") == TERMINAL_CHECK_ID
    ]
    if not matches:
        raise TerminalExpectedError("terminal_expected_missing")
    if len(matches) != 1:
        raise TerminalExpectedError("terminal_expected_ambiguous")
    entry = copy.deepcopy(matches[0])
    return entry, _parent_fields(expected)


def _validate_terminal_entry(entry, *, proven_phase):
    """Validate pre-execution Expected shape; phase may come from parent only."""
    for field in _POST_HOC_ENTRY_FIELDS:
        if field in entry:
            raise TerminalExpectedError("terminal_expected_post_hoc_run_forbidden")
    declared = entry.get("expected_check_ref")
    if declared is None:
        raise TerminalExpectedError("terminal_expected_check_ref_missing")
    declared = _text(declared, "terminal_expected_check_ref_invalid")
    computed = expected_checks.entry_identity(
        {key: value for key, value in entry.items() if key != "expected_check_ref"}
    )
    if declared != computed:
        raise TerminalExpectedError("terminal_expected_check_ref_mismatch")

    # Structural check_contract view may attach parent-proven phase without
    # reminting the stored Expected identity (entry itself stays untouched).
    validation_view = copy.deepcopy(entry)
    if validation_view.get("phase") is None:
        validation_view["phase"] = proven_phase
    elif validation_view.get("phase") != proven_phase:
        raise TerminalExpectedError("terminal_expected_phase_mismatch")
    try:
        check_contract.validate_expected_instance(validation_view)
    except check_contract.CheckContractError as error:
        raise TerminalExpectedError("terminal_expected_instance_invalid") from error
    if entry.get("check_id") != TERMINAL_CHECK_ID:
        raise TerminalExpectedError("terminal_expected_check_id_invalid")
    return entry, declared


# Current #66-B2 validate_projection accepts only these phase_status values.
_B2_PHASE_STATUSES = frozenset({None, "not_applicable", "unverified"})


def _freeze_parent_phase(parent_meta, entry):
    """Read phase from frozen B2 parent — never upgrade phase_status here."""
    entry_phase = entry.get("phase")
    if parent_meta is None:
        if entry_phase != check_contract.POST_COMPLETION_PHASE:
            raise TerminalExpectedError("terminal_expected_phase_unproven")
        return entry_phase, "unverified"

    parent_phase = parent_meta.get("phase")
    if parent_phase is None:
        raise TerminalExpectedError("terminal_expected_parent_phase_missing")
    if parent_phase != check_contract.POST_COMPLETION_PHASE:
        raise TerminalExpectedError("terminal_expected_phase_invalid")
    if entry_phase is not None and entry_phase != parent_phase:
        raise TerminalExpectedError("terminal_expected_phase_mismatch")
    phase_status = parent_meta.get("phase_status")
    if phase_status is None:
        phase_status = "unverified"
    # Current validate_projection rejects phase_status=verified on the parent.
    if phase_status not in _B2_PHASE_STATUSES:
        raise TerminalExpectedError("terminal_expected_parent_phase_status_unsupported")
    parent_status = parent_meta.get("status")
    if parent_status == "complete" and phase_status == "unverified":
        raise TerminalExpectedError("terminal_expected_phase_complete_unproven")
    return parent_phase, phase_status


def _require_parent_scope(parent_meta, entry, *, scope_id_fallback=None):
    """Deterministic parent identity — does not treat phase as domain-complete."""
    reasons = []
    gaps = []
    if parent_meta is None:
        scope_id = entry.get("scope_id") or scope_id_fallback
        if scope_id is None:
            raise TerminalExpectedError("terminal_expected_scope_id_missing")
        reasons.append("terminal_expected_parent_missing")
        gaps.append(
            {
                "kind": "expected_scope",
                "ref": scope_id,
                "reason": "parent_projection_missing",
            }
        )
        return (
            _text(scope_id, "terminal_expected_scope_id_invalid"),
            None,
            "unverified",
            reasons,
            gaps,
        )

    scope_id = parent_meta["scope_id"]
    entry_scope = entry.get("scope_id")
    if entry_scope is not None and entry_scope != scope_id:
        reasons.append("terminal_expected_scope_mismatch")
        gaps.append(
            {
                "kind": "expected_scope",
                "ref": entry_scope,
                "reason": "entry_parent_scope_mismatch",
            }
        )
    status = parent_meta.get("status")
    phase_status = parent_meta.get("phase_status") or "unverified"
    if status == "complete" and phase_status == "unverified":
        raise TerminalExpectedError("terminal_expected_phase_complete_unproven")
    if status not in {"complete", "partial"}:
        reasons.append("terminal_expected_scope_incomplete")
        gaps.append(
            {
                "kind": "expected_scope",
                "ref": scope_id,
                "reason": f"scope_status_{status}",
            }
        )
    # Declared phase without a separate Phase Witness stays unverified on parent.
    if phase_status == "unverified":
        reasons.append("terminal_expected_phase_unverified")
        gaps.append(
            {
                "kind": "expected_phase",
                "ref": scope_id,
                "reason": "phase_witness_required",
            }
        )
    schema_version = parent_meta.get("schema_version")
    if schema_version not in {
        None,
        expected_checks.EXPECTED_VERSION,
        expected_checks.EXPECTED_V2,
    }:
        reasons.append("terminal_expected_schema_unsupported")
        gaps.append(
            {
                "kind": "expected_scope",
                "ref": scope_id,
                "reason": "schema_unsupported",
            }
        )
    parent = parent_meta.get("parent") or {}
    declared_set_ref = parent.get("expected_set_ref")
    if declared_set_ref is None:
        reasons.append("terminal_expected_set_ref_missing")
        gaps.append(
            {
                "kind": "expected_scope",
                "ref": scope_id,
                "reason": "expected_set_ref_missing",
            }
        )
        return scope_id, None, "frozen", reasons, gaps

    schema_version = parent.get("schema_version", expected_checks.EXPECTED_VERSION)
    recomputed_set_ref = expected_checks.set_identity(
        parent.get("checks") or [],
        parent.get("non_check_obligations") or (),
        schema_version=schema_version,
    )
    if declared_set_ref != recomputed_set_ref:
        reasons.append("terminal_expected_set_ref_mismatch")
        gaps.append(
            {
                "kind": "expected_scope",
                "ref": declared_set_ref,
                "reason": "expected_set_ref_recompute_mismatch",
            }
        )
        return scope_id, declared_set_ref, "frozen", reasons, gaps
    return scope_id, declared_set_ref, "frozen", reasons, gaps


def _witness_payload(witness):
    """Canonical Phase Witness body — digest excludes witness_ref itself."""
    return {key: value for key, value in witness.items() if key != "witness_ref"}


def _phase_witness_structure(
    witness, *, scope_id, expected_set_ref, phase, profile_binding
):
    """Deterministic Scope/Set/Profile/time/digest association only.

    A matching ``witness_ref`` proves the payload is self-consistent for this
    call — it does **not** authenticate the issuer or authorize production.
    """
    if witness is None:
        return False, "missing", ["terminal_phase_witness_missing"]
    if not isinstance(witness, dict):
        raise TerminalExpectedError("terminal_phase_witness_invalid")
    if witness.get("accepted") is not True or witness.get("status") != "authorized":
        return False, "rejected", ["terminal_phase_witness_rejected"]
    if witness.get("phase") != phase:
        return False, "rejected", ["terminal_phase_witness_phase_mismatch"]
    if witness.get("scope_id") != scope_id:
        return False, "rejected", ["terminal_phase_witness_scope_mismatch"]
    if (
        expected_set_ref is not None
        and witness.get("expected_set_ref") != expected_set_ref
    ):
        return False, "rejected", ["terminal_phase_witness_expected_set_mismatch"]
    if witness.get("domain_complete") is not True:
        return False, "incomplete", ["terminal_phase_witness_domain_incomplete"]

    issuer = witness.get("issuer_ref")
    if not isinstance(issuer, dict) or not issuer.get("sha256"):
        return False, "untrusted", ["terminal_phase_witness_issuer_unproven"]
    if witness.get("profile_revision") != profile_binding.get("profile_revision"):
        return False, "untrusted", ["terminal_phase_witness_profile_revision_mismatch"]
    if witness.get("profile_ref") != profile_binding.get("profile_ref"):
        return False, "untrusted", ["terminal_phase_witness_profile_ref_mismatch"]
    validated_at = witness.get("validated_at")
    if not isinstance(validated_at, str) or not validated_at:
        return False, "untrusted", ["terminal_phase_witness_validated_at_invalid"]
    try:
        text = (
            validated_at[:-1] + "+00:00" if validated_at.endswith("Z") else validated_at
        )
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return False, "untrusted", ["terminal_phase_witness_validated_at_invalid"]
    except ValueError:
        return False, "untrusted", ["terminal_phase_witness_validated_at_invalid"]

    declared_ref = witness.get("witness_ref")
    if not isinstance(declared_ref, str) or not declared_ref:
        return False, "untrusted", ["terminal_phase_witness_ref_missing"]
    recomputed = _digest(_witness_payload(witness))
    if declared_ref != recomputed:
        return False, "untrusted", ["terminal_phase_witness_ref_mismatch"]
    return True, "structurally_aligned", []


def _bind_phase_witness(
    witness,
    *,
    scope_id,
    expected_set_ref,
    phase,
    profile_binding,
    authority=None,
):
    """Associate a Phase Witness, then require ``phase_witness_authority``.

    ``seal_phase_witness`` / matching ``witness_ref`` only prove structure.
    Production ``phase_ok`` needs a trusted upstream authority callback (stand-in
    for a future #66-B2 Phase/Obligation Producer verification result).
    """
    ok, status, reasons = _phase_witness_structure(
        witness,
        scope_id=scope_id,
        expected_set_ref=expected_set_ref,
        phase=phase,
        profile_binding=profile_binding,
    )
    if not ok:
        return False, status, reasons
    if authority is None:
        return (
            False,
            "structurally_aligned",
            ["terminal_phase_witness_authority_missing"],
        )
    context = {
        "phase_witness": copy.deepcopy(witness),
        "scope_id": scope_id,
        "expected_set_ref": expected_set_ref,
        "phase": phase,
        "profile_ref": copy.deepcopy(profile_binding.get("profile_ref")),
        "profile_revision": profile_binding.get("profile_revision"),
    }
    accepted, reason = _authority_accepted(authority, context)
    if accepted:
        return True, "authority_verified", []
    mapped = {
        "terminal_expected_authority_missing": (
            "terminal_phase_witness_authority_missing"
        ),
        "terminal_expected_authority_rejected": (
            "terminal_phase_witness_authority_rejected"
        ),
        "terminal_expected_authority_unverified": (
            "terminal_phase_witness_authority_unverified"
        ),
    }.get(reason, reason or "terminal_phase_witness_authority_rejected")
    return False, "unverified", [mapped]


def _bind_projection_proof(
    proof,
    *,
    scope_id,
    expected_set_ref,
    parent,
    git=None,
    expected_plan=None,
    expected_root=None,
):
    """Prove parent projection via live ``validate_projection`` only.

    Caller-supplied receipts (``accepted=True`` dictionaries) are recorded as
    untrusted structural claims and **never** elevate production authorization.
    The Terminal ``post_completion_plan`` must not be passed here — supply the
    original Expected generation Plan/Git/Root instead.
    """
    if git is not None:
        if expected_plan is None:
            return False, "failed", ["terminal_projection_expected_plan_missing"]
        # Original Expected Plan carries check_scope (v1) or effective_profile
        # inputs (v2). Do not substitute the Terminal post_completion Plan.
        check_scope = None
        if isinstance(expected_plan, dict):
            check_scope = expected_plan.get("check_scope")
        try:
            expected_checks.validate_projection(
                parent,
                git,
                check_scope=check_scope,
                plan=expected_plan,
                root=expected_root,
            )
        except expected_checks.ExpectedError as error:
            code = getattr(error, "code", None) or str(error)
            return False, "failed", [f"terminal_projection_validation_{code}"]
        return True, "validate_projection", []

    if proof is None:
        return False, "unverified", ["terminal_projection_proof_missing"]
    if not isinstance(proof, dict):
        raise TerminalExpectedError("terminal_projection_proof_invalid")
    # Structural field checks only — never trusted for authorized=True.
    reasons = ["terminal_projection_proof_untrusted"]
    if proof.get("validator") != "expected_checks.validate_projection":
        reasons.append("terminal_projection_proof_validator_invalid")
    if proof.get("scope_id") != scope_id:
        reasons.append("terminal_projection_proof_scope_mismatch")
    if (
        expected_set_ref is not None
        and proof.get("expected_set_ref") != expected_set_ref
    ):
        reasons.append("terminal_projection_proof_expected_set_mismatch")
    return False, "untrusted", reasons


def _normalize_completed_run(value):
    """Separate completed Run/Attempt/Terminal proof — not part of Expected."""
    if not isinstance(value, dict):
        raise TerminalExpectedError("terminal_completed_run_invalid")
    run_id = _text(value.get("run_id"), "terminal_completed_run_id_invalid")
    attempt = value.get("attempt", value.get("run_attempt"))
    if type(attempt) is not int or attempt < 1:
        raise TerminalExpectedError("terminal_completed_run_attempt_invalid")
    result = _text(value.get("result"), "terminal_completed_run_result_invalid")
    status = value.get("status", "completed")
    if status != "completed":
        raise TerminalExpectedError("terminal_completed_run_not_completed")
    completed = {
        "run_id": run_id,
        "attempt": attempt,
        "result": result,
        "status": "completed",
    }
    digest = value.get("artifact_digest") or value.get("sha256")
    if digest is not None:
        completed["artifact_digest"] = _closure_digest(
            digest, "terminal_completed_run_digest_invalid"
        )
    source = value.get("source")
    if source is not None:
        completed["source"] = _text(source, "terminal_completed_run_source_invalid")
    proof_ref = value.get("proof_ref")
    if proof_ref is not None:
        completed["proof_ref"] = _text(
            proof_ref, "terminal_completed_run_proof_ref_invalid"
        )
    return completed


def _verify_completed_run(completed, authority):
    """Self-reported Plan params cannot alone prove a Provider terminal."""
    if authority is not None:
        accepted, reason = _authority_accepted(authority, {"completed_run": completed})
        if accepted:
            return True, "authority_verified", []
        return (
            False,
            "rejected",
            [reason or "terminal_completed_run_authority_rejected"],
        )
    if completed.get("source") and completed.get("proof_ref"):
        # Structured Provider proof present but not yet re-validated here.
        return False, "unverified", ["terminal_completed_run_proof_unverified"]
    return False, "unverified", ["terminal_completed_run_source_unproven"]


def _require_post_completion_plan(plan):
    if not isinstance(plan, dict):
        raise TerminalExpectedError("terminal_plan_invalid")
    if plan.get("phase") != selection.POST_COMPLETION_PHASE:
        raise TerminalExpectedError("terminal_plan_phase_invalid")
    binding = plan.get("post_completion")
    if not isinstance(binding, dict):
        raise TerminalExpectedError("terminal_plan_binding_missing")
    for key in ("run_id", "attempt", "result", "sha256", "status"):
        if key not in binding:
            raise TerminalExpectedError("terminal_plan_binding_incomplete")
    if binding.get("status") != "completed":
        raise TerminalExpectedError("terminal_plan_not_completed")
    run_id = _text(binding.get("run_id"), "terminal_plan_run_id_invalid")
    attempt = binding.get("attempt")
    if type(attempt) is not int or attempt < 1:
        raise TerminalExpectedError("terminal_plan_attempt_invalid")
    result = _text(binding.get("result"), "terminal_plan_result_invalid")
    closure = _closure_digest(binding.get("sha256"), "terminal_plan_closure_invalid")
    return {
        "run_id": run_id,
        "attempt": attempt,
        "result": result,
        "status": "completed",
        "closure_sha256": closure,
    }


def _match_completed_run_to_plan(completed_run, plan_identity):
    """Correlate sourced completed Run with Plan — Expected stays pre-execution."""
    reasons = []
    gaps = []
    if completed_run["run_id"] != plan_identity["run_id"]:
        reasons.append("terminal_completed_run_mismatch")
        gaps.append(
            {
                "kind": "completed_run_plan_identity",
                "ref": completed_run["run_id"],
                "reason": "run_id_mismatch",
            }
        )
    if completed_run["attempt"] != plan_identity["attempt"]:
        reasons.append("terminal_completed_attempt_mismatch")
        gaps.append(
            {
                "kind": "completed_run_plan_identity",
                "ref": completed_run["attempt"],
                "reason": "attempt_mismatch",
            }
        )
    if completed_run["result"] != plan_identity["result"]:
        reasons.append("terminal_completed_result_mismatch")
        gaps.append(
            {
                "kind": "completed_run_plan_identity",
                "ref": completed_run["result"],
                "reason": "result_mismatch",
            }
        )
    return reasons, gaps


def _verify_input_closure(plan, root):
    """Reuse selection.validate_post_completion_binding — do not recompute locally."""
    if root is None:
        return "unverified", None, ["terminal_input_closure_root_missing"]
    try:
        gate = selection.validate_post_completion_binding(plan, Path(root))
    except selection.SelectionError as error:
        return (
            "failed",
            {"ok": False, "reason": str(error)},
            ["terminal_input_closure_validation_error"],
        )
    if not isinstance(gate, dict):
        return "failed", None, ["terminal_input_closure_result_invalid"]
    if gate.get("ok") is True and gate.get("input_closure_verified") == "verified":
        return "verified", gate, []
    reason = gate.get("reason") or "terminal_input_closure_unverified"
    return "failed", gate, [reason]


def _authority_accepted(authority, context):
    if authority is None:
        return False, "terminal_expected_authority_missing"
    outcome = authority(context)
    if outcome is True:
        return True, None
    if outcome is False or outcome is None:
        return False, "terminal_expected_authority_rejected"
    if not isinstance(outcome, dict):
        raise TerminalExpectedError("terminal_expected_authority_result_invalid")
    if outcome.get("accepted") is True and outcome.get("status") == "authorized":
        return True, None
    if outcome.get("status") == "unverified":
        return False, "terminal_expected_authority_unverified"
    return False, "terminal_expected_authority_rejected"


def _consistency(envelope):
    errors = []
    if envelope.get("kind") != KIND:
        errors.append("terminal_binding_kind_invalid")
    if envelope.get("schema_version") != VERSION:
        errors.append("terminal_binding_schema_unsupported")
    authorized = envelope.get("authorized")
    sample = envelope.get("contract_sample")
    status = envelope.get("status")
    input_closure = envelope.get("input_closure")
    if not isinstance(authorized, bool):
        errors.append("terminal_authorized_flag_invalid")
    if not isinstance(sample, bool):
        errors.append("terminal_contract_sample_flag_invalid")
    if status not in {"bound", "rejected", "unverified"}:
        errors.append("terminal_binding_status_invalid")
    if input_closure not in INPUT_CLOSURE_STATES:
        errors.append("terminal_input_closure_invalid")
    if authorized is True:
        if status != "bound":
            errors.append("terminal_authorized_requires_bound_status")
        if sample is not False:
            errors.append("terminal_authorized_rejects_contract_sample")
        if envelope.get("authorization_basis") != "authority_verified":
            errors.append("terminal_authorized_requires_authority_verified")
        if input_closure != "verified":
            errors.append("terminal_authorized_requires_input_closure_verified")
        if envelope.get("phase_witness_verified") is not True:
            errors.append("terminal_authorized_requires_phase_witness")
        if envelope.get("phase_witness_status") != "authority_verified":
            errors.append("terminal_authorized_requires_phase_witness_authority")
        if envelope.get("projection_verification") != "validate_projection":
            errors.append("terminal_authorized_requires_live_projection_validation")
        if envelope.get("completed_run_verified") is not True:
            errors.append("terminal_authorized_requires_completed_run_verified")
        if envelope.get("gaps"):
            errors.append("terminal_authorized_rejects_gaps")
    if envelope.get("check_id") != TERMINAL_CHECK_ID:
        errors.append("terminal_binding_check_id_invalid")
    if envelope.get("phase") != check_contract.POST_COMPLETION_PHASE:
        errors.append("terminal_binding_phase_invalid")
    try:
        _text(envelope.get("scope_id"), "terminal_binding_scope_id_invalid")
        _text(
            envelope.get("expected_check_ref"),
            "terminal_binding_expected_check_ref_invalid",
        )
    except TerminalExpectedError as error:
        errors.append(error.code)
    plan_identity = envelope.get("plan_identity")
    if not isinstance(plan_identity, dict):
        errors.append("terminal_binding_plan_identity_invalid")
    else:
        try:
            _text(plan_identity.get("run_id"), "terminal_binding_plan_run_invalid")
            _text(plan_identity.get("result"), "terminal_binding_plan_result_invalid")
            _closure_digest(
                plan_identity.get("closure_sha256"),
                "terminal_binding_plan_closure_invalid",
            )
            attempt = plan_identity.get("attempt")
            if type(attempt) is not int or attempt < 1:
                errors.append("terminal_binding_plan_attempt_invalid")
        except TerminalExpectedError as error:
            errors.append(error.code)
    completed = envelope.get("completed_run")
    if not isinstance(completed, dict):
        errors.append("terminal_binding_completed_run_invalid")
    return errors


def seal_phase_witness(
    *,
    phase,
    scope_id,
    expected_set_ref,
    profile_ref,
    profile_revision,
    issuer_ref,
    validated_at,
    domain_complete=True,
    **extra,
):
    """Structural Phase Witness constructor — digest is not source authentication.

    Callers may forge ``issuer_ref`` / ``domain_complete`` and recompute a valid
    ``witness_ref``. Trust requires ``phase_witness_authority``, not this seal.
    """
    witness = {
        "accepted": True,
        "status": "authorized",
        "phase": phase,
        "scope_id": scope_id,
        "expected_set_ref": expected_set_ref,
        "domain_complete": domain_complete,
        "profile_ref": copy.deepcopy(profile_ref),
        "profile_revision": profile_revision,
        "issuer_ref": copy.deepcopy(issuer_ref),
        "validated_at": validated_at,
        "note": "structural seal only — not a trusted #66-B2 Phase Producer result",
    }
    witness.update(extra)
    witness["witness_ref"] = _digest(_witness_payload(witness))
    return witness


def bind_terminal_expected(
    expected,
    *,
    plan,
    completed_run,
    root=None,
    expected_authority=None,
    completed_run_authority=None,
    phase_witness=None,
    phase_witness_authority=None,
    projection_proof=None,
    projection_git=None,
    expected_plan=None,
    expected_root=None,
    contract_sample=False,
):
    """Bind a frozen #66-B2 Expected onto a post_completion Plan.

    Parent Expected stays validate_projection-compatible (declared phase ⇒
    ``partial`` / ``phase_status=unverified``). Production ``authorized=True``
    requires live ``expected_checks.validate_projection`` against the **original
    Expected Plan/Git/Root** (never the Terminal Plan), a Phase Witness verified
    by ``phase_witness_authority`` (seal digest alone is insufficient),
    completed-run authority, Expected authority, and verified input closure.
    Caller-supplied ``projection_proof`` dictionaries are untrusted.
    """
    contract_sample = _flag(contract_sample, "terminal_contract_sample_flag_invalid")
    expected_authority = _callable_or_none(
        expected_authority, "terminal_expected_authority_invalid"
    )
    completed_run_authority = _callable_or_none(
        completed_run_authority, "terminal_completed_run_authority_invalid"
    )
    phase_witness_authority = _callable_or_none(
        phase_witness_authority, "terminal_phase_witness_authority_invalid"
    )
    entry, parent_meta = _extract_entry(expected)
    proven_phase, phase_basis = _freeze_parent_phase(parent_meta, entry)
    entry, expected_check_ref = _validate_terminal_entry(
        entry, proven_phase=proven_phase
    )
    plan_identity = _require_post_completion_plan(plan)
    completed = _normalize_completed_run(completed_run)

    reasons = []
    gaps = []
    if contract_sample:
        reasons.append("contract_sample_inputs_not_production_terminal")

    (
        scope_id,
        expected_set_ref,
        projection_identity,
        scope_reasons,
        scope_gaps,
    ) = _require_parent_scope(parent_meta, entry)
    reasons.extend(scope_reasons)
    gaps.extend(scope_gaps)

    parent_obj = None if parent_meta is None else parent_meta.get("parent")
    profile = {}
    if isinstance(parent_obj, dict):
        effective = parent_obj.get("effective_profile")
        if isinstance(effective, dict):
            profile = {
                "profile_ref": copy.deepcopy(effective.get("profile_ref")),
                "profile_revision": effective.get("profile_revision"),
            }
        elif isinstance(parent_obj.get("profile_ref"), dict):
            profile = {
                "profile_ref": copy.deepcopy(parent_obj.get("profile_ref")),
                "profile_revision": parent_obj.get("profile_revision"),
            }

    proj_ok, proj_basis, proj_reasons = _bind_projection_proof(
        projection_proof,
        scope_id=scope_id,
        expected_set_ref=expected_set_ref,
        parent=parent_obj,
        git=projection_git,
        expected_plan=expected_plan,
        expected_root=expected_root,
    )
    reasons.extend(proj_reasons)
    if not proj_ok:
        gaps.append(
            {
                "kind": "projection_proof",
                "ref": scope_id,
                "reason": proj_basis,
            }
        )

    phase_ok, phase_witness_status, phase_reasons = _bind_phase_witness(
        phase_witness,
        scope_id=scope_id,
        expected_set_ref=expected_set_ref,
        phase=proven_phase,
        profile_binding=profile,
        authority=phase_witness_authority,
    )
    reasons.extend(phase_reasons)
    if not phase_ok:
        gaps.append(
            {
                "kind": "phase_witness",
                "ref": scope_id,
                "reason": phase_witness_status,
            }
        )
    else:
        reasons = [
            item for item in reasons if item != "terminal_expected_phase_unverified"
        ]
        gaps = [item for item in gaps if item.get("kind") != "expected_phase"]

    run_reasons, run_gaps = _match_completed_run_to_plan(completed, plan_identity)
    reasons.extend(run_reasons)
    gaps.extend(run_gaps)

    run_ok, run_verification, run_verify_reasons = _verify_completed_run(
        completed, completed_run_authority
    )
    reasons.extend(run_verify_reasons)
    if not run_ok:
        gaps.append(
            {
                "kind": "completed_run",
                "ref": completed["run_id"],
                "reason": run_verification,
            }
        )

    input_closure, closure_gate, closure_reasons = _verify_input_closure(plan, root)
    reasons.extend(closure_reasons)
    if input_closure != "verified":
        gaps.append(
            {
                "kind": "input_closure",
                "ref": plan_identity["closure_sha256"],
                "reason": (closure_gate or {}).get("reason")
                if isinstance(closure_gate, dict)
                else closure_reasons[0],
            }
        )

    authority_context = {
        "expected": copy.deepcopy(entry),
        "scope_id": scope_id,
        "expected_check_ref": expected_check_ref,
        "expected_set_ref": expected_set_ref,
        "phase": proven_phase,
        "phase_basis": phase_basis,
        "parent_status": None if parent_meta is None else parent_meta.get("status"),
        "completed_run": copy.deepcopy(completed),
        "plan_identity": copy.deepcopy(plan_identity),
        "input_closure": input_closure,
    }
    accepted, authority_reason = _authority_accepted(
        expected_authority, authority_context
    )
    if not accepted:
        reasons.append(authority_reason)
        gaps.append(
            {
                "kind": "expected_authority",
                "ref": expected_check_ref,
                "reason": authority_reason,
            }
        )
        authorization_basis = "structural_fixture"
    else:
        authorization_basis = "authority_verified"

    blocking = [item for item in reasons if item not in _INFORMATIONAL]
    soft = {
        "terminal_expected_authority_missing",
        "terminal_expected_authority_unverified",
        "terminal_input_closure_root_missing",
        "terminal_expected_parent_missing",
        "terminal_expected_phase_unverified",
        "terminal_phase_witness_missing",
        "terminal_phase_witness_domain_incomplete",
        "terminal_phase_witness_issuer_unproven",
        "terminal_phase_witness_ref_missing",
        "terminal_phase_witness_authority_missing",
        "terminal_phase_witness_authority_unverified",
        "terminal_projection_proof_missing",
        "terminal_projection_proof_untrusted",
        "terminal_completed_run_source_unproven",
        "terminal_completed_run_proof_unverified",
    }
    if blocking:
        status = "unverified" if any(item in soft for item in blocking) else "rejected"
    else:
        status = "bound"

    # Production authorization requires live validate_projection — never a
    # caller-supplied accepted=True dictionary.
    authorized = (
        status == "bound"
        and not contract_sample
        and authorization_basis == "authority_verified"
        and input_closure == "verified"
        and phase_ok
        and proj_ok
        and proj_basis == "validate_projection"
        and run_ok
        and parent_meta is not None
        and expected_set_ref is not None
        and not gaps
        and not blocking
    )

    result = {
        "kind": KIND,
        "schema_version": VERSION,
        "status": status,
        "authorized": authorized,
        "authorization_basis": authorization_basis,
        "input_closure": input_closure,
        "input_closure_gate": copy.deepcopy(closure_gate),
        "contract_sample": contract_sample,
        "check_id": TERMINAL_CHECK_ID,
        "phase": proven_phase,
        "phase_basis": phase_basis,
        "phase_witness_verified": phase_ok,
        "phase_witness_status": phase_witness_status,
        "projection_verified": proj_ok,
        "projection_verification": proj_basis,
        "projection_identity": projection_identity,
        "scope_id": scope_id,
        "expected_set_ref": expected_set_ref,
        "expected_check_ref": expected_check_ref,
        "expected": entry,
        "completed_run": completed,
        "completed_run_verified": run_ok,
        "completed_run_verification": run_verification,
        "plan_identity": plan_identity,
        "plan": copy.deepcopy(plan),
        "gaps": gaps,
        "reasons": reasons,
        "limitations": [
            "terminal_binding_does_not_invent_expected",
            "span_expectations_are_not_check_expected",
            "terminal_binding_does_not_produce_native_result",
            "terminal_binding_does_not_admit_or_require",
            "terminal_root_missing_cannot_fake_pass",
            "expected_authority_is_not_input_closure_proof",
            "expected_authority_is_not_phase_or_domain_proof",
            "phase_witness_is_external_to_parent_schema",
            "phase_witness_seal_is_not_source_authentication",
            "caller_projection_proof_is_untrusted",
            "terminal_plan_is_not_expected_plan",
            "completed_run_is_not_written_into_expected",
            "plan_builder_params_are_not_terminal_proof",
            "does_not_implement_phase_producer",
            "does_not_rewrite_b2_projection_schema",
            "binding_digest_is_not_authorization_proof",
        ],
        "binding_ref": None,
    }
    if contract_sample:
        result["limitations"].append(
            "contract_sample_cannot_authorize_production_terminal"
        )
    if authorization_basis == "structural_fixture":
        result["limitations"].append(
            "structural_fixture_cannot_authorize_production_terminal"
        )
    if input_closure != "verified":
        result["limitations"].append("input_closure_unverified_cannot_execute_terminal")
    if status != "bound" or not authorized:
        result["limitations"].append("unverified_terminal_must_not_execute_as_required")
    consistency = _consistency(result)
    if consistency:
        raise TerminalExpectedError(consistency[0])
    result["binding_ref"] = _digest(
        {key: value for key, value in result.items() if key != "binding_ref"}
    )
    return result


def post_completion_with_expected(
    *,
    root,
    config_path,
    weaver_config,
    run_id,
    attempt,
    result,
    expected,
    expected_authority=None,
    completed_run=None,
    completed_run_authority=None,
    phase_witness=None,
    phase_witness_authority=None,
    projection_proof=None,
    projection_git=None,
    expected_plan=None,
    expected_root=None,
    source_tree_id=None,
    contract_sample=False,
):
    """Build ``post_completion_plan`` then bind the Terminal Check Expected.

    Helper Plan builder params are never treated as Provider terminal proof.
    ``expected_plan`` / ``projection_git`` are the original Expected context —
    not this Terminal Plan. ``phase_witness_authority`` is required for
    production Phase verification — seal digest alone is not enough.
    """
    plan = selection.post_completion_plan(
        root=root,
        config_path=config_path,
        weaver_config=weaver_config,
        run_id=run_id,
        attempt=attempt,
        result=result,
        source_tree_id=source_tree_id,
    )
    if completed_run is None:
        completed_run = {
            "run_id": run_id,
            "attempt": attempt,
            "result": result,
            "status": "completed",
        }
    return bind_terminal_expected(
        expected,
        plan=plan,
        completed_run=completed_run,
        root=root,
        expected_authority=expected_authority,
        completed_run_authority=completed_run_authority,
        phase_witness=phase_witness,
        phase_witness_authority=phase_witness_authority,
        projection_proof=projection_proof,
        projection_git=projection_git,
        expected_plan=expected_plan,
        expected_root=expected_root,
        contract_sample=contract_sample,
    )


def validate_terminal_expected_binding(value):
    """Recompute digest and re-check pure binding consistency predicates."""
    if not isinstance(value, dict):
        raise TerminalExpectedError("terminal_binding_invalid")
    stored = _internal_ref(value.get("binding_ref"), "terminal_binding_ref_invalid")
    payload = {key: item for key, item in value.items() if key != "binding_ref"}
    if _digest(payload) != stored:
        raise TerminalExpectedError("terminal_binding_ref_mismatch")
    consistency = _consistency(value)
    if consistency:
        raise TerminalExpectedError(consistency[0])
    proven_phase = value.get("phase")
    entry, expected_check_ref = _validate_terminal_entry(
        copy.deepcopy(value.get("expected") or {}),
        proven_phase=proven_phase,
    )
    if value.get("expected_check_ref") != expected_check_ref:
        raise TerminalExpectedError("terminal_binding_expected_check_ref_mismatch")
    recomputed = expected_checks.entry_identity(
        {key: item for key, item in entry.items() if key != "expected_check_ref"}
    )
    if recomputed != expected_check_ref:
        raise TerminalExpectedError("terminal_binding_expected_identity_mismatch")
    if value.get("check_id") != entry.get("check_id"):
        raise TerminalExpectedError("terminal_binding_check_id_mismatch")
    recorded = value.get("plan_identity") or {}
    actual = _require_post_completion_plan(value.get("plan") or {})
    if actual != recorded:
        raise TerminalExpectedError("terminal_binding_plan_identity_mismatch")
    completed = _normalize_completed_run(value.get("completed_run") or {})
    match_reasons, _gaps = _match_completed_run_to_plan(completed, actual)
    if match_reasons:
        raise TerminalExpectedError(match_reasons[0])
    if value.get("scope_id") is None:
        raise TerminalExpectedError("terminal_binding_identity_incomplete")
    return value
