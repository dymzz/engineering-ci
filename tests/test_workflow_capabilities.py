"""Explicit approval, independent version selection and original-check failure semantics."""

import copy
import os
import subprocess
import sys

import pytest
from filelock import FileLock
from test_checks import checkout as check_checkout
from test_workflow_comparison import trial

from engineering_ci.cli import main as cli
from engineering_ci.cli import workflow as workflow_cli
from engineering_ci.workflow_adapters.capabilities import (
    admission,
    approve,
    create_call,
    discover,
)
from engineering_ci.workflow_adapters.comparison import compare
from engineering_ci.workflow_adapters.evidence import execution_pair
from engineering_ci.workflow_adapters.feedback import prepare
from engineering_ci.workflow_core.capabilities import CapabilityStore
from engineering_ci.workflow_core.documents import WorkflowError, digest, read, write
from engineering_ci.workflow_core.schemas import SCHEMAS, check_schema, validate

checkout = check_checkout


def reviewed(checkout, *, fail=False):
    runtime, baseline, candidate, calls = trial(checkout, fail=fail)
    report = checkout / ".test-tools/comparison.json"
    write(
        report, compare(*execution_pair(baseline, candidate, runtime.catalog, checkout))
    )
    store = CapabilityStore(checkout / ".test-tools/capabilities")
    reference = approve(
        store,
        report,
        baseline,
        candidate,
        "pr-check-reuse",
        "1.0.0",
        runtime.catalog,
        checkout,
    )
    return runtime, store, reference, baseline, candidate, report, calls


def enable(runtime, store, checkout, version="1.0.0"):
    return store.activate(
        "pr-check-reuse",
        version,
        runtime.catalog,
        lambda value, path: admission(value, path, runtime.catalog, checkout),
    )


def test_approval_is_separate_from_activation_and_never_reruns_checks(checkout):
    runtime, store, reference, _, _, _, calls = reviewed(checkout)
    assert len(calls) == 5 and not store.index()["active"]
    assert reference["version"] == "1.0.0"
    with pytest.raises(WorkflowError, match="not enabled"):
        store.select("pr-check-reuse", None, runtime.catalog)
    enable(runtime, store, checkout)
    value, directory, revision = store.select("pr-check-reuse", None, runtime.catalog)
    assert value["approval_id"] == reference["approval_id"] and directory.is_dir()
    assert revision == 2 and len(calls) == 5
    enable(runtime, store, checkout)
    assert store.index()["revision"] == 2


def test_identical_approval_retry_and_version_conflict(checkout):
    runtime, store, reference, baseline, candidate, report, _ = reviewed(checkout)
    assert (
        approve(
            store,
            report,
            baseline,
            candidate,
            reference["name"],
            reference["version"],
            runtime.catalog,
            checkout,
        )
        == reference
    )
    value = read(
        store.location(reference["name"], reference["version"]) / "approval.json"
    )
    changed = copy.deepcopy(value)
    changed["decision"] = "auto-approve"
    with pytest.raises(WorkflowError):
        store.publish(changed, {}, {}, {})
    value["comparison_id"] = "f" * 64
    value["approval_id"] = digest(
        {key: item for key, item in value.items() if key != "approval_id"}
    )
    with pytest.raises(WorkflowError, match="another approval"):
        store.publish(value, {}, {}, {})


@pytest.mark.parametrize(
    "name,version", [("../main", "1.0.0"), ("safe", "../outside"), ("safe", "01.0.0")]
)
def test_invalid_identifiers_are_rejected_before_writing(checkout, name, version):
    store = CapabilityStore(checkout / ".test-tools/catalog")
    with pytest.raises(WorkflowError):
        store.location(name, version)
    assert not store.directory.exists()


def test_failed_original_quality_cannot_be_approved(checkout):
    with pytest.raises(WorkflowError, match="verified passed"):
        reviewed(checkout, fail=True)


def test_tampered_report_and_unavailable_resources_refuse_approval_or_activation(
    checkout,
):
    runtime, store, _, baseline, candidate, report, _ = reviewed(checkout)
    bad = read(report)
    bad["check_submissions_avoided"] = 100
    write(report, bad, replace=True)
    with pytest.raises(WorkflowError, match="unchanged"):
        approve(
            store,
            report,
            baseline,
            candidate,
            "pr-check-reuse",
            "1.0.1",
            runtime.catalog,
            checkout,
        )
    resource = read(candidate / "state.json")["steps"]["pytest"]["result"]["outputs"][
        "evidence"
    ][0]
    (checkout / resource["path"]).write_text("corrupt")
    with pytest.raises(WorkflowError, match="evidence"):
        enable(runtime, store, checkout)
    assert not store.index()["active"]


def test_modified_approved_definition_is_rejected(checkout):
    runtime, store, _, _, _, _, _ = reviewed(checkout)
    path = store.location("pr-check-reuse", "1.0.0") / "workflow.json"
    value = read(path)
    value["steps"][0]["inputs"]["coverage"] = True
    write(path, value, replace=True)
    with pytest.raises(WorkflowError):
        enable(runtime, store, checkout)


def test_explicit_activation_switches_versions_without_automatic_latest(checkout):
    runtime, store, _, baseline, candidate, report, _ = reviewed(checkout)
    enable(runtime, store, checkout)
    newer = approve(
        store,
        report,
        baseline,
        candidate,
        "pr-check-reuse",
        "1.1.0",
        runtime.catalog,
        checkout,
    )
    assert (
        store.select("pr-check-reuse", None, runtime.catalog)[0]["version"] == "1.0.0"
    )
    with pytest.raises(WorkflowError, match="not enabled"):
        store.select("pr-check-reuse", "1.1.0", runtime.catalog)
    enable(runtime, store, checkout, "1.1.0")
    assert (
        store.select("pr-check-reuse", None, runtime.catalog)[0]["approval_id"]
        == newer["approval_id"]
    )
    enable(runtime, store, checkout, "1.0.0")
    assert (
        store.select("pr-check-reuse", None, runtime.catalog)[0]["version"] == "1.0.0"
    )


def test_same_input_call_reuses_five_checks_and_feedback_is_not_another_execution(
    checkout,
):
    runtime, store, _, _, _, _, calls = reviewed(checkout)
    enable(runtime, store, checkout)
    directory, binding = create_call(
        store,
        "pr-check-reuse",
        None,
        checkout / ".test-tools/calls",
        runtime.catalog,
        checkout,
    )
    state = runtime.resume(directory)
    assert state["status"] == "passed" and len(calls) == 5
    first, path = prepare(directory, runtime.catalog, checkout)
    assert (
        first["metrics"]["check_submissions"] == 0
        and first["metrics"]["reused_checks"] == 5
    )
    assert first["capability"]["approval_id"] == binding["approval_id"]
    assert first["current_input_validity"] == "not_checked"
    assert (
        prepare(directory, runtime.catalog, checkout) == (first, path)
        and len(calls) == 5
    )
    store.deactivate("pr-check-reuse")
    assert prepare(directory, runtime.catalog, checkout) == (first, path)
    with pytest.raises(WorkflowError, match="not enabled"):
        create_call(
            store,
            "pr-check-reuse",
            "1.0.0",
            checkout / ".test-tools/calls",
            runtime.catalog,
            checkout,
        )
    assert runtime.status(directory) == state


def test_evidence_keeps_approval_binding_separate_from_raw_generation_facts(checkout):
    from engineering_ci.workflow_adapters.evidence import evidence

    runtime, store, reference, _, _, _, _ = reviewed(checkout)
    enable(runtime, store, checkout)
    directory, _ = create_call(
        store,
        "pr-check-reuse",
        None,
        checkout / ".test-tools/calls",
        runtime.catalog,
        checkout,
    )
    runtime.resume(directory)
    view = evidence(directory, runtime.catalog, checkout)
    assert view["approved"] is False
    assert (
        view["capability_call"]["capability"]["approval_id"] == reference["approval_id"]
    )
    assert view["capability_call"]["selection_state_now"] == "not_checked"


def test_concurrent_resume_of_approved_source_cannot_change_frozen_call_input(
    checkout, monkeypatch
):
    from engineering_ci.workflow_core import Runtime

    runtime, store, _, _, candidate, _, calls = reviewed(checkout)
    enable(runtime, store, checkout)
    original = Runtime.create

    def raced(self, workflow, destination, **kwargs):
        runtime.resume(candidate)
        return original(self, workflow, destination, **kwargs)

    monkeypatch.setattr(Runtime, "create", raced)
    with pytest.raises(WorkflowError, match="changed during call admission"):
        create_call(
            store,
            "pr-check-reuse",
            None,
            checkout / ".test-tools/calls",
            runtime.catalog,
            checkout,
        )
    assert len(calls) == 5
    directories = list((checkout / ".test-tools/calls").iterdir())
    assert (
        len(directories) == 1
        and Runtime(runtime.catalog).status(directories[0])["phase"] == "READY"
    )
    assert not (directories[0] / "capability.json").exists()


def test_changed_current_input_reruns_only_related_check(checkout):
    runtime, store, _, _, _, _, calls = reviewed(checkout)
    enable(runtime, store, checkout)
    (checkout / "tests/new_source.py").write_text("# input changed\n")
    directory, _ = create_call(
        store,
        "pr-check-reuse",
        None,
        checkout / ".test-tools/calls",
        runtime.catalog,
        checkout,
    )
    assert runtime.resume(directory)["status"] == "passed" and len(calls) == 6
    value, _ = prepare(directory, runtime.catalog, checkout)
    assert (
        value["metrics"]["check_submissions"] == 1
        and value["metrics"]["reused_checks"] == 4
    )
    assert not value["optimization_evaluated"]


def test_feedback_forged_selection_binding_cannot_replace_original_context(checkout):
    runtime, store, _, _, _, _, _ = reviewed(checkout)
    enable(runtime, store, checkout)
    directory, binding = create_call(
        store,
        "pr-check-reuse",
        None,
        checkout / ".test-tools/calls",
        runtime.catalog,
        checkout,
    )
    runtime.resume(directory)
    binding["approval_id"] = "f" * 64
    write(directory / "capability.json", binding, replace=True)
    with pytest.raises(WorkflowError, match="frozen"):
        prepare(directory, runtime.catalog, checkout)


def test_catalog_lock_blocks_concurrent_selection_mutation(checkout):
    runtime, store, _, _, _, _, _ = reviewed(checkout)
    with FileLock(store.directory / "catalog.lock", timeout=0):
        with pytest.raises(WorkflowError, match="busy"):
            enable(runtime, store, checkout)
        with pytest.raises(WorkflowError, match="busy"):
            store.deactivate("pr-check-reuse")


def test_discovery_is_paged_and_cursor_expires_after_selection_change(checkout):
    from engineering_ci.tracing.retrieval import encode

    runtime, store, _, baseline, candidate, report, calls = reviewed(checkout)
    approve(
        store,
        report,
        baseline,
        candidate,
        "pr-check-reuse",
        "1.1.0",
        runtime.catalog,
        checkout,
    )
    page = discover(store, runtime.catalog, checkout, limit=1)
    assert len(page["items"]) == 1 and page["page"]["next_after"] and len(calls) == 5
    for budget in (1024, 1200, 16384):
        bounded = discover(store, runtime.catalog, checkout, budget=budget)
        assert len(encode(bounded)) <= budget
        assert bounded["operation"] == "workflow.capabilities"
    next_page = discover(
        store, runtime.catalog, checkout, limit=1, after=page["page"]["next_after"]
    )
    assert next_page["items"][0]["version"] == "1.1.0"
    enable(runtime, store, checkout)
    with pytest.raises(Exception, match="changed|stale|match"):
        discover(store, runtime.catalog, checkout, after=page["page"]["next_after"])


def test_capability_schemas_and_sensitive_feedback_fields(checkout):
    runtime, store, _, _, _, _, _ = reviewed(checkout)
    enable(runtime, store, checkout)
    directory, _ = create_call(
        store,
        "pr-check-reuse",
        None,
        checkout / ".test-tools/calls",
        runtime.catalog,
        checkout,
    )
    runtime.resume(directory)
    value, _ = prepare(directory, runtime.catalog, checkout)
    for name in (
        "capability_approval",
        "capability_catalog",
        "capability_call",
        "capability_feedback",
    ):
        check_schema(SCHEMAS[name])
    value["token"] = "secret"
    with pytest.raises(WorkflowError):
        validate(value, SCHEMAS["capability_feedback"])


def test_cli_approval_selection_call_and_feedback_without_postgresql(
    checkout, monkeypatch, capsys
):
    runtime, store, _, baseline, candidate, report, calls = reviewed(checkout)
    monkeypatch.setattr(workflow_cli, "ROOT", checkout)
    monkeypatch.setattr(workflow_cli, "catalog", lambda root: runtime.catalog)
    args = ["--store", str(store.directory)]
    assert (
        cli(
            [
                "workflow",
                "approve",
                str(report),
                str(baseline),
                str(candidate),
                "--name",
                "pr-check-reuse",
                "--version",
                "1.0.0",
                *args,
            ]
        )
        == 0
    )
    assert (
        cli(
            [
                "workflow",
                "activate",
                "--name",
                "pr-check-reuse",
                "--version",
                "1.0.0",
                *args,
            ]
        )
        == 0
    )
    action = checkout / ".test-tools/call-action.json"
    assert (
        cli(
            [
                "workflow",
                "call",
                "--name",
                "pr-check-reuse",
                "--out",
                str(checkout / ".test-tools/calls"),
                "--json",
                str(action),
                *args,
            ]
        )
        == 0
    )
    result = read(action)
    assert result["feedback"]["status"] == "saved" and len(calls) == 5
    assert cli(["workflow", "feedback", result["execution"]]) == 0
    assert "secret" not in capsys.readouterr().out
    saved_index = read(store.directory / "catalog.json")
    assert (
        cli(
            [
                "workflow",
                "select",
                "--name",
                "pr-check-reuse",
                "--json",
                str(store.directory / "catalog.json"),
                *args,
            ]
        )
        == 3
    )
    assert read(store.directory / "catalog.json") == saved_index


def test_real_failed_pytest_call_is_nonzero_even_when_feedback_saves(
    checkout, monkeypatch
):
    from test_workflow_adapter import native

    runtime, store, _, _, _, _, _ = reviewed(checkout)
    enable(runtime, store, checkout)
    fixture = checkout / "tests/test_real_failure.py"
    fixture.write_text("def test_failure():\n    assert 1 == 2\n")
    actual_calls = []

    def process(args, **kwargs):
        if "pytest" not in args:
            return subprocess.CompletedProcess(args, 0)
        actual_calls.append(args)
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                str(fixture),
                "--junitxml=core-junit.xml",
            ],
            cwd=kwargs["cwd"],
            env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
            check=False,
        )

    runtime.catalog.resolve("python.pytest")[1].runner = native(checkout, process)
    monkeypatch.setattr(workflow_cli, "ROOT", checkout)
    monkeypatch.setattr(workflow_cli, "catalog", lambda root: runtime.catalog)
    output = checkout / ".test-tools/failed-call.json"
    assert (
        cli(
            [
                "workflow",
                "call",
                "--name",
                "pr-check-reuse",
                "--store",
                str(store.directory),
                "--out",
                str(checkout / ".test-tools/calls"),
                "--json",
                str(output),
            ]
        )
        == 1
    )
    result = read(output)
    assert len(actual_calls) == 1 and result["feedback"]["status"] == "saved"
    value = read(result["feedback"]["output"])
    assert (
        value["recorded_result"]["status"] == "failed"
        and value["recorded_result"]["exit_code"] == 1
    )
    assert (
        value["metrics"]["check_submissions"] == 1
        and not value["optimization_evaluated"]
    )


def test_call_exception_keeps_context_and_resume_retains_selected_version(
    checkout, monkeypatch
):
    from engineering_ci.workflow_core import Runtime

    runtime, store, reference, _, _, _, _ = reviewed(checkout)
    enable(runtime, store, checkout)
    monkeypatch.setattr(workflow_cli, "ROOT", checkout)
    monkeypatch.setattr(workflow_cli, "catalog", lambda root: runtime.catalog)
    original = Runtime.resume

    def interrupted(self, directory):
        raise OSError("PRIVATE interruption")

    monkeypatch.setattr(Runtime, "resume", interrupted)
    output = checkout / ".test-tools/interrupted-call.json"
    assert (
        cli(
            [
                "workflow",
                "call",
                "--name",
                "pr-check-reuse",
                "--store",
                str(store.directory),
                "--out",
                str(checkout / ".test-tools/calls"),
                "--json",
                str(output),
            ]
        )
        == 3
    )
    result = read(output)
    assert result["execution"] and result["next_actions"][0][-1] == result["execution"]
    store.deactivate("pr-check-reuse")
    monkeypatch.setattr(Runtime, "resume", original)
    assert cli(["workflow", "resume", result["execution"], "--json", str(output)]) == 0
    resumed = read(output)
    feedback = read(resumed["feedback"]["output"])
    assert feedback["capability"]["approval_id"] == reference["approval_id"]


def test_partial_approval_write_is_unpublished_and_same_command_can_recover(
    checkout, monkeypatch
):
    from vesryn.workflow_core import capabilities

    runtime, store, _, baseline, candidate, report, _ = reviewed(checkout)
    original = capabilities.write

    def interrupted(path, value, **kwargs):
        if path.name == "approval.json":
            raise OSError("interrupted approval")
        return original(path, value, **kwargs)

    monkeypatch.setattr(capabilities, "write", interrupted)
    with pytest.raises(OSError):
        approve(
            store,
            report,
            baseline,
            candidate,
            "pr-check-reuse",
            "1.0.1",
            runtime.catalog,
            checkout,
        )
    assert len(store.index()["versions"]) == 1
    monkeypatch.setattr(capabilities, "write", original)
    approve(
        store,
        report,
        baseline,
        candidate,
        "pr-check-reuse",
        "1.0.1",
        runtime.catalog,
        checkout,
    )
    assert len(store.index()["versions"]) == 2


def test_feedback_write_failure_does_not_change_original_call_pass(
    checkout, monkeypatch
):
    from engineering_ci.workflow_adapters import feedback

    runtime, store, _, _, _, _, _ = reviewed(checkout)
    enable(runtime, store, checkout)
    monkeypatch.setattr(workflow_cli, "ROOT", checkout)
    monkeypatch.setattr(workflow_cli, "catalog", lambda root: runtime.catalog)

    def unavailable(*args, **kwargs):
        raise OSError("PRIVATE write failure")

    monkeypatch.setattr(feedback, "write", unavailable)
    output = checkout / ".test-tools/no-feedback-call.json"
    assert (
        cli(
            [
                "workflow",
                "call",
                "--name",
                "pr-check-reuse",
                "--store",
                str(store.directory),
                "--out",
                str(checkout / ".test-tools/calls"),
                "--json",
                str(output),
            ]
        )
        == 0
    )
    result = read(output)
    assert (
        result["feedback"]["status"] == "unavailable"
        and result["result"]["exit_code"] == 0
    )


def test_cli_feedback_archive_failure_keeps_saved_record_and_original_exit(
    checkout, monkeypatch, capsys
):
    from engineering_ci.workflow_adapters import feedback

    runtime, store, _, _, _, _, _ = reviewed(checkout)
    enable(runtime, store, checkout)
    directory, _ = create_call(
        store,
        "pr-check-reuse",
        None,
        checkout / ".test-tools/calls",
        runtime.catalog,
        checkout,
    )
    runtime.resume(directory)
    monkeypatch.setattr(workflow_cli, "ROOT", checkout)
    monkeypatch.setattr(workflow_cli, "catalog", lambda root: runtime.catalog)

    def broken(*args):
        raise RuntimeError("postgres://private:secret@host")

    monkeypatch.setattr(feedback, "archive", broken)
    output = checkout / ".test-tools/feedback-action.json"
    assert (
        cli(
            ["workflow", "feedback", str(directory), "--archive", "--json", str(output)]
        )
        == 3
    )
    result = read(output)
    assert (
        result["status"] == "archive_unavailable" and result["execution_exit_code"] == 0
    )
    assert result["output"] and runtime.status(directory)["exit_code"] == 0
    assert "secret" not in capsys.readouterr().out
