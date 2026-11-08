"""The required-check contract and its binding to the CI Workflow.

A required check that is not wired into the Workflow is not required. These
tests make that mismatch a failing test rather than a silent gap, so adding,
renaming or rescoping a check cannot pass unnoticed.
"""

import json
import re
from pathlib import Path

import pytest

from engineering_ci import requirements
from engineering_ci.release import registry

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = {
    name: ROOT / ".github/workflows" / name
    for name in ("ci.yml", "release.yml", "reusable-python-ci.yml")
}
POWERSHELL_SUFFIXES = (".ps1", ".psm1")


def workflow_jobs(path):
    """Read the job ids, display names and step names from a Workflow file.

    The repository validates Workflow structure without a YAML dependency, so
    this reads the same small subset of the format by indentation.
    """
    jobs = []
    current = None
    in_jobs = False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            in_jobs = stripped == "jobs:"
            current = None
            continue
        if not in_jobs:
            continue
        if indent == 2 and stripped.endswith(":"):
            current = {"id": stripped[:-1], "name": None, "uses": None, "steps": []}
            jobs.append(current)
        elif current is not None and indent == 4:
            if stripped.startswith("name:"):
                current["name"] = stripped.split(":", 1)[1].strip().strip("'\"")
            elif stripped.startswith("uses:"):
                current["uses"] = stripped.split(":", 1)[1].strip().strip("'\"")
        elif current is not None and indent == 6 and stripped.startswith("- name:"):
            current["steps"].append(stripped.split(":", 1)[1].strip().strip("'\""))
    return jobs


def display_name(job):
    """The name the Jobs API reports for a Workflow job."""
    return job["name"] if job["name"] else job["id"]


def calling_workflow(caller):
    """The Workflow file the release job calls, or None."""
    for job in workflow_jobs(WORKFLOWS["release.yml"]):
        if job["id"] == caller:
            used = job["uses"] or ""
            return used.rsplit("/", 1)[-1]
    return None


def owning_workflow(requirement):
    """The Workflow file that owns the requirement's job."""
    if requirement.caller:
        return calling_workflow(requirement.caller)
    return "release.yml"


def step_run(path, step_name):
    """The run command of one named step, or None."""
    text = path.read_text(encoding="utf-8")
    for job in workflow_jobs(path):
        for name in job["steps"]:
            if name != step_name:
                continue
            body = text.split(f"- name: {name}", 1)[1]
            match = re.search(r"^\s+run:\s*(.*)$", body, re.MULTILINE)
            return match.group(1).strip() if match else None
    return None


def block_scalar(text, key):
    """The lines of one block scalar value in a Workflow file."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.strip().startswith(f"{key}:"):
            continue
        indent = len(line) - len(line.lstrip())
        body = []
        for candidate in lines[index + 1 :]:
            if not candidate.strip():
                body.append("")
                continue
            if len(candidate) - len(candidate.lstrip()) <= indent:
                break
            body.append(candidate.strip())
        return [item for item in body if item]
    return []


def scope_paths(scope):
    """The declared paths of a requirement scope."""
    kind, _, paths = scope.partition(":")
    return kind, [item for item in paths.split(",") if item]


def pattern_regex(pattern):
    """A regular expression for one Dagger exclude pattern."""
    parts = []
    for index, chunk in enumerate(pattern.split("/")):
        if index:
            parts.append("/")
        if chunk == "**":
            parts.append(".*")
        else:
            parts.append(
                "".join(
                    ".*" if char == "*" else "[^/]+" if char == "?" else re.escape(char)
                    for char in chunk
                )
            )
    return re.compile("^" + "".join(parts) + "$")


def declared_check_names():
    """The native Dagger check names the module declares.

    Dagger exposes a Python function name as a kebab-case check name.
    """
    text = (ROOT / ".dagger/src/engineering_ci/main.py").read_text(encoding="utf-8")
    return {
        name.replace("_", "-") for name in re.findall(r"@check\s+async def (\w+)", text)
    }


def source_excludes():
    """The shared exclusion list the check inputs are filtered with."""
    import importlib.util
    import sys
    import types

    stub = types.ModuleType("dagger")
    stub.dag = types.SimpleNamespace()
    stub.Directory = object
    stub.Container = object
    stub.cache_volume = lambda name: name
    original = sys.modules.get("dagger")
    sys.modules["dagger"] = stub
    try:
        spec = importlib.util.spec_from_file_location(
            "repository_unit", ROOT / ".dagger/src/engineering_ci/repository.py"
        )
        repository = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(repository)
        return list(repository.SOURCE_EXCLUDES)
    finally:
        if original is None:
            sys.modules.pop("dagger", None)
        else:
            sys.modules["dagger"] = original


def test_every_required_check_is_wired_into_its_workflow():
    for requirement in requirements.REQUIREMENTS:
        owner = owning_workflow(requirement)
        assert owner in WORKFLOWS, f"{requirement.key}: unknown owning workflow"
        jobs = [
            job
            for job in workflow_jobs(WORKFLOWS[owner])
            if display_name(job) == requirement.job
        ]
        assert len(jobs) == 1, (
            f"{requirement.key}: {owner} must declare exactly one job named "
            f"{requirement.job!r}, found {len(jobs)}"
        )
        assert requirement.step in jobs[0]["steps"], (
            f"{requirement.key}: {owner} job {requirement.job!r} has no step "
            f"{requirement.step!r}"
        )


def test_a_required_dagger_check_exists_and_is_selected():
    """A requirement that names a Dagger check needs that check to exist."""
    declared = declared_check_names()
    for requirement in requirements.REQUIREMENTS:
        owner = owning_workflow(requirement)
        command = step_run(WORKFLOWS[owner], requirement.step) or ""
        selected = re.findall(r"--check=([a-z0-9-]+)", command)
        if not selected:
            continue
        for name in selected:
            assert name in declared, (
                f"{requirement.key}: selects Dagger check {name!r}, which the "
                "module does not declare"
            )
            assert requirement.key.replace("_", "-") == name or requirement.key in (
                "ruff_lint",
                "ruff_format",
                "core_tests",
                "version_integrity",
            ), (
                f"{requirement.key}: step selects {name!r}; the requirement and "
                "the selected check must name the same check"
            )


def test_required_check_names_are_unique_and_ordered():
    keys = [requirement.key for requirement in requirements.REQUIREMENTS]
    assert len(keys) == len(set(keys))
    assert set(keys) == set(registry.CHECKS) | set(registry.DAGGER_POLICY_CHECKS)


def test_the_powershell_scope_matches_the_workflow_input():
    requirement = requirements.BY_KEY["powershell_analysis"]
    kind, paths = scope_paths(requirement.scope)
    assert kind == "powershell"
    text = WORKFLOWS["ci.yml"].read_text(encoding="utf-8")
    match = re.search(r"POWERSHELL_PATHS:\s*'?([^'\n]+)'?", text)
    assert match, "the Workflow must declare the analysed PowerShell paths"
    declared = [item for item in match.group(1).strip().split(",") if item]
    assert declared == paths


def test_the_workflow_scope_matches_actionlint_policy():
    """The workflow_analysis requirement scope must bind to actionlint's scan paths."""
    requirement = requirements.BY_KEY["workflow_analysis"]
    kind, paths = scope_paths(requirement.scope)
    assert kind == "workflow"
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "script_analysis", ROOT / ".dagger/src/engineering_ci/script_analysis.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    policy = module.actionlint_policy()
    assert policy["scan_paths"] == paths


def test_every_repository_powershell_script_is_covered_or_excluded():
    """A script outside the declared scope would never be analysed."""
    requirement = requirements.BY_KEY["powershell_analysis"]
    _, paths = scope_paths(requirement.scope)
    covered = [item for item in paths]
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in POWERSHELL_SUFFIXES:
            continue
        relative = path.relative_to(ROOT).as_posix()
        if any(
            relative.startswith(f"{excluded}/")
            for excluded in requirements.POWERSHELL_EXCLUSIONS
        ):
            continue
        assert any(
            relative == item or relative.startswith(f"{item}/") for item in covered
        ), f"{relative} is not covered by the required PowerShell scope"


def test_no_source_exclusion_shadows_a_required_scope():
    """An excluded path would silently shrink the checked input set."""
    patterns = [pattern_regex(item) for item in source_excludes()]
    for requirement in requirements.REQUIREMENTS:
        kind, paths = scope_paths(requirement.scope)
        if not paths:
            continue
        for item in paths:
            root = ROOT / item
            candidates = [root] if root.is_file() else sorted(root.rglob("*"))
            for candidate in candidates:
                if not candidate.is_file():
                    continue
                if (
                    kind == "powershell"
                    and candidate.suffix.lower() not in POWERSHELL_SUFFIXES
                ):
                    continue
                relative = candidate.relative_to(ROOT).as_posix()
                for pattern in patterns:
                    assert not pattern.match(relative), (
                        f"{relative} is excluded from the check input but is "
                        f"inside the {requirement.key!r} scope"
                    )


def test_requirement_digest_changes_with_the_requirement_set():
    current = requirements.records()
    assert requirements.digest() == requirements.digest_records(current)
    added = current + [
        {
            "key": "extra",
            "job": "j",
            "step": "s",
            "scope": "scope",
            "dagger_check": "",
        }
    ]
    assert requirements.digest_records(added) != requirements.digest_records(current)
    rescoped = [dict(item) for item in current]
    rescoped[0]["scope"] = "other"
    assert requirements.digest_records(rescoped) != requirements.digest_records(current)


def test_requirement_delta_names_the_difference():
    current = requirements.records()
    removed = [item for item in current if item["key"] != current[0]["key"]]
    difference = requirements.delta(removed, current)
    assert difference["added"] == [current[0]["key"]]
    assert difference["removed"] == []
    assert difference["changed"] == []
    assert current[0]["key"] in requirements.describe(difference)
    assert requirements.delta(current, current) == {
        "added": [],
        "removed": [],
        "changed": [],
    }


def test_the_required_dagger_execution_set_is_complete():
    """Every native Dagger check the release runs has to be required."""
    assert set(registry.DAGGER_EXECUTION_CHECKS) == {
        "validation:fixture-test",
        "validation:fixture-failure-report",
        "validation:fixture-quality-gates",
        "validation:fixture-python-matrix",
    }
    assert set(registry.DAGGER_POLICY_CHECKS.values()) <= set(
        registry.DAGGER_EXECUTION_CHECKS
    )


def test_the_release_workflow_selects_every_required_dagger_check():
    text = WORKFLOWS["release.yml"].read_text(encoding="utf-8")
    selected = {f"validation:{item}" for item in block_scalar(text, "dagger_checks")}
    assert selected, "the release Workflow must declare its Dagger checks"
    assert selected == set(registry.DAGGER_EXECUTION_CHECKS)


def input_defaults(text):
    """The declared default of every workflow input."""
    defaults = {}
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^ {6}(\w+):\s*$", line)
        if not match:
            continue
        for candidate in lines[index + 1 :]:
            if candidate.strip() and not candidate.startswith(" " * 8):
                break
            default = re.match(r"^ {8}default:\s*'?([^'\n]+?)'?\s*$", candidate)
            if default:
                defaults[match.group(1)] = default.group(1)
                break
    return defaults


def uploaded_artifact_names(path):
    """The Artifact names a Workflow uploads, resolving an input's default."""
    text = path.read_text(encoding="utf-8")
    defaults = input_defaults(text)
    names = set()
    for match in re.finditer(
        r"uses: actions/upload-artifact@\S+\n(?:.*\n)*?^ {10}name:\s*(.+)$",
        text,
        re.MULTILINE,
    ):
        value = match.group(1).strip().strip("'\"")
        resolved = re.fullmatch(r"\$\{\{\s*inputs\.(\w+)\s*\}\}", value)
        names.add(defaults.get(resolved.group(1), value) if resolved else value)
    return names


def test_the_execution_ledger_reaches_the_release_evidence():
    """The ledger the Release evidence reads has to be in the Artifact."""
    text = WORKFLOWS["reusable-python-ci.yml"].read_text(encoding="utf-8")
    for name in ("dagger-checks.txt", "remaining-checks.txt", "replaced-check.txt"):
        assert f"pytest-report/{name}" in text, f"{name} is never uploaded"
        assert f"$RUNNER_TEMP/{name}" in text, f"{name} is never produced"
    planner = (ROOT / ".dagger/src/engineering_ci/pytest_ci.py").read_text(
        encoding="utf-8"
    )
    assert "replaced-check.txt" in planner, "the plan step never records it"


def test_every_required_artifact_is_produced_and_collected():
    """An Artifact that is not produced, or not collected, is missing evidence."""
    produced = set()
    for path in WORKFLOWS.values():
        produced |= uploaded_artifact_names(path)
    for name in registry.ARTIFACT_NAMES:
        assert name in produced, f"no Workflow uploads the {name!r} Artifact"
    text = WORKFLOWS["release.yml"].read_text(encoding="utf-8")
    for job, output in registry.ARTIFACT_OUTPUTS.values():
        assert f"needs.{job}.outputs.{output}" in text, (
            f"the release Workflow never collects needs.{job}.outputs.{output}"
        )


def test_the_manifest_schema_requires_every_required_check():
    schema = json.loads(
        (ROOT / "docs/architecture/release/manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    required = set(schema["properties"]["checks"]["required"])
    assert required == set(registry.CHECKS) | set(registry.DAGGER_POLICY_CHECKS) | {
        "core_coverage_threshold",
        "consumer_failure_probe",
    }
    assert "requirements" in schema["required"]


@pytest.mark.parametrize(
    "requirement", list(requirements.REQUIREMENTS), ids=lambda item: item.key
)
def test_each_requirement_names_a_scope_and_a_step(requirement):
    assert requirement.key
    assert requirement.job
    assert requirement.step
    assert requirement.scope
    assert requirement.release_job == (
        f"{requirement.caller} / {requirement.job}"
        if requirement.caller
        else requirement.job
    )
