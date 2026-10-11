"""Read the repository's shared check definitions; no scheduling or shell templates."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path, PurePosixPath, PureWindowsPath

from ._paths import ROOT


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
        if not {
            "config/pipeline.toml",
            "src/engineering_ci/pipeline.py",
            "src/engineering_ci/checks.py",
            "src/engineering_ci/toolchain.py",
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
        for backend, spec in data["optional_backends"].items():
            if backend != "aria2" or spec != {
                "tool": "aria2c",
                "junit_property": "engineering_ci.optional_backend",
                "junit_value": "aria2",
            }:
                raise ValueError("Unsupported optional backend contract")
        for name, spec in checks.items():
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
                name != "version"
                and "uv" not in dependencies
                or name.startswith("ruff-")
                and "uvx" not in dependencies
            ):
                raise ValueError(
                    f"Missing tool required by the check implementation: {name}"
                )
        if "tests" not in data["scopes"][checks["pytest"]["scope"]]:
            raise ValueError("Full pytest inputs must include tests")
        return data
    except (OSError, KeyError, TypeError, ValueError, AttributeError) as error:
        raise ValueError(f"Invalid pipeline configuration {path}: {error}") from error


PIPELINE = load()
