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


def test_pipeline_input_changes_invalidate_old_check_identity(tmp_path):
    path = tmp_path / "config/pipeline.toml"
    path.parent.mkdir()
    path.write_text("# original definitions\n")
    initial, _ = checks.snapshot(tmp_path, "version")
    path.write_text("# changed tool or dependency definitions\n")
    changed, _ = checks.snapshot(tmp_path, "version")
    assert checks.digest(initial) != checks.digest(changed)
    assert "SECRET" not in json.dumps(changed)
