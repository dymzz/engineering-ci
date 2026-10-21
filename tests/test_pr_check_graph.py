"""The evidence sink consumes branch facts and never launches another check."""

import copy
import json

import pytest

from engineering_ci import checks, checks_container


def test_environment_probe_uses_provider_imports_and_consumer_inputs(
    tmp_path, monkeypatch
):
    provider = tmp_path / "provider"
    consumer = tmp_path / "consumer"
    monkeypatch.setattr(checks, "ROOT", provider)
    seen = []
    monkeypatch.setattr(
        checks, "execution_environment", lambda root, env: seen.append(root) or {}
    )
    monkeypatch.setattr(checks, "environment_identity", lambda env: {})
    monkeypatch.setattr(
        checks, "preflight", lambda name, root, *args: {"target": str(root)}
    )
    monkeypatch.setattr(
        checks, "snapshot", lambda root, *args, **kwargs: ({"target": str(root)}, None)
    )
    result = checks_container.prepare(consumer, ["pytest"], {})
    assert seen == [provider]
    assert result["source_inputs"]["pytest"] == {"target": str(consumer)}
    assert result["tools"]["pytest"] == {"target": str(consumer)}


@pytest.fixture
def branches(tmp_path, monkeypatch):
    names = ["ruff-lint", "pytest", "version"]
    parameters = {"coverage": True, "junit": "core-junit.xml"}
    request = {"selectors": names, **parameters}
    environment = {
        "schema_version": 1,
        "runtime": {"engine": "pinned"},
        "environment": {"platform": "Linux"},
        "tools": {name: {"uv": "locked"} for name in names},
        "source_inputs": {name: {"sha256": "same"} for name in names},
    }
    monkeypatch.setattr(
        checks_container, "prepare", lambda *args: copy.deepcopy(environment)
    )
    monkeypatch.setattr(
        checks, "execute", lambda *a, **k: pytest.fail("aggregation reran checks")
    )
    source = tmp_path / "branches"
    out = tmp_path / "out"
    out.mkdir()
    for name in names:
        path = source / name
        path.mkdir(parents=True)
        entry = {
            "name": name,
            "status": "passed",
            "exit_code": 0,
            "executed": True,
            "input_stable": True,
            "passed_for_current_input": True,
            "identity": {
                "name": name,
                "parameters": parameters,
                "tools": environment["tools"][name],
                "environment": environment["environment"],
            },
        }
        checks.atomic_json(
            path / "container-report.json",
            {
                "schema_version": 1,
                "selectors": [name],
                "profile": name,
                "checks": [entry],
                "overall_status": "passed",
                "exit_code": 0,
            },
        )
        checks.atomic_json(path / "environment.json", environment)
        (path / "exit-code.txt").write_text("0")
        (path / "stdout.log").write_text(name + "\n")
        (path / "stderr.log").write_text("")
    for filename in ("core-junit.xml", "core-coverage.xml", "core-coverage.json"):
        (source / "pytest" / filename).write_text("original pytest evidence")
    return tmp_path, names, request, environment, source, out


def run(bundle):
    root, names, request, env, source, out = bundle
    return checks_container.aggregate(root, names, request, env["runtime"], source, out)


def mutate(bundle, filename, change):
    path = bundle[-2] / "pytest" / filename
    data = json.loads(path.read_text())
    change(data)
    checks.atomic_json(path, data)


def test_deterministic_sink_preserves_single_pytest_and_coverage(branches):
    assert run(branches) == 0
    out = branches[-1]
    report = checks.read_report(out / "container-report.json")
    assert [item["name"] for item in report["checks"]] == branches[1]
    assert (out / "stdout.log").read_text() == "ruff-lint\npytest\nversion\n"
    assert (out / "core-coverage.json").read_text() == "original pytest evidence"


@pytest.mark.parametrize("exit_code", [1, 143])
def test_failure_is_not_hidden_by_artifact_delivery(branches, exit_code):
    def fail(report):
        report.update(overall_status="failed", exit_code=exit_code)
        report["checks"][0].update(
            status="failed",
            exit_code=-15 if exit_code == 143 else 1,
            passed_for_current_input=False,
        )

    mutate(branches, "container-report.json", fail)
    (branches[-2] / "pytest/exit-code.txt").write_text(str(exit_code))
    assert run(branches) == (1 if exit_code == 143 else exit_code)
    report = checks.read_report(branches[-1] / "container-report.json")
    assert report["exit_code"] == exit_code
    assert report["checks"][1]["exit_code"] == (-15 if exit_code == 143 else 1)
    assert (branches[-1] / "core-junit.xml").is_file()


def test_changed_branch_inputs_are_stale(branches):
    mutate(
        branches,
        "environment.json",
        lambda data: data["source_inputs"]["pytest"].update(sha256="changed"),
    )
    assert run(branches) == 3
    report = checks.read_report(branches[-1] / "container-report.json")
    assert report["checks"][1]["status"] == "stale"


def test_stale_failure_keeps_original_nonzero_exit(branches):
    def fail(report):
        report.update(overall_status="failed", exit_code=1)
        report["checks"][0].update(
            status="failed", exit_code=1, passed_for_current_input=False
        )

    mutate(branches, "container-report.json", fail)
    (branches[-2] / "pytest/exit-code.txt").write_text("1")
    mutate(
        branches,
        "environment.json",
        lambda data: data["source_inputs"]["pytest"].update(sha256="changed"),
    )
    assert run(branches) == 1


@pytest.mark.parametrize(
    "kind", ["missing", "exit", "identity", "parameters", "false_pass"]
)
def test_missing_or_inconsistent_facts_cannot_pass(branches, kind):
    source = branches[-2] / "pytest"
    if kind == "missing":
        (source / "container-report.json").unlink()
    elif kind == "exit":
        (source / "exit-code.txt").write_text("1")
    else:

        def corrupt(report):
            entry = report["checks"][0]
            if kind == "identity":
                entry["identity"]["name"] = "lock"
            elif kind == "parameters":
                entry["identity"]["parameters"] = {
                    "coverage": False,
                    "junit": "other.xml",
                }
            else:
                entry["executed"] = False

        mutate(branches, "container-report.json", corrupt)
    with pytest.raises((ValueError, OSError)):
        run(branches)


def test_selection_deduplicates_aliases_without_execution(capsys):
    request = {
        "selectors": ["tests", "pytest", "ruff"],
        "coverage": False,
        "junit": "core-junit.xml",
    }
    assert checks_container.main(["select", "--request", json.dumps(request)]) == 0
    assert json.loads(capsys.readouterr().out) == ["pytest", "ruff-lint", "ruff-format"]


def test_branch_precondition_error_retains_classification(branches):
    checks.atomic_json(
        branches[-2] / "pytest/environment.json",
        {"error": {"status": "missing_tool", "reason": "Required tool unavailable"}},
    )
    with pytest.raises(
        checks.PreconditionError, match="Required tool unavailable"
    ) as error:
        run(branches)
    assert error.value.status == "missing_tool"
