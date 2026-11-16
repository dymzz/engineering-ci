"""#66-B2: Phase is part of approved profile scope, not a consumer guess."""

import pytest

from engineering_ci import expected_checks


def _profile(phase=None):
    return expected_checks.build_effective_profile(
        profile_ref={"name": "phase-test", "version": "1.0.0"},
        profile_revision="rev-1",
        approval_source="governance-signed",
        authority_verified=True,
        effective_at="2026-11-15T00:00:00Z",
        evaluated_at="2026-11-15T01:00:00Z",
        context="release",
        action="publish",
        target="artifact",
        phase=phase,
        required_obligations=[
            {
                "obligation_ref": "eci/terminal",
                "kind": "non_check",
                "phases": ["post_publish"],
            }
        ],
    )


def test_phase_scoped_obligation_must_have_approved_evaluation_phase():
    with pytest.raises(expected_checks.ExpectedError, match="phase_unresolved"):
        expected_checks.validate_effective_profile(_profile())


def test_approved_phase_is_bound_in_identity_and_scope():
    before = _profile("pre_publish")
    after = _profile("post_publish")
    expected_checks.validate_effective_profile(before)
    expected_checks.validate_effective_profile(after)
    assert before["profile_sha256"] != after["profile_sha256"]
    assert before["approval_binding"]["target_scope"]["phase"] == "pre_publish"
    assert expected_checks._obligation_matches_scope(
        before["required_obligations"][0], phase="post_publish"
    )
    assert not expected_checks._obligation_matches_scope(
        before["required_obligations"][0], phase="pre_publish"
    )
    modified = dict(after)
    modified["phase"] = "pre_publish"
    with pytest.raises(expected_checks.ExpectedError, match="digest_mismatch"):
        expected_checks.validate_effective_profile(modified)


def test_unscoped_obligations_keep_existing_behavior():
    assert expected_checks._obligation_matches_scope(
        {"obligation_ref": "eci/general"}, phase=None
    )
    assert not expected_checks._obligation_matches_scope(
        {"phases": ["post_publish"]}, phase=None
    )
