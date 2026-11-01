"""The required engineering checks: what must run, where, and over which inputs.

One table defines every required check. The Release evidence contract, the CI
Workflow and the drift tests all read this table, so adding, removing or
rescoping a check is one edit plus the machine-checked binding to the
Workflow YAML — not four hand-maintained copies.

Three facts are recorded per requirement, and each answers one question the
Release evidence contract has to be able to answer later:

- `job` and `step` name the GitHub Actions step that executes the check.
  A reusable workflow's jobs appear in the caller's Run under the caller's
  job name, so `caller` records which release job owns the call.
- `scope` names the input set the check establishes. A requirement that
  covers different inputs is a different requirement.
- `dagger_check` names the module-qualified native Dagger check, when the
  requirement is one Dagger check rather than one Workflow step.

The recorded identity is what lets a later Run distinguish "this result is
still valid" from "this result was produced against requirements that no
longer exist". A historical result cannot be promoted to a present-tense
guarantee by being re-read.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Requirement:
    """One required check and the input set it establishes.

    `job` is the job display name inside the workflow that owns it. `caller`
    is the release job that calls that workflow; a reusable workflow's jobs
    are reported by the Jobs API as `<caller> / <job>`. `scope` names the
    covered input set, and `dagger_check` names the native Dagger check when
    the requirement is one rather than a single Workflow step.
    """

    key: str
    job: str
    step: str
    scope: str
    caller: str = ""
    dagger_check: str = ""

    @property
    def release_job(self) -> str:
        """The job display name the Release Run reports for this check."""
        return f"{self.caller} / {self.job}" if self.caller else self.job


# The complete required-check set. Order is the canonical order used by the
# Release Manifest and by the requirement digest.
REQUIREMENTS = (
    Requirement(
        key="candidate",
        job="candidate",
        step="Validate manual release request",
        scope="release-request",
    ),
    Requirement(
        key="ruff_lint",
        job="python-ci / checks",
        step="Ruff Lint",
        scope="repository:python",
        caller="core-tests",
    ),
    Requirement(
        key="ruff_format",
        job="python-ci / checks",
        step="Ruff Format Check",
        scope="repository:python",
        caller="core-tests",
    ),
    Requirement(
        key="workflow_analysis",
        job="python-ci / checks",
        step="Workflow analysis",
        scope="workflow:.github/workflows",
        caller="core-tests",
    ),
    Requirement(
        key="core_tests",
        job="sync-tool-tests",
        step="Run all core unit and process tests",
        scope="repository:tests",
        caller="core-tests",
    ),
    Requirement(
        key="version_integrity",
        job="version-metadata",
        step="Validate release metadata and root lock consistency",
        scope="repository:metadata",
        caller="core-tests",
    ),
    Requirement(
        key="powershell_analysis",
        job="powershell-analysis",
        step="Analyse PowerShell scripts",
        scope="powershell:.dagger/src/engineering_ci,config",
        caller="core-tests",
    ),
    Requirement(
        key="dependency_security",
        job="dependency-security",
        step="Check lock integrity and scan known vulnerabilities",
        scope="repository:lock",
        caller="complete-ci",
    ),
    Requirement(
        key="dagger",
        job="checks",
        step="Run Dagger Checks",
        scope="dagger:validation",
        caller="complete-ci",
    ),
    Requirement(
        key="tests",
        job="checks",
        step="Gate on pytest execution and explicit quality policy",
        scope="release-evidence:pytest",
        caller="complete-ci",
    ),
    Requirement(
        key="module_lock",
        job="checks",
        step="Verify generated module lock consistency",
        scope="dagger:module-lock",
        caller="complete-ci",
    ),
    Requirement(
        key="coverage",
        job="checks",
        step="Run Dagger Checks",
        scope="dagger:validation:fixture-quality-gates",
        caller="complete-ci",
        dagger_check="validation:fixture-quality-gates",
    ),
    Requirement(
        key="python_matrix",
        job="checks",
        step="Run Dagger Checks",
        scope="dagger:validation:fixture-python-matrix",
        caller="complete-ci",
        dagger_check="validation:fixture-python-matrix",
    ),
    Requirement(
        key="evidence",
        job="evidence",
        step="Require usable evidence and original execution gates",
        scope="release-evidence",
    ),
)

BY_KEY = {requirement.key: requirement for requirement in REQUIREMENTS}

# Repository paths whose PowerShell files are not the repository's own
# scripts. The fixture scripts deliberately contain rule violations and are
# analysed by the fixture tests, which expect them to; a virtual environment
# and the local scratch directory are generated. Everything else must be
# inside a required scope, so a script added outside the declared scope
# cannot go unnoticed.
POWERSHELL_EXCLUSIONS = ("tests/fixtures", ".venv", ".test-tools")


def records(requirements=None) -> list[dict]:
    """The canonical, comparable form of a requirement set.

    Only recorded facts appear, so two requirement sets can be compared
    without consulting the code that defines them.
    """
    return [
        {
            "key": requirement.key,
            "job": requirement.release_job,
            "step": requirement.step,
            "scope": requirement.scope,
            "dagger_check": requirement.dagger_check,
        }
        for requirement in (REQUIREMENTS if requirements is None else requirements)
    ]


def digest(requirements=None) -> str:
    """A stable identity for one requirement set."""
    return digest_records(records(requirements))


def digest_records(items: list[dict]) -> str:
    """A stable identity for an already recorded requirement set.

    Used to check a recorded set against its own digest, so a manifest
    cannot claim a requirement set other than the one it carries.
    """
    body = json.dumps(items, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def delta(recorded: list[dict], current: list[dict]) -> dict:
    """Name exactly which requirements were added, removed or rescoped.

    An empty result means the two sets are identical, so a result recorded
    against `recorded` still covers the requirements in `current`.
    """
    old = {item["key"]: item for item in recorded}
    new = {item["key"]: item for item in current}
    return {
        "added": sorted(new.keys() - old.keys()),
        "removed": sorted(old.keys() - new.keys()),
        "changed": sorted(
            key for key in old.keys() & new.keys() if old[key] != new[key]
        ),
    }


def describe(delta_result: dict) -> str:
    """One sentence naming the difference between two requirement sets."""
    parts = []
    if delta_result["added"]:
        parts.append("added " + ", ".join(delta_result["added"]))
    if delta_result["removed"]:
        parts.append("removed " + ", ".join(delta_result["removed"]))
    if delta_result["changed"]:
        parts.append("rescoped " + ", ".join(delta_result["changed"]))
    return "; ".join(parts) if parts else "no difference"
