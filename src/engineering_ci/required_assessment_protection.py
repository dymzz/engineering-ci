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
from urllib.parse import quote

from .github_cli import ApiError, GitHub
from .required_assessment_check import CHECK_NAME

PROTECTION_KIND = "engineering-ci-required-assessment-protection"
PROTECTION_VERSION = 2
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


def _required_check(context, app_id, strict):
    if not isinstance(context, str) or not context:
        return None
    pinned = type(app_id) is int and app_id > 0
    return {
        "context": context,
        "source_app_id": app_id if pinned else None,
        "source_app_status": (
            "pinned"
            if pinned
            else "unpinned"
            if app_id is None or type(app_id) is int and app_id == -1
            else "unknown"
        ),
        "strict": strict if type(strict) is bool else None,
    }


def _checks_from_status_check_rule(rule):
    if not isinstance(rule, dict) or rule.get("type") != "required_status_checks":
        return []
    parameters = rule.get("parameters") or {}
    if not isinstance(parameters, dict):
        return []
    checks = []
    strict = parameters.get("strict_required_status_checks_policy")
    for item in _as_list(parameters.get("required_status_checks")):
        if isinstance(item, dict):
            check = _required_check(
                item.get("context"), item.get("integration_id"), strict
            )
        else:
            check = _required_check(item, None, strict)
        if check is not None:
            checks.append(check)
    return checks


def _checks_from_branch_rules(rules):
    return [
        check
        for rule in _as_list(rules)
        for check in _checks_from_status_check_rule(rule)
    ]


def _context_names_from_ruleset_detail(detail):
    if not isinstance(detail, dict):
        return []
    return [
        check["context"] for check in _checks_from_branch_rules(detail.get("rules"))
    ]


def _checks_from_branch_protection(payload):
    checks = []
    if not isinstance(payload, dict):
        return checks
    status_checks = payload.get("required_status_checks")
    if not isinstance(status_checks, dict):
        return checks
    strict = status_checks.get("strict")
    for item in _as_list(status_checks.get("checks")):
        if isinstance(item, dict):
            check = _required_check(item.get("context"), item.get("app_id"), strict)
            if check is not None:
                checks.append(check)
    # GitHub also lists typed entries in the legacy contexts array.
    typed_names = {check["context"] for check in checks}
    for context in _as_list(status_checks.get("contexts")):
        if isinstance(context, str) and context not in typed_names:
            check = _required_check(context, None, strict)
            if check is not None:
                checks.append(check)
    return checks


def _match_source_app(checks, expected_app_id):
    if not checks or expected_app_id is None:
        return "not_verified"
    if any(item["source_app_status"] == "unpinned" for item in checks):
        return "unpinned"
    if any(
        item["source_app_status"] == "pinned"
        and item["source_app_id"] != expected_app_id
        for item in checks
    ):
        return "mismatch"
    if any(item["source_app_status"] == "unknown" for item in checks):
        return "not_verified"
    return "matched"


def _branch_ref_applicability(conditions, branch):
    """Classify Ruleset ref conditions against ``branch`` without a full engine.

    Returns ``applicable``, ``not_applicable``, or ``unknown``. Only exact
    ``refs/heads/{branch}`` includes (no wildcards / tilde aliases) prove
    applicability. Patterned includes stay ``unknown`` so they never drive
    branch ``misconfigured`` alone.
    """
    if not isinstance(conditions, dict):
        return "unknown"
    ref_name = conditions.get("ref_name")
    if not isinstance(ref_name, dict):
        return "unknown"
    include = _as_list(ref_name.get("include"))
    exclude = _as_list(ref_name.get("exclude"))
    if not include:
        return "unknown"
    target = f"refs/heads/{branch}"
    if any(not isinstance(item, str) for item in include + exclude):
        return "unknown"
    if any(("*" in item) or item.startswith("~") for item in include + exclude):
        return "unknown"
    if target in exclude:
        return "not_applicable"
    if target in include:
        return "applicable"
    return "not_applicable"


def _load_supplemental_rulesets(github, *, branch):
    """Optional Ruleset list/detail for scoped supplemental diagnostics."""
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
            "conditions": None,
            "branch_applicability": "unknown",
            "bypass_actors": None,
        }
        if detail_visibility == "ok":
            if not isinstance(detail, dict):
                errors.append(f"ruleset_{ruleset_id}_detail_invalid")
                detail_complete = False
            else:
                enforcement = detail.get("enforcement")
                record["enforcement"] = enforcement
                record["contexts"] = _context_names_from_ruleset_detail(detail)
                if isinstance(detail.get("bypass_actors"), list):
                    record["bypass_actors"] = detail["bypass_actors"]
                conditions = detail.get("conditions")
                record["conditions"] = (
                    copy_conditions(conditions)
                    if isinstance(conditions, dict)
                    else None
                )
                record["branch_applicability"] = _branch_ref_applicability(
                    conditions, branch
                )
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


def copy_conditions(conditions):
    ref_name = conditions.get("ref_name") if isinstance(conditions, dict) else None
    if not isinstance(ref_name, dict):
        return {"ref_name": None}
    return {
        "ref_name": {
            "include": list(_as_list(ref_name.get("include"))),
            "exclude": list(_as_list(ref_name.get("exclude"))),
        }
    }


def inspect_server_protection(
    repository,
    *,
    branch="main",
    check_name=CHECK_NAME,
    expected_source_app_id=None,
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
    if expected_source_app_id is not None and (
        type(expected_source_app_id) is not int or expected_source_app_id <= 0
    ):
        raise RequiredAssessmentProtectionError("protection_source_app_invalid")
    client = github or GitHub(repository)

    reasons = []
    branch_rules_status, branch_rules, branch_rules_complete = _paginate(
        client, f"rules/branches/{quote(branch, safe='')}"
    )
    branch_rule_checks = []
    if branch_rules_status == "ok":
        branch_rules_visibility = "readable"
        branch_rule_checks = _checks_from_branch_rules(branch_rules)
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
    ) = _load_supplemental_rulesets(client, branch=branch)
    reasons.extend(ruleset_errors)

    bp_visibility_raw, bp_payload = _get(
        client, f"branches/{quote(branch, safe='')}/protection"
    )
    branch_protection_checks = []
    if bp_visibility_raw == "ok" and isinstance(bp_payload, dict):
        bp_visibility = "readable"
        branch_protection_checks = _checks_from_branch_protection(bp_payload)
    elif bp_visibility_raw == "absent":
        bp_visibility = "absent"
    elif bp_visibility_raw == "forbidden":
        bp_visibility = "forbidden"
        reasons.append("branch_protection_not_readable")
    else:
        bp_visibility = "error"
        reasons.append("branch_protection_read_error")

    branch_rule_contexts = [check["context"] for check in branch_rule_checks]
    branch_protection_contexts = [
        check["context"] for check in branch_protection_checks
    ]
    named_checks = [
        check
        for check in branch_rule_checks + branch_protection_checks
        if check["context"] == check_name
    ]
    source_app_status = _match_source_app(named_checks, expected_source_app_id)
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

    # Branch-scoped misconfiguration only. Repository-wide disabled/evaluate
    # Rulesets that are not proven applicable to this branch stay diagnostic.
    branch_disabled_hits = []
    branch_evaluate_hits = []
    supplemental_diagnostics = []
    for record in ruleset_records:
        enforcement = record.get("enforcement")
        contexts = record.get("contexts") or ()
        if check_name not in contexts:
            continue
        if enforcement not in {"disabled", "evaluate"}:
            continue
        applicability = record.get("branch_applicability") or "unknown"
        entry = {
            "id": record["id"],
            "enforcement": enforcement,
            "branch_applicability": applicability,
        }
        supplemental_diagnostics.append(entry)
        if applicability != "applicable":
            continue
        if enforcement == "disabled":
            branch_disabled_hits.append(record["id"])
        else:
            branch_evaluate_hits.append(record["id"])

    if readable_hits:
        if source_app_status in {"unpinned", "mismatch"}:
            enforcement_status = "misconfigured"
            reasons.append(f"required_assessment_source_app_{source_app_status}")
        elif applicable_incomplete or source_app_status != "matched":
            enforcement_status = "not_verified"
            reasons.append("required_assessment_source_app_not_verified")
        else:
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
        # Applicable authoritative sources fully deny the Context. Only
        # disabled/evaluate Rulesets proven applicable to this branch may
        # elevate to misconfigured; other-branch supplemental stays diagnostic.
        if branch_disabled_hits or branch_evaluate_hits:
            enforcement_status = "misconfigured"
            if branch_disabled_hits:
                reasons.append("required_assessment_context_only_in_disabled_ruleset")
            if branch_evaluate_hits:
                reasons.append("required_assessment_context_only_in_evaluate_ruleset")
        else:
            enforcement_status = "absent"
            reasons.append("required_assessment_context_absent")
            if supplemental_diagnostics:
                reasons.append("supplemental_non_applicable_ruleset_diagnostics")
            if supplemental_visibility in {"forbidden", "error"} or (
                supplemental_visibility == "readable" and not supplemental_complete
            ):
                reasons.append("supplemental_rulesets_incomplete_for_misconfig_scan")
    else:
        enforcement_status = "not_verified"
        reasons.append("protection_observation_inconclusive")

    if branch_disabled_hits and enforcement_status == "configured":
        reasons.append("additional_disabled_ruleset_lists_context")

    observation = {
        "kind": PROTECTION_KIND,
        "schema_version": PROTECTION_VERSION,
        "repository": repository,
        "branch": branch,
        "check_name": check_name,
        "expected_source_app_id": expected_source_app_id,
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
            "branch_rule_checks": branch_rule_checks,
            "rulesets": ruleset_records,
            "ruleset_contexts": sorted(set(branch_rule_contexts)),
            "branch_protection_contexts": sorted(set(branch_protection_contexts)),
            "branch_protection_checks": branch_protection_checks,
            "branch_protection_enforce_admins": (
                bp_payload["enforce_admins"].get("enabled")
                if isinstance(bp_payload, dict)
                and isinstance(bp_payload.get("enforce_admins"), dict)
                else None
            ),
            "required_assessment_in_branch_rules": in_branch_rules,
            "required_assessment_in_rulesets": in_rulesets,
            "required_assessment_in_branch_protection": in_branch_protection,
            "branch_rules_complete": branch_rules_complete,
            "supplemental_rulesets_complete": supplemental_complete,
            "supplemental_ruleset_diagnostics": supplemental_diagnostics,
        },
        "enforcement": {
            "status": enforcement_status,
            "source_app_status": source_app_status,
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
    version = observation.get("schema_version")
    if type(version) is not int or version not in {1, PROTECTION_VERSION}:
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
    enforcement_status = enforcement["status"]
    if version == 1 and enforcement_status == "configured":
        enforcement_status = "not_verified"
    if version == PROTECTION_VERSION and enforcement_status == "configured":
        configuration = observation.get("configuration")
        if not isinstance(configuration, dict):
            raise RequiredAssessmentProtectionError("protection_source_app_unverified")
        checks = [
            check
            for key in ("branch_rule_checks", "branch_protection_checks")
            for check in _as_list(configuration.get(key))
            if isinstance(check, dict) and check.get("context") == check_name
        ]
        app_id = observation.get("expected_source_app_id")
        if (
            type(app_id) is not int
            or app_id <= 0
            or not checks
            or any(
                type(check.get("source_app_id")) is not int
                or check["source_app_id"] != app_id
                or check.get("source_app_status") != "pinned"
                for check in checks
            )
            or configuration.get("branch_rules_complete") is not True
            or visibility["branch_rules"] != "readable"
            or visibility["branch_protection"] not in {"readable", "absent"}
            or enforcement.get("source_app_status") != "matched"
        ):
            raise RequiredAssessmentProtectionError("protection_source_app_unverified")
    return {
        "repository": repository,
        "branch": branch,
        "check_name": check_name,
        "enforcement_status": enforcement_status,
        "observation_ref": expected,
    }
