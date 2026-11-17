"""#61 G3/R3 server-side Required Assessment Context observation.

Read-only inspection of repository Rulesets and Branch Protection for the
stable Required Assessment Check name published by #61 R2/G2. Separates
**visibility** (can the current credential read the setting), **configuration**
(is the Check named among required contexts), and **enforcement probe**
(controlled push/merge rejection — never claimed by this module).

This module does **not**:

- mutate Rulesets, Branch Protection, Apps, bypasses or permissions;
- publish the Required Assessment Check (#61 R2/G2);
- re-run Assessment (#61 R1) or authorize Release (#61 R4/R5);
- treat Workflow PASS as proof that server protection is enforced.
"""

from __future__ import annotations

import hashlib
import json
import re

from .github_cli import ApiError, GitHub
from .required_assessment_check import CHECK_NAME

PROTECTION_KIND = "engineering-ci-required-assessment-protection"
PROTECTION_VERSION = 1
VISIBILITY_STATUSES = frozenset({"readable", "absent", "forbidden", "error"})
ENFORCEMENT_STATUSES = frozenset(
    {"configured", "absent", "not_verified", "misconfigured"}
)
PROBE_STATUSES = frozenset({"not_performed"})
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class RequiredAssessmentProtectionError(ValueError):
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
        raise RequiredAssessmentProtectionError(code)
    return value


def _get(github, path):
    try:
        return "ok", github(path)
    except ApiError as error:
        if error.status == 404:
            return "absent", None
        if error.status in {401, 403}:
            return "forbidden", None
        return "error", {"status": error.status}


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return []


def _context_names_from_ruleset(detail):
    """Extract required status-check context names from one Ruleset detail."""
    names = []
    if not isinstance(detail, dict):
        return names
    for rule in _as_list(detail.get("rules")):
        if not isinstance(rule, dict):
            continue
        if rule.get("type") != "required_status_checks":
            continue
        parameters = rule.get("parameters") or {}
        if not isinstance(parameters, dict):
            continue
        for item in _as_list(parameters.get("required_status_checks")):
            if isinstance(item, dict) and isinstance(item.get("context"), str):
                names.append(item["context"])
            elif isinstance(item, str):
                names.append(item)
    return names


def _context_names_from_branch_protection(payload):
    """Extract required status-check context names from Branch Protection."""
    names = []
    if not isinstance(payload, dict):
        return names
    status_checks = payload.get("required_status_checks")
    if not isinstance(status_checks, dict):
        return names
    for item in _as_list(status_checks.get("checks")):
        if isinstance(item, dict) and isinstance(item.get("context"), str):
            names.append(item["context"])
    for context in _as_list(status_checks.get("contexts")):
        if isinstance(context, str):
            names.append(context)
    return names


def _ruleset_enabled(detail):
    if not isinstance(detail, dict):
        return False
    enforcement = detail.get("enforcement")
    if enforcement is None:
        return True
    return enforcement == "active"


def inspect_server_protection(
    repository,
    *,
    branch="main",
    check_name=CHECK_NAME,
    github=None,
):
    """Observe Ruleset / Branch Protection configuration for ``check_name``.

    Returns a self-digesting observation. Configuration presence is not push
    rejection proof; ``enforcement.probe`` stays ``not_performed``.
    """
    repository = _text(repository, "protection_repository_invalid")
    if not _REPO_RE.fullmatch(repository):
        raise RequiredAssessmentProtectionError("protection_repository_invalid")
    branch = _text(branch, "protection_branch_invalid")
    check_name = _text(check_name, "protection_check_name_invalid")
    client = github or GitHub(repository)

    rulesets_visibility, rulesets_payload = _get(client, "rulesets")
    ruleset_records = []
    ruleset_contexts = []
    ruleset_errors = []
    if rulesets_visibility == "ok":
        if not isinstance(rulesets_payload, list):
            rulesets_visibility = "error"
            ruleset_errors.append("rulesets_payload_invalid")
        else:
            for item in rulesets_payload:
                if not isinstance(item, dict) or type(item.get("id")) is not int:
                    ruleset_errors.append("ruleset_list_entry_invalid")
                    continue
                ruleset_id = item["id"]
                detail_visibility, detail = _get(client, f"rulesets/{ruleset_id}")
                record = {
                    "id": ruleset_id,
                    "name": item.get("name")
                    if isinstance(item.get("name"), str)
                    else None,
                    "detail_visibility": (
                        "readable" if detail_visibility == "ok" else detail_visibility
                    ),
                    "enforcement": None,
                    "contexts": [],
                }
                if detail_visibility == "ok":
                    record["enforcement"] = (
                        detail.get("enforcement") if isinstance(detail, dict) else None
                    )
                    contexts = _context_names_from_ruleset(detail)
                    record["contexts"] = contexts
                    if _ruleset_enabled(detail):
                        ruleset_contexts.extend(contexts)
                    else:
                        record["detail_visibility"] = "readable"
                elif detail_visibility == "forbidden":
                    ruleset_errors.append(f"ruleset_{ruleset_id}_forbidden")
                elif detail_visibility == "error":
                    ruleset_errors.append(f"ruleset_{ruleset_id}_error")
                ruleset_records.append(record)
            rulesets_visibility = "readable"
    elif rulesets_visibility == "absent":
        # Empty list is readable; a true 404 on the collection is unexpected.
        rulesets_visibility = "error"
        ruleset_errors.append("rulesets_endpoint_absent")
    elif rulesets_visibility == "forbidden":
        pass
    else:
        rulesets_visibility = "error"

    bp_visibility_raw, bp_payload = _get(client, f"branches/{branch}/protection")
    branch_protection_contexts = []
    if bp_visibility_raw == "ok":
        bp_visibility = "readable"
        branch_protection_contexts = _context_names_from_branch_protection(bp_payload)
    elif bp_visibility_raw == "absent":
        bp_visibility = "absent"
    elif bp_visibility_raw == "forbidden":
        bp_visibility = "forbidden"
    else:
        bp_visibility = "error"

    in_rulesets = None
    if rulesets_visibility == "readable":
        in_rulesets = check_name in ruleset_contexts
    in_branch_protection = None
    if bp_visibility == "readable":
        in_branch_protection = check_name in branch_protection_contexts
    elif bp_visibility == "absent":
        in_branch_protection = False

    reasons = []
    if rulesets_visibility == "forbidden":
        reasons.append("rulesets_not_readable")
    if rulesets_visibility == "error":
        reasons.append("rulesets_read_error")
    if bp_visibility == "forbidden":
        reasons.append("branch_protection_not_readable")
    if bp_visibility == "error":
        reasons.append("branch_protection_read_error")
    reasons.extend(ruleset_errors)

    readable_hits = []
    if in_rulesets is True:
        readable_hits.append("rulesets")
    if in_branch_protection is True:
        readable_hits.append("branch_protection")
    readable_misses = []
    if in_rulesets is False:
        readable_misses.append("rulesets")
    if in_branch_protection is False:
        readable_misses.append("branch_protection")

    if readable_hits:
        enforcement_status = "configured"
        reasons.append("required_assessment_context_configured")
    elif rulesets_visibility in {"forbidden", "error"} and bp_visibility in {
        "forbidden",
        "error",
    }:
        enforcement_status = "not_verified"
        reasons.append("no_readable_protection_source")
    elif readable_misses and not readable_hits:
        # At least one source was readable and lacked the context.
        if rulesets_visibility in {"forbidden", "error"} or bp_visibility in {
            "forbidden",
            "error",
        }:
            enforcement_status = "not_verified"
            reasons.append("partial_visibility_without_configured_context")
        else:
            enforcement_status = "absent"
            reasons.append("required_assessment_context_absent")
    else:
        enforcement_status = "not_verified"
        reasons.append("protection_observation_inconclusive")

    # Disabled Rulesets that still list the context are misconfigured signal.
    disabled_hits = []
    for record in ruleset_records:
        if record.get("enforcement") not in {
            None,
            "active",
        } and check_name in record.get("contexts", ()):
            disabled_hits.append(record["id"])
    if disabled_hits and enforcement_status == "absent":
        enforcement_status = "misconfigured"
        reasons.append("required_assessment_context_only_in_disabled_ruleset")
    elif disabled_hits and enforcement_status == "configured":
        reasons.append("additional_disabled_ruleset_lists_context")

    observation = {
        "kind": PROTECTION_KIND,
        "schema_version": PROTECTION_VERSION,
        "repository": repository,
        "branch": branch,
        "check_name": check_name,
        "visibility": {
            "rulesets": rulesets_visibility,
            "branch_protection": bp_visibility,
        },
        "configuration": {
            "rulesets": ruleset_records,
            "ruleset_contexts": sorted(set(ruleset_contexts)),
            "branch_protection_contexts": sorted(set(branch_protection_contexts)),
            "required_assessment_in_rulesets": in_rulesets,
            "required_assessment_in_branch_protection": in_branch_protection,
        },
        "enforcement": {
            "status": enforcement_status,
            "probe": "not_performed",
            "reasons": reasons,
        },
        "limitations": [
            "observation_does_not_mutate_protection",
            "configuration_presence_is_not_push_rejection_proof",
            "enforcement_probe_not_performed",
            "native_required_contexts_remain_independent",
            "does_not_authorize_release",
            "does_not_publish_required_assessment_check",
        ],
        "observation_ref": None,
    }
    if enforcement_status not in ENFORCEMENT_STATUSES:
        raise RequiredAssessmentProtectionError("protection_enforcement_invalid")
    if observation["enforcement"]["probe"] not in PROBE_STATUSES:
        raise RequiredAssessmentProtectionError("protection_probe_invalid")
    for status in observation["visibility"].values():
        if status not in VISIBILITY_STATUSES:
            raise RequiredAssessmentProtectionError("protection_visibility_invalid")
    observation["observation_ref"] = _digest(
        {key: value for key, value in observation.items() if key != "observation_ref"}
    )
    return observation


def validate_protection_observation(observation):
    """Recompute digest and enforce observation shape invariants."""
    if not isinstance(observation, dict):
        raise RequiredAssessmentProtectionError("protection_observation_invalid")
    if observation.get("kind") != PROTECTION_KIND:
        raise RequiredAssessmentProtectionError("protection_kind_invalid")
    if observation.get("schema_version") != PROTECTION_VERSION:
        raise RequiredAssessmentProtectionError("protection_version_invalid")
    check_name = _text(observation.get("check_name"), "protection_check_name_invalid")
    repository = _text(observation.get("repository"), "protection_repository_invalid")
    if not _REPO_RE.fullmatch(repository):
        raise RequiredAssessmentProtectionError("protection_repository_invalid")
    branch = _text(observation.get("branch"), "protection_branch_invalid")
    visibility = observation.get("visibility")
    if not isinstance(visibility, dict):
        raise RequiredAssessmentProtectionError("protection_visibility_invalid")
    for key in ("rulesets", "branch_protection"):
        if visibility.get(key) not in VISIBILITY_STATUSES:
            raise RequiredAssessmentProtectionError("protection_visibility_invalid")
    enforcement = observation.get("enforcement")
    if not isinstance(enforcement, dict):
        raise RequiredAssessmentProtectionError("protection_enforcement_invalid")
    if enforcement.get("status") not in ENFORCEMENT_STATUSES:
        raise RequiredAssessmentProtectionError("protection_enforcement_invalid")
    if enforcement.get("probe") not in PROBE_STATUSES:
        raise RequiredAssessmentProtectionError("protection_probe_invalid")
    expected = _digest(
        {key: value for key, value in observation.items() if key != "observation_ref"}
    )
    if observation.get("observation_ref") != expected:
        raise RequiredAssessmentProtectionError("protection_observation_ref_mismatch")
    return {
        "repository": repository,
        "branch": branch,
        "check_name": check_name,
        "enforcement_status": enforcement["status"],
        "observation_ref": expected,
    }
