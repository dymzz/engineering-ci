"""P1 and P2 verification: Persisted selection binding and end-to-end Registry invocation."""

import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from test_checks import checkout as check_checkout
from vesryn.workflow_core import Catalog, DiscoveryScope, ToolResult, ToolSpec, generate
from vesryn.workflow_core.documents import WorkflowError, digest, read, write
from vesryn.workflow_core.schemas import validate

from engineering_ci import checks
from engineering_ci.cli import workflow
from engineering_ci.workflow_adapters.binding import (
    BindingRuntime,
    call_binding,
    create_binding,
    create_bound_execution,
    restore_binding,
    resume_binding,
    verify_binding,
)
from engineering_ci.workflow_adapters.checks import catalog as legacy_catalog
from engineering_ci.workflow_adapters.checks import dagger_catalog
from engineering_ci.workflow_adapters.contracts import SELECTION_BINDING

checkout = check_checkout


# ==============================================================================
# P1: 持久化选择 Binding
# ==============================================================================


def test_create_selection_binding_records_tool_provider_version_parameters_scope_digest(
    checkout,
):
    """Binding stores tool identity, Provider, version, parameters, scope and contract digest."""
    tools = dagger_catalog(checkout)
    scope = DiscoveryScope(
        repository="engineering-ci",
        environment="dagger",
        permissions=frozenset(["check"]),
    )
    binding, path = create_binding(
        tools,
        "python.ruff-lint",
        provider="engineering-ci-dagger",
        parameters={"executor": "dagger"},
        scope=scope,
        out_dir=checkout / ".test-tools/workflow-bindings",
        root=checkout,
    )

    assert path.is_file()
    assert path.name == "binding.json"
    validate(binding, SELECTION_BINDING)

    assert binding["tool_id"] == "python.ruff-lint"
    assert binding["provider"] == "engineering-ci-dagger"
    assert binding["version"] == "1"
    assert binding["parameters"] == {"executor": "dagger"}
    assert binding["scope"] == {
        "repository": "engineering-ci",
        "environment": "dagger",
        "permissions": ["check"],
    }

    spec, _ = tools.resolve("python.ruff-lint")
    assert binding["contract_sha256"] == spec.sha256
    assert binding["contract_summary"]["tool_id"] == "python.ruff-lint"
    assert binding["contract_summary"]["domain"] == "engineering"
    assert binding["contract_summary"]["effect"] == "check"
    assert binding["contract_summary"]["contract_sha256"] == spec.sha256

    # Deterministic binding_id check
    expected_id = digest({k: v for k, v in binding.items() if k != "binding_id"})
    assert binding["binding_id"] == expected_id


def test_verify_binding_rejects_tampered_id(checkout):
    """Tampering with any binding field causes binding_id verification to fail."""
    tools = dagger_catalog(checkout)
    binding, _ = create_binding(
        tools,
        "python.ruff-lint",
        provider="engineering-ci-dagger",
        parameters={"executor": "dagger"},
        out_dir=checkout / ".test-tools/workflow-bindings",
        root=checkout,
    )

    tampered = copy.deepcopy(binding)
    tampered["parameters"]["executor"] = "auto"
    with pytest.raises(WorkflowError, match="integrity check failed"):
        verify_binding(tampered, tools)


def test_verify_binding_rejects_changed_contract_sha256_and_does_not_select_latest(
    checkout,
):
    """Refuse to reuse stale binding when tool contract changes; do not auto-select latest."""
    tools = dagger_catalog(checkout)
    binding, _ = create_binding(
        tools,
        "python.ruff-lint",
        provider="engineering-ci-dagger",
        parameters={"executor": "dagger"},
        out_dir=checkout / ".test-tools/workflow-bindings",
        root=checkout,
    )

    # Simulate catalog with altered ToolSpec contract (e.g. modified input_schema)
    altered_tools = Catalog()
    for doc in tools.documents():
        spec, adapter = tools.resolve(doc["tool_id"])
        if spec.tool_id == "python.ruff-lint":
            mutated_schema = copy.deepcopy(spec.input_schema)
            mutated_schema["properties"]["extra_field"] = {"type": "string"}
            mutated_spec = ToolSpec(
                tool_id=spec.tool_id,
                input_schema=mutated_schema,
                output_schema=spec.output_schema,
                contract_version=spec.contract_version,
                effect=spec.effect,
                environments=spec.environments,
                domain=spec.domain,
            )
            altered_tools.register(mutated_spec, adapter)
        else:
            altered_tools.register(spec, adapter)

    with pytest.raises(
        WorkflowError,
        match="Tool contract sha256 changed; refuse to use stale binding; do not auto-select latest",
    ):
        verify_binding(binding, altered_tools)


def test_verify_binding_rejects_changed_version_without_auto_selecting_latest(checkout):
    """Refuse stale binding when contract_version increments; no auto-selection of latest."""
    tools = dagger_catalog(checkout)
    binding, _ = create_binding(
        tools,
        "python.ruff-lint",
        provider="engineering-ci-dagger",
        parameters={"executor": "dagger"},
        out_dir=checkout / ".test-tools/workflow-bindings",
        root=checkout,
    )

    # Simulate tool incrementing version to "2"
    v2_tools = Catalog()
    for doc in tools.documents():
        spec, adapter = tools.resolve(doc["tool_id"])
        if spec.tool_id == "python.ruff-lint":
            v2_spec = ToolSpec(
                tool_id=spec.tool_id,
                input_schema=spec.input_schema,
                output_schema=spec.output_schema,
                contract_version="2",
                effect=spec.effect,
                environments=spec.environments,
                domain=spec.domain,
            )
            v2_tools.register(v2_spec, adapter)
        else:
            v2_tools.register(spec, adapter)

    with pytest.raises(
        WorkflowError,
        match="Tool contract version changed from 1 to 2; refuse to use stale binding; do not auto-select latest",
    ):
        verify_binding(binding, v2_tools)


def test_verify_binding_rejects_scope_mismatch(checkout):
    """Refuse binding when permission scope does not admit the tool."""
    tools = dagger_catalog(checkout)
    binding, _ = create_binding(
        tools,
        "python.ruff-lint",
        provider="engineering-ci-dagger",
        parameters={"executor": "dagger"},
        scope=DiscoveryScope(
            repository="engineering-ci",
            environment="dagger",
            permissions=frozenset(["check"]),
        ),
        out_dir=checkout / ".test-tools/workflow-bindings",
        root=checkout,
    )

    # Restricting scope to empty permissions blocks the bound tool
    restricted_scope = DiscoveryScope(
        repository="engineering-ci",
        environment="dagger",
        permissions=frozenset(["read"]),
    )
    with pytest.raises(WorkflowError, match="scope mismatch"):
        verify_binding(binding, tools, current_scope=restricted_scope)


def test_restore_binding_explicitly_discloses_bound_tool_facts(checkout):
    """Restoring from execution checkpoint explicitly discloses what is invoked."""

    def mock_runner(names, **kwargs):
        report = {
            "schema_version": 1,
            "profile": "ruff-lint",
            "selectors": ["ruff-lint"],
            "checks": [
                {
                    "name": "ruff-lint",
                    "status": "passed",
                    "exit_code": 0,
                    "executed": True,
                    "input_stable": True,
                    "evidence": {},
                }
            ],
        }
        checks.atomic_json(kwargs["output"], report)
        return report, 0

    tools = dagger_catalog(checkout, runner=mock_runner)
    binding, _ = create_binding(
        tools,
        "python.ruff-lint",
        provider="engineering-ci-dagger",
        parameters={"executor": "dagger"},
        out_dir=checkout / ".test-tools/workflow-bindings",
        root=checkout,
    )

    called = call_binding(
        binding, tools, checkout / ".test-tools/workflow-runs", root=checkout
    )
    execution_dir = Path(called["execution"])
    assert (execution_dir / "binding.json").is_file()

    restored = restore_binding(execution_dir, tools)
    assert restored["binding"]["tool_id"] == "python.ruff-lint"
    assert restored["binding"]["provider"] == "engineering-ci-dagger"
    assert restored["binding"]["version"] == "1"
    assert restored["binding"]["parameters"] == {"executor": "dagger"}
    assert restored["binding"]["contract_summary"]["tool_id"] == "python.ruff-lint"
    assert restored["status"] == "passed"
    assert restored["exit_code"] == 0


def test_restore_binding_refuses_when_contract_changed_after_binding(checkout):
    """Refuse to restore execution when underlying tool contract changed after execution."""

    def mock_runner(names, **kwargs):
        report = {
            "schema_version": 1,
            "profile": "ruff-lint",
            "selectors": ["ruff-lint"],
            "checks": [
                {
                    "name": "ruff-lint",
                    "status": "passed",
                    "exit_code": 0,
                    "executed": True,
                    "input_stable": True,
                    "evidence": {},
                }
            ],
        }
        checks.atomic_json(kwargs["output"], report)
        return report, 0

    tools = dagger_catalog(checkout, runner=mock_runner)
    binding, _ = create_binding(
        tools,
        "python.ruff-lint",
        provider="engineering-ci-dagger",
        parameters={"executor": "dagger"},
        out_dir=checkout / ".test-tools/workflow-bindings",
        root=checkout,
    )

    called = call_binding(
        binding, tools, checkout / ".test-tools/workflow-runs", root=checkout
    )
    execution_dir = Path(called["execution"])

    # Simulate catalog with altered ToolSpec
    altered_tools = Catalog()
    for doc in tools.documents():
        spec, adapter = tools.resolve(doc["tool_id"])
        if spec.tool_id == "python.ruff-lint":
            mutated_schema = copy.deepcopy(spec.input_schema)
            mutated_schema["properties"]["new_rule"] = {"type": "boolean"}
            mutated_spec = ToolSpec(
                tool_id=spec.tool_id,
                input_schema=mutated_schema,
                output_schema=spec.output_schema,
                contract_version=spec.contract_version,
                effect=spec.effect,
                environments=spec.environments,
                domain=spec.domain,
            )
            altered_tools.register(mutated_spec, adapter)
        else:
            altered_tools.register(spec, adapter)

    with pytest.raises(
        WorkflowError,
        match="Tool contract sha256 changed; refuse to use stale binding; do not auto-select latest",
    ):
        restore_binding(execution_dir, altered_tools)


# ==============================================================================
# P2: 端到端调用验收
# ==============================================================================


def test_end_to_end_call_via_registry_reaches_dagger_execution_graph(checkout):
    """Through Registry, select engineering check and invoke existing Dagger execution graph."""
    called_names = []

    def mock_runner(names, **kwargs):
        called_names.extend(names)
        assert kwargs.get("executor") == "dagger"
        output_file = kwargs["output"]
        report = {
            "schema_version": 1,
            "profile": "ruff-lint",
            "selectors": ["ruff-lint"],
            "checks": [
                {
                    "name": "ruff-lint",
                    "status": "passed",
                    "exit_code": 0,
                    "executed": True,
                    "input_stable": True,
                    "evidence": {},
                }
            ],
        }
        checks.atomic_json(output_file, report)
        return report, 0

    tools = dagger_catalog(checkout, runner=mock_runner)
    binding, _ = create_binding(
        tools,
        "python.ruff-lint",
        provider="engineering-ci-dagger",
        parameters={"executor": "dagger"},
        out_dir=checkout / ".test-tools/workflow-bindings",
        root=checkout,
    )

    result = call_binding(
        binding, tools, checkout / ".test-tools/workflow-runs", root=checkout
    )
    assert called_names == ["ruff-lint"]
    assert result["status"] == "passed"
    assert result["exit_code"] == 0

    step = result["step_results"]["ruff-lint"]
    assert step["status"] == "passed"
    assert step["exit_code"] == 0
    assert step["executed"] is True
    # Output report is correlated with raw evidence file
    report_resource = step["report"]
    assert report_resource is not None
    assert (checkout / report_resource["path"]).is_file()
    assert report_resource["sha256"] is not None


def test_end_to_end_failure_is_not_swallowed_and_retains_raw_evidence(checkout):
    """Failed check exit code and failure status are strictly preserved, not swallowed."""

    def mock_failing_runner(names, **kwargs):
        output_file = kwargs["output"]
        report = {
            "schema_version": 1,
            "profile": "pytest",
            "selectors": ["pytest"],
            "checks": [
                {
                    "name": "pytest",
                    "status": "failed",
                    "exit_code": 7,
                    "executed": True,
                    "input_stable": True,
                    "evidence": {},
                }
            ],
        }
        checks.atomic_json(output_file, report)
        return report, 7

    tools = dagger_catalog(checkout, runner=mock_failing_runner)
    binding, _ = create_binding(
        tools,
        "python.pytest",
        provider="engineering-ci-dagger",
        parameters={"executor": "dagger"},
        out_dir=checkout / ".test-tools/workflow-bindings",
        root=checkout,
    )

    result = call_binding(
        binding, tools, checkout / ".test-tools/workflow-runs", root=checkout
    )
    assert result["status"] == "failed"
    assert result["exit_code"] == 7

    step = result["step_results"]["pytest"]
    assert step["status"] == "failed"
    assert step["exit_code"] == 7
    assert step["executed"] is True
    assert (checkout / step["report"]["path"]).is_file()


def test_end_to_end_recovery_and_reuse_does_not_duplicate_pytest(checkout):
    """Resume and reuse verified evidence without repeating pytest."""
    import hashlib

    junit_bytes = b"<junit/>"
    (checkout / "core-junit.xml").write_bytes(junit_bytes)
    junit_sha = hashlib.sha256(junit_bytes).hexdigest()

    call_counts = {"pytest": 0}

    def mock_runner(names, **kwargs):
        call_counts["pytest"] += 1
        output_file = kwargs["output"]
        report = {
            "schema_version": 1,
            "profile": "pytest",
            "selectors": ["pytest"],
            "checks": [
                {
                    "name": "pytest",
                    "status": "passed",
                    "exit_code": 0,
                    "executed": True,
                    "executed_this_invocation": call_counts["pytest"] == 1,
                    "reused": call_counts["pytest"] > 1,
                    "input_stable": True,
                    "evidence": {
                        "junit": {
                            "path": (checkout / "core-junit.xml")
                            .relative_to(checkout)
                            .as_posix(),
                            "sha256": junit_sha,
                        }
                    },
                }
            ],
        }
        checks.atomic_json(output_file, report)
        return report, 0

    tools = dagger_catalog(checkout, runner=mock_runner)
    binding, _ = create_binding(
        tools,
        "python.pytest",
        provider="engineering-ci-dagger",
        parameters={"executor": "dagger"},
        out_dir=checkout / ".test-tools/workflow-bindings",
        root=checkout,
    )

    first = call_binding(
        binding, tools, checkout / ".test-tools/workflow-runs", root=checkout
    )
    assert first["status"] == "passed"
    assert call_counts["pytest"] == 1

    # Second call with reuse_from reuses previous evidence
    second = call_binding(
        binding,
        tools,
        checkout / ".test-tools/workflow-runs",
        reuse_from=first["execution"],
        root=checkout,
    )
    assert second["status"] == "passed"
    assert second["step_results"]["pytest"]["reused"] is True


@pytest.fixture
def binding_workspace():
    """Keep real Provider CLI outputs inside the primary checkout."""
    directory = checks.ROOT / ".test-tools"
    directory.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="binding-tests-", dir=directory) as temporary:
        yield Path(temporary)


def test_cli_workflow_select_and_call_binding_integration(
    binding_workspace, monkeypatch, capsys
):
    """Use the installed Provider and real catalog; replace only tool invocation."""
    from engineering_ci.workflow_adapters.provider import ProviderAdapter

    checkout = binding_workspace
    called_names = []

    original_request = ProviderAdapter.request

    def request(self, action, value=None, run_dir=None):
        if action == "tools":
            return original_request(self, action, value, run_dir)
        called_names.extend(call["tool_id"] for call in value["calls"])
        return {
            "results": {
                call["step_id"]: ToolResult(
                    "passed",
                    0,
                    executed=True,
                    complete=True,
                    current=True,
                ).document()
                for call in value["calls"]
            }
        }

    monkeypatch.setattr(ProviderAdapter, "request", request)

    # 1. Select tool via CLI
    select_out = checkout / ".test-tools/workflow-bindings"
    code = workflow.main(
        [
            "select",
            "--tool",
            "python.ruff-lint",
            "--out",
            str(select_out),
            "--json",
            str(checkout / "select.json"),
        ]
    )
    assert code == 0
    select_receipt = json.loads((checkout / "select.json").read_text())
    assert select_receipt["status"] == "selected"
    assert select_receipt["tool_id"] == "python.ruff-lint"
    spec = dagger_catalog(checks.ROOT).resolve("python.ruff-lint")[0]
    assert select_receipt["contract_sha256"] == spec.sha256
    assert (
        select_receipt["contract_sha256"]
        != legacy_catalog(checks.ROOT).resolve("python.ruff-lint")[0].sha256
    )
    binding_file = Path(select_receipt["binding_file"])
    assert binding_file.is_file()

    # 2. Call tool via CLI using the binding
    runs_out = checkout / ".test-tools/workflow-runs"
    code = workflow.main(
        [
            "call",
            "--binding",
            str(binding_file),
            "--out",
            str(runs_out),
            "--json",
            str(checkout / "call.json"),
        ]
    )
    assert code == 0
    call_receipt = json.loads((checkout / "call.json").read_text())
    assert call_receipt["status"] == "passed"
    assert call_receipt["exit_code"] == 0
    execution_dir = Path(call_receipt["execution"])
    assert (execution_dir / "binding.json").is_file()
    assert called_names == ["python.ruff-lint"]

    # 3. Status inspects the execution and identifies the binding
    code = workflow.main(
        [
            "status",
            str(execution_dir),
            "--json",
            str(checkout / "status.json"),
        ]
    )
    assert code == 0
    status_receipt = json.loads((checkout / "status.json").read_text())
    assert status_receipt["status"] == "passed"
    assert status_receipt["selection_binding"]["status"] == "bound"
    assert status_receipt["selection_binding"]["tool_id"] == "python.ruff-lint"
    assert status_receipt["selection_binding"]["provider"] == "engineering-ci-dagger"


def test_end_to_end_interruption_and_resume_preserves_lifecycle(checkout):
    """Interrupted execution preserves exit_code 130 and is resumed without losing binding."""
    interrupted_once = [True]

    def mock_runner(names, **kwargs):
        if interrupted_once[0]:
            interrupted_once[0] = False
            raise KeyboardInterrupt()
        output_file = kwargs["output"]
        report = {
            "schema_version": 1,
            "profile": "ruff-lint",
            "selectors": ["ruff-lint"],
            "checks": [
                {
                    "name": "ruff-lint",
                    "status": "passed",
                    "exit_code": 0,
                    "executed": True,
                    "input_stable": True,
                    "evidence": {},
                }
            ],
        }
        checks.atomic_json(output_file, report)
        return report, 0

    tools = dagger_catalog(checkout, runner=mock_runner)
    binding, _ = create_binding(
        tools,
        "python.ruff-lint",
        provider="engineering-ci-dagger",
        parameters={"executor": "dagger"},
        out_dir=checkout / ".test-tools/workflow-bindings",
        root=checkout,
    )

    first = call_binding(
        binding, tools, checkout / ".test-tools/workflow-runs", root=checkout
    )
    assert first["status"] == "interrupted"
    assert first["exit_code"] == 130
    execution_dir = Path(first["execution"])
    assert (execution_dir / "binding.json").is_file()

    # Resume completes the execution
    resumed = resume_binding(execution_dir, tools)
    assert resumed["status"] == "passed"
    assert resumed["exit_code"] == 0
    assert resumed["binding"]["tool_id"] == "python.ruff-lint"


@pytest.fixture
def admitted_selection(checkout):
    calls = []

    def forbidden_runner(*args, **kwargs):
        calls.append(args)
        raise AssertionError("Rejected selections must not invoke a check")

    tools = dagger_catalog(checkout, runner=forbidden_runner)
    binding, path = create_binding(
        tools, "python.ruff-lint", out_dir=checkout / "bindings", root=checkout
    )
    return checkout, tools, binding, path, calls


def reidentify(binding):
    binding["binding_id"] = digest(
        {key: value for key, value in binding.items() if key != "binding_id"}
    )


def alternate_workflow(root, tools, binding, *, tool=None, inputs=None, extra=False):
    steps = [
        {
            "id": binding["tool_id"].split(".")[-1],
            "tool": tool or binding["tool_id"],
            "inputs": binding["parameters"] if inputs is None else inputs,
        }
    ]
    if extra:
        steps.append({"id": "extra", "tool": "python.ruff-format", "inputs": {}})
    return generate(
        {"schema_version": 1, "kind": "workflow-recipe", "steps": steps},
        tools,
        root / "alternatives",
    )


@pytest.mark.parametrize("change", ["tool", "parameters", "extra_step", "generation"])
def test_substituted_generation_is_rejected_before_execution(
    admitted_selection, change
):
    root, tools, binding, path, calls = admitted_selection
    replacement = alternate_workflow(
        root,
        tools,
        binding,
        tool="python.ruff-format" if change == "tool" else None,
        inputs={"coverage": True} if change == "parameters" else None,
        extra=change == "extra_step",
    )
    for name in ("workflow.json", "provenance.json"):
        (path.parent / name).write_bytes((replacement.parent / name).read_bytes())
    with pytest.raises(WorkflowError, match="Workflow identity differs"):
        call_binding(binding, tools, root / "runs", root=root)
    assert calls == []
    assert not (root / "runs").exists()


@pytest.mark.parametrize("change", ["tool", "parameters", "extra_step"])
def test_binding_ids_cannot_disguise_different_step_semantics(
    admitted_selection, change
):
    root, tools, binding, _, calls = admitted_selection
    replacement = alternate_workflow(
        root,
        tools,
        binding,
        tool="python.ruff-format" if change == "tool" else None,
        inputs={"coverage": True} if change == "parameters" else None,
        extra=change == "extra_step",
    )
    value = read(replacement)
    binding["workflow"].update(
        definition_id=value["definition_id"],
        generation_id=value["generation_id"],
        workflow_path=str(replacement),
    )
    reidentify(binding)
    with pytest.raises(WorkflowError, match="Workflow steps differ"):
        call_binding(binding, tools, root / "runs", root=root)
    assert calls == []


@pytest.mark.parametrize(
    "change",
    [
        "digest",
        "provider",
        "permissions",
        "execution",
        "generation",
        "repository",
        "environment",
        "summary",
    ],
)
def test_resume_rejects_invalid_binding_without_state_mutation(
    admitted_selection, change
):
    root, tools, binding, _, calls = admitted_selection
    directory = create_bound_execution(binding, tools, root / "runs", root=root)
    frozen = read(directory / "binding.json")
    if change == "digest":
        frozen["binding_id"] = "0" * 64
    else:
        if change == "provider":
            frozen["provider"] = "another-provider"
        elif change == "permissions":
            frozen["scope"]["permissions"] = []
        elif change == "execution":
            frozen["execution_id"] = "0" * 32
        elif change == "generation":
            frozen["workflow"]["generation_id"] = "0" * 32
        elif change in {"repository", "environment"}:
            frozen["scope"][change] = ""
        else:
            frozen["contract_summary"]["tool_id"] = "python.ruff-format"
        reidentify(frozen)
    write(directory / "binding.json", frozen, replace=True)
    before = (directory / "state.json").read_bytes()
    with pytest.raises(WorkflowError):
        resume_binding(directory, tools)
    assert calls == []
    assert (directory / "state.json").read_bytes() == before
    assert not (directory / "observations").exists()


def test_call_honors_host_scope_and_provider_identity(admitted_selection):
    root, tools, binding, _, calls = admitted_selection
    with pytest.raises(WorkflowError, match="scope mismatch"):
        call_binding(
            binding,
            tools,
            root / "runs",
            root=root,
            current_scope=DiscoveryScope(environment="dagger", permissions=frozenset()),
        )
    with pytest.raises(WorkflowError, match="Provider does not match"):
        create_binding(
            tools, binding["tool_id"], provider="not-installed", out_dir=root / "bad"
        )
    assert calls == []
    assert not (root / "runs").exists()
    assert not (root / "bad").exists()


def test_runtime_admission_is_inside_existing_execution_lock(
    admitted_selection, monkeypatch
):
    from filelock import FileLock, Timeout

    root, tools, binding, _, calls = admitted_selection
    directory = create_bound_execution(binding, tools, root / "runs", root=root)
    original_status = BindingRuntime.status

    def status(self, path):
        # Another owner cannot enter while Binding admission runs.
        with pytest.raises(Timeout), FileLock(directory / "execution.lock", timeout=0):
            pass
        frozen = read(directory / "binding.json")
        frozen["binding_id"] = "0" * 64
        write(directory / "binding.json", frozen, replace=True)
        return original_status(self, path)

    monkeypatch.setattr(BindingRuntime, "status", status)
    with pytest.raises(WorkflowError, match="integrity check failed"):
        resume_binding(directory, tools)
    assert calls == []
    assert read(directory / "state.json")["phase"] == "READY"


def test_cli_unknown_provider_does_not_generate_binding(binding_workspace, capsys):
    output = binding_workspace / "bindings"
    assert (
        workflow.main(
            [
                "select",
                "--tool",
                "python.ruff-lint",
                "--provider",
                "not-installed",
                "--out",
                str(output),
            ]
        )
        == 3
    )
    assert (
        "Provider must name one explicit installed domain adapter"
        in capsys.readouterr().out
    )
    assert not output.exists()


@pytest.mark.parametrize("action", ["resume", "run"])
def test_real_cli_rejects_binding_before_provider_invocation(
    binding_workspace, monkeypatch, capsys, action
):
    from engineering_ci.workflow_adapters.binding import binding_catalog
    from engineering_ci.workflow_adapters.provider import ProviderAdapter

    tools = binding_catalog("engineering-ci-dagger", checks.ROOT)
    binding, path = create_binding(
        tools,
        "python.ruff-lint",
        out_dir=binding_workspace / "bindings",
        root=checks.ROOT,
    )
    directory = create_bound_execution(
        binding, tools, binding_workspace / "runs", root=checks.ROOT
    )
    if action == "resume":
        target = directory
        document = read(directory / "binding.json")
        document["binding_id"] = "0" * 64
        write(directory / "binding.json", document, replace=True)
    else:
        target = path.parent / "workflow.json"
        replacement = alternate_workflow(
            binding_workspace, tools, binding, tool="python.ruff-format"
        )
        for name in ("workflow.json", "provenance.json"):
            (path.parent / name).write_bytes((replacement.parent / name).read_bytes())
    before = (directory / "state.json").read_bytes()
    invocations = []
    original_request = ProviderAdapter.request

    def request(self, operation, value=None, run_dir=None):
        if operation == "invoke":
            invocations.append(value)
            raise AssertionError("Provider invocation preceded Binding admission")
        return original_request(self, operation, value, run_dir)

    monkeypatch.setattr(ProviderAdapter, "request", request)
    argv = [action, str(target)]
    if action == "run":
        argv += ["--out", str(binding_workspace / "new-runs")]
    assert workflow.main(argv) == 3
    assert invocations == []
    assert (directory / "state.json").read_bytes() == before
    assert not (binding_workspace / "new-runs").exists()
