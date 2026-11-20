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
BOUND_SUBJECTS = {"event_ref": "evt/actions-1", "scope_id": "scope/main-pre"}
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
            **BOUND_SUBJECTS,
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
                **BOUND_SUBJECTS,
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


@pytest.mark.parametrize(
    "bad_check_ids",
    [
        "eci/hygiene/tach",
        {"eci/hygiene/tach": True},
        123,
        True,
        None,
        [],
        [""],
        ["eci/hygiene/tach", 42],
    ],
)
def test_new059_producer_authorization_rejects_malformed_check_ids(bad_check_ids):
    scope = {
        "action": "validate",
        "target_ref": "refs/heads/main",
        "phase": "pre_publish",
        "check_ids": bad_check_ids,
    }
    with pytest.raises(
        handoff.LocalActionsHandoffError, match="handoff_authorization_scope_invalid"
    ):
        build_package(producer_authorization=producer_authorization(scope=scope))


def test_new059_producer_authorization_deduplicates_valid_check_ids():
    scope = {
        "action": "validate",
        "target_ref": "refs/heads/main",
        "phase": "pre_publish",
        "check_ids": ["eci/hygiene/tach", "eci/hygiene/tach"],
        **BOUND_SUBJECTS,
    }
    package = build_package(producer_authorization=producer_authorization(scope=scope))
    assert package["producer_authorization"]["scope"]["check_ids"] == [
        "eci/hygiene/tach"
    ]
    assert handoff.validate_local_handoff_package(package) is package
    verified = verify(package)
    assert verified["status"] == "verified"
    assert verified["authorized_check_ids"] == ["eci/hygiene/tach"]
    assert verified["remaining_work"] == []


@pytest.mark.parametrize(
    "field",
    [
        "produced_at",
        "evaluated_at",
        "executed_at",
        "valid_from",
        "valid_until",
        "revoked_at",
    ],
)
def test_new044_naive_datetimes_are_rejected_without_type_error(field):
    naive = "2026-11-17T04:00:00"
    if field == "produced_at":
        with pytest.raises(
            handoff.LocalActionsHandoffError, match="handoff_produced_at_invalid"
        ):
            build_package(produced_at=naive)
    elif field == "evaluated_at":
        package = build_package()
        with pytest.raises(
            handoff.LocalActionsHandoffError, match="handoff_evaluated_at_invalid"
        ):
            verify(package, evaluated_at=naive)
    elif field == "executed_at":
        package = build_package()
        with pytest.raises(
            handoff.LocalActionsHandoffError, match="handoff_executed_at_invalid"
        ):
            verify(
                package,
                execution_authority=lambda _pkg: {
                    "accepted": True,
                    "executed_at": naive,
                },
            )
    else:
        with pytest.raises(
            handoff.LocalActionsHandoffError,
            match=f"handoff_authorization_{field}_invalid",
        ):
            build_package(
                producer_authorization=producer_authorization(**{field: naive})
            )


def test_new044_mixed_timezone_offsets_verify_cleanly():
    package = build_package(
        produced_at="2026-11-17T12:00:00+08:00",
        producer_authorization=producer_authorization(
            valid_from="2026-11-01T00:00:00Z",
            valid_until="2026-12-01T00:00:00Z",
        ),
    )
    verified = verify(
        package,
        evaluated_at="2026-11-17T05:00:00Z",
        execution_authority=lambda _pkg: {
            "accepted": True,
            "executed_at": "2026-11-17T04:00:00Z",
        },
    )
    assert verified["status"] == "verified"
    assert verified["admitted_for_t1a"] is True


def test_new043_bound_grant_and_payload_scope_identities_reject_mismatches():
    bound_scope = {
        "action": "validate",
        "target_ref": "refs/heads/main",
        "phase": "pre_publish",
        "check_ids": ["eci/hygiene/tach"],
        "scope_id": "scope/main-pre",
        "expected_set_ref": "sha256:" + "7" * 64,
        "candidate_digest": "sha256:" + "8" * 64,
        "event_ref": "evt/actions-1",
    }
    package = build_package(
        payload=payload(
            job_receipts=[
                {
                    "kind": "engineering-ci-job-receipt",
                    "sha256": "3" * 64,
                    "scope_id": "scope/main-pre",
                    "expected_set_ref": "sha256:" + "7" * 64,
                }
            ]
        ),
        producer_authorization=producer_authorization(scope=bound_scope),
    )
    consumer = consumer_scope(
        scope_id="scope/main-pre",
        expected_set_ref="sha256:" + "7" * 64,
        candidate_digest="sha256:" + "8" * 64,
        event_ref="evt/actions-1",
    )

    def structured_auth(context):
        return {
            "accepted": True,
            "handoff_ref": context["handoff_ref"],
            "consumer_scope_ref": context["consumer_scope_ref"],
        }

    ok = verify(
        package, consumer_scope=consumer, authorization_authority=structured_auth
    )
    assert ok["status"] == "verified"
    assert ok["admitted_for_t1a"] is True

    wrong_candidate = verify(
        package,
        consumer_scope={**consumer, "candidate_digest": "sha256:" + "9" * 64},
        authorization_authority=structured_auth,
    )
    assert wrong_candidate["status"] == "rejected"
    assert (
        "producer_authorization_candidate_digest_mismatch" in wrong_candidate["reasons"]
    )
    assert wrong_candidate["remaining_work"] == [
        {
            "check_id": "eci/hygiene/tach",
            "reason": "producer_authorization_candidate_digest_mismatch",
        }
    ]

    wrong_payload_scope = verify(
        build_package(
            payload=payload(
                job_receipts=[
                    {
                        "kind": "engineering-ci-job-receipt",
                        "sha256": "3" * 64,
                        "scope_id": "scope/other",
                        "expected_set_ref": "sha256:" + "7" * 64,
                    }
                ]
            ),
        ),
        consumer_scope=consumer,
    )
    assert wrong_payload_scope["status"] == "rejected"
    assert "handoff_payload_scope_id_mismatch" in wrong_payload_scope["reasons"]
    assert wrong_payload_scope["admitted_for_t1a"] is False

    wrong_auth_binding = verify(
        package,
        consumer_scope=consumer,
        authorization_authority=lambda ctx: {
            "accepted": True,
            "handoff_ref": ctx["handoff_ref"],
            "consumer_scope_ref": "sha256:" + "0" * 64,
        },
    )
    assert wrong_auth_binding["status"] == "rejected"
    assert "authorization_authority_rejected" in wrong_auth_binding["reasons"]


def test_rejected_handoff_keeps_all_consumer_checks_in_remaining_work():
    package = build_package(issuer_ref=OTHER_ISSUER)
    rejected = verify(
        package,
        consumer_scope=consumer_scope(
            check_ids=["eci/hygiene/tach", "eci/hygiene/vulture"]
        ),
    )
    assert rejected["status"] == "rejected"
    assert rejected["authorized_check_ids"] == []
    assert rejected["remaining_work"] == [
        {"check_id": "eci/hygiene/tach", "reason": "handoff_issuer_not_allowed"},
        {
            "check_id": "eci/hygiene/vulture",
            "reason": "producer_authorization_check_not_granted",
        },
    ]


def _subject_consumer(**changes):
    return consumer_scope(
        expected_set_ref="sha256:" + "7" * 64,
        candidate_digest="sha256:" + "8" * 64,
        **changes,
    )


def _echo_authority(*, bound_subjects=None):
    def authority(context):
        decision = {
            "accepted": True,
            "handoff_ref": context["handoff_ref"],
            "consumer_scope_ref": context["consumer_scope_ref"],
        }
        if bound_subjects is not None:
            decision["bound_subjects"] = list(bound_subjects)
        return decision

    return authority


def test_d_g02_required_subject_missing_from_grant_is_not_verified():
    # The consumer requires Candidate and Expected-set identity; the grant only
    # covers Event/Scope. A bare-True authorization cannot supply the missing
    # subjects and must not release T1-a materials.
    package = build_package()
    result = verify(package, consumer_scope=_subject_consumer())
    assert result["status"] == "unverified"
    assert result["provenance_status"] == "unverified"
    assert result["admitted_for_t1a"] is False
    assert result["materials"] is None
    assert "consumer_candidate_digest_unbound" in result["reasons"]
    assert "consumer_expected_set_ref_unbound" in result["reasons"]
    assert result["authorized_check_ids"] == []
    assert result["remaining_work"] == [
        {
            "check_id": "eci/hygiene/tach",
            "reason": "consumer_candidate_digest_unbound",
        }
    ]


def test_d_g02_structured_authority_echo_without_subject_list_is_insufficient():
    package = build_package()
    result = verify(
        package,
        consumer_scope=_subject_consumer(),
        authorization_authority=_echo_authority(),
    )
    assert result["status"] == "unverified"
    assert "consumer_candidate_digest_unbound" in result["reasons"]
    assert result["materials"] is None


def test_d_g02_authority_must_cover_every_unbound_subject():
    package = build_package()
    partial = verify(
        package,
        consumer_scope=_subject_consumer(),
        authorization_authority=_echo_authority(bound_subjects=["candidate_digest"]),
    )
    assert partial["status"] == "unverified"
    assert "consumer_candidate_digest_unbound" not in partial["reasons"]
    assert "consumer_expected_set_ref_unbound" in partial["reasons"]

    covered = verify(
        package,
        consumer_scope=_subject_consumer(),
        authorization_authority=_echo_authority(
            bound_subjects=["candidate_digest", "expected_set_ref"]
        ),
    )
    assert covered["status"] == "verified"
    assert covered["admitted_for_t1a"] is True
    assert covered["materials"] is not None


def test_d_g02_authority_subject_binding_requires_matching_refs():
    package = build_package()
    wrong = verify(
        package,
        consumer_scope=_subject_consumer(),
        authorization_authority=lambda ctx: {
            "accepted": True,
            "handoff_ref": ctx["handoff_ref"],
            "consumer_scope_ref": "sha256:" + "0" * 64,
            "bound_subjects": ["candidate_digest", "expected_set_ref"],
        },
    )
    assert wrong["status"] == "rejected"
    assert "authorization_authority_rejected" in wrong["reasons"]


def test_d_g02_grant_and_receipt_bound_subjects_need_no_authority_echo():
    scope = {
        "action": "validate",
        "target_ref": "refs/heads/main",
        "phase": "pre_publish",
        "check_ids": ["eci/hygiene/tach"],
        "candidate_digest": "sha256:" + "8" * 64,
        **BOUND_SUBJECTS,
    }
    package = build_package(
        payload=payload(
            job_receipts=[
                {
                    "kind": "engineering-ci-job-receipt",
                    "sha256": "3" * 64,
                    "scope_id": "scope/main-pre",
                    "expected_set_ref": "sha256:" + "7" * 64,
                }
            ]
        ),
        producer_authorization=producer_authorization(scope=scope),
    )
    result = verify(package, consumer_scope=_subject_consumer())
    assert result["status"] == "verified"
    assert result["admitted_for_t1a"] is True


def test_d_g02_subjects_the_consumer_does_not_require_are_not_demanded():
    bare_scope = {
        "action": "validate",
        "target_ref": "refs/heads/main",
        "phase": "pre_publish",
        "check_ids": ["eci/hygiene/tach"],
    }
    package = build_package(
        producer_authorization=producer_authorization(scope=bare_scope)
    )
    result = verify(package, consumer_scope=bare_scope)
    assert result["status"] == "verified"
    assert result["admitted_for_t1a"] is True


def test_d_g02_context_action_target_required_subjects_and_legitimate_no_candidate():
    # Release -> publish -> candidate requires event_ref, scope_id,
    # expected_set_ref, and candidate_digest even when omitted from consumer_scope.
    grant_no_cand = {
        "action": "publish",
        "target_ref": "refs/heads/main",
        "phase": "pre_publish",
        "check_ids": ["eci/hygiene/tach"],
        "event_ref": "evt/actions-1",
        "scope_id": "scope/main-pre",
        "expected_set_ref": "sha256:" + "7" * 64,
    }
    pkg_no_cand = build_package(
        producer_authorization=producer_authorization(scope=grant_no_cand)
    )
    missing_cand = verify(
        pkg_no_cand,
        consumer_scope={
            "context": "release",
            "action": "publish",
            "target": "candidate",
            "target_ref": "refs/heads/main",
            "phase": "pre_publish",
            "check_ids": ["eci/hygiene/tach"],
            "event_ref": "evt/actions-1",
            "scope_id": "scope/main-pre",
            "expected_set_ref": "sha256:" + "7" * 64,
        },
    )
    assert missing_cand["status"] == "unverified"
    assert "consumer_candidate_digest_required" in missing_cand["reasons"]
    assert missing_cand["materials"] is None
    assert missing_cand["admitted_for_t1a"] is False

    # Pull-request -> validate -> event_head requires Event/Expected subjects
    # and MUST NOT require or force a fabricated candidate_digest.
    grant_pr = {
        "action": "validate",
        "target_ref": "refs/heads/main",
        "phase": "pre_publish",
        "check_ids": ["eci/hygiene/tach"],
        "event_ref": "evt/actions-1",
        "scope_id": "scope/main-pre",
        "expected_set_ref": "sha256:" + "7" * 64,
    }
    pkg_pr = build_package(
        producer_authorization=producer_authorization(scope=grant_pr)
    )
    pr_verified = verify(
        pkg_pr,
        consumer_scope={
            "context": "pull_request",
            "action": "validate",
            "target": "event_head",
            "target_ref": "refs/heads/main",
            "phase": "pre_publish",
            "check_ids": ["eci/hygiene/tach"],
            "event_ref": "evt/actions-1",
            "scope_id": "scope/main-pre",
            "expected_set_ref": "sha256:" + "7" * 64,
        },
    )
    assert pr_verified["status"] == "verified"
    assert pr_verified["admitted_for_t1a"] is True

    # Payload Job Receipt event_ref mismatch is rejected, and mismatched
    # authority subjects map is rejected.
    wrong_receipt_event = verify(
        build_package(
            payload=payload(
                job_receipts=[
                    {
                        "kind": "engineering-ci-job-receipt",
                        "sha256": "3" * 64,
                        "event_ref": "evt/other",
                    }
                ]
            )
        )
    )
    assert wrong_receipt_event["status"] == "rejected"
    assert "handoff_payload_event_ref_mismatch" in wrong_receipt_event["reasons"]

    wrong_subjects_map = verify(
        build_package(),
        consumer_scope=_subject_consumer(),
        authorization_authority=lambda ctx: {
            "accepted": True,
            "handoff_ref": ctx["handoff_ref"],
            "consumer_scope_ref": ctx["consumer_scope_ref"],
            "bound_subjects": ["candidate_digest", "expected_set_ref"],
            "subjects": {"candidate_digest": "sha256:" + "0" * 64},
        },
    )
    assert wrong_subjects_map["status"] == "rejected"
    assert "authorization_authority_rejected" in wrong_subjects_map["reasons"]


def test_d_g02_formal_event_context_completeness_and_release_target_contract():
    # Generic unscoped caller stays non-formal and records explicit limitation.
    bare_scope = {
        "action": "validate",
        "target_ref": "refs/heads/main",
        "phase": "pre_publish",
        "check_ids": ["eci/hygiene/tach"],
    }
    bare_pkg = build_package(
        producer_authorization=producer_authorization(scope=bare_scope)
    )
    generic = verify(bare_pkg, consumer_scope=bare_scope)
    assert generic["formal_event_context_bound"] is False
    assert "unscoped_consumer_context_not_formal_event" in generic["limitations"]

    # Formal Event/Release receiver refuses omitted context/target.
    with pytest.raises(
        handoff.LocalActionsHandoffError,
        match="handoff_formal_consumer_context_required",
    ):
        handoff.verify_event_local_handoff(
            bare_pkg,
            handoff_policy=handoff_policy(),
            consumer_scope=bare_scope,
            issuer_authority=accept_issuer,
            execution_authority=accept_execution,
            authorization_authority=accept_authorization,
            evaluated_at=EVALUATED_AT,
        )

    # Half-specified formal context (only context or only target) is refused.
    with pytest.raises(
        handoff.LocalActionsHandoffError, match="handoff_consumer_context_incomplete"
    ):
        verify(bare_pkg, consumer_scope={**bare_scope, "context": "pull_request"})

    # Release action/context cannot use event_head to waive Candidate binding.
    for combo in (
        {"context": "pull_request", "action": "release", "target": "event_head"},
        {"context": "release", "action": "publish", "target": "event_head"},
        {"context": "release", "action": "release", "target": "branch"},
    ):
        with pytest.raises(
            handoff.LocalActionsHandoffError,
            match="handoff_release_requires_candidate_target",
        ):
            verify(
                bare_pkg,
                consumer_scope={
                    **combo,
                    "target_ref": "refs/heads/main",
                    "phase": "pre_publish",
                    "check_ids": ["eci/hygiene/tach"],
                    "event_ref": "evt/actions-1",
                    "scope_id": "scope/main-pre",
                    "expected_set_ref": "sha256:" + "7" * 64,
                },
            )

    # Receiver-established valid event_head PR scope succeeds without candidate_digest,
    # whereas release x release x candidate demands candidate_digest.
    pr_scope = handoff.build_event_consumer_scope(
        context="pull_request",
        action="validate",
        target="event_head",
        target_ref="refs/pull/42/merge",
        phase="pre_publish",
        check_ids=["eci/hygiene/tach"],
        event_ref="evt/pr-42",
        scope_id="scope/pr-42",
        expected_set_ref="sha256:" + "7" * 64,
    )
    pr_grant = {
        "action": "validate",
        "target_ref": "refs/pull/42/merge",
        "phase": "pre_publish",
        "check_ids": ["eci/hygiene/tach"],
        "event_ref": "evt/pr-42",
        "scope_id": "scope/pr-42",
        "expected_set_ref": "sha256:" + "7" * 64,
    }
    pr_verified = handoff.verify_event_local_handoff(
        build_package(producer_authorization=producer_authorization(scope=pr_grant)),
        handoff_policy=handoff_policy(),
        consumer_scope=pr_scope,
        issuer_authority=accept_issuer,
        execution_authority=accept_execution,
        authorization_authority=accept_authorization,
        evaluated_at=EVALUATED_AT,
    )
    assert pr_verified["status"] == "verified"
    assert pr_verified["formal_event_context_bound"] is True
    assert pr_verified["admitted_for_t1a"] is True

    with pytest.raises(
        handoff.LocalActionsHandoffError, match="consumer_candidate_digest_required"
    ):
        handoff.build_event_consumer_scope(
            context="release",
            action="release",
            target="candidate",
            target_ref="refs/tags/v1.0.0",
            phase="pre_publish",
            check_ids=["eci/hygiene/tach"],
            event_ref="evt/rel-1",
            scope_id="scope/rel-1",
            expected_set_ref="sha256:" + "7" * 64,
        )

    rel_scope = handoff.build_event_consumer_scope(
        context="release",
        action="release",
        target="candidate",
        target_ref="refs/tags/v1.0.0",
        phase="pre_publish",
        check_ids=["eci/hygiene/tach"],
        event_ref="evt/rel-1",
        scope_id="scope/rel-1",
        expected_set_ref="sha256:" + "7" * 64,
        candidate_digest="sha256:" + "8" * 64,
    )
    rel_grant = {
        "action": "release",
        "target_ref": "refs/tags/v1.0.0",
        "phase": "pre_publish",
        "check_ids": ["eci/hygiene/tach"],
        "event_ref": "evt/rel-1",
        "scope_id": "scope/rel-1",
        "expected_set_ref": "sha256:" + "7" * 64,
        "candidate_digest": "sha256:" + "8" * 64,
    }
    rel_verified = handoff.verify_event_local_handoff(
        build_package(producer_authorization=producer_authorization(scope=rel_grant)),
        handoff_policy=handoff_policy(),
        consumer_scope=rel_scope,
        issuer_authority=accept_issuer,
        execution_authority=accept_execution,
        authorization_authority=accept_authorization,
        evaluated_at=EVALUATED_AT,
    )
    assert rel_verified["status"] == "verified"
    assert rel_verified["formal_event_context_bound"] is True
    assert rel_verified["admitted_for_t1a"] is True
