"""#61 G2 trusted Workflow publish authority contract."""

from __future__ import annotations

import copy

import pytest
from test_required_assessment_check import (
    AUTHORITY_REF,
    EVALUATOR_REF,
    POLICY_REF,
    SOURCE_APP_ID,
    RecordingGitHub,
    accept_authority,
    blocked_assessment,
    passed_assessment,
    publication_context,
    scoped_context,
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
        "source_app_id": SOURCE_APP_ID,
        "allowed_events": ["pull_request", "push", "merge_group", "workflow_dispatch"],
        "approved_evaluator_refs": [copy.deepcopy(EVALUATOR_REF)],
        "approved_policy_refs": [copy.deepcopy(POLICY_REF)],
        "authority_ref": copy.deepcopy(AUTHORITY_REF),
        "valid_from": None,
        "valid_to": None,
    }
    approval.update(changes)
    return approval


def execution_identity(**changes):
    identity = {
        "workflow_sha": WORKFLOW_SHA,
        "evaluator_ref": copy.deepcopy(EVALUATOR_REF),
        "policy_ref": copy.deepcopy(POLICY_REF),
    }
    identity.update(changes)
    return identity


def accept_approval(context):
    assert context["approval"]["authority_ref"]["sha256"] == AUTHORITY_REF["sha256"]
    return True


def reject_approval(context):
    return False


def production_context(assessment=None, **changes):
    options = {
        "trust_basis": "protected_workflow",
        "workflow_ref": REAL_WORKFLOW_REF,
        "workflow_sha": WORKFLOW_SHA,
        **changes,
    }
    if assessment is None:
        return publication_context(**options)
    return scoped_context(assessment, **options)


def test_parse_workflow_ref_requires_owner_repo_prefix():
    parsed = rap.parse_workflow_ref(REAL_WORKFLOW_REF)
    assert parsed["workflow_repository"] == "dymzz/engineering-ci"
    assert parsed["workflow_path"] == ".github/workflows/ci.yml"
    with pytest.raises(rap.RequiredAssessmentPublishError) as raised:
        rap.parse_workflow_ref(".github/workflows/ci.yml@refs/heads/main")
    assert raised.value.code == "actions_workflow_ref_invalid"


def test_actions_runtime_identity_parses_real_workflow_ref():
    identity = rap.actions_runtime_identity(actions_env())
    assert identity["workflow_ref"] == REAL_WORKFLOW_REF
    assert identity["workflow_path"] == ".github/workflows/ci.yml"


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
        approval_authority=accept_approval,
        execution_identity=execution_identity(),
        environ=actions_env(),
    )
    normalized = rac.normalize_publication_context(context)
    assert normalized["workflow_ref"] == REAL_WORKFLOW_REF
    assert normalized["details_url"].endswith("/actions/runs/44003289402")


def test_publication_context_rejects_unapproved_workflow_path_by_default():
    with pytest.raises(rap.RequiredAssessmentPublishError) as raised:
        rap.publication_context_from_actions(
            evaluator_ref=copy.deepcopy(EVALUATOR_REF),
            policy_ref=copy.deepcopy(POLICY_REF),
            job_name="required-assessment",
            publisher_approval=publisher_approval(
                workflow_path=".github/workflows/attacker.yml"
            ),
            approval_authority=accept_approval,
            execution_identity=execution_identity(),
            environ=actions_env(
                GITHUB_WORKFLOW_REF=(
                    "dymzz/engineering-ci/.github/workflows/attacker.yml@refs/heads/main"
                )
            ),
        )
    assert raised.value.code == "actions_workflow_path_not_allowed"


def test_workflow_publish_authority_accepts_verified_approval_and_execution():
    assessment = passed_assessment()
    context = production_context()
    publication = rac.build_check_publication(assessment, context)
    approval = publisher_approval()
    authority = rap.workflow_publish_authority(
        publisher_approval=approval,
        approval_authority=accept_approval,
        execution_identity=execution_identity(),
        environ=actions_env(),
    )
    normalized = rap.normalize_publisher_approval(approval)
    authorization = rac.publishing_authorization_for(
        publication,
        authority_ref=normalized["authority_ref"],
        source_app_id=SOURCE_APP_ID,
    )
    assert authority(authorization, publication, RecordingGitHub()) is True


def test_workflow_publish_authority_rejects_caller_minted_approval():
    assessment = passed_assessment()
    context = production_context()
    publication = rac.build_check_publication(assessment, context)
    # Structurally valid and runtime-consistent, but authority rejects the claim.
    with pytest.raises(rap.RequiredAssessmentPublishError) as raised:
        rap.workflow_publish_authority(
            publisher_approval=publisher_approval(),
            approval_authority=reject_approval,
            execution_identity=execution_identity(),
            environ=actions_env(),
        )
    assert raised.value.code == "approval_authority_rejected"
    with pytest.raises(rap.RequiredAssessmentPublishError) as raised:
        rap.authorize_and_publish(
            publication,
            publisher_approval=publisher_approval(),
            approval_authority=reject_approval,
            execution_identity=execution_identity(),
            environ=actions_env(),
            github=object(),
        )
    assert raised.value.code == "approval_authority_rejected"


def test_workflow_publish_authority_rejects_missing_approval_authority():
    with pytest.raises(rap.RequiredAssessmentPublishError) as raised:
        rap.workflow_publish_authority(
            publisher_approval=publisher_approval(),
            approval_authority=None,
            execution_identity=execution_identity(),
            environ=actions_env(),
        )
    assert raised.value.code == "approval_authority_required"


def test_workflow_publish_authority_rejects_mismatched_actual_evaluator():
    # Publication may declare an approved evaluator, but actual execution used
    # another revision — that must fail closed before POST.
    foreign = {
        "name": "engineering-ci/required-assessment-check",
        "version": "pr-modified",
        "sha256": "f" * 64,
    }
    with pytest.raises(rap.RequiredAssessmentPublishError) as raised:
        rap.workflow_publish_authority(
            publisher_approval=publisher_approval(),
            approval_authority=accept_approval,
            execution_identity=execution_identity(evaluator_ref=foreign),
            environ=actions_env(),
        )
    assert raised.value.code == "execution_identity_evaluator_not_approved"


def test_workflow_publish_authority_accepts_authorized_new_revision():
    new_evaluator = {
        "name": "engineering-ci/required-assessment-check",
        "version": "2",
        "sha256": "9" * 64,
    }
    assessment = passed_assessment()
    context = production_context(evaluator_ref=copy.deepcopy(new_evaluator))
    publication = rac.build_check_publication(assessment, context)
    approval = publisher_approval(
        approved_evaluator_refs=[copy.deepcopy(new_evaluator)]
    )
    authority = rap.workflow_publish_authority(
        publisher_approval=approval,
        approval_authority=accept_approval,
        execution_identity=execution_identity(
            evaluator_ref=copy.deepcopy(new_evaluator)
        ),
        environ=actions_env(),
    )
    normalized = rap.normalize_publisher_approval(approval)
    authorization = rac.publishing_authorization_for(
        publication,
        authority_ref=normalized["authority_ref"],
        source_app_id=SOURCE_APP_ID,
    )
    assert authority(authorization, publication, RecordingGitHub()) is True


def test_authorize_and_publish_success_posts_when_verified():
    assessment = passed_assessment()
    context = production_context(assessment)
    publication = rac.build_check_publication(assessment, context)
    github = RecordingGitHub()

    result = rap.authorize_and_publish(
        publication,
        publisher_approval=publisher_approval(),
        approval_authority=accept_approval,
        execution_identity=execution_identity(),
        assessment_authority=lambda ref, candidate: assessment,
        environ=actions_env(),
        github=github,
    )
    assert result["id"] == 99
    assert github.posts == [("check-runs", "POST", publication["request"])]


def test_authorize_and_publish_failure_requires_verified_publisher():
    assessment = blocked_assessment()
    context = production_context()
    publication = rac.build_check_publication(assessment, context)
    with pytest.raises(rap.RequiredAssessmentPublishError) as raised:
        rap.authorize_and_publish(
            publication,
            publisher_approval=publisher_approval(),
            approval_authority=reject_approval,
            environ=actions_env(),
            github=object(),
        )
    assert raised.value.code == "approval_authority_rejected"


def test_authorize_and_publish_failure_posts_when_publisher_verified():
    assessment = blocked_assessment()
    context = production_context(assessment)
    publication = rac.build_check_publication(assessment, context)
    github = RecordingGitHub()

    result = rap.authorize_and_publish(
        publication,
        publisher_approval=publisher_approval(),
        approval_authority=accept_approval,
        environ=actions_env(),
        github=github,
    )
    assert result["id"] == 99
    assert github.posts == [("check-runs", "POST", publication["request"])]


@pytest.mark.parametrize("build_assessment", [passed_assessment, blocked_assessment])
def test_publish_respects_narrower_event_policy_for_both_conclusions(build_assessment):
    assessment = build_assessment()
    publication = rac.build_check_publication(
        assessment, production_context(assessment)
    )
    github = RecordingGitHub()
    with pytest.raises(
        rap.RequiredAssessmentPublishError, match="actions_event_unsupported"
    ):
        rap.authorize_and_publish(
            publication,
            publisher_approval=publisher_approval(),
            approval_authority=accept_approval,
            execution_identity=execution_identity(),
            environ=actions_env(),
            allowed_events={"push"},
            github=github,
        )
    assert github.posts == []


def test_approval_authority_can_reject_the_actual_publishing_client():
    assessment = blocked_assessment()
    publication = rac.build_check_publication(
        assessment, production_context(assessment)
    )
    github = RecordingGitHub()

    def reject_client(context):
        assert context["approval"]["source_app_id"] == SOURCE_APP_ID
        assert (
            context["publication"]["publication_ref"] == publication["publication_ref"]
        )
        assert context["github"] is github
        return False

    with pytest.raises(
        rap.RequiredAssessmentPublishError, match="approval_authority_rejected"
    ):
        rap.authorize_and_publish(
            publication,
            publisher_approval=publisher_approval(),
            approval_authority=reject_client,
            environ=actions_env(),
            github=github,
        )
    assert github.posts == []


def test_accept_authority_fixture_still_works_for_r2_tests():
    assert (
        accept_authority({"assessment_ref": "x"}, {"assessment_ref": "x"}, None) is True
    )
    assert target_binding()["kind"] == "candidate_commit"
