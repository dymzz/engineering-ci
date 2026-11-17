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
    }
    context.update(changes)
    return context


def passed_assessment(*, contract_sample=False):
    profile = make_profile()
    entry = make_entry()
    projection = make_projection(profile, [entry])
    obligation = check_obligation("o/tach", [entry["expected_check_ref"]])
    universe = make_universe(
        profile, projection, [obligation], contract_sample=contract_sample
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
    )
    admission = make_admission(
        projection,
        coverage_entries=[(entry, historical_coverage(projection, entry))],
        contract_sample=False,
    )
    return assess(universe, projection, admission=admission)


def test_passed_assessment_publishes_success_under_approved_evaluator():
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


def test_blocked_assessment_always_publishes_failure():
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


def test_contract_sample_success_requires_explicit_sample_trust():
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
    assert (
        "contract_sample_publication_not_production_trust"
        in publication["publication_reasons"]
    )


def test_head_sha_must_match_assessment_candidate_commit():
    assessment = passed_assessment(contract_sample=False)

    with pytest.raises(
        rac.RequiredAssessmentCheckError, match="publication_head_sha_mismatch"
    ):
        rac.build_check_publication(assessment, publication_context(head_sha="3" * 40))


def test_missing_assessment_completes_as_failure_not_skipped():
    publication = rac.publication_for_missing_assessment(
        publication_context(trust_basis="protected_workflow"),
        reason="assessment_artifact_missing",
    )

    assert publication["conclusion"] == "failure"
    assert publication["status"] == "completed"
    assert publication["assessment_ref"] is None
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


def test_publish_check_run_posts_validated_request(monkeypatch):
    assessment = passed_assessment(contract_sample=False)
    publication = rac.build_check_publication(assessment, publication_context())
    captured = {}

    class FakeGitHub:
        def __init__(self, repository):
            captured["repository"] = repository

        def __call__(self, path, method="GET", data=None):
            captured["path"] = path
            captured["method"] = method
            captured["data"] = data
            return {"id": 99, "name": rac.CHECK_NAME, "conclusion": "success"}

    monkeypatch.setattr(rac, "GitHub", FakeGitHub)
    result = rac.publish_check_run(publication)

    assert result["id"] == 99
    assert captured["repository"] == "dymzz/engineering-ci"
    assert captured["path"] == "check-runs"
    assert captured["method"] == "POST"
    assert captured["data"] == publication["request"]


def test_complete_current_run_assessment_still_publishes_under_production_trust():
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
