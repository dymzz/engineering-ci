"""The release checks, tool scopes and native Artifact contract."""

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

# The checks whose original execution the release evidence has to name.
#
# Each entry pairs the Job that runs the operation with the Step that performs
# it, so one required operation is one piece of evidence. A Job that succeeds
# only proves its own Steps succeeded; a required operation that has no Step
# of its own has no evidence at all.
CHECKS = {
    "candidate": ("candidate", "Validate manual release request"),
    "ruff_lint": (
        "core-tests / python-ci / checks",
        "Ruff Lint",
    ),
    "ruff_format": (
        "core-tests / python-ci / checks",
        "Ruff Format Check",
    ),
    "core_tests": (
        "core-tests / sync-tool-tests",
        "Run all core unit and process tests",
    ),
    "version_integrity": (
        "core-tests / version-metadata",
        "Validate release version metadata",
    ),
    "lock_integrity": (
        "core-tests / version-metadata",
        "Verify uv lock consistency",
    ),
    "workflow_analysis": (
        "core-tests / workflow-analysis",
        "Analyse GitHub Actions Workflows",
    ),
    "powershell_analysis": (
        "core-tests / powershell-analysis",
        "Analyse PowerShell scripts",
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

# The Dagger Checks the release has to execute in the validation environment.
#
# This is the single declaration of that set: the release Workflow selects
# exactly these names, and the evidence has to show that each of them ran.
# A check that is required here but absent from the executed list makes the
# release fail instead of quietly narrowing the verified scope.
REQUIRED_DAGGER_CHECKS = (
    "fixture-test",
    "fixture-failure-report",
    "fixture-quality-gates",
    "fixture-python-matrix",
)

# The one required Check a single report execution replaces. Its own result
# comes from the exported bundle, not from a second Check execution.
REPLACED_DAGGER_CHECK = "fixture-test"

# The Dagger module the release selects the required Checks in.
DAGGER_MODULE = "validation"


def required_dagger_checks() -> set[str]:
    """The required Dagger Checks, named as the executed Check list names them.

    The replaced Check is absent on purpose: a single report execution takes
    its place, so its result is proven by the exported bundle instead.
    """
    return {
        f"{DAGGER_MODULE}:{name}"
        for name in REQUIRED_DAGGER_CHECKS
        if name != REPLACED_DAGGER_CHECK
    }


# Required Dagger Checks whose evidence is the executed Check list rather than
# a Step of their own.
DAGGER_POLICY_CHECKS = {
    "failure_report": "validation:fixture-failure-report",
    "coverage": "validation:fixture-quality-gates",
    "python_matrix": "validation:fixture-python-matrix",
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
