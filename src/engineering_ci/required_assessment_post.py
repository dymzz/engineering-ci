"""#61 R5 Post-stage Required Assessment envelope.

Binds an immutable prior (typically pre-publish) #61 R1 Assessment to a fresh
post-stage R1 Assessment using an observable trigger and due window. Preserves
prior facts; never rewrites a prior PASS as "never passed".

This module does **not**:

- re-implement obligation evaluation (#61 R1 owns that);
- publish a GitHub Check (#61 R2);
- read Rulesets (#61 R3);
- perform Release Manifest reconciliation (#61 R4);
- wait for the same incomplete Workflow Run's own Terminal evidence;
- auto-revoke published artifacts.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime

from . import required_assessment

POST_KIND = "engineering-ci-required-assessment-post"
POST_VERSION = 1
TRIGGER_KINDS = frozenset(
    {
        "run_completed",
        "publication_completed",
        "artifact_available",
        "authorized_reevaluation",
    }
)
DUE_STATES = frozenset({"not_yet_due", "within_window", "overdue", "not_applicable"})
DISPOSITIONS = frozenset(
    {
        "none",
        "block_progression",
        "await_repair",
        "reevaluate",
        "notify",
        "not_applicable",
    }
)
ENVELOPE_STATUSES = frozenset({"verified", "rejected", "unverified", "pending"})
_SHA256_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_INFORMATIONAL = frozenset({"contract_sample_inputs_not_production_post_stage"})


class RequiredAssessmentPostError(ValueError):
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
        raise RequiredAssessmentPostError(code)
    return value


def _flag(value, code):
    if not isinstance(value, bool):
        raise RequiredAssessmentPostError(code)
    return value


def _positive_int(value, code):
    if type(value) is not int or value <= 0:
        raise RequiredAssessmentPostError(code)
    return value


def _datetime(value, code):
    text = _text(value, code)
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError as error:
        raise RequiredAssessmentPostError(code) from error


def _internal_ref(value, code):
    text = _text(value, code)
    if not _SHA256_DIGEST_RE.fullmatch(text):
        raise RequiredAssessmentPostError(code)
    return text


def _callable_or_none(value, code):
    if value is None:
        return None
    if not callable(value):
        raise RequiredAssessmentPostError(code)
    return value


def _normalize_trigger(value):
    if not isinstance(value, dict):
        raise RequiredAssessmentPostError("post_trigger_invalid")
    kind = _text(value.get("kind"), "post_trigger_kind_invalid")
    if kind not in TRIGGER_KINDS:
        raise RequiredAssessmentPostError("post_trigger_kind_invalid")
    trigger = {
        "kind": kind,
        "observed_at": _datetime(
            value.get("observed_at"), "post_trigger_observed_at_invalid"
        ).isoformat(),
        "run_id": _text(value.get("run_id"), "post_trigger_run_id_invalid"),
        "run_attempt": _positive_int(
            value.get("run_attempt"), "post_trigger_run_attempt_invalid"
        ),
        "target": _text(value.get("target"), "post_trigger_target_invalid"),
    }
    digest = value.get("artifact_digest")
    if digest is not None:
        trigger["artifact_digest"] = _internal_ref(
            digest, "post_trigger_artifact_digest_invalid"
        )
    completed = value.get("run_completed")
    if completed is None:
        # Observable completion identity: completed Run is required except when
        # explicitly marking an incomplete current Run (must not force wait).
        completed = True
    trigger["run_completed"] = _flag(completed, "post_trigger_run_completed_invalid")
    return trigger


def _normalize_due_window(value):
    if not isinstance(value, dict):
        raise RequiredAssessmentPostError("post_due_window_invalid")
    earliest = _datetime(value.get("earliest_at"), "post_due_earliest_invalid")
    due = _datetime(value.get("due_at"), "post_due_at_invalid")
    if due < earliest:
        raise RequiredAssessmentPostError("post_due_window_inverted")
    return {"earliest_at": earliest.isoformat(), "due_at": due.isoformat()}


def _due_state(evaluated_at, window, *, profile_applicable):
    if not profile_applicable:
        return "not_applicable"
    earliest = _datetime(window["earliest_at"], "post_due_earliest_invalid")
    due = _datetime(window["due_at"], "post_due_at_invalid")
    if evaluated_at < earliest:
        return "not_yet_due"
    if evaluated_at > due:
        return "overdue"
    return "within_window"


def _disposition_from_authority(authority, context):
    if authority is None:
        return "none", "missing", ["post_disposition_authority_missing"]
    outcome = authority(context)
    if outcome is True:
        return "none", "authorized", []
    if outcome is False or outcome is None:
        return "none", "rejected", ["post_disposition_authority_rejected"]
    if not isinstance(outcome, dict):
        raise RequiredAssessmentPostError("post_disposition_authority_result_invalid")
    disposition = outcome.get("disposition", "none")
    if disposition not in DISPOSITIONS:
        raise RequiredAssessmentPostError("post_disposition_invalid")
    if outcome.get("accepted") is True and outcome.get("status") == "authorized":
        return disposition, "authorized", []
    if outcome.get("status") == "unverified":
        return disposition, "unverified", ["post_disposition_authority_unverified"]
    return disposition, "rejected", ["post_disposition_authority_rejected"]


def _envelope_status(
    *,
    due_state,
    prior_status,
    post_status,
    contract_sample,
    reasons,
):
    blocking = [item for item in reasons if item not in _INFORMATIONAL]
    if prior_status != "passed":
        return "rejected"
    if due_state == "not_yet_due":
        return "pending"
    if due_state == "not_applicable":
        return "verified" if not blocking and not contract_sample else "unverified"
    if due_state == "overdue" and post_status != "passed":
        return "rejected" if post_status == "blocked" else "unverified"
    if post_status == "passed" and not blocking and not contract_sample:
        return "verified"
    if post_status == "blocked" or any(
        item.startswith("post_prior_") or item.startswith("post_trigger_")
        for item in blocking
    ):
        return "rejected"
    return "unverified"


def _consistency(envelope):
    errors = []
    if envelope.get("kind") != POST_KIND:
        errors.append("post_stage_kind_invalid")
    if envelope.get("schema_version") != POST_VERSION:
        errors.append("post_stage_schema_unsupported")
    status = envelope.get("status")
    due_state = envelope.get("due_state")
    sample = envelope.get("contract_sample")
    prior_status = envelope.get("prior_assessment_status")
    post_status = envelope.get("post_assessment_status")
    disposition = envelope.get("disposition")
    if status not in ENVELOPE_STATUSES:
        errors.append("post_stage_status_invalid")
    if due_state not in DUE_STATES:
        errors.append("post_stage_due_state_invalid")
    if not isinstance(sample, bool):
        errors.append("post_contract_sample_flag_invalid")
    if disposition not in DISPOSITIONS:
        errors.append("post_disposition_invalid")
    if prior_status not in {"passed", "blocked"}:
        errors.append("post_prior_status_invalid")
    if post_status not in {"passed", "blocked"}:
        errors.append("post_assessment_status_invalid")
    if status == "verified":
        if sample is not False:
            errors.append("post_verified_rejects_contract_sample")
        if prior_status != "passed":
            errors.append("post_verified_requires_prior_passed")
        if due_state == "not_yet_due":
            errors.append("post_verified_rejects_not_yet_due")
        if due_state != "not_applicable" and post_status != "passed":
            errors.append("post_verified_requires_post_passed")
        if due_state == "overdue" and post_status != "passed":
            errors.append("post_verified_rejects_overdue_failure")
    if envelope.get("lifecycle_all_phases_passed") is True and status != "verified":
        errors.append("post_lifecycle_requires_verified")
    if envelope.get("auto_revoke_published") is True:
        errors.append("post_auto_revoke_forbidden")
    try:
        _internal_ref(
            envelope.get("prior_assessment_ref"), "post_prior_assessment_ref_invalid"
        )
        _internal_ref(
            envelope.get("post_assessment_ref"), "post_post_assessment_ref_invalid"
        )
    except RequiredAssessmentPostError as error:
        errors.append(error.code)
    trigger = envelope.get("trigger_event")
    if not isinstance(trigger, dict) or trigger.get("kind") not in TRIGGER_KINDS:
        errors.append("post_trigger_invalid")
    elif trigger.get("run_completed") is not True and status == "verified":
        errors.append("post_verified_requires_completed_run_trigger")
    return errors


def reconcile_post_stage(
    prior_assessment,
    post_assessment,
    *,
    trigger_event,
    due_window,
    evaluated_at,
    disposition_authority=None,
    profile_applicable=True,
    contract_sample=False,
):
    """Reconcile prior + post R1 Assessments under trigger/due-window rules."""
    prior = required_assessment.validate_assessment(prior_assessment)
    post = required_assessment.validate_assessment(post_assessment)
    trigger = _normalize_trigger(trigger_event)
    window = _normalize_due_window(due_window)
    evaluated = _datetime(evaluated_at, "post_evaluated_at_invalid")
    contract_sample = _flag(contract_sample, "post_contract_sample_flag_invalid")
    profile_applicable = _flag(profile_applicable, "post_profile_applicable_invalid")
    disposition_authority = _callable_or_none(
        disposition_authority, "post_disposition_authority_invalid"
    )

    reasons = []
    gaps = []
    if contract_sample or prior.get("inputs", {}).get("contract_sample"):
        reasons.append("contract_sample_inputs_not_production_post_stage")
        contract_sample = True

    if prior.get("status") != "passed":
        reasons.append("post_prior_assessment_not_passed")
        gaps.append(
            {
                "kind": "prior_assessment",
                "ref": prior.get("assessment_ref"),
                "reason": "prior_not_passed",
            }
        )

    if trigger["run_completed"] is not True:
        reasons.append("post_trigger_run_not_completed")
        gaps.append(
            {
                "kind": "trigger",
                "ref": trigger["run_id"],
                "reason": "incomplete_run_must_not_force_workflow_wait",
            }
        )

    due_state = _due_state(
        evaluated, window, profile_applicable=profile_applicable is True
    )
    if due_state == "not_yet_due":
        reasons.append("post_stage_not_yet_due")
    if due_state == "overdue" and post.get("status") != "passed":
        reasons.append("post_stage_overdue_without_passing_evidence")
        gaps.append(
            {
                "kind": "due_window",
                "ref": window["due_at"],
                "reason": "overdue_missing_or_failed_evidence",
            }
        )

    disposition_context = {
        "prior_assessment_ref": prior["assessment_ref"],
        "post_assessment_ref": post["assessment_ref"],
        "prior_status": prior["status"],
        "post_status": post["status"],
        "due_state": due_state,
        "trigger_event": copy.deepcopy(trigger),
    }
    disposition, disposition_authority_status, disposition_reasons = (
        _disposition_from_authority(disposition_authority, disposition_context)
    )
    # Missing disposition authority is informational unless a non-none disposition
    # is required by failure handling; default remains "none" (no auto-revoke).
    if disposition != "none" and disposition_authority_status != "authorized":
        reasons.extend(disposition_reasons)
    elif disposition_authority is None:
        disposition_authority_status = "not_requested"

    if post.get("status") == "blocked":
        reasons.append("post_assessment_blocked")
        if disposition == "none" and disposition_authority is not None:
            # Keep recorded disposition from authority; otherwise note block.
            pass

    status = _envelope_status(
        due_state=due_state,
        prior_status=prior["status"],
        post_status=post["status"],
        contract_sample=contract_sample,
        reasons=reasons,
    )

    # Lifecycle claim: only when this post stage verified; never erases prior.
    lifecycle_all_phases_passed = status == "verified" and not contract_sample

    result = {
        "kind": POST_KIND,
        "schema_version": POST_VERSION,
        "status": status,
        "contract_sample": contract_sample,
        "prior_assessment_ref": prior["assessment_ref"],
        "prior_assessment_status": prior["status"],
        "prior_phase": prior.get("phase"),
        "post_assessment_ref": post["assessment_ref"],
        "post_assessment_status": post["status"],
        "post_phase": post.get("phase"),
        "trigger_event": trigger,
        "due_window": window,
        "due_state": due_state,
        "evaluated_at": evaluated.isoformat(),
        "disposition": disposition,
        "disposition_authority_status": disposition_authority_status,
        "auto_revoke_published": False,
        "lifecycle_all_phases_passed": lifecycle_all_phases_passed,
        "gaps": gaps,
        "reasons": reasons,
        "limitations": [
            "post_stage_does_not_rewrite_prior_assessment",
            "post_stage_does_not_wait_on_own_workflow_terminal",
            "post_stage_does_not_publish_github_check",
            "post_stage_does_not_read_rulesets",
            "post_stage_does_not_reconcile_release_manifest",
            "does_not_auto_revoke_published_artifacts",
            "post_stage_digest_is_not_authorization_proof",
        ],
        "post_stage_ref": None,
    }
    if contract_sample:
        result["limitations"].append(
            "contract_sample_cannot_authorize_production_post_stage"
        )
    if status != "verified":
        result["limitations"].append("unverified_post_stage_must_not_claim_lifecycle_pass")
    consistency = _consistency(result)
    if consistency:
        raise RequiredAssessmentPostError(consistency[0])
    result["post_stage_ref"] = _digest(
        {key: value for key, value in result.items() if key != "post_stage_ref"}
    )
    return result


def validate_post_stage(value):
    """Recompute digest and re-check pure post-stage consistency predicates."""
    if not isinstance(value, dict):
        raise RequiredAssessmentPostError("post_stage_invalid")
    if value.get("kind") != POST_KIND:
        raise RequiredAssessmentPostError("post_stage_kind_invalid")
    if value.get("schema_version") != POST_VERSION:
        raise RequiredAssessmentPostError("post_stage_schema_unsupported")
    stored = _internal_ref(value.get("post_stage_ref"), "post_stage_ref_invalid")
    payload = {key: item for key, item in value.items() if key != "post_stage_ref"}
    if _digest(payload) != stored:
        raise RequiredAssessmentPostError("post_stage_ref_mismatch")
    consistency = _consistency(value)
    if consistency:
        raise RequiredAssessmentPostError(consistency[0])
    return value
