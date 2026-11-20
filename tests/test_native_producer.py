"""Producer contract tests; constructed observations are not native evidence."""

import copy
import json

import pytest

from engineering_ci import check_contract, execution, native_producer, weaver_tool

IMPL = {
    "implementation_id": "controlled-test-adapter",
    "implementation_version": "1",
    "implementation_sha256": "a" * 64,
}
CONTEXT = {
    "execution_ref": "execution:controlled",
    "parent_execution_ref": "execution:parent",
    "run_ref": "run/123",
    "attempt_ref": "attempt/2",
    "run_attempt_verification": "unverified",
    "origin_execution_attributed": False,
}
SOURCE = native_producer.source_reference()


def weaver_observation(
    kind="registry", *, failed_stage=None, gate_failed=False, baseline=False
):
    stages = ["registry-check", "registry-package", "registry-local-check"]
    if baseline:
        stages += [
            "baseline-check",
            "baseline-package",
            "baseline-local-check",
            "registry-diff",
        ]
    if kind != "registry":
        stages.append("live")
    commands = []
    for stage in stages:
        code = 17 if stage == failed_stage else 0
        commands.append(
            {
                "argv": ["weaver", "registry", "check"],
                "exit_code": code,
                "stdout": stage + ".stdout",
                "stderr": stage + ".stderr",
            }
        )
        if code:
            break
    return {
        "name": "weaver_" + kind,
        "status": "failed" if failed_stage or gate_failed else "passed",
        "exit_code": commands[-1]["exit_code"],
        "gate_exit_code": 9 if failed_stage or gate_failed else 0,
        "version": weaver_tool.VERSION,
        "tool_asset_sha256": weaver_tool.LINUX_SHA256,
        "baseline_required": baseline,
        "inputs": {"config_sha256": "c" * 64},
        "commands": commands,
        "trusted": False,
        **(
            {
                "phase": "post_completion",
                "terminal_correlation_status": "failed" if gate_failed else "passed",
            }
            if kind == "terminal"
            else {}
        ),
    }


def adapt(raw, **kwargs):
    return native_producer.weaver_check(
        raw, source=SOURCE, context=CONTEXT, impl=IMPL, **kwargs
    )


@pytest.mark.parametrize("kind,count", [("registry", 3), ("live", 4), ("terminal", 4)])
def test_actual_stages_have_native_contract_and_consumed_predecessor_ids(kind, count):
    raw = weaver_observation(kind)
    before = copy.deepcopy(raw)
    item = adapt(raw)
    assert raw == before
    assert len(item["native_results"]) == count
    assert item["native_satisfaction"]["status"] == "satisfied"
    for result in item["native_results"]:
        check_contract.validate_result(result)
        assert result["result_origin"] == "dagger"
        assert result["native_exit_code"] == 0
        assert result["evidence"]["execution"] == CONTEXT
        assert result["evidence"]["source_limit"] == "input_closure_not_proven"
        assert result["source_ref"]["binding_status"] == "unknown"
        assert result["tool"]["artifact_sha256"] == weaver_tool.LINUX_SHA256
    assert item["native_results"][0]["result_id"] in {
        ref["result_id"]
        for ref in item["native_results"][1]["input_refs"]
        if ref["kind"] == "check_result"
    }
    assert item["trusted"] is False


def test_baseline_stages_consume_both_real_registry_package_chains():
    item = adapt(weaver_observation(baseline=True))
    assert len(item["native_results"]) == 7
    assert item["native_satisfaction"]["status"] == "satisfied"
    assert (
        len(
            [
                ref
                for ref in item["native_results"][-1]["input_refs"]
                if ref["kind"] == "check_result"
            ]
        )
        == 2
    )


def test_native_failure_is_not_replaced_by_the_gate_exit_or_missing_later_stages():
    raw = weaver_observation(failed_stage="registry-check")
    item = adapt(raw)
    assert item["exit_code"] == 17 and item["gate_exit_code"] == 9
    assert len(item["native_results"]) == 1
    assert item["native_results"][0]["native_exit_code"] == 17
    assert item["native_results"][0]["status"] == "failed"


def test_gate_failure_after_native_zero_keeps_the_native_process_fact():
    raw = weaver_observation("terminal", gate_failed=True)
    raw["reason"] = "terminal_pipeline_signal_missing"
    item = adapt(raw)
    assert item["status"] == "failed"
    native = item["native_results"][-1]
    assert native["status"] == "passed"
    assert native["native_exit_code"] == 0
    assert native["evidence"]["gate_exit_code"] == 9
    assert native["evidence"]["terminal_correlation_status"] == "failed"
    assert native["evidence"]["terminal_correlation_reason"] == raw["reason"]
    assert item["native_satisfaction"]["status"] == "unsatisfied"
    assert item["native_satisfaction"]["reason"] == "terminal_correlation_unproven"
    assert item["native_satisfaction"]["selected_result_ids"] == []


@pytest.mark.parametrize(
    "correlation,gate_failed",
    [(None, False), ("not_executed", False), ("passed", True)],
)
def test_terminal_satisfaction_requires_passed_correlation_and_gate(
    correlation, gate_failed
):
    raw = weaver_observation("terminal", gate_failed=gate_failed)
    raw["terminal_correlation_status"] = correlation
    item = adapt(raw)
    assert item["native_results"][-1]["native_exit_code"] == 0
    assert item["native_satisfaction"]["status"] == "unsatisfied"
    assert item["native_satisfaction"]["reason"] == "terminal_correlation_unproven"


def test_configuration_refusal_has_no_fabricated_native_result():
    raw = weaver_observation()
    raw.update(
        status="failed",
        exit_code=None,
        reason="config_schema_invalid",
        commands=[
            {
                "argv": ["weaver", "--version"],
                "exit_code": 0,
                "stdout": "version.stdout",
                "stderr": "version.stderr",
            }
        ],
    )
    item = adapt(raw)
    assert item["native_results"] == []
    assert item["check_contract"]["status"] == "not_started"
    assert item["reason"] == "config_schema_invalid"
    assert item["exit_code"] is None


@pytest.mark.parametrize("framework", ["pytest", "unittest"])
@pytest.mark.parametrize("code", [0, 1])
def test_framework_results_preserve_native_exit_and_do_not_claim_source_or_admission(
    framework, code
):
    result = native_producer.test_check(
        {"status": "passed" if code == 0 else "failed", "exit_code": code},
        framework=framework,
        targets=["tests.test_controlled"],
        observation={"executable": "python", "stdout": "original", "stderr": "native"},
        source=SOURCE,
        context=CONTEXT,
        impl=IMPL,
    )
    native = result["native_result"]
    check_contract.validate_result(native)
    assert native["native_exit_code"] == code
    assert native["tool"]["access_id"] == "python/" + framework
    assert native["evidence"]["observation"]["stdout"] == "original"
    assert native["source_ref"]["binding_status"] == "unknown"
    assert "trusted" not in native


def test_test_prerequisite_blockage_is_a_definition_observation_without_native_exit():
    item = native_producer.test_check(
        {
            "status": "not_executed",
            "exit_code": None,
            "reason": "tach_graph_unvalidated",
        },
        framework="pytest",
        targets=["tests"],
        observation={},
        source=SOURCE,
        context=CONTEXT,
        impl=IMPL,
    )
    assert item["check_contract"]["status"] == "not_started"
    assert item["native_results"] == []
    assert item["reason"] == "tach_graph_unvalidated"


def test_implementation_version_and_bytes_participate_in_result_identity(tmp_path):
    path = tmp_path / "adapter.py"
    path.write_text("original implementation")
    first, hashes = native_producer.implementation(
        "eci/test-adapter", {"adapter.py": path}
    )
    path.write_text("changed implementation")
    second, _ = native_producer.implementation("eci/test-adapter", {"adapter.py": path})
    assert first["implementation_sha256"] != second["implementation_sha256"]
    assert "engineering_ci/check_contract.py" in hashes
    a = adapt(weaver_observation())
    b = native_producer.weaver_check(
        weaver_observation(),
        source=SOURCE,
        context=CONTEXT,
        impl={**IMPL, "implementation_version": "2"},
    )
    assert a["result_id"] != b["result_id"]


def test_parent_and_run_attempt_are_explicit_unverified_references():
    context = native_producer.execution_identity(
        run_ref="run/1", attempt_ref="attempt/2", parent_ref="parent"
    )
    assert context["run_attempt_verification"] == "unverified"
    assert context["origin_execution_attributed"] is False
    with pytest.raises(ValueError, match="pair_required"):
        native_producer.execution_identity(run_ref="run/1")
    assert (
        native_producer.execution_identity()["run_attempt_verification"]
        == "not_supplied"
    )


def test_declared_plan_binding_does_not_promote_actual_source_strength():
    plan = {
        "git": {
            "execution_tree": "a" * 40,
            "source_tree_id": "b" * 64,
            "content_binding": {"digest": "c" * 64, "paths": {"file": "d" * 64}},
        }
    }
    source = native_producer.source_reference(plan)
    assert source["binding_status"] == "identity_only"
    assert source["scope_kind"] == "changed_paths"
    assert source["verified_input_scope"] == []


@pytest.mark.parametrize("framework", ["unittest", "pytest"])
@pytest.mark.parametrize("fails", [False, True])
def test_real_host_framework_runs_once_even_when_its_definition_is_selected(
    tmp_path, framework, fails
):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/__init__.py").write_text("")
    assertion = "False" if fails else "True"
    (tmp_path / "tests/test_once.py").write_text(
        "import unittest\nfrom pathlib import Path\n"
        "class Once(unittest.TestCase):\n"
        "    def test_once(self):\n"
        "        p=Path('invocations.txt')\n"
        "        p.write_text(str(int(p.read_text())+1) if p.exists() else '1')\n"
        f"        self.assertTrue({assertion})\n"
    )
    plan = {
        "schema_version": 1,
        "kind": "engineering-ci-selection-plan",
        "generator": {"name": "engineering-ci", "version": "0.7.0"},
        "git": {"execution_tree": None},
        "selection": {
            "tests": ["tests.test_once"]
            if framework == "unittest"
            else ["tests/test_once.py"],
            "framework": framework,
        },
        "check_scope": [{"name": framework, "status": "selected"}],
    }
    report = execution.execute(
        plan, root=tmp_path, run_instance="execution:controlled-host"
    )
    assert report["execution_identity"]["execution_ref"] == "execution:controlled-host"
    assert (tmp_path / "invocations.txt").read_text() == "1"
    assert report["status"] == ("failed" if fails else "passed")
    checks = [item for item in report["checks"] if item["name"] == framework]
    assert len(checks) == 1
    result = checks[0]["native_result"]
    assert result["native_exit_code"] == int(fails)
    assert result["result_origin"] == "host"
    assert result == report["tests"]["native_result"]
    check_contract.validate_result(json.loads(json.dumps(result)))


@pytest.mark.parametrize("producer", ["weaver", "unittest", "pytest"])
@pytest.mark.parametrize("field", ["producer_ref", "path", "sha256"])
def test_generated_input_identity_participates_in_native_result_digest(producer, field):
    original = {
        "kind": "generated_input",
        "input_kind": "file",
        "producer_ref": {"kind": "prerequisite_action", "name": "generate"},
        "path": "generated/input.txt",
        "sha256": "a" * 64,
    }
    changed = {
        **original,
        field: {
            "producer_ref": {"kind": "prerequisite_action", "name": "other"},
            "path": "generated/other.txt",
            "sha256": "b" * 64,
        }[field],
    }

    def result(ref):
        if producer == "weaver":
            return adapt(weaver_observation(), generated_inputs=[ref])
        return native_producer.test_check(
            {"status": "passed", "exit_code": 0},
            framework=producer,
            targets=["tests.test_controlled"],
            observation={"stdout": "same output"},
            source=SOURCE,
            context=CONTEXT,
            impl=IMPL,
            generated_inputs=[ref],
        )

    first, second = result(original), result(changed)
    for item, expected in [(first, original), (second, changed)]:
        assert item["generated_input_refs"] == [expected]
        for native in item["native_results"]:
            assert expected in native["input_refs"]
            check_contract.validate_result(native)
    assert all(
        a["result_id"] != b["result_id"]
        for a, b in zip(first["native_results"], second["native_results"], strict=True)
    )
