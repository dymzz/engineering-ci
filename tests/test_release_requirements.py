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


def make_v070_manifest():
    """Authentic historical release manifest for v0.7.0 (prior to ECI-CHECK-01)."""
    return {
        "schema_version": 1,
        "repository": "dymzz/engineering-ci",
        "mode": "publication",
        "result": "passed",
        "release": {
            "version": "0.7.0",
            "tag": "v0.7.0",
            "commit_sha": "9d03ade4f2af89538c9a0362beae55c9a0835e18",
            "release_url": "https://api.github.com/repos/dymzz/engineering-ci/releases/419641784",
        },
        "workflow": {
            "event": "workflow_dispatch",
            "path": ".github/workflows/release.yml",
            "run_attempt": 1,
            "run_id": 39614205390,
            "run_url": "https://github.com/dymzz/engineering-ci/actions/runs/39614205390",
        },
        "artifacts": [
            {
                "digest": "sha256:00fc1d211651ce3a16e1bf94a382b65cec9bb95f73b201134b549f852ae902be",
                "id": 12351403610,
                "name": "core-tests",
                "url": "https://api.github.com/repos/dymzz/engineering-ci/actions/artifacts/12351403610",
            },
            {
                "digest": "sha256:222b198499ea1c0363cfa54c2a7cd5a300b2cdfa6ed5a10e55f61490586110bb",
                "id": 12351212985,
                "name": "dependency-security",
                "url": "https://api.github.com/repos/dymzz/engineering-ci/actions/artifacts/12351212985",
            },
            {
                "digest": "sha256:064aea2125a38acafcf1423f3bca06cc011fb151a178b36c2fbd97f860950270",
                "id": 12352375695,
                "name": "pytest-report",
                "url": "https://api.github.com/repos/dymzz/engineering-ci/actions/artifacts/12352375695",
            },
            {
                "digest": "sha256:b4e8ae537ac5079f04ba9e66e6963d3d4cb91cf22a497815b0423238a6686444",
                "id": 12351977770,
                "name": "release-tools-ruff",
                "url": "https://api.github.com/repos/dymzz/engineering-ci/actions/artifacts/12351977770",
            },
        ],
        "checks": {
            "candidate": {
                "job": "candidate",
                "job_conclusion": "success",
                "job_id": 119270758077,
                "required": True,
                "status": "passed",
                "step": "Validate manual release request",
                "step_conclusion": "success",
            },
            "consumer_failure_probe": {
                "reason": "The smoke-only Ruff rejection probe is disabled in core releases.",
                "required": False,
                "status": "not_enabled",
            },
            "core_coverage_threshold": {
                "reason": "Core coverage is exported; no core coverage threshold is configured.",
                "required": False,
                "status": "not_enabled",
            },
            "core_tests": {
                "job": "core-tests / sync-tool-tests",
                "job_conclusion": "success",
                "job_id": 119270919810,
                "required": True,
                "status": "passed",
                "step": "Run all core unit and process tests",
                "step_conclusion": "success",
            },
            "coverage": {
                "dagger_check": "validation:fixture-quality-gates",
                "job": "complete-ci / checks",
                "job_conclusion": "success",
                "job_id": 119270919257,
                "required": True,
                "status": "passed",
                "step": "Run Dagger Checks",
                "step_conclusion": "success",
            },
            "dagger": {
                "job": "complete-ci / checks",
                "job_conclusion": "success",
                "job_id": 119270919257,
                "required": True,
                "status": "passed",
                "step": "Run Dagger Checks",
                "step_conclusion": "success",
            },
            "dependency_security": {
                "job": "complete-ci / dependency-security",
                "job_conclusion": "success",
                "job_id": 119270919312,
                "required": True,
                "status": "passed",
                "step": "Check lock integrity and scan known vulnerabilities",
                "step_conclusion": "success",
            },
            "evidence": {
                "job": "evidence",
                "job_conclusion": "success",
                "job_id": 119272169399,
                "required": True,
                "status": "passed",
                "step": "Require usable evidence and original execution gates",
                "step_conclusion": "success",
            },
            "module_lock": {
                "job": "complete-ci / checks",
                "job_conclusion": "success",
                "job_id": 119270919257,
                "required": True,
                "status": "passed",
                "step": "Verify generated module lock consistency",
                "step_conclusion": "success",
            },
            "python_matrix": {
                "dagger_check": "validation:fixture-python-matrix",
                "job": "complete-ci / checks",
                "job_conclusion": "success",
                "job_id": 119270919257,
                "required": True,
                "status": "passed",
                "step": "Run Dagger Checks",
                "step_conclusion": "success",
            },
            "ruff_format": {
                "job": "core-tests / python-ci / checks",
                "job_conclusion": "success",
                "job_id": 119270919523,
                "required": True,
                "status": "passed",
                "step": "Ruff Format Check",
                "step_conclusion": "success",
            },
            "ruff_lint": {
                "job": "core-tests / python-ci / checks",
                "job_conclusion": "success",
                "job_id": 119270919523,
                "required": True,
                "status": "passed",
                "step": "Ruff Lint",
                "step_conclusion": "success",
            },
            "tests": {
                "job": "complete-ci / checks",
                "job_conclusion": "success",
                "job_id": 119270919257,
                "required": True,
                "status": "passed",
                "step": "Gate on pytest execution and explicit quality policy",
                "step_conclusion": "success",
            },
            "version_integrity": {
                "job": "core-tests / version-metadata",
                "job_conclusion": "success",
                "job_id": 119270919383,
                "required": True,
                "status": "passed",
                "step": "Validate release metadata and root lock consistency",
                "step_conclusion": "success",
            },
        },
        "tools": [
            {"name": "pytest", "scope": "core", "version": "9.0.3"},
            {"name": "pytest-cov", "scope": "core", "version": "7.0.0"},
            {"name": "python", "scope": "core", "version": "3.14.8"},
            {"name": "uv", "scope": "core", "version": "0.13.0"},
            {"name": "dagger", "scope": "dagger_cli", "version": "1.0.0-beta.16"},
            {"name": "osv-scanner", "scope": "root_lock", "version": "2.6.0"},
            {"name": "ruff", "scope": "ruff", "version": "0.16.10"},
            {"name": "uv", "scope": "ruff", "version": "0.13.0"},
        ],
    }


def make_v068_manifest():
    """Authentic historical release manifest for v0.6.8 with its genuine Dagger check identities."""
    manifest = make_v070_manifest()
    manifest["release"]["version"] = "0.6.8"
    manifest["release"]["tag"] = "v0.6.8"
    manifest["release"]["commit_sha"] = "eb0e457de2c172006b62d3962b0a7e932a5adb32"
    manifest["release"]["release_url"] = (
        "https://api.github.com/repos/dymzz/engineering-ci/releases/418568386"
    )
    manifest["workflow"]["run_id"] = 39449394205
    manifest["workflow"]["run_url"] = (
        "https://github.com/dymzz/engineering-ci/actions/runs/39449394205"
    )
    manifest["checks"]["coverage"]["dagger_check"] = (
        "engineering-ci:fixture-quality-gates"
    )
    manifest["checks"]["python_matrix"]["dagger_check"] = (
        "engineering-ci:fixture-python-matrix"
    )
    return manifest


def make_legacy_manifest():
    return make_v070_manifest()


def _matching_actions_jobs(manifest):
    groups = {}
    for record in manifest["checks"].values():
        if not record.get("required"):
            continue
        job_name, step_name = record["job"], record["step"]
        if job_name not in groups:
            groups[job_name] = {
                "id": record["job_id"],
                "run_id": manifest["workflow"]["run_id"],
                "run_attempt": manifest["workflow"]["run_attempt"],
                "head_sha": manifest["release"]["commit_sha"],
                "name": job_name,
                "status": "completed",
                "conclusion": record["job_conclusion"],
                "steps": [],
            }
        if not any(s["name"] == step_name for s in groups[job_name]["steps"]):
            groups[job_name]["steps"].append(
                {
                    "name": step_name,
                    "status": "completed",
                    "conclusion": record["step_conclusion"],
                }
            )
    return list(groups.values())


class HistoricalGitHub:
    def __init__(self, manifest):
        self.manifest = manifest
        run_id = manifest["workflow"]["run_id"]
        attempt = manifest["workflow"]["run_attempt"]
        repo = manifest["repository"]
        head = manifest["release"]["commit_sha"]
        self.run = {
            "id": run_id,
            "run_attempt": attempt,
            "head_sha": head,
            "event": "workflow_dispatch",
            "repository": {"full_name": repo},
            "html_url": manifest["workflow"]["run_url"],
            "path": ".github/workflows/release.yml",
            "head_branch": "main",
            "token": "ghp_DO_NOT_RECORD",
        }
        self.jobs = _matching_actions_jobs(manifest)
        self.artifacts = [
            {
                "id": item["id"],
                "name": item["name"],
                "url": item["url"],
                "expired": False,
                "digest": item["digest"],
                "workflow_run": {"head_sha": head, "id": run_id},
                "archive_download_url": "https://secret.example/?token=DO_NOT_RECORD",
            }
            for item in manifest["artifacts"]
        ]

    def __call__(self, path, method="GET", data=None):
        from engineering_ci.github_cli import ApiError

        run_id = self.manifest["workflow"]["run_id"]
        attempt = self.manifest["workflow"]["run_attempt"]
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
    """v0.7.0 historical release validates cleanly under its authentic contract."""
    v070 = make_v070_manifest()
    jobs = _matching_actions_jobs(v070)
    evidence.verified_results(jobs, v070)
    evidence.validate(
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
    """v0.6.8 historical release validates cleanly with its authentic engineering-ci Dagger identity."""
    v068 = make_v068_manifest()
    jobs = _matching_actions_jobs(v068)
    evidence.verified_results(jobs, v068)
    evidence.validate(
        v068,
        v068["repository"],
        v068["release"]["tag"],
        v068["release"]["commit_sha"],
    )
    reqs = evidence.effective_requirements(v068)
    assert len(reqs) == 12
    coverage_req = next(r for r in reqs if r["key"] == "coverage")
    assert coverage_req["dagger_check"] == "engineering-ci:fixture-quality-gates"


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

    legacy = make_v070_manifest()
    api = HistoricalGitHub(legacy)
    publishing.published_evidence(
        api,
        legacy,
        legacy["repository"],
        legacy["release"]["tag"],
        legacy["release"]["commit_sha"],
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
