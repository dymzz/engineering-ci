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
        self.run["status"] = "completed"
        self.run["conclusion"] = "success"
        self.run["head_branch"] = "main"
        for job in self.jobs:
            job["head_sha"] = HEAD
        for item in self.artifacts:
            item["workflow_run"] = {"head_sha": HEAD, "id": 123}
            item["url"] = (
                f"https://api.github.com/repos/{REPO}/actions/artifacts/{item['id']}"
            )

    def __call__(self, path, method="GET", data=None):
        if path == "actions/runs/123":
            return self.run
        return super().__call__(path, method=method, data=data)


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


def publication_same_run_context(**changes):
    return publish_context(mode="publication", **changes)


def publication_handoff_context(**changes):
    value = publish_context(
        mode="publication",
        manifest_binding="validation_to_publication",
        run_id=999,
        run_attempt=1,
        validation_identity={"run_id": 123, "run_attempt": 1},
    )
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


def authorize(assessment, **changes):
    """Publication-stage authorization using a validation→publication handoff."""
    kwargs = {
        "manifest": make_v1_manifest(mode="validation"),
        "publish_context": publication_handoff_context(),
        "handoff_authority": accept_provider,
    }
    kwargs.update(changes)
    return reconcile(assessment, **kwargs)


def test_validation_mode_is_verified_but_does_not_authorize_publication():
    assessment, _entry = passed_assessment(contract_sample=False)
    manifest = make_v1_manifest(mode="validation")
    result = reconcile(assessment, manifest)
    assert result["status"] == "verified"
    assert result["authorization_basis"] == "provider_verified"
    assert result["publish_context"]["mode"] == "validation"
    assert result["release_authorized"] is False
    assert result["policy_snapshot_status"] == "authorized"
    assert result["gaps"] == []
    assert all(
        item["trusted"] is False and item["evidence_admission"] == "not_evaluated"
        for item in result["fixed_manifest_checks"].values()
    )
    assert rar.validate_release_reconciliation(result) is result


def test_publication_mode_with_authorities_can_authorize_release():
    assessment, _ = passed_assessment(contract_sample=False)
    result = authorize(assessment)
    assert result["status"] == "verified"
    assert result["publish_context"]["mode"] == "publication"
    assert result["release_authorized"] is True
    assert rar.validate_release_reconciliation(result) is result


def test_publication_same_run_context_can_authorize():
    assessment, _ = passed_assessment(contract_sample=False)
    manifest = make_v1_manifest(mode="publication")
    result = reconcile(
        assessment,
        manifest,
        publish_context=publication_same_run_context(),
    )
    assert result["status"] == "verified"
    assert result["release_authorized"] is True


def test_provider_reuses_verification_evidence_for_artifact_digest_run_binding():
    assessment, _ = passed_assessment(contract_sample=False)
    api = R4ManifestGitHub()
    manifest = make_v1_manifest(api=api, mode="validation")
    result = authorize(
        assessment,
        manifest=manifest,
        manifest_provider_authority=rar.manifest_provider_from_verification(api),
    )
    assert result["status"] == "verified"
    assert result["authorization_basis"] == "provider_verified"
    assert result["release_authorized"] is True


def test_provider_rejects_same_name_wrong_artifact_digest():
    assessment, _ = passed_assessment(contract_sample=False)
    api = R4ManifestGitHub()
    manifest = make_v1_manifest(api=api)
    # Keep name/id/url valid for manifest.validate, but disagree with Provider digest.
    manifest["artifacts"] = copy.deepcopy(manifest["artifacts"])
    manifest["artifacts"][0]["digest"] = "sha256:" + ("d" * 64)
    release_manifest.validate(manifest, REPO, TAG, HEAD)
    result = authorize(
        assessment,
        manifest=manifest,
        manifest_provider_authority=rar.manifest_provider_from_verification(api),
    )
    assert result["release_authorized"] is False
    assert result["authorization_basis"] == "structural_fixture"
    assert "release_provider_authority_rejected" in result["reasons"]


def test_invalid_v1_artifact_digest_format_is_rejected():
    assessment, _ = passed_assessment(contract_sample=False)
    invalid = make_v1_manifest()
    invalid["artifacts"][0]["digest"] = "not-a-digest"
    with pytest.raises(
        rar.RequiredAssessmentReleaseError, match="release_manifest_v1_invalid"
    ):
        reconcile(assessment, invalid)


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
    context = publication_handoff_context()
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


def test_same_run_different_attempt_is_not_distinct_run_handoff():
    assessment, _ = passed_assessment(contract_sample=False)
    result = reconcile(
        assessment,
        make_v1_manifest(mode="validation"),
        publish_context=publication_handoff_context(
            run_id=123,
            run_attempt=2,
            validation_identity={"run_id": 123, "run_attempt": 1},
        ),
        handoff_authority=accept_provider,
    )
    assert result["status"] == "rejected"
    assert "release_handoff_requires_distinct_publication_run" in result["reasons"]
    assert result["release_authorized"] is False


def test_distinct_run_same_attempt_handoff_is_allowed():
    assessment, _ = passed_assessment(contract_sample=False)
    result = authorize(
        assessment,
        publish_context=publication_handoff_context(
            run_id=999,
            run_attempt=1,
            validation_identity={"run_id": 123, "run_attempt": 1},
        ),
    )
    assert result["status"] == "verified"
    assert result["release_authorized"] is True
    assert result["publish_context"]["run_id"] == 999
    assert result["validation_identity"]["run_id"] == 123


def test_validation_handoff_rejects_wrong_validation_identity():
    assessment, _ = passed_assessment(contract_sample=False)
    validation = make_v1_manifest(mode="validation")
    result = reconcile(
        assessment,
        validation,
        publish_context=publication_handoff_context(
            validation_identity={"run_id": 50, "run_attempt": 1},
        ),
        handoff_authority=accept_provider,
    )
    assert result["status"] == "rejected"
    assert "release_handoff_validation_run_mismatch" in result["reasons"]


def test_complete_authorized_artifact_obligation_set_matches_manifest():
    assessment, _ = passed_assessment(contract_sample=False)
    result = authorize(
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
    result = authorize(
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
    result = authorize(assessment, contract_sample=True)
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
    result = authorize(assessment)
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
    forged["assessment_status"] = "blocked"
    forged = _recompute(forged)
    with pytest.raises(
        rar.RequiredAssessmentReleaseError,
        match="release_authorized_requires_assessment_passed",
    ):
        rar.validate_release_reconciliation(forged)

    forged = copy.deepcopy(result)
    forged["artifact_obligation_status"] = "rejected"
    forged = _recompute(forged)
    with pytest.raises(
        rar.RequiredAssessmentReleaseError,
        match="release_authorized_requires_artifact_obligations_authorized",
    ):
        rar.validate_release_reconciliation(forged)

    forged = copy.deepcopy(result)
    forged["publish_context"] = dict(forged["publish_context"])
    forged["publish_context"]["mode"] = "validation"
    forged = _recompute(forged)
    with pytest.raises(
        rar.RequiredAssessmentReleaseError,
        match="release_validation_handoff_requires_publication_mode",
    ):
        rar.validate_release_reconciliation(forged)

    forged = copy.deepcopy(result)
    forged["publish_context"] = dict(forged["publish_context"])
    forged["publish_context"]["mode"] = "validation"
    forged["publish_context"]["manifest_binding"] = "same_run"
    forged["publish_context"].pop("validation_identity", None)
    forged["validation_identity"] = None
    forged["manifest_identity"] = dict(forged["manifest_identity"])
    forged["manifest_identity"]["mode"] = "validation"
    forged["manifest_identity"]["run_id"] = forged["publish_context"]["run_id"]
    forged["manifest_identity"]["run_attempt"] = forged["publish_context"][
        "run_attempt"
    ]
    forged = _recompute(forged)
    with pytest.raises(
        rar.RequiredAssessmentReleaseError,
        match="release_authorized_requires_publication_mode",
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

    forged = copy.deepcopy(result)
    forged["validation_identity"] = dict(forged["validation_identity"])
    forged["publish_context"] = dict(forged["publish_context"])
    forged["publish_context"]["validation_identity"] = dict(
        forged["publish_context"]["validation_identity"]
    )
    publication_run = forged["publish_context"]["run_id"]
    forged["validation_identity"]["run_id"] = publication_run
    forged["publish_context"]["validation_identity"]["run_id"] = publication_run
    forged = _recompute(forged)
    with pytest.raises(
        rar.RequiredAssessmentReleaseError,
        match="release_authorized_requires_distinct_publication_run",
    ):
        rar.validate_release_reconciliation(forged)


def test_manifest_identity_tracks_artifact_id_and_digest():
    assessment, _ = passed_assessment(contract_sample=False)
    base = make_v1_manifest(mode="validation")
    authorized = authorize(assessment, manifest=base)
    stable = authorize(assessment, manifest=copy.deepcopy(base))
    assert authorized["manifest_identity"] == stable["manifest_identity"]
    assert authorized["reconciliation_ref"] == stable["reconciliation_ref"]
    assert authorized["manifest_identity"] == rar.bind_manifest_identity(
        base, repository=REPO, tag=TAG, head=HEAD
    )
    assert (
        rar.require_reconciliation_manifest(
            authorized, base, repository=REPO, tag=TAG, head=HEAD
        )
        == authorized["manifest_identity"]
    )

    changed_id = copy.deepcopy(base)
    new_id = changed_id["artifacts"][0]["id"] + 1000
    changed_id["artifacts"][0]["id"] = new_id
    changed_id["artifacts"][0]["url"] = (
        f"https://api.github.com/repos/{REPO}/actions/artifacts/{new_id}"
    )
    release_manifest.validate(changed_id, REPO, TAG, HEAD)
    id_result = authorize(assessment, manifest=changed_id)
    assert (
        id_result["manifest_identity"]["content_digest"]
        != authorized["manifest_identity"]["content_digest"]
    )
    assert id_result["reconciliation_ref"] != authorized["reconciliation_ref"]
    assert (
        id_result["manifest_identity"]["artifacts"]
        != authorized["manifest_identity"]["artifacts"]
    )
    with pytest.raises(
        rar.RequiredAssessmentReleaseError, match="release_manifest_identity_mismatch"
    ):
        rar.require_reconciliation_manifest(
            authorized, changed_id, repository=REPO, tag=TAG, head=HEAD
        )

    changed_digest = copy.deepcopy(base)
    changed_digest["artifacts"][0]["digest"] = "sha256:" + ("a" * 64)
    release_manifest.validate(changed_digest, REPO, TAG, HEAD)
    digest_result = authorize(assessment, manifest=changed_digest)
    assert (
        digest_result["manifest_identity"]["content_digest"]
        != authorized["manifest_identity"]["content_digest"]
    )
    assert digest_result["reconciliation_ref"] != authorized["reconciliation_ref"]
    with pytest.raises(
        rar.RequiredAssessmentReleaseError, match="release_manifest_identity_mismatch"
    ):
        rar.require_reconciliation_manifest(
            authorized, changed_digest, repository=REPO, tag=TAG, head=HEAD
        )


def test_validate_rejects_inconsistent_validation_identity():
    assessment, _ = passed_assessment(contract_sample=False)
    result = authorize(assessment)
    assert result["release_authorized"] is True

    forged = copy.deepcopy(result)
    forged["validation_identity"] = dict(forged["validation_identity"])
    forged["validation_identity"]["run_id"] = 124
    forged = _recompute(forged)
    with pytest.raises(
        rar.RequiredAssessmentReleaseError,
        match="release_validation_identity_inconsistent",
    ):
        rar.validate_release_reconciliation(forged)

    forged = copy.deepcopy(result)
    forged["validation_identity"] = dict(forged["validation_identity"])
    forged["validation_identity"]["run_attempt"] = 2
    forged = _recompute(forged)
    with pytest.raises(
        rar.RequiredAssessmentReleaseError,
        match="release_validation_identity_inconsistent",
    ):
        rar.validate_release_reconciliation(forged)

    forged = copy.deepcopy(result)
    forged["validation_identity"] = dict(forged["validation_identity"])
    forged["validation_identity"]["mode"] = "publication"
    forged = _recompute(forged)
    with pytest.raises(
        rar.RequiredAssessmentReleaseError,
        match="release_validation_identity_inconsistent",
    ):
        rar.validate_release_reconciliation(forged)

    forged = copy.deepcopy(result)
    forged["publish_context"] = dict(forged["publish_context"])
    forged["publish_context"]["manifest_binding"] = "same_run"
    forged = _recompute(forged)
    with pytest.raises(
        rar.RequiredAssessmentReleaseError,
        match="release_same_run_rejects_validation_identity",
    ):
        rar.validate_release_reconciliation(forged)

    same_run = reconcile(
        assessment,
        make_v1_manifest(mode="publication"),
        publish_context=publication_same_run_context(),
    )
    assert same_run["release_authorized"] is True
    assert same_run["validation_identity"] is None
    assert (
        rar.validate_release_reconciliation(copy.deepcopy(same_run))[
            "reconciliation_ref"
        ]
        == same_run["reconciliation_ref"]
    )


def test_validate_accepts_unchanged_valid_envelope():
    assessment, _ = passed_assessment(contract_sample=False)
    result = authorize(assessment)
    assert (
        rar.validate_release_reconciliation(copy.deepcopy(result))["reconciliation_ref"]
        == result["reconciliation_ref"]
    )
    validated = reconcile(assessment, make_v1_manifest(mode="validation"))
    assert validated["release_authorized"] is False
    assert (
        rar.validate_release_reconciliation(copy.deepcopy(validated))["status"]
        == "verified"
    )
