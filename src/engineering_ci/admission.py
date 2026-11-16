"""#63 T1-a same-execution evidence admission.

This module independently admits a limited set of raw execution evidence for an
already reconciled #66-C Receipt. It does not execute checks, enumerate
Expected Checks, rewrite the Receipt, or decide #61 Required/Release status.

The caller supplies:

- the immutable #66-B Expected projection and its #66-C Receipt;
- the actual raw Execution Report and Job/Attempt receipt objects;
- the verification implementation identity embedded in the Report check;
- an explicit admission policy naming trusted issuers and approved
  Check/definition implementation digests.

The output is per ``(scope_id, expected_check_ref)``. ``origin_status`` records
whether the raw native fact was authenticated; ``obligation_status`` records
whether the current Expected obligation is satisfied. ``admitted`` is true only
when both are established and the selected #52 witness is a successful native
result. A verified native FAIL is preserved as ``verified`` +
``unsatisfied`` and is never promoted to Accepted Coverage.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re

from . import check_contract, reconciliation

ADMISSION_KIND = "engineering-ci-evidence-admission"
ADMISSION_VERSION = 1
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


def _entry_admission_v2(entry, *, reports, job_receipts, policy, authority):
    if entry.get("binding_status") == "not_applicable":
        return {
            "scope_id": entry.get("scope_id"),
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
    selected_clean = bool(selected_facts) and all(
        fact.get("integrity_status") == "verified"
        and fact.get("issuer_status") == "verified"
        and fact.get("implementation_status") == "approved"
        for fact in selected_facts
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
    ref_scope_id=None,
):
    """Admit raw evidence for one #66-C Receipt and emit T1-a coverage.

    ``reports`` and ``job_receipts`` are the independently obtained raw objects;
    receipt compact references alone are never accepted as raw evidence.
    ``policy`` is the explicit trust/approval input. ``authority`` is the
    separately established issuer/provenance proof; without it, no production
    coverage is admitted. This function does not execute or re-run a Check.
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
            reports=reports,
            job_receipts=job_receipts,
            policy=policy,
            authority=authority,
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
