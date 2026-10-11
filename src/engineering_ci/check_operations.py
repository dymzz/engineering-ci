"""Registered check commands and tool requirements; configuration contains no code."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

Configuration = Mapping[str, Any]


@dataclass(frozen=True)
class CheckOperation:
    command: Callable[[Configuration, bool, str], list[str]]
    required_tools: frozenset[str]
    linux_tools: frozenset[str] = frozenset()
    optional_backends: frozenset[str] = frozenset()
    required_inputs: frozenset[str] = frozenset()
    requires_release_metadata: bool = False


def _ruff(config, name, operation):
    """Use the configured target and the same scope as the check's input identity."""
    scope = config["scopes"][config["checks"][name]["scope"]]
    return [
        *ruff_command(config, operation),
        "--isolated",
        "--target-version",
        config["arguments"]["ruff_target"],
        *(["--check"] if operation == "format" else []),
        *scope,
    ]


def ruff_command(config, *arguments):
    """Use one bootstrap definition for tool probes and real Ruff executions."""
    return ["uvx", "--no-config", f"ruff=={config['toolchain']['ruff']}", *arguments]


def _lint(config, coverage, junit):
    return _ruff(config, "ruff-lint", "check")


def _format(config, coverage, junit):
    return _ruff(config, "ruff-format", "format")


def _pytest(config, coverage, junit):
    args = [
        "uv",
        "run",
        "--locked",
        "--group",
        "dev",
        "--python",
        config["toolchain"]["python"],
        "pytest",
        *config["arguments"]["pytest"],
        f"--junitxml={junit}",
    ]
    if coverage:
        args += [
            "--cov=src/engineering_ci",
            "--cov=.dagger/src/engineering_ci",
            "--cov-report=xml:core-coverage.xml",
            "--cov-report=json:core-coverage.json",
        ]
    return args


def _version(config, coverage, junit):
    return [
        "uv",
        "run",
        "--locked",
        "--python",
        config["toolchain"]["python"],
        "python",
        "-m",
        "engineering_ci.cli",
        "version",
        "check",
    ]


def _lock(config, coverage, junit):
    return [
        "uv",
        "lock",
        "--project",
        ".",
        "--check",
        "--python",
        config["toolchain"]["python"],
    ]


OPERATIONS = MappingProxyType(
    {
        "ruff-lint": CheckOperation(_lint, frozenset({"uv", "uvx"})),
        "ruff-format": CheckOperation(_format, frozenset({"uv", "uvx"})),
        "pytest": CheckOperation(
            _pytest,
            frozenset({"uv", "git", "gh"}),
            frozenset({"bash"}),
            frozenset({"aria2"}),
            frozenset({"tests", "src", "scripts", ".dagger/src"}),
            requires_release_metadata=True,
        ),
        "version": CheckOperation(
            _version,
            frozenset({"uv"}),
            required_inputs=frozenset(
                {
                    "src/engineering_ci/release/version.py",
                }
            ),
            requires_release_metadata=True,
        ),
        "lock": CheckOperation(
            _lock,
            frozenset({"uv"}),
            required_inputs=frozenset(
                {"pyproject.toml", "uv.lock", ".python-version", "uv.toml"}
            ),
        ),
    }
)


def command(name, config, *, coverage=False, junit="core-junit.xml"):
    """Resolve a fixed operation; never import or execute a configured handler."""
    if name not in OPERATIONS:
        raise ValueError(f"Unsupported check: {name}")
    return OPERATIONS[name].command(config, coverage, junit)


def container_plan(config):
    """Prepare one environment for the full PR profile, including partial reuse."""
    tools, optional = set(), set()
    for name in config["profiles"]["pr"]:
        spec = config["checks"][name]
        tools.update(spec["required_tools"] + spec["linux_tools"])
        optional.update(spec["optional_backends"])
    packages = {"ca-certificates"}
    system_tools = {"git": "git", "gh": "gh", "bash": "bash"}
    packages.update(system_tools[name] for name in tools if name in system_tools)
    if "aria2" in optional:
        packages.add("aria2")
    return {
        "schema_version": 1,
        "toolchain": {name: config["toolchain"][name] for name in ("python", "uv")},
        "packages": sorted(packages),
    }
