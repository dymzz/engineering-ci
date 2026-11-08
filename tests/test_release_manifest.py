"""Release evidence uses existing Actions facts and mocked publication only."""

import copy
import json
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
from engineering_ci.release import manifest as evidence
from engineering_ci.release import publishing as release

HEAD = "a" * 40
TAG = "v0.4.11"
REPOSITORY = "example/ci"
IDS = {name: 45 + index for index, name in enumerate(sorted(evidence.ARTIFACT_NAMES))}


class ManifestGitHub:
    def __init__(self):
        self.run = {
            "id": 123,
            "run_attempt": 1,
            "head_sha": HEAD,
            "event": "workflow_dispatch",
            "repository": {"full_name": REPOSITORY},
            "html_url": f"https://github.com/{REPOSITORY}/actions/runs/123",
            "path": ".github/workflows/release.yml",
            "head_branch": "main",
            "token": "ghp_DO_NOT_RECORD",
        }
        groups = {}
        for name, step in evidence.CHECKS.values():
            if name not in groups:
                groups[name] = {
                    "id": len(groups) + 1,
                    "run_id": 123,
                    "run_attempt": 1,
                    "head_sha": HEAD,
                    "name": name,
                    "status": "completed",
                    "conclusion": "success",
                    "steps": [],
                }
            groups[name]["steps"].append(
                {"name": step, "status": "completed", "conclusion": "success"}
            )
        self.jobs = list(groups.values())
        self.artifacts = [
            {
                "id": asset_id,
                "name": name,
                "url": f"https://api.github.com/repos/{REPOSITORY}/actions/artifacts/{asset_id}",
                "expired": False,
                "digest": "sha256:" + "c" * 64,
                "workflow_run": {"head_sha": HEAD, "id": 123},
                "archive_download_url": "https://secret.example/?token=DO_NOT_RECORD",
            }
            for name, asset_id in IDS.items()
        ]

    def __call__(self, path, method="GET", data=None):
        if path == "actions/runs/123/attempts/1":
            return self.run
        if path.startswith("actions/runs/123/attempts/1/jobs?"):
            return {"jobs": self.jobs}
        if path.startswith("actions/artifacts/"):
            asset_id = int(path.rsplit("/", 1)[1])
            for item in self.artifacts:
                if item["id"] == asset_id:
                    return item
            raise release.ApiError(404)
        raise AssertionError((path, method, data))


def facts():
    return {
        "tools": evidence.clean_tools(
            [
                {"name": name, "version": version, "scope": scope}
                for name, version, scope in [
                    ("python", "3.14.0", "core"),
                    ("uv", "0.12.23", "core"),
                    ("pytest", "9.0.3", "core"),
                    ("pytest-cov", "7.0.0", "core"),
                    ("uv", "0.12.23", "ruff"),
                    ("ruff", "0.16.10", "ruff"),
                    ("dagger", "0.21.10", "dagger_cli"),
                    ("osv-scanner", "2.6.0", "root_lock"),
                ]
            ]
        ),
        "dagger_checks": list(evidence.DAGGER_POLICY_CHECKS.values()),
    }


def make_manifest(api=None, mode="publication", source=None, ids=None):
    return evidence.manifest(
        api or ManifestGitHub(),
        REPOSITORY,
        TAG,
        HEAD,
        123,
        1,
        mode,
        facts() if source is None else source,
        ids=IDS if ids is None else ids,
    )


def write_ledger(directory, replaced="validation:fixture-test", executed=None):
    """Write the native Dagger execution ledger the reusable Workflow produces.

    The ledger names the checks the module offered, the checks that were left
    to run and the one check a report execution replaced.
    """
    checks = list(
        executed if executed is not None else evidence.DAGGER_EXECUTION_CHECKS
    )
    (directory / "pytest/dagger-checks.txt").write_text(
        "".join(
            f"--validation --check={name.split(':', 1)[1]} # {name}\n"
            for name in checks
        ),
        encoding="utf-8",
    )
    (directory / "pytest/replaced-check.txt").write_text(
        replaced + "\n", encoding="utf-8"
    )
    (directory / "pytest/remaining-checks.txt").write_text(
        "".join(name + "\n" for name in checks if name != replaced), encoding="utf-8"
    )
    return checks


def publish(api, value=None):
    with tempfile.TemporaryDirectory() as temp:
        return release.publish_release(
            api,
            TAG,
            HEAD,
            make_manifest(api) if value is None else value,
            Path(temp) / "release-manifest.json",
        )


def test_identity_schema_deterministic_and_no_sensitive_output():
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("GH_TOKEN", "ghp_ENV_DO_NOT_RECORD")
        value = make_manifest()
    assert value == make_manifest()
    assert evidence.encode(value) == evidence.encode(make_manifest())
    assert value["release"] == {
        "version": "0.4.11",
        "tag": TAG,
        "commit_sha": HEAD,
        "release_url": None,
    }
    assert value["workflow"]["run_id"] == 123
    assert value["checks"]["core_coverage_threshold"]["status"] == "not_enabled"
    assert value["checks"]["consumer_failure_probe"]["required"] is False
    assert value["result"] == "passed"
    assert "DO_NOT_RECORD" not in evidence.encode(value)
    assert "archive_download_url" not in evidence.encode(value)
    schema = json.loads(
        (ROOT / "docs/architecture/release/manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(schema["required"]) == set(value)
    assert set(schema["properties"]["checks"]["required"]) == set(value["checks"])
    assert schema["additionalProperties"] is False
    evidence.validate(value, REPOSITORY, TAG, HEAD)


@pytest.mark.parametrize(
    "conclusion", ["failure", "skipped", "cancelled", "timed_out", None]
)
def test_required_job_or_step_is_never_faked_as_passed(conclusion):
    api = ManifestGitHub()
    api.jobs[0]["conclusion"] = conclusion
    value = make_manifest(api)
    assert value["result"] == "failed"
    assert value["checks"]["candidate"]["status"] != "passed"
    api = ManifestGitHub()
    api.jobs[0]["steps"][0]["conclusion"] = conclusion
    assert make_manifest(api)["result"] == "failed"


def test_missing_duplicate_or_running_results_are_unavailable():
    for change in ("missing", "duplicate", "running", "missing_step"):
        api = ManifestGitHub()
        if change == "missing":
            api.jobs.pop(0)
        elif change == "duplicate":
            api.jobs.append(copy.deepcopy(api.jobs[0]))
        elif change == "running":
            api.jobs[0]["status"] = "in_progress"
        else:
            api.jobs[0]["steps"] = []
        assert make_manifest(api)["checks"]["candidate"]["status"] == "unavailable"


def test_report_success_cannot_mask_failed_original_execution():
    api = ManifestGitHub()
    job = next(job for job in api.jobs if job["name"] == "complete-ci / checks")
    job["conclusion"] = "failure"
    value = make_manifest(api)
    assert value["checks"]["tests"]["status"] == "failed"
    assert value["checks"]["tests"]["step_conclusion"] == "success"
    assert value["result"] == "failed"


def test_policy_checks_require_existing_execution_evidence():
    source = facts()
    source["dagger_checks"] = []
    value = make_manifest(source=source)
    assert value["checks"]["coverage"]["status"] == "unavailable"
    assert value["checks"]["python_matrix"]["status"] == "unavailable"
    assert value["result"] == "failed"
    value = evidence.manifest(
        ManifestGitHub(), REPOSITORY, TAG, HEAD, 123, 1, "validation", None
    )
    assert not value["tools"]
    assert value["checks"]["evidence"]["status"] == "unavailable"


def test_missing_artifacts_or_tools_cannot_claim_passed():
    api = ManifestGitHub()
    api.artifacts = []
    value = make_manifest(api, ids={})
    assert value["result"] == "failed"
    assert value["checks"]["evidence"]["status"] == "unavailable"
    value = make_manifest()
    value["artifacts"] = []
    with pytest.raises(ValueError, match="artifact references"):
        evidence.validate(value, REPOSITORY, TAG, HEAD)
    value = make_manifest()
    value["tools"] = []
    with pytest.raises(ValueError, match="tool evidence"):
        evidence.validate(value, REPOSITORY, TAG, HEAD)


def test_publication_evidence_must_be_a_manual_main_run():
    api = ManifestGitHub()
    api.run["head_branch"] = "candidate"
    with pytest.raises(ValueError, match="identity"):
        make_manifest(api, mode="publication")
    assert make_manifest(api, mode="validation")["result"] == "passed"


@pytest.mark.parametrize(
    "output,version",
    [
        ("dagger v0.21.10 linux/amd64\n", "0.21.10"),
        (
            (
                "version:     v1.0.0-beta.16\ncommit:      2d95ef98\ndirty:       no\n"
                "platform:    windows(10.0.22631)/amd64\n"
                "runner-host: docker-container://dagger-engine\n"
            ),
            "1.0.0-beta.16",
        ),
    ],
)
def test_actual_tool_files_are_required_and_scoped(
    tmp_path, monkeypatch, output, version
):
    monkeypatch.setattr(release, "require_evidence", lambda _: None)
    for name in ("core", "ruff", "pytest", "security"):
        (tmp_path / name).mkdir()
    records = facts()["tools"]
    for item in records:
        if item["name"] == "dagger":
            item["version"] = version
    for scope in ("core", "ruff"):
        evidence.write(
            tmp_path / scope / "tool-versions.json",
            [item for item in records if item["scope"] == scope],
        )
    (tmp_path / "pytest/dagger-version.txt").write_text(output)
    write_ledger(tmp_path)
    evidence.write(tmp_path / "security/report.json", {"scanner": {"version": "2.6.0"}})
    assert evidence.facts(tmp_path)["tools"] == records
    (tmp_path / "pytest/dagger-version.txt").unlink()
    with pytest.raises(OSError):
        evidence.facts(tmp_path)


@pytest.mark.parametrize(
    "output",
    [
        "version: v1.0.0-beta.16 token=SECRET",
        "version: v1.0.0-beta.16\nversion: v0.21.10",
        "version: main",
        "commit: 2d95ef98",
    ],
)
def test_dagger_cli_version_rejects_missing_or_ambiguous_output(output):
    with pytest.raises(ValueError, match="Dagger CLI version unavailable"):
        evidence.dagger_cli_version(output)


def test_the_dagger_execution_ledger_must_cover_every_required_check(
    tmp_path, monkeypatch
):
    """A successful enclosing Job does not prove the required set ran."""
    monkeypatch.setattr(release, "require_evidence", lambda _: None)
    for name in ("core", "ruff", "pytest", "security"):
        (tmp_path / name).mkdir()
    records = facts()["tools"]
    for scope in ("core", "ruff"):
        evidence.write(
            tmp_path / scope / "tool-versions.json",
            [item for item in records if item["scope"] == scope],
        )
    (tmp_path / "pytest/dagger-version.txt").write_text("version: v1.0.0-beta.16")
    evidence.write(tmp_path / "security/report.json", {"scanner": {"version": "2.6.0"}})
    incomplete = [
        name
        for name in evidence.DAGGER_EXECUTION_CHECKS
        if name != "validation:fixture-failure-report"
    ]
    write_ledger(tmp_path, executed=incomplete)
    with pytest.raises(ValueError, match="Required Dagger execution evidence missing"):
        evidence.facts(tmp_path)
    write_ledger(tmp_path)
    assert set(evidence.facts(tmp_path)["dagger_checks"]) == set(
        evidence.DAGGER_EXECUTION_CHECKS
    )


def test_the_dagger_execution_ledger_must_agree_with_the_listing(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "require_evidence", lambda _: None)
    for name in ("core", "ruff", "pytest", "security"):
        (tmp_path / name).mkdir()
    records = facts()["tools"]
    for scope in ("core", "ruff"):
        evidence.write(
            tmp_path / scope / "tool-versions.json",
            [item for item in records if item["scope"] == scope],
        )
    (tmp_path / "pytest/dagger-version.txt").write_text("version: v1.0.0-beta.16")
    evidence.write(tmp_path / "security/report.json", {"scanner": {"version": "2.6.0"}})
    write_ledger(tmp_path)
    (tmp_path / "pytest/remaining-checks.txt").write_text(
        "validation:fixture-test\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="disagrees with the discovered check listing"):
        evidence.facts(tmp_path)


def test_the_dagger_execution_ledger_rejects_unpermitted_replaced_check(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(release, "require_evidence", lambda _: None)
    for name in ("core", "ruff", "pytest", "security"):
        (tmp_path / name).mkdir()
    records = facts()["tools"]
    for scope in ("core", "ruff"):
        evidence.write(
            tmp_path / scope / "tool-versions.json",
            [item for item in records if item["scope"] == scope],
        )
    (tmp_path / "pytest/dagger-version.txt").write_text("version: v1.0.0-beta.16")
    evidence.write(tmp_path / "security/report.json", {"scanner": {"version": "2.6.0"}})
    write_ledger(tmp_path, replaced="validation:fixture-quality-gates")
    with pytest.raises(
        ValueError, match="The replaced Dagger check must be 'validation:fixture-test'"
    ):
        evidence.facts(tmp_path)


def test_dagger_cli_version_does_not_record_runner_credentials():
    assert (
        evidence.dagger_cli_version(
            "version: v1.0.0-beta.16\nrunner-host: tls://user:SECRET@example.invalid\n"
        )
        == "1.0.0-beta.16"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("head_sha", "b" * 40),
        ("id", 9),
        ("run_attempt", 2),
        ("event", "push"),
        ("path", ".github/workflows/ci.yml"),
        ("html_url", "https://secret.example/?token=SECRET"),
        ("repository", {"full_name": "wrong/repo"}),
    ],
)
def test_workflow_identity_conflicts_are_rejected(field, value):
    api = ManifestGitHub()
    api.run[field] = value
    with pytest.raises(ValueError, match="identity"):
        make_manifest(api)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["release"].update(commit_sha="b" * 40),
        lambda value: value["release"].update(version="0.4.8"),
        lambda value: value["release"].update(tag="v0.4.8"),
        lambda value: value["checks"]["tests"].update(status="unknown"),
        lambda value: value["checks"].pop("tests"),
        lambda value: value.update(token="SECRET"),
        lambda value: value["tools"][0].update(version="1.0.0 token=SECRET"),
        lambda value: value["checks"]["tests"].update(step_conclusion="failure"),
        lambda value: value["workflow"].update(run_id=True),
        lambda value: value.update(schema_version=True),
    ],
)
def test_invalid_or_sensitive_manifest_is_rejected(mutation):
    value = make_manifest()
    mutation(value)
    with pytest.raises(ValueError):
        evidence.validate(value, REPOSITORY, TAG, HEAD)


def test_artifact_identity_and_api_errors_are_not_success():
    api = ManifestGitHub()
    api.artifacts[0]["workflow_run"]["head_sha"] = "b" * 40
    with pytest.raises(ValueError, match="Artifact identity"):
        make_manifest(api)

    def unavailable(*_):
        raise release.ApiError(503)

    with pytest.raises(release.ApiError):
        make_manifest(unavailable)


def test_pagination_preserves_current_attempt():
    calls = []

    def api(path):
        calls.append(path)
        return {"jobs": list(range(100)) if path.endswith("&page=1") else [100]}

    assert len(evidence.pages(api, "actions/runs/123/attempts/2/jobs", "jobs")) == 101
    assert calls[-1].endswith("page=2")


def test_schema_registry_enums_and_required_contract_do_not_drift():
    schema = json.loads(
        (ROOT / "docs/architecture/release/manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    fields = schema["properties"]
    assert (
        schema["$id"]
        == "https://github.com/dymzz/engineering-ci/blob/main/docs/architecture/release/manifest.schema.json"
    )
    assert set(fields["checks"]["required"]) == set(evidence.CHECKS) | set(
        evidence.DAGGER_POLICY_CHECKS
    ) | {
        "core_coverage_threshold",
        "consumer_failure_probe",
    }
    assert set(schema["$defs"]["check"]["properties"]["status"]["enum"]) == set(
        evidence.STATES.values()
    )
    tool = fields["tools"]["items"]["properties"]
    assert set(tool["name"]["enum"]) == evidence.TOOLS
    assert set(tool["scope"]["enum"]) == evidence.SCOPES
    assert tool["version"]["pattern"] == evidence.TOOL_VERSION.pattern
    pairs = {
        (
            entry["contains"]["properties"]["scope"]["const"],
            entry["contains"]["properties"]["name"]["const"],
        )
        for entry in fields["tools"]["allOf"]
    }
    assert pairs == evidence.REQUIRED_TOOLS
    assert (
        set(fields["artifacts"]["items"]["properties"]["name"]["enum"])
        == evidence.ARTIFACT_NAMES
    )
    assert fields["tools"]["maxItems"] == len(evidence.REQUIRED_TOOLS)
    assert fields["artifacts"]["maxItems"] == len(evidence.ARTIFACT_NAMES)
    checks = fields["checks"]["properties"]
    for key, (job, step) in evidence.CHECKS.items():
        assert checks[key]["properties"]["job"]["const"] == job
        assert checks[key]["properties"]["step"]["const"] == step
    for key, name in evidence.DAGGER_POLICY_CHECKS.items():
        assert checks[key]["properties"]["dagger_check"]["const"] == name
    for key in ("core_coverage_threshold", "consumer_failure_probe"):
        assert (
            checks[key]["properties"]["reason"]["const"]
            == make_manifest()["checks"][key]["reason"]
        )


def test_native_artifact_ids_are_exact_and_missing_values_are_not_inferred():
    needs = {}
    for name, (job, output) in evidence.ARTIFACT_OUTPUTS.items():
        needs.setdefault(job, {"outputs": {}})["outputs"][output] = str(IDS[name])
    assert evidence.artifact_ids(needs) == IDS
    for invalid in ("", "0", "045", "1\n2", True, 45):
        wrong = copy.deepcopy(needs)
        wrong["complete-ci"]["outputs"]["pytest_artifact_id"] = invalid
        assert "pytest-report" not in evidence.artifact_ids(wrong)


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", 999),
        ("run_attempt", 2),
        ("head_sha", "b" * 40),
    ],
)
def test_job_identity_from_wrong_attempt_or_commit_is_rejected(field, value):
    api = ManifestGitHub()
    api.jobs[0][field] = value
    with pytest.raises(ValueError, match="Run, attempt and commit"):
        make_manifest(api)


def test_old_artifacts_of_same_run_and_sha_are_not_enumerated_or_selected():
    api = ManifestGitHub()
    old = {
        **api.artifacts[0],
        "id": 999,
        "url": f"https://api.github.com/repos/{REPOSITORY}/actions/artifacts/999",
    }
    api.artifacts.append(old)
    value = make_manifest(api)
    assert {item["id"] for item in value["artifacts"]} == set(IDS.values())
    assert 999 not in {item["id"] for item in value["artifacts"]}
    api.artifacts[0]["workflow_run"]["id"] = 999
    with pytest.raises(ValueError, match="Artifact identity"):
        make_manifest(api)


def test_expired_artifacts_block_new_evidence_but_metadata_can_validate_history():
    api = ManifestGitHub()
    api.artifacts[0]["expired"] = True
    assert make_manifest(api)["result"] == "failed"
    item = api.artifacts[0]
    record = evidence.artifact_metadata(
        api,
        REPOSITORY,
        item["name"],
        item["id"],
        HEAD,
        123,
        allow_expired=True,
    )
    assert record["id"] == item["id"]


def test_no_extra_test_execution_or_consumer_defaults():
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert workflow.count("uses: ./.github/workflows/ci.yml") == 1
    assert workflow.count("uses: ./.github/workflows/reusable-python-ci.yml") == 1
    assert "dagger call" not in workflow and "pytest -q" not in workflow
    assert "publish=false" not in workflow  # YAML boolean default remains false.
    assert "generate_release_notes" in (
        ROOT / "src/engineering_ci/release/publishing.py"
    ).read_text(encoding="utf-8")
