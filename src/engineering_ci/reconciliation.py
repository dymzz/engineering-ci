"""#66-C Expected -> Routing -> Native Result reconciliation receipt.

This module is the independent binding layer between the immutable Expected
projection produced before execution (#66-B) and the Native Results produced by
#52 checks. It does not execute checks, enumerate Expected Checks, admit
trusted evidence, or decide Required/Release status.

The receipt preserves the original ``expected_check_ref`` / ``expected_set_ref``
/ ``scope_id`` identities. It binds them to real scheduler Job references and
Native Results, records how rule/source/baseline constraints were resolved from
separate evidence, and reports ``matched`` / ``missing`` / ``insufficient`` /
``unknown`` / ``unrouted`` per Expected instance plus ``unexpected`` and
``duplicate`` observations.

Witness selection is never duplicated here: for each coherent candidate group
this module calls ``check_contract.evaluate_expected_satisfaction()`` exactly
once and cites its ``selected_result_ids``. Failed or unselected Results never
complete input or coverage constraints.

A multi-ref Expected container is not a runnable scope. Callers reconcile one
Ref at a time with ``ref_scope_id`` and keep the aggregate identities untouched.
"""

from __future__ import annotations

import hashlib
import json
import re

from . import check_contract, expected_checks

RECONCILIATION_KIND = "engineering-ci-expected-result-reconciliation"
RECONCILIATION_VERSION = 1
ENTRY_STATUSES = frozenset(
    {"matched", "missing", "insufficient", "unknown", "unrouted", "not_applicable"}
)
RECEIPT_STATUSES = frozenset({"matched", "partial", "unrouted", "unknown"})
ROUTING_STATUSES = frozenset(
    {"routed", "unrouted", "filtered", "not_configured", "skipped", "unknown"}
)
_NOT_APPLICABLE_STATUSES = frozenset({"not_applicable", "no_changes", "no_tests"})
_RECONCILABLE_APPLICABILITY = frozenset({"selected", "targeted", "full", "escalated"})
_ENTRY_FIELDS = (
    "name",
    "check_id",
    "definition_version",
    "definition_status",
    "event_ref",
    "selection_rule_ref",
    "rule_ref",
    "rule_status",
    "profile_ref",
    "applicability",
    "policy_status",
    "source_ref",
    "input_refs",
    "baseline_ref",
)
_SHA256_REF_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ReconciliationError(ValueError):
    """A malformed or incoherent reconciliation input.

    ``code`` is the stable public reason; ``native_code`` preserves the
    underlying ``check_contract`` rejection when one caused this error.
    """

    def __init__(self, code, *, native_code=None):
        self.code = code
        self.native_code = native_code
        super().__init__(code)


def _digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _text(value, code):
    if not isinstance(value, str) or not value:
        raise ReconciliationError(code)
    return value


def _sha256_ref(value, code):
    if not isinstance(value, str) or not _SHA256_REF_RE.fullmatch(value):
        raise ReconciliationError(code)
    return value


def _evidence_ref(value, code):
    if not isinstance(value, dict) or set(value) - {
        "name",
        "version",
        "path",
        "sha256",
    }:
        raise ReconciliationError(code)
    _text(value.get("name"), code)
    if not isinstance(value.get("version"), (str, int)) or not str(value["version"]):
        raise ReconciliationError(code)
    if not isinstance(value.get("sha256"), str) or not _SHA256_RE.fullmatch(
        value["sha256"]
    ):
        raise ReconciliationError(code)
    if value.get("path") is not None:
        _text(value["path"], code)
    return value


def _select_scope(expected, ref_scope_id):
    """Return one single-Ref Expected scope, never the aggregate container."""
    if not isinstance(expected, dict):
        raise ReconciliationError("expected_invalid")
    if expected.get("kind") != expected_checks.EXPECTED_KIND:
        raise ReconciliationError("expected_kind_invalid")
    schema_version = expected.get("schema_version")
    if schema_version not in expected_checks.EXPECTED_VERSIONS:
        raise ReconciliationError("expected_schema_unsupported")
    per_ref = expected.get("per_ref")
    if per_ref is not None:
        if ref_scope_id is None:
            raise ReconciliationError("expected_ref_scope_required")
        if not isinstance(per_ref, (list, tuple)):
            raise ReconciliationError("expected_invalid")
        matches = [
            item
            for item in per_ref
            if isinstance(item, dict) and item.get("scope_id") == ref_scope_id
        ]
        if len(matches) != 1:
            raise ReconciliationError("expected_ref_scope_unknown")
        ref = matches[0]
        scope = {
            "schema_version": schema_version,
            "status": ref.get("status"),
            "reason": ref.get("reason"),
            "consumer": expected.get("consumer"),
            "profile_ref": expected.get("profile_ref"),
            "profile_source": expected.get("profile_source"),
            "effective_profile": expected.get("effective_profile"),
            "selection_rule_ref": expected.get("selection_rule_ref"),
            "hygiene_rule_ref": expected.get("hygiene_rule_ref"),
            "sources_rule_ref": expected.get("sources_rule_ref"),
            "event_ref": ref.get("event_ref"),
            "source_ref": ref.get("source"),
            "checks": ref.get("checks", []),
            "non_check_obligations": ref.get("non_check_obligations", []),
            "scope_id": ref.get("scope_id"),
            "expected_set_ref": ref.get("expected_set_ref"),
            "consumer_completeness": "per_ref",
            "ref": {
                key: ref[key]
                for key in (
                    "local_ref",
                    "remote_ref",
                    "ref_action",
                    "force_push",
                    "baseline_status",
                    "source_ref",
                )
                if key in ref
            },
        }
    else:
        if ref_scope_id is not None:
            raise ReconciliationError("expected_ref_scope_unexpected")
        scope = {
            "schema_version": schema_version,
            "status": expected.get("status"),
            "reason": expected.get("reason"),
            "consumer": expected.get("consumer"),
            "profile_ref": expected.get("profile_ref"),
            "profile_source": expected.get("profile_source"),
            "effective_profile": expected.get("effective_profile"),
            "selection_rule_ref": expected.get("selection_rule_ref"),
            "hygiene_rule_ref": expected.get("hygiene_rule_ref"),
            "sources_rule_ref": expected.get("sources_rule_ref"),
            "event_ref": expected.get("event_ref"),
            "source_ref": expected.get("source_ref"),
            "checks": expected.get("checks"),
            "non_check_obligations": expected.get("non_check_obligations", []),
            "scope_id": expected.get("scope_id"),
            "expected_set_ref": expected.get("expected_set_ref"),
            "consumer_completeness": expected.get("consumer_completeness"),
            "ref": {},
        }
    if scope["status"] not in expected_checks.STATUSES:
        raise ReconciliationError("expected_status_invalid")
    if not isinstance(scope["checks"], (list, tuple)):
        raise ReconciliationError("expected_checks_invalid")
    if not isinstance(scope["event_ref"], dict):
        raise ReconciliationError("expected_event_invalid")
    if not isinstance(scope["source_ref"], dict):
        raise ReconciliationError("expected_source_invalid")
    if not isinstance(scope["selection_rule_ref"], dict):
        raise ReconciliationError("expected_selection_rule_invalid")
    if schema_version == 2 and not isinstance(scope["effective_profile"], dict):
        raise ReconciliationError("expected_effective_profile_missing")
    if scope["source_ref"].get("snapshot") == "multi":
        raise ReconciliationError("expected_multi_ref_requires_per_ref")
    return scope


def _verify_scope(scope):
    """Recompute immutable identities and parent/entry consistency."""
    checks = []
    seen = set()
    for entry in scope["checks"]:
        if not isinstance(entry, dict):
            raise ReconciliationError("expected_check_invalid")
        missing = [key for key in _ENTRY_FIELDS if key not in entry]
        if missing:
            raise ReconciliationError("expected_check_incomplete")
        ref = _sha256_ref(entry.get("expected_check_ref"), "expected_check_ref_invalid")
        if ref in seen:
            raise ReconciliationError("expected_check_ref_duplicate")
        seen.add(ref)
        if expected_checks.entry_identity(entry) != ref:
            raise ReconciliationError("expected_check_ref_mismatch")
        if entry["event_ref"] != scope["event_ref"]:
            raise ReconciliationError("expected_entry_event_mismatch")
        if entry["source_ref"] != scope["source_ref"]:
            raise ReconciliationError("expected_entry_source_mismatch")
        if entry["selection_rule_ref"] != scope["selection_rule_ref"]:
            raise ReconciliationError("expected_entry_rule_mismatch")
        if entry["profile_ref"] != scope["profile_ref"]:
            raise ReconciliationError("expected_entry_profile_mismatch")
        if not isinstance(entry["applicability"], dict):
            raise ReconciliationError("expected_applicability_invalid")
        if not isinstance(entry["input_refs"], list):
            raise ReconciliationError("expected_input_refs_invalid")
        checks.append(entry)
    expected_set_ref = expected_checks.set_identity(
        checks,
        scope.get("non_check_obligations") or (),
        schema_version=scope["schema_version"],
    )
    if expected_set_ref != _sha256_ref(
        scope.get("expected_set_ref"), "expected_set_ref_invalid"
    ):
        raise ReconciliationError("expected_set_ref_mismatch")
    profile = scope.get("effective_profile") or {}
    if scope["schema_version"] == 1:
        scope_ref = expected_checks.scope_identity(
            scope.get("consumer"),
            scope.get("profile_ref"),
            scope.get("profile_source"),
            scope["event_ref"],
            scope["source_ref"],
            scope.get("selection_rule_ref"),
        )
    else:
        scope_ref = expected_checks.scope_identity(
            scope.get("consumer"),
            scope.get("profile_ref"),
            scope.get("profile_source"),
            scope["event_ref"],
            scope["source_ref"],
            scope.get("selection_rule_ref"),
            scope.get("hygiene_rule_ref"),
            scope.get("sources_rule_ref"),
            profile_sha256=profile.get("profile_sha256"),
            evaluated_at=profile.get("evaluated_at"),
        )
    if scope_ref != _sha256_ref(scope.get("scope_id"), "expected_scope_id_invalid"):
        raise ReconciliationError("expected_scope_id_mismatch")
    return checks


def _normalize_routing(routing, expected_refs):
    if isinstance(routing, dict) or not isinstance(routing, (list, tuple)):
        raise ReconciliationError("routing_invalid")
    bindings = {}
    for item in routing:
        if not isinstance(item, dict):
            raise ReconciliationError("routing_invalid")
        ref = _sha256_ref(
            item.get("expected_check_ref"), "routing_expected_ref_invalid"
        )
        if ref not in expected_refs:
            raise ReconciliationError("routing_expected_unknown")
        if ref in bindings:
            raise ReconciliationError("routing_duplicate")
        status = item.get("mapping_status")
        if status not in ROUTING_STATUSES:
            raise ReconciliationError("routing_status_invalid")
        jobs = item.get("job_refs", [])
        if not isinstance(jobs, (list, tuple)):
            raise ReconciliationError("routing_jobs_invalid")
        job_refs = []
        for job in jobs:
            job_ref = _text(job, "routing_job_invalid")
            if job_ref in job_refs:
                raise ReconciliationError("routing_job_duplicate")
            job_refs.append(job_ref)
        if status == "routed" and not job_refs:
            raise ReconciliationError("routing_jobs_missing")
        for key in ("scheduler_ref", "effective_config_ref"):
            value = item.get(key)
            if value is not None and not isinstance(value, dict):
                raise ReconciliationError("routing_reference_invalid")
        reason = item.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise ReconciliationError("routing_reason_invalid")
        bindings[ref] = {
            "job_refs": job_refs,
            "mapping_status": status,
            "scheduler_ref": item.get("scheduler_ref"),
            "effective_config_ref": item.get("effective_config_ref"),
            "reason": reason,
        }
    return bindings


def _normalize_resolutions(resolutions, expected_refs):
    if isinstance(resolutions, dict) or not isinstance(resolutions, (list, tuple)):
        raise ReconciliationError("resolution_invalid")
    normalized = {}
    for item in resolutions:
        if not isinstance(item, dict):
            raise ReconciliationError("resolution_invalid")
        ref = _sha256_ref(
            item.get("expected_check_ref"), "resolution_expected_ref_invalid"
        )
        if ref not in expected_refs:
            raise ReconciliationError("resolution_expected_unknown")
        if ref in normalized:
            raise ReconciliationError("resolution_duplicate")
        for key in ("rule_source_ref", "source_receipt_ref", "baseline_source_ref"):
            if item.get(key) is not None:
                _evidence_ref(item[key], f"resolution_{key}_invalid")
        normalized[ref] = dict(item)
    return normalized


def _normalize_observations(observations):
    if isinstance(observations, dict) or not isinstance(observations, (list, tuple)):
        raise ReconciliationError("observation_invalid")
    normalized = []
    seen_jobs = set()
    occurrences = {}
    for item in observations:
        if not isinstance(item, dict):
            raise ReconciliationError("observation_invalid")
        job_ref = _text(item.get("job_ref"), "observation_job_invalid")
        if job_ref in seen_jobs:
            raise ReconciliationError("observation_job_duplicate")
        seen_jobs.add(job_ref)
        runner_ref = item.get("runner_ref")
        if runner_ref is not None:
            runner_ref = _text(runner_ref, "observation_runner_invalid")
        report_ref = item.get("report_ref")
        if report_ref is not None and not isinstance(report_ref, dict):
            raise ReconciliationError("observation_report_invalid")
        results = item.get("native_results")
        if not isinstance(results, (list, tuple)):
            raise ReconciliationError("observation_results_invalid")
        job_results = []
        job_seen = set()
        for result in results:
            try:
                check_contract.validate_result(result)
            except check_contract.CheckContractError as error:
                raise ReconciliationError(
                    "native_result_invalid", native_code=error.code
                ) from None
            result_id = result["result_id"]
            if result_id in job_seen:
                raise ReconciliationError("observation_result_duplicate")
            job_seen.add(result_id)
            occurrences.setdefault(result_id, []).append(job_ref)
            job_results.append(result)
        normalized.append(
            {
                "job_ref": job_ref,
                "runner_ref": runner_ref,
                "report_ref": report_ref,
                "native_results": job_results,
            }
        )
    duplicates = [
        {"result_id": result_id, "job_refs": list(jobs)}
        for result_id, jobs in sorted(occurrences.items())
        if len(jobs) > 1
    ]
    return normalized, duplicates


def _unknown_fields(reason):
    mapping = {
        "resolution_missing": ["rule_ref", "source_ref"],
        "resolution_evidence_missing": ["rule_source_ref", "source_receipt_ref"],
        "expected_rule_unproven": ["rule_ref"],
        "expected_source_unproven": ["source_ref"],
        "expected_source_scope_insufficient": ["source_ref"],
        "expected_baseline_missing": ["baseline_ref"],
        "expected_baseline_unresolved": ["baseline_ref"],
        "expected_baseline_rule_unresolved": ["rule_ref"],
        "expected_post_completion_phase_required": ["phase"],
        "expected_phase_invalid": ["phase"],
        "applicability_unknown": ["applicability"],
        "routing_unknown": ["routing"],
        "ambiguous_native_candidate_groups": ["candidate_result_group"],
        "check_definition_unresolved": ["check_id", "definition_version"],
    }
    return mapping.get(reason, ["expected_constraints"])


def _resolve_expected(entry, resolution):
    """Build the strict #52 Expected instance from separately sourced evidence.

    Returns ``(strict_expected, status, reason, evidence_refs)``. ``status`` is
    ``unknown`` when authoritative evidence is absent and ``insufficient`` when
    the resolved identity conflicts with the immutable Expected instance.
    """
    if resolution is None:
        return None, "unknown", "resolution_missing", {}
    evidence = {}
    for key in ("rule_source_ref", "source_receipt_ref"):
        if resolution.get(key) is None:
            return None, "unknown", "resolution_evidence_missing", evidence
        evidence[key] = resolution[key]

    entry_rule = entry.get("rule_ref")
    resolved_rule = resolution.get("rule_ref")
    if resolved_rule is None:
        # The immutable Expected instance may already declare the native rule.
        resolved_rule = entry_rule
    elif entry_rule is not None and entry_rule != resolved_rule:
        return None, "insufficient", "expected_rule_mismatch", evidence

    resolved_baseline = resolution.get("baseline_ref")
    if resolved_baseline is not None:
        if resolution.get("baseline_source_ref") is None:
            return None, "unknown", "resolution_evidence_missing", evidence
        evidence["baseline_source_ref"] = resolution["baseline_source_ref"]

    expected_source = entry["source_ref"]
    resolved_source = resolution.get("source_ref")
    if not isinstance(resolved_source, dict):
        return None, "unknown", "expected_source_unproven", evidence
    if expected_source.get("snapshot") != resolved_source.get("snapshot"):
        return None, "insufficient", "expected_source_snapshot_mismatch", evidence
    for key in ("declared_target_tree", "content_digest", "head_commit", "index_tree"):
        if expected_source.get(key) is not None and expected_source.get(
            key
        ) != resolved_source.get(key):
            return None, "insufficient", f"expected_source_{key}_mismatch", evidence
    if set(expected_source.get("verified_input_scope") or ()) - set(
        resolved_source.get("verified_input_scope") or ()
    ):
        return None, "insufficient", "expected_input_scope_uncovered", evidence

    expected_baseline = entry.get("baseline_ref")
    if isinstance(expected_baseline, dict):
        base_commit = expected_baseline.get("base_commit")
        if base_commit is not None:
            if resolved_baseline is None:
                # The immutable Expected baseline stays authoritative when the
                # resolution supplies no competing comparison envelope.
                resolved_baseline = {"base_commit": base_commit}
            elif (
                not isinstance(resolved_baseline, dict)
                or resolved_baseline.get("base_commit") != base_commit
            ):
                return None, "insufficient", "expected_baseline_mismatch", evidence
        elif expected_baseline.get("baseline_status") in {
            "unknown",
            "old_object_unavailable",
            "policy",
        } or expected_baseline.get("raw_before") in (None, ""):
            return None, "unknown", "expected_baseline_unresolved", evidence

    strict = {
        "check_id": entry["check_id"],
        "definition_version": entry["definition_version"],
        "rule_ref": resolved_rule,
        "source_ref": resolved_source,
    }
    if resolved_baseline is not None:
        strict["baseline_ref"] = resolved_baseline
    for key in ("input_refs", "coverage", "coverage_by_requirement", "phase"):
        if resolution.get(key) is not None:
            strict[key] = resolution[key]
    if entry.get("comparison"):
        strict["comparison"] = entry["comparison"]
    try:
        check_contract.validate_expected_instance(strict)
    except check_contract.CheckContractError as error:
        return None, "unknown", error.code, evidence
    return strict, "matched", None, evidence


def _result_group_key(result):
    check = result["check"]
    return _canonical(
        {
            "check_id": check["check_id"],
            "definition_version": check["definition_version"],
            "rule_ref": check.get("rule_ref"),
            "profile_ref": check.get("profile_ref"),
            "source_ref": result["source_ref"],
            "baseline_ref": result.get("baseline_ref"),
            "phase": result.get("phase"),
        }
    )


def _evaluate_candidates(strict_expected, candidates):
    """Call #52 exactly once for the single coherent candidate group.

    Multiple coherent groups mean the routing evidence did not qualify one
    Run/Attempt/instance identity. This layer refuses to guess between them;
    #52's witness selection is never repeated to manufacture a satisfying set.
    """
    groups = {}
    for result in candidates:
        groups.setdefault(_result_group_key(result), []).append(result)
    if len(groups) > 1:
        return (
            None,
            [
                {
                    "result_ids": [item["result_id"] for item in group],
                    "status": "ambiguous",
                    "reason": "candidate_group_ambiguous",
                    "selected_result_ids": [],
                }
                for _key, group in sorted(groups.items())
            ],
        )
    group = next(iter(groups.values()))
    try:
        decision = check_contract.evaluate_expected_satisfaction(group, strict_expected)
    except check_contract.CheckContractError as error:
        return (
            None,
            [
                {
                    "result_ids": [item["result_id"] for item in group],
                    "status": "error",
                    "reason": error.code,
                    "selected_result_ids": [],
                }
            ],
        )
    summary = {
        "result_ids": list(decision.get("result_ids") or ()),
        "status": decision["status"],
        "reason": decision["reason"],
        "selected_result_ids": list(decision.get("selected_result_ids") or ()),
    }
    return (
        (decision, [summary])
        if decision["status"] == "satisfied"
        else (None, [summary])
    )


def _native_status(candidates):
    statuses = {item["status"] for item in candidates}
    for value in ("passed", "failed", "not_executed", "not_applicable"):
        if value in statuses:
            return value
    return "unknown" if statuses else None


def _result_evidence(result):
    """Compact observed/native evidence reference; never a second Result copy."""
    return {
        "result_id": result["result_id"],
        "result_instance": result.get("result_instance"),
        "status": result["status"],
        "result_origin": result["result_origin"],
        "native_exit_code": result.get("native_exit_code"),
        "tool": result.get("tool"),
    }


def _routing_entry(entry, binding):
    if binding is None or binding["mapping_status"] == "unrouted":
        return "unrouted", "routing_unrouted", [], binding
    if binding["mapping_status"] in {"filtered", "not_configured", "skipped"}:
        return (
            "missing",
            f"job_{binding['mapping_status']}",
            list(binding["job_refs"]),
            binding,
        )
    if binding["mapping_status"] == "unknown":
        return (
            "unknown",
            binding.get("reason") or "routing_unknown",
            list(binding["job_refs"]),
            binding,
        )
    return None, None, list(binding["job_refs"]), binding


def _candidate_results(entry, job_refs, results_by_job):
    candidates = []
    version_mismatch = []
    seen = set()
    for job_ref in job_refs:
        for result in results_by_job.get(job_ref, ()):
            check = result["check"]
            if check["check_id"] != entry["check_id"]:
                continue
            if check["definition_version"] != entry["definition_version"]:
                version_mismatch.append(result)
                continue
            if result["result_id"] in seen:
                continue
            seen.add(result["result_id"])
            candidates.append(result)
    return candidates, version_mismatch


def _unsatisfied_status(reasons):
    if any(
        reason in {"baseline_rule_unresolved", "rule_condition_unresolved"}
        for reason in reasons
    ):
        return "unknown"
    return "insufficient"


def _reconcile_entry(entry, binding, resolution, results_by_job):
    output = {
        "expected_check_ref": entry["expected_check_ref"],
        "name": entry.get("name"),
        "check_id": entry.get("check_id"),
        "definition_version": entry.get("definition_version"),
        "applicability": entry["applicability"],
        "job_refs": [],
        "mapping_status": "unrouted",
        "scheduler_ref": None,
        "effective_config_ref": None,
        "routing_reason": None,
        "binding_status": "unknown",
        "reason": None,
        "observed_result_ids": [],
        "observed_results": [],
        "selected_result_ids": [],
        "selected_results": [],
        "native_status": None,
        "resolved_expected": None,
        "resolved_expected_ref": None,
        "evidence_refs": {},
        "native_satisfaction": None,
        "candidate_decisions": [],
        "unknown_fields": [],
    }
    applicability = entry["applicability"]
    application_status = applicability.get("status")
    if application_status in _NOT_APPLICABLE_STATUSES:
        output.update(
            binding_status="not_applicable",
            reason=applicability.get("reason") or "not_applicable",
        )
        return output
    if entry.get("definition_status") != "resolved" or entry.get("check_id") is None:
        output.update(
            binding_status="unknown",
            reason="check_definition_unresolved",
            unknown_fields=_unknown_fields("check_definition_unresolved"),
        )
        return output
    if application_status not in _RECONCILABLE_APPLICABILITY:
        output.update(
            binding_status="unknown",
            reason="applicability_unknown",
            unknown_fields=_unknown_fields("applicability_unknown"),
        )
        return output
    if binding is not None:
        output.update(
            scheduler_ref=binding.get("scheduler_ref"),
            effective_config_ref=binding.get("effective_config_ref"),
            routing_reason=binding.get("reason"),
        )
    status, reason, job_refs, binding = _routing_entry(entry, binding)
    output["job_refs"] = job_refs
    output["mapping_status"] = (
        binding["mapping_status"] if binding is not None else "unrouted"
    )
    candidates, version_mismatch = _candidate_results(entry, job_refs, results_by_job)
    observed = [*candidates, *version_mismatch]
    output["observed_result_ids"] = [item["result_id"] for item in observed]
    output["observed_results"] = [_result_evidence(item) for item in observed]
    output["native_status"] = _native_status(observed)
    if status is not None:
        output.update(
            binding_status=status,
            reason=reason,
            unknown_fields=_unknown_fields(reason) if status == "unknown" else [],
        )
        return output

    if not candidates:
        if version_mismatch:
            output.update(
                binding_status="insufficient",
                reason="definition_version_mismatch",
            )
        else:
            output.update(binding_status="missing", reason="native_result_missing")
        return output

    strict, resolved_status, resolution_reason, evidence = _resolve_expected(
        entry, resolution
    )
    output["evidence_refs"] = evidence
    if strict is None:
        output.update(
            binding_status=resolved_status,
            reason=resolution_reason,
            unknown_fields=(
                _unknown_fields(resolution_reason)
                if resolved_status == "unknown"
                else []
            ),
        )
        return output
    output["resolved_expected"] = strict
    output["resolved_expected_ref"] = _digest(strict)

    decision, decisions = _evaluate_candidates(strict, candidates)
    output["candidate_decisions"] = decisions
    if decision is not None:
        selected_ids = list(decision.get("selected_result_ids") or ())
        output.update(
            binding_status="matched",
            reason="native_witness_satisfied",
            selected_result_ids=selected_ids,
            selected_results=[
                _result_evidence(item)
                for item in candidates
                if item["result_id"] in selected_ids
            ],
            native_satisfaction=decision,
        )
        return output
    if any(item["status"] == "ambiguous" for item in decisions):
        output.update(
            binding_status="unknown",
            reason="ambiguous_native_candidate_groups",
            unknown_fields=_unknown_fields("ambiguous_native_candidate_groups"),
        )
        return output
    reasons = [item["reason"] for item in decisions]
    status = _unsatisfied_status(reasons)
    output.update(
        binding_status=status,
        reason=decisions[0]["reason"] if decisions else "native_result_missing",
        unknown_fields=(
            _unknown_fields(decisions[0]["reason"]) if status == "unknown" else []
        ),
    )
    return output


def _unexpected_observations(observations, routing, reconciled_by_ref):
    job_expected = {}
    for ref, binding in routing.items():
        for job_ref in binding["job_refs"]:
            job_expected.setdefault(job_ref, set()).add(ref)
    unexpected = []
    for observation in observations:
        job_ref = observation["job_ref"]
        refs = job_expected.get(job_ref)
        for result in observation["native_results"]:
            check_id = result["check"]["check_id"]
            if not refs:
                reason = "job_not_routed"
            else:
                expected_for_job = [
                    reconciled_by_ref[ref] for ref in refs if ref in reconciled_by_ref
                ]
                if any(item["check_id"] == check_id for item in expected_for_job):
                    if all(
                        item["binding_status"] == "not_applicable"
                        for item in expected_for_job
                        if item["check_id"] == check_id
                    ):
                        reason = "expected_not_applicable"
                    else:
                        continue
                else:
                    reason = "result_check_not_expected"
            unexpected.append(
                {
                    "result_id": result["result_id"],
                    "job_ref": job_ref,
                    "check_id": check_id,
                    "reason": reason,
                }
            )
    return unexpected


def _receipt_status(expected_status, entries, unexpected):
    eligible = [item for item in entries if item["binding_status"] != "not_applicable"]
    if not entries:
        if expected_status == "complete" and not unexpected:
            return "matched"
        return "partial" if unexpected else "unknown"
    if not eligible:
        return (
            "matched" if expected_status == "complete" and not unexpected else "partial"
        )
    statuses = {item["binding_status"] for item in eligible}
    if expected_status == "complete" and statuses == {"matched"} and not unexpected:
        return "matched"
    if statuses == {"unrouted"}:
        return "unrouted"
    if statuses == {"unknown"}:
        return "unknown"
    return "partial"


def reconcile_expected_results(
    expected,
    *,
    ref_scope_id=None,
    routing=(),
    resolutions=(),
    observations=(),
):
    """Reconcile one immutable Expected scope with routed Jobs and results.

    ``expected`` is the #66-B projection (or, for a multi-ref Schema v2
    projection, select one child scope with ``ref_scope_id``). ``routing``
    carries scheduler evidence, ``resolutions`` carries separately sourced
    rule/source/baseline evidence, and ``observations`` carries genuine #52
    Native Results grouped by Job reference.

    The returned receipt is additive and versioned. It never rewrites the
    Expected projection and does not claim trusted evidence or Required status.
    """
    scope = _select_scope(expected, ref_scope_id)
    checks = _verify_scope(scope)
    expected_refs = {item["expected_check_ref"] for item in checks}
    bindings = _normalize_routing(routing, expected_refs)
    resolutions_by_ref = _normalize_resolutions(resolutions, expected_refs)
    normalized_observations, duplicates = _normalize_observations(observations)
    results_by_job = {
        item["job_ref"]: item["native_results"] for item in normalized_observations
    }
    entries = [
        _reconcile_entry(
            entry,
            bindings.get(entry["expected_check_ref"]),
            resolutions_by_ref.get(entry["expected_check_ref"]),
            results_by_job,
        )
        for entry in checks
    ]
    entries_by_ref = {item["expected_check_ref"]: item for item in entries}
    unexpected = _unexpected_observations(
        normalized_observations, bindings, entries_by_ref
    )
    summary = {
        "expected": len(entries),
        "matched": sum(item["binding_status"] == "matched" for item in entries),
        "missing": sum(item["binding_status"] == "missing" for item in entries),
        "insufficient": sum(
            item["binding_status"] == "insufficient" for item in entries
        ),
        "unknown": sum(item["binding_status"] == "unknown" for item in entries),
        "unrouted": sum(item["binding_status"] == "unrouted" for item in entries),
        "not_applicable": sum(
            item["binding_status"] == "not_applicable" for item in entries
        ),
        "unexpected": len(unexpected),
        "duplicate": len(duplicates),
        "selected_results": len(
            {result_id for item in entries for result_id in item["selected_result_ids"]}
        ),
    }
    limitations = [
        "trusted_evidence_not_admitted",
        "required_gate_not_evaluated",
        "expected_completeness_not_upgraded",
        "expected_projection_authority_not_reverified",
        "native_witness_selected_once_by_check_contract",
    ]
    payload = {
        "kind": RECONCILIATION_KIND,
        "schema_version": RECONCILIATION_VERSION,
        "expected_set_ref": scope.get("expected_set_ref"),
        "scope_id": scope.get("scope_id"),
        "expected_schema_version": scope["schema_version"],
        "expected_status": scope["status"],
        "expected_reason": scope.get("reason"),
        "expected_completeness": scope.get("consumer_completeness"),
        "expected_event_ref": scope["event_ref"],
        "expected_source_ref": scope["source_ref"],
        "ref": scope.get("ref") or None,
        "status": _receipt_status(scope["status"], entries, unexpected),
        "entries": entries,
        "unexpected": unexpected,
        "duplicate": duplicates,
        "summary": summary,
        "trusted": False,
        "required": False,
        "evidence_admission": "not_evaluated",
        "required_gate": "not_evaluated",
        "limitations": limitations,
    }
    payload["receipt_ref"] = _digest(payload)
    return payload


def validate_receipt(receipt):
    """Validate the receipt shape and recompute its immutable reference."""
    if not isinstance(receipt, dict):
        raise ReconciliationError("receipt_invalid")
    if receipt.get("kind") != RECONCILIATION_KIND:
        raise ReconciliationError("receipt_kind_invalid")
    if receipt.get("schema_version") != RECONCILIATION_VERSION:
        raise ReconciliationError("receipt_schema_unsupported")
    _sha256_ref(receipt.get("expected_set_ref"), "receipt_expected_set_ref_invalid")
    _sha256_ref(receipt.get("scope_id"), "receipt_scope_id_invalid")
    if receipt.get("status") not in RECEIPT_STATUSES:
        raise ReconciliationError("receipt_status_invalid")
    if receipt.get("trusted") is not False or receipt.get("required") is not False:
        raise ReconciliationError("receipt_authority_claimed")
    if receipt.get("evidence_admission") != "not_evaluated":
        raise ReconciliationError("receipt_authority_claimed")
    if receipt.get("required_gate") != "not_evaluated":
        raise ReconciliationError("receipt_authority_claimed")
    entries = receipt.get("entries")
    if not isinstance(entries, list):
        raise ReconciliationError("receipt_entries_invalid")
    for entry in entries:
        if not isinstance(entry, dict):
            raise ReconciliationError("receipt_entries_invalid")
        _sha256_ref(
            entry.get("expected_check_ref"), "receipt_expected_check_ref_invalid"
        )
        if entry.get("binding_status") not in ENTRY_STATUSES:
            raise ReconciliationError("receipt_entry_status_invalid")
        observed = entry.get("observed_result_ids")
        selected = entry.get("selected_result_ids")
        if not isinstance(observed, list) or not isinstance(selected, list):
            raise ReconciliationError("receipt_result_ids_invalid")
        if any(item not in observed for item in selected):
            raise ReconciliationError("receipt_selected_not_observed")
        observed_results = entry.get("observed_results")
        selected_results = entry.get("selected_results")
        if not isinstance(observed_results, list) or not isinstance(
            selected_results, list
        ):
            raise ReconciliationError("receipt_result_evidence_invalid")
        observed_evidence_ids = {
            item.get("result_id") for item in observed_results if isinstance(item, dict)
        }
        selected_evidence_ids = {
            item.get("result_id") for item in selected_results if isinstance(item, dict)
        }
        if observed_evidence_ids != set(observed):
            raise ReconciliationError("receipt_result_evidence_mismatch")
        if selected_evidence_ids != set(selected):
            raise ReconciliationError("receipt_result_evidence_mismatch")
    for key in ("unexpected", "duplicate"):
        if not isinstance(receipt.get(key), list):
            raise ReconciliationError("receipt_observations_invalid")
    receipt_ref = receipt.get("receipt_ref")
    payload = {key: value for key, value in receipt.items() if key != "receipt_ref"}
    if _digest(payload) != _sha256_ref(receipt_ref, "receipt_ref_invalid"):
        raise ReconciliationError("receipt_ref_mismatch")
    return receipt
