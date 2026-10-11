"""Read shared checks and delivery settings; no scheduling or shell templates."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path, PurePosixPath, PureWindowsPath

from ._paths import ROOT


def delivery_config(data):
    """Migrate schema 1 defaults without allowing a weaker release policy."""
    legacy = {
        "checks_profile": "pr",
        "release": {"workflow": "release.yml", "branch": "main", "trigger": "manual"},
    }
    if "delivery" not in data:
        return legacy
    delivery = data["delivery"]
    if not isinstance(delivery, dict) or set(delivery) != {"checks_profile", "release"}:
        raise ValueError("Delivery requires checks_profile and release only")
    profile = delivery["checks_profile"]
    if not isinstance(profile, str) or profile not in data["profiles"]:
        raise ValueError("Unknown delivery check profile")
    if profile != "pr":
        raise ValueError("Delivery must retain the existing complete PR profile")
    release = delivery["release"]
    if not isinstance(release, dict) or release != legacy["release"]:
        raise ValueError(
            "Release supports only the existing manual release.yml on main"
        )
    return {"checks_profile": profile, "release": dict(release)}


def load(path: Path = ROOT / "config/pipeline.toml") -> dict:
    """Reject missing or contradictory definitions before executing any check."""
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise ValueError("Unsupported pipeline schema")
        if set(data["toolchain"]) != {"uv", "ruff", "python"} or not all(
            isinstance(value, str) and re.fullmatch(r"\d+\.\d+(?:\.\d+)?", value)
            for value in data["toolchain"].values()
        ):
            raise ValueError("Invalid toolchain versions")

        def strings(value):
            return (
                isinstance(value, list)
                and all(isinstance(v, str) and v for v in value)
                and len(value) == len(set(value))
            )

        if not re.fullmatch(r"py\d+", data["arguments"]["ruff_target"]) or not strings(
            data["arguments"]["pytest"]
        ):
            raise ValueError("Invalid execution arguments")
        if any(value.startswith("--junitxml") for value in data["arguments"]["pytest"]):
            raise ValueError("JUnit destination belongs to the check invocation")
        if not strings(data["inputs"]["common"]) or not all(
            strings(v) for v in data["scopes"].values()
        ):
            raise ValueError("Invalid input scopes")
        if data["inputs"].get("release_metadata") != [
            "pyproject.toml",
            "uv.lock",
            ".dagger/pyproject.toml",
            ".dagger/uv.lock",
        ]:
            raise ValueError(
                "Release metadata must identify the four first-party version fields"
            )
        if not {
            "config/pipeline.toml",
            "src/engineering_ci/pipeline.py",
            "src/engineering_ci/checks.py",
            "src/engineering_ci/toolchain.py",
            "src/engineering_ci/workflow_consistency.py",
            "src/engineering_ci/release/registry.py",
            ".github/workflows/ci.yml",
            ".github/workflows/release.yml",
            ".github/workflows/reusable-python-ci.yml",
        } <= set(data["inputs"]["common"]):
            raise ValueError("Check definition inputs cannot be excluded")
        paths = data["inputs"]["common"] + [
            path for values in data["scopes"].values() for path in values
        ]
        if any(
            PureWindowsPath(path).drive
            or "\\" in path
            or PurePosixPath(path).is_absolute()
            or ".." in PurePosixPath(path).parts
            for path in paths
        ):
            raise ValueError("Input scopes must stay inside the repository")
        checks = data["checks"]
        supported = {"ruff-lint", "ruff-format", "pytest", "version", "lock"}
        if set(checks) != supported:
            raise ValueError("Unsupported or missing check implementation")
        if not data["profiles"]["pr"] or not all(
            strings(names) and set(names) <= supported
            for names in data["profiles"].values()
        ):
            raise ValueError("Invalid check profile")
        if set(data["profiles"]["pr"]) != supported:
            raise ValueError("PR profile must retain the existing required checks")
        data["delivery"] = delivery_config(data)
        # Schema 1 previously grouped lock with version without repeating its CI identity.
        if not {"ci_job", "ci_step"} & checks["lock"].keys():
            for key in ("ci_job", "ci_step"):
                checks["lock"][key] = checks["version"][key]
        for backend, spec in data["optional_backends"].items():
            if backend != "aria2" or spec != {
                "tool": "aria2c",
                "junit_property": "engineering_ci.optional_backend",
                "junit_value": "aria2",
            }:
                raise ValueError("Unsupported optional backend contract")
        for name, spec in checks.items():
            if "version_only_reuse" in spec and (
                name != "pytest" or spec["version_only_reuse"] is not True
            ):
                raise ValueError(
                    "Version-only reuse applies only to the existing pytest implementation"
                )
            if "ci_step" in spec and (
                not isinstance(spec.get("ci_job"), str) or not spec["ci_job"]
            ):
                raise ValueError("CI check requires a job identity")
            if "ci_step" in spec and (
                not isinstance(spec["ci_step"], str) or not spec["ci_step"]
            ):
                raise ValueError("Invalid CI step name")
            if spec["scope"] not in data["scopes"] or not all(
                strings(spec[key])
                for key in ("required_tools", "linux_tools", "optional_backends")
            ):
                raise ValueError(f"Invalid dependencies or scope: {name}")
            dependencies = set(spec["required_tools"] + spec["linux_tools"])
            if not dependencies <= {"uv", "uvx", "git", "gh", "bash"}:
                raise ValueError(f"Unsupported required tool: {name}")
            if not set(spec["optional_backends"]) <= set(data["optional_backends"]):
                raise ValueError(f"Unknown optional backend: {name}")
            if "uvx" in dependencies and "uv" not in dependencies:
                raise ValueError("uvx requires the pinned uv bootstrap")
            if (
                "uv" not in dependencies
                or name.startswith("ruff-")
                and "uvx" not in dependencies
            ):
                raise ValueError(
                    f"Missing tool required by the check implementation: {name}"
                )
        if "tests" not in data["scopes"][checks["pytest"]["scope"]]:
            raise ValueError("Full pytest inputs must include tests")
        if not set(data["inputs"]["release_metadata"]) <= set(
            data["scopes"][checks["pytest"]["scope"]]
        ):
            raise ValueError("Version-only reuse requires all four metadata inputs")
        return data
    except (OSError, KeyError, TypeError, ValueError, AttributeError) as error:
        raise ValueError(f"Invalid pipeline configuration {path}: {error}") from error


PIPELINE = load()
