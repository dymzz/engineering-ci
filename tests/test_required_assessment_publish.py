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

REAL_WORKFLOW_REF = "dymzz/engineering-ci/.github/workflows/ci.yml@refs/heads/main"
WORKFLOW_SHA = "a" * 40


def actions_env(**changes):
    env = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_REPOSITORY": "dymzz/engineering-ci",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_SHA": "2" * 40,
        "GITHUB_WORKFLOW_REF": REAL_WORKFLOW_REF,
        "GITHUB_WORKFLOW_SHA": WORKFLOW_SHA,
        "GITHUB_RUN_ID": "44003289402",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SERVER_URL": "https://github.com",
    }
    env.update(changes)
    return env


def publisher_approval(**changes):
    approval = {
        "status": "active",
        "repository": "dymzz/engineering-ci",
        "workflow_repository": "dymzz/engineering-ci",
        "workflow_path": ".github/workflows/ci.yml",
        "workflow_sha": WORKFLOW_SHA,
        "allowed_events": ["pull_request", "push", "merge_group", "workflow_dispatch"],
        "approved_evaluator_refs": [copy.deepcopy(EVALUATOR_REF)],
        "approved_policy_refs": [copy.deepcopy(POLICY_REF)],
        "authority_ref": copy.deepcopy(AUTHORITY_REF),
        "valid_from": None,
        "valid_to": None,
    }
    approval.update(changes)
    return approval


def production_context(**changes):
    context = publication_context(
        trust_basis="protected_workflow",
        workflow_ref=REAL_WORKFLOW_REF,
        workflow_sha=WORKFLOW_SHA,
    )
    context.update(changes)
    return context


def test_parse_workflow_ref_requires_owner_repo_prefix():
    parsed = rap.parse_workflow_ref(REAL_WORKFLOW_REF)
    assert parsed["workflow_repository"] == "dymzz/engineering-ci"
    assert parsed["workflow_path"] == ".github/workflows/ci.yml"
    assert parsed["workflow_git_ref"] == "refs/heads/main"
    with pytest.raises(rap.RequiredAssessmentPublishError) as raised:
        rap.parse_workflow_ref(".github/workflows/ci.yml@refs/heads/main")
    assert raised.value.code == "actions_workflow_ref_invalid"


def test_actions_runtime_identity_requires_actions():
    with pytest.raises(rap.RequiredAssessmentPublishError) as raised:
        rap.actions_runtime_identity({"GITHUB_ACTIONS": "false"})
    assert raised.value.code == "actions_runtime_required"


def test_actions_runtime_identity_parses_real_workflow_ref():
    identity = rap.actions_runtime_identity(actions_env())
    assert identity["workflow_repository"] == "dymzz/engineering-ci"
    assert identity["workflow_path"] == ".github/workflows/ci.yml"
    assert identity["workflow_ref"] == REAL_WORKFLOW_REF
    assert identity["run_id"] == 44003289402


def test_actions_runtime_rejects_workflow_repository_mismatch():
    with pytest.raises(rap.RequiredAssessmentPublishError) as raised:
        rap.actions_runtime_identity(
            actions_env(
                GITHUB_WORKFLOW_REF=(
                    "other/repo/.github/workflows/ci.yml@refs/heads/main"
                )
            )
        )
    assert raised.value.code == "actions_workflow_repository_mismatch"


def test_publication_context_from_actions_binds_target():
    context = rap.publication_context_from_actions(
        evaluator_ref=copy.deepcopy(EVALUATOR_REF),
        policy_ref=copy.deepcopy(POLICY_REF),
        job_name="required-assessment",
        publisher_approval=publisher_approval(),
        environ=actions_env(),
    )
    normalized = rac.normalize_publication_context(context)
    assert normalized["trust_basis"] == "protected_workflow"
    assert normalized["workflow_ref"] == REAL_WORKFLOW_REF
    assert normalized["target_binding"]["kind"] == "event_head"
    assert normalized["details_url"].endswith("/actions/runs/44003289402")


def test_publication_context_rejects_unapproved_workflow_path_by_default():
    with pytest.raises(rap.RequiredAssessmentPublishError) as raised:
        rap.publication_context_from_actions(
            evaluator_ref=copy.deepcopy(EVALUATOR_REF),
            policy_ref=copy.deepcopy(POLICY_REF),
            job_name="required-assessment",
            publisher_approval=publisher_approval(
                workflow_path=".github/workflows/attacker.yml",
                workflow_sha=WORKFLOW_SHA,
            ),
            environ=actions_env(
                GITHUB_WORKFLOW_REF=(
                    "dymzz/engineering-ci/.github/workflows/attacker.yml@refs/heads/main"
                )
            ),
        )
    assert raised.value.code == "actions_workflow_path_not_allowed"


def test_workflow_publish_authority_accepts_matching_runtime():
    assessment = passed_assessment()
    context = production_context()
    publication = rac.build_check_publication(assessment, context)
    approval = publisher_approval()
    authority = rap.workflow_publish_authority(
        publisher_approval=approval,
        environ=actions_env(),
    )
    normalized = rap.normalize_publisher_approval(approval)
    authorization = rac.publishing_authorization_for(
        publication, authority_ref=normalized["authority_ref"]
    )
    assert authority(authorization, publication) is True


def test_workflow_publish_authority_rejects_run_mismatch():
    assessment = passed_assessment()
    context = production_context()
    publication = rac.build_check_publication(assessment, context)
    approval = publisher_approval()
    authority = rap.workflow_publish_authority(
        publisher_approval=approval,
        environ=actions_env(GITHUB_RUN_ID="999"),
    )
    normalized = rap.normalize_publisher_approval(approval)
    authorization = rac.publishing_authorization_for(
        publication, authority_ref=normalized["authority_ref"]
    )
    with pytest.raises(rap.RequiredAssessmentPublishError) as raised:
        authority(authorization, publication)
    assert raised.value.code == "publish_authority_runtime_run_id_mismatch"


def test_workflow_publish_authority_rejects_unapproved_evaluator():
    assessment = passed_assessment()
    context = production_context()
    publication = rac.build_check_publication(assessment, context)
    # Internally consistent Actions + publication + authorization, but the
    # independent approval does not list this evaluator revision.
    foreign_evaluator = {
        "name": "engineering-ci/required-assessment-check",
        "version": "pr-modified",
        "sha256": "f" * 64,
    }
    approval = publisher_approval(approved_evaluator_refs=[foreign_evaluator])
    authority = rap.workflow_publish_authority(
        publisher_approval=approval,
        environ=actions_env(),
    )
    normalized = rap.normalize_publisher_approval(approval)
    authorization = rac.publishing_authorization_for(
        publication, authority_ref=normalized["authority_ref"]
    )
    with pytest.raises(rap.RequiredAssessmentPublishError) as raised:
        authority(authorization, publication)
    assert raised.value.code == "publish_authority_evaluator_not_approved"


def test_workflow_publish_authority_rejects_unapproved_policy():
    assessment = passed_assessment()
    context = production_context()
    publication = rac.build_check_publication(assessment, context)
    foreign_policy = {
        "name": "org/required-assessment-policy",
        "version": "pr-modified",
        "sha256": "1" * 64,
    }
    approval = publisher_approval(approved_policy_refs=[foreign_policy])
    authority = rap.workflow_publish_authority(
        publisher_approval=approval,
        environ=actions_env(),
    )
    normalized = rap.normalize_publisher_approval(approval)
    authorization = rac.publishing_authorization_for(
        publication, authority_ref=normalized["authority_ref"]
    )
    with pytest.raises(rap.RequiredAssessmentPublishError) as raised:
        authority(authorization, publication)
    assert raised.value.code == "publish_authority_policy_not_approved"


def test_workflow_publish_authority_rejects_revoked_approval():
    with pytest.raises(rap.RequiredAssessmentPublishError) as raised:
        rap.workflow_publish_authority(
            publisher_approval=publisher_approval(status="revoked"),
            environ=actions_env(),
        )
    assert raised.value.code == "publisher_approval_revoked"


def test_authorize_and_publish_success_posts_when_approved():
    assessment = passed_assessment()
    context = production_context()
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
        publisher_approval=publisher_approval(),
        environ=actions_env(),
        github=FakeGitHub("dymzz/engineering-ci"),
    )
    assert result["id"] == 77
    assert posted[0]["name"] == rac.CHECK_NAME
    assert posted[0]["conclusion"] == "success"


def test_authorize_and_publish_failure_requires_publisher_identity():
    assessment = blocked_assessment()
    context = production_context()
    publication = rac.build_check_publication(assessment, context)
    with pytest.raises(rap.RequiredAssessmentPublishError) as raised:
        rap.authorize_and_publish(
            publication,
            publisher_approval=publisher_approval(),
            environ={"GITHUB_ACTIONS": "false"},
            github=object(),
        )
    assert raised.value.code == "actions_runtime_required"


def test_authorize_and_publish_failure_posts_when_publisher_verified():
    assessment = blocked_assessment()
    context = production_context()
    publication = rac.build_check_publication(assessment, context)
    posted = []

    class FakeGitHub:
        def __init__(self, repository):
            self.repository = repository

        def __call__(self, path, method="GET", data=None):
            posted.append(data)
            return {"id": 88}

    result = rap.authorize_and_publish(
        publication,
        publisher_approval=publisher_approval(),
        environ=actions_env(),
        github=FakeGitHub("dymzz/engineering-ci"),
    )
    assert result["id"] == 88
    assert posted[0]["conclusion"] == "failure"


def test_accept_authority_fixture_still_works_for_r2_tests():
    assert accept_authority({"assessment_ref": "x"}, {"assessment_ref": "x"}) is True
    assert target_binding()["kind"] == "candidate_commit"
