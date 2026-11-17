"""#61 G3/R3 server-side Required Assessment Context observation.

Read-only inspection of **branch-applicable** Rules and Branch Protection for
the stable Required Assessment Check name published by #61 R2/G2. Separates
**visibility**, **configuration**, and **enforcement probe** (always
``not_performed`` here).

Primary source: ``GET .../rules/branches/{branch}`` (effective active rules for
the queried branch, paginated). Repository Ruleset list/detail is supplemental
evidence for disabled/evaluate misconfiguration signals only.

This module does **not**:

- mutate Rulesets, Branch Protection, Apps, bypasses or permissions;
- publish the Required Assessment Check (#61 R2/G2);
- re-run Assessment (#61 R1) or authorize Release (#61 R4/R5);
- treat Workflow PASS or configuration presence as push-rejection proof.
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
_MAX_PAGES = 20
_PER_PAGE = 100


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


def _paginate(github, base_path, *, per_page=_PER_PAGE, max_pages=_MAX_PAGES):
    """Fetch all pages for a list endpoint.

    Returns ``(visibility, items, complete)``. Incomplete pagination or a
    mid-stream read failure never claims a negative configuration proof.
    """
    items = []
    for page in range(1, max_pages + 1):
        path = f"{base_path}?per_page={per_page}&page={page}"
        status, payload = _get(github, path)
        if page == 1 and status != "ok":
            return status, [], False
        if status != "ok":
            return "error", items, False
        if not isinstance(payload, list):
            return "error", items, False
        items.extend(payload)
        if len(payload) < per_page:
            return "ok", items, True
    return "ok", items, False


def _contexts_from_status_check_rule(rule):
    names = []
    if not isinstance(rule, dict):
        return names
    if rule.get("type") != "required_status_checks":
        return names
    parameters = rule.get("parameters") or {}
    if not isinstance(parameters, dict):
        return names
    for item in _as_list(parameters.get("required_status_checks")):
        if isinstance(item, dict) and isinstance(item.get("context"), str):
            names.append(item["context"])
        elif isinstance(item, str):
            names.append(item)
    return names


def _context_names_from_branch_rules(rules):
    names = []
    for rule in _as_list(rules):
        names.extend(_contexts_from_status_check_rule(rule))
    return names


def _context_names_from_ruleset_detail(detail):
    names = []
    if not isinstance(detail, dict):
        return names
    for rule in _as_list(detail.get("rules")):
        names.extend(_contexts_from_status_check_rule(rule))
    return names


def _context_names_from_branch_protection(payload):
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


def _load_supplemental_rulesets(github):
    """Optional Ruleset list/detail for disabled/evaluate signals only."""
    status, entries, complete = _paginate(github, "rulesets")
    records = []
    errors = []
    if status == "forbidden":
        return "forbidden", records, errors, False
    if status == "absent":
        return "error", records, ["rulesets_endpoint_absent"], False
    if status != "ok":
        return "error", records, ["rulesets_read_error"], False
    detail_complete = complete
    for item in entries:
        if not isinstance(item, dict) or type(item.get("id")) is not int:
            errors.append("ruleset_list_entry_invalid")
            detail_complete = False
            continue
        ruleset_id = item["id"]
        detail_visibility, detail = _get(github, f"rulesets/{ruleset_id}")
        record = {
            "id": ruleset_id,
            "name": item.get("name") if isinstance(item.get("name"), str) else None,
            "detail_visibility": (
                "readable" if detail_visibility == "ok" else detail_visibility
            ),
            "enforcement": None,
            "contexts": [],
        }
        if detail_visibility == "ok":
            if not isinstance(detail, dict):
                errors.append(f"ruleset_{ruleset_id}_detail_invalid")
                detail_complete = False
            else:
                enforcement = detail.get("enforcement")
                record["enforcement"] = enforcement
                record["contexts"] = _context_names_from_ruleset_detail(detail)
                if enforcement is None:
                    # Unknown enforcement is not treated as active applicability.
                    errors.append(f"ruleset_{ruleset_id}_enforcement_unknown")
                    detail_complete = False
        elif detail_visibility == "absent":
            errors.append(f"ruleset_{ruleset_id}_detail_absent")
            detail_complete = False
        elif detail_visibility == "forbidden":
            errors.append(f"ruleset_{ruleset_id}_forbidden")
            detail_complete = False
        else:
            errors.append(f"ruleset_{ruleset_id}_error")
            detail_complete = False
        records.append(record)
    return "readable", records, errors, detail_complete and complete


def inspect_server_protection(
    repository,
    *,
    branch="main",
    check_name=CHECK_NAME,
    github=None,
):
    """Observe branch-applicable Rules / Branch Protection for ``check_name``.

    Returns a self-digesting observation. Configuration presence is not push
    rejection proof; ``enforcement.probe`` stays ``not_performed``. Incomplete
    reads never yield ``absent``.
    """
    repository = _text(repository, "protection_repository_invalid")
    if not _REPO_RE.fullmatch(repository):
        raise RequiredAssessmentProtectionError("protection_repository_invalid")
    branch = _text(branch, "protection_branch_invalid")
    check_name = _text(check_name, "protection_check_name_invalid")
    client = github or GitHub(repository)

    reasons = []
    branch_rules_status, branch_rules, branch_rules_complete = _paginate(
        client, f"rules/branches/{branch}"
    )
    branch_rule_contexts = []
    if branch_rules_status == "ok":
        branch_rules_visibility = "readable"
        branch_rule_contexts = _context_names_from_branch_rules(branch_rules)
        if not branch_rules_complete:
            reasons.append("branch_rules_pagination_incomplete")
    elif branch_rules_status == "forbidden":
        branch_rules_visibility = "forbidden"
        reasons.append("branch_rules_not_readable")
        branch_rules_complete = False
    elif branch_rules_status == "absent":
        # Endpoint should exist; treat as error rather than "no rules".
        branch_rules_visibility = "error"
        reasons.append("branch_rules_endpoint_absent")
        branch_rules_complete = False
    else:
        branch_rules_visibility = "error"
        reasons.append("branch_rules_read_error")
        branch_rules_complete = False

    (
        supplemental_visibility,
        ruleset_records,
        ruleset_errors,
        supplemental_complete,
    ) = _load_supplemental_rulesets(client)
    reasons.extend(ruleset_errors)

    bp_visibility_raw, bp_payload = _get(client, f"branches/{branch}/protection")
    branch_protection_contexts = []
    if bp_visibility_raw == "ok":
        bp_visibility = "readable"
        branch_protection_contexts = _context_names_from_branch_protection(bp_payload)
    elif bp_visibility_raw == "absent":
        bp_visibility = "absent"
    elif bp_visibility_raw == "forbidden":
        bp_visibility = "forbidden"
        reasons.append("branch_protection_not_readable")
    else:
        bp_visibility = "error"
        reasons.append("branch_protection_read_error")

    in_branch_rules = None
    if branch_rules_visibility == "readable" and branch_rules_complete:
        in_branch_rules = check_name in branch_rule_contexts
    elif branch_rules_visibility == "readable" and not branch_rules_complete:
        # Partial page may already show a hit; absence cannot be proven.
        if check_name in branch_rule_contexts:
            in_branch_rules = True
        else:
            in_branch_rules = None
            reasons.append("branch_rules_incomplete_negative_unproven")

    in_branch_protection = None
    if bp_visibility == "readable":
        in_branch_protection = check_name in branch_protection_contexts
    elif bp_visibility == "absent":
        in_branch_protection = False

    # Legacy field name kept for consumers: "rulesets" now means branch-applicable
    # required contexts from rules/branches/{branch}.
    in_rulesets = in_branch_rules

    readable_hits = []
    if in_branch_rules is True:
        readable_hits.append("branch_rules")
    if in_branch_protection is True:
        readable_hits.append("branch_protection")
    readable_misses = []
    if in_branch_rules is False:
        readable_misses.append("branch_rules")
    if in_branch_protection is False:
        readable_misses.append("branch_protection")

    applicable_incomplete = (
        (branch_rules_visibility == "readable" and not branch_rules_complete)
        or branch_rules_visibility in {"forbidden", "error"}
        or bp_visibility in {"forbidden", "error"}
        or (in_branch_rules is None and branch_rules_visibility == "readable")
    )

    disabled_hits = []
    evaluate_hits = []
    for record in ruleset_records:
        enforcement = record.get("enforcement")
        contexts = record.get("contexts") or ()
        if check_name not in contexts:
            continue
        if enforcement == "disabled":
            disabled_hits.append(record["id"])
        elif enforcement == "evaluate":
            evaluate_hits.append(record["id"])

    if readable_hits:
        enforcement_status = "configured"
        reasons.append("required_assessment_context_configured")
    elif applicable_incomplete and not readable_hits:
        enforcement_status = "not_verified"
        if branch_rules_visibility in {"forbidden", "error"} and bp_visibility in {
            "forbidden",
            "error",
        }:
            reasons.append("no_readable_protection_source")
        else:
            reasons.append("incomplete_protection_observation")
    elif readable_misses and not readable_hits:
        # Applicable sources fully deny the context for this branch. Disabled /
        # evaluate Rulesets are not applicable (branch-rules omits them) but
        # still surface misconfiguration when supplemental detail is available.
        if disabled_hits or evaluate_hits:
            enforcement_status = "misconfigured"
            if disabled_hits:
                reasons.append("required_assessment_context_only_in_disabled_ruleset")
            if evaluate_hits:
                reasons.append("required_assessment_context_only_in_evaluate_ruleset")
        else:
            enforcement_status = "absent"
            reasons.append("required_assessment_context_absent")
            if supplemental_visibility in {"forbidden", "error"} or (
                supplemental_visibility == "readable" and not supplemental_complete
            ):
                reasons.append("supplemental_rulesets_incomplete_for_misconfig_scan")
    else:
        enforcement_status = "not_verified"
        reasons.append("protection_observation_inconclusive")

    if disabled_hits and enforcement_status == "configured":
        reasons.append("additional_disabled_ruleset_lists_context")

    observation = {
        "kind": PROTECTION_KIND,
        "schema_version": PROTECTION_VERSION,
        "repository": repository,
        "branch": branch,
        "check_name": check_name,
        "visibility": {
            "branch_rules": branch_rules_visibility,
            "rulesets": supplemental_visibility,
            "branch_protection": bp_visibility,
        },
        "configuration": {
            "branch_rules": branch_rules
            if branch_rules_visibility == "readable"
            else [],
            "branch_rule_contexts": sorted(set(branch_rule_contexts)),
            "rulesets": ruleset_records,
            "ruleset_contexts": sorted(set(branch_rule_contexts)),
            "branch_protection_contexts": sorted(set(branch_protection_contexts)),
            "required_assessment_in_branch_rules": in_branch_rules,
            "required_assessment_in_rulesets": in_rulesets,
            "required_assessment_in_branch_protection": in_branch_protection,
            "branch_rules_complete": branch_rules_complete,
            "supplemental_rulesets_complete": supplemental_complete,
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
            "branch_rules_are_applicability_authority",
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
    for key in ("branch_rules", "rulesets", "branch_protection"):
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
