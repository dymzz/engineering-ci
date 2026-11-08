"""Release evidence and the requirement set it was verified against.

A recorded result is evidence for the requirements that were in force when
it was produced. Reading it again later does not extend it to requirements
that were added or rescoped afterwards, so a candidate is checked against the
current requirement set while a published release keeps the requirement set
it recorded.
"""

import copy
import json
from pathlib import Path

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


HISTORICAL_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "historical"


def load_historical_manifest(tag: str) -> dict:
    path = HISTORICAL_FIXTURES / f"{tag}-release-manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_historical_github_data(tag: str) -> dict:
    path = HISTORICAL_FIXTURES / f"{tag}-github-data.json"
    return json.loads(path.read_text(encoding="utf-8"))


def make_v070_manifest() -> dict:
    """Authentic historical release manifest for v0.7.0 loaded from release asset fixture."""
    return load_historical_manifest("v0.7.0")


def make_v068_manifest() -> dict:
    """Authentic historical release manifest for v0.6.8 loaded from release asset fixture."""
    return load_historical_manifest("v0.6.8")


def make_legacy_manifest() -> dict:
    return make_v070_manifest()


class HistoricalGitHub:
    """Mock GitHub API that returns independently recorded genuine GitHub REST facts for a historical release."""

    def __init__(self, target: str | dict):
        tag = target if isinstance(target, str) else target["release"]["tag"]
        self.data = load_historical_github_data(tag)
        self.run = self.data["run"]
        self.jobs = self.data["jobs"]
        self.artifacts = self.data["artifacts"]

    def __call__(self, path, method="GET", data=None):
        from engineering_ci.github_cli import ApiError

        run_id = self.run["id"]
        attempt = self.run["run_attempt"]
        if path == f"actions/runs/{run_id}/attempts/{attempt}":
            return self.run
        if path.startswith(f"actions/runs/{run_id}/attempts/{attempt}/jobs?"):
            return {"jobs": self.jobs}
        if path.startswith("actions/artifacts/"):
            asset_id = int(path.rsplit("/", 1)[1])
            for item in self.artifacts:
                if item["id"] == asset_id:
                    return item
            raise ApiError(404)
        raise AssertionError((path, method, data))


def test_v070_historical_manifest_validates_under_its_authentic_contract():
    """v0.7.0 historical release validates cleanly against authentic independent GitHub records."""
    from engineering_ci.release import publishing

    v070 = make_v070_manifest()
    api = HistoricalGitHub("v0.7.0")
    # Verify exact authentic metadata facts
    assert v070["release"]["tag"] == "v0.7.0"
    assert v070["release"]["commit_sha"] == "9d03ade4f2af89538c9a0362beae55c9a0835e18"
    assert v070["workflow"]["run_id"] == 39614205390
    assert v070["workflow"]["run_attempt"] == 1
    assert v070["checks"]["candidate"]["job_id"] == 119270758077
    assert (
        v070["checks"]["coverage"]["dagger_check"] == "validation:fixture-quality-gates"
    )
    assert len(v070["artifacts"]) == 4

    evidence.verified_results(api.jobs, v070)
    evidence.validate(
        v070,
        v070["repository"],
        v070["release"]["tag"],
        v070["release"]["commit_sha"],
    )
    publishing.published_evidence(
        api,
        v070,
        v070["repository"],
        v070["release"]["tag"],
        v070["release"]["commit_sha"],
    )
    reqs = evidence.effective_requirements(v070)
    assert len(reqs) == 12
    coverage_req = next(r for r in reqs if r["key"] == "coverage")
    assert coverage_req["dagger_check"] == "validation:fixture-quality-gates"


def test_v068_historical_manifest_validates_under_its_authentic_contract():
    """v0.6.8 historical release validates cleanly with its authentic engineering-ci Dagger identity and independent GitHub records."""
    from engineering_ci.release import publishing

    v068 = make_v068_manifest()
    api = HistoricalGitHub("v0.6.8")
    # Verify exact authentic metadata facts
    assert v068["release"]["tag"] == "v0.6.8"
    assert v068["release"]["commit_sha"] == "eb0e457de2c172006b62d3962b0a7e932a5adb32"
    assert v068["workflow"]["run_id"] == 39449394205
    assert v068["workflow"]["run_attempt"] == 1
    assert v068["checks"]["candidate"]["job_id"] == 118719846431
    assert (
        v068["checks"]["coverage"]["dagger_check"]
        == "engineering-ci:fixture-quality-gates"
    )
    assert len(v068["artifacts"]) == 4

    evidence.verified_results(api.jobs, v068)
    evidence.validate(
        v068,
        v068["repository"],
        v068["release"]["tag"],
        v068["release"]["commit_sha"],
    )
    publishing.published_evidence(
        api,
        v068,
        v068["repository"],
        v068["release"]["tag"],
        v068["release"]["commit_sha"],
    )
    reqs = evidence.effective_requirements(v068)
    assert len(reqs) == 12
    coverage_req = next(r for r in reqs if r["key"] == "coverage")
    assert coverage_req["dagger_check"] == "engineering-ci:fixture-quality-gates"


def test_historical_manifest_tampered_job_id_is_rejected():
    """Manifest with tampered or swapped Job ID is rejected by published_evidence."""
    from engineering_ci.release import publishing

    v068 = make_v068_manifest()
    # Swapping v0.6.8 candidate job_id (118719846431) with v0.7.0 job_id (119270758077)
    v068["checks"]["candidate"]["job_id"] = 119270758077
    api = HistoricalGitHub("v0.6.8")
    with pytest.raises(
        ValueError, match="Manifest does not match original Actions results"
    ):
        publishing.published_evidence(
            api,
            v068,
            v068["repository"],
            v068["release"]["tag"],
            v068["release"]["commit_sha"],
        )


def test_historical_manifest_tampered_artifact_id_is_rejected():
    """Manifest with tampered or swapped Artifact ID is rejected by published_evidence."""
    from engineering_ci.release import publishing

    v068 = make_v068_manifest()
    # Swapping v0.6.8 artifact id with nonexistent or mismatched artifact id
    v068["artifacts"][0]["id"] = 999999999
    v068["artifacts"][0]["url"] = (
        f"https://api.github.com/repos/{v068['repository']}/actions/artifacts/999999999"
    )
    api = HistoricalGitHub("v0.6.8")
    with pytest.raises((ValueError, Exception)):
        publishing.published_evidence(
            api,
            v068,
            v068["repository"],
            v068["release"]["tag"],
            v068["release"]["commit_sha"],
        )


def test_v068_with_v070_dagger_identity_is_rejected():
    """Swapping Dagger check identity to later version's module is rejected."""
    v068 = make_v068_manifest()
    v068["checks"]["coverage"]["dagger_check"] = "validation:fixture-quality-gates"
    with pytest.raises(ValueError, match="Invalid Dagger check"):
        evidence.validate(
            v068,
            v068["repository"],
            v068["release"]["tag"],
            v068["release"]["commit_sha"],
        )


def test_v070_with_v068_dagger_identity_is_rejected():
    """Swapping Dagger check identity to earlier version's module is rejected."""
    v070 = make_v070_manifest()
    v070["checks"]["coverage"]["dagger_check"] = "engineering-ci:fixture-quality-gates"
    with pytest.raises(ValueError, match="Invalid Dagger check"):
        evidence.validate(
            v070,
            v070["repository"],
            v070["release"]["tag"],
            v070["release"]["commit_sha"],
        )


def test_historical_manifest_missing_artifact_is_rejected():
    legacy = make_v070_manifest()
    legacy["artifacts"] = [
        item for item in legacy["artifacts"] if item["name"] != "core-tests"
    ]
    with pytest.raises(
        ValueError, match="Passed manifest lacks required artifact references"
    ):
        evidence.validate(
            legacy,
            legacy["repository"],
            legacy["release"]["tag"],
            legacy["release"]["commit_sha"],
        )


def test_historical_manifest_missing_check_is_rejected():
    legacy = make_v070_manifest()
    legacy["checks"].pop("dependency_security")
    with pytest.raises(ValueError, match="Missing or unexpected manifest checks"):
        evidence.validate(
            legacy,
            legacy["repository"],
            legacy["release"]["tag"],
            legacy["release"]["commit_sha"],
        )


def test_historical_manifest_with_fabricated_check_is_rejected():
    legacy = make_v070_manifest()
    legacy["checks"]["fabricated_check"] = {
        "required": True,
        "status": "passed",
        "job": "fake",
        "step": "fake",
        "job_id": 1,
        "job_conclusion": "success",
        "step_conclusion": "success",
    }
    with pytest.raises(ValueError, match="Missing or unexpected manifest checks"):
        evidence.validate(
            legacy,
            legacy["repository"],
            legacy["release"]["tag"],
            legacy["release"]["commit_sha"],
        )


def test_historical_manifest_tampered_commit_is_rejected():
    legacy = make_v070_manifest()
    legacy["release"]["commit_sha"] = "0" * 40
    with pytest.raises(
        ValueError, match="Historical manifest lacks authentic release contract"
    ):
        evidence.validate(
            legacy,
            legacy["repository"],
            legacy["release"]["tag"],
            legacy["release"]["commit_sha"],
        )


def test_historical_manifest_tampered_tag_is_rejected():
    legacy = make_v070_manifest()
    legacy["release"]["tag"] = "v0.9.9"
    legacy["release"]["version"] = "0.9.9"
    with pytest.raises(
        ValueError, match="Historical manifest lacks authentic release contract"
    ):
        evidence.validate(
            legacy,
            legacy["repository"],
            legacy["release"]["tag"],
            legacy["release"]["commit_sha"],
        )


def test_historical_manifest_tampered_run_id_is_rejected():
    legacy = make_v070_manifest()
    legacy["workflow"]["run_id"] = 999999999
    legacy["workflow"]["run_url"] = (
        f"https://github.com/{legacy['repository']}/actions/runs/999999999"
    )
    with pytest.raises(
        ValueError, match="Historical manifest lacks authentic release contract"
    ):
        evidence.validate(
            legacy,
            legacy["repository"],
            legacy["release"]["tag"],
            legacy["release"]["commit_sha"],
        )


def test_first_publication_rejects_historical_manifest(tmp_path):
    from engineering_ci.release import publishing

    legacy = make_v070_manifest()
    api = ManifestGitHub()
    with pytest.raises(
        ValueError,
        match="Release evidence does not record the required checks it covers",
    ):
        publishing.publish_release(
            api,
            legacy["release"]["tag"],
            legacy["release"]["commit_sha"],
            legacy,
            tmp_path / "release-manifest.json",
        )


def test_first_publication_rejects_manifest_omitting_workflow_analysis_even_with_matching_digest(
    tmp_path,
):
    from engineering_ci.release import publishing

    value = make_manifest()
    value["checks"].pop("workflow_analysis")
    value["requirements"]["checks"] = [
        item
        for item in value["requirements"]["checks"]
        if item["key"] != "workflow_analysis"
    ]
    value["requirements"]["digest"] = requirements.digest_records(
        value["requirements"]["checks"]
    )
    api = ManifestGitHub()
    with pytest.raises(
        ValueError,
        match="Release evidence does not cover the current required checks: added workflow_analysis",
    ):
        publishing.publish_release(
            api, TAG, HEAD, value, tmp_path / "release-manifest.json"
        )


def test_first_publication_rejects_manifest_omitting_powershell_analysis_even_with_matching_digest(
    tmp_path,
):
    from engineering_ci.release import publishing

    value = make_manifest()
    value["checks"].pop("powershell_analysis")
    value["artifacts"] = [
        item for item in value["artifacts"] if item["name"] != "powershell-analysis"
    ]
    value["requirements"]["checks"] = [
        item
        for item in value["requirements"]["checks"]
        if item["key"] != "powershell_analysis"
    ]
    value["requirements"]["digest"] = requirements.digest_records(
        value["requirements"]["checks"]
    )
    api = ManifestGitHub()
    with pytest.raises(
        ValueError,
        match="Release evidence does not cover the current required checks: added powershell_analysis",
    ):
        publishing.publish_release(
            api, TAG, HEAD, value, tmp_path / "release-manifest.json"
        )


def test_published_evidence_verifies_historical_manifest():
    from engineering_ci.release import publishing

    for tag in ("v0.6.8", "v0.7.0"):
        manifest = load_historical_manifest(tag)
        api = HistoricalGitHub(tag)
        publishing.published_evidence(
            api,
            manifest,
            manifest["repository"],
            manifest["release"]["tag"],
            manifest["release"]["commit_sha"],
        )


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


def test_manifest_with_duplicate_requirement_keys_is_rejected():
    """A manifest whose requirements.checks list contains duplicate keys is rejected.

    Even if its digest is recomputed to match the duplicated list, validate() must
    reject duplicate keys to prevent divergence between the list digest and the
    parsed dictionary.
    """
    value = make_manifest()
    duplicate_item = copy.deepcopy(value["requirements"]["checks"][0])
    value["requirements"]["checks"].append(duplicate_item)
    value["requirements"]["digest"] = requirements.digest_records(
        value["requirements"]["checks"]
    )
    with pytest.raises(ValueError, match="Invalid recorded requirement set"):
        evidence.validate(value, REPOSITORY, TAG, HEAD)


def test_historical_manifest_distinguishes_schema_contract_from_validation():
    """Historical manifests omit requirements, which the current candidate schema requires,

    but historical evidence remains verifiable through the Python validation contract.
    """
    import json
    from pathlib import Path

    schema = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "docs/architecture/release/manifest.schema.json"
        ).read_text(encoding="utf-8")
    )
    legacy = make_legacy_manifest()
    assert "requirements" not in legacy
    assert "requirements" in schema["required"]
    # Fails current candidate schema requirement:
    assert set(legacy.keys()) != set(schema["required"])
    # But passes Python validation under historical legacy contract:
    evidence.validate(
        legacy,
        legacy["repository"],
        legacy["release"]["tag"],
        legacy["release"]["commit_sha"],
    )
