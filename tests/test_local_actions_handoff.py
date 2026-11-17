"""#63 T1-b local → Actions handoff contract tests."""

from __future__ import annotations

import copy

import pytest

from engineering_ci import local_actions_handoff as handoff

ISSUER = {"name": "local-hook-issuer", "version": "1", "sha256": "a" * 64}
OTHER_ISSUER = {"name": "other-issuer", "version": "1", "sha256": "b" * 64}
COLLECTOR = {"name": "controlled-collector", "version": "1", "sha256": "c" * 64}
PRODUCER = {"name": "org/local-cli", "version": "1", "sha256": "d" * 64}
AUTHORITY = {"name": "org/local-producer-grant", "version": "1", "sha256": "e" * 64}
TRANSPORT = {"name": "actions-artifact", "version": "1", "sha256": "f" * 64}
PROOF = {"name": "execution-attestation", "version": "1", "sha256": "1" * 64}
PRODUCED_AT = "2026-11-17T04:00:00+00:00"
EVALUATED_AT = "2026-11-17T05:00:00+00:00"


def payload(**changes):
    value = {
        "reports": [{"kind": "engineering-ci-execution-report", "sha256": "2" * 64}],
        "job_receipts": [{"kind": "engineering-ci-job-receipt", "sha256": "3" * 64}],
        "result_ids": ["sha256:" + "4" * 64],
        "source_digests": ["5" * 64],
        "invocation_ref": "inv/local-1",
        "event_payload_ref": "evt/local-1",
    }
    value.update(changes)
    return value


def execution_boundary(**changes):
    value = {
        "kind": "controlled_local_environment",
        "collector_ref": copy.deepcopy(COLLECTOR),
        "controlled": True,
        "self_reported": False,
        "proof_ref": copy.deepcopy(PROOF),
    }
    value.update(changes)
    return value


def producer_authorization(**changes):
    value = {
        "authority_ref": copy.deepcopy(AUTHORITY),
        "producer_ref": copy.deepcopy(PRODUCER),
        "scope": {
            "action": "validate",
            "target_ref": "refs/heads/main",
            "phase": "pre_publish",
            "check_ids": ["eci/hygiene/tach"],
        },
        "valid_from": "2026-11-01T00:00:00+00:00",
        "valid_until": "2026-12-01T00:00:00+00:00",
        "revoked_at": None,
    }
    value.update(changes)
    return value


def consumer_scope(**changes):
    value = {
        "action": "validate",
        "target_ref": "refs/heads/main",
        "phase": "pre_publish",
        "check_ids": ["eci/hygiene/tach"],
        "event_ref": "evt/actions-1",
        "scope_id": "scope/main-pre",
    }
    value.update(changes)
    return value


def handoff_policy(**changes):
    value = {
        "allowed_issuers": [copy.deepcopy(ISSUER)],
        "allowed_execution_boundaries": [
            "controlled_local_environment",
            "controlled_actions_producer",
        ],
    }
    value.update(changes)
    return value


def accept_issuer(_package):
    return True


def reject_issuer(_package):
    return False


def accept_execution(package, *, executed_at=None):
    return {
        "accepted": True,
        "executed_at": executed_at or package["produced_at"],
    }


def accept_execution_bare_true(_package):
    return True


def reject_execution(_package):
    return False


def accept_authorization(_context):
    return True


def reject_authorization(_context):
    return False


def build_package(**changes):
    kwargs = {
        "payload": payload(),
        "issuer_ref": ISSUER,
        "execution_boundary": execution_boundary(),
        "producer_authorization": producer_authorization(),
        "transport_ref": TRANSPORT,
        "produced_at": PRODUCED_AT,
        "declared_trusted": False,
    }
    kwargs.update(changes)
    return handoff.build_local_handoff_package(**kwargs)


def verify(package, **changes):
    kwargs = {
        "handoff_policy": handoff_policy(),
        "consumer_scope": consumer_scope(),
        "issuer_authority": accept_issuer,
        "execution_authority": accept_execution,
        "authorization_authority": accept_authorization,
        "evaluated_at": EVALUATED_AT,
    }
    kwargs.update(changes)
    return handoff.verify_local_handoff(package, **kwargs)


def test_controlled_local_handoff_verifies_and_exposes_t1a_materials():
    package = build_package()
    assert package["status"] == "candidate"
    assert package["declared_trusted"] is False
    assert handoff.validate_local_handoff_package(package) is package

    verified = verify(package)
    assert verified["status"] == "verified"
    assert verified["provenance_status"] == "authenticated"
    assert verified["consumer_coverage"] == "complete"
    assert verified["admitted_for_t1a"] is True
    assert verified["executed_at"] == PRODUCED_AT
    assert verified["authorized_check_ids"] == ["eci/hygiene/tach"]
    assert verified["remaining_work"] == []
    assert verified["materials"]["result_ids"] == ["sha256:" + "4" * 64]
    assert verified["materials"]["authorized_check_ids"] == ["eci/hygiene/tach"]
    assert verified["payload_digest"] == package["payload"]["payload_digest"]
    assert verified["consumer_scope"]["target_ref"] == "refs/heads/main"


def test_declared_trusted_local_pass_is_not_authority():
    package = build_package(declared_trusted=True)
    assert "local_declared_trusted_ignored" in package["reasons"]

    verified = verify(package)
    assert verified["status"] == "verified"
    assert "local_declared_trusted_ignored" in verified["reasons"]


def test_uncontrolled_or_self_reported_execution_is_rejected():
    uncontrolled = build_package(
        execution_boundary=execution_boundary(
            kind="uncontrolled_developer_workstation",
            controlled=False,
            self_reported=True,
            proof_ref=None,
        )
    )
    verified = verify(
        uncontrolled,
        handoff_policy=handoff_policy(
            allowed_execution_boundaries=["uncontrolled_developer_workstation"]
        ),
    )
    assert verified["status"] == "rejected"
    assert verified["admitted_for_t1a"] is False
    assert verified["materials"] is None
    assert "uncontrolled_local_execution" in verified["reasons"]
    assert "self_reported_execution_evidence" in verified["reasons"]


def test_signed_identity_with_rejected_execution_authority_stays_untrusted():
    package = build_package()
    verified = verify(package, execution_authority=reject_execution)
    assert verified["status"] == "rejected"
    assert "execution_authority_rejected" in verified["reasons"]
    assert verified["materials"] is None
    assert verified["executed_at"] is None


def test_expired_or_revoked_producer_authorization_is_rejected():
    expired = build_package(
        producer_authorization=producer_authorization(
            valid_until="2026-11-16T00:00:00+00:00"
        )
    )
    revoked = build_package(
        producer_authorization=producer_authorization(
            revoked_at="2026-11-17T01:00:00+00:00"
        )
    )
    for package, reason in (
        (expired, "producer_authorization_expired"),
        (revoked, "producer_authorization_revoked_at_execution"),
    ):
        verified = verify(package)
        assert verified["status"] == "rejected"
        assert reason in verified["reasons"]
        assert verified["materials"] is None


def test_main_grant_rejected_for_different_pr_target():
    package = build_package()
    verified = verify(
        package,
        consumer_scope=consumer_scope(target_ref="refs/pull/9/merge"),
    )
    assert verified["status"] == "rejected"
    assert "producer_authorization_target_mismatch" in verified["reasons"]
    assert verified["materials"] is None
    assert verified["admitted_for_t1a"] is False


def test_mismatched_phase_or_action_is_rejected():
    package = build_package()
    phase = verify(package, consumer_scope=consumer_scope(phase="post_publish"))
    action = verify(package, consumer_scope=consumer_scope(action="release"))
    assert phase["status"] == "rejected"
    assert "producer_authorization_phase_mismatch" in phase["reasons"]
    assert phase["materials"] is None
    assert action["status"] == "rejected"
    assert "producer_authorization_action_mismatch" in action["reasons"]
    assert action["materials"] is None


def test_empty_consumer_check_set_is_rejected_without_materials():
    package = build_package()
    with pytest.raises(
        handoff.LocalActionsHandoffError, match="handoff_consumer_check_set_missing"
    ):
        verify(package, consumer_scope=consumer_scope(check_ids=[]))


def test_omitted_consumer_scope_is_rejected():
    package = build_package()
    with pytest.raises(TypeError):
        handoff.verify_local_handoff(
            package,
            handoff_policy=handoff_policy(),
            issuer_authority=accept_issuer,
            execution_authority=accept_execution,
            authorization_authority=accept_authorization,
            evaluated_at=EVALUATED_AT,
        )


def test_mixed_authorized_and_unauthorized_checks_not_ready_for_t1a():
    package = build_package(
        producer_authorization=producer_authorization(
            scope={
                "action": "validate",
                "target_ref": "refs/heads/main",
                "phase": "pre_publish",
                "check_ids": ["eci/hygiene/tach"],
            }
        )
    )
    result = verify(
        package,
        consumer_scope=consumer_scope(
            check_ids=["eci/hygiene/tach", "eci/hygiene/vulture"]
        ),
    )
    # Provenance may be authenticated, but incomplete consumer coverage is not
    # a full verified handoff and cannot feed T1-a without bound raw materials.
    assert result["status"] == "partial"
    assert result["provenance_status"] == "authenticated"
    assert result["consumer_coverage"] == "partial"
    assert result["authorized_check_ids"] == ["eci/hygiene/tach"]
    assert result["remaining_work"] == [
        {
            "check_id": "eci/hygiene/vulture",
            "reason": "producer_authorization_check_not_granted",
        }
    ]
    assert "producer_authorization_scope_partial" in result["reasons"]
    assert result["materials"] is None
    assert result["admitted_for_t1a"] is False


def test_fully_authorized_handoff_still_exposes_raw_materials_for_t1a():
    package = build_package()
    verified = verify(package)
    assert verified["status"] == "verified"
    assert verified["provenance_status"] == "authenticated"
    assert verified["consumer_coverage"] == "complete"
    assert verified["admitted_for_t1a"] is True
    assert verified["materials"]["reports"]
    assert verified["materials"]["job_receipts"]
    assert verified["remaining_work"] == []


def test_none_of_consumer_checks_granted_is_rejected():
    package = build_package()
    verified = verify(
        package,
        consumer_scope=consumer_scope(check_ids=["eci/hygiene/vulture"]),
    )
    assert verified["status"] == "rejected"
    assert "producer_authorization_checks_none_granted" in verified["reasons"]
    assert verified["materials"] is None


def test_execution_before_valid_from_is_rejected():
    package = build_package(
        produced_at="2026-10-30T12:00:00+00:00",
        producer_authorization=producer_authorization(
            valid_from="2026-11-01T00:00:00+00:00"
        ),
    )
    verified = verify(package)
    assert verified["status"] == "rejected"
    assert "producer_authorization_not_yet_valid_at_execution" in verified["reasons"]
    assert verified["materials"] is None


def test_future_execution_after_evaluation_is_rejected():
    package = build_package(produced_at="2026-11-17T06:00:00+00:00")
    verified = verify(package)
    assert verified["status"] == "rejected"
    assert "execution_time_after_evaluation" in verified["reasons"]
    assert verified["materials"] is None


def test_authorized_execution_inside_valid_window_verifies():
    package = build_package(
        produced_at="2026-11-10T12:00:00+00:00",
        producer_authorization=producer_authorization(
            valid_from="2026-11-01T00:00:00+00:00",
            valid_until="2026-12-01T00:00:00+00:00",
        ),
    )
    verified = verify(package, evaluated_at="2026-11-17T05:00:00+00:00")
    assert verified["status"] == "verified"
    assert verified["executed_at"] == "2026-11-10T12:00:00+00:00"
    assert verified["materials"]["authorized_check_ids"] == ["eci/hygiene/tach"]


def test_expiry_before_current_consumption_is_rejected():
    package = build_package(
        produced_at="2026-11-10T12:00:00+00:00",
        producer_authorization=producer_authorization(
            valid_from="2026-11-01T00:00:00+00:00",
            valid_until="2026-11-15T00:00:00+00:00",
        ),
    )
    verified = verify(package, evaluated_at="2026-11-17T05:00:00+00:00")
    assert verified["status"] == "rejected"
    assert "producer_authorization_expired" in verified["reasons"]
    assert verified["materials"] is None


def test_bare_true_execution_authority_leaves_time_unverified():
    package = build_package()
    verified = verify(package, execution_authority=accept_execution_bare_true)
    assert verified["status"] == "unverified"
    assert "execution_time_unverified" in verified["reasons"]
    assert verified["executed_at"] is None
    assert verified["materials"] is None
    assert verified["admitted_for_t1a"] is False


def test_execution_time_mismatch_with_produced_at_is_rejected():
    def mismatched(_package):
        return {"accepted": True, "executed_at": "2026-11-17T03:00:00+00:00"}

    package = build_package()
    verified = verify(package, execution_authority=mismatched)
    assert verified["status"] == "rejected"
    assert "execution_time_mismatch_produced_at" in verified["reasons"]
    assert verified["materials"] is None


def test_missing_authorities_are_unverified_not_admitted():
    package = build_package()
    verified = verify(
        package,
        issuer_authority=None,
        execution_authority=None,
        authorization_authority=None,
    )
    assert verified["status"] == "unverified"
    assert verified["admitted_for_t1a"] is False
    assert "issuer_authority_missing" in verified["reasons"]
    assert "execution_authority_missing" in verified["reasons"]
    assert "authorization_authority_missing" in verified["reasons"]


def test_disallowed_issuer_is_rejected():
    package = build_package(issuer_ref=OTHER_ISSUER)
    verified = verify(package)
    assert verified["status"] == "rejected"
    assert "handoff_issuer_not_allowed" in verified["reasons"]


def test_tampered_handoff_digest_is_rejected():
    package = build_package()
    tampered = copy.deepcopy(package)
    tampered["payload"]["result_ids"] = ["sha256:" + "9" * 64]
    tampered["handoff_ref"] = handoff._digest(
        {key: value for key, value in tampered.items() if key != "handoff_ref"}
    )
    with pytest.raises(
        handoff.LocalActionsHandoffError, match="handoff_payload_digest_mismatch"
    ):
        handoff.validate_local_handoff_package(tampered)
