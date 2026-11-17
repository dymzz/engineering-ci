"""#61 G2 trusted Workflow publish authority contract."""

from __future__ import annotations

import copy

import pytest
from test_required_assessment_check import (
    AUTHORITY_REF,
    EVALUATOR_REF,
    POLICY_REF,
    accept_authority,
    blocked_assessment,
    passed_assessment,
    publication_context,
    target_binding,
)

from engineering_ci import required_assessment_check as rac
from engineering_ci import required_assessment_publish as rap


def actions_env(**changes):
    env = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_REPOSITORY": "dymzz/engineering-ci",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_SHA": "2" * 40,
        "GITHUB_WORKFLOW_REF": ".github/workflows/ci.yml@refs/heads/main",
        "GITHUB_WORKFLOW_SHA": "a" * 40,
        "GITHUB_RUN_ID": "44003289402",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SERVER_URL": "https://github.com",
    }
    env.update(changes)
    return env


def test_actions_runtime_identity_requires_actions():
    with pytest.raises(rap.RequiredAssessmentPublishError) as raised:
        rap.actions_runtime_identity({"GITHUB_ACTIONS": "false"})
    assert raised.value.code == "actions_runtime_required"


def test_actions_runtime_identity_parses_workflow_ref():
    identity = rap.actions_runtime_identity(actions_env())
    assert identity["workflow_path"] == ".github/workflows/ci.yml"
    assert identity["run_id"] == 44003289402
    assert identity["run_attempt"] == 1
    assert identity["head_sha"] == "2" * 40


def test_publication_context_from_actions_binds_target():
    context = rap.publication_context_from_actions(
        evaluator_ref=copy.deepcopy(EVALUATOR_REF),
        policy_ref=copy.deepcopy(POLICY_REF),
        job_name="required-assessment",
        environ=actions_env(),
    )
    normalized = rac.normalize_publication_context(context)
    assert normalized["trust_basis"] == "protected_workflow"
    assert normalized["workflow_ref"].startswith(".github/workflows/ci.yml@")
    assert normalized["target_binding"]["kind"] == "event_head"
    assert normalized["target_binding"]["commit"] == "2" * 40
    assert normalized["details_url"].endswith("/actions/runs/44003289402")


def test_publication_context_rejects_unlisted_workflow_path():
    with pytest.raises(rap.RequiredAssessmentPublishError) as raised:
        rap.publication_context_from_actions(
            evaluator_ref=copy.deepcopy(EVALUATOR_REF),
            job_name="required-assessment",
            environ=actions_env(
                GITHUB_WORKFLOW_REF=".github/workflows/attacker.yml@refs/heads/main"
            ),
        )
    assert raised.value.code == "actions_workflow_path_not_allowed"


def test_workflow_publish_authority_accepts_matching_runtime():
    assessment = passed_assessment()
    context = publication_context(trust_basis="protected_workflow")
    publication = rac.build_check_publication(assessment, context)
    authority = rap.workflow_publish_authority(
        authority_ref=copy.deepcopy(AUTHORITY_REF),
        environ=actions_env(),
    )
    authorization = rac.publishing_authorization_for(
        publication, authority_ref=copy.deepcopy(AUTHORITY_REF)
    )
    assert authority(authorization, publication) is True


def test_workflow_publish_authority_rejects_run_mismatch():
    assessment = passed_assessment()
    context = publication_context(trust_basis="protected_workflow")
    publication = rac.build_check_publication(assessment, context)
    authority = rap.workflow_publish_authority(
        authority_ref=copy.deepcopy(AUTHORITY_REF),
        environ=actions_env(GITHUB_RUN_ID="999"),
    )
    authorization = rac.publishing_authorization_for(
        publication, authority_ref=copy.deepcopy(AUTHORITY_REF)
    )
    with pytest.raises(rap.RequiredAssessmentPublishError) as raised:
        authority(authorization, publication)
    assert raised.value.code == "publish_authority_runtime_run_id_mismatch"


def test_workflow_publish_authority_rejects_forged_authority_ref():
    assessment = passed_assessment()
    context = publication_context(trust_basis="protected_workflow")
    publication = rac.build_check_publication(assessment, context)
    authority = rap.workflow_publish_authority(
        authority_ref=copy.deepcopy(AUTHORITY_REF),
        environ=actions_env(),
    )
    forged = copy.deepcopy(AUTHORITY_REF)
    forged["sha256"] = "e" * 64
    authorization = rac.publishing_authorization_for(publication, authority_ref=forged)
    with pytest.raises(rap.RequiredAssessmentPublishError) as raised:
        authority(authorization, publication)
    assert raised.value.code == "publish_authority_ref_mismatch"


def test_workflow_publish_authority_rejects_contract_sample_publication():
    assessment = passed_assessment(contract_sample=True)
    context = publication_context(
        trust_basis="contract_sample",
        allow_contract_sample_publication=True,
    )
    publication = rac.build_check_publication(assessment, context)
    authority = rap.workflow_publish_authority(
        authority_ref=copy.deepcopy(AUTHORITY_REF),
        environ=actions_env(),
    )
    # Sample publications are not success-authorized via production path;
    # authority still refuses if invoked directly.
    with pytest.raises(rap.RequiredAssessmentPublishError) as raised:
        authority(
            {
                "authority_ref": copy.deepcopy(AUTHORITY_REF),
                "repository": publication["repository"],
                "head_sha": publication["head_sha"],
                "event_name": publication["event_name"],
                "workflow_ref": publication["workflow_ref"],
                "workflow_sha": publication["workflow_sha"],
                "run_id": publication["run_id"],
                "run_attempt": publication["run_attempt"],
            },
            publication,
        )
    assert raised.value.code in {
        "publish_authority_non_production_check",
        "publish_authority_contract_sample_refused",
        "publish_authority_trust_basis_not_production",
    }


def test_authorize_and_publish_success_posts_when_runtime_matches():
    assessment = passed_assessment()
    context = publication_context(trust_basis="protected_workflow")
    publication = rac.build_check_publication(assessment, context)
    posted = []

    class FakeGitHub:
        def __init__(self, repository):
            self.repository = repository

        def __call__(self, path, method="GET", data=None):
            assert path == "check-runs"
            assert method == "POST"
            posted.append(data)
            return {"id": 77}

    result = rap.authorize_and_publish(
        publication,
        authority_ref=copy.deepcopy(AUTHORITY_REF),
        environ=actions_env(),
        github=FakeGitHub("dymzz/engineering-ci"),
    )
    assert result["id"] == 77
    assert posted[0]["name"] == rac.CHECK_NAME
    assert posted[0]["conclusion"] == "success"


def test_authorize_and_publish_failure_skips_authority():
    assessment = blocked_assessment()
    context = publication_context(trust_basis="protected_workflow")
    publication = rac.build_check_publication(assessment, context)
    posted = []

    class FakeGitHub:
        def __init__(self, repository):
            self.repository = repository

        def __call__(self, path, method="GET", data=None):
            posted.append(data)
            return {"id": 88}

    # Failure publications do not require Actions runtime authority.
    result = rap.authorize_and_publish(
        publication,
        authority_ref=copy.deepcopy(AUTHORITY_REF),
        environ={"GITHUB_ACTIONS": "false"},
        github=FakeGitHub("dymzz/engineering-ci"),
    )
    assert result["id"] == 88
    assert posted[0]["conclusion"] == "failure"


def test_accept_authority_fixture_still_works_for_r2_tests():
    # Sanity: G2 helpers do not break the R2 stub used by other tests.
    assert accept_authority({"assessment_ref": "x"}, {"assessment_ref": "x"}) is True
    assert target_binding()["kind"] == "candidate_commit"
