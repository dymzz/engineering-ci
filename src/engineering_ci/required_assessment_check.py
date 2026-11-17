"""#61 R2 GitHub Check publication for a staged Required Assessment.

Separates **declarative Check construction** from **authorized production
publication**:

- ``build_check_publication`` / ``publication_for_missing_assessment`` build a
  self-digesting envelope and request body. They do not call GitHub.
- ``publish_check_run`` is the production publisher for the stable Required
  Context name. It refuses contract-sample / non-production Check names and
  requires a verified publishing authorization before POSTing ``success``.

This module does **not** re-run Assessment (#61 R1), admit evidence (#63),
read Rulesets (#61 R3), authorize Release (#61 R4/R5), or prove that a
declared ``trust_basis`` string is a trusted issuer.
"""

from __future__ import annotations

import hashlib
import json
import re

from . import required_assessment
from .github_cli import ApiError, GitHub

PUBLICATION_KIND = "engineering-ci-required-assessment-check"
PUBLICATION_VERSION = 1
CHECK_NAME = "engineering-ci / required-assessment"
SAMPLE_CHECK_NAME = "engineering-ci / required-assessment (contract-sample)"
CHECK_STATUS_COMPLETED = "completed"
CONCLUSIONS = frozenset({"success", "failure"})
FORBIDDEN_CONCLUSIONS = frozenset({"skipped", "neutral", "cancelled", "timed_out"})
TRUST_BASES = frozenset(
    {
        "contract_sample",
        "untrusted_pr_path",
        "protected_workflow",
        "approved_evaluator",
    }
)
PRODUCTION_TRUST_BASES = frozenset({"protected_workflow", "approved_evaluator"})
TARGET_BINDING_KINDS = frozenset(
    {"candidate_commit", "event_head", "explicit_assessment_target"}
)
ALLOWED_EVENTS = frozenset({"pull_request", "push", "merge_group", "workflow_dispatch"})
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_REF_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_EVIDENCE_REF_FIELDS = frozenset({"name", "version", "path", "sha256"})


class RequiredAssessmentCheckError(ValueError):
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
        raise RequiredAssessmentCheckError(code)
    return value


def _flag(value, code):
    if not isinstance(value, bool):
        raise RequiredAssessmentCheckError(code)
    return value


def _sha1(value, code):
    text = _text(value, code).lower()
    if not _SHA1_RE.fullmatch(text):
        raise RequiredAssessmentCheckError(code)
    return text


def _internal_ref(value, code):
    text = _text(value, code)
    if not _SHA256_REF_RE.fullmatch(text):
        raise RequiredAssessmentCheckError(code)
    return text


def _evidence_ref(value, code):
    if not isinstance(value, dict) or set(value) - _EVIDENCE_REF_FIELDS:
        raise RequiredAssessmentCheckError(code)
    record = {
        "name": _text(value.get("name"), code),
        "version": _text(value.get("version"), code),
        "sha256": _text(value.get("sha256"), code).lower(),
    }
    if not re.fullmatch(r"[0-9a-f]{64}", record["sha256"]):
        raise RequiredAssessmentCheckError(code)
    if value.get("path") is not None:
        record["path"] = _text(value.get("path"), code)
    return record


def _optional_evidence_ref(value, code):
    if value is None:
        return None
    return _evidence_ref(value, code)


def _canonical_evidence(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def normalize_target_binding(value, context):
    """Require an explicit Assessment→GitHub target mapping.

    Candidate Commit is not mandated for every event. A verified binding must
    still name the assessed commit (equal to ``head_sha``) and match the
    repository / event / Run / Attempt used for publication.
    """
    if value is None:
        raise RequiredAssessmentCheckError("publication_target_binding_missing")
    if not isinstance(value, dict):
        raise RequiredAssessmentCheckError("publication_target_binding_invalid")
    kind = _text(value.get("kind"), "publication_target_binding_invalid")
    if kind not in TARGET_BINDING_KINDS:
        raise RequiredAssessmentCheckError("publication_target_binding_invalid")
    commit = _sha1(value.get("commit"), "publication_target_commit_invalid")
    if commit != context["head_sha"]:
        raise RequiredAssessmentCheckError("publication_target_sha_mismatch")
    repository = _text(value.get("repository"), "publication_target_repository_invalid")
    if not _REPO_RE.fullmatch(repository):
        raise RequiredAssessmentCheckError("publication_target_repository_invalid")
    if repository != context["repository"]:
        raise RequiredAssessmentCheckError("publication_target_repository_mismatch")
    event_name = _text(value.get("event_name"), "publication_target_event_invalid")
    if event_name != context["event_name"]:
        raise RequiredAssessmentCheckError("publication_target_event_mismatch")
    run_id = value.get("run_id")
    run_attempt = value.get("run_attempt")
    if type(run_id) is not int or run_id < 1:
        raise RequiredAssessmentCheckError("publication_target_run_id_invalid")
    if type(run_attempt) is not int or run_attempt < 1:
        raise RequiredAssessmentCheckError("publication_target_run_attempt_invalid")
    if run_id != context["run_id"] or run_attempt != context["run_attempt"]:
        raise RequiredAssessmentCheckError("publication_target_run_mismatch")
    binding = {
        "kind": kind,
        "commit": commit,
        "repository": repository,
        "event_name": event_name,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "tree": None,
    }
    if value.get("tree") is not None:
        binding["tree"] = _sha1(value.get("tree"), "publication_target_tree_invalid")
    return binding


def normalize_publication_context(value):
    """Normalize the GitHub/Actions identity that will publish the Check."""
    if not isinstance(value, dict):
        raise RequiredAssessmentCheckError("publication_context_invalid")
    repository = _text(value.get("repository"), "publication_repository_invalid")
    if not _REPO_RE.fullmatch(repository):
        raise RequiredAssessmentCheckError("publication_repository_invalid")
    trust_basis = _text(value.get("trust_basis"), "publication_trust_basis_invalid")
    if trust_basis not in TRUST_BASES:
        raise RequiredAssessmentCheckError("publication_trust_basis_invalid")
    event_name = _text(value.get("event_name"), "publication_event_invalid")
    if event_name not in ALLOWED_EVENTS:
        raise RequiredAssessmentCheckError("publication_event_unsupported")
    context = {
        "repository": repository,
        "head_sha": _sha1(value.get("head_sha"), "publication_head_sha_invalid"),
        "event_name": event_name,
        "workflow_ref": _text(
            value.get("workflow_ref"), "publication_workflow_invalid"
        ),
        "workflow_sha": _sha1(
            value.get("workflow_sha"), "publication_workflow_sha_invalid"
        ),
        "run_id": value.get("run_id"),
        "run_attempt": value.get("run_attempt"),
        "job_name": _text(value.get("job_name"), "publication_job_name_invalid"),
        "evaluator_ref": _evidence_ref(
            value.get("evaluator_ref"), "publication_evaluator_invalid"
        ),
        "policy_ref": _optional_evidence_ref(
            value.get("policy_ref"), "publication_policy_invalid"
        ),
        "trust_basis": trust_basis,
        "allow_contract_sample_publication": _flag(
            value.get("allow_contract_sample_publication", False),
            "publication_contract_sample_flag_invalid",
        ),
        "details_url": value.get("details_url"),
    }
    if type(context["run_id"]) is not int or context["run_id"] < 1:
        raise RequiredAssessmentCheckError("publication_run_id_invalid")
    if type(context["run_attempt"]) is not int or context["run_attempt"] < 1:
        raise RequiredAssessmentCheckError("publication_run_attempt_invalid")
    if context["details_url"] is not None:
        context["details_url"] = _text(
            context["details_url"], "publication_details_url_invalid"
        )
    context["target_binding"] = normalize_target_binding(
        value.get("target_binding"), context
    )
    return context


def _bind_assessment_target(assessment, context):
    """Fail closed unless Assessment identity matches the verified target."""
    binding = context["target_binding"]
    candidate = assessment.get("candidate")
    commit = None
    if isinstance(candidate, dict):
        raw = candidate.get("commit")
        if raw is not None:
            if not isinstance(raw, str) or not _SHA1_RE.fullmatch(raw.lower()):
                raise RequiredAssessmentCheckError(
                    "assessment_candidate_commit_invalid"
                )
            commit = raw.lower()
    if commit is not None:
        if commit != binding["commit"] or commit != context["head_sha"]:
            raise RequiredAssessmentCheckError("publication_head_sha_mismatch")
        return
    if binding["kind"] == "candidate_commit":
        raise RequiredAssessmentCheckError("assessment_candidate_commit_missing")
    # event_head / explicit_assessment_target may bind without Candidate.commit,
    # but only through the verified target_binding already equal to head_sha.


def _title_for(assessment):
    if assessment["status"] == "passed":
        return "Required Assessment passed"
    reasons = assessment.get("blocked_reasons") or ()
    if reasons:
        return f"Required Assessment blocked ({reasons[0]})"
    return "Required Assessment blocked"


def _summary_for(assessment, context):
    summary = assessment.get("summary") or {}
    lines = [
        f"status: `{assessment['status']}`",
        f"assessment_ref: `{assessment['assessment_ref']}`",
        f"phase: `{assessment.get('phase')}`",
        f"action: `{assessment.get('action')}`",
        f"target: `{assessment.get('target')}`",
        f"head_sha: `{context['head_sha']}`",
        f"event: `{context['event_name']}`",
        f"trust_basis: `{context['trust_basis']}`",
        f"target_binding: `{context['target_binding']['kind']}`",
        (
            "obligations: "
            f"satisfied={summary.get('satisfied', 0)} "
            f"unmet={summary.get('unmet', 0)} "
            f"unverified={summary.get('unverified', 0)} "
            f"not_applicable={summary.get('not_applicable', 0)} "
            f"not_yet_due={summary.get('not_yet_due', 0)}"
        ),
    ]
    blocked = assessment.get("blocked_reasons") or ()
    if blocked:
        lines.append("blocked_reasons:")
        lines.extend(f"- `{reason}`" for reason in blocked[:20])
    return "\n".join(lines)


def resolve_conclusion(assessment, context):
    """Map Assessment + declared trust to a GitHub Check conclusion.

    A blocked Assessment always yields ``failure``. ``skipped`` / ``neutral``
    are never legal gate conclusions. Declared ``trust_basis`` alone never
    authorizes a production POST; see ``publish_check_run``.
    """
    status = assessment["status"]
    if status not in required_assessment.GATE_STATUSES:
        raise RequiredAssessmentCheckError("assessment_status_invalid")
    if status == "blocked":
        return "failure", ["assessment_blocked"]
    contract_sample = bool((assessment.get("inputs") or {}).get("contract_sample"))
    if contract_sample:
        if not context["allow_contract_sample_publication"]:
            raise RequiredAssessmentCheckError("contract_sample_success_refused")
        if context["trust_basis"] != "contract_sample":
            raise RequiredAssessmentCheckError("contract_sample_trust_basis_required")
        return "success", ["contract_sample_publication_not_production_trust"]
    if context["trust_basis"] not in PRODUCTION_TRUST_BASES:
        raise RequiredAssessmentCheckError("production_success_trust_unproven")
    return "success", []


def _check_name_for(contract_sample):
    return SAMPLE_CHECK_NAME if contract_sample else CHECK_NAME


def build_check_publication(assessment, publication_context):
    """Build the Check Run publication envelope from a validated Assessment.

    Contract-sample envelopes use ``SAMPLE_CHECK_NAME`` so they never share the
    production Required Context identity. Does not call GitHub.
    """
    try:
        required_assessment.validate_assessment(assessment)
    except required_assessment.RequiredAssessmentError as error:
        raise RequiredAssessmentCheckError(error.code) from None
    context = normalize_publication_context(publication_context)
    _bind_assessment_target(assessment, context)
    conclusion, publication_reasons = resolve_conclusion(assessment, context)
    if conclusion in FORBIDDEN_CONCLUSIONS:
        raise RequiredAssessmentCheckError("forbidden_conclusion")
    if conclusion not in CONCLUSIONS:
        raise RequiredAssessmentCheckError("conclusion_invalid")
    if assessment["status"] == "blocked" and conclusion == "success":
        raise RequiredAssessmentCheckError("forced_success_refused")

    contract_sample = bool((assessment.get("inputs") or {}).get("contract_sample"))
    check_name = _check_name_for(contract_sample)
    request = {
        "name": check_name,
        "head_sha": context["head_sha"],
        "status": CHECK_STATUS_COMPLETED,
        "conclusion": conclusion,
        "external_id": assessment["assessment_ref"],
        "output": {
            "title": _title_for(assessment),
            "summary": _summary_for(assessment, context),
        },
    }
    if context["details_url"] is not None:
        request["details_url"] = context["details_url"]

    publication = {
        "kind": PUBLICATION_KIND,
        "schema_version": PUBLICATION_VERSION,
        "check_name": check_name,
        "assessment_ref": assessment["assessment_ref"],
        "assessment_status": assessment["status"],
        "conclusion": conclusion,
        "status": CHECK_STATUS_COMPLETED,
        "head_sha": context["head_sha"],
        "repository": context["repository"],
        "event_name": context["event_name"],
        "workflow_ref": context["workflow_ref"],
        "workflow_sha": context["workflow_sha"],
        "run_id": context["run_id"],
        "run_attempt": context["run_attempt"],
        "job_name": context["job_name"],
        "evaluator_ref": context["evaluator_ref"],
        "policy_ref": context["policy_ref"],
        "trust_basis": context["trust_basis"],
        "target_binding": context["target_binding"],
        "contract_sample": contract_sample,
        "publication_reasons": publication_reasons,
        "limitations": [
            "publication_does_not_verify_server_rulesets",
            "publication_does_not_authorize_release",
            "declarative_trust_basis_is_not_publish_authorization",
            "native_required_contexts_remain_independent",
        ],
        "request": request,
        "publication_ref": None,
    }
    if contract_sample:
        publication["limitations"].append(
            "contract_sample_uses_non_required_check_name"
        )
        publication["limitations"].append(
            "contract_sample_rejected_by_production_publisher"
        )
    if context["trust_basis"] == "untrusted_pr_path":
        publication["limitations"].append("untrusted_pr_path_cannot_mint_success")
    publication["publication_ref"] = _digest(
        {key: value for key, value in publication.items() if key != "publication_ref"}
    )
    return publication


def publication_for_missing_assessment(publication_context, *, reason):
    """Emit a completed failure Check when the Assessment itself is absent.

    GitHub may treat a skipped/neutral Required Check as passable. Missing
    Assessment evidence must therefore complete as ``failure``, not ``skipped``.
    """
    context = normalize_publication_context(publication_context)
    reason = _text(reason, "missing_assessment_reason_invalid")
    request = {
        "name": CHECK_NAME,
        "head_sha": context["head_sha"],
        "status": CHECK_STATUS_COMPLETED,
        "conclusion": "failure",
        "output": {
            "title": "Required Assessment missing",
            "summary": (
                f"No staged Required Assessment was available for publication.\n"
                f"reason: `{reason}`\n"
                f"head_sha: `{context['head_sha']}`\n"
                f"event: `{context['event_name']}`\n"
                f"trust_basis: `{context['trust_basis']}`"
            ),
        },
    }
    if context["details_url"] is not None:
        request["details_url"] = context["details_url"]
    publication = {
        "kind": PUBLICATION_KIND,
        "schema_version": PUBLICATION_VERSION,
        "check_name": CHECK_NAME,
        "assessment_ref": None,
        "assessment_status": None,
        "conclusion": "failure",
        "status": CHECK_STATUS_COMPLETED,
        "head_sha": context["head_sha"],
        "repository": context["repository"],
        "event_name": context["event_name"],
        "workflow_ref": context["workflow_ref"],
        "workflow_sha": context["workflow_sha"],
        "run_id": context["run_id"],
        "run_attempt": context["run_attempt"],
        "job_name": context["job_name"],
        "evaluator_ref": context["evaluator_ref"],
        "policy_ref": context["policy_ref"],
        "trust_basis": context["trust_basis"],
        "target_binding": context["target_binding"],
        "contract_sample": False,
        "publication_reasons": [reason],
        "limitations": [
            "publication_does_not_verify_server_rulesets",
            "publication_does_not_authorize_release",
            "declarative_trust_basis_is_not_publish_authorization",
            "native_required_contexts_remain_independent",
            "missing_assessment_completed_as_failure",
        ],
        "request": request,
        "publication_ref": None,
    }
    publication["publication_ref"] = _digest(
        {key: value for key, value in publication.items() if key != "publication_ref"}
    )
    return publication


def validate_publication(publication):
    """Recompute the publication digest and enforce gate conclusion invariants."""
    if not isinstance(publication, dict):
        raise RequiredAssessmentCheckError("publication_invalid")
    if publication.get("kind") != PUBLICATION_KIND:
        raise RequiredAssessmentCheckError("publication_kind_invalid")
    if publication.get("schema_version") != PUBLICATION_VERSION:
        raise RequiredAssessmentCheckError("publication_schema_unsupported")
    stored = _internal_ref(
        publication.get("publication_ref"), "publication_ref_invalid"
    )
    payload = {
        key: value for key, value in publication.items() if key != "publication_ref"
    }
    if _digest(payload) != stored:
        raise RequiredAssessmentCheckError("publication_ref_mismatch")
    check_name = publication.get("check_name")
    contract_sample = publication.get("contract_sample")
    if contract_sample is True:
        if check_name != SAMPLE_CHECK_NAME:
            raise RequiredAssessmentCheckError("publication_sample_check_name_invalid")
    elif contract_sample is False:
        if check_name != CHECK_NAME:
            raise RequiredAssessmentCheckError("publication_check_name_invalid")
    else:
        raise RequiredAssessmentCheckError("publication_contract_sample_flag_invalid")
    if publication.get("status") != CHECK_STATUS_COMPLETED:
        raise RequiredAssessmentCheckError("publication_status_incomplete")
    conclusion = publication.get("conclusion")
    if conclusion in FORBIDDEN_CONCLUSIONS:
        raise RequiredAssessmentCheckError("forbidden_conclusion")
    if conclusion not in CONCLUSIONS:
        raise RequiredAssessmentCheckError("conclusion_invalid")
    if publication.get("assessment_status") == "blocked" and conclusion == "success":
        raise RequiredAssessmentCheckError("forced_success_refused")
    request = publication.get("request")
    if not isinstance(request, dict):
        raise RequiredAssessmentCheckError("publication_request_invalid")
    if request.get("name") != check_name:
        raise RequiredAssessmentCheckError("publication_request_name_invalid")
    if request.get("head_sha") != publication.get("head_sha"):
        raise RequiredAssessmentCheckError("publication_request_sha_mismatch")
    if request.get("conclusion") != conclusion:
        raise RequiredAssessmentCheckError("publication_request_conclusion_mismatch")
    if request.get("status") != CHECK_STATUS_COMPLETED:
        raise RequiredAssessmentCheckError("publication_request_status_incomplete")
    assessment_ref = publication.get("assessment_ref")
    if assessment_ref is None:
        if request.get("external_id") is not None:
            raise RequiredAssessmentCheckError("publication_external_id_unexpected")
    else:
        _internal_ref(assessment_ref, "publication_assessment_ref_invalid")
        if request.get("external_id") != assessment_ref:
            raise RequiredAssessmentCheckError("publication_external_id_mismatch")
    target = publication.get("target_binding")
    if not isinstance(target, dict):
        raise RequiredAssessmentCheckError("publication_target_binding_missing")
    if target.get("commit") != publication.get("head_sha"):
        raise RequiredAssessmentCheckError("publication_target_sha_mismatch")
    if target.get("repository") != publication.get("repository"):
        raise RequiredAssessmentCheckError("publication_target_repository_mismatch")
    return publication


def normalize_publishing_authorization(value, publication):
    """Normalize a Provider/Workflow publishing authorization for production POST.

    The authorization must bind the exact Assessment, Check name, conclusion,
    repository, head SHA, Workflow identity, Run/Attempt and evaluator. A
    caller-asserted ``trust_basis`` string is not sufficient.
    """
    if not isinstance(value, dict):
        raise RequiredAssessmentCheckError("publishing_authorization_invalid")
    record = {
        "assessment_ref": _internal_ref(
            value.get("assessment_ref"), "publishing_authorization_assessment_invalid"
        ),
        "check_name": _text(
            value.get("check_name"), "publishing_authorization_check_name_invalid"
        ),
        "conclusion": _text(
            value.get("conclusion"), "publishing_authorization_conclusion_invalid"
        ),
        "head_sha": _sha1(
            value.get("head_sha"), "publishing_authorization_head_sha_invalid"
        ),
        "repository": _text(
            value.get("repository"), "publishing_authorization_repository_invalid"
        ),
        "workflow_ref": _text(
            value.get("workflow_ref"), "publishing_authorization_workflow_invalid"
        ),
        "workflow_sha": _sha1(
            value.get("workflow_sha"), "publishing_authorization_workflow_sha_invalid"
        ),
        "run_id": value.get("run_id"),
        "run_attempt": value.get("run_attempt"),
        "evaluator_ref": _evidence_ref(
            value.get("evaluator_ref"), "publishing_authorization_evaluator_invalid"
        ),
        "authority_ref": _evidence_ref(
            value.get("authority_ref"), "publishing_authorization_authority_invalid"
        ),
    }
    if type(record["run_id"]) is not int or record["run_id"] < 1:
        raise RequiredAssessmentCheckError("publishing_authorization_run_id_invalid")
    if type(record["run_attempt"]) is not int or record["run_attempt"] < 1:
        raise RequiredAssessmentCheckError(
            "publishing_authorization_run_attempt_invalid"
        )
    if record["check_name"] != CHECK_NAME:
        raise RequiredAssessmentCheckError(
            "publishing_authorization_non_production_check"
        )
    if record["conclusion"] != "success":
        raise RequiredAssessmentCheckError("publishing_authorization_success_required")
    if record["assessment_ref"] != publication.get("assessment_ref"):
        raise RequiredAssessmentCheckError(
            "publishing_authorization_assessment_mismatch"
        )
    if record["check_name"] != publication.get("check_name"):
        raise RequiredAssessmentCheckError(
            "publishing_authorization_check_name_mismatch"
        )
    if record["conclusion"] != publication.get("conclusion"):
        raise RequiredAssessmentCheckError(
            "publishing_authorization_conclusion_mismatch"
        )
    if record["head_sha"] != publication.get("head_sha"):
        raise RequiredAssessmentCheckError("publishing_authorization_head_sha_mismatch")
    if record["repository"] != publication.get("repository"):
        raise RequiredAssessmentCheckError(
            "publishing_authorization_repository_mismatch"
        )
    if record["workflow_ref"] != publication.get("workflow_ref"):
        raise RequiredAssessmentCheckError("publishing_authorization_workflow_mismatch")
    if record["workflow_sha"] != publication.get("workflow_sha"):
        raise RequiredAssessmentCheckError(
            "publishing_authorization_workflow_sha_mismatch"
        )
    if record["run_id"] != publication.get("run_id") or record[
        "run_attempt"
    ] != publication.get("run_attempt"):
        raise RequiredAssessmentCheckError("publishing_authorization_run_mismatch")
    if _canonical_evidence(record["evaluator_ref"]) != _canonical_evidence(
        publication.get("evaluator_ref")
    ):
        raise RequiredAssessmentCheckError(
            "publishing_authorization_evaluator_mismatch"
        )
    return record


def publishing_authorization_for(publication, *, authority_ref):
    """Build a correctly bound success authorization envelope for tests/stubs."""
    if publication.get("conclusion") != "success":
        raise RequiredAssessmentCheckError("publishing_authorization_success_required")
    return {
        "assessment_ref": publication["assessment_ref"],
        "check_name": publication["check_name"],
        "conclusion": publication["conclusion"],
        "head_sha": publication["head_sha"],
        "repository": publication["repository"],
        "workflow_ref": publication["workflow_ref"],
        "workflow_sha": publication["workflow_sha"],
        "run_id": publication["run_id"],
        "run_attempt": publication["run_attempt"],
        "evaluator_ref": publication["evaluator_ref"],
        "authority_ref": authority_ref,
    }


def publish_check_run(
    publication,
    *,
    publishing_authorization=None,
    publish_authority=None,
    repository=None,
    github=None,
):
    """POST a production Required Check through native ``gh api``.

    Refuses contract-sample / non-production Check names. Production
    ``success`` requires ``publishing_authorization`` bound to the publication
    and accepted by ``publish_authority`` (trusted Provider/Workflow boundary).
    Caller-asserted ``trust_basis`` alone never authorizes the POST. Repository
    overrides that disagree with the publication are rejected.
    """
    validate_publication(publication)
    if repository is not None and repository != publication["repository"]:
        raise RequiredAssessmentCheckError("publication_repository_override_rejected")
    if (
        publication.get("contract_sample")
        or publication.get("check_name") != CHECK_NAME
    ):
        raise RequiredAssessmentCheckError(
            "production_publisher_rejects_non_production_check"
        )
    if publication["conclusion"] == "success":
        if publish_authority is None:
            raise RequiredAssessmentCheckError("publish_authority_missing")
        authorization = normalize_publishing_authorization(
            publishing_authorization, publication
        )
        if publish_authority(authorization, publication) is not True:
            raise RequiredAssessmentCheckError("publishing_authorization_rejected")
    client = github or GitHub(publication["repository"])
    try:
        result = client("check-runs", method="POST", data=publication["request"])
    except ApiError as error:
        raise RequiredAssessmentCheckError(
            f"github_check_publish_failed:{error.status or 'unavailable'}"
        ) from None
    if not isinstance(result, dict) or type(result.get("id")) is not int:
        raise RequiredAssessmentCheckError("github_check_response_invalid")
    return result
