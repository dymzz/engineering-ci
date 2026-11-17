"""#61 G2 trusted Workflow publish authority for Required Assessment Check.

Implements the production Provider/Workflow boundary that R2
``publish_check_run`` requires via ``publish_authority``. Live Actions
identity is bound to a publisher-approval *claim* that an independent
``approval_authority`` verifier must accept, plus an ``execution_identity``
for the revisions that actually ran. A caller-asserted ``trust_basis``,
self-consistent ``authority_ref``, or caller-minted approval dict alone
cannot POST ``success``.

This module does **not**:

- produce live Universe / Admission / Receipt inputs (still follow-up G2
  production wiring);
- re-run Assessment (#61 R1) or rebuild publication envelopes beyond the
  Actions identity helpers;
- inspect or mutate Rulesets / Branch Protection (#61 G3/R3);
- authorize Release (#61 R4/R5);
- mint or store publisher approvals (integration owns the trusted source).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime

from . import required_assessment_check as rac
from .github_cli import GitHub

DEFAULT_PROTECTED_WORKFLOW_PATHS = frozenset(
    {
        ".github/workflows/ci.yml",
        ".github/workflows/required-assessment.yml",
    }
)
DEFAULT_ALLOWED_EVENTS = frozenset(rac.ALLOWED_EVENTS)
APPROVAL_STATUSES = frozenset({"active", "revoked", "expired"})
# Real Actions form: owner/repo/.github/workflows/name.yml@ref
_WORKFLOW_REF_RE = re.compile(
    r"^(?P<repository>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/"
    r"(?P<path>\.github/workflows/[^@]+\.ya?ml)@(?P<ref>.+)$"
)
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_EVIDENCE_REF_FIELDS = frozenset({"name", "version", "path", "sha256"})


class RequiredAssessmentPublishError(ValueError):
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
        raise RequiredAssessmentPublishError(code)
    return value


def _sha1(value, code):
    text = _text(value, code).lower()
    if not _SHA1_RE.fullmatch(text):
        raise RequiredAssessmentPublishError(code)
    return text


def _evidence_ref(value, code):
    if not isinstance(value, dict) or set(value) - _EVIDENCE_REF_FIELDS:
        raise RequiredAssessmentPublishError(code)
    record = {
        "name": _text(value.get("name"), code),
        "version": _text(value.get("version"), code),
        "sha256": _text(value.get("sha256"), code).lower(),
    }
    if not re.fullmatch(r"[0-9a-f]{64}", record["sha256"]):
        raise RequiredAssessmentPublishError(code)
    if value.get("path") is not None:
        record["path"] = _text(value.get("path"), code)
    return record


def _canonical_evidence(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _positive_int(value, code):
    if isinstance(value, bool) or type(value) is not int or value < 1:
        if isinstance(value, str) and value.isdigit() and int(value) >= 1:
            return int(value)
        raise RequiredAssessmentPublishError(code)
    return value


def _parse_iso8601(value, code):
    if value is None:
        return None
    text = _text(value, code)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise RequiredAssessmentPublishError(code) from error
    if parsed.tzinfo is None:
        raise RequiredAssessmentPublishError(code)
    return parsed.astimezone(UTC)


def parse_workflow_ref(workflow_ref, code="actions_workflow_ref_invalid"):
    """Parse ``OWNER/REPO/.github/workflows/name.yml@ref`` into parts."""
    text = _text(workflow_ref, code)
    match = _WORKFLOW_REF_RE.fullmatch(text)
    if match is None:
        raise RequiredAssessmentPublishError(code)
    return {
        "workflow_ref": text,
        "workflow_repository": match.group("repository"),
        "workflow_path": match.group("path"),
        "workflow_git_ref": match.group("ref"),
    }


def actions_runtime_identity(environ=None):
    """Extract the live Actions identity used to authorize publication.

    Fail closed when not running under Actions or when required identity
    fields are missing / malformed. ``GITHUB_WORKFLOW_REF`` must use the real
    ``OWNER/REPO/.github/workflows/...@ref`` form.
    """
    env = os.environ if environ is None else environ
    if env.get("GITHUB_ACTIONS") != "true":
        raise RequiredAssessmentPublishError("actions_runtime_required")
    repository = _text(env.get("GITHUB_REPOSITORY"), "actions_repository_invalid")
    if not _REPO_RE.fullmatch(repository):
        raise RequiredAssessmentPublishError("actions_repository_invalid")
    parsed = parse_workflow_ref(
        env.get("GITHUB_WORKFLOW_REF"), "actions_workflow_ref_invalid"
    )
    if parsed["workflow_repository"].lower() != repository.lower():
        raise RequiredAssessmentPublishError("actions_workflow_repository_mismatch")
    event_name = _text(env.get("GITHUB_EVENT_NAME"), "actions_event_invalid")
    if event_name not in DEFAULT_ALLOWED_EVENTS:
        raise RequiredAssessmentPublishError("actions_event_unsupported")
    head_sha = _sha1(env.get("GITHUB_SHA"), "actions_head_sha_invalid")
    workflow_sha = _sha1(env.get("GITHUB_WORKFLOW_SHA"), "actions_workflow_sha_invalid")
    run_id = _positive_int(env.get("GITHUB_RUN_ID"), "actions_run_id_invalid")
    run_attempt = _positive_int(
        env.get("GITHUB_RUN_ATTEMPT"), "actions_run_attempt_invalid"
    )
    return {
        "repository": repository,
        "head_sha": head_sha,
        "event_name": event_name,
        "workflow_ref": parsed["workflow_ref"],
        "workflow_repository": parsed["workflow_repository"],
        "workflow_path": parsed["workflow_path"],
        "workflow_git_ref": parsed["workflow_git_ref"],
        "workflow_sha": workflow_sha,
        "run_id": run_id,
        "run_attempt": run_attempt,
    }


def _approval_authority(value):
    if value is None:
        raise RequiredAssessmentPublishError("approval_authority_required")
    if not callable(value):
        raise RequiredAssessmentPublishError("approval_authority_invalid")
    return value


def normalize_execution_identity(value):
    """Normalize the *actually executed* workflow / evaluator / policy identity.

    Distinct from publication-declared refs: a PR may declare an approved
    evaluator while executing a different revision.
    """
    if not isinstance(value, dict):
        raise RequiredAssessmentPublishError("execution_identity_invalid")
    record = {
        "workflow_sha": _sha1(
            value.get("workflow_sha"), "execution_identity_workflow_sha_invalid"
        ),
        "evaluator_ref": _evidence_ref(
            value.get("evaluator_ref"), "execution_identity_evaluator_invalid"
        ),
        "policy_ref": None,
    }
    if value.get("policy_ref") is not None:
        record["policy_ref"] = _evidence_ref(
            value.get("policy_ref"), "execution_identity_policy_invalid"
        )
    return record


def _verify_approval_authority(approval_authority, context):
    verifier = _approval_authority(approval_authority)
    try:
        accepted = verifier(context)
    except RequiredAssessmentPublishError:
        raise
    except Exception as error:
        raise RequiredAssessmentPublishError(
            "approval_authority_verifier_error"
        ) from error
    if accepted is not True:
        raise RequiredAssessmentPublishError("approval_authority_rejected")


def normalize_publisher_approval(value, *, now=None):
    """Normalize a publisher-approval *claim*.

    Structural/temporal checks only. Production trust requires a separate
    ``approval_authority`` verifier that independently confirms the claim was
    issued by an authorized source outside the evaluated PR.
    """
    if not isinstance(value, dict):
        raise RequiredAssessmentPublishError("publisher_approval_invalid")
    status = _text(value.get("status"), "publisher_approval_status_invalid")
    if status not in APPROVAL_STATUSES:
        raise RequiredAssessmentPublishError("publisher_approval_status_invalid")
    repository = _text(value.get("repository"), "publisher_approval_repository_invalid")
    if not _REPO_RE.fullmatch(repository):
        raise RequiredAssessmentPublishError("publisher_approval_repository_invalid")
    workflow_repository = _text(
        value.get("workflow_repository"),
        "publisher_approval_workflow_repository_invalid",
    )
    if not _REPO_RE.fullmatch(workflow_repository):
        raise RequiredAssessmentPublishError(
            "publisher_approval_workflow_repository_invalid"
        )
    workflow_path = _text(
        value.get("workflow_path"), "publisher_approval_workflow_path_invalid"
    )
    if not workflow_path.startswith(".github/workflows/") or not workflow_path.endswith(
        (".yml", ".yaml")
    ):
        raise RequiredAssessmentPublishError("publisher_approval_workflow_path_invalid")
    workflow_sha = _sha1(
        value.get("workflow_sha"), "publisher_approval_workflow_sha_invalid"
    )
    allowed_events = value.get("allowed_events")
    if not isinstance(allowed_events, (list, tuple)) or not allowed_events:
        raise RequiredAssessmentPublishError("publisher_approval_events_invalid")
    events = []
    for event in allowed_events:
        text = _text(event, "publisher_approval_events_invalid")
        if text not in DEFAULT_ALLOWED_EVENTS:
            raise RequiredAssessmentPublishError("publisher_approval_events_invalid")
        events.append(text)
    evaluators = value.get("approved_evaluator_refs")
    if not isinstance(evaluators, (list, tuple)) or not evaluators:
        raise RequiredAssessmentPublishError("publisher_approval_evaluators_invalid")
    approved_evaluators = [
        _evidence_ref(item, "publisher_approval_evaluator_invalid")
        for item in evaluators
    ]
    policies = value.get("approved_policy_refs")
    if policies is None:
        approved_policies = []
    else:
        if not isinstance(policies, (list, tuple)):
            raise RequiredAssessmentPublishError("publisher_approval_policies_invalid")
        approved_policies = [
            _evidence_ref(item, "publisher_approval_policy_invalid")
            for item in policies
        ]
    authority_ref = _evidence_ref(
        value.get("authority_ref"), "publisher_approval_authority_invalid"
    )
    valid_from = _parse_iso8601(
        value.get("valid_from"), "publisher_approval_valid_from_invalid"
    )
    valid_to = _parse_iso8601(
        value.get("valid_to"), "publisher_approval_valid_to_invalid"
    )
    if valid_from is not None and valid_to is not None and valid_to <= valid_from:
        raise RequiredAssessmentPublishError(
            "publisher_approval_validity_window_invalid"
        )
    record = {
        "kind": "engineering-ci-required-assessment-publisher-approval",
        "schema_version": 1,
        "status": status,
        "repository": repository,
        "workflow_repository": workflow_repository,
        "workflow_path": workflow_path,
        "workflow_sha": workflow_sha,
        "allowed_events": sorted(set(events)),
        "approved_evaluator_refs": approved_evaluators,
        "approved_policy_refs": approved_policies,
        "authority_ref": authority_ref,
        "valid_from": None if valid_from is None else valid_from.isoformat(),
        "valid_to": None if valid_to is None else valid_to.isoformat(),
        "approval_ref": None,
    }
    clock = now or datetime.now(UTC)
    if status == "revoked":
        raise RequiredAssessmentPublishError("publisher_approval_revoked")
    if status == "expired":
        raise RequiredAssessmentPublishError("publisher_approval_expired")
    if valid_from is not None and clock < valid_from:
        raise RequiredAssessmentPublishError("publisher_approval_not_yet_valid")
    if valid_to is not None and clock >= valid_to:
        raise RequiredAssessmentPublishError("publisher_approval_expired")
    record["approval_ref"] = _digest(
        {key: value for key, value in record.items() if key != "approval_ref"}
    )
    return record


def _require_workflow_allowed(identity, *, allowed_workflow_paths, approval=None):
    allowed = (
        DEFAULT_PROTECTED_WORKFLOW_PATHS
        if allowed_workflow_paths is None
        else frozenset(allowed_workflow_paths)
    )
    if identity["workflow_path"] not in allowed:
        raise RequiredAssessmentPublishError("actions_workflow_path_not_allowed")
    if approval is not None:
        if identity["repository"].lower() != approval["repository"].lower():
            raise RequiredAssessmentPublishError(
                "publisher_approval_repository_mismatch"
            )
        if (
            identity["workflow_repository"].lower()
            != approval["workflow_repository"].lower()
        ):
            raise RequiredAssessmentPublishError(
                "publisher_approval_workflow_repository_mismatch"
            )
        if identity["workflow_path"] != approval["workflow_path"]:
            raise RequiredAssessmentPublishError(
                "publisher_approval_workflow_path_mismatch"
            )
        if identity["workflow_sha"] != approval["workflow_sha"]:
            raise RequiredAssessmentPublishError(
                "publisher_approval_workflow_sha_mismatch"
            )
        if identity["event_name"] not in approval["allowed_events"]:
            raise RequiredAssessmentPublishError("publisher_approval_event_not_allowed")


def _require_execution_matches_approval(execution, approval, identity):
    if execution["workflow_sha"] != identity["workflow_sha"]:
        raise RequiredAssessmentPublishError(
            "execution_identity_workflow_sha_runtime_mismatch"
        )
    if execution["workflow_sha"] != approval["workflow_sha"]:
        raise RequiredAssessmentPublishError(
            "execution_identity_workflow_sha_not_approved"
        )
    if not any(
        _canonical_evidence(item) == _canonical_evidence(execution["evaluator_ref"])
        for item in approval["approved_evaluator_refs"]
    ):
        raise RequiredAssessmentPublishError(
            "execution_identity_evaluator_not_approved"
        )
    if execution["policy_ref"] is None:
        if approval["approved_policy_refs"]:
            raise RequiredAssessmentPublishError(
                "execution_identity_policy_required_by_approval"
            )
    elif not any(
        _canonical_evidence(item) == _canonical_evidence(execution["policy_ref"])
        for item in approval["approved_policy_refs"]
    ):
        raise RequiredAssessmentPublishError("execution_identity_policy_not_approved")


def publication_context_from_actions(
    *,
    evaluator_ref,
    job_name,
    publisher_approval,
    approval_authority,
    execution_identity,
    environ=None,
    policy_ref=None,
    trust_basis="protected_workflow",
    target_binding_kind="event_head",
    details_url=None,
    allow_contract_sample_publication=False,
    allowed_workflow_paths=None,
    now=None,
):
    """Build an R2 publication context bound to live Actions + verified approval."""
    approval = normalize_publisher_approval(publisher_approval, now=now)
    identity = actions_runtime_identity(environ)
    execution = normalize_execution_identity(execution_identity)
    _require_workflow_allowed(
        identity, allowed_workflow_paths=allowed_workflow_paths, approval=approval
    )
    _verify_approval_authority(
        approval_authority,
        {
            "approval": approval,
            "runtime_identity": identity,
            "execution_identity": execution,
        },
    )
    _require_execution_matches_approval(execution, approval, identity)
    if trust_basis not in rac.PRODUCTION_TRUST_BASES:
        raise RequiredAssessmentPublishError("actions_trust_basis_not_production")
    evaluator = _evidence_ref(evaluator_ref, "actions_evaluator_invalid")
    if _canonical_evidence(evaluator) != _canonical_evidence(
        execution["evaluator_ref"]
    ):
        raise RequiredAssessmentPublishError("actions_evaluator_execution_mismatch")
    normalized_policy = None
    if policy_ref is not None:
        normalized_policy = _evidence_ref(policy_ref, "actions_policy_invalid")
        if _canonical_evidence(normalized_policy) != _canonical_evidence(
            execution["policy_ref"]
        ):
            raise RequiredAssessmentPublishError("actions_policy_execution_mismatch")
    elif execution["policy_ref"] is not None:
        raise RequiredAssessmentPublishError("actions_policy_execution_mismatch")
    server_url = (environ or os.environ).get("GITHUB_SERVER_URL", "https://github.com")
    if details_url is None:
        details_url = (
            f"{server_url.rstrip('/')}/{identity['repository']}"
            f"/actions/runs/{identity['run_id']}"
        )
    return {
        "repository": identity["repository"],
        "head_sha": identity["head_sha"],
        "event_name": identity["event_name"],
        "workflow_ref": identity["workflow_ref"],
        "workflow_sha": identity["workflow_sha"],
        "run_id": identity["run_id"],
        "run_attempt": identity["run_attempt"],
        "job_name": _text(job_name, "actions_job_name_invalid"),
        "evaluator_ref": evaluator,
        "policy_ref": normalized_policy,
        "trust_basis": trust_basis,
        "allow_contract_sample_publication": allow_contract_sample_publication,
        "details_url": details_url,
        "target_binding": {
            "kind": target_binding_kind,
            "commit": identity["head_sha"],
            "repository": identity["repository"],
            "event_name": identity["event_name"],
            "run_id": identity["run_id"],
            "run_attempt": identity["run_attempt"],
            "tree": None,
        },
    }


def verify_publisher_identity(
    publication,
    *,
    publisher_approval,
    approval_authority,
    environ=None,
    allowed_workflow_paths=None,
    now=None,
):
    """Prove the live publisher may emit the official Check name.

    Required for both ``success`` and ``failure`` production publications.
    Requires an independent ``approval_authority``; does not by itself authorize
    a ``success`` conclusion (that needs execution identity too).
    """
    approval = normalize_publisher_approval(publisher_approval, now=now)
    identity = actions_runtime_identity(environ)
    _require_workflow_allowed(
        identity, allowed_workflow_paths=allowed_workflow_paths, approval=approval
    )
    if not isinstance(publication, dict):
        raise RequiredAssessmentPublishError("publisher_identity_publication_invalid")
    if publication.get("check_name") != rac.CHECK_NAME:
        raise RequiredAssessmentPublishError("publisher_identity_non_production_check")
    if publication.get("contract_sample"):
        raise RequiredAssessmentPublishError(
            "publisher_identity_contract_sample_refused"
        )
    for key in (
        "repository",
        "head_sha",
        "event_name",
        "workflow_ref",
        "workflow_sha",
        "run_id",
        "run_attempt",
    ):
        if publication.get(key) != identity[key]:
            raise RequiredAssessmentPublishError(
                f"publisher_identity_publication_{key}_mismatch"
            )
    _verify_approval_authority(
        approval_authority,
        {
            "approval": approval,
            "runtime_identity": identity,
            "publication": publication,
        },
    )
    return {"identity": identity, "approval": approval}


def workflow_publish_authority(
    *,
    publisher_approval,
    approval_authority,
    execution_identity,
    environ=None,
    allowed_workflow_paths=None,
    allowed_events=None,
    now=None,
):
    """Return a ``publish_authority`` callable for production ``success``.

    ``True`` requires:

    1. ``approval_authority`` independently accepts the approval claim;
    2. live Actions identity matches the verified approval;
    3. ``execution_identity`` (actual executed revisions) matches the approval
       and the publication's declared evaluator/policy;
    4. authorization runtime fields match the live identity.
    """
    approval = normalize_publisher_approval(publisher_approval, now=now)
    identity = actions_runtime_identity(environ)
    execution = normalize_execution_identity(execution_identity)
    _require_workflow_allowed(
        identity, allowed_workflow_paths=allowed_workflow_paths, approval=approval
    )
    _verify_approval_authority(
        approval_authority,
        {
            "approval": approval,
            "runtime_identity": identity,
            "execution_identity": execution,
        },
    )
    _require_execution_matches_approval(execution, approval, identity)
    allowed_event_names = (
        frozenset(approval["allowed_events"])
        if allowed_events is None
        else frozenset(allowed_events)
    )
    if identity["event_name"] not in allowed_event_names:
        raise RequiredAssessmentPublishError("actions_event_unsupported")

    def publish_authority(authorization, publication):
        if not isinstance(authorization, dict) or not isinstance(publication, dict):
            raise RequiredAssessmentPublishError("publish_authority_inputs_invalid")
        if authorization.get("authority_ref") != approval["authority_ref"]:
            raise RequiredAssessmentPublishError("publish_authority_ref_mismatch")
        for key in (
            "repository",
            "head_sha",
            "event_name",
            "workflow_ref",
            "workflow_sha",
            "run_id",
            "run_attempt",
        ):
            if authorization.get(key) != identity[key]:
                raise RequiredAssessmentPublishError(
                    f"publish_authority_runtime_{key}_mismatch"
                )
            if publication.get(key) != identity[key]:
                raise RequiredAssessmentPublishError(
                    f"publish_authority_publication_{key}_mismatch"
                )
        if publication.get("trust_basis") not in rac.PRODUCTION_TRUST_BASES:
            raise RequiredAssessmentPublishError(
                "publish_authority_trust_basis_not_production"
            )
        if publication.get("check_name") != rac.CHECK_NAME:
            raise RequiredAssessmentPublishError(
                "publish_authority_non_production_check"
            )
        if publication.get("contract_sample"):
            raise RequiredAssessmentPublishError(
                "publish_authority_contract_sample_refused"
            )
        if _canonical_evidence(publication.get("evaluator_ref")) != _canonical_evidence(
            execution["evaluator_ref"]
        ):
            raise RequiredAssessmentPublishError(
                "publish_authority_evaluator_execution_mismatch"
            )
        pub_policy = publication.get("policy_ref")
        if execution["policy_ref"] is None:
            if pub_policy is not None:
                raise RequiredAssessmentPublishError(
                    "publish_authority_policy_execution_mismatch"
                )
        elif _canonical_evidence(pub_policy) != _canonical_evidence(
            execution["policy_ref"]
        ):
            raise RequiredAssessmentPublishError(
                "publish_authority_policy_execution_mismatch"
            )
        return True

    return publish_authority


def authorize_and_publish(
    publication,
    *,
    publisher_approval,
    approval_authority,
    environ=None,
    execution_identity=None,
    allowed_workflow_paths=None,
    allowed_events=None,
    github=None,
    repository=None,
    now=None,
):
    """Authorize a production publication and POST it.

    Both conclusions require Actions runtime + ``approval_authority``-verified
    publisher approval. ``success`` additionally requires ``execution_identity``
    matching the verified approval and publication declarations.
    """
    client = github or GitHub(publication["repository"])
    verified = verify_publisher_identity(
        publication,
        publisher_approval=publisher_approval,
        approval_authority=approval_authority,
        environ=environ,
        allowed_workflow_paths=allowed_workflow_paths,
        now=now,
    )
    if publication.get("conclusion") != "success":
        return rac.publish_check_run(
            publication,
            repository=repository,
            github=client,
        )
    if execution_identity is None:
        raise RequiredAssessmentPublishError("execution_identity_required")
    authority = workflow_publish_authority(
        publisher_approval=verified["approval"],
        approval_authority=approval_authority,
        execution_identity=execution_identity,
        environ=environ,
        allowed_workflow_paths=allowed_workflow_paths,
        allowed_events=allowed_events,
        now=now,
    )
    authorization = rac.publishing_authorization_for(
        publication, authority_ref=verified["approval"]["authority_ref"]
    )
    return rac.publish_check_run(
        publication,
        publishing_authorization=authorization,
        publish_authority=authority,
        repository=repository,
        github=client,
    )
