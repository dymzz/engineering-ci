"""The release checks, tool scopes and native Artifact contract."""

from ..checks import CI_STEPS

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

CHECKS = {
    "candidate": ("candidate", "Validate manual release request"),
    "ruff_lint": ("core-tests / python-ci / checks", CI_STEPS["ruff-lint"]),
    "ruff_format": ("core-tests / python-ci / checks", CI_STEPS["ruff-format"]),
    "core_tests": (
        "core-tests / sync-tool-tests",
        CI_STEPS["pytest"],
    ),
    "version_integrity": (
        "core-tests / version-metadata",
        CI_STEPS["version"],
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

DAGGER_POLICY_CHECKS = {
    "coverage": "engineering-ci:fixture-quality-gates",
    "python_matrix": "engineering-ci:fixture-python-matrix",
}

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
}

ARTIFACT_NAMES = set(ARTIFACT_OUTPUTS)
