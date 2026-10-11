"""The independent definition preserves CI contracts and safe dependency scopes."""

import copy
import json

import pytest

from engineering_ci import checks, pipeline, toolchain


def test_existing_checks_and_ci_share_the_configuration():
    configured = pipeline.PIPELINE
    assert tuple(configured["profiles"]["pr"]) == checks.PR_CHECKS
    assert toolchain.RUFF_VERSION == configured["toolchain"]["ruff"]
    assert toolchain.UV_VERSION == configured["toolchain"]["uv"]
    assert toolchain.PYTHON_VERSION == configured["toolchain"]["python"]
    workflow = (checks.ROOT / ".github/workflows/ci.yml").read_text()
    for spec in configured["checks"].values():
        if "ci_step" in spec:
            assert f"name: {spec['ci_step']}" in workflow
    for name in checks.PR_CHECKS:
        assert "config/pipeline.toml" in checks.COMMON_INPUTS
        assert checks.SCOPES[name] == tuple(
            configured["scopes"][configured["checks"][name]["scope"]]
        )
    assert "aria2c" not in configured["checks"]["pytest"]["required_tools"]
    assert configured["checks"]["pytest"]["optional_backends"] == ["aria2"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.update(schema_version=2),
        lambda data: data["checks"]["pytest"].update(required_tools=["uv", "aria2c"]),
        lambda data: data["checks"]["pytest"].update(optional_backends=["missing"]),
        lambda data: data["checks"]["ruff-lint"].update(required_tools=["uvx"]),
        lambda data: data["profiles"].update(pr=["ruff-lint"]),
        lambda data: data["arguments"].update(pytest=["--junitxml=other.xml"]),
        lambda data: data["scopes"].update(ruff=["../outside"]),
        lambda data: data["scopes"].update(ruff=["D:/outside"]),
        lambda data: data["scopes"].update(ruff=["/outside"]),
        lambda data: data["checks"]["pytest"].update(ci_step=False),
        lambda data: data["checks"]["pytest"].update(required_tools=[]),
        lambda data: data["inputs"].update(common=["README.md"]),
        lambda data: data["inputs"].update(release_metadata=["pyproject.toml"]),
        lambda data: data["checks"]["lock"].update(version_only_reuse=True),
        lambda data: data["scopes"].update(pytest=["src"]),
    ],
)
def test_invalid_definitions_stop_before_tools(tmp_path, monkeypatch, mutation):
    data = copy.deepcopy(pipeline.PIPELINE)
    mutation(data)
    path = tmp_path / "pipeline.toml"
    path.touch()
    monkeypatch.setattr(pipeline.tomllib, "load", lambda stream: data)
    with pytest.raises(ValueError, match="Invalid pipeline configuration"):
        pipeline.load(path)


def test_missing_configuration_does_not_silently_use_defaults(tmp_path):
    with pytest.raises(ValueError, match="Invalid pipeline configuration"):
        pipeline.load(tmp_path / "missing.toml")


def test_dagger_cli_and_engine_pins_match_the_module():
    version = json.loads((checks.ROOT / "dagger.json").read_text(encoding="utf-8"))[
        "engineVersion"
    ]
    workflow = (checks.ROOT / ".github/workflows/reusable-python-ci.yml").read_text(
        encoding="utf-8"
    )
    assert f"DAGGER_VERSION: '{version.removeprefix('v')}'" in workflow
    assert workflow.count('"registry.dagger.io/engine:v$DAGGER_VERSION"') == 2
    assert 'BIN_DIR="$RUNNER_TEMP/dagger-bin"' in workflow
    assert "DAGGER_ENGINE: container://dagger-engine" in workflow


def test_pipeline_input_changes_invalidate_old_check_identity(tmp_path):
    path = tmp_path / "config/pipeline.toml"
    path.parent.mkdir()
    path.write_text("# original definitions\n")
    initial, _ = checks.snapshot(tmp_path, "version")
    path.write_text("# changed tool or dependency definitions\n")
    changed, _ = checks.snapshot(tmp_path, "version")
    assert checks.digest(initial) != checks.digest(changed)
    assert "SECRET" not in json.dumps(changed)


def test_dagger_lock_changes_invalidate_pytest_identity(tmp_path):
    path = tmp_path / "dagger.lock"
    path.write_text("original image pins\n", encoding="utf-8")
    initial, _ = checks.snapshot(tmp_path, "pytest")
    path.write_text("updated image pins\n", encoding="utf-8")
    changed, _ = checks.snapshot(tmp_path, "pytest")
    assert checks.digest(initial) != checks.digest(changed)


def test_delivery_settings_are_loaded_without_changing_existing_operations():
    from engineering_ci.cli import release

    delivery = pipeline.PIPELINE["delivery"]
    assert delivery == {
        "checks_profile": "pr",
        "release": {"workflow": "release.yml", "branch": "main", "trigger": "manual"},
    }
    assert delivery["release"] is release.RELEASE_CONFIG
    assert pipeline.PIPELINE["profiles"][delivery["checks_profile"]] == list(
        checks.PR_CHECKS
    )


def test_schema_one_without_delivery_keeps_legacy_behavior(tmp_path, monkeypatch):
    data = copy.deepcopy(pipeline.PIPELINE)
    data.pop("delivery")
    path = tmp_path / "pipeline.toml"
    path.touch()
    monkeypatch.setattr(pipeline.tomllib, "load", lambda _: data)
    loaded = pipeline.load(path)
    assert loaded["schema_version"] == 1
    assert loaded["delivery"] == pipeline.PIPELINE["delivery"]


@pytest.mark.parametrize(
    "delivery",
    [
        None,
        [],
        {},
        {"checks_profile": "pr"},
        {"checks_profile": "missing", "release": {}},
        {"checks_profile": "ruff", "release": {}},
        {"checks_profile": ["pr"], "release": {}},
        {"checks_profile": "__import__('os').system('SECRET')", "release": {}},
        {"checks_profile": "pr", "release": "release.yml"},
        {
            "checks_profile": "pr",
            "release": {"workflow": "release.yml", "branch": "main"},
        },
        {
            "checks_profile": "pr",
            "release": {"workflow": "other.yml", "branch": "main", "trigger": "manual"},
        },
        {
            "checks_profile": "pr",
            "release": {
                "workflow": "../release.yml",
                "branch": "main",
                "trigger": "manual",
            },
        },
        {
            "checks_profile": "pr",
            "release": {
                "workflow": "release.yml",
                "branch": "candidate",
                "trigger": "manual",
            },
        },
        {
            "checks_profile": "pr",
            "release": {"workflow": "release.yml", "branch": "main", "trigger": "push"},
        },
        {
            "checks_profile": "pr",
            "release": {"workflow": "release.yml", "branch": "main", "trigger": True},
        },
        {
            "checks_profile": "pr",
            "release": {
                "workflow": "release.yml",
                "branch": "main",
                "trigger": "manual",
                "skip_verification": True,
            },
        },
        {
            "checks_profile": "pr",
            "release": {
                "workflow": "release.yml",
                "branch": "main",
                "trigger": "manual",
            },
            "command": "echo SECRET",
        },
    ],
)
def test_invalid_delivery_is_rejected_before_any_process(
    tmp_path, monkeypatch, delivery
):
    import subprocess

    data = copy.deepcopy(pipeline.PIPELINE)
    data["delivery"] = delivery
    path = tmp_path / "pipeline.toml"
    path.touch()
    monkeypatch.setattr(pipeline.tomllib, "load", lambda _: data)
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: pytest.fail("No operation may run")
    )
    with pytest.raises(ValueError, match="Invalid pipeline configuration"):
        pipeline.load(path)
