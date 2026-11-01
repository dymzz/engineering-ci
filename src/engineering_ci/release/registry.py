"""The release checks, tool scopes and native Artifact contract.

The required-check set itself lives in `engineering_ci.requirements`, so the
Release contract, the CI Workflow and the drift tests share one definition.
This module only derives the release-specific views of it.
"""

from ..requirements import REQUIREMENTS

STATES = {
    "success": "passed",
    "failure": "failed",
    "skipped": "skipped",
    "cancelled": "cancelled",
    "timed_out": "timed_out",
    "neutral": "unavailable",
    "action_required": "unavailable",
    "stale": "unavailable",
    "startup_failure": "failed",
}

# Minimum direct inputs used by release guards, not a second Workflow DAG.
RELEASE_DEPENDENCIES = {
    "verified": frozenset({"candidate", "core-tests", "complete-ci", "evidence"}),
    "publish": frozenset({"candidate", "verified"}),
}

# The required checks that one Workflow step executes, as the job display
# name and step name the Release Run reports.
CHECKS = {
    requirement.key: (requirement.release_job, requirement.step)
    for requirement in REQUIREMENTS
    if not requirement.dagger_check
}

# The required checks that one native Dagger check executes. They share the
# Dagger step, so their identity is the module-qualified check name.
DAGGER_POLICY_CHECKS = {
    requirement.key: requirement.dagger_check
    for requirement in REQUIREMENTS
    if requirement.dagger_check
}

# Every native Dagger check the release Run must have executed. One report
# execution replaces exactly one of them; the rest still have to run.
DAGGER_EXECUTION_CHECKS = (
    "validation:fixture-test",
    "validation:fixture-failure-report",
    "validation:fixture-quality-gates",
    "validation:fixture-python-matrix",
)

TOOLS = {"python", "uv", "pytest", "pytest-cov", "ruff", "dagger", "osv-scanner"}

SCOPES = {"core", "ruff", "dagger_cli", "root_lock"}

REQUIRED_TOOLS = {
    ("core", "python"),
    ("core", "uv"),
    ("core", "pytest"),
    ("core", "pytest-cov"),
    ("ruff", "uv"),
    ("ruff", "ruff"),
    ("dagger_cli", "dagger"),
    ("root_lock", "osv-scanner"),
}

ARTIFACT_OUTPUTS = {
    "pytest-report": ("complete-ci", "pytest_artifact_id"),
    "dependency-security": ("complete-ci", "security_artifact_id"),
    "core-tests": ("core-tests", "core_artifact_id"),
    "release-tools-ruff": ("core-tests", "ruff_artifact_id"),
    "powershell-analysis": ("core-tests", "powershell_artifact_id"),
}

ARTIFACT_NAMES = set(ARTIFACT_OUTPUTS)
