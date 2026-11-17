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
- invent a second Release decisioner, scheduler or parallel Manifest schema.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime

from . import required_assessment
from .release import registry as release_registry

RECONCILIATION_KIND = "engineering-ci-required-assessment-release"
RECONCILIATION_VERSION = 1
MANIFEST_SCHEMA_VERSION = 1
RELEASE_PHASES = frozenset({"pre_publish", "publish", "post_publish"})
PUBLISH_MODES = frozenset({"validation", "publication"})
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_EVIDENCE_REF_FIELDS = frozenset({"name", "version", "path", "sha256"})


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


def _evidence_ref(value, code):
    if not isinstance(value, dict) or set(value) - _EVIDENCE_REF_FIELDS:
        raise RequiredAssessmentReleaseError(code)
    record = {
        "name": _text(value.get("name"), code),
        "version": _text(value.get("version"), code),
        "sha256": _text(value.get("sha256"), code).lower(),
    }
    if value.get("path") is not None:
        record["path"] = _text(value.get("path"), code)
    return record


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
    return {
        "repository": repository,
        "tag": tag,
        "head_sha": _sha1(value.get("head_sha"), "release_head_sha_invalid"),
        "mode": mode,
        "phase": phase,
        "evaluated_at": _datetime(
            value.get("evaluated_at"), "release_evaluated_at_invalid"
        ).isoformat(),
        "run_id": value.get("run_id"),
        "run_attempt": value.get("run_attempt"),
    }


def _normalize_manifest(value, *, publish_context):
    """Accept Manifest v1 identity fields without rewriting check statuses."""
    if not isinstance(value, dict):
        raise RequiredAssessmentReleaseError("release_manifest_invalid")
    if value.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise RequiredAssessmentReleaseError("release_manifest_schema_unsupported")
    if value.get("repository") != publish_context["repository"]:
        raise RequiredAssessmentReleaseError("release_manifest_repository_mismatch")
    release = value.get("release")
    if not isinstance(release, dict):
        raise RequiredAssessmentReleaseError("release_manifest_release_invalid")
    if release.get("tag") != publish_context["tag"]:
        raise RequiredAssessmentReleaseError("release_manifest_tag_mismatch")
    if release.get("commit_sha") != publish_context["head_sha"]:
        raise RequiredAssessmentReleaseError("release_manifest_commit_mismatch")
    if value.get("mode") not in PUBLISH_MODES:
        raise RequiredAssessmentReleaseError("release_manifest_mode_invalid")
    if value.get("result") not in {"passed", "failed"}:
        raise RequiredAssessmentReleaseError("release_manifest_result_invalid")
    checks = value.get("checks")
    if not isinstance(checks, dict) or not checks:
        raise RequiredAssessmentReleaseError("release_manifest_checks_invalid")
    required_keys = set(release_registry.CHECKS) | set(
        release_registry.DAGGER_POLICY_CHECKS
    )
    missing = required_keys - set(checks)
    if missing:
        raise RequiredAssessmentReleaseError("release_manifest_checks_incomplete")
    for record in checks.values():
        if not isinstance(record, dict) or "status" not in record:
            raise RequiredAssessmentReleaseError("release_manifest_check_invalid")
        # Never upgrade or trust-rewrite original statuses here.
        _text(record.get("status"), "release_manifest_check_status_invalid")
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, list):
        raise RequiredAssessmentReleaseError("release_manifest_artifacts_invalid")
    workflow = value.get("workflow")
    if not isinstance(workflow, dict):
        raise RequiredAssessmentReleaseError("release_manifest_workflow_invalid")
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "repository": value["repository"],
        "release": {
            "version": release.get("version"),
            "tag": release["tag"],
            "commit_sha": release["commit_sha"],
            "release_url": release.get("release_url"),
        },
        "workflow": {
            "run_id": workflow.get("run_id"),
            "run_attempt": workflow.get("run_attempt"),
            "run_url": workflow.get("run_url"),
            "event": workflow.get("event"),
            "path": workflow.get("path"),
        },
        "mode": value["mode"],
        "checks": copy.deepcopy(checks),
        "tools": copy.deepcopy(value.get("tools") or []),
        "artifacts": copy.deepcopy(artifacts),
        "result": value["result"],
    }


def _normalize_bindings(value):
    """Optional Manifest check key → Assessment expected_check_ref bindings."""
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


def _normalize_artifact_requirements(value):
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise RequiredAssessmentReleaseError("release_artifact_requirements_invalid")
    names = []
    for item in value:
        name = _text(item, "release_artifact_requirements_invalid")
        if name not in release_registry.ARTIFACT_NAMES:
            raise RequiredAssessmentReleaseError("release_artifact_requirement_unknown")
        if name not in names:
            names.append(name)
    return names


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
    """Decide whether the frozen Assessment Profile may still authorize publish.

    The authorized policy source (via ``policy_snapshot_authority``) is the
    trust boundary. Local window fields alone never grant publication.
    """
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


def reconcile_release_assessment(
    assessment,
    manifest,
    *,
    publish_context,
    policy_snapshot_authority=None,
    check_bindings=None,
    artifact_requirements=None,
    contract_sample=False,
):
    """Reconcile an R1 Assessment with Manifest v1 for Release publication.

    ``policy_snapshot_authority`` must prove the Assessment's frozen Profile
    revision remains authorized at publish time. Same Candidate SHA alone never
    preserves Required authorization across policy drift.
    """
    required_assessment.validate_assessment(assessment)
    context = _normalize_publish_context(publish_context)
    evaluated = _datetime(context["evaluated_at"], "release_evaluated_at_invalid")
    normalized_manifest = _normalize_manifest(manifest, publish_context=context)
    bindings = _normalize_bindings(check_bindings)
    required_artifacts = _normalize_artifact_requirements(artifact_requirements)
    contract_sample = _flag(contract_sample, "release_contract_sample_flag_invalid")
    policy_snapshot_authority = _callable_or_none(
        policy_snapshot_authority, "policy_snapshot_authority_invalid"
    )

    reasons = []
    gaps = []
    if contract_sample or assessment.get("inputs", {}).get("contract_sample"):
        reasons.append("contract_sample_inputs_not_production_release")
        contract_sample = True

    # Candidate / identity binding
    assessment_commit = _assessment_candidate_commit(assessment)
    if assessment_commit is None:
        reasons.append("release_assessment_candidate_missing")
    elif assessment_commit != context["head_sha"]:
        reasons.append("release_assessment_commit_mismatch")

    # Assessment phase must match the Release stage being authorized.
    if assessment.get("phase") != context["phase"]:
        reasons.append("release_assessment_phase_mismatch")

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

    # Manifest fixed checks remain original execution facts — never trusted rewrite.
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

    # Bind declared Manifest checks to Assessment instances without inventing coverage.
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
        # Structural Manifest PASS never upgrades an unsatisfied Assessment row.

    # Dynamic Required obligations with unmet/unverified remain Release gaps.
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

    present_artifacts = {
        item.get("name")
        for item in normalized_manifest["artifacts"]
        if isinstance(item, dict) and item.get("name")
    }
    for name in required_artifacts:
        if name not in present_artifacts:
            gaps.append(
                {
                    "kind": "artifact_requirement",
                    "ref": name,
                    "reason": "required_artifact_missing",
                }
            )
            if "release_required_artifacts_missing" not in reasons:
                reasons.append("release_required_artifacts_missing")

    snapshot = _profile_snapshot(assessment)
    # Local revoked_at / valid_until are diagnostic only; authority decides.
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

    # Informational: same SHA does not imply policy continuity.
    if assessment_commit == context["head_sha"]:
        # retained as limitation, not a pass condition
        pass

    blocking = [
        reason
        for reason in reasons
        if reason != "contract_sample_inputs_not_production_release"
    ]
    if not blocking:
        status = "verified"
        release_authorized = not contract_sample
    elif (
        policy_status == "unverified" or "policy_snapshot_authority_missing" in blocking
    ):
        status = "unverified"
        release_authorized = False
    else:
        status = "rejected"
        release_authorized = False

    result = {
        "kind": RECONCILIATION_KIND,
        "schema_version": RECONCILIATION_VERSION,
        "status": status,
        "release_authorized": release_authorized,
        "contract_sample": contract_sample,
        "assessment_ref": assessment["assessment_ref"],
        "assessment_status": assessment["status"],
        "manifest_result": normalized_manifest["result"],
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "publish_context": {
            **{key: value for key, value in context.items() if key != "evaluated_at"},
            "evaluated_at": evaluated.isoformat(),
        },
        "candidate": {
            "commit": assessment_commit,
            "manifest_commit": normalized_manifest["release"]["commit_sha"],
            "tag": context["tag"],
            "repository": context["repository"],
        },
        "profile_snapshot": snapshot,
        "policy_snapshot_status": policy_status,
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
        "artifact_requirements": required_artifacts,
        "gaps": gaps,
        "reasons": reasons,
        "limitations": [
            "release_reconciliation_does_not_rerun_assessment",
            "release_reconciliation_does_not_publish_github_check",
            "release_reconciliation_does_not_read_rulesets",
            "release_reconciliation_does_not_evaluate_post_publish",
            "manifest_fixed_checks_are_not_trusted_admission",
            "same_candidate_sha_does_not_preserve_policy_authorization",
            "fixture_policy_authority_is_not_production_trust_root",
        ],
        "reconciliation_ref": None,
    }
    if contract_sample:
        result["limitations"].append(
            "contract_sample_cannot_authorize_production_release"
        )
        result["release_authorized"] = False
    if status != "verified":
        result["limitations"].append("unverified_release_must_not_publish")
    result["reconciliation_ref"] = _digest(
        {key: value for key, value in result.items() if key != "reconciliation_ref"}
    )
    return result


def validate_release_reconciliation(value):
    """Recompute ``reconciliation_ref`` and enforce envelope invariants."""
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
    if value.get("status") not in {"verified", "rejected", "unverified"}:
        raise RequiredAssessmentReleaseError("release_reconciliation_status_invalid")
    _flag(value.get("release_authorized"), "release_authorized_flag_invalid")
    _internal_ref(value.get("assessment_ref"), "release_assessment_ref_invalid")
    return value
