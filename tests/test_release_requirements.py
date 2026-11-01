"""Release evidence and the requirement set it was verified against.

A recorded result is evidence for the requirements that were in force when
it was produced. Reading it again later does not extend it to requirements
that were added or rescoped afterwards, so a candidate is checked against the
current requirement set while a published release keeps the requirement set
it recorded.
"""

import copy

import pytest
from test_release_manifest import (
    HEAD,
    REPOSITORY,
    TAG,
    ManifestGitHub,
    make_manifest,
)

from engineering_ci import requirements
from engineering_ci.release import manifest as evidence
from engineering_ci.release import verification


def test_a_manifest_records_the_requirement_set_it_was_verified_against():
    value = make_manifest()
    recorded = value["requirements"]
    assert recorded["schema_version"] == 1
    assert recorded["digest"] == requirements.digest()
    assert recorded["checks"] == requirements.records()
    assert {item["key"] for item in recorded["checks"]} == set(value["checks"]) - {
        "core_coverage_threshold",
        "consumer_failure_probe",
    }


def test_the_recorded_requirement_set_describes_each_check():
    value = make_manifest()
    for item in value["requirements"]["checks"]:
        record = value["checks"][item["key"]]
        assert record["job"] == item["job"]
        assert record["step"] == item["step"]
        if item["dagger_check"]:
            assert record["dagger_check"] == item["dagger_check"]


def test_candidate_verification_accepts_evidence_for_the_current_requirements():
    value = make_manifest()
    assert verification.current_requirements(value) is value


def test_candidate_verification_refuses_a_manifest_without_the_requirement_set():
    value = make_manifest()
    value.pop("requirements")
    with pytest.raises(
        ValueError, match="does not record the required checks it covers"
    ):
        verification.current_requirements(value)


def test_candidate_verification_names_an_added_requirement(monkeypatch):
    value = make_manifest()
    extra = requirements.Requirement(
        key="extra_check",
        job="python-ci / checks",
        step="Extra check",
        scope="repository:extra",
        caller="core-tests",
    )
    monkeypatch.setattr(
        requirements, "REQUIREMENTS", (*requirements.REQUIREMENTS, extra)
    )
    with pytest.raises(
        ValueError,
        match="does not cover the current required checks: added extra_check",
    ):
        verification.current_requirements(value)


def test_candidate_verification_names_a_rescoped_requirement(monkeypatch):
    value = make_manifest()
    rescoped = [
        requirements.Requirement(
            key=item.key,
            job=item.job,
            step=item.step,
            scope="other" if item.key == "workflow_analysis" else item.scope,
            caller=item.caller,
            dagger_check=item.dagger_check,
        )
        for item in requirements.REQUIREMENTS
    ]
    monkeypatch.setattr(requirements, "REQUIREMENTS", tuple(rescoped))
    with pytest.raises(
        ValueError, match="does not cover the current required checks: rescoped"
    ):
        verification.current_requirements(value)


def test_candidate_verification_names_a_removed_requirement(monkeypatch):
    value = make_manifest()
    remaining = tuple(
        item for item in requirements.REQUIREMENTS if item.key != "workflow_analysis"
    )
    monkeypatch.setattr(requirements, "REQUIREMENTS", remaining)
    with pytest.raises(
        ValueError, match="does not cover the current required checks: removed"
    ):
        verification.current_requirements(value)


def test_a_published_manifest_is_verified_against_its_own_recorded_sources():
    """A published release stays verifiable after the requirements change."""
    value = make_manifest()
    legacy = copy.deepcopy(value)
    legacy.pop("requirements")
    api = ManifestGitHub()
    # The recorded results still match the original Actions results.
    evidence.verified_results(api.jobs, legacy)
    # The manifest is still structurally valid.
    evidence.validate(legacy, REPOSITORY, TAG, HEAD)


def test_a_manifest_that_disagrees_with_its_own_requirement_set_is_rejected():
    value = make_manifest()
    value["requirements"]["checks"][0]["job"] = "somewhere / else"
    value["requirements"]["digest"] = requirements.digest_records(
        value["requirements"]["checks"]
    )
    with pytest.raises(ValueError, match="Invalid required check"):
        evidence.validate(value, REPOSITORY, TAG, HEAD)


def test_a_manifest_with_a_tampered_requirement_digest_is_rejected():
    value = make_manifest()
    value["requirements"]["digest"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="Invalid recorded requirement set"):
        evidence.validate(value, REPOSITORY, TAG, HEAD)


def test_a_manifest_with_an_unreadable_requirement_set_is_rejected():
    value = make_manifest()
    value["requirements"]["checks"] = [{"key": "only"}]
    with pytest.raises(ValueError, match="Invalid recorded requirement set"):
        evidence.validate(value, REPOSITORY, TAG, HEAD)


def test_an_unchanged_requirement_set_keeps_its_result_valid():
    """The distinction the requirement set exists to make."""
    value = make_manifest()
    difference = requirements.delta(
        evidence.effective_requirements(value), requirements.records()
    )
    assert difference == {"added": [], "removed": [], "changed": []}
