"""#61 R4 Release reconciliation contract tests."""

from __future__ import annotations

import copy

import pytest
from test_release_manifest import IDS, ManifestGitHub, facts
from test_required_assessment import (
    assess,
    check_obligation,
    historical_coverage,
    make_admission,
    make_entry,
    make_profile,
    make_projection,
    make_universe,
)

from engineering_ci import required_assessment_release as rar
from engineering_ci.release import manifest as release_manifest
from engineering_ci.release import registry as release_registry

HEAD = "2" * 40
TAG = "v0.4.11"
REPO = "example/ci"
EVALUATED_AT = "2026-11-17T12:00:00+00:00"


class R4ManifestGitHub(ManifestGitHub):
    def __init__(self):
        super().__init__()
        self.run["head_sha"] = HEAD
        self.run["repository"] = {"full_name": REPO}
        self.run["html_url"] = f"https://github.com/{REPO}/actions/runs/123"
        for job in self.jobs:
            job["head_sha"] = HEAD
        for item in self.artifacts:
            item["workflow_run"] = {"head_sha": HEAD, "id": 123}
            item["url"] = (
                f"https://api.github.com/repos/{REPO}/actions/artifacts/{item['id']}"
            )


def make_v1_manifest(*, mode="validation", api=None, ids=None, source=None):
    value = release_manifest.manifest(
        api or R4ManifestGitHub(),
        REPO,
        TAG,
        HEAD,
        123,
        1,
        mode,
        facts() if source is None else source,
        ids=IDS if ids is None else ids,
    )
    release_manifest.validate(value, REPO, TAG, HEAD)
    return value


def passed_assessment(*, contract_sample=False, candidate_commit=HEAD):
    # Keep profile phase-less (R1/R2 pattern) while universe evaluation carries
    # the Release pre_publish phase used by R4 publish_context.
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(
        profile,
        projection,
        [obligation],
        contract_sample=contract_sample,
        phase="pre_publish",
        candidate={"commit": candidate_commit},
    )
    admission = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
        contract_sample=contract_sample,
    )
    return assess(universe, projection, admission=admission), entry


def blocked_assessment():
    profile = make_profile(authority_verified=False)
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(
        profile,
        projection,
        [obligation],
        authority_status="unverified",
        contract_sample=False,
        phase="pre_publish",
        candidate={"commit": HEAD},
    )
    admission = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
        contract_sample=False,
    )
    return assess(universe, projection, admission=admission)


def publish_context(**changes):
    value = {
        "repository": REPO,
        "tag": TAG,
        "head_sha": HEAD,
        "mode": "validation",
        "phase": "pre_publish",
        "manifest_binding": "same_run",
        "evaluated_at": EVALUATED_AT,
        "run_id": 123,
        "run_attempt": 1,
    }
    value.update(changes)
    return value


def empty_artifact_obligations():
    return {"completeness": "empty", "items": []}


def complete_artifact_obligations(*names):
    return {
        "completeness": "complete",
        "items": [
            {"name": name, "obligation_ref": f"o/artifact-{name}"} for name in names
        ],
    }


def accept(_context):
    return {"accepted": True, "status": "authorized"}


def reject_policy(_context):
    return {"accepted": False, "status": "revoked"}


def unverified_policy(_context):
    return {"accepted": False, "status": "unverified"}


def accept_provider(_context):
    return True


def reject_provider(_context):
    return False


def reconcile(assessment, manifest=None, **changes):
    kwargs = {
        "publish_context": publish_context(),
        "policy_snapshot_authority": accept,
        "artifact_obligations": empty_artifact_obligations(),
        "artifact_obligation_authority": accept,
        "manifest_provider_authority": accept_provider,
        "contract_sample": False,
    }
    kwargs.update(changes)
    return rar.reconcile_release_assessment(
        assessment,
        manifest if manifest is not None else make_v1_manifest(),
        **kwargs,
    )


def test_valid_v1_manifest_with_provider_authority_authorizes_release():
    assessment, _entry = passed_assessment(contract_sample=False)
    manifest = make_v1_manifest()
    result = reconcile(assessment, manifest)
    assert result["status"] == "verified"
    assert result["authorization_basis"] == "provider_verified"
    assert result["release_authorized"] is True
    assert result["policy_snapshot_status"] == "authorized"
    assert result["gaps"] == []
    assert all(
        item["trusted"] is False and item["evidence_admission"] == "not_evaluated"
        for item in result["fixed_manifest_checks"].values()
    )
    assert rar.validate_release_reconciliation(result) is result


def test_structural_fixture_without_provider_is_not_production_authorization():
    assessment, _ = passed_assessment(contract_sample=False)
    result = reconcile(assessment, manifest_provider_authority=None)
    assert result["status"] == "unverified"
    assert result["authorization_basis"] == "structural_fixture"
    assert result["release_authorized"] is False
    assert "release_provider_authority_missing" in result["reasons"]


def test_invalid_v1_passed_manifest_missing_tools_is_rejected():
    assessment, _ = passed_assessment(contract_sample=False)
    invalid = make_v1_manifest()
    invalid["tools"] = []
    with pytest.raises(
        rar.RequiredAssessmentReleaseError, match="release_manifest_v1_invalid"
    ):
        reconcile(assessment, invalid)


def test_invalid_v1_required_false_check_is_rejected():
    assessment, _ = passed_assessment(contract_sample=False)
    invalid = make_v1_manifest()
    invalid["checks"]["ruff_lint"]["required"] = False
    with pytest.raises(
        rar.RequiredAssessmentReleaseError, match="release_manifest_v1_invalid"
    ):
        reconcile(assessment, invalid)


def test_invalid_v1_job_step_identity_is_rejected():
    assessment, _ = passed_assessment(contract_sample=False)
    invalid = make_v1_manifest()
    invalid["checks"]["ruff_lint"]["job"] = "wrong-job"
    with pytest.raises(
        rar.RequiredAssessmentReleaseError, match="release_manifest_v1_invalid"
    ):
        reconcile(assessment, invalid)


def test_invalid_v1_aggregate_result_is_rejected():
    assessment, _ = passed_assessment(contract_sample=False)
    invalid = make_v1_manifest()
    invalid["result"] = "failed"
    with pytest.raises(
        rar.RequiredAssessmentReleaseError, match="release_manifest_v1_invalid"
    ):
        reconcile(assessment, invalid)


def test_invalid_v1_artifact_id_is_rejected():
    assessment, _ = passed_assessment(contract_sample=False)
    invalid = make_v1_manifest()
    invalid["artifacts"][0]["id"] = 0
    with pytest.raises(
        rar.RequiredAssessmentReleaseError, match="release_manifest_v1_invalid"
    ):
        reconcile(assessment, invalid)


def test_blocked_assessment_cannot_authorize_release():
    assessment = blocked_assessment()
    result = reconcile(assessment)
    assert result["status"] == "rejected"
    assert result["release_authorized"] is False
    assert "release_assessment_blocked" in result["reasons"]


def test_failed_manifest_fixed_check_blocks_without_trust_rewrite():
    assessment, _ = passed_assessment(contract_sample=False)
    api = R4ManifestGitHub()
    for job in api.jobs:
        if job["name"] == release_registry.CHECKS["ruff_lint"][0]:
            for step in job["steps"]:
                if step["name"] == release_registry.CHECKS["ruff_lint"][1]:
                    step["conclusion"] = "failure"
                    job["conclusion"] = "failure"
    manifest = make_v1_manifest(api=api)
    assert manifest["result"] == "failed"
    result = reconcile(assessment, manifest)
    assert result["status"] == "rejected"
    assert result["release_authorized"] is False
    assert "release_manifest_result_failed" in result["reasons"]
    assert result["fixed_manifest_checks"]["ruff_lint"]["trusted"] is False


def test_same_candidate_sha_does_not_preserve_revoked_policy():
    assessment, _ = passed_assessment(contract_sample=False)
    result = reconcile(assessment, policy_snapshot_authority=reject_policy)
    assert result["status"] == "rejected"
    assert result["release_authorized"] is False
    assert "policy_snapshot_revoked" in result["reasons"]


def test_wrong_mode_same_run_is_rejected():
    assessment, _ = passed_assessment(contract_sample=False)
    result = reconcile(
        assessment,
        publish_context=publish_context(mode="publication"),
    )
    assert result["status"] == "rejected"
    assert "release_manifest_mode_mismatch" in result["reasons"]
    assert result["release_authorized"] is False


def test_wrong_run_attempt_same_run_is_rejected():
    assessment, _ = passed_assessment(contract_sample=False)
    result = reconcile(
        assessment,
        publish_context=publish_context(run_id=999, run_attempt=2),
    )
    assert result["status"] == "rejected"
    assert "release_manifest_run_mismatch" in result["reasons"]
    assert "release_manifest_attempt_mismatch" in result["reasons"]


def test_validation_to_publication_handoff_requires_distinct_run_and_authorities():
    assessment, _ = passed_assessment(contract_sample=False)
    validation = make_v1_manifest(mode="validation")
    context = publish_context(
        mode="publication",
        manifest_binding="validation_to_publication",
        run_id=999,
        run_attempt=2,
        validation_identity={"run_id": 123, "run_attempt": 1, "mode": "validation"},
    )
    missing = reconcile(
        assessment,
        validation,
        publish_context=context,
        handoff_authority=None,
    )
    assert missing["release_authorized"] is False
    assert "release_handoff_authority_missing" in missing["reasons"]

    ok = reconcile(
        assessment,
        validation,
        publish_context=context,
        handoff_authority=accept_provider,
    )
    assert ok["status"] == "verified"
    assert ok["authorization_basis"] == "provider_verified"
    assert ok["release_authorized"] is True
    assert ok["validation_identity"]["run_id"] == 123


def test_validation_handoff_rejects_wrong_validation_identity():
    assessment, _ = passed_assessment(contract_sample=False)
    validation = make_v1_manifest(mode="validation")
    result = reconcile(
        assessment,
        validation,
        publish_context=publish_context(
            mode="publication",
            manifest_binding="validation_to_publication",
            run_id=999,
            run_attempt=2,
            validation_identity={"run_id": 50, "run_attempt": 1},
        ),
        handoff_authority=accept_provider,
    )
    assert result["status"] == "rejected"
    assert "release_handoff_validation_run_mismatch" in result["reasons"]


def test_complete_authorized_artifact_obligation_set_matches_manifest():
    assessment, _ = passed_assessment(contract_sample=False)
    result = reconcile(
        assessment,
        artifact_obligations=complete_artifact_obligations(
            "pytest-report", "core-tests"
        ),
        artifact_obligation_authority=accept,
    )
    assert result["status"] == "verified"
    assert result["release_authorized"] is True
    assert {item["name"] for item in result["artifact_obligations"]["items"]} == {
        "pytest-report",
        "core-tests",
    }


def test_missing_declared_artifact_despite_unrelated_artifact_is_rejected():
    assessment, _ = passed_assessment(contract_sample=False)
    # Build a failed-incomplete artifact list is invalid for passed v1; instead
    # declare an obligation for a name that we remove by using ids subset and
    # accepting failed aggregate path.
    api = R4ManifestGitHub()
    # Keep all artifacts so v1 passed stays valid, then declare an unknown mapping
    # is already rejected at normalize. Simulate missing by filtering artifacts and
    # forcing failed result with a failed check so validate still accepts.
    for job in api.jobs:
        if job["name"] == release_registry.CHECKS["candidate"][0]:
            job["conclusion"] = "failure"
            job["steps"][0]["conclusion"] = "failure"
    # Remove one artifact while result is failed (passed path requires all).
    ids = {name: asset_id for name, asset_id in IDS.items() if name != "pytest-report"}
    manifest = make_v1_manifest(api=api, ids=ids)
    assert manifest["result"] == "failed"
    assert "pytest-report" not in {item["name"] for item in manifest["artifacts"]}
    result = reconcile(
        assessment,
        manifest,
        artifact_obligations=complete_artifact_obligations("pytest-report"),
        artifact_obligation_authority=accept,
    )
    assert result["status"] == "rejected"
    assert "release_required_artifacts_missing" in result["reasons"]
    assert any(
        gap["ref"] == "pytest-report" and gap["reason"] == "required_artifact_missing"
        for gap in result["gaps"]
    )
    assert result["release_authorized"] is False


def test_artifact_obligation_authority_missing_is_unverified():
    assessment, _ = passed_assessment(contract_sample=False)
    result = reconcile(assessment, artifact_obligation_authority=None)
    assert result["status"] == "unverified"
    assert result["release_authorized"] is False
    assert "artifact_obligation_authority_missing" in result["reasons"]


def test_authorized_empty_artifact_obligation_set_is_valid():
    assessment, _ = passed_assessment(contract_sample=False)
    result = reconcile(
        assessment,
        artifact_obligations=empty_artifact_obligations(),
        artifact_obligation_authority=accept,
    )
    assert result["artifact_obligations"]["completeness"] == "empty"
    assert result["status"] == "verified"
    assert result["release_authorized"] is True
    assert result["artifact_obligation_status"] == "authorized"


def test_phase_mismatch_is_rejected():
    assessment, _ = passed_assessment(contract_sample=False)
    result = reconcile(
        assessment, publish_context=publish_context(phase="post_publish")
    )
    assert result["status"] == "rejected"
    assert "release_assessment_phase_mismatch" in result["reasons"]


def test_contract_sample_cannot_authorize_production_release():
    assessment, _ = passed_assessment(contract_sample=True)
    result = reconcile(assessment, contract_sample=True)
    assert result["status"] == "verified"
    assert result["release_authorized"] is False
    assert "contract_sample_inputs_not_production_release" in result["reasons"]


def test_check_binding_keeps_unsatisfied_assessment_as_gap():
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(
        profile,
        projection,
        [obligation],
        contract_sample=False,
        phase="pre_publish",
        candidate={"commit": HEAD},
    )
    assessment = assess(universe, projection, admission=None)
    result = reconcile(
        assessment,
        check_bindings=[
            {
                "manifest_check_key": "ruff_lint",
                "expected_check_ref": entry["expected_check_ref"],
            }
        ],
    )
    assert result["status"] == "rejected"
    assert result["release_authorized"] is False
    assert any(gap["kind"] == "check_binding" for gap in result["gaps"])


def _recompute(envelope):
    payload = {
        key: value for key, value in envelope.items() if key != "reconciliation_ref"
    }
    envelope["reconciliation_ref"] = rar._digest(payload)
    return envelope


def test_validate_rejects_recomputed_authorized_inconsistent_envelope():
    assessment, _ = passed_assessment(contract_sample=False)
    result = reconcile(assessment)
    assert result["release_authorized"] is True

    forged = copy.deepcopy(result)
    forged["release_authorized"] = True
    forged["status"] = "rejected"
    forged = _recompute(forged)
    with pytest.raises(
        rar.RequiredAssessmentReleaseError,
        match="release_authorized_requires_verified_status",
    ):
        rar.validate_release_reconciliation(forged)

    forged = copy.deepcopy(result)
    forged["contract_sample"] = True
    forged["release_authorized"] = True
    forged = _recompute(forged)
    with pytest.raises(
        rar.RequiredAssessmentReleaseError,
        match="release_authorized_rejects_contract_sample",
    ):
        rar.validate_release_reconciliation(forged)

    forged = copy.deepcopy(result)
    forged["policy_snapshot_status"] = "rejected"
    forged = _recompute(forged)
    with pytest.raises(
        rar.RequiredAssessmentReleaseError,
        match="release_authorized_requires_policy_authorized",
    ):
        rar.validate_release_reconciliation(forged)

    forged = copy.deepcopy(result)
    forged["authorization_basis"] = "structural_fixture"
    forged = _recompute(forged)
    with pytest.raises(
        rar.RequiredAssessmentReleaseError,
        match="release_authorized_requires_provider_verified",
    ):
        rar.validate_release_reconciliation(forged)

    forged = copy.deepcopy(result)
    forged["manifest_result"] = "failed"
    forged = _recompute(forged)
    with pytest.raises(
        rar.RequiredAssessmentReleaseError,
        match="release_authorized_requires_manifest_passed",
    ):
        rar.validate_release_reconciliation(forged)

    forged = copy.deepcopy(result)
    forged["gaps"] = [{"kind": "x", "ref": "y", "reason": "z"}]
    forged = _recompute(forged)
    with pytest.raises(
        rar.RequiredAssessmentReleaseError, match="release_authorized_rejects_gaps"
    ):
        rar.validate_release_reconciliation(forged)

    forged = copy.deepcopy(result)
    forged["candidate"] = dict(forged["candidate"])
    forged["candidate"]["commit"] = "3" * 40
    forged = _recompute(forged)
    with pytest.raises(
        rar.RequiredAssessmentReleaseError,
        match="release_verified_requires_commit_alignment",
    ):
        rar.validate_release_reconciliation(forged)


def test_validate_accepts_unchanged_valid_envelope():
    assessment, _ = passed_assessment(contract_sample=False)
    result = reconcile(assessment)
    assert rar.validate_release_reconciliation(copy.deepcopy(result)) is not None
