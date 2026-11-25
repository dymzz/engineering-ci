"""Associate existing Container results with the shared Native Result contract."""

import json
from pathlib import Path

from engineering_ci import check_contract, native_producer


def implementation(kind):
    root = Path(__file__).parent
    names = [
        "main.py",
        "plan_execution.py",
        "plan_result.py",
        "native_results.py",
        "tooling.py",
    ]
    names += (
        ["weaver_runtime.py", "weaver.py", "weaver-requirements.txt"]
        if kind == "weaver"
        else [
            "pytest_quality.py",
            "pytest_report.py",
            "pytest_ci.py",
            "python_dependency_auth.py",
        ]
    )
    return native_producer.implementation(
        "engineering-ci/dagger/" + kind,
        {"dagger/" + name: root / name for name in names},
    )


def child_context(parent=None):
    parent = parent or {}
    return native_producer.execution_identity(
        parent_ref=parent.get("execution_ref"),
        run_ref=parent.get("run_ref"),
        attempt_ref=parent.get("attempt_ref"),
    )


async def directory_reference(source):
    return {"directory_id": str(await source.id()), "digest": await source.digest()}


async def weaver_result(
    files,
    source,
    kind,
    *,
    context=None,
    plan=None,
    generated_inputs=(),
    input_source=None,
    source_excludes=(),
):
    raw = json.loads(await files.file("report.json").contents())
    if kind not in {"registry", "live", "terminal"}:
        return files
    impl, hashes = implementation("weaver")
    raw_for_adapter = dict(raw)
    raw_for_adapter["native_artifacts"] = await directory_reference(files)
    if kind == "terminal":
        raw_for_adapter["phase"] = check_contract.POST_COMPLETION_PHASE
    consumed_ref = await directory_reference(source)
    input_ref = (
        await directory_reference(input_source)
        if input_source is not None
        else consumed_ref
    )
    filtered = input_ref["digest"] != consumed_ref["digest"]
    try:
        enriched = native_producer.weaver_check(
            raw_for_adapter,
            source=native_producer.source_reference(plan),
            context=child_context(context),
            impl=impl,
            source_directory=consumed_ref,
            generated_inputs=generated_inputs,
        )
    except check_contract.CheckContractError as error:
        enriched = {**raw_for_adapter, "check_contract": {"status": error.code}}
    if input_source is not None:
        enriched["input_source_directory"] = input_ref
        enriched["consumed_source_directory"] = consumed_ref
        enriched["container_source_filtered"] = filtered
        if filtered and source_excludes:
            enriched["container_source_excludes"] = list(source_excludes)
    enriched["implementation_sources"] = hashes
    enriched["trusted"] = False
    return files.with_new_file(
        "check-result.json", json.dumps(enriched, sort_keys=True)
    )


def _downgrade_binding_source(binding):
    """Strip proven source closure while keeping native rule and profile refs."""
    if not isinstance(binding, dict):
        return binding
    return {key: value for key, value in binding.items() if key != "source_ref"}


def _pre_execution_closure_verified(proof):
    return isinstance(proof, dict) and proof.get("source_equivalence_status") in {
        "verified_equivalent",
        "unfiltered_identical",
    }


async def test_result(
    item,
    container,
    source,
    framework,
    targets,
    *,
    artifacts,
    context=None,
    plan=None,
    generated_inputs=(),
    binding=None,
    consumed_source=None,
    source_excludes=(),
    pre_execution_proof=None,
):
    # This reads the environment of the retained result Container. It never
    # invokes pytest/unittest a second time, and never uses post-exec container
    # file hashes as proof of pre-execution input closure.
    probe = (
        "import sys,json,platform,importlib.metadata;"
        "print(json.dumps({'executable':sys.executable,'python':platform.python_version(),"
        "'platform':platform.platform(),'tool_version':"
        + (
            "importlib.metadata.version('pytest')"
            if framework == "pytest"
            else "platform.python_version()"
        )
        + "}))"
    )
    effective_binding = binding
    try:
        observation = json.loads(
            await container.with_exec(
                ["uv", "run", "--no-sync", "python", "-I", "-c", probe]
            ).stdout()
        )
        if isinstance(pre_execution_proof, dict):
            observation.update(pre_execution_proof)
            if (
                observation.get("container_source_filtered")
                and isinstance((effective_binding or {}).get("source_ref"), dict)
                and not _pre_execution_closure_verified(observation)
            ):
                effective_binding = _downgrade_binding_source(effective_binding)
        else:
            input_ref = await directory_reference(source)
            observation["source_directory"] = input_ref
            if consumed_source is not None:
                consumed_ref = await directory_reference(consumed_source)
                observation["consumed_source_directory"] = consumed_ref
                filtered = consumed_ref["digest"] != input_ref["digest"]
                observation["container_source_filtered"] = filtered
                if filtered and source_excludes:
                    observation["container_source_excludes"] = list(source_excludes)
                if not filtered:
                    observation["source_equivalence_status"] = "unfiltered_identical"
                elif isinstance((effective_binding or {}).get("source_ref"), dict):
                    # Fail closed if filtered without pre-execution closure proof.
                    effective_binding = _downgrade_binding_source(effective_binding)
                    observation["source_equivalence_status"] = "unproven"
                    observation["source_equivalence_reason"] = (
                        "pre_execution_proof_missing"
                    )
    except Exception as error:  # noqa: BLE001 - retain a completed test even if metadata transport fails
        # A failed metadata read cannot erase the completed test process.
        observation = {"status": "unavailable", "reason": str(error)}
        if isinstance(pre_execution_proof, dict):
            observation.update(pre_execution_proof)
        if (
            consumed_source is not None
            and isinstance((effective_binding or {}).get("source_ref"), dict)
            and not _pre_execution_closure_verified(pre_execution_proof)
        ):
            effective_binding = _downgrade_binding_source(effective_binding)
            observation.setdefault("source_equivalence_status", "unproven")
    try:
        observation["native_artifacts"] = await directory_reference(artifacts)
    except Exception as error:  # noqa: BLE001 - artifact metadata failure cannot rewrite the native exit
        observation["artifact_identity"] = {
            "status": "unavailable",
            "reason": str(error),
        }
    impl, hashes = implementation("tests")
    enriched = native_producer.test_check(
        item,
        framework=framework,
        targets=targets,
        observation=observation,
        source=native_producer.source_reference(plan),
        context=child_context(context),
        impl=impl,
        generated_inputs=generated_inputs,
        plan_authorized=True if plan is not None else None,
        binding=effective_binding,
    )
    enriched["implementation_sources"] = hashes
    return enriched
