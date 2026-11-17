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


def accept(_package):
    return True


def reject(_package):
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


def test_controlled_local_handoff_verifies_and_exposes_t1a_materials():
    package = build_package()
    assert package["status"] == "candidate"
    assert package["declared_trusted"] is False
    assert handoff.validate_local_handoff_package(package) is package

    verified = handoff.verify_local_handoff(
        package,
        handoff_policy=handoff_policy(),
        issuer_authority=accept,
        execution_authority=accept,
        authorization_authority=accept,
        evaluated_at=EVALUATED_AT,
        required_check_ids=["eci/hygiene/tach"],
    )
    assert verified["status"] == "verified"
    assert verified["admitted_for_t1a"] is True
    assert verified["materials"]["result_ids"] == ["sha256:" + "4" * 64]
    assert verified["payload_digest"] == package["payload"]["payload_digest"]


def test_declared_trusted_local_pass_is_not_authority():
    package = build_package(declared_trusted=True)
    assert "local_declared_trusted_ignored" in package["reasons"]

    verified = handoff.verify_local_handoff(
        package,
        handoff_policy=handoff_policy(),
        issuer_authority=accept,
        execution_authority=accept,
        authorization_authority=accept,
        evaluated_at=EVALUATED_AT,
        required_check_ids=["eci/hygiene/tach"],
    )
    # Declared trust alone does not reject when authorities accept, but it is
    # never treated as authority; the reason is retained.
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
    verified = handoff.verify_local_handoff(
        uncontrolled,
        handoff_policy=handoff_policy(
            allowed_execution_boundaries=["uncontrolled_developer_workstation"]
        ),
        issuer_authority=accept,
        execution_authority=accept,
        authorization_authority=accept,
        evaluated_at=EVALUATED_AT,
    )
    assert verified["status"] == "rejected"
    assert verified["admitted_for_t1a"] is False
    assert verified["materials"] is None
    assert "uncontrolled_local_execution" in verified["reasons"]
    assert "self_reported_execution_evidence" in verified["reasons"]


def test_signed_identity_with_rejected_execution_authority_stays_untrusted():
    package = build_package()
    verified = handoff.verify_local_handoff(
        package,
        handoff_policy=handoff_policy(),
        issuer_authority=accept,
        execution_authority=reject,
        authorization_authority=accept,
        evaluated_at=EVALUATED_AT,
        required_check_ids=["eci/hygiene/tach"],
    )
    assert verified["status"] == "rejected"
    assert "execution_authority_rejected" in verified["reasons"]
    assert verified["materials"] is None


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
        (revoked, "producer_authorization_revoked"),
    ):
        verified = handoff.verify_local_handoff(
            package,
            handoff_policy=handoff_policy(),
            issuer_authority=accept,
            execution_authority=accept,
            authorization_authority=accept,
            evaluated_at=EVALUATED_AT,
            required_check_ids=["eci/hygiene/tach"],
        )
        assert verified["status"] == "rejected"
        assert reason in verified["reasons"]


def test_unauthorized_check_scope_is_rejected():
    package = build_package()
    verified = handoff.verify_local_handoff(
        package,
        handoff_policy=handoff_policy(),
        issuer_authority=accept,
        execution_authority=accept,
        authorization_authority=accept,
        evaluated_at=EVALUATED_AT,
        required_check_ids=["eci/hygiene/tach", "eci/hygiene/vulture"],
    )
    assert verified["status"] == "rejected"
    assert "producer_authorization_scope_incomplete" in verified["reasons"]


def test_missing_authorities_are_unverified_not_admitted():
    package = build_package()
    verified = handoff.verify_local_handoff(
        package,
        handoff_policy=handoff_policy(),
        evaluated_at=EVALUATED_AT,
        required_check_ids=["eci/hygiene/tach"],
    )
    assert verified["status"] == "unverified"
    assert verified["admitted_for_t1a"] is False
    assert "issuer_authority_missing" in verified["reasons"]
    assert "execution_authority_missing" in verified["reasons"]
    assert "authorization_authority_missing" in verified["reasons"]


def test_disallowed_issuer_is_rejected():
    package = build_package(issuer_ref=OTHER_ISSUER)
    verified = handoff.verify_local_handoff(
        package,
        handoff_policy=handoff_policy(),
        issuer_authority=accept,
        execution_authority=accept,
        authorization_authority=accept,
        evaluated_at=EVALUATED_AT,
    )
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
