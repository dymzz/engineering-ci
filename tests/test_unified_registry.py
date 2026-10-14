"""Domain-only Dagger Provider and optional consumption of the shared Registry."""

import pytest
from test_checks import checkout as check_checkout

from engineering_ci.cli import workflow
from engineering_ci.workflow_adapters.checks import catalog, dagger_catalog
from engineering_ci.workflow_core import Invocation
from engineering_ci.workflow_core.documents import WorkflowError
from engineering_ci.workflow_core.schemas import validate

checkout = check_checkout


def test_domain_provider_does_not_duplicate_shared_definitions(checkout):
    legacy, domain = catalog(checkout), dagger_catalog(checkout)
    assert domain.page()["total"] == 5
    for document in domain.documents():
        assert document["tool_id"] not in {
            "codex.events",
            "verify.stateful",
            "benchmark.discovery",
        }
        spec, _ = domain.resolve(document["tool_id"])
        assert spec.environments == ("dagger",) and spec.domain == "engineering"
        assert legacy.resolve(document["tool_id"])[0].environments == (
            "native",
            "dagger",
        )
        with pytest.raises(WorkflowError):
            validate({"executor": "native"}, spec.input_schema)


def test_domain_adapter_refuses_native_without_calling_original_runner(checkout):
    calls = []
    tools = dagger_catalog(
        checkout, runner=lambda *args, **kwargs: calls.append(kwargs)
    )
    _, adapter = tools.resolve("engineering-ci.version-check")
    result = adapter.invoke(
        (
            Invocation(
                "version", "engineering-ci.version-check", {"executor": "native"}, 1
            ),
        ),
        type("Context", (), {"run_dir": checkout / ".test-tools/runs"})(),
    )["version"]
    assert result.status == "unavailable" and not result.executed and calls == []


def test_registry_command_delegates_to_vesryn_with_explicit_context(monkeypatch):
    from vesryn.cli import workflow as shared

    calls = []
    monkeypatch.setattr(shared, "main", lambda argv: calls.append(argv) or 17)
    assert (
        workflow.main(
            ["registry", "--repo", "engineering-ci", "search", "--query", "events"]
        )
        == 17
    )
    assert calls == [
        [
            "--adapter",
            "unified",
            "--root",
            str(workflow.ROOT),
            "--repo",
            "engineering-ci",
            "search",
            "--query",
            "events",
        ]
    ]


def test_domain_default_reaches_original_runner_as_dagger(checkout):
    from types import SimpleNamespace

    from vesryn.workflow_core import Invocation

    def original(names, **kwargs):
        assert kwargs["executor"] == "dagger"
        raise RuntimeError("original runner reached")

    tools = dagger_catalog(checkout, runner=original)
    _, adapter = tools.resolve("engineering-ci.version-check")
    context = SimpleNamespace(
        run_dir=checkout / ".test-tools/runs", execution_id="a" * 32
    )
    with pytest.raises(RuntimeError, match="original runner reached"):
        adapter.invoke(
            (Invocation("version", "engineering-ci.version-check", {}, 1),), context
        )


def test_provider_cli_keeps_failed_result_separate_from_json_delivery(
    tmp_path, monkeypatch, capsys
):
    import json

    from vesryn.workflow_core import Catalog, ToolResult, ToolSpec
    from vesryn.workflow_core.schemas import object_schema

    from engineering_ci.workflow_adapters import checks as adapter_module
    from engineering_ci.workflow_adapters import provider

    class FailedAdapter:
        def invoke(self, calls, context):
            print("progress from original check")
            return {
                call.step_id: ToolResult("failed", 13, executed=True) for call in calls
            }

    spec = ToolSpec("domain.check", object_schema({}), object_schema({}))
    tools = Catalog()
    tools.register(spec, FailedAdapter())
    monkeypatch.setattr(adapter_module, "dagger_catalog", lambda *args, **kwargs: tools)
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "calls": [
                    {
                        "step_id": "check",
                        "tool_id": spec.tool_id,
                        "inputs": {},
                        "attempt": 1,
                        "previous": None,
                        "contract_sha256": spec.sha256,
                    }
                ],
                "context": {"execution_id": "a" * 32, "run_dir": str(tmp_path)},
            }
        ),
        encoding="utf-8",
    )
    assert (
        provider.main(["invoke", "--root", str(tmp_path), "--request", str(request)])
        == 0
    )
    captured = capsys.readouterr()
    assert "progress from original check" in captured.err
    result = json.loads(captured.out)["results"]["check"]
    assert (
        result["status"] == "failed"
        and result["exit_code"] == 13
        and result["executed"]
    )
