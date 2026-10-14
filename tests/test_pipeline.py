"""The independent definition preserves CI contracts and safe dependency scopes."""

import copy
import json

import pytest

from engineering_ci import checks, dagger_checks, pipeline, toolchain
from engineering_ci.check_operations import OPERATIONS, command, container_plan


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
    assert set(configured["checks"]) == set(OPERATIONS)


@pytest.mark.parametrize(
    "location", ["pipeline", "arguments", "inputs", "checks.pytest", "checks.ruff-lint"]
)
@pytest.mark.parametrize(
    "field", ["command", "handler", "needs", "permissions", "env", "shell"]
)
def test_unknown_or_unimplemented_fields_are_rejected_before_tools(
    tmp_path, monkeypatch, location, field
):
    import subprocess

    data = copy.deepcopy(pipeline.PIPELINE)
    table = data
    if location != "pipeline":
        for part in location.split("."):
            table = table[part]
    table[field] = "SECRET_configured_code"
    path = tmp_path / "pipeline.toml"
    path.touch()
    monkeypatch.setattr(pipeline.tomllib, "load", lambda _: data)
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: pytest.fail("No tool may run")
    )
    with pytest.raises(ValueError, match="Unsupported or missing fields") as error:
        pipeline.load(path)
    assert "SECRET" not in str(error.value)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data["profiles"].update(pytest=["ruff-lint"]),
        lambda data: data["profiles"].update(tests=["version"]),
        lambda data: data["profiles"].update(extra=[]),
        lambda data: data["scopes"].update(unused=["README.md"]),
        lambda data: data["scopes"].update(ruff=[]),
        lambda data: data["scopes"]["pytest"].remove("src"),
        lambda data: data["scopes"]["version"].remove("uv.lock"),
        lambda data: data["scopes"]["lock"].remove("uv.toml"),
        lambda data: data["checks"]["pytest"]["required_tools"].remove("gh"),
        lambda data: data["checks"]["pytest"]["linux_tools"].remove("bash"),
        lambda data: data["checks"]["ruff-lint"].update(optional_backends=["aria2"]),
        lambda data: data["checks"]["pytest"].update(optional_backends=[]),
    ],
)
def test_shadowed_operations_unused_definitions_and_missing_contracts_are_rejected(
    tmp_path, monkeypatch, mutation
):
    data = copy.deepcopy(pipeline.PIPELINE)
    mutation(data)
    path = tmp_path / "pipeline.toml"
    path.touch()
    monkeypatch.setattr(pipeline.tomllib, "load", lambda _: data)
    with pytest.raises(ValueError, match="Invalid pipeline configuration"):
        pipeline.load(path)


def test_configured_ruff_scope_and_tool_arguments_drive_the_registered_command():
    data = copy.deepcopy(pipeline.PIPELINE)
    data["scopes"]["lint-only"] = ["src/engineering_ci/pipeline.py"]
    data["checks"]["ruff-lint"]["scope"] = "lint-only"
    data["arguments"]["ruff_target"] = "py314"
    data["toolchain"]["ruff"] = "0.16.9"
    args = command("ruff-lint", data)
    assert args[2] == "ruff==0.16.9"
    assert args[6:] == ["py314", "src/engineering_ci/pipeline.py"]
    assert (
        command("ruff-format", data)[-len(data["scopes"]["ruff"]) :]
        == data["scopes"]["ruff"]
    )
    data["arguments"]["pytest"] = ["-vv", "--tb=short"]
    data["toolchain"]["python"] = "3.15"
    args = command("pytest", data)
    assert args[args.index("--python") + 1] == "3.15"
    assert args[args.index("pytest") + 1 :] == [
        "-vv",
        "--tb=short",
        "--junitxml=core-junit.xml",
    ]
    with pytest.raises(ValueError, match="Unsupported check"):
        command("os.system", data)


def test_container_plan_uses_full_profile_and_explicit_optional_tools():
    config = copy.deepcopy(pipeline.PIPELINE)
    first = container_plan(config)
    assert first["packages"] == ["aria2", "bash", "ca-certificates", "gh", "git"]
    assert first["toolchain"] == {
        name: config["toolchain"][name] for name in ("python", "uv")
    }
    config["profiles"]["pr"].reverse()
    assert container_plan(config) == first
    config["checks"]["pytest"]["optional_backends"] = []
    config["optional_backends"] = {}
    assert "aria2" not in container_plan(config)["packages"]


def test_registered_operations_are_inputs_for_every_check(tmp_path):
    path = tmp_path / "src/engineering_ci/check_operations.py"
    path.parent.mkdir(parents=True)
    before = {name: checks.snapshot(tmp_path, name)[0] for name in checks.PR_CHECKS}
    path.write_text("# changed registered command or dependency\n")
    assert all(
        checks.snapshot(tmp_path, name)[0] != before[name] for name in checks.PR_CHECKS
    )


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
        lambda data: data["scopes"].update(ruff=["--output-format=json"]),
        lambda data: data["scopes"].update(ruff=["src\n--fix"]),
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
    version = dagger_checks.engine_version(checks.ROOT)
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


@pytest.mark.parametrize("backend", ["auto", "native", "dagger"])
def test_execution_backend_is_explicit_and_not_an_arbitrary_command(
    tmp_path, monkeypatch, backend
):
    data = copy.deepcopy(pipeline.PIPELINE)
    data["execution"] = {"backend": backend}
    path = tmp_path / "pipeline.toml"
    path.touch()
    monkeypatch.setattr(pipeline.tomllib, "load", lambda _: data)
    assert pipeline.load(path)["execution"] == {"backend": backend}


@pytest.mark.parametrize(
    "value",
    [None, {}, {"backend": "unknown"}, {"backend": "dagger", "command": "echo PASS"}],
)
def test_invalid_execution_is_rejected_before_operations(tmp_path, monkeypatch, value):
    data = copy.deepcopy(pipeline.PIPELINE)
    data["execution"] = value
    path = tmp_path / "pipeline.toml"
    path.touch()
    monkeypatch.setattr(pipeline.tomllib, "load", lambda _: data)
    with pytest.raises(ValueError, match="Invalid pipeline configuration"):
        pipeline.load(path)


def test_legacy_schema_one_keeps_native_execution(tmp_path, monkeypatch):
    data = copy.deepcopy(pipeline.PIPELINE)
    data.pop("execution")
    path = tmp_path / "pipeline.toml"
    path.touch()
    monkeypatch.setattr(pipeline.tomllib, "load", lambda _: data)
    assert pipeline.load(path)["execution"] == {"backend": "native"}


@pytest.mark.parametrize(
    "path",
    [
        "src/engineering_ci/dagger_checks.py",
        "src/engineering_ci/checks_container.py",
        ".dagger/src/engineering_ci/main.py",
        ".dagger/src/engineering_ci/pr_checks.py",
        "dagger-module.toml",
        ".dagger/sdk",
        "dagger.toml",
    ],
)
def test_container_executor_inputs_cannot_be_removed(tmp_path, monkeypatch, path):
    data = copy.deepcopy(pipeline.PIPELINE)
    data["inputs"]["common"].remove(path)
    config = tmp_path / "pipeline.toml"
    config.touch()
    monkeypatch.setattr(pipeline.tomllib, "load", lambda _: data)
    with pytest.raises(ValueError, match="executor definition inputs"):
        pipeline.load(config)


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
