"""The required check contract, its evidence and the Workflows that run it.

These tests are the propagation guard: a required operation that the release
evidence does not name, or that the Workflows do not actually run, fails here
instead of quietly narrowing the verified scope.
"""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
from engineering_ci.release import manifest as evidence
from engineering_ci.release import registry

WORKFLOWS = ROOT / ".github/workflows"


def parse_jobs(text):
    """Read the Job definitions of one Workflow without a YAML dependency.

    Only what the evidence contract names is read: the Job id, its display
    name, the Step names it runs, the command each Step runs and the reusable
    Workflow it calls.
    """
    jobs = {}
    in_jobs = False
    in_steps = False
    current = None
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        raw = lines[index]
        index += 1
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent == 0:
            in_jobs = stripped == "jobs:"
            in_steps = False
            current = None
            continue
        if not in_jobs:
            continue
        if indent == 2 and stripped.endswith(":") and not stripped.startswith("-"):
            current = stripped[:-1]
            jobs[current] = {
                "display": current,
                "steps": [],
                "runs": {},
                "uses": "",
            }
            in_steps = False
            continue
        if current is None:
            continue
        if indent == 4:
            in_steps = False
            if stripped.startswith("name:"):
                jobs[current]["display"] = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("uses:"):
                jobs[current]["uses"] = stripped.split(":", 1)[1].strip()
            elif stripped == "steps:":
                in_steps = True
            continue
        if indent == 6 and in_steps and stripped.startswith("- name:"):
            jobs[current]["steps"].append(stripped.split(":", 1)[1].strip())
            continue
        if indent == 8 and in_steps and stripped.startswith("run:"):
            command = stripped.split(":", 1)[1].strip()
            if command in {"|", ">", "|-", ">-", "|+", ">+"}:
                body = []
                while index < len(lines):
                    following = lines[index]
                    if not following.strip():
                        body.append("")
                        index += 1
                        continue
                    if len(following) - len(following.lstrip(" ")) <= indent:
                        break
                    body.append(following.strip())
                    index += 1
                command = "\n".join(body)
            if command:
                jobs[current]["runs"][jobs[current]["steps"][-1]] = command
    return jobs


def workflow_jobs(name):
    return parse_jobs((WORKFLOWS / name).read_text(encoding="utf-8"))


def release_jobs():
    """Every Job of the release Run, named the way the Actions API names it.

    A Job that calls a reusable Workflow appears in the caller Run as
    `<caller Job name> / <called Job name>`, and its Steps are the called
    Workflow's Steps.
    """
    result = {}
    for job in workflow_jobs("release.yml").values():
        if job["uses"]:
            callee = parse_jobs((ROOT / job["uses"]).read_text(encoding="utf-8"))
            for callee_job in callee.values():
                result[f"{job['display']} / {callee_job['display']}"] = callee_job
        else:
            result[job["display"]] = job
    return result


def test_every_required_check_names_a_real_job_and_step():
    jobs = release_jobs()
    for key, (job, step) in evidence.CHECKS.items():
        assert job in jobs, f"{key} names the unknown Job {job}"
        assert step in jobs[job]["steps"], (
            f"{key} names the unknown Step {step!r} of {job}"
        )


def test_every_dagger_check_step_in_the_core_workflow_is_named_by_the_contract():
    named = {
        step
        for job, step in evidence.CHECKS.values()
        if job.startswith("core-tests / ")
    }
    for job in workflow_jobs("ci.yml").values():
        for step, command in job["runs"].items():
            if "--check=" not in command:
                continue
            assert step in named, (
                f"the core Workflow runs {step!r} but the release evidence "
                "contract does not name it"
            )


def test_the_core_workflow_runs_every_required_check_it_names():
    ran = set()
    for job in workflow_jobs("ci.yml").values():
        for command in job["runs"].values():
            for part in command.split():
                if part.startswith("--check="):
                    ran.add(part.split("=", 1)[1])
    assert ran == {
        "lint",
        "format-check",
        "tests",
        "version",
        "lock",
        "workflow-analysis",
    }


def test_the_required_dagger_check_set_is_complete_and_declared_once():
    assert set(registry.required_dagger_checks()) == set(
        registry.DAGGER_POLICY_CHECKS.values()
    )
    assert registry.REPLACED_DAGGER_CHECK in registry.REQUIRED_DAGGER_CHECKS
    assert (
        f"{registry.DAGGER_MODULE}:{registry.REPLACED_DAGGER_CHECK}"
        not in registry.required_dagger_checks()
    )


def test_the_release_workflow_selects_the_declared_dagger_checks():
    text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    assert "dagger_checks: ${{ needs.candidate.outputs.dagger_checks }}" in text
    assert f"dagger_module: {registry.DAGGER_MODULE}" in text


def test_the_workflow_analysis_check_is_not_excluded_from_the_default_set():
    text = (ROOT / "dagger.toml").read_text(encoding="utf-8")
    skipped = [
        item.strip().strip('"')
        for item in text.split("skip = [", 1)[1].split("]", 1)[0].split(",")
    ]
    assert "workflow-analysis" not in skipped


def test_facts_reject_a_missing_required_dagger_check(tmp_path):
    directory = _facts_directory(tmp_path)
    (directory / "pytest/remaining-checks.txt").write_text(
        "validation:fixture-quality-gates\n", encoding="utf-8"
    )
    with (
        _evidence_present(),
        pytest.raises(ValueError, match="Required Dagger Checks did not run"),
    ):
        evidence.facts(directory)


def test_facts_accept_the_complete_required_dagger_check_set(tmp_path):
    directory = _facts_directory(tmp_path)
    (directory / "pytest/remaining-checks.txt").write_text(
        "\n".join(sorted(registry.required_dagger_checks())) + "\n",
        encoding="utf-8",
    )
    with _evidence_present():
        value = evidence.facts(directory)
    assert set(value["dagger_checks"]) == registry.required_dagger_checks()


def _evidence_present():
    """The Dagger Check evidence is what this test is about, not the bundle."""
    from unittest.mock import patch

    from engineering_ci.release import publishing

    return patch.object(publishing, "require_evidence", lambda _directory: None)


def _facts_directory(tmp_path):
    directory = tmp_path / "evidence"
    (directory / "core").mkdir(parents=True)
    (directory / "ruff").mkdir()
    (directory / "pytest").mkdir()
    (directory / "security").mkdir()
    (directory / "core/tool-versions.json").write_text(
        json.dumps(
            [
                {"name": "python", "version": "3.14.0", "scope": "core"},
                {"name": "uv", "version": "0.12.23", "scope": "core"},
                {"name": "pytest", "version": "9.0.3", "scope": "core"},
                {"name": "pytest-cov", "version": "7.0.0", "scope": "core"},
                {"name": "uv", "version": "0.12.23", "scope": "ruff"},
                {"name": "ruff", "version": "0.16.10", "scope": "ruff"},
            ]
        ),
        encoding="utf-8",
    )
    (directory / "ruff/tool-versions.json").write_text("[]", encoding="utf-8")
    (directory / "pytest/dagger-version.txt").write_text(
        "version: v1.0.0-beta.16\n", encoding="utf-8"
    )
    (directory / "security/report.json").write_text(
        json.dumps({"scanner": {"version": "2.6.0"}}), encoding="utf-8"
    )
    return directory
