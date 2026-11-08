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

DAGGER_MODULE = "validation"
REPLACED_DAGGER_CHECK = "fixture-test"
PERMITTED_REPLACED_CHECK = f"{DAGGER_MODULE}:{REPLACED_DAGGER_CHECK}"

# The authentic historical 4-artifact set prior to powershell-analysis.
LEGACY_ARTIFACT_NAMES = frozenset(
    {
        "pytest-report",
        "dependency-security",
        "core-tests",
        "release-tools-ruff",
    }
)

# The authentic historical 12-check set prior to ECI-CHECK-01.
LEGACY_CHECKS = {
    "candidate": ("candidate", "Validate manual release request"),
    "ruff_lint": ("core-tests / python-ci / checks", "Ruff Lint"),
    "ruff_format": ("core-tests / python-ci / checks", "Ruff Format Check"),
    "core_tests": (
        "core-tests / sync-tool-tests",
        "Run all core unit and process tests",
    ),
    "version_integrity": (
        "core-tests / version-metadata",
        "Validate release metadata and root lock consistency",
    ),
    "dependency_security": (
        "complete-ci / dependency-security",
        "Check lock integrity and scan known vulnerabilities",
    ),
    "dagger": ("complete-ci / checks", "Run Dagger Checks"),
    "tests": (
        "complete-ci / checks",
        "Gate on pytest execution and explicit quality policy",
    ),
    "module_lock": ("complete-ci / checks", "Verify generated module lock consistency"),
    "evidence": ("evidence", "Require usable evidence and original execution gates"),
}

LEGACY_DAGGER_POLICY_CHECKS = {
    "coverage": "validation:fixture-quality-gates",
    "python_matrix": "validation:fixture-python-matrix",
}

LEGACY_REQUIRED_CHECKS = frozenset(LEGACY_CHECKS) | frozenset(
    LEGACY_DAGGER_POLICY_CHECKS
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

HISTORICAL_ARTIFACT_NAMES = frozenset(
    {"pytest-report", "dependency-security", "core-tests", "release-tools-ruff"}
)
EARLIEST_ARTIFACT_NAMES = frozenset({"pytest-report", "dependency-security"})
