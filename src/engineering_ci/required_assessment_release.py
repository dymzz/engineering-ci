"""#61 R4 Release reconciliation for a staged Required Assessment.

Maps one immutable #61 R1 Assessment onto the existing Release Manifest v1 /
Run / Artifact chain and checks whether a previously frozen Profile snapshot
remains authorized for ``verified`` → ``publish``.

This module does **not**:

- re-run Assessment (#61 R1);
- publish a GitHub Check (#61 R2);
- read Rulesets (#61 R3);
- evaluate post-publish / terminal stages (#61 R5);
- rewrite Manifest ``checks[].status`` into trusted/#63 admitted facts;
- invent a second Release decisioner, scheduler or parallel Manifest schema;
- replace ``release.manifest.validate`` with a looser structural gate.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime

from . import required_assessment
from .release import manifest as release_manifest
from .release import registry as release_registry

RECONCILIATION_KIND = "engineering-ci-required-assessment-release"
RECONCILIATION_VERSION = 1
MANIFEST_SCHEMA_VERSION = 1
RELEASE_PHASES = frozenset({"pre_publish", "publish", "post_publish"})
PUBLISH_MODES = frozenset({"validation", "publication"})
MANIFEST_BINDINGS = frozenset({"same_run", "validation_to_publication"})
AUTHORIZATION_BASES = frozenset({"structural_fixture", "provider_verified"})
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_INFORMATIONAL_REASONS = frozenset({"contract_sample_inputs_not_production_release"})


class RequiredAssessmentReleaseError(ValueError):
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
        raise RequiredAssessmentReleaseError(code)
    return value


def _flag(value, code):
    if not isinstance(value, bool):
        raise RequiredAssessmentReleaseError(code)
    return value


def _positive_int(value, code):
    if type(value) is not int or value <= 0:
        raise RequiredAssessmentReleaseError(code)
    return value


def _datetime(value, code):
    text = _text(value, code)
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError as error:
        raise RequiredAssessmentReleaseError(code) from error


def _optional_datetime(value, code):
    if value is None:
        return None
    return _datetime(value, code)


def _sha1(value, code):
    text = _text(value, code).lower()
    if not _SHA1_RE.fullmatch(text):
        raise RequiredAssessmentReleaseError(code)
    return text


def _internal_ref(value, code):
    text = _text(value, code)
    if not _SHA256_DIGEST_RE.fullmatch(text):
        raise RequiredAssessmentReleaseError(code)
    return text


def _callable_or_none(value, code):
    if value is None:
        return None
    if not callable(value):
        raise RequiredAssessmentReleaseError(code)
    return value


def _normalize_publish_context(value):
    if not isinstance(value, dict):
        raise RequiredAssessmentReleaseError("release_publish_context_invalid")
    repository = _text(value.get("repository"), "release_repository_invalid")
    if not _REPO_RE.fullmatch(repository):
        raise RequiredAssessmentReleaseError("release_repository_invalid")
    tag = _text(value.get("tag"), "release_tag_invalid")
    if not tag.startswith("v") or not _VERSION_RE.fullmatch(tag[1:]):
        raise RequiredAssessmentReleaseError("release_tag_invalid")
    mode = _text(value.get("mode"), "release_mode_invalid")
    if mode not in PUBLISH_MODES:
        raise RequiredAssessmentReleaseError("release_mode_invalid")
    phase = _text(value.get("phase"), "release_phase_invalid")
    if phase not in RELEASE_PHASES:
        raise RequiredAssessmentReleaseError("release_phase_invalid")
    binding = _text(value.get("manifest_binding"), "release_manifest_binding_invalid")
    if binding not in MANIFEST_BINDINGS:
        raise RequiredAssessmentReleaseError("release_manifest_binding_invalid")
    context = {
        "repository": repository,
        "tag": tag,
        "head_sha": _sha1(value.get("head_sha"), "release_head_sha_invalid"),
        "mode": mode,
        "phase": phase,
        "manifest_binding": binding,
        "evaluated_at": _datetime(
            value.get("evaluated_at"), "release_evaluated_at_invalid"
        ).isoformat(),
        "run_id": _positive_int(value.get("run_id"), "release_run_id_invalid"),
        "run_attempt": _positive_int(
            value.get("run_attempt"), "release_run_attempt_invalid"
        ),
    }
    if binding == "validation_to_publication":
        identity = value.get("validation_identity")
        if not isinstance(identity, dict):
            raise RequiredAssessmentReleaseError("release_validation_identity_invalid")
        context["validation_identity"] = {
            "mode": "validation",
            "run_id": _positive_int(
                identity.get("run_id"), "release_validation_run_id_invalid"
            ),
            "run_attempt": _positive_int(
                identity.get("run_attempt"), "release_validation_run_attempt_invalid"
            ),
        }
        if mode != "publication":
            raise RequiredAssessmentReleaseError(
                "release_validation_handoff_requires_publication_mode"
            )
    return context


def _load_manifest_v1(value, *, repository, tag, head):
    """Canonical Manifest v1 gate — never a looser parallel validator."""
    if not isinstance(value, dict):
        raise RequiredAssessmentReleaseError("release_manifest_invalid")
    try:
        release_manifest.validate(value, repository, tag, head)
    except ValueError as error:
        raise RequiredAssessmentReleaseError("release_manifest_v1_invalid") from error
    return copy.deepcopy(value)


def _bind_manifest_run(manifest, context):
    """Enforce same-run equality or explicit validation→publication handoff."""
    reasons = []
    workflow = manifest["workflow"]
    binding = context["manifest_binding"]
    if binding == "same_run":
        if manifest["mode"] != context["mode"]:
            reasons.append("release_manifest_mode_mismatch")
        if workflow.get("run_id") != context["run_id"]:
            reasons.append("release_manifest_run_mismatch")
        if workflow.get("run_attempt") != context["run_attempt"]:
            reasons.append("release_manifest_attempt_mismatch")
        return reasons, None
    # validation → publication across Runs
    identity = context["validation_identity"]
    if manifest["mode"] != "validation":
        reasons.append("release_handoff_manifest_not_validation")
    if workflow.get("run_id") != identity["run_id"]:
        reasons.append("release_handoff_validation_run_mismatch")
    if workflow.get("run_attempt") != identity["run_attempt"]:
        reasons.append("release_handoff_validation_attempt_mismatch")
    if (
        context["run_id"] == identity["run_id"]
        and context["run_attempt"] == identity["run_attempt"]
    ):
        reasons.append("release_handoff_requires_distinct_publication_run")
    return reasons, identity


def _normalize_bindings(value):
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise RequiredAssessmentReleaseError("release_check_bindings_invalid")
    bindings = []
    seen = set()
    for item in value:
        if not isinstance(item, dict):
            raise RequiredAssessmentReleaseError("release_check_bindings_invalid")
        key = _text(item.get("manifest_check_key"), "release_check_bindings_invalid")
        expected = _text(
            item.get("expected_check_ref"), "release_check_bindings_invalid"
        )
        if key in seen:
            raise RequiredAssessmentReleaseError("release_check_bindings_duplicate")
        seen.add(key)
        bindings.append({"manifest_check_key": key, "expected_check_ref": expected})
    return bindings


def _normalize_artifact_obligations(value):
    """Authorized current Profile Artifact obligation set (complete or empty)."""
    if value is None:
        raise RequiredAssessmentReleaseError("release_artifact_obligations_missing")
    if not isinstance(value, dict):
        raise RequiredAssessmentReleaseError("release_artifact_obligations_invalid")
    completeness = _text(
        value.get("completeness"), "release_artifact_obligations_invalid"
    )
    if completeness not in {"complete", "empty"}:
        raise RequiredAssessmentReleaseError(
            "release_artifact_obligations_completeness_invalid"
        )
    items = value.get("items")
    if not isinstance(items, list):
        raise RequiredAssessmentReleaseError("release_artifact_obligations_invalid")
    if completeness == "empty" and items:
        raise RequiredAssessmentReleaseError(
            "release_artifact_obligations_empty_nonempty_items"
        )
    if completeness == "complete" and not items:
        # Complete with zero items is represented as completeness=empty.
        raise RequiredAssessmentReleaseError(
            "release_artifact_obligations_complete_empty_items"
        )
    normalized = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise RequiredAssessmentReleaseError("release_artifact_obligations_invalid")
        name = _text(item.get("name"), "release_artifact_obligations_invalid")
        if name not in release_registry.ARTIFACT_NAMES:
            raise RequiredAssessmentReleaseError("release_artifact_obligation_unknown")
        if name in seen:
            raise RequiredAssessmentReleaseError(
                "release_artifact_obligations_duplicate"
            )
        seen.add(name)
        normalized.append(
            {
                "name": name,
                "obligation_ref": _text(
                    item.get("obligation_ref"), "release_artifact_obligations_invalid"
                ),
            }
        )
    return {"completeness": completeness, "items": normalized}


def _assessment_candidate_commit(assessment):
    candidate = assessment.get("candidate")
    if not isinstance(candidate, dict):
        return None
    commit = candidate.get("commit")
    if commit is None:
        return None
    return _sha1(commit, "release_assessment_candidate_invalid")


def _profile_snapshot(assessment):
    inputs = assessment.get("inputs") or {}
    profile = inputs.get("profile")
    if not isinstance(profile, dict):
        raise RequiredAssessmentReleaseError("release_assessment_profile_missing")
    authority = inputs.get("authority")
    if not isinstance(authority, dict):
        raise RequiredAssessmentReleaseError("release_assessment_authority_missing")
    return {
        "profile_ref": copy.deepcopy(profile.get("profile_ref")),
        "profile_revision": profile.get("profile_revision"),
        "profile_sha256": profile.get("profile_sha256"),
        "authority": {
            "status": authority.get("status"),
            "source_ref": copy.deepcopy(authority.get("source_ref")),
            "approved_at": authority.get("approved_at"),
            "effective_at": authority.get("effective_at"),
            "valid_from": authority.get("valid_from"),
            "valid_until": authority.get("valid_until"),
            "revoked_at": authority.get("revoked_at"),
            "evaluated_at": authority.get("evaluated_at"),
        },
        "assessment_ref": assessment.get("assessment_ref"),
        "assessment_evaluated_at": assessment.get("evaluated_at"),
        "universe_ref": inputs.get("universe_ref"),
    }


def _policy_still_authorized(snapshot, *, evaluated_at, authority):
    if authority is None:
        return False, "policy_snapshot_authority_missing", "unverified"
    context = {
        "snapshot": copy.deepcopy(snapshot),
        "evaluated_at": evaluated_at.isoformat(),
    }
    result = authority(context)
    if result is True:
        return True, None, "authorized"
    if result is False or result is None:
        return False, "policy_snapshot_authority_rejected", "rejected"
    if not isinstance(result, dict):
        raise RequiredAssessmentReleaseError("policy_snapshot_authority_result_invalid")
    status = result.get("status")
    if status == "authorized" and result.get("accepted") is True:
        return True, None, "authorized"
    if status in {"revoked", "expired", "superseded", "rejected"}:
        return False, f"policy_snapshot_{status}", "rejected"
    if status == "unverified" or result.get("accepted") is not True:
        return False, "policy_snapshot_unverified", "unverified"
    raise RequiredAssessmentReleaseError("policy_snapshot_authority_result_invalid")


def _artifact_set_authorized(obligations, *, authority, snapshot):
    if authority is None:
        return False, "artifact_obligation_authority_missing", "unverified"
    context = {
        "artifact_obligations": copy.deepcopy(obligations),
        "snapshot": copy.deepcopy(snapshot),
    }
    result = authority(context)
    if result is True:
        return True, None, "authorized"
    if result is False or result is None:
        return False, "artifact_obligation_authority_rejected", "rejected"
    if not isinstance(result, dict):
        raise RequiredAssessmentReleaseError(
            "artifact_obligation_authority_result_invalid"
        )
    status = result.get("status")
    if status == "authorized" and result.get("accepted") is True:
        return True, None, "authorized"
    if status in {"rejected", "incomplete"}:
        return False, f"artifact_obligation_{status}", "rejected"
    if status == "unverified" or result.get("accepted") is not True:
        return False, "artifact_obligation_unverified", "unverified"
    raise RequiredAssessmentReleaseError("artifact_obligation_authority_result_invalid")


def _provider_basis(manifest, context, *, provider_authority, handoff_authority):
    """Structural fixtures never become production authorization by themselves."""
    if provider_authority is None:
        return "structural_fixture", ["release_provider_authority_missing"]
    provider_context = {
        "manifest": copy.deepcopy(manifest),
        "publish_context": copy.deepcopy(context),
    }
    accepted = provider_authority(provider_context)
    if accepted is not True:
        return "structural_fixture", ["release_provider_authority_rejected"]
    if context["manifest_binding"] == "validation_to_publication":
        if handoff_authority is None:
            return "structural_fixture", ["release_handoff_authority_missing"]
        if handoff_authority(provider_context) is not True:
            return "structural_fixture", ["release_handoff_authority_rejected"]
    return "provider_verified", []


def _blocking_reasons(reasons):
    return [reason for reason in reasons if reason not in _INFORMATIONAL_REASONS]


def _derive_status(blocking, *, policy_status, artifact_status):
    if not blocking:
        return "verified"
    unverified_markers = {
        "policy_snapshot_authority_missing",
        "policy_snapshot_unverified",
        "artifact_obligation_authority_missing",
        "artifact_obligation_unverified",
        "release_provider_authority_missing",
        "release_handoff_authority_missing",
    }
    if (
        policy_status == "unverified"
        or artifact_status == "unverified"
        or any(marker in blocking for marker in unverified_markers)
    ):
        return "unverified"
    return "rejected"


def _authorization_consistent(envelope):
    """Pure predicates for release authorization — digest is not authority."""
    errors = []
    status = envelope.get("status")
    authorized = envelope.get("release_authorized")
    sample = envelope.get("contract_sample")
    policy = envelope.get("policy_snapshot_status")
    basis = envelope.get("authorization_basis")
    manifest_result = envelope.get("manifest_result")
    gaps = envelope.get("gaps")
    reasons = envelope.get("reasons")
    if status not in {"verified", "rejected", "unverified"}:
        errors.append("release_reconciliation_status_invalid")
    if not isinstance(authorized, bool):
        errors.append("release_authorized_flag_invalid")
    if not isinstance(sample, bool):
        errors.append("release_contract_sample_flag_invalid")
    if policy not in {"authorized", "rejected", "unverified"}:
        errors.append("release_policy_snapshot_status_invalid")
    if basis not in AUTHORIZATION_BASES:
        errors.append("release_authorization_basis_invalid")
    if manifest_result not in {"passed", "failed"}:
        errors.append("release_manifest_result_invalid")
    if not isinstance(gaps, list) or not isinstance(reasons, list):
        errors.append("release_reconciliation_collections_invalid")
        return errors
    blocking = _blocking_reasons(reasons)
    if authorized is True:
        if status != "verified":
            errors.append("release_authorized_requires_verified_status")
        if sample is not False:
            errors.append("release_authorized_rejects_contract_sample")
        if policy != "authorized":
            errors.append("release_authorized_requires_policy_authorized")
        if basis != "provider_verified":
            errors.append("release_authorized_requires_provider_verified")
        if manifest_result != "passed":
            errors.append("release_authorized_requires_manifest_passed")
        if gaps:
            errors.append("release_authorized_rejects_gaps")
        if blocking:
            errors.append("release_authorized_rejects_blocking_reasons")
    candidate = envelope.get("candidate")
    context = envelope.get("publish_context")
    if not isinstance(candidate, dict) or not isinstance(context, dict):
        errors.append("release_candidate_context_invalid")
        return errors
    if candidate.get("commit") != context.get("head_sha") and (
        status == "verified" or authorized is True
    ):
        errors.append("release_verified_requires_commit_alignment")
    if candidate.get("manifest_commit") != context.get("head_sha") and (
        status == "verified" or authorized is True
    ):
        errors.append("release_verified_requires_manifest_commit_alignment")
    if candidate.get("tag") != context.get("tag"):
        errors.append("release_candidate_tag_mismatch")
    if candidate.get("repository") != context.get("repository"):
        errors.append("release_candidate_repository_mismatch")
    return errors


def reconcile_release_assessment(
    assessment,
    manifest,
    *,
    publish_context,
    policy_snapshot_authority=None,
    artifact_obligations,
    artifact_obligation_authority=None,
    check_bindings=None,
    manifest_provider_authority=None,
    handoff_authority=None,
    contract_sample=False,
):
    """Reconcile an R1 Assessment with Manifest v1 for Release publication.

    Manifest structure is gated by ``release.manifest.validate``. Production
    ``release_authorized`` additionally requires provider-verified evidence
    (and handoff proof for validation→publication). Structural fixtures may
    reach ``status=verified`` only as a contract shape — they do not authorize
    publication.
    """
    required_assessment.validate_assessment(assessment)
    context = _normalize_publish_context(publish_context)
    evaluated = _datetime(context["evaluated_at"], "release_evaluated_at_invalid")
    normalized_manifest = _load_manifest_v1(
        manifest,
        repository=context["repository"],
        tag=context["tag"],
        head=context["head_sha"],
    )
    bindings = _normalize_bindings(check_bindings)
    artifacts = _normalize_artifact_obligations(artifact_obligations)
    contract_sample = _flag(contract_sample, "release_contract_sample_flag_invalid")
    policy_snapshot_authority = _callable_or_none(
        policy_snapshot_authority, "policy_snapshot_authority_invalid"
    )
    artifact_obligation_authority = _callable_or_none(
        artifact_obligation_authority, "artifact_obligation_authority_invalid"
    )
    manifest_provider_authority = _callable_or_none(
        manifest_provider_authority, "manifest_provider_authority_invalid"
    )
    handoff_authority = _callable_or_none(
        handoff_authority, "handoff_authority_invalid"
    )

    reasons = []
    gaps = []
    if contract_sample or assessment.get("inputs", {}).get("contract_sample"):
        reasons.append("contract_sample_inputs_not_production_release")
        contract_sample = True

    assessment_commit = _assessment_candidate_commit(assessment)
    if assessment_commit is None:
        reasons.append("release_assessment_candidate_missing")
    elif assessment_commit != context["head_sha"]:
        reasons.append("release_assessment_commit_mismatch")

    if assessment.get("phase") != context["phase"]:
        reasons.append("release_assessment_phase_mismatch")

    run_reasons, validation_identity = _bind_manifest_run(normalized_manifest, context)
    reasons.extend(run_reasons)

    if assessment.get("status") != "passed":
        reasons.append("release_assessment_blocked")
        for item in assessment.get("blocked_reasons") or ():
            gaps.append(
                {
                    "kind": "assessment_blocked_reason",
                    "ref": _text(
                        str(item), "release_assessment_blocked_reason_invalid"
                    ),
                    "reason": "assessment_not_passed",
                }
            )

    if normalized_manifest["result"] != "passed":
        reasons.append("release_manifest_result_failed")
    failed_fixed = [
        key
        for key, record in normalized_manifest["checks"].items()
        if record.get("required") is True and record.get("status") != "passed"
    ]
    for key in failed_fixed:
        gaps.append(
            {
                "kind": "manifest_fixed_check",
                "ref": key,
                "reason": "manifest_fixed_check_not_passed",
                "manifest_status": normalized_manifest["checks"][key]["status"],
            }
        )
        if "release_manifest_fixed_checks_incomplete" not in reasons:
            reasons.append("release_manifest_fixed_checks_incomplete")

    assessment_checks = {
        row["expected_check_ref"]: row
        for row in assessment.get("check_instances") or ()
    }
    for binding in bindings:
        key = binding["manifest_check_key"]
        expected = binding["expected_check_ref"]
        if key not in normalized_manifest["checks"]:
            reasons.append("release_check_binding_unknown_manifest_key")
            gaps.append(
                {
                    "kind": "check_binding",
                    "ref": key,
                    "reason": "manifest_check_missing",
                }
            )
            continue
        manifest_status = normalized_manifest["checks"][key]["status"]
        row = assessment_checks.get(expected)
        if row is None:
            gaps.append(
                {
                    "kind": "check_binding",
                    "ref": expected,
                    "reason": "assessment_check_instance_missing",
                    "manifest_check_key": key,
                    "manifest_status": manifest_status,
                }
            )
            reasons.append("release_check_binding_assessment_missing")
            continue
        if row.get("status") not in {"satisfied", "not_applicable", "not_yet_due"}:
            gaps.append(
                {
                    "kind": "check_binding",
                    "ref": expected,
                    "reason": "assessment_check_not_satisfied",
                    "assessment_status": row.get("status"),
                    "manifest_check_key": key,
                    "manifest_status": manifest_status,
                }
            )
            reasons.append("release_check_binding_unsatisfied")

    for row in assessment.get("obligations") or ():
        if row.get("status") in {"unmet", "unverified"}:
            gaps.append(
                {
                    "kind": "required_obligation",
                    "ref": row.get("instance_ref") or row.get("obligation_ref"),
                    "reason": f"obligation_{row.get('status')}",
                }
            )
            if "release_required_obligations_unmet" not in reasons:
                reasons.append("release_required_obligations_unmet")

    snapshot = _profile_snapshot(assessment)
    artifact_ok, artifact_reason, artifact_status = _artifact_set_authorized(
        artifacts, authority=artifact_obligation_authority, snapshot=snapshot
    )
    if not artifact_ok:
        reasons.append(artifact_reason)

    present_artifacts = {
        item["name"]: item
        for item in normalized_manifest["artifacts"]
        if isinstance(item, dict) and item.get("name")
    }
    for item in artifacts["items"]:
        name = item["name"]
        recorded = present_artifacts.get(name)
        if recorded is None:
            gaps.append(
                {
                    "kind": "artifact_obligation",
                    "ref": name,
                    "obligation_ref": item["obligation_ref"],
                    "reason": "required_artifact_missing",
                }
            )
            if "release_required_artifacts_missing" not in reasons:
                reasons.append("release_required_artifacts_missing")
            continue
        # Structural ID/digest/URL already enforced by manifest.validate.
        # Provider authority is required before treating them as publication proof.
        if recorded.get("digest") is None and context["mode"] == "publication":
            gaps.append(
                {
                    "kind": "artifact_obligation",
                    "ref": name,
                    "obligation_ref": item["obligation_ref"],
                    "reason": "required_artifact_digest_missing",
                }
            )
            if "release_required_artifact_digest_missing" not in reasons:
                reasons.append("release_required_artifact_digest_missing")

    local_revoked = _optional_datetime(
        snapshot["authority"].get("revoked_at"),
        "release_assessment_authority_revoked_at_invalid",
    )
    if local_revoked is not None and evaluated >= local_revoked:
        reasons.append("release_assessment_authority_revoked_locally")

    policy_ok, policy_reason, policy_status = _policy_still_authorized(
        snapshot,
        evaluated_at=evaluated,
        authority=policy_snapshot_authority,
    )
    if not policy_ok:
        reasons.append(policy_reason)

    authorization_basis, provider_reasons = _provider_basis(
        normalized_manifest,
        context,
        provider_authority=manifest_provider_authority,
        handoff_authority=handoff_authority,
    )
    reasons.extend(provider_reasons)

    blocking = _blocking_reasons(reasons)
    status = _derive_status(
        blocking, policy_status=policy_status, artifact_status=artifact_status
    )
    # Production authorization requires provider-verified basis; structural
    # fixtures never mint release_authorized=true.
    release_authorized = (
        status == "verified"
        and not contract_sample
        and authorization_basis == "provider_verified"
        and not gaps
        and not blocking
    )

    result = {
        "kind": RECONCILIATION_KIND,
        "schema_version": RECONCILIATION_VERSION,
        "status": status,
        "release_authorized": release_authorized,
        "authorization_basis": authorization_basis,
        "contract_sample": contract_sample,
        "assessment_ref": assessment["assessment_ref"],
        "assessment_status": assessment["status"],
        "manifest_result": normalized_manifest["result"],
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "publish_context": {
            **{key: value for key, value in context.items() if key != "evaluated_at"},
            "evaluated_at": evaluated.isoformat(),
        },
        "validation_identity": validation_identity,
        "candidate": {
            "commit": assessment_commit,
            "manifest_commit": normalized_manifest["release"]["commit_sha"],
            "tag": context["tag"],
            "repository": context["repository"],
        },
        "profile_snapshot": snapshot,
        "policy_snapshot_status": policy_status,
        "artifact_obligation_status": artifact_status,
        "artifact_obligations": artifacts,
        "fixed_manifest_checks": {
            key: {
                "required": normalized_manifest["checks"][key].get("required"),
                "status": normalized_manifest["checks"][key]["status"],
                "trusted": False,
                "evidence_admission": "not_evaluated",
            }
            for key in sorted(
                set(release_registry.CHECKS)
                | set(release_registry.DAGGER_POLICY_CHECKS)
            )
        },
        "check_bindings": bindings,
        "gaps": gaps,
        "reasons": reasons,
        "limitations": [
            "release_reconciliation_does_not_rerun_assessment",
            "release_reconciliation_does_not_publish_github_check",
            "release_reconciliation_does_not_read_rulesets",
            "release_reconciliation_does_not_evaluate_post_publish",
            "manifest_fixed_checks_are_not_trusted_admission",
            "same_candidate_sha_does_not_preserve_policy_authorization",
            "fixture_authorities_are_not_production_trust_roots",
            "structural_fixture_is_not_provider_verification",
            "reconciliation_digest_is_not_authorization_proof",
        ],
        "reconciliation_ref": None,
    }
    if contract_sample:
        result["limitations"].append(
            "contract_sample_cannot_authorize_production_release"
        )
    if authorization_basis == "structural_fixture":
        result["limitations"].append(
            "structural_fixture_cannot_authorize_production_release"
        )
    if status != "verified" or not release_authorized:
        result["limitations"].append("unverified_release_must_not_publish")
    consistency = _authorization_consistent(result)
    if consistency:
        raise RequiredAssessmentReleaseError(consistency[0])
    result["reconciliation_ref"] = _digest(
        {key: value for key, value in result.items() if key != "reconciliation_ref"}
    )
    return result


def validate_release_reconciliation(value):
    """Recompute digest and re-establish authorization-consistency predicates."""
    if not isinstance(value, dict):
        raise RequiredAssessmentReleaseError("release_reconciliation_invalid")
    if value.get("kind") != RECONCILIATION_KIND:
        raise RequiredAssessmentReleaseError("release_reconciliation_kind_invalid")
    if value.get("schema_version") != RECONCILIATION_VERSION:
        raise RequiredAssessmentReleaseError(
            "release_reconciliation_schema_unsupported"
        )
    stored = _internal_ref(
        value.get("reconciliation_ref"), "release_reconciliation_ref_invalid"
    )
    payload = {key: item for key, item in value.items() if key != "reconciliation_ref"}
    if _digest(payload) != stored:
        raise RequiredAssessmentReleaseError("release_reconciliation_ref_mismatch")
    _internal_ref(value.get("assessment_ref"), "release_assessment_ref_invalid")
    consistency = _authorization_consistent(value)
    if consistency:
        raise RequiredAssessmentReleaseError(consistency[0])
    return value
