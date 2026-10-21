"""Reject semantic Workflow drift without executing checks or GitHub operations."""

import copy
import json
import shutil
import subprocess
import sys

import pytest
import yaml

from engineering_ci import checks, pipeline
from engineering_ci import workflow_consistency as mod
from engineering_ci.release import publishing, version
from engineering_ci.release.registry import RELEASE_DEPENDENCIES


@pytest.fixture
def workflows(tmp_path):
    directory = tmp_path / ".github/workflows"
    directory.mkdir(parents=True)
    for name in ("ci.yml", "release.yml", "reusable-python-ci.yml"):
        shutil.copyfile(checks.ROOT / ".github/workflows" / name, directory / name)
    return tmp_path


def change(root, filename, mutation):
    path = root / ".github/workflows" / filename
    data = mod._workflow(path)
    mutation(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def bound_step(data, job, check):
    """Mutate the registered check rather than an unrelated bootstrap step position."""
    name = pipeline.PIPELINE["checks"][check]["ci_step"]
    return next(step for step in data["jobs"][job]["steps"] if step.get("name") == name)


def test_current_workflows_and_yaml_boolean_rules(workflows):
    data = mod._workflow(workflows / ".github/workflows/release.yml")
    assert "on" in data and True not in data
    assert data["on"]["workflow_dispatch"]["inputs"]["publish"]["default"] is False
    mod.validate(root=workflows)


def test_core_ci_and_release_do_not_require_retired_private_package_credentials(
    workflows,
):
    ci = mod._workflow(workflows / ".github/workflows/ci.yml")
    release = mod._workflow(workflows / ".github/workflows/release.yml")
    assert "secrets" not in ci["on"]["workflow_call"]
    assert "secrets" not in release["jobs"]["core-tests"]
    assert (
        "dependency_security_git_policy" not in release["jobs"]["complete-ci"]["with"]
    )
    assert (
        release["jobs"]["complete-ci"]["with"]["dependency_security_mode"] == "enforce"
    )
    for workflow in (ci, release):
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                assert "ENGINEERING_CI_READ_TOKEN" not in step.get("env", {})
                assert "auth setup-git" not in step.get("run", "")
    for job in ("candidate", "verified", "publish"):
        invocation = next(
            step["run"]
            for step in release["jobs"][job]["steps"]
            if "engineering_ci.workflow_actions release " in step.get("run", "")
        )
        assert "uv run --locked --no-default-groups python" in invocation


@pytest.mark.parametrize(
    "filename, mutation",
    [
        ("ci.yml", lambda d: d["on"]["pull_request"].update(branches=["other"])),
        ("ci.yml", lambda d: d["jobs"]["python-ci"].update(name="different")),
        ("ci.yml", lambda d: d["jobs"]["python-ci"].update(**{"if": "false"})),
        (
            "ci.yml",
            lambda d: d["jobs"]["python-ci"]["steps"][3].update(run="echo passed"),
        ),
        (
            "ci.yml",
            lambda d: d["jobs"]["python-ci"]["steps"][3].update(
                **{"continue-on-error": True}
            ),
        ),
        (
            "ci.yml",
            lambda d: d["jobs"]["python-ci"]["steps"].append(
                copy.deepcopy(d["jobs"]["python-ci"]["steps"][3])
            ),
        ),
        (
            "ci.yml",
            lambda d: d["jobs"]["python-ci"]["steps"][2]["with"].update(
                version="other"
            ),
        ),
        (
            "ci.yml",
            lambda d: bound_step(d, "version-metadata", "version").update(
                run="python3 -m engineering_ci.workflow_actions checks version"
            ),
        ),
        (
            "ci.yml",
            lambda d: bound_step(d, "sync-tool-tests", "pytest").update(
                run=bound_step(d, "sync-tool-tests", "pytest")["run"] + " || true"
            ),
        ),
        (
            "ci.yml",
            lambda d: d["on"]["workflow_call"]["outputs"]["core_artifact_id"].update(
                value="other"
            ),
        ),
        (
            "ci.yml",
            lambda d: d["on"]["workflow_call"]["inputs"]["coverage"].update(
                default=True
            ),
        ),
        ("release.yml", lambda d: d["on"].update(push={"branches": ["main"]})),
        (
            "release.yml",
            lambda d: d["on"]["workflow_dispatch"]["inputs"]["publish"].update(
                default=True
            ),
        ),
        ("release.yml", lambda d: d["jobs"]["publish"].update(needs=["candidate"])),
        (
            "release.yml",
            lambda d: d["jobs"]["publish"].update(**{"if": "${{ always() }}"}),
        ),
        (
            "release.yml",
            lambda d: d["jobs"]["candidate"].update(permissions={"contents": "write"}),
        ),
        (
            "release.yml",
            lambda d: d["jobs"]["publish"].update(**{"continue-on-error": True}),
        ),
        (
            "release.yml",
            lambda d: d["jobs"]["core-tests"].update(
                uses="dymzz/engineering-ci/.github/workflows/ci.yml@main"
            ),
        ),
        (
            "release.yml",
            lambda d: d["jobs"]["core-tests"]["with"].update(coverage=False),
        ),
        (
            "release.yml",
            lambda d: d["jobs"]["complete-ci"]["with"].update(
                dependency_security_mode="off"
            ),
        ),
        (
            "release.yml",
            lambda d: d["jobs"]["complete-ci"]["with"].update(
                pytest_check="another-test"
            ),
        ),
        (
            "release.yml",
            lambda d: d["jobs"]["candidate"]["steps"][0]["with"].update(ref="main"),
        ),
        (
            "release.yml",
            lambda d: d["jobs"]["candidate"]["steps"][-1].update(
                run="python3 -m engineering_ci.workflow_actions release prepare"
            ),
        ),
        (
            "reusable-python-ci.yml",
            lambda d: d["jobs"]["checks"]["steps"].append(
                {"name": "Run Dagger Checks", "run": "echo success"}
            ),
        ),
    ],
)
def test_changed_bindings_are_rejected(workflows, filename, mutation):
    change(workflows, filename, mutation)
    with pytest.raises(ValueError, match="Pipeline / Workflow mismatch"):
        mod.validate(root=workflows)


@pytest.mark.parametrize("filename", ["ci.yml", "release.yml"])
def test_ordinary_steps_and_read_only_jobs_do_not_redeclare_the_workflow(
    workflows, filename
):
    def adjust(data):
        for job in data["jobs"].values():
            if "steps" in job:
                job["timeout-minutes"] = 12
                for step in job["steps"]:
                    if step.get("id") == "check_toolchain":
                        step["name"] = "Load configured tools"
                job["steps"].append(
                    {"name": "Diagnostics", "run": "echo changed diagnostics"}
                )
        data["jobs"]["diagnostics"] = {
            "runs-on": "ubuntu-24.04",
            "permissions": {},
            "if": "${{ always() }}",
            "continue-on-error": True,
            "strategy": {"matrix": {"sample": [1, 2]}},
            "steps": [{"run": "echo diagnostic matrix"}],
        }
        if filename == "release.yml":
            data["jobs"]["verified"]["needs"].append("diagnostics")
            data["jobs"]["publish"]["needs"].append("diagnostics")

    change(workflows, filename, adjust)
    mod.validate(root=workflows)


@pytest.mark.parametrize("filename", ["ci.yml", "release.yml"])
@pytest.mark.parametrize(
    "permissions", [{"contents": "write"}, {"id-token": "write"}, "write-all"]
)
def test_extra_jobs_cannot_acquire_write_permissions(workflows, filename, permissions):
    change(
        workflows,
        filename,
        lambda d: d["jobs"].update(
            extra={
                "runs-on": "ubuntu-24.04",
                "permissions": permissions,
                "steps": [{"run": "echo extra"}],
            }
        ),
    )
    with pytest.raises(ValueError, match="permissions"):
        mod.validate(root=workflows)


@pytest.mark.parametrize(
    "job_id,dependency",
    [
        (job_id, dependency)
        for job_id, dependencies in RELEASE_DEPENDENCIES.items()
        for dependency in sorted(dependencies)
    ],
)
def test_every_critical_direct_dependency_is_required(workflows, job_id, dependency):
    def remove(data):
        job = data["jobs"][job_id]
        actual = job["needs"]
        job["needs"] = [
            value
            for value in ([actual] if isinstance(actual, str) else actual)
            if value != dependency
        ]

    change(workflows, "release.yml", remove)
    with pytest.raises(ValueError, match=f"{job_id} dependencies"):
        mod.validate(root=workflows)


@pytest.mark.parametrize("job_id", ["verified", "publish"])
def test_extra_dependencies_must_pass_at_runtime_as_well_as_static_validation(
    workflows, job_id
):
    change(
        workflows,
        "release.yml",
        lambda d: (
            d["jobs"].update(
                diagnostics={
                    "runs-on": "ubuntu-24.04",
                    "steps": [{"run": "echo diagnostics"}],
                }
            ),
            d["jobs"][job_id]["needs"].append("diagnostics"),
        ),
    )
    mod.validate(root=workflows)
    head = "a" * 40
    facts = {name: {"result": "success"} for name in RELEASE_DEPENDENCIES[job_id]}
    facts["candidate"]["outputs"] = {"sha": head}
    facts["diagnostics"] = {"result": "success"}
    publishing.require_jobs(facts, head, publishing=job_id == "publish")
    for state in ("failure", "skipped", "cancelled", "unknown"):
        facts["diagnostics"]["result"] = state
        with pytest.raises(ValueError, match="actually succeeded"):
            publishing.require_jobs(facts, head, publishing=job_id == "publish")


@pytest.mark.parametrize(
    "job_id,mutation",
    [
        (
            "evidence",
            lambda j: j["outputs"].update(
                facts_artifact_id="${{ steps.other.outputs.artifact-id }}"
            ),
        ),
        (
            "verified",
            lambda j: j["outputs"].update(
                manifest_artifact_id="${{ steps.other.outputs.artifact-id }}"
            ),
        ),
        (
            "evidence",
            lambda j: j["steps"][2]["with"].update(
                **{
                    "artifact-ids": "${{ needs.complete-ci.outputs.security_artifact_id }}"
                }
            ),
        ),
        (
            "publish",
            lambda j: j["steps"][1]["with"].update(
                **{"artifact-ids": "${{ needs.evidence.outputs.facts_artifact_id }}"}
            ),
        ),
        (
            "verified",
            lambda j: j["steps"][-1]["with"].update(name="different-manifest"),
        ),
    ],
)
def test_artifact_producer_consumer_contracts_cannot_drift(workflows, job_id, mutation):
    change(workflows, "release.yml", lambda d: mutation(d["jobs"][job_id]))
    with pytest.raises(ValueError, match="Artifact|exactly one Step"):
        mod.validate(root=workflows)


def test_artifact_step_ids_can_change_when_declared_outputs_follow_them(workflows):
    def rename(data):
        job = data["jobs"]["evidence"]
        job["steps"][-1]["id"] = "facts_receipt"
        job["outputs"]["facts_artifact_id"] = (
            "${{ steps.facts_receipt.outputs.artifact-id }}"
        )

    change(workflows, "release.yml", rename)
    mod.validate(root=workflows)


def test_a_name_in_comments_or_the_wrong_job_is_not_evidence(workflows):
    path = workflows / ".github/workflows/ci.yml"
    path.write_text(
        path.read_text().replace("name: Ruff Lint", "name: Another check")
        + "\n# name: Ruff Lint\n"
    )
    with pytest.raises(ValueError, match="Ruff Lint"):
        mod.validate(root=workflows)

    shutil.copyfile(checks.ROOT / ".github/workflows/ci.yml", path)

    def move(data):
        step = data["jobs"]["python-ci"]["steps"].pop(3)
        data["jobs"]["sync-tool-tests"]["steps"].append(step)

    change(workflows, "ci.yml", move)
    with pytest.raises(ValueError, match="Ruff Lint"):
        mod.validate(root=workflows)


@pytest.mark.parametrize(
    "text",
    [
        "on: {}\non: {}\n",
        "on: [\n",
        "!!python/object/apply:os.system ['SECRET']",
        "[1, 2]",
    ],
)
def test_malformed_or_unsafe_yaml_is_refused_without_raw_content(workflows, text):
    path = workflows / ".github/workflows/ci.yml"
    path.write_text(text)
    with pytest.raises(ValueError) as error:
        mod.validate(root=workflows)
    assert "SECRET" not in str(error.value)


def test_toml_step_mapping_must_match_the_actual_invocation(workflows):
    config = copy.deepcopy(pipeline.PIPELINE)
    config["checks"]["ruff-lint"]["ci_step"] = config["checks"]["ruff-format"][
        "ci_step"
    ]
    with pytest.raises(ValueError, match="check invocation"):
        mod.validate(root=workflows, config=config)


def test_ci_tests_alias_cannot_silently_select_ruff(workflows):
    config = copy.deepcopy(pipeline.PIPELINE)
    config["profiles"]["tests"] = ["ruff-lint"]
    with pytest.raises(ValueError, match="tests alias"):
        mod.validate(root=workflows, config=config)


def test_complete_profile_or_toml_section_order_does_not_change_ci_binding(workflows):
    config = copy.deepcopy(pipeline.PIPELINE)
    config["profiles"]["pr"].reverse()
    config["checks"] = dict(reversed(config["checks"].items()))
    mod.validate(root=workflows, config=config)


def test_release_execution_context_is_bound_to_candidate(workflows):
    head = "a" * 40
    env = {
        "GITHUB_REPOSITORY": "dymzz/engineering-ci",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_WORKFLOW_REF": "dymzz/engineering-ci/.github/workflows/release.yml@refs/heads/main",
        "GITHUB_WORKFLOW_SHA": head,
        "GITHUB_SHA": head,
    }
    mod.validate(root=workflows, environment=env)
    for field, wrong in (
        (
            "GITHUB_WORKFLOW_REF",
            "dymzz/engineering-ci/.github/workflows/other.yml@refs/heads/main",
        ),
        ("GITHUB_WORKFLOW_SHA", "b" * 40),
        ("GITHUB_EVENT_NAME", "push"),
    ):
        with pytest.raises(ValueError, match="mismatch"):
            mod.validate(root=workflows, environment={**env, field: wrong})


def test_publication_refuses_drift_before_git_or_api(workflows, monkeypatch, capsys):
    change(workflows, "release.yml", lambda d: d["jobs"]["publish"].update(needs=[]))
    validate = mod.validate
    monkeypatch.setattr(mod, "validate", lambda **kwargs: validate(root=workflows))

    def forbidden(*args, **kwargs):
        pytest.fail("No Git or GitHub operation may precede configuration validation")

    monkeypatch.setattr(publishing.subprocess, "check_output", forbidden)
    monkeypatch.setattr(publishing, "GitHub", forbidden)
    assert publishing.main(["prepare"]) == 1
    assert "publish dependencies" in capsys.readouterr().err


def test_existing_version_check_propagates_configuration_failure(
    workflows, monkeypatch, capsys
):
    change(workflows, "ci.yml", lambda d: d["on"]["push"].update(branches=["other"]))
    validate = mod.validate
    monkeypatch.setattr(mod, "validate", lambda: validate(root=workflows))
    assert version.main(["check"]) == 1
    assert "CI push branch" in capsys.readouterr().err


def test_real_child_version_command_fails_on_workflow_drift(workflows):
    change(
        workflows,
        "ci.yml",
        lambda d: bound_step(d, "version-metadata", "version").update(run="echo pass"),
    )
    code = """import sys
from pathlib import Path
from engineering_ci import workflow_consistency as w
from engineering_ci.release.version import main
validate = w.validate
w.validate = lambda: validate(root=Path(sys.argv[1]))
raise SystemExit(main(['check']))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(workflows)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "check invocation" in result.stderr
    assert "PASS" not in result.stdout


@pytest.mark.parametrize("name", checks.PR_CHECKS)
@pytest.mark.parametrize(
    "path",
    [
        "src/engineering_ci/workflow_consistency.py",
        "src/engineering_ci/release/registry.py",
        ".github/workflows/release.yml",
        ".github/workflows/reusable-python-ci.yml",
    ],
)
def test_workflow_and_validator_changes_expire_every_check(tmp_path, name, path):
    initial, _ = checks.snapshot(tmp_path, name)
    changed = tmp_path / path
    changed.parent.mkdir(parents=True, exist_ok=True)
    changed.write_text("changed Workflow contract")
    after, _ = checks.snapshot(tmp_path, name)
    assert after != initial
    assert "changed Workflow contract" not in json.dumps(after)


def test_toolchain_bootstrap_does_not_require_yaml_or_execution_dependencies():
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); from engineering_ci.workflow_actions import main; assert main(['checks', 'toolchain']) == 0; assert 'yaml' not in sys.modules",
            str(checks.ROOT / "src"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_schema_one_lock_mapping_is_migrated(tmp_path, monkeypatch):
    config = copy.deepcopy(pipeline.PIPELINE)
    config["checks"]["lock"].pop("ci_job")
    config["checks"]["lock"].pop("ci_step")
    path = tmp_path / "pipeline.toml"
    path.touch()
    monkeypatch.setattr(pipeline.tomllib, "load", lambda _: config)
    actual = pipeline.load(path)
    assert actual["checks"]["lock"]["ci_step"] == actual["checks"]["version"]["ci_step"]


def test_version_identity_records_the_actual_locked_python_and_yaml(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        checks, "tool", lambda *args: {"executable": "uv", "version": checks.UV_VERSION}
    )

    def probe(args, *unused):
        assert args[:3] == ["uv", "run", "--locked"]
        assert "pyyaml" in args[-1] and "pytest" not in args[-1]
        return json.dumps({"python": "3.14.8", "pyyaml": "6.0.3"})

    monkeypatch.setattr(checks, "probe", probe)
    tools = checks.preflight("version", tmp_path, None, lambda name: "uv", {})
    assert tools["python"] == {"version": "3.14.8"}
    assert tools["pyyaml"] == {"version": "6.0.3"}
    monkeypatch.setattr(
        checks, "probe", lambda *args: '{"python":"3.13.9","pyyaml":"6.0.3"}'
    )
    with pytest.raises(checks.PreconditionError, match="Locked Python/version"):
        checks.preflight("version", tmp_path, None, lambda name: "uv", {})
