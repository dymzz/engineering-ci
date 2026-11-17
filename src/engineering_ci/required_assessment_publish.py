"""#61 G2 trusted Workflow publish authority for Required Assessment Check.

Implements the production Provider/Workflow boundary that R2
``publish_check_run`` requires via ``publish_authority``. It binds live
Actions identity (``GITHUB_*``) to the publication and authorization
envelopes so a caller-asserted ``trust_basis`` string alone cannot POST
``success``.

This module does **not**:

- produce live Universe / Admission / Receipt inputs (still follow-up G2
  production wiring);
- re-run Assessment (#61 R1) or rebuild publication envelopes beyond the
  Actions identity helpers;
- inspect or mutate Rulesets / Branch Protection (#61 G3/R3);
- authorize Release (#61 R4/R5).
"""

from __future__ import annotations

import os
import re

from . import required_assessment_check as rac
from .github_cli import GitHub

DEFAULT_PROTECTED_WORKFLOW_PATHS = frozenset(
    {
        ".github/workflows/ci.yml",
        ".github/workflows/required-assessment.yml",
    }
)
DEFAULT_ALLOWED_EVENTS = frozenset(rac.ALLOWED_EVENTS)
_WORKFLOW_REF_RE = re.compile(r"^(?P<path>.+\.ya?ml)@(?P<ref>.+)$")
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_EVIDENCE_REF_FIELDS = frozenset({"name", "version", "path", "sha256"})


class RequiredAssessmentPublishError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


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


def _positive_int(value, code):
    if isinstance(value, bool) or type(value) is not int or value < 1:
        if isinstance(value, str) and value.isdigit() and int(value) >= 1:
            return int(value)
        raise RequiredAssessmentPublishError(code)
    return value


def _parse_workflow_ref(workflow_ref, code="actions_workflow_ref_invalid"):
    text = _text(workflow_ref, code)
    match = _WORKFLOW_REF_RE.fullmatch(text)
    if match is None:
        raise RequiredAssessmentPublishError(code)
    return match.group("path"), match.group("ref")


def actions_runtime_identity(environ=None):
    """Extract the live Actions identity used to authorize publication.

    Fail closed when not running under Actions or when required identity
    fields are missing / malformed.
    """
    env = os.environ if environ is None else environ
    if env.get("GITHUB_ACTIONS") != "true":
        raise RequiredAssessmentPublishError("actions_runtime_required")
    repository = _text(env.get("GITHUB_REPOSITORY"), "actions_repository_invalid")
    if not _REPO_RE.fullmatch(repository):
        raise RequiredAssessmentPublishError("actions_repository_invalid")
    workflow_ref = _text(env.get("GITHUB_WORKFLOW_REF"), "actions_workflow_ref_invalid")
    workflow_path, _ref = _parse_workflow_ref(workflow_ref)
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
        "workflow_ref": workflow_ref,
        "workflow_path": workflow_path,
        "workflow_sha": workflow_sha,
        "run_id": run_id,
        "run_attempt": run_attempt,
    }


def publication_context_from_actions(
    *,
    evaluator_ref,
    job_name,
    environ=None,
    policy_ref=None,
    trust_basis="protected_workflow",
    target_binding_kind="event_head",
    details_url=None,
    allow_contract_sample_publication=False,
    allowed_workflow_paths=None,
):
    """Build an R2 publication context bound to the live Actions identity."""
    identity = actions_runtime_identity(environ)
    allowed = (
        DEFAULT_PROTECTED_WORKFLOW_PATHS
        if allowed_workflow_paths is None
        else frozenset(allowed_workflow_paths)
    )
    if identity["workflow_path"] not in allowed:
        raise RequiredAssessmentPublishError("actions_workflow_path_not_allowed")
    if trust_basis not in rac.PRODUCTION_TRUST_BASES:
        raise RequiredAssessmentPublishError("actions_trust_basis_not_production")
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
        "evaluator_ref": evaluator_ref,
        "policy_ref": policy_ref,
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


def workflow_publish_authority(
    *,
    authority_ref,
    environ=None,
    allowed_workflow_paths=None,
    allowed_events=None,
):
    """Return a ``publish_authority`` callable bound to trusted Actions identity.

    The callable returns exactly ``True`` only when the authorization envelope
    matches both the publication and the live Actions runtime. Any mismatch
    raises; a soft ``False`` is reserved for explicit verifier rejection of an
    otherwise well-formed authorization (not used by this boundary).
    """
    identity = actions_runtime_identity(environ)
    allowed_paths = (
        DEFAULT_PROTECTED_WORKFLOW_PATHS
        if allowed_workflow_paths is None
        else frozenset(allowed_workflow_paths)
    )
    allowed_event_names = (
        DEFAULT_ALLOWED_EVENTS if allowed_events is None else frozenset(allowed_events)
    )
    if identity["workflow_path"] not in allowed_paths:
        raise RequiredAssessmentPublishError("actions_workflow_path_not_allowed")
    if identity["event_name"] not in allowed_event_names:
        raise RequiredAssessmentPublishError("actions_event_unsupported")
    expected_authority = _evidence_ref(authority_ref, "publish_authority_ref_invalid")

    def publish_authority(authorization, publication):
        if not isinstance(authorization, dict) or not isinstance(publication, dict):
            raise RequiredAssessmentPublishError("publish_authority_inputs_invalid")
        if authorization.get("authority_ref") != expected_authority:
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
        return True

    return publish_authority


def authorize_and_publish(
    publication,
    *,
    authority_ref,
    environ=None,
    allowed_workflow_paths=None,
    allowed_events=None,
    github=None,
    repository=None,
):
    """Authorize a production publication against Actions identity and POST it.

    ``failure`` publications still POST without Actions runtime authority.
    ``success`` always requires a live trusted Workflow identity.
    """
    client = github or GitHub(publication["repository"])
    if publication.get("conclusion") != "success":
        return rac.publish_check_run(
            publication,
            repository=repository,
            github=client,
        )
    authority = workflow_publish_authority(
        authority_ref=authority_ref,
        environ=environ,
        allowed_workflow_paths=allowed_workflow_paths,
        allowed_events=allowed_events,
    )
    authorization = rac.publishing_authorization_for(
        publication, authority_ref=authority_ref
    )
    return rac.publish_check_run(
        publication,
        publishing_authorization=authorization,
        publish_authority=authority,
        repository=repository,
        github=client,
    )
