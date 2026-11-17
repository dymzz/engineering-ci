"""#52-D4 Terminal Check Expected → post_completion Plan binding.

Binds one authorized #66-B2 ``weaver_terminal`` Check Expected
(``scope_id`` / ``expected_check_ref`` / ``phase=post_completion``) onto an
existing ``selection.post_completion_plan`` consumption closure.

This module does **not**:

- invent a Check Expected when #66-B2 / authority is missing;
- confuse Weaver Span Expectations JSON with Check Expected;
- reimplement ``post_completion_plan`` / ``validate_post_completion_binding``;
- produce Native Results, #66-C Receipts, #63 Admission or #61 Required;
- wire ``release.yml`` or production orchestration.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

from . import check_contract, expected_checks, selection

KIND = "engineering-ci-terminal-expected-binding"
VERSION = 1
TERMINAL_CHECK_ID = "eci/weaver/terminal"
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA256_REF_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_INFORMATIONAL = frozenset({"contract_sample_inputs_not_production_terminal"})
INPUT_CLOSURE_STATES = frozenset({"verified", "unverified", "failed"})


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


def _extract_entry(expected):
    """Accept one entry or an Expected v2 scope containing weaver_terminal."""
    if not isinstance(expected, dict):
        raise TerminalExpectedError("terminal_expected_invalid")
    if expected.get("check_id") == TERMINAL_CHECK_ID:
        entry = copy.deepcopy(expected)
        scope_id = expected.get("scope_id")
        if scope_id is None:
            raise TerminalExpectedError("terminal_expected_scope_id_missing")
        return entry, _text(scope_id, "terminal_expected_scope_id_invalid"), None
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
    scope_id = expected.get("scope_id")
    if scope_id is None:
        raise TerminalExpectedError("terminal_expected_scope_id_missing")
    parent_status = expected.get("status")
    return (
        entry,
        _text(scope_id, "terminal_expected_scope_id_invalid"),
        parent_status,
    )


def _validate_terminal_entry(entry):
    try:
        check_contract.validate_expected_instance(entry)
    except check_contract.CheckContractError as error:
        raise TerminalExpectedError("terminal_expected_instance_invalid") from error
    if entry.get("check_id") != TERMINAL_CHECK_ID:
        raise TerminalExpectedError("terminal_expected_check_id_invalid")
    if entry.get("phase") != check_contract.POST_COMPLETION_PHASE:
        raise TerminalExpectedError("terminal_expected_phase_invalid")
    declared = entry.get("expected_check_ref")
    computed = expected_checks.entry_identity(
        {key: value for key, value in entry.items() if key != "expected_check_ref"}
    )
    if declared is None:
        entry = copy.deepcopy(entry)
        entry["expected_check_ref"] = computed
        declared = computed
    else:
        declared = _text(declared, "terminal_expected_check_ref_invalid")
        if declared != computed:
            raise TerminalExpectedError("terminal_expected_check_ref_mismatch")
    return entry, declared


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


def _match_expected_to_plan(entry, plan_identity):
    """Deterministic Run/Attempt identity check — authority cannot skip this."""
    reasons = []
    gaps = []
    run_id = entry.get("run_id")
    attempt = entry.get("run_attempt")
    if run_id is None and isinstance(entry.get("terminal_run"), dict):
        run_id = entry["terminal_run"].get("run_id")
        attempt = entry["terminal_run"].get("run_attempt")
    if run_id is None:
        reasons.append("terminal_expected_run_identity_missing")
        gaps.append(
            {
                "kind": "expected_plan_identity",
                "ref": plan_identity["run_id"],
                "reason": "expected_run_identity_missing",
            }
        )
        return reasons, gaps
    if run_id != plan_identity["run_id"]:
        reasons.append("terminal_expected_run_mismatch")
        gaps.append(
            {
                "kind": "expected_plan_identity",
                "ref": run_id,
                "reason": "expected_run_mismatch",
            }
        )
    if type(attempt) is not int or attempt < 1:
        reasons.append("terminal_expected_attempt_invalid")
        gaps.append(
            {
                "kind": "expected_plan_identity",
                "ref": plan_identity["attempt"],
                "reason": "expected_attempt_invalid",
            }
        )
    elif attempt != plan_identity["attempt"]:
        reasons.append("terminal_expected_attempt_mismatch")
        gaps.append(
            {
                "kind": "expected_plan_identity",
                "ref": attempt,
                "reason": "expected_attempt_mismatch",
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
        return "failed", {"ok": False, "reason": str(error)}, [
            "terminal_input_closure_validation_error"
        ]
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
    return errors


def bind_terminal_expected(
    expected,
    *,
    plan,
    root=None,
    expected_authority=None,
    contract_sample=False,
):
    """Bind an authorized Terminal Check Expected onto a post_completion Plan.

    ``expected_authority`` and ``input_closure`` are independent facts. Overall
    ``authorized=True`` requires both authority acceptance and a verified
    ``selection.validate_post_completion_binding`` result against ``root``.
    """
    contract_sample = _flag(contract_sample, "terminal_contract_sample_flag_invalid")
    expected_authority = _callable_or_none(
        expected_authority, "terminal_expected_authority_invalid"
    )
    entry, scope_id, parent_status = _extract_entry(expected)
    if parent_status is not None and parent_status not in {
        "complete",
        "partial",
        None,
    }:
        # Unknown parent completeness is recorded; incomplete parent cannot authorize.
        pass
    entry, expected_check_ref = _validate_terminal_entry(entry)
    plan_identity = _require_post_completion_plan(plan)

    reasons = []
    gaps = []
    if contract_sample:
        reasons.append("contract_sample_inputs_not_production_terminal")

    identity_reasons, identity_gaps = _match_expected_to_plan(entry, plan_identity)
    reasons.extend(identity_reasons)
    gaps.extend(identity_gaps)

    if parent_status is not None and parent_status not in {"complete"}:
        reasons.append("terminal_expected_scope_incomplete")
        gaps.append(
            {
                "kind": "expected_scope",
                "ref": scope_id,
                "reason": f"scope_status_{parent_status}",
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
    if blocking:
        status = (
            "unverified"
            if any(
                item
                in {
                    "terminal_expected_authority_missing",
                    "terminal_expected_authority_unverified",
                    "terminal_input_closure_root_missing",
                    "terminal_expected_run_identity_missing",
                }
                for item in blocking
            )
            else "rejected"
        )
    else:
        status = "bound"

    authorized = (
        status == "bound"
        and not contract_sample
        and authorization_basis == "authority_verified"
        and input_closure == "verified"
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
        "phase": check_contract.POST_COMPLETION_PHASE,
        "scope_id": scope_id,
        "expected_check_ref": expected_check_ref,
        "expected": entry,
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
    source_tree_id=None,
    contract_sample=False,
):
    """Build ``post_completion_plan`` then bind the Terminal Check Expected."""
    plan = selection.post_completion_plan(
        root=root,
        config_path=config_path,
        weaver_config=weaver_config,
        run_id=run_id,
        attempt=attempt,
        result=result,
        source_tree_id=source_tree_id,
    )
    return bind_terminal_expected(
        expected,
        plan=plan,
        root=root,
        expected_authority=expected_authority,
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
    entry, expected_check_ref = _validate_terminal_entry(
        copy.deepcopy(value.get("expected") or {})
    )
    if value.get("expected_check_ref") != expected_check_ref:
        raise TerminalExpectedError("terminal_binding_expected_check_ref_mismatch")
    # Recompute identity from embedded Expected — reject mutated inner payload.
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
    match_reasons, _gaps = _match_expected_to_plan(entry, actual)
    if match_reasons:
        raise TerminalExpectedError(match_reasons[0])
    if value.get("scope_id") is None:
        raise TerminalExpectedError("terminal_binding_identity_incomplete")
    return value
