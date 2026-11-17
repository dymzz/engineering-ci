"""#63 evidence admission: T1-a same-execution and T2 historical reuse.

T1-a independently admits raw execution evidence for an already reconciled
#66-C Receipt. T2 admits historically authenticated evidence against the
current Expected instances using #66-P0 pre-execution Source proofs and
per-Result input equivalence. Neither path executes checks, rewrites a
Receipt, or decides #61 Required/Release status.

T1-a caller supplies:

- the immutable #66-B Expected projection and its #66-C Receipt;
- the actual raw Execution Report and Job/Attempt receipt objects;
- the verification implementation identity embedded in the Report check;
- an explicit admission policy naming trusted issuers and approved
  Check/definition implementation digests.

T2 caller supplies:

- the current immutable #66-B Expected projection (no current Receipt);
- independently obtained #66-P0 pre-execution Source proofs;
- historical candidates with per-Result origin envelopes and
  ``verification_implementation``;
- an explicit reuse policy (approved implementations);
- current Action×Target×Phase authorization;
- integration-supplied authority verifiers for historical origin, P0
  provenance and current authorization.

The output is per ``(scope_id, expected_check_ref)``. ``origin_status`` records
whether the raw native fact was authenticated; ``obligation_status`` records
whether the current Expected obligation is satisfied. ``admitted`` is true only
when both are established and the selected #52 witness is a successful native
result. A verified native FAIL is preserved as ``verified`` +
``unsatisfied`` and is never promoted to Accepted Coverage. T2 never rebinds
historical Run/Attempt/Result identities onto the current invocation.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re

from . import check_contract, reconciliation

ADMISSION_KIND = "engineering-ci-evidence-admission"
ADMISSION_VERSION = 1
HISTORICAL_REUSE_KIND = "engineering-ci-historical-reuse-admission"
HISTORICAL_REUSE_VERSION = 1
ORIGIN_STATUSES = frozenset({"verified", "rejected", "unverified"})
OBLIGATION_STATUSES = frozenset(
    {"satisfied", "unsatisfied", "unknown", "not_applicable"}
)
ENTRY_STATUSES = frozenset(
    {"admitted", "unsatisfied", "unverified", "rejected", "not_applicable"}
)
ADMISSION_STATUSES = frozenset({"admitted", "partial", "rejected", "unknown"})
IMPLEMENTATION_KEYS = frozenset(
    {"implementation_id", "implementation_version", "implementation_sha256"}
)
POLICY_KEYS = frozenset(
    {
        "trusted_issuers",
        "approved_implementations",
        "allowed_runner_refs",
        "allowed_result_origins",
    }
)
REUSE_POLICY_KEYS = frozenset(
    {
        "permitted_check_ids",
        "approved_implementations",
    }
)
CURRENT_AUTHORIZATION_KEYS = frozenset(
    {
        "phase",
        "action",
        "target_ref",
        "candidate_digest",
        "profile_sha256",
    }
)
CLOSED_SOURCE_SCOPES = frozenset({"check_input_closure", "full_input_closure"})
HISTORICAL_CANDIDATE_KEYS = frozenset(
    {
        "historical_scope_id",
        "historical_expected_check_ref",
        "historical_event_ref",
        "result_origins",
        "results",
        "verification_implementation",
        "check_name",
        "check_id",
        "definition_version",
        "applicability",
    }
)
RESULT_ORIGIN_KEYS = frozenset(
    {
        "result_id",
        "run_ref",
        "attempt_ref",
        "runner_ref",
        "result_origin",
        "report_ref",
        "job_receipt_ref",
    }
)
NOT_YET_DUE_APPLICABILITY = frozenset({"not_yet_due"})
IMPLEMENTATION_RECORD_KEYS = frozenset(
    {
        "check_id",
        "definition_version",
        "implementation_id",
        "implementation_version",
        "implementation_sha256",
    }
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA256_REF_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_NOT_APPLICABLE_APPLICABILITY = frozenset({"not_applicable", "no_changes", "no_tests"})


class AdmissionError(ValueError):
    """A malformed or incoherent admission input."""

    def __init__(self, code, *, native_code=None):
        self.code = code
        self.native_code = native_code
        super().__init__(code)


def _text(value, code):
    if not isinstance(value, str) or not value:
        raise AdmissionError(code)
    return value


def _digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _evidence_ref(value, code):
    if not isinstance(value, dict) or set(value) - {
        "name",
        "version",
        "path",
        "sha256",
    }:
        raise AdmissionError(code)
    _text(value.get("name"), code)
    if not isinstance(value.get("version"), (str, int)) or not str(value["version"]):
        raise AdmissionError(code)
    if not isinstance(value.get("sha256"), str) or not _SHA256_RE.fullmatch(
        value["sha256"]
    ):
        raise AdmissionError(code)
    if value.get("path") is not None:
        _text(value["path"], code)
    return value


def _authority(value):
    """Require an integration-supplied verifier, not a JSON trust claim."""
    if value is None:
        return None
    if not callable(value):
        raise AdmissionError("admission_authority_verifier_required")
    return value


def _resolution_authority(value):
    if value is None:
        return None
    if not callable(value):
        raise AdmissionError("admission_resolution_authority_verifier_required")
    return value


def _policy(value):
    if not isinstance(value, dict) or set(value) - POLICY_KEYS:
        raise AdmissionError("admission_policy_invalid")
    issuers = value.get("trusted_issuers")
    if not isinstance(issuers, (list, tuple)) or not issuers:
        raise AdmissionError("admission_policy_issuers_invalid")
    for issuer in issuers:
        _evidence_ref(issuer, "admission_policy_issuers_invalid")
    implementations = value.get("approved_implementations")
    if not isinstance(implementations, (list, tuple)) or not implementations:
        raise AdmissionError("admission_policy_implementations_invalid")
    normalized_implementations = []
    for item in implementations:
        if not isinstance(item, dict) or set(item) != IMPLEMENTATION_RECORD_KEYS:
            raise AdmissionError("admission_policy_implementations_invalid")
        _text(item.get("check_id"), "admission_policy_implementations_invalid")
        version = item.get("definition_version")
        if type(version) is not int or version < 1:
            raise AdmissionError("admission_policy_implementations_invalid")
        _text(
            item.get("implementation_id"),
            "admission_policy_implementations_invalid",
        )
        implementation_version = item.get("implementation_version")
        if not isinstance(implementation_version, (str, int)) or not str(
            implementation_version
        ):
            raise AdmissionError("admission_policy_implementations_invalid")
        digest = item.get("implementation_sha256")
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise AdmissionError("admission_policy_implementations_invalid")
        normalized_implementations.append(dict(item))
    allowed_runners = value.get("allowed_runner_refs")
    if allowed_runners is not None:
        if not isinstance(allowed_runners, (list, tuple)) or any(
            not isinstance(item, str) or not item for item in allowed_runners
        ):
            raise AdmissionError("admission_policy_runners_invalid")
        allowed_runners = tuple(allowed_runners)
    allowed_origins = value.get("allowed_result_origins")
    if allowed_origins is not None:
        if not isinstance(allowed_origins, (list, tuple)) or any(
            not isinstance(item, str) or not item for item in allowed_origins
        ):
            raise AdmissionError("admission_policy_origins_invalid")
        allowed_origins = tuple(allowed_origins)
    return {
        "trusted_issuers": tuple(issuers),
        "approved_implementations": tuple(normalized_implementations),
        "allowed_runner_refs": allowed_runners,
        "allowed_result_origins": allowed_origins,
    }


def _index_reports(reports):
    if not isinstance(reports, (list, tuple)):
        raise AdmissionError("admission_reports_invalid")
    indexed = {}
    for report in reports:
        if not isinstance(report, dict):
            raise AdmissionError("admission_reports_invalid")
        digest = _digest(report)
        if digest in indexed:
            raise AdmissionError("admission_report_duplicate")
        indexed[digest] = report
    return indexed


def _index_job_receipts(receipts):
    if not isinstance(receipts, (list, tuple)):
        raise AdmissionError("admission_job_receipts_invalid")
    indexed = {}
    for receipt in receipts:
        if not isinstance(receipt, dict):
            raise AdmissionError("admission_job_receipts_invalid")
        digest = _digest(receipt)
        if digest in indexed:
            raise AdmissionError("admission_job_receipt_duplicate")
        indexed[digest] = receipt
    return indexed


def _approved_implementation(policy, check, implementation):
    for item in policy["approved_implementations"]:
        if (
            item["check_id"] == check["check_id"]
            and item["definition_version"] == check["definition_version"]
            and item["implementation_id"] == implementation["implementation_id"]
            and item["implementation_version"]
            == implementation["implementation_version"]
            and item["implementation_sha256"] == implementation["implementation_sha256"]
        ):
            return item
    return None


def _report_result(report, report_ref, result_id):
    results, rejections = reconciliation._execution_report(report, report_ref)
    for result in results:
        if result["result_id"] == result_id:
            return result, rejections.get(result_id)
    return None, rejections.get(result_id)


def _parent_check(report, result_id):
    for check in report.get("checks") or ():
        if not isinstance(check, dict):
            continue
        native = check.get("native_results")
        if native is None and isinstance(check.get("native_result"), dict):
            native = [check["native_result"]]
        if not isinstance(native, list):
            continue
        if any(
            isinstance(item, dict) and item.get("result_id") == result_id
            for item in native
        ):
            return check
    return None


def _contains_result(report, result_id):
    return _parent_check(report, result_id) is not None


def _implementation(check):
    if not isinstance(check, dict):
        raise _EvidenceFailure("unverified", "verification_implementation_missing")
    implementation = check.get("verification_implementation")
    if (
        not isinstance(implementation, dict)
        or set(implementation) != IMPLEMENTATION_KEYS
    ):
        raise _EvidenceFailure("unverified", "verification_implementation_missing")
    implementation_id = implementation.get("implementation_id")
    version = implementation.get("implementation_version")
    digest = implementation.get("implementation_sha256")
    if (
        not isinstance(implementation_id, str)
        or not implementation_id
        or not isinstance(version, (str, int))
        or not str(version)
        or not isinstance(digest, str)
        or not _SHA256_RE.fullmatch(digest)
    ):
        raise _EvidenceFailure("unverified", "verification_implementation_invalid")
    return implementation


class _EvidenceFailure(Exception):
    def __init__(self, origin_status, reason):
        self.origin_status = origin_status
        self.reason = reason


def _authenticate_evidence(
    evidence,
    *,
    reports,
    job_receipts,
    policy,
    authority,
):
    """Authenticate one raw Result without applying any-Result satisfaction."""
    if not isinstance(evidence, dict):
        raise _EvidenceFailure("unverified", "evidence_invalid")
    result_id = evidence.get("result_id")
    if not isinstance(result_id, str) or not result_id:
        raise _EvidenceFailure("unverified", "result_id_missing")
    try:
        report_ref = reconciliation._report_ref(
            evidence.get("report_ref"), "admission_report_ref_invalid"
        )
    except reconciliation.ReconciliationError:
        raise _EvidenceFailure("rejected", "report_ref_invalid") from None
    report = reports.get("sha256:" + report_ref["sha256"])
    if report is None:
        if any(_contains_result(item, result_id) for item in reports.values()):
            raise _EvidenceFailure("rejected", "report_digest_mismatch")
        raise _EvidenceFailure("unverified", "report_missing")
    try:
        result, report_rejection = _report_result(report, report_ref, result_id)
    except reconciliation.ReconciliationError as error:
        raise _EvidenceFailure("rejected", error.code) from None
    if result is None:
        raise _EvidenceFailure("unverified", "native_result_missing")
    if report_rejection is not None:
        raise _EvidenceFailure("rejected", f"report_{report_rejection}")
    check = _parent_check(report, result_id)
    implementation = _implementation(check)

    receipt_ref = evidence.get("job_receipt_ref")
    if not isinstance(receipt_ref, str) or not _SHA256_REF_RE.fullmatch(receipt_ref):
        raise _EvidenceFailure("unverified", "job_receipt_missing")
    job_receipt = job_receipts.get(receipt_ref)
    if job_receipt is None:
        if any(
            item.get("job_ref") == evidence.get("job_ref")
            and item.get("report_ref") == report_ref
            for item in job_receipts.values()
        ):
            raise _EvidenceFailure("rejected", "job_receipt_identity_mismatch")
        raise _EvidenceFailure("unverified", "job_receipt_missing")
    try:
        job_receipt = reconciliation._job_receipt(
            job_receipt,
            job_ref=evidence.get("job_ref"),
            run_ref=evidence.get("run_ref"),
            attempt_ref=evidence.get("attempt_ref"),
            runner_ref=evidence.get("runner_ref"),
            report_ref=report_ref,
        )
    except reconciliation.ReconciliationError:
        raise _EvidenceFailure("rejected", "job_receipt_invalid") from None
    if _digest(job_receipt) != receipt_ref:
        raise _EvidenceFailure("rejected", "job_receipt_digest_mismatch")
    if job_receipt.get("verification_status") != "upstream_verified":
        raise _EvidenceFailure("rejected", "job_receipt_not_verified")
    if (
        policy["allowed_runner_refs"] is not None
        and evidence.get("runner_ref") not in policy["allowed_runner_refs"]
    ):
        raise _EvidenceFailure("rejected", "runner_not_authorized")
    if (
        policy["allowed_result_origins"] is not None
        and result.get("result_origin") not in policy["allowed_result_origins"]
    ):
        raise _EvidenceFailure("rejected", "origin_not_authorized")

    issuer_ref = job_receipt.get("issuer_ref")
    if authority is None:
        issuer_status = "unverified"
        issuer_reason = "authority_verifier_missing"
    else:
        context = {
            "issuer_ref": issuer_ref,
            "report_ref": report_ref,
            "job_receipt_ref": receipt_ref,
            "job_ref": job_receipt["job_ref"],
            "run_ref": job_receipt["run_ref"],
            "attempt_ref": job_receipt["attempt_ref"],
            "runner_ref": job_receipt["runner_ref"],
            "result_id": result_id,
            "implementation": copy.deepcopy(implementation),
        }
        try:
            verified = authority(context)
        except (KeyError, RuntimeError, TypeError, ValueError):
            raise _EvidenceFailure("rejected", "authority_verifier_error") from None
        if verified is not True:
            raise _EvidenceFailure("rejected", "authority_rejected")
        if issuer_ref not in policy["trusted_issuers"]:
            raise _EvidenceFailure("rejected", "issuer_untrusted")
        issuer_status = "verified"
        issuer_reason = None
    approved = _approved_implementation(policy, result["check"], implementation)
    implementation_status = "approved" if approved is not None else "unapproved"
    reasons = []
    if issuer_reason is not None:
        reasons.append(issuer_reason)
    if implementation_status == "unapproved":
        reasons.append("verification_implementation_unapproved")
    return {
        "result_id": result_id,
        "status": result.get("status"),
        "result_origin": result.get("result_origin"),
        "native_exit_code": result.get("native_exit_code"),
        "job_ref": job_receipt["job_ref"],
        "run_ref": job_receipt["run_ref"],
        "attempt_ref": job_receipt["attempt_ref"],
        "runner_ref": job_receipt["runner_ref"],
        "report_ref": report_ref,
        "job_receipt_ref": receipt_ref,
        "implementation": implementation,
        "implementation_approved": approved is not None,
        "integrity_status": "verified",
        "issuer_status": issuer_status,
        "implementation_status": implementation_status,
        "origin_status": "verified" if issuer_status == "verified" else "unverified",
        "obligation_status": "unsatisfied"
        if implementation_status == "unapproved"
        else "unknown",
        "admitted": False,
        "selected": False,
        "reasons": reasons,
        "_raw_result": result,
    }


def _same_observed_scope(left, right):
    left_report = left.get("report_ref") or {}
    right_report = right.get("report_ref") or {}
    if left_report.get("sha256") and left_report.get("sha256") == right_report.get(
        "sha256"
    ):
        return True
    left_receipt = left.get("job_receipt_ref")
    return bool(left_receipt and left_receipt == right.get("job_receipt_ref"))


def _entry_admission_v2(
    entry,
    *,
    scope_id,
    expected_entry,
    reports,
    job_receipts,
    policy,
    authority,
    resolution,
    resolution_authority,
):
    if entry.get("binding_status") == "not_applicable":
        return {
            "scope_id": scope_id,
            "expected_check_ref": entry["expected_check_ref"],
            "name": entry.get("name"),
            "check_id": entry.get("check_id"),
            "status": "not_applicable",
            "origin_status": "unverified",
            "integrity_status": "unverified",
            "obligation_status": "not_applicable",
            "admitted": False,
            "native_outcome": None,
            "result_ids": [],
            "facts": [],
            "reasons": [],
        }
    resolution_status = "verified"
    resolution_reason = None
    if resolution is None:
        resolution_status = "unverified"
        resolution_reason = "resolution_missing"
    elif resolution_authority is None:
        resolution_status = "unverified"
        resolution_reason = "resolution_authority_verifier_missing"
    else:
        context = {
            "scope_id": scope_id,
            "expected_check_ref": entry["expected_check_ref"],
            "resolution": copy.deepcopy(resolution),
        }
        try:
            authorized = resolution_authority(context)
        except (KeyError, RuntimeError, TypeError, ValueError):
            resolution_status = "rejected"
            resolution_reason = "resolution_authority_verifier_error"
        if resolution_status != "rejected":
            if authorized is not True:
                resolution_status = "unverified"
                resolution_reason = "resolution_authority_unverified"
            else:
                recomputed, status, reason, _evidence = (
                    reconciliation._resolve_expected(expected_entry, resolution)
                )
                if recomputed is None or status != "matched":
                    resolution_status = "unverified"
                    resolution_reason = reason or "resolution_unresolved"
                elif _digest(recomputed) != entry.get("resolved_expected_ref"):
                    resolution_status = "rejected"
                    resolution_reason = "resolved_expected_derivation_mismatch"
    selected_ids = list(entry.get("selected_result_ids") or ())
    observed_ids = list(entry.get("observed_result_ids") or ())
    evidence_by_id = {}
    for item in list(entry.get("observed_results") or ()) + list(
        entry.get("selected_results") or ()
    ):
        if isinstance(item, dict) and isinstance(item.get("result_id"), str):
            evidence_by_id.setdefault(item["result_id"], item)
    ordered_ids = []
    for result_id in observed_ids + selected_ids:
        if result_id not in ordered_ids:
            ordered_ids.append(result_id)
    facts = []
    raw_by_id = {}
    resolved_expected = entry.get("resolved_expected")
    for result_id in ordered_ids:
        evidence = evidence_by_id.get(result_id)
        if evidence is None:
            facts.append(
                {
                    "result_id": result_id,
                    "integrity_status": "unverified",
                    "issuer_status": "unverified",
                    "implementation_status": "unverified",
                    "origin_status": "unverified",
                    "obligation_status": "unknown",
                    "admitted": False,
                    "selected": result_id in selected_ids,
                    "reasons": ["result_evidence_missing"],
                }
            )
            continue
        try:
            fact = _authenticate_evidence(
                evidence,
                reports=reports,
                job_receipts=job_receipts,
                policy=policy,
                authority=authority,
            )
            raw_by_id[result_id] = fact.pop("_raw_result", None)
        except _EvidenceFailure as failure:
            fact = {
                "result_id": result_id,
                "report_ref": copy.deepcopy(evidence.get("report_ref"))
                if isinstance(evidence.get("report_ref"), dict)
                else None,
                "job_ref": evidence.get("job_ref"),
                "run_ref": evidence.get("run_ref"),
                "attempt_ref": evidence.get("attempt_ref"),
                "runner_ref": evidence.get("runner_ref"),
                "integrity_status": failure.origin_status,
                "issuer_status": "unverified",
                "implementation_status": "unverified",
                "origin_status": failure.origin_status,
                "obligation_status": "unknown",
                "admitted": False,
                "selected": result_id in selected_ids,
                "reasons": [failure.reason],
            }
        fact["selected"] = result_id in selected_ids
        facts.append(fact)

    selected_facts = [fact for fact in facts if fact["selected"]]
    bad_facts = [fact for fact in facts if fact["origin_status"] != "verified"]
    contaminated = any(
        _same_observed_scope(selected, bad)
        for selected in selected_facts
        for bad in bad_facts
        if bad is not selected
    )
    selected_raw = [raw_by_id[item] for item in selected_ids if raw_by_id.get(item)]
    decision = None
    decision_reason = None
    if selected_ids and len(selected_raw) == len(selected_ids):
        try:
            decision = check_contract.evaluate_expected_satisfaction(
                selected_raw, resolved_expected
            )
        except (check_contract.CheckContractError, ValueError, KeyError, TypeError):
            decision_reason = "native_satisfaction_invalid"
    selected_decision = set((decision or {}).get("selected_result_ids") or ())
    decision_ok = (
        decision is not None
        and decision.get("status") == "satisfied"
        and selected_decision == set(selected_ids)
    )
    selected_clean = (
        bool(selected_facts)
        and resolution_status == "verified"
        and all(
            fact.get("integrity_status") == "verified"
            and fact.get("issuer_status") == "verified"
            and fact.get("implementation_status") == "approved"
            for fact in selected_facts
        )
    )
    admitted = (
        entry.get("binding_status") == "matched"
        and bool(selected_ids)
        and selected_clean
        and not contaminated
        and decision_ok
        and all(fact.get("status") == "passed" for fact in selected_facts)
    )

    if (
        any(fact.get("origin_status") == "rejected" for fact in selected_facts)
        or contaminated
        or resolution_status == "rejected"
    ):
        origin_status = "rejected"
    elif selected_facts:
        origin_status = (
            "verified"
            if all(fact.get("origin_status") == "verified" for fact in selected_facts)
            else "unverified"
        )
    elif any(fact.get("origin_status") == "verified" for fact in facts):
        origin_status = "verified"
    else:
        origin_status = "unverified"
    integrity_status = (
        "verified"
        if any(fact.get("integrity_status") == "verified" for fact in facts)
        else "unverified"
    )
    if (
        any(
            fact.get("selected")
            and fact.get("integrity_status") == "verified"
            and fact.get("status") == "failed"
            for fact in facts
        )
        or any(
            fact.get("implementation_status") == "unapproved" for fact in selected_facts
        )
        or (decision is not None and decision.get("status") == "unsatisfied")
        or entry.get("binding_status") == "insufficient"
    ):
        obligation_status = "unsatisfied"
    elif any(
        fact.get("origin_status") in {"unverified", "rejected"}
        for fact in selected_facts
    ):
        obligation_status = "unknown"
    elif admitted:
        obligation_status = "satisfied"
    else:
        obligation_status = "unknown"

    reasons = []
    if entry.get("binding_status") != "matched":
        reasons.append(f"receipt_status_{entry.get('binding_status')}")
    if not selected_ids:
        reasons.append("selected_witness_missing")
    if any(
        fact.get("integrity_status") == "verified" and fact.get("status") == "failed"
        for fact in facts
    ):
        reasons.append("native_failed")
    if contaminated:
        reasons.append("selected_witness_contaminated")
    if resolution_reason is not None:
        reasons.append(resolution_reason)
    if decision_reason is not None:
        reasons.append(decision_reason)
    if decision is not None and not decision_ok:
        reasons.append("selected_witness_mismatch")
    for fact in selected_facts:
        reasons.extend(fact.get("reasons") or ())
    if not selected_clean and selected_facts:
        if any(
            fact.get("implementation_status") == "unapproved" for fact in selected_facts
        ):
            reasons.append("verification_implementation_unapproved")
        if any(fact.get("issuer_status") == "unverified" for fact in selected_facts):
            reasons.append("authority_missing")
    reasons = list(dict.fromkeys(reasons))
    if admitted:
        status = "admitted"
    elif origin_status == "rejected":
        status = "rejected"
    elif obligation_status == "unsatisfied":
        status = "unsatisfied"
    else:
        status = "unverified"
    native_outcome = next(
        (fact.get("status") for fact in selected_facts if fact.get("status")),
        next(
            (
                fact.get("status")
                for fact in facts
                if fact.get("integrity_status") == "verified" and fact.get("status")
            ),
            None,
        ),
    )
    return {
        "scope_id": entry.get("scope_id"),
        "expected_check_ref": entry["expected_check_ref"],
        "name": entry.get("name"),
        "check_id": entry.get("check_id"),
        "status": status,
        "origin_status": origin_status,
        "integrity_status": integrity_status,
        "resolution_status": resolution_status,
        "obligation_status": obligation_status,
        "admitted": admitted,
        "native_outcome": native_outcome,
        "result_ids": selected_ids,
        "facts": facts,
        "observed_diagnostics": [fact for fact in facts if not fact.get("selected")],
        "reasons": reasons,
    }


def _coverage_entry(entry):
    facts = [fact for fact in entry["facts"] if fact.get("selected")]
    return {
        "scope_id": entry["scope_id"],
        "expected_check_ref": entry["expected_check_ref"],
        "check_name": entry.get("name"),
        "admission_status": entry["status"],
        "admitted": entry.get("admitted") is True,
        "origin_status": entry.get("origin_status"),
        "integrity_status": entry.get("integrity_status"),
        "obligation_status": entry.get("obligation_status"),
        "native_outcome": entry.get("native_outcome"),
        "result_ids": list(entry.get("result_ids") or ()),
        "report_refs": [fact.get("report_ref") for fact in facts],
        "job_receipt_refs": [fact.get("job_receipt_ref") for fact in facts],
        "reasons": list(entry.get("reasons") or ()),
    }


def admit_evidence(
    expected,
    receipt,
    *,
    reports=(),
    job_receipts=(),
    policy,
    authority=None,
    resolutions=(),
    resolution_authority=None,
    ref_scope_id=None,
):
    """Admit raw evidence for one #66-C Receipt and emit T1-a coverage.

    ``reports`` and ``job_receipts`` are the independently obtained raw objects;
    receipt compact references alone are never accepted as raw evidence.
    ``policy`` is the explicit trust/approval input. ``authority`` is the
    separately established issuer/provenance verifier; ``resolutions`` are the
    independently sourced derivation inputs and ``resolution_authority`` is
    their verifier. Without these, no production coverage is admitted.
    """
    try:
        reconciliation.validate_receipt(receipt)
    except reconciliation.ReconciliationError as error:
        raise AdmissionError(
            "admission_receipt_invalid", native_code=error.code
        ) from None
    try:
        scope = reconciliation._select_scope(expected, ref_scope_id)
        reconciliation._verify_scope(scope)
    except reconciliation.ReconciliationError as error:
        raise AdmissionError(
            "admission_expected_invalid", native_code=error.code
        ) from None
    if receipt.get("scope_id") != scope.get("scope_id") or receipt.get(
        "expected_set_ref"
    ) != scope.get("expected_set_ref"):
        raise AdmissionError("admission_receipt_scope_mismatch")
    policy = _policy(policy)
    authority = _authority(authority)
    reports = _index_reports(reports)
    job_receipts = _index_job_receipts(job_receipts)
    expected_by_ref = {item["expected_check_ref"]: item for item in scope["checks"]}
    try:
        resolutions_by_ref = reconciliation._normalize_resolutions(
            resolutions, set(expected_by_ref)
        )
    except reconciliation.ReconciliationError as error:
        raise AdmissionError(
            "admission_resolution_invalid", native_code=error.code
        ) from None
    resolution_authority = _resolution_authority(resolution_authority)
    raw_entries = receipt.get("entries")
    if not isinstance(raw_entries, list):
        raise AdmissionError("admission_receipt_invalid")
    if {item.get("expected_check_ref") for item in raw_entries} != set(expected_by_ref):
        raise AdmissionError("admission_receipt_entries_mismatch")
    entries = []
    for raw_entry in raw_entries:
        expected_entry = expected_by_ref.get(raw_entry.get("expected_check_ref"))
        if expected_entry is None:
            raise AdmissionError("admission_expected_entry_missing")
        entry = _entry_admission_v2(
            raw_entry,
            scope_id=scope.get("scope_id"),
            expected_entry=expected_entry,
            reports=reports,
            job_receipts=job_receipts,
            policy=policy,
            authority=authority,
            resolution=resolutions_by_ref.get(raw_entry.get("expected_check_ref")),
            resolution_authority=resolution_authority,
        )
        entry["scope_id"] = scope.get("scope_id")
        entry["resolved_expected_check_id"] = (
            raw_entry.get("resolved_expected") or {}
        ).get("check_id")
        entries.append(entry)

    applicable = [
        entry for entry in entries if entry["obligation_status"] != "not_applicable"
    ]
    admitted_entries = [entry for entry in entries if entry["admitted"]]
    if not applicable:
        status = "unknown"
    elif len(admitted_entries) == len(applicable):
        status = "admitted"
    elif admitted_entries:
        status = "partial"
    elif any(entry["origin_status"] == "rejected" for entry in entries):
        status = "rejected"
    else:
        status = "unknown"

    remaining_work = [
        {
            "scope_id": entry["scope_id"],
            "expected_check_ref": entry["expected_check_ref"],
            "reason": (entry["reasons"] or ["obligation_not_admitted"])[0],
            "reasons": list(entry["reasons"]),
        }
        for entry in applicable
        if not entry["admitted"]
    ]
    integrity_facts = [
        fact
        for entry in entries
        for fact in entry["facts"]
        if fact.get("integrity_status") == "verified"
    ]
    verified_facts = [
        fact
        for entry in entries
        for fact in entry["facts"]
        if fact.get("origin_status") == "verified"
    ]
    rejected_facts = [
        fact
        for entry in entries
        for fact in entry["facts"]
        if fact.get("origin_status") == "rejected"
    ]
    unverified_facts = [
        fact
        for entry in entries
        for fact in entry["facts"]
        if fact.get("origin_status") == "unverified"
    ]
    accepted = {
        f"{entry['scope_id']}/{entry['expected_check_ref']}": _coverage_entry(entry)
        for entry in entries
    }
    summary = {
        "expected": len(entries),
        "admitted": len(admitted_entries),
        "remaining": len(remaining_work),
        "verified_native_facts": len(verified_facts),
        "integrity_validated_native_facts": len(integrity_facts),
        "rejected_evidence": len(rejected_facts),
        "unverified_evidence": len(unverified_facts),
        "not_applicable": sum(
            entry["obligation_status"] == "not_applicable" for entry in entries
        ),
    }
    return {
        "kind": ADMISSION_KIND,
        "schema_version": ADMISSION_VERSION,
        "scope_id": scope.get("scope_id"),
        "expected_set_ref": scope.get("expected_set_ref"),
        "receipt_ref": receipt.get("receipt_ref"),
        "expected_status": scope.get("status"),
        "receipt_status": receipt.get("status"),
        "ref": scope.get("ref"),
        "status": status,
        "entries": entries,
        "accepted_coverage_by_instance": accepted,
        "remaining_work": remaining_work,
        "verified_native_facts": verified_facts,
        "integrity_validated_native_facts": integrity_facts,
        "rejected_evidence": rejected_facts,
        "unverified_evidence": unverified_facts,
        "summary": summary,
        "evidence_admission": "evaluated",
        "required_gate": "not_evaluated",
        "limitations": [
            "receipt_is_structural_not_self_authenticating",
            "issuer_authority_requires_external_authority_proof",
            "verification_implementation_from_report_check",
            "legally_empty_expected_is_not_trusted_complete",
            "cross_run_reuse_not_evaluated",
            "required_gate_not_evaluated",
        ],
    }


def _reuse_policy(value):
    if not isinstance(value, dict) or set(value) - REUSE_POLICY_KEYS:
        raise AdmissionError("reuse_policy_invalid")
    permitted = value.get("permitted_check_ids")
    if permitted is not None:
        if not isinstance(permitted, (list, tuple)) or not permitted:
            raise AdmissionError("reuse_policy_checks_invalid")
        if any(not isinstance(item, str) or not item for item in permitted):
            raise AdmissionError("reuse_policy_checks_invalid")
        permitted = tuple(permitted)
    implementations = value.get("approved_implementations")
    if not isinstance(implementations, (list, tuple)) or not implementations:
        raise AdmissionError("reuse_policy_implementations_invalid")
    normalized = []
    for item in implementations:
        if not isinstance(item, dict) or set(item) != IMPLEMENTATION_RECORD_KEYS:
            raise AdmissionError("reuse_policy_implementations_invalid")
        _text(item.get("check_id"), "reuse_policy_implementations_invalid")
        version = item.get("definition_version")
        if type(version) is not int or version < 1:
            raise AdmissionError("reuse_policy_implementations_invalid")
        _text(item.get("implementation_id"), "reuse_policy_implementations_invalid")
        implementation_version = item.get("implementation_version")
        if not isinstance(implementation_version, (str, int)) or not str(
            implementation_version
        ):
            raise AdmissionError("reuse_policy_implementations_invalid")
        digest = item.get("implementation_sha256")
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise AdmissionError("reuse_policy_implementations_invalid")
        normalized.append(dict(item))
    return {
        "permitted_check_ids": permitted,
        "approved_implementations": tuple(normalized),
    }


def _current_authorization(value):
    if not isinstance(value, dict) or set(value) - CURRENT_AUTHORIZATION_KEYS:
        raise AdmissionError("current_authorization_invalid")
    for key in ("phase", "action", "target_ref"):
        _text(value.get(key), "current_authorization_invalid")
    for key in ("candidate_digest", "profile_sha256"):
        field = value.get(key)
        if field is not None and (not isinstance(field, str) or not field):
            raise AdmissionError("current_authorization_invalid")
    return {
        "phase": value["phase"],
        "action": value["action"],
        "target_ref": value["target_ref"],
        "candidate_digest": value.get("candidate_digest"),
        "profile_sha256": value.get("profile_sha256"),
    }


def _callable_authority(value, code):
    if value is None:
        return None
    if not callable(value):
        raise AdmissionError(code)
    return value


def _index_pre_execution_proofs(proofs):
    if not isinstance(proofs, (list, tuple)):
        raise AdmissionError("pre_execution_proofs_invalid")
    indexed = {}
    for proof in proofs:
        if not isinstance(proof, dict):
            raise AdmissionError("pre_execution_proofs_invalid")
        if proof.get("phase") != "pre_execution":
            raise AdmissionError("pre_execution_proof_phase_invalid")
        if proof.get("execution_started") is not False:
            raise AdmissionError("pre_execution_proof_execution_started")
        expected_ref = proof.get("expected_check_ref")
        if not isinstance(expected_ref, str) or not expected_ref:
            raise AdmissionError("pre_execution_proof_ref_missing")
        key = (proof.get("scope_id"), expected_ref)
        if key in indexed:
            raise AdmissionError("pre_execution_proof_duplicate")
        indexed[key] = proof
    return indexed


def _recompute_closure_digest(proof):
    source = proof.get("source_ref") or {}
    payload = {
        "check_id": proof.get("check_id"),
        "definition_version": proof.get("definition_version"),
        "execution_context": proof.get("execution_context"),
        "scope_kind": source.get("scope_kind"),
        "snapshot": source.get("snapshot"),
        "content_digest": source.get("content_digest"),
        "verified_input_scope": source.get("verified_input_scope"),
        "declared_target_tree": source.get("declared_target_tree"),
        "rule_ref": proof.get("rule_ref"),
        "profile_ref": proof.get("profile_ref"),
        "baseline_ref": proof.get("baseline_ref"),
        "baseline_source_ref": proof.get("baseline_source_ref"),
        "environment_inputs": proof.get("environment_inputs"),
        "tool_identities": proof.get("tool_identities"),
        "prerequisite_inputs": proof.get("prerequisite_inputs"),
        "changed_paths_content_digest": proof.get("changed_paths_content_digest"),
        "overlay_subset_paths": list(proof.get("overlay_subset_paths") or ()),
    }
    return _digest(payload)


def _result_origin(value):
    if not isinstance(value, dict) or set(value) - RESULT_ORIGIN_KEYS:
        raise AdmissionError("historical_result_origin_invalid")
    for key in ("result_id", "run_ref", "attempt_ref", "result_origin"):
        _text(value.get(key), "historical_result_origin_invalid")
    if value.get("runner_ref") is not None:
        _text(value["runner_ref"], "historical_result_origin_invalid")
    report_ref = value.get("report_ref")
    if not isinstance(report_ref, dict):
        raise AdmissionError("historical_result_origin_invalid")
    try:
        report_ref = reconciliation._report_ref(
            report_ref, "historical_result_origin_invalid"
        )
    except reconciliation.ReconciliationError as error:
        raise AdmissionError(
            "historical_result_origin_invalid", native_code=error.code
        ) from None
    job_receipt_ref = value.get("job_receipt_ref")
    if job_receipt_ref is not None and (
        not isinstance(job_receipt_ref, str)
        or not _SHA256_REF_RE.fullmatch(job_receipt_ref)
    ):
        raise AdmissionError("historical_result_origin_invalid")
    return {
        "result_id": value["result_id"],
        "run_ref": value["run_ref"],
        "attempt_ref": value["attempt_ref"],
        "runner_ref": value.get("runner_ref"),
        "result_origin": value["result_origin"],
        "report_ref": report_ref,
        "job_receipt_ref": job_receipt_ref,
    }


def _verification_implementation(value):
    if not isinstance(value, dict) or set(value) != IMPLEMENTATION_KEYS:
        raise AdmissionError("historical_implementation_invalid")
    _text(value.get("implementation_id"), "historical_implementation_invalid")
    version = value.get("implementation_version")
    if not isinstance(version, (str, int)) or not str(version):
        raise AdmissionError("historical_implementation_invalid")
    digest = value.get("implementation_sha256")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise AdmissionError("historical_implementation_invalid")
    return {
        "implementation_id": value["implementation_id"],
        "implementation_version": version,
        "implementation_sha256": digest,
    }


def _historical_candidate(value):
    if not isinstance(value, dict) or set(value) - HISTORICAL_CANDIDATE_KEYS:
        raise AdmissionError("historical_candidate_invalid")
    historical_scope = _text(
        value.get("historical_scope_id"), "historical_candidate_invalid"
    )
    historical_ref = _text(
        value.get("historical_expected_check_ref"), "historical_candidate_invalid"
    )
    results = value.get("results")
    if not isinstance(results, (list, tuple)) or not results:
        raise AdmissionError("historical_candidate_results_invalid")
    validated_results = []
    for item in results:
        try:
            check_contract.validate_result(item)
        except check_contract.CheckContractError as error:
            raise AdmissionError(
                "historical_candidate_results_invalid", native_code=error.code
            ) from None
        validated_results.append(item)
    raw_origins = value.get("result_origins")
    if not isinstance(raw_origins, (list, tuple)) or not raw_origins:
        raise AdmissionError("historical_result_origins_invalid")
    origins = [_result_origin(item) for item in raw_origins]
    result_ids = [item["result_id"] for item in validated_results]
    origin_ids = [item["result_id"] for item in origins]
    if sorted(origin_ids) != sorted(result_ids):
        raise AdmissionError("historical_result_origin_membership_mismatch")
    if len(set(origin_ids)) != len(origin_ids):
        raise AdmissionError("historical_result_origin_duplicate")
    for origin, result in zip(
        sorted(origins, key=lambda item: item["result_id"]),
        sorted(validated_results, key=lambda item: item["result_id"]),
        strict=True,
    ):
        if origin["result_id"] != result["result_id"]:
            raise AdmissionError("historical_result_origin_membership_mismatch")
        if origin["result_origin"] != result.get("result_origin"):
            raise AdmissionError("historical_result_origin_mismatch")
    check_id = _text(value.get("check_id"), "historical_candidate_invalid")
    definition_version = value.get("definition_version")
    if type(definition_version) is not int or definition_version < 1:
        raise AdmissionError("historical_candidate_invalid")
    check_name = value.get("check_name")
    if check_name is not None:
        _text(check_name, "historical_candidate_invalid")
    applicability = value.get("applicability")
    if applicability is not None and (
        not isinstance(applicability, dict) or not applicability.get("status")
    ):
        raise AdmissionError("historical_candidate_applicability_invalid")
    event_ref = value.get("historical_event_ref")
    if event_ref is not None and not isinstance(event_ref, dict):
        raise AdmissionError("historical_candidate_event_invalid")
    return {
        "historical_scope_id": historical_scope,
        "historical_expected_check_ref": historical_ref,
        "historical_event_ref": copy.deepcopy(event_ref),
        "result_origins": origins,
        "results": validated_results,
        "verification_implementation": _verification_implementation(
            value.get("verification_implementation")
        ),
        "check_name": check_name,
        "check_id": check_id,
        "definition_version": definition_version,
        "applicability": copy.deepcopy(applicability),
    }


def _policy_permits_reuse(policy, expected_entry):
    check_id = expected_entry.get("check_id")
    if (
        policy["permitted_check_ids"] is not None
        and check_id not in policy["permitted_check_ids"]
    ):
        return False, "reuse_policy_check_not_permitted"
    return True, None


def _implementation_approved(policy, candidate):
    implementation = candidate["verification_implementation"]
    for item in policy["approved_implementations"]:
        if (
            item["check_id"] == candidate["check_id"]
            and item["definition_version"] == candidate["definition_version"]
            and item["implementation_id"] == implementation["implementation_id"]
            and item["implementation_version"]
            == implementation["implementation_version"]
            and item["implementation_sha256"] == implementation["implementation_sha256"]
        ):
            return True
    return False


def _verify_callable(
    authority, context, *, missing_reason, rejected_reason, error_reason
):
    if authority is None:
        return False, missing_reason
    try:
        verified = authority(context)
    except (KeyError, RuntimeError, TypeError, ValueError):
        return False, error_reason
    if verified is not True:
        return False, rejected_reason
    return True, None


def _authenticate_pre_execution_proof(
    proof,
    *,
    scope_id,
    expected_entry,
    current_authorization,
    pre_execution_authority,
):
    if proof.get("status") != "verified_candidate_inputs":
        return False, proof.get("reason") or "pre_execution_source_unverified", None
    source = proof.get("source_ref") or {}
    try:
        check_contract.validate_source_ref(source)
    except check_contract.CheckContractError:
        return False, "pre_execution_source_unverified", None
    if (
        source.get("scope_kind") not in CLOSED_SOURCE_SCOPES
        or source.get("binding_status") != "content_digest"
    ):
        return False, "pre_execution_source_unverified", None
    if proof.get("expected_check_ref") != expected_entry.get("expected_check_ref"):
        return False, "pre_execution_proof_ref_mismatch", None
    if proof.get("scope_id") not in {None, scope_id}:
        return False, "pre_execution_proof_scope_mismatch", None
    if proof.get("check_id") != expected_entry.get("check_id") or proof.get(
        "definition_version"
    ) != expected_entry.get("definition_version"):
        return False, "pre_execution_proof_check_mismatch", None
    if proof.get("rule_ref") != expected_entry.get("rule_ref"):
        return False, "pre_execution_proof_rule_mismatch", None
    recomputed = _recompute_closure_digest(proof)
    if proof.get("closure_digest") != recomputed:
        return False, "pre_execution_closure_digest_mismatch", recomputed
    ok, reason = _verify_callable(
        pre_execution_authority,
        {
            "scope_id": scope_id,
            "expected_check_ref": expected_entry.get("expected_check_ref"),
            "expected_entry": copy.deepcopy(expected_entry),
            "proof": copy.deepcopy(proof),
            "recomputed_closure_digest": recomputed,
            "current_authorization": copy.deepcopy(current_authorization),
        },
        missing_reason="pre_execution_authority_missing",
        rejected_reason="pre_execution_authority_unverified",
        error_reason="pre_execution_authority_error",
    )
    if not ok:
        return False, reason, recomputed
    return True, None, recomputed


def _equivalence_basis(pre_proof, comparisons, *, origin_authenticated):
    source = pre_proof.get("source_ref") or {}
    return {
        "verified": all(item.get("equivalent") is True for item in comparisons),
        "requires_baseline": pre_proof.get("baseline_ref") is not None,
        "closed_source_scope_kind": source.get("scope_kind"),
        "raw_native_source_digest_equal": all(
            item.get("reason") != "source_content_mismatch" for item in comparisons
        ),
        "raw_native_rule_and_profile_equal": all(
            item.get("reason") not in {"rule_mismatch", "profile_mismatch"}
            for item in comparisons
        ),
        "origin_authenticated": origin_authenticated,
        "closure_digest": pre_proof.get("closure_digest"),
        "comparison_reasons": [
            item.get("reason") for item in comparisons if item.get("reason")
        ],
    }


def _derive_strict_expected_from_pre_proof(expected_entry, pre_proof):
    """Build a temporary strict #52 Expected from immutable Expected + P0 proof.

    Reuses ``reconciliation.derive_strict_expected()`` without fabricating a
    current Receipt or dummy resolution evidence refs. The original Expected is
    not mutated. P0 ``prerequisite_inputs`` are not Expected ``input_refs``.
    """
    if not isinstance(pre_proof.get("source_ref"), dict):
        return None, "pre_execution_source_unverified"
    resolved = {
        "source_ref": copy.deepcopy(pre_proof.get("source_ref")),
        "rule_ref": copy.deepcopy(pre_proof.get("rule_ref")),
        "profile_ref": copy.deepcopy(pre_proof.get("profile_ref")),
        "baseline_ref": copy.deepcopy(pre_proof.get("baseline_ref")),
        "changed_paths_content_digest": pre_proof.get("changed_paths_content_digest"),
    }
    strict, status, reason = reconciliation.derive_strict_expected(
        expected_entry, resolved
    )
    if status != "matched" or strict is None:
        return None, reason or "strict_expected_unresolved"
    return strict, None


def _evaluate_historical_candidate(
    expected_entry,
    pre_proof,
    candidate,
    policy,
    *,
    historical_origin_authority,
    current_authorization,
):
    """Return coverage decision for one historical candidate against one Expected."""
    if candidate["check_id"] != expected_entry.get("check_id") or candidate[
        "definition_version"
    ] != expected_entry.get("definition_version"):
        return {
            "admitted": False,
            "status": "rejected",
            "origin_status": "unverified",
            "obligation_status": "unsatisfied",
            "reason": "check_identity_mismatch",
            "equivalence_basis": None,
            "comparisons": [],
        }
    if not _implementation_approved(policy, candidate):
        return {
            "admitted": False,
            "status": "rejected",
            "origin_status": "unverified",
            "obligation_status": "unsatisfied",
            "reason": "verification_implementation_unapproved",
            "equivalence_basis": None,
            "comparisons": [],
        }

    origin_ok, origin_reason = _verify_callable(
        historical_origin_authority,
        {
            "historical_scope_id": candidate["historical_scope_id"],
            "historical_expected_check_ref": candidate["historical_expected_check_ref"],
            "historical_event_ref": copy.deepcopy(
                candidate.get("historical_event_ref")
            ),
            "result_origins": copy.deepcopy(candidate["result_origins"]),
            "results": copy.deepcopy(candidate["results"]),
            "verification_implementation": copy.deepcopy(
                candidate["verification_implementation"]
            ),
            "check_id": candidate["check_id"],
            "definition_version": candidate["definition_version"],
            "historical_applicability": copy.deepcopy(candidate.get("applicability")),
            "current_authorization": copy.deepcopy(current_authorization),
        },
        missing_reason="historical_origin_authority_missing",
        rejected_reason="historical_origin_not_accepted",
        error_reason="historical_origin_authority_error",
    )
    if not origin_ok:
        return {
            "admitted": False,
            "status": "unverified",
            "origin_status": "unverified",
            "obligation_status": "unknown",
            "reason": origin_reason,
            "equivalence_basis": None,
            "comparisons": [],
        }

    from . import execution

    comparisons = [
        execution.compare_pre_execution_to_historical_result(pre_proof, result)
        for result in candidate["results"]
    ]
    basis = _equivalence_basis(pre_proof, comparisons, origin_authenticated=True)
    if any(item.get("equivalent") is not True for item in comparisons):
        reason = next(
            (
                item.get("reason")
                for item in comparisons
                if item.get("equivalent") is not True
            ),
            "input_not_equivalent",
        )
        return {
            "admitted": False,
            "status": "rejected",
            "origin_status": "verified",
            "obligation_status": "unsatisfied",
            "reason": reason or "input_not_equivalent",
            "equivalence_basis": basis,
            "comparisons": comparisons,
        }

    strict_expected, strict_reason = _derive_strict_expected_from_pre_proof(
        expected_entry, pre_proof
    )
    if strict_expected is None:
        return {
            "admitted": False,
            "status": "unverified",
            "origin_status": "verified",
            "obligation_status": "unknown",
            "reason": strict_reason or "strict_expected_unresolved",
            "equivalence_basis": basis,
            "comparisons": comparisons,
        }

    try:
        satisfaction = check_contract.evaluate_expected_satisfaction(
            list(candidate["results"]), strict_expected
        )
    except check_contract.CheckContractError as error:
        return {
            "admitted": False,
            "status": "rejected",
            "origin_status": "verified",
            "obligation_status": "unsatisfied",
            "reason": error.code,
            "equivalence_basis": basis,
            "comparisons": comparisons,
        }
    if satisfaction.get("status") != "satisfied":
        native_fail = any(
            result.get("status") == "failed" for result in candidate["results"]
        )
        return {
            "admitted": False,
            "status": "unsatisfied",
            "origin_status": "verified",
            "obligation_status": "unsatisfied",
            "reason": (
                "verified_native_fail_not_reusable_as_coverage"
                if native_fail
                else satisfaction.get("reason") or "result_not_eligible"
            ),
            "equivalence_basis": basis,
            "comparisons": comparisons,
            "satisfaction": satisfaction,
        }

    for comparison in comparisons:
        # Keep comparator trusted=False: equivalence alone is not T2 trust.
        comparison["whole_check_reuse_admitted"] = True
        comparison["check_satisfaction_evaluated"] = True
        comparison["admission_status"] = "admitted"

    return {
        "admitted": True,
        "status": "admitted",
        "origin_status": "verified",
        "obligation_status": "satisfied",
        "reason": None,
        "equivalence_basis": {
            **basis,
            "verified": True,
            "check_satisfaction_status": "satisfied",
            "selected_result_ids": list(satisfaction.get("selected_result_ids") or ()),
            "current_phase": current_authorization["phase"],
            "current_action": current_authorization["action"],
            "current_target_ref": current_authorization["target_ref"],
            "current_candidate_digest": current_authorization.get("candidate_digest"),
        },
        "comparisons": comparisons,
        "satisfaction": satisfaction,
    }


def _empty_entry(scope_id, expected_entry, *, status, obligation_status, reasons):
    return {
        "scope_id": scope_id,
        "expected_check_ref": expected_entry.get("expected_check_ref"),
        "check_name": expected_entry.get("name"),
        "check_id": expected_entry.get("check_id"),
        "status": status,
        "admitted": False,
        "origin_status": "unverified",
        "obligation_status": obligation_status,
        "historical_scope_id": None,
        "historical_expected_check_ref": None,
        "historical_event_ref": None,
        "result_origins": [],
        "equivalence_basis": None,
        "result_ids": [],
        "reasons": list(reasons),
    }


def _reuse_coverage_entry(entry):
    return {
        "scope_id": entry["scope_id"],
        "expected_check_ref": entry["expected_check_ref"],
        "check_name": entry.get("check_name"),
        "admission_status": entry["status"],
        "admitted": entry.get("admitted") is True,
        "source_kind": "historical_reuse" if entry.get("admitted") else None,
        "origin_status": entry.get("origin_status"),
        "obligation_status": entry.get("obligation_status"),
        "historical_scope_id": entry.get("historical_scope_id"),
        "historical_expected_check_ref": entry.get("historical_expected_check_ref"),
        "historical_event_ref": copy.deepcopy(entry.get("historical_event_ref")),
        "result_origins": copy.deepcopy(entry.get("result_origins") or []),
        "equivalence_basis": copy.deepcopy(entry.get("equivalence_basis")),
        "result_ids": list(entry.get("result_ids") or ()),
        "reasons": list(entry.get("reasons") or ()),
    }


def admit_historical_reuse(
    expected,
    *,
    pre_execution_proofs,
    historical_candidates,
    reuse_policy,
    current_authorization,
    historical_origin_authority=None,
    pre_execution_authority=None,
    authorization_authority=None,
    ref_scope_id=None,
):
    """Admit historically authenticated evidence for current Expected instances.

    This is the #63 T2 contract surface. It evaluates
    ``OriginAccepted ∧ InputEquivalent ∧ ResultEligible ∧ ScopeCovered ∧
    PolicyPermitsReuse`` *before* the current expensive Check runs and without
    requiring a current #66-C Receipt, Job, or Native Result.

    OriginAccepted and current Action×Target×Phase authorization require
    integration-supplied verifier callables; self-declared candidate flags and
    fabricated pre-execution proofs cannot produce admitted coverage.
    """
    try:
        scope = reconciliation._select_scope(expected, ref_scope_id)
        reconciliation._verify_scope(scope)
    except reconciliation.ReconciliationError as error:
        raise AdmissionError(
            "admission_expected_invalid", native_code=error.code
        ) from None
    policy = _reuse_policy(reuse_policy)
    authorization = _current_authorization(current_authorization)
    historical_origin_authority = _callable_authority(
        historical_origin_authority, "historical_origin_authority_verifier_required"
    )
    pre_execution_authority = _callable_authority(
        pre_execution_authority, "pre_execution_authority_verifier_required"
    )
    authorization_authority = _callable_authority(
        authorization_authority, "authorization_authority_verifier_required"
    )
    proofs = _index_pre_execution_proofs(pre_execution_proofs)
    if not isinstance(historical_candidates, (list, tuple)):
        raise AdmissionError("historical_candidates_invalid")
    candidates = [_historical_candidate(item) for item in historical_candidates]

    entries = []
    for expected_entry in scope.get("checks") or ():
        expected_ref = expected_entry.get("expected_check_ref")
        scope_id = scope.get("scope_id")
        applicability = expected_entry.get("applicability") or {}
        app_status = applicability.get("status")
        if app_status in _NOT_APPLICABLE_APPLICABILITY:
            entries.append(
                _empty_entry(
                    scope_id,
                    expected_entry,
                    status="not_applicable",
                    obligation_status="not_applicable",
                    reasons=[],
                )
            )
            continue
        if app_status in NOT_YET_DUE_APPLICABILITY:
            entries.append(
                _empty_entry(
                    scope_id,
                    expected_entry,
                    status="unverified",
                    obligation_status="unknown",
                    reasons=["current_obligation_not_yet_due"],
                )
            )
            continue

        permitted, policy_reason = _policy_permits_reuse(policy, expected_entry)
        if not permitted:
            entries.append(
                _empty_entry(
                    scope_id,
                    expected_entry,
                    status="rejected",
                    obligation_status="unsatisfied",
                    reasons=[policy_reason],
                )
            )
            continue

        auth_ok, auth_reason = _verify_callable(
            authorization_authority,
            {
                "scope_id": scope_id,
                "expected_check_ref": expected_ref,
                "expected_entry": copy.deepcopy(expected_entry),
                "current_authorization": copy.deepcopy(authorization),
            },
            missing_reason="current_authorization_authority_missing",
            rejected_reason="current_authorization_unverified",
            error_reason="current_authorization_authority_error",
        )
        if not auth_ok:
            entries.append(
                _empty_entry(
                    scope_id,
                    expected_entry,
                    status="unverified",
                    obligation_status="unknown",
                    reasons=[auth_reason],
                )
            )
            continue

        proof = proofs.get((scope_id, expected_ref))
        if proof is None:
            proof = proofs.get((None, expected_ref))
        if proof is None:
            entries.append(
                _empty_entry(
                    scope_id,
                    expected_entry,
                    status="unverified",
                    obligation_status="unknown",
                    reasons=["pre_execution_proof_missing"],
                )
            )
            continue
        proof_ok, proof_reason, _ = _authenticate_pre_execution_proof(
            proof,
            scope_id=scope_id,
            expected_entry=expected_entry,
            current_authorization=authorization,
            pre_execution_authority=pre_execution_authority,
        )
        if not proof_ok:
            entries.append(
                _empty_entry(
                    scope_id,
                    expected_entry,
                    status="unverified",
                    obligation_status="unknown",
                    reasons=[proof_reason],
                )
            )
            continue

        matching = [
            candidate
            for candidate in candidates
            if candidate["check_id"] == expected_entry.get("check_id")
            and candidate["definition_version"]
            == expected_entry.get("definition_version")
        ]
        if not matching:
            entries.append(
                _empty_entry(
                    scope_id,
                    expected_entry,
                    status="unverified",
                    obligation_status="unknown",
                    reasons=["historical_candidate_missing"],
                )
            )
            continue

        decisions = [
            (
                candidate,
                _evaluate_historical_candidate(
                    expected_entry,
                    proof,
                    candidate,
                    policy,
                    historical_origin_authority=historical_origin_authority,
                    current_authorization=authorization,
                ),
            )
            for candidate in matching
        ]
        admitted = [
            (candidate, decision)
            for candidate, decision in decisions
            if decision.get("admitted") is True
        ]
        if admitted:
            admitted.sort(
                key=lambda item: (
                    item[0]["historical_scope_id"],
                    item[0]["historical_expected_check_ref"],
                )
            )
            candidate, decision = admitted[0]
            entries.append(
                {
                    "scope_id": scope_id,
                    "expected_check_ref": expected_ref,
                    "check_name": expected_entry.get("name"),
                    "check_id": expected_entry.get("check_id"),
                    "status": "admitted",
                    "admitted": True,
                    "origin_status": "verified",
                    "obligation_status": "satisfied",
                    "historical_scope_id": candidate["historical_scope_id"],
                    "historical_expected_check_ref": candidate[
                        "historical_expected_check_ref"
                    ],
                    "historical_event_ref": copy.deepcopy(
                        candidate.get("historical_event_ref")
                    ),
                    "result_origins": copy.deepcopy(candidate["result_origins"]),
                    "equivalence_basis": copy.deepcopy(decision["equivalence_basis"]),
                    "result_ids": [
                        result["result_id"] for result in candidate["results"]
                    ],
                    "reasons": [],
                }
            )
            continue

        ranked = sorted(
            decisions,
            key=lambda item: (
                0
                if item[1]["status"] == "unsatisfied"
                else 1
                if item[1]["status"] == "rejected"
                else 2,
                item[0]["historical_scope_id"],
                item[0]["historical_expected_check_ref"],
            ),
        )
        candidate, decision = ranked[0]
        entries.append(
            {
                "scope_id": scope_id,
                "expected_check_ref": expected_ref,
                "check_name": expected_entry.get("name"),
                "check_id": expected_entry.get("check_id"),
                "status": decision["status"],
                "admitted": False,
                "origin_status": decision["origin_status"],
                "obligation_status": decision["obligation_status"],
                "historical_scope_id": candidate["historical_scope_id"],
                "historical_expected_check_ref": candidate[
                    "historical_expected_check_ref"
                ],
                "historical_event_ref": copy.deepcopy(
                    candidate.get("historical_event_ref")
                ),
                "result_origins": copy.deepcopy(candidate["result_origins"]),
                "equivalence_basis": copy.deepcopy(decision.get("equivalence_basis")),
                "result_ids": [result["result_id"] for result in candidate["results"]],
                "reasons": [decision["reason"]] if decision.get("reason") else [],
            }
        )

    applicable = [
        entry for entry in entries if entry["obligation_status"] != "not_applicable"
    ]
    admitted_entries = [entry for entry in entries if entry["admitted"]]
    if not applicable:
        status = "unknown"
    elif len(admitted_entries) == len(applicable):
        status = "admitted"
    elif admitted_entries:
        status = "partial"
    elif any(entry["status"] == "rejected" for entry in entries):
        status = "rejected"
    else:
        status = "unknown"

    remaining_work = [
        {
            "scope_id": entry["scope_id"],
            "expected_check_ref": entry["expected_check_ref"],
            "check_name": entry.get("check_name"),
            "reason": (entry["reasons"] or ["obligation_not_admitted"])[0],
            "reasons": list(entry["reasons"]),
        }
        for entry in applicable
        if not entry["admitted"]
    ]
    accepted = {
        f"{entry['scope_id']}/{entry['expected_check_ref']}": _reuse_coverage_entry(
            entry
        )
        for entry in entries
    }
    summary = {
        "expected": len(entries),
        "admitted": len(admitted_entries),
        "remaining": len(remaining_work),
        "historical_candidates": len(candidates),
        "not_applicable": sum(
            entry["obligation_status"] == "not_applicable" for entry in entries
        ),
    }
    return {
        "kind": HISTORICAL_REUSE_KIND,
        "schema_version": HISTORICAL_REUSE_VERSION,
        "scope_id": scope.get("scope_id"),
        "expected_set_ref": scope.get("expected_set_ref"),
        "receipt_ref": None,
        "expected_status": scope.get("status"),
        "ref": scope.get("ref"),
        "current_authorization": copy.deepcopy(authorization),
        "status": status,
        "entries": entries,
        "accepted_coverage_by_instance": accepted,
        "remaining_work": remaining_work,
        "summary": summary,
        "evidence_admission": "evaluated",
        "required_gate": "not_evaluated",
        "limitations": [
            "current_receipt_not_required_for_pre_execution_reuse",
            "historical_origin_requires_external_authority_proof",
            "pre_execution_proof_requires_external_authority_proof",
            "current_authorization_requires_external_authority_proof",
            "fixture_authority_verifiers_are_not_production_trust",
            "t1b_local_to_actions_handoff_not_evaluated",
            "required_gate_not_evaluated",
        ],
    }
