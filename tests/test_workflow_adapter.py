"""Actual check library contracts behind independently generated workflow steps."""

import copy
import json
from pathlib import Path

from test_checks import checkout as check_checkout
from test_checks import fake_process

from engineering_ci import checks
from engineering_ci.cli import main as cli
from engineering_ci.workflow_adapters.checks import catalog, recipe
from engineering_ci.workflow_core import Runtime, generate
from engineering_ci.workflow_core.documents import read

checkout = check_checkout


def native(checkout, process):
    def invoke(selectors, **kwargs):
        kwargs.pop("executor")
        return checks.execute(selectors, run=process, environ={}, **kwargs)

    return invoke


def setup(checkout, runner):
    tools = catalog(checkout, runner=runner)
    workflow = generate(
        recipe(["pr"], executor="native"), tools, checkout / ".test-tools/generated"
    )
    runtime = Runtime(tools)
    return runtime, workflow


def test_actual_five_check_calls_and_reuse_across_distinct_generations(checkout):
    calls = []
    runner = native(checkout, fake_process(action=lambda args, kw: calls.append(args)))
    runtime, workflow = setup(checkout, runner)
    first_dir = runtime.create(workflow, checkout / ".test-tools/runs")
    first = runtime.resume(first_dir)
    assert first["exit_code"] == 0 and len(calls) == 5
    next_workflow = generate(
        recipe(["pr"], executor="native"),
        runtime.catalog,
        checkout / ".test-tools/generated",
    )
    next_dir = runtime.create(
        next_workflow, checkout / ".test-tools/runs", reuse_from=first_dir
    )
    second = runtime.resume(next_dir)
    assert second["exit_code"] == 0 and len(calls) == 5
    assert all(item["result"]["reused"] for item in second["steps"].values())
    assert (
        first["generation_id"] != second["generation_id"]
        and first["definition_id"] == second["definition_id"]
    )
    assert read(first_dir / "state.json") == first
    resources = [x["result"]["outputs"]["report"] for x in second["steps"].values()]
    assert (
        len({x["path"] for x in resources}) == 1
    )  # One real check batch, five factual children.


def test_config_changes_actual_selection_order_and_parameters(checkout):
    calls = []
    tools = catalog(
        checkout,
        runner=native(
            checkout, fake_process(action=lambda args, kw: calls.append(args))
        ),
    )
    value = recipe(["lock", "version"], executor="native", coverage=True)
    value["steps"][1]["needs"] = ["lock"]
    workflow = generate(value, tools, checkout / ".test-tools/generated")
    runtime = Runtime(tools)
    result = runtime.resume(runtime.create(workflow, checkout / ".test-tools/runs"))
    assert result["exit_code"] == 0 and len(calls) == 2
    assert calls[0][1] == "lock" and "version" in calls[1]
    assert list(result["steps"]) == ["lock", "version"]
    for step in result["steps"].values():
        entry = checks.read_report(
            checkout / step["result"]["outputs"]["report"]["path"]
        )["checks"][0]
        assert entry["identity"]["parameters"]["coverage"] is True


def test_input_change_invalidates_only_related_check_and_failed_reuse_stays_failed(
    checkout,
):
    calls = []
    runtime, workflow = setup(
        checkout, native(checkout, fake_process(7, lambda args, kw: calls.append(args)))
    )
    directory = runtime.create(workflow, checkout / ".test-tools/runs")
    first = runtime.resume(directory)
    assert first["exit_code"] == 7 and len(calls) == 5
    assert all(
        x["result"]["report_generated"] and x["status"] == "failed"
        for x in first["steps"].values()
    )
    assert runtime.resume(directory)["exit_code"] == 7 and len(calls) == 5
    (checkout / "tests/test_added.py").write_text("def test_added(): pass\n")
    assert runtime.resume(directory)["exit_code"] == 7 and len(calls) == 6
    assert "pytest" in calls[-1]


def test_current_input_changes_during_call_cannot_pass(checkout):
    def mutate(args, kw):
        (checkout / "tests/test_added.py").write_text("# mutation\n")

    runtime, workflow = setup(checkout, native(checkout, fake_process(action=mutate)))
    result = runtime.resume(runtime.create(workflow, checkout / ".test-tools/runs"))
    assert (
        result["exit_code"] == 3
        and result["steps"]["pytest"]["status"] == "unavailable"
    )
    assert not result["steps"]["pytest"]["result"]["current"]


def test_missing_tool_and_required_skips_are_never_passed(checkout, monkeypatch):
    def missing(*args):
        raise checks.PreconditionError("missing_tool", "uv missing")

    monkeypatch.setattr(checks, "preflight", missing)
    runtime, workflow = setup(checkout, native(checkout, fake_process()))
    result = runtime.resume(runtime.create(workflow, checkout / ".test-tools/runs"))
    assert result["exit_code"] == 127
    assert all(
        x["status"] == "unavailable" and not x["result"]["executed"]
        for x in result["steps"].values()
    )


def test_corrupt_frozen_native_junit_cannot_be_reused(checkout):
    calls = []
    runtime, workflow = setup(
        checkout,
        native(checkout, fake_process(action=lambda args, kw: calls.append(args))),
    )
    directory = runtime.create(workflow, checkout / ".test-tools/runs")
    result = runtime.resume(directory)
    resource = result["steps"]["pytest"]["result"]["outputs"]["evidence"][0]
    (checkout / resource["path"]).write_text("BROKEN")
    assert runtime.resume(directory)["exit_code"] == 0 and len(calls) == 6
    assert "pytest" in calls[-1]


def test_dagger_checks_share_one_real_api_batch_and_immutable_receipt_reuse(
    checkout, monkeypatch
):
    from test_dagger_checks import TOOLS, Dagger

    from engineering_ci import dagger_checks

    (checkout / "dagger.json").write_text('{"engineVersion":"v1.0.0-beta.16"}')
    (checkout / "dagger.lock").write_text("# lock\n")
    backend = Dagger(checkout, monkeypatch)
    monkeypatch.setattr(checks, "preflight", lambda *args: copy.deepcopy(TOOLS))

    def invoke(names, **kwargs):
        kwargs.pop("executor")
        return dagger_checks.execute(
            names, run=backend.run, which=backend.which, environ={}, **kwargs
        )

    tools = catalog(checkout, runner=invoke)
    workflow = generate(
        recipe(["pr"], executor="dagger"), tools, checkout / ".test-tools/generated"
    )
    runtime = Runtime(tools)
    directory = runtime.create(workflow, checkout / ".test-tools/runs")
    assert runtime.resume(directory)["exit_code"] == 0 and len(backend.exports) == 1
    assert runtime.resume(directory)["exit_code"] == 0 and len(backend.exports) == 1
    result = runtime.status(directory)
    assert all(x["result"]["reused"] for x in result["steps"].values())


def test_cli_generate_validate_run_status_and_resume_are_real_entrypoints(
    checkout, monkeypatch, capsys
):
    from engineering_ci.cli import workflow

    tools = catalog(checkout, runner=native(checkout, fake_process()))
    monkeypatch.setattr(workflow, "ROOT", checkout)
    monkeypatch.setattr(workflow, "catalog", lambda root: tools)
    assert (
        cli(
            [
                "workflow",
                "generate",
                "--profile",
                "pr",
                "--executor",
                "native",
                "--out",
                str(checkout / ".test-tools/generated"),
            ]
        )
        == 0
    )
    generated = json.loads(capsys.readouterr().out)
    assert generated["status"] == "generated"
    path = generated["output"]
    assert cli(["workflow", "validate", path]) == 0
    assert json.loads(capsys.readouterr().out)["plan"]["order"] == list(
        checks.PR_CHECKS
    )
    assert (
        cli(["workflow", "run", path, "--out", str(checkout / ".test-tools/runs")]) == 0
    )
    # Original tool output remains on stdout; the recorded result is independent JSON.
    capsys.readouterr()
    directory = next((checkout / ".test-tools/runs").iterdir())
    assert cli(["workflow", "status", str(directory)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["current_input_validity"] == "not_checked" and status["next_actions"]
    assert cli(["workflow", "resume", str(directory)]) == 0


def test_original_checks_import_no_generated_core_or_mining():
    source = Path(checks.__file__).read_text(encoding="utf-8")
    assert "workflow_core" not in source and "mining" not in source


def test_completed_domain_batch_is_recovered_if_core_did_not_receive_it(checkout):
    calls, interrupted = [], [True]
    original = native(
        checkout, fake_process(action=lambda args, kw: calls.append(args))
    )

    def stop_after_tools(names, **kwargs):
        result = original(names, **kwargs)
        if interrupted[0]:
            interrupted[0] = False
            raise KeyboardInterrupt
        return result

    runtime, workflow = setup(checkout, stop_after_tools)
    directory = runtime.create(workflow, checkout / ".test-tools/runs")
    assert runtime.resume(directory)["phase"] == "PAUSED" and len(calls) == 5
    result = runtime.resume(directory)
    # Native pytest has no frozen receipt at this exact stop point; four other
    # completed operations are recovered, and only that unverified pytest reruns.
    assert result["exit_code"] == 0 and len(calls) == 6 and "pytest" in calls[-1]
    assert all(
        item["result"]["reused"]
        for name, item in result["steps"].items()
        if name != "pytest"
    )


def test_cli_result_cannot_overwrite_definition_when_validation_rejects_it(
    checkout, monkeypatch, capsys
):
    from engineering_ci.cli import workflow

    monkeypatch.setattr(workflow, "ROOT", checkout)
    tools = catalog(checkout)
    path = generate(recipe(["version"]), tools, checkout / ".test-tools/generated")
    before = path.read_bytes()
    assert cli(["workflow", "validate", str(path), "--json", str(path)]) == 3
    assert path.read_bytes() == before
    assert json.loads(capsys.readouterr().out)["status"] == "invalid_or_unavailable"


def test_real_report_resource_flows_to_a_different_inspection_tool(checkout):
    tools = catalog(checkout, runner=native(checkout, fake_process()))
    tools.resolve("engineering-ci.check-inspect")[1].inspector = lambda path, **kwargs: (
        checks.inspect(path, environ={}, **kwargs)
    )
    proposed = recipe(["version"], executor="native")
    proposed["steps"].append(
        {
            "id": "inspect",
            "tool": "engineering-ci.check-inspect",
            "inputs": {"report": {"$from": "version", "pointer": "/report"}},
            "when": "always",
        }
    )
    workflow = generate(proposed, tools, checkout / ".test-tools/generated")
    runtime = Runtime(tools)
    result = runtime.resume(runtime.create(workflow, checkout / ".test-tools/runs"))
    assert result["status"] == "passed"
    assert (
        result["steps"]["inspect"]["result"]["outputs"]["passed_for_current_input"]
        is True
    )


def test_report_save_failure_does_not_mask_original_pytest_exit(checkout, monkeypatch):
    import subprocess

    def process(args, **kwargs):
        if "pytest" in args:
            (checkout / "core-junit.xml").write_text(
                '<testsuite tests="1" failures="1"><testcase name="failed"><failure>assert 1 == 2</failure></testcase></testsuite>'
            )
        return subprocess.CompletedProcess(args, 1 if "pytest" in args else 0)

    def unavailable(*args):
        raise OSError("Cannot save original check JSON")

    monkeypatch.setattr(checks, "atomic_json", unavailable)
    runtime, workflow = setup(checkout, native(checkout, process))
    result = runtime.resume(runtime.create(workflow, checkout / ".test-tools/runs"))
    assert result["status"] == "failed" and result["exit_code"] == 1
    assert result["steps"]["pytest"]["status"] == "failed"
    assert not result["steps"]["pytest"]["result"]["report_generated"]
