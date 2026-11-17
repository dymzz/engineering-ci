"""#61 R4 Release reconciliation contract tests."""

from __future__ import annotations

import copy

import pytest
from test_required_assessment import (
    assess,
    check_obligation,
    make_entry,
    make_profile,
    make_projection,
    make_universe,
)
from test_required_assessment_check import blocked_assessment, passed_assessment

from engineering_ci import required_assessment_release as rar
from engineering_ci.release import registry as release_registry

HEAD = "2" * 40
TAG = "v0.9.0"
REPO = "dymzz/engineering-ci"
EVALUATED_AT = "2026-11-17T12:00:00+00:00"


def make_manifest(**changes):
    checks = {}
    for key in set(release_registry.CHECKS) | set(
        release_registry.DAGGER_POLICY_CHECKS
    ):
        record = {
            "required": True,
            "status": "passed",
            "job": "job",
            "job_id": 1,
            "step": "step",
            "job_conclusion": "success",
            "step_conclusion": "success",
        }
        if key in release_registry.DAGGER_POLICY_CHECKS:
            record["dagger_check"] = release_registry.DAGGER_POLICY_CHECKS[key]
        checks[key] = record
    checks["core_coverage_threshold"] = {
        "required": False,
        "status": "not_enabled",
        "reason": "Core coverage is exported; no core coverage threshold is configured.",
    }
    checks["consumer_failure_probe"] = {
        "required": False,
        "status": "not_enabled",
        "reason": "The smoke-only Ruff rejection probe is disabled in core releases.",
    }
    value = {
        "schema_version": 1,
        "repository": REPO,
        "release": {
            "version": "0.9.0",
            "tag": TAG,
            "commit_sha": HEAD,
            "release_url": None,
        },
        "workflow": {
            "run_id": 100,
            "run_attempt": 1,
            "run_url": f"https://github.com/{REPO}/actions/runs/100",
            "event": "workflow_dispatch",
            "path": ".github/workflows/release.yml",
        },
        "mode": "validation",
        "checks": checks,
        "tools": [],
        "artifacts": [
            {
                "name": name,
                "id": index + 1,
                "digest": "sha256:" + ("a" * 64),
                "url": (
                    f"https://api.github.com/repos/{REPO}/actions/artifacts/{index + 1}"
                ),
            }
            for index, name in enumerate(sorted(release_registry.ARTIFACT_NAMES))
        ],
        "result": "passed",
    }
    value.update(changes)
    return value


def publish_context(**changes):
    value = {
        "repository": REPO,
        "tag": TAG,
        "head_sha": HEAD,
        "mode": "validation",
        "phase": "pre_publish",
        "evaluated_at": EVALUATED_AT,
        "run_id": 100,
        "run_attempt": 1,
    }
    value.update(changes)
    return value


def accept_policy(_context):
    return {"accepted": True, "status": "authorized"}


def reject_policy(_context):
    return {"accepted": False, "status": "revoked"}


def unverified_policy(_context):
    return {"accepted": False, "status": "unverified"}


def reconcile(assessment, manifest=None, **changes):
    kwargs = {
        "publish_context": publish_context(),
        "policy_snapshot_authority": accept_policy,
        "artifact_requirements": sorted(release_registry.ARTIFACT_NAMES),
        "contract_sample": False,
    }
    kwargs.update(changes)
    return rar.reconcile_release_assessment(
        assessment, manifest if manifest is not None else make_manifest(), **kwargs
    )


def test_passed_assessment_and_passed_manifest_authorize_release():
    assessment = passed_assessment(contract_sample=False)
    assert assessment["status"] == "passed"
    assert assessment["phase"] == "pre_publish"
    assert assessment["candidate"]["commit"] == HEAD

    result = reconcile(assessment)
    assert result["status"] == "verified"
    assert result["release_authorized"] is True
    assert result["policy_snapshot_status"] == "authorized"
    assert result["manifest_result"] == "passed"
    assert result["gaps"] == []
    assert all(
        item["trusted"] is False and item["evidence_admission"] == "not_evaluated"
        for item in result["fixed_manifest_checks"].values()
    )
    assert rar.validate_release_reconciliation(result) is result


def test_blocked_assessment_cannot_authorize_release():
    assessment = blocked_assessment()
    result = reconcile(assessment)
    assert result["status"] == "rejected"
    assert result["release_authorized"] is False
    assert "release_assessment_blocked" in result["reasons"]
    assert result["gaps"]


def test_failed_manifest_fixed_checks_block_without_trust_rewrite():
    assessment = passed_assessment(contract_sample=False)
    manifest = make_manifest()
    manifest["checks"]["ruff_lint"]["status"] = "failed"
    manifest["checks"]["ruff_lint"]["job_conclusion"] = "failure"
    manifest["checks"]["ruff_lint"]["step_conclusion"] = "failure"
    manifest["result"] = "failed"

    result = reconcile(assessment, manifest)
    assert result["status"] == "rejected"
    assert result["release_authorized"] is False
    assert "release_manifest_result_failed" in result["reasons"]
    assert "release_manifest_fixed_checks_incomplete" in result["reasons"]
    assert result["fixed_manifest_checks"]["ruff_lint"]["status"] == "failed"
    assert result["fixed_manifest_checks"]["ruff_lint"]["trusted"] is False


def test_same_candidate_sha_does_not_preserve_revoked_policy():
    assessment = passed_assessment(contract_sample=False)
    result = reconcile(assessment, policy_snapshot_authority=reject_policy)
    assert result["status"] == "rejected"
    assert result["release_authorized"] is False
    assert "policy_snapshot_revoked" in result["reasons"]
    assert result["policy_snapshot_status"] == "rejected"
    assert result["candidate"]["commit"] == HEAD


def test_missing_policy_authority_is_unverified_not_authorized():
    assessment = passed_assessment(contract_sample=False)
    result = reconcile(assessment, policy_snapshot_authority=None)
    assert result["status"] == "unverified"
    assert result["release_authorized"] is False
    assert "policy_snapshot_authority_missing" in result["reasons"]


def test_unverified_policy_authority_is_unverified():
    assessment = passed_assessment(contract_sample=False)
    result = reconcile(assessment, policy_snapshot_authority=unverified_policy)
    assert result["status"] == "unverified"
    assert result["release_authorized"] is False
    assert "policy_snapshot_unverified" in result["reasons"]


def test_commit_mismatch_between_assessment_and_manifest_is_rejected():
    assessment = passed_assessment(contract_sample=False)
    result = reconcile(
        assessment,
        publish_context=publish_context(head_sha="3" * 40),
        manifest=make_manifest(
            release={
                "version": "0.9.0",
                "tag": TAG,
                "commit_sha": "3" * 40,
                "release_url": None,
            }
        ),
    )
    assert result["status"] == "rejected"
    assert "release_assessment_commit_mismatch" in result["reasons"]
    assert result["release_authorized"] is False


def test_phase_mismatch_is_rejected():
    assessment = passed_assessment(contract_sample=False)
    result = reconcile(
        assessment, publish_context=publish_context(phase="post_publish")
    )
    assert result["status"] == "rejected"
    assert "release_assessment_phase_mismatch" in result["reasons"]


def test_missing_required_artifact_is_rejected():
    assessment = passed_assessment(contract_sample=False)
    manifest = make_manifest(artifacts=[])
    result = reconcile(
        assessment,
        manifest,
        artifact_requirements=["pytest-report"],
    )
    assert result["status"] == "rejected"
    assert "release_required_artifacts_missing" in result["reasons"]
    assert any(
        gap["ref"] == "pytest-report" and gap["reason"] == "required_artifact_missing"
        for gap in result["gaps"]
    )


def test_check_binding_keeps_unsatisfied_assessment_as_gap():
    profile = make_profile(phase="pre_publish")
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(
        profile, projection, [obligation], contract_sample=False, phase="pre_publish"
    )
    # No admission → check instance unverified / unmet path.
    assessment = assess(universe, projection, admission=None)
    assert assessment["status"] == "blocked"

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


def test_contract_sample_cannot_authorize_production_release():
    assessment = passed_assessment(contract_sample=True)
    result = reconcile(assessment, contract_sample=True)
    assert result["status"] == "verified"
    assert result["release_authorized"] is False
    assert "contract_sample_inputs_not_production_release" in result["reasons"]
    assert (
        "contract_sample_cannot_authorize_production_release" in result["limitations"]
    )


def test_future_only_profile_change_keeps_authorized_snapshot():
    """Authorized snapshot may continue; authority decides, SHA alone does not."""
    assessment = passed_assessment(contract_sample=False)
    calls = []

    def still_authorized(context):
        calls.append(context["snapshot"]["profile_revision"])
        return {"accepted": True, "status": "authorized"}

    result = reconcile(assessment, policy_snapshot_authority=still_authorized)
    assert result["status"] == "verified"
    assert result["release_authorized"] is True
    assert calls == [assessment["inputs"]["profile"]["profile_revision"]]


def test_tampered_reconciliation_digest_is_rejected():
    assessment = passed_assessment(contract_sample=False)
    result = reconcile(assessment)
    tampered = copy.deepcopy(result)
    tampered["release_authorized"] = False
    tampered["reconciliation_ref"] = rar._digest(
        {key: value for key, value in tampered.items() if key != "reconciliation_ref"}
    )
    # Changing release_authorized without updating dependent fields still
    # recomputes digest; force a payload/digest mismatch instead.
    tampered["reasons"] = list(tampered["reasons"]) + ["forged"]
    with pytest.raises(
        rar.RequiredAssessmentReleaseError, match="release_reconciliation_ref_mismatch"
    ):
        rar.validate_release_reconciliation(tampered)
