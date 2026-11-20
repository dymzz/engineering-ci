"""#61 R2 GitHub Check publication contract for Required Assessment."""

from __future__ import annotations

import copy

import pytest
from test_required_assessment import (
    EVENT,
    assess,
    check_obligation,
    coverage,
    historical_coverage,
    make_admission,
    make_entry,
    make_profile,
    make_projection,
    make_receipt,
    make_universe,
)

from engineering_ci import required_assessment_check as rac

EVALUATOR_REF = {
    "name": "engineering-ci/required-assessment-check",
    "version": "1",
    "sha256": "b" * 64,
}
POLICY_REF = {
    "name": "org/required-assessment-policy",
    "version": "1",
    "sha256": "c" * 64,
}
AUTHORITY_REF = {
    "name": "org/protected-workflow-publisher",
    "version": "1",
    "sha256": "d" * 64,
}


SOURCE_APP_ID = 42
CANDIDATE = {"commit": "2" * 40, "tree": "3" * 40}


class RecordingGitHub:
    repository = "dymzz/engineering-ci"

    def __init__(self, *, app_id=SOURCE_APP_ID):
        self.posts = []
        self.app_id = app_id

    def __call__(self, path, method="GET", data=None):
        self.posts.append((path, method, data))
        return {
            "id": 99,
            "app": {"id": self.app_id},
            **{key: data[key] for key in ("name", "head_sha", "status", "conclusion")},
            "external_id": data.get("external_id"),
        }


def target_binding(**changes):
    binding = {
        "kind": "candidate_commit",
        "commit": "2" * 40,
        "repository": "dymzz/engineering-ci",
        "event_name": "pull_request",
        "run_id": 44003289402,
        "run_attempt": 1,
        "tree": None,
    }
    binding.update(changes)
    return binding


def publication_context(**changes):
    context = {
        "repository": "dymzz/engineering-ci",
        "head_sha": "2" * 40,
        "event_name": "pull_request",
        "workflow_ref": ".github/workflows/ci.yml@refs/heads/main",
        "workflow_sha": "a" * 40,
        "run_id": 44003289402,
        "run_attempt": 1,
        "job_name": "required-assessment",
        "evaluator_ref": copy.deepcopy(EVALUATOR_REF),
        "policy_ref": copy.deepcopy(POLICY_REF),
        "trust_basis": "approved_evaluator",
        "allow_contract_sample_publication": False,
        "details_url": "https://github.com/dymzz/engineering-ci/actions/runs/1",
        "target_binding": target_binding(),
    }
    context.update(changes)
    if "target_binding" not in changes and (
        "head_sha" in changes
        or "event_name" in changes
        or "repository" in changes
        or "run_id" in changes
        or "run_attempt" in changes
    ):
        binding = target_binding()
        for key in ("head_sha", "event_name", "repository", "run_id", "run_attempt"):
            if key in changes:
                binding_key = "commit" if key == "head_sha" else key
                binding[binding_key] = changes[key]
        context["target_binding"] = binding
    return context


def accept_authority(authorization, publication, github):
    assert authorization["assessment_ref"] == publication["assessment_ref"]
    return True


def publishing_claim(publication):
    return rac.publishing_authorization_for(
        publication, authority_ref=AUTHORITY_REF, source_app_id=SOURCE_APP_ID
    )


def scoped_context(assessment, **changes):
    context = publication_context(**changes)
    context["target_binding"].update(
        tree=assessment["candidate"]["tree"],
        assessment_scope={
            key: copy.deepcopy(assessment[key])
            for key in ("context", "action", "target", "phase", "refs")
        },
    )
    return context


def reject_authority(authorization, publication, github):
    return False


def passed_assessment(*, contract_sample=False, include_candidate=True):
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(
        profile,
        projection,
        [obligation],
        contract_sample=contract_sample,
        include_candidate=include_candidate,
        candidate=CANDIDATE,
    )
    admission = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
        contract_sample=contract_sample,
    )
    return assess(universe, projection, admission=admission)


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
        candidate=CANDIDATE,
    )
    admission = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
        contract_sample=False,
    )
    return assess(universe, projection, admission=admission)


def test_passed_assessment_builds_success_under_approved_evaluator():
    assessment = passed_assessment(contract_sample=False)
    assert assessment["status"] == "passed"
    assert assessment["inputs"]["contract_sample"] is False

    publication = rac.build_check_publication(assessment, publication_context())

    assert publication["check_name"] == rac.CHECK_NAME
    assert publication["conclusion"] == "success"
    assert publication["status"] == "completed"
    assert publication["assessment_ref"] == assessment["assessment_ref"]
    assert publication["request"]["name"] == rac.CHECK_NAME
    assert publication["request"]["head_sha"] == "2" * 40
    assert publication["request"]["conclusion"] == "success"
    assert publication["request"]["external_id"] == assessment["assessment_ref"]
    assert rac.validate_publication(publication) is publication


def test_blocked_assessment_always_builds_failure():
    assessment = blocked_assessment()
    assert assessment["status"] == "blocked"

    publication = rac.build_check_publication(assessment, publication_context())

    assert publication["conclusion"] == "failure"
    assert publication["request"]["conclusion"] == "failure"
    assert "assessment_blocked" in publication["publication_reasons"]
    assert publication["assessment_status"] == "blocked"


def test_untrusted_pr_path_cannot_mint_success():
    assessment = passed_assessment(contract_sample=False)

    with pytest.raises(
        rac.RequiredAssessmentCheckError, match="production_success_trust_unproven"
    ):
        rac.build_check_publication(
            assessment, publication_context(trust_basis="untrusted_pr_path")
        )


def test_contract_sample_uses_non_required_name_and_cannot_production_post():
    assessment = passed_assessment(contract_sample=True)
    assert assessment["inputs"]["contract_sample"] is True

    with pytest.raises(
        rac.RequiredAssessmentCheckError, match="contract_sample_success_refused"
    ):
        rac.build_check_publication(assessment, publication_context())

    publication = rac.build_check_publication(
        assessment,
        publication_context(
            trust_basis="contract_sample",
            allow_contract_sample_publication=True,
        ),
    )
    assert publication["conclusion"] == "success"
    assert publication["contract_sample"] is True
    assert publication["check_name"] == rac.SAMPLE_CHECK_NAME
    assert publication["request"]["name"] == rac.SAMPLE_CHECK_NAME
    assert (
        "contract_sample_publication_not_production_trust"
        in publication["publication_reasons"]
    )

    github = RecordingGitHub()
    with pytest.raises(
        rac.RequiredAssessmentCheckError,
        match="production_publisher_rejects_non_production_check",
    ):
        rac.publish_check_run(
            publication,
            publishing_authorization=publishing_claim(publication),
            publish_authority=accept_authority,
            github=github,
        )
    assert github.posts == []


def test_forged_approved_evaluator_cannot_post_production_success():
    assessment = passed_assessment(contract_sample=False)
    publication = rac.build_check_publication(
        assessment, publication_context(trust_basis="approved_evaluator")
    )
    assert publication["trust_basis"] == "approved_evaluator"
    assert publication["conclusion"] == "success"

    github = RecordingGitHub()

    with pytest.raises(
        rac.RequiredAssessmentCheckError, match="publish_authority_missing"
    ):
        rac.publish_check_run(publication, github=github)
    assert github.posts == []

    forged = publishing_claim(publication)
    with pytest.raises(
        rac.RequiredAssessmentCheckError, match="publishing_authorization_rejected"
    ):
        rac.publish_check_run(
            publication,
            publishing_authorization=forged,
            publish_authority=reject_authority,
            github=github,
        )
    assert github.posts == []


def test_authorized_production_success_posts_once():
    assessment = passed_assessment()
    publication = rac.build_check_publication(assessment, scoped_context(assessment))
    github = RecordingGitHub()

    def resolve_assessment(ref, candidate):
        assert ref == assessment["assessment_ref"]
        assert candidate["publication_ref"] == publication["publication_ref"]
        return assessment

    result = rac.publish_check_run(
        publication,
        publishing_authorization=publishing_claim(publication),
        publish_authority=accept_authority,
        assessment_authority=resolve_assessment,
        github=github,
    )

    assert result["id"] == 99
    assert github.posts == [("check-runs", "POST", publication["request"])]


def test_production_success_requires_original_assessment():
    assessment = passed_assessment()
    publication = rac.build_check_publication(assessment, scoped_context(assessment))
    github = RecordingGitHub()
    with pytest.raises(
        rac.RequiredAssessmentCheckError, match="assessment_authority_missing"
    ):
        rac.publish_check_run(
            publication,
            publishing_authorization=publishing_claim(publication),
            publish_authority=accept_authority,
            github=github,
        )
    assert github.posts == []
    # A self-digested success envelope cannot certify its own gate result.
    publication["assessment_status"] = None
    publication["publication_ref"] = rac._digest(
        {key: value for key, value in publication.items() if key != "publication_ref"}
    )
    with pytest.raises(
        rac.RequiredAssessmentCheckError, match="forced_success_refused"
    ):
        rac.validate_publication(publication)


@pytest.mark.parametrize(
    "field,value",
    [
        ("context", "delivery"),
        ("action", "merge"),
        ("target", "release"),
        ("phase", "post_publish"),
        ("refs", [{"target_ref": "refs/heads/other", "ref_action": "update"}]),
        ("refs", [{"target_ref": "refs/heads/main", "ref_action": "delete"}]),
    ],
)
def test_trusted_assessment_for_same_sha_cannot_supply_another_scope(field, value):
    assessment = passed_assessment()
    publication = rac.build_check_publication(assessment, scoped_context(assessment))
    publication["target_binding"]["assessment_scope"][field] = value
    publication["publication_ref"] = rac._digest(
        {key: value for key, value in publication.items() if key != "publication_ref"}
    )
    assert rac.validate_publication(publication) is publication
    github = RecordingGitHub()
    with pytest.raises(
        rac.RequiredAssessmentCheckError, match="publication_assessment_scope_mismatch"
    ):
        rac.publish_check_run(
            publication,
            publishing_authorization=publishing_claim(publication),
            publish_authority=accept_authority,
            assessment_authority=lambda ref, candidate: assessment,
            github=github,
        )
    assert github.posts == []


def test_production_failure_cannot_bypass_publisher_authority():
    assessment = blocked_assessment()
    context = scoped_context(assessment)
    github = RecordingGitHub()
    for publication in (
        rac.build_check_publication(assessment, context),
        rac.publication_for_missing_assessment(
            context, reason="assessment_artifact_missing"
        ),
    ):
        with pytest.raises(
            rac.RequiredAssessmentCheckError, match="publish_authority_missing"
        ):
            rac.publish_check_run(publication, github=github)
    assert github.posts == []


def test_publisher_detects_wrong_source_app_in_github_response():
    assessment = blocked_assessment()
    publication = rac.build_check_publication(assessment, scoped_context(assessment))
    github = RecordingGitHub(app_id=SOURCE_APP_ID + 1)
    with pytest.raises(
        rac.RequiredAssessmentCheckError, match="github_check_source_app_mismatch"
    ):
        rac.publish_check_run(
            publication,
            publishing_authorization=publishing_claim(publication),
            publish_authority=accept_authority,
            github=github,
        )
    assert len(github.posts) == 1


def test_head_sha_must_match_assessment_candidate_commit():
    assessment = passed_assessment(contract_sample=False)

    with pytest.raises(
        rac.RequiredAssessmentCheckError, match="publication_head_sha_mismatch"
    ):
        rac.build_check_publication(
            assessment,
            publication_context(
                head_sha="3" * 40,
                target_binding=target_binding(commit="3" * 40),
            ),
        )

    with pytest.raises(
        rac.RequiredAssessmentCheckError, match="publication_target_sha_mismatch"
    ):
        rac.build_check_publication(
            assessment,
            publication_context(
                head_sha="2" * 40,
                target_binding=target_binding(commit="3" * 40),
            ),
        )


def test_missing_target_binding_is_rejected():
    assessment = passed_assessment(contract_sample=False)
    context = publication_context()
    del context["target_binding"]

    with pytest.raises(
        rac.RequiredAssessmentCheckError, match="publication_target_binding_missing"
    ):
        rac.build_check_publication(assessment, context)


def test_event_head_binding_allows_absent_candidate_commit():
    assessment = passed_assessment(contract_sample=False, include_candidate=False)
    assert assessment.get("candidate") in (None, {})

    publication = rac.build_check_publication(
        assessment,
        publication_context(
            target_binding=target_binding(kind="event_head"),
        ),
    )
    assert publication["conclusion"] == "success"
    assert publication["target_binding"]["kind"] == "event_head"


def test_wrong_event_or_run_binding_is_rejected():
    assessment = passed_assessment(contract_sample=False)

    with pytest.raises(
        rac.RequiredAssessmentCheckError, match="publication_target_event_mismatch"
    ):
        rac.build_check_publication(
            assessment,
            publication_context(
                event_name="push",
                target_binding=target_binding(event_name="pull_request"),
            ),
        )

    with pytest.raises(
        rac.RequiredAssessmentCheckError, match="publication_target_run_mismatch"
    ):
        rac.build_check_publication(
            assessment,
            publication_context(
                target_binding=target_binding(run_id=1),
            ),
        )


def test_repository_override_is_rejected():
    assessment = blocked_assessment()
    publication = rac.build_check_publication(assessment, publication_context())
    github = RecordingGitHub()
    with pytest.raises(
        rac.RequiredAssessmentCheckError,
        match="publication_repository_override_rejected",
    ):
        rac.publish_check_run(publication, repository="other/repo", github=github)
    assert github.posts == []


def test_missing_assessment_completes_as_failure_not_skipped():
    publication = rac.publication_for_missing_assessment(
        publication_context(trust_basis="protected_workflow"),
        reason="assessment_artifact_missing",
    )

    assert publication["conclusion"] == "failure"
    assert publication["status"] == "completed"
    assert publication["assessment_ref"] is None
    assert publication["request"].get("external_id") is None
    assert publication["request"]["conclusion"] == "failure"
    assert publication["publication_reasons"] == ["assessment_artifact_missing"]
    assert "missing_assessment_completed_as_failure" in publication["limitations"]
    assert rac.validate_publication(publication) is publication


def test_forced_success_on_blocked_publication_is_rejected():
    assessment = blocked_assessment()
    publication = rac.build_check_publication(assessment, publication_context())
    tampered = copy.deepcopy(publication)
    tampered["conclusion"] = "success"
    tampered["request"] = {
        **tampered["request"],
        "conclusion": "success",
    }
    tampered["publication_ref"] = rac._digest(
        {key: value for key, value in tampered.items() if key != "publication_ref"}
    )

    with pytest.raises(
        rac.RequiredAssessmentCheckError, match="forced_success_refused"
    ):
        rac.validate_publication(tampered)


def test_skipped_or_neutral_conclusions_are_forbidden():
    assessment = blocked_assessment()
    publication = rac.build_check_publication(assessment, publication_context())
    for conclusion in ("skipped", "neutral", "cancelled"):
        tampered = copy.deepcopy(publication)
        tampered["conclusion"] = conclusion
        tampered["request"] = {**tampered["request"], "conclusion": conclusion}
        tampered["publication_ref"] = rac._digest(
            {key: value for key, value in tampered.items() if key != "publication_ref"}
        )
        with pytest.raises(
            rac.RequiredAssessmentCheckError, match="forbidden_conclusion"
        ):
            rac.validate_publication(tampered)


def test_external_id_must_equal_assessment_ref():
    assessment = passed_assessment(contract_sample=False)
    publication = rac.build_check_publication(assessment, publication_context())
    tampered = copy.deepcopy(publication)
    tampered["request"] = {
        **tampered["request"],
        "external_id": "sha256:" + "0" * 64,
    }
    tampered["publication_ref"] = rac._digest(
        {key: value for key, value in tampered.items() if key != "publication_ref"}
    )
    with pytest.raises(
        rac.RequiredAssessmentCheckError, match="publication_external_id_mismatch"
    ):
        rac.validate_publication(tampered)


def test_complete_current_run_assessment_builds_under_production_trust():
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    receipt, result_id = make_receipt(projection, entry)
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(profile, projection, [obligation], contract_sample=False)
    admission = make_admission(
        projection,
        coverage_entries=[(entry, coverage(projection, entry, result_id))],
        contract_sample=False,
    )
    assessment = assess(universe, projection, admission=admission, receipt=receipt)
    assert assessment["status"] == "passed"

    publication = rac.build_check_publication(
        assessment,
        publication_context(
            trust_basis="protected_workflow",
            event_name="push",
            head_sha=EVENT["head_commit"],
        ),
    )
    assert publication["conclusion"] == "success"
    assert publication["trust_basis"] == "protected_workflow"
    assert publication["check_name"] == rac.CHECK_NAME


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("event_name", "push", "publication_target_event_mismatch"),
        ("run_id", 1, "publication_target_run_mismatch"),
        ("run_attempt", 9, "publication_target_run_mismatch"),
    ],
)
def test_validate_publication_rejects_tampered_target_identity(field, value, reason):
    assessment = passed_assessment(contract_sample=False)
    publication = rac.build_check_publication(assessment, publication_context())
    tampered = copy.deepcopy(publication)
    tampered["target_binding"] = {
        **tampered["target_binding"],
        field: value,
    }
    tampered["publication_ref"] = rac._digest(
        {key: value for key, value in tampered.items() if key != "publication_ref"}
    )

    with pytest.raises(rac.RequiredAssessmentCheckError, match=reason):
        rac.validate_publication(tampered)

    github = RecordingGitHub()
    with pytest.raises(rac.RequiredAssessmentCheckError, match=reason):
        rac.publish_check_run(
            tampered,
            publishing_authorization=publishing_claim(publication),
            publish_authority=accept_authority,
            github=github,
        )
    assert github.posts == []
