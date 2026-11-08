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

# Authentic historical release snapshots mapped by their trusted release tag and commit.
# Only actually published versions are admitted; unknown sources or tampered identities are rejected.
HISTORICAL_RELEASE_CONTRACTS = {
    "v0.7.0": {
        "repository": "dymzz/engineering-ci",
        "commit_sha": "9d03ade4f2af89538c9a0362beae55c9a0835e18",
        "run_id": 39614205390,
        "run_attempt": 1,
        "artifacts": LEGACY_ARTIFACT_NAMES,
        "checks": LEGACY_CHECKS,
        "dagger_policy_checks": {
            "coverage": "validation:fixture-quality-gates",
            "python_matrix": "validation:fixture-python-matrix",
        },
    },
    "v0.6.8": {
        "repository": "dymzz/engineering-ci",
        "commit_sha": "eb0e457de2c172006b62d3962b0a7e932a5adb32",
        "run_id": 39449394205,
        "run_attempt": 1,
        "artifacts": LEGACY_ARTIFACT_NAMES,
        "checks": LEGACY_CHECKS,
        "dagger_policy_checks": {
            "coverage": "engineering-ci:fixture-quality-gates",
            "python_matrix": "engineering-ci:fixture-python-matrix",
        },
    },
    "v0.6.7": {
        "repository": "dymzz/engineering-ci",
        "commit_sha": "0f74b2f776796b9db83f7a61c0849e504afdbea0",
        "run_id": 38849770235,
        "run_attempt": 1,
        "artifacts": LEGACY_ARTIFACT_NAMES,
        "checks": LEGACY_CHECKS,
        "dagger_policy_checks": {
            "coverage": "engineering-ci:fixture-quality-gates",
            "python_matrix": "engineering-ci:fixture-python-matrix",
        },
    },
    "v0.6.5": {
        "repository": "dymzz/engineering-ci",
        "commit_sha": "804d791c60d34468166b6910c88fbc8149fdbf26",
        "run_id": 38449979357,
        "run_attempt": 1,
        "artifacts": LEGACY_ARTIFACT_NAMES,
        "checks": LEGACY_CHECKS,
        "dagger_policy_checks": {
            "coverage": "engineering-ci:fixture-quality-gates",
            "python_matrix": "engineering-ci:fixture-python-matrix",
        },
    },
    "v0.6.2": {
        "repository": "dymzz/engineering-ci",
        "commit_sha": "4ba1a64be78c0e7f4befa4407336500a96af22fb",
        "run_id": 38387146133,
        "run_attempt": 1,
        "artifacts": LEGACY_ARTIFACT_NAMES,
        "checks": LEGACY_CHECKS,
        "dagger_policy_checks": {
            "coverage": "engineering-ci:fixture-quality-gates",
            "python_matrix": "engineering-ci:fixture-python-matrix",
        },
    },
    "v0.6.0": {
        "repository": "dymzz/engineering-ci",
        "commit_sha": "e4fc1cdd98140e380e5dbec401290bc87de79f15",
        "run_id": 38333998282,
        "run_attempt": 1,
        "artifacts": LEGACY_ARTIFACT_NAMES,
        "checks": LEGACY_CHECKS,
        "dagger_policy_checks": {
            "coverage": "engineering-ci:fixture-quality-gates",
            "python_matrix": "engineering-ci:fixture-python-matrix",
        },
    },
    "v0.5.4": {
        "repository": "dymzz/engineering-ci",
        "commit_sha": "f1d7d31014417468fa7299be27a86f0a57b47521",
        "run_id": 38205608105,
        "run_attempt": 1,
        "artifacts": LEGACY_ARTIFACT_NAMES,
        "checks": LEGACY_CHECKS,
        "dagger_policy_checks": {
            "coverage": "engineering-ci:fixture-quality-gates",
            "python_matrix": "engineering-ci:fixture-python-matrix",
        },
    },
    "v0.5.3": {
        "repository": "dymzz/engineering-ci",
        "commit_sha": "9c2c875af4c42e7e8dd3d024136a5dbcaa12e618",
        "run_id": 38195712769,
        "run_attempt": 1,
        "artifacts": LEGACY_ARTIFACT_NAMES,
        "checks": LEGACY_CHECKS,
        "dagger_policy_checks": {
            "coverage": "engineering-ci:fixture-quality-gates",
            "python_matrix": "engineering-ci:fixture-python-matrix",
        },
    },
    "v0.5.1": {
        "repository": "dymzz/engineering-ci",
        "commit_sha": "9314eb37fae9b0794bb61691d7f122b4520c882d",
        "run_id": 38161247965,
        "run_attempt": 1,
        "artifacts": LEGACY_ARTIFACT_NAMES,
        "checks": LEGACY_CHECKS,
        "dagger_policy_checks": {
            "coverage": "engineering-ci:fixture-quality-gates",
            "python_matrix": "engineering-ci:fixture-python-matrix",
        },
    },
    "v0.4.12": {
        "repository": "dymzz/engineering-ci",
        "commit_sha": "2d33464bf91651eb37ceefc77d1d04127c508cf7",
        "run_id": 38134368486,
        "run_attempt": 1,
        "artifacts": LEGACY_ARTIFACT_NAMES,
        "checks": LEGACY_CHECKS,
        "dagger_policy_checks": {
            "coverage": "engineering-ci:fixture-quality-gates",
            "python_matrix": "engineering-ci:fixture-python-matrix",
        },
    },
    "v0.4.9": {
        "repository": "dymzz/engineering-ci",
        "commit_sha": "77dbb155aee08d53808066b3264b8040cef7507c",
        "run_id": 38054204518,
        "run_attempt": 1,
        "artifacts": LEGACY_ARTIFACT_NAMES,
        "checks": LEGACY_CHECKS,
        "dagger_policy_checks": {
            "coverage": "engineering-ci:fixture-quality-gates",
            "python_matrix": "engineering-ci:fixture-python-matrix",
        },
    },
}


def resolve_historical_contract(
    tag: str | None,
    commit_sha: str | None,
    run_id: int | None = None,
    run_attempt: int | None = None,
    repository: str | None = None,
) -> dict | None:
    """Resolve the authentic historical requirement and evidence contract for a published release.

    Returns None when the release tag or provenance is unknown or mismatched.
    """
    if not tag or not commit_sha:
        return None
    contract = HISTORICAL_RELEASE_CONTRACTS.get(tag)
    if contract is None:
        return None
    if contract["commit_sha"] != commit_sha:
        return None
    if run_id is not None and contract["run_id"] != run_id:
        return None
    if run_attempt is not None and contract["run_attempt"] != run_attempt:
        return None
    if repository is not None and contract["repository"] != repository:
        return None
    return contract


LEGACY_DAGGER_POLICY_CHECKS = HISTORICAL_RELEASE_CONTRACTS["v0.7.0"][
    "dagger_policy_checks"
]

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
