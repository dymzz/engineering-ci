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


_WORKSPACE_CLOSURE_PROBE = """
import hashlib, json, os, stat
root = '/workspace'
root_real = os.path.realpath(root)
with open('/tmp/eci-expected-paths.json', 'r', encoding='utf-8') as handle:
    rel_paths = json.load(handle)
observed = {}
missing = []
non_regular = []
for rel in rel_paths:
    parts = [part for part in rel.split('/') if part]
    if not parts or any(part in {'.', '..'} for part in parts):
        non_regular.append(rel)
        continue
    cur = root
    problem = None
    for idx, part in enumerate(parts):
        cur = os.path.join(cur, part)
        if not os.path.lexists(cur):
            problem = 'missing'
            break
        if os.path.islink(cur):
            problem = 'non_regular'
            break
        if idx == len(parts) - 1 and not stat.S_ISREG(os.lstat(cur).st_mode):
            problem = 'non_regular'
            break
    if problem == 'missing':
        missing.append(rel)
        continue
    if problem is not None:
        non_regular.append(rel)
        continue
    if os.path.realpath(cur) != os.path.join(root_real, *parts):
        non_regular.append(rel)
        continue
    digest = hashlib.sha256()
    with open(cur, 'rb') as handle:
        for chunk in iter(lambda: handle.read(65536), b''):
            digest.update(chunk)
    observed[rel] = digest.hexdigest()
tree_digest = hashlib.sha256(
    json.dumps(observed, sort_keys=True, separators=(',', ':')).encode('utf-8')
).hexdigest()
print(json.dumps({'paths': observed, 'digest': tree_digest, 'missing': missing, 'non_regular': non_regular}, sort_keys=True))
""".strip()


def _downgrade_binding_source(binding):
    """Strip proven source closure while keeping native rule and profile refs."""
    if not isinstance(binding, dict):
        return binding
    return {key: value for key, value in binding.items() if key != "source_ref"}


async def _verify_bound_container_closure(container, binding, bound_binding):
    """Prove filtered container /workspace matches bound regular-file bytes."""
    source_ref = binding.get("source_ref") if isinstance(binding, dict) else None
    if not isinstance(source_ref, dict):
        return binding, {"source_equivalence_status": "unproven"}
    if (
        not isinstance(bound_binding, dict)
        or not isinstance(bound_binding.get("paths"), dict)
        or not isinstance(bound_binding.get("digest"), str)
        or bound_binding.get("unverified_paths")
        or bound_binding.get("link_bindings")
    ):
        return _downgrade_binding_source(binding), {
            "source_equivalence_status": "unproven",
            "source_equivalence_reason": "bound_closure_unavailable",
        }
    if not hasattr(container, "with_new_file"):
        return _downgrade_binding_source(binding), {
            "source_equivalence_status": "unproven",
            "source_equivalence_reason": "container_probe_unsupported",
        }
    expected_paths = bound_binding["paths"]
    expected_digest = bound_binding["digest"]
    try:
        output = await (
            container.with_new_file(
                "/tmp/eci-expected-paths.json",
                json.dumps(sorted(expected_paths), sort_keys=True),
            )
            .with_exec(
                [
                    "uv",
                    "run",
                    "--no-sync",
                    "python",
                    "-I",
                    "-c",
                    _WORKSPACE_CLOSURE_PROBE,
                ]
            )
            .stdout()
        )
        probed = json.loads(output)
    except Exception as error:  # noqa: BLE001 - retain executed test when closure probe fails
        return _downgrade_binding_source(binding), {
            "source_equivalence_status": "unproven",
            "source_equivalence_reason": str(error),
        }
    missing = list(probed.get("missing") or [])
    non_regular = list(probed.get("non_regular") or [])
    observed_paths = probed.get("paths") or {}
    actual_digest = probed.get("digest")
    if (
        not missing
        and not non_regular
        and isinstance(observed_paths, dict)
        and observed_paths == expected_paths
        and actual_digest == expected_digest
    ):
        return binding, {
            "source_equivalence_status": "verified_equivalent",
            "verified_bound_digest": expected_digest,
        }
    if missing:
        status = "missing_bound_inputs"
    elif non_regular:
        status = "non_regular_bound_inputs"
    else:
        status = "mismatched_bound_inputs"
    mismatched = sorted(
        path
        for path, digest in expected_paths.items()
        if path not in missing
        and path not in non_regular
        and observed_paths.get(path) != digest
    )
    extra = sorted(path for path in observed_paths if path not in expected_paths)
    return _downgrade_binding_source(binding), {
        "source_equivalence_status": status,
        **({"missing_bound_paths": missing} if missing else {}),
        **({"non_regular_bound_paths": non_regular} if non_regular else {}),
        **({"mismatched_bound_paths": mismatched} if mismatched else {}),
        **({"unexpected_bound_paths": extra} if extra else {}),
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
    bound_binding=None,
):
    # This reads the environment of the retained result Container. It never
    # invokes pytest/unittest a second time.
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
        input_ref = await directory_reference(source)
        observation["source_directory"] = input_ref
        if consumed_source is not None:
            consumed_ref = await directory_reference(consumed_source)
            observation["consumed_source_directory"] = consumed_ref
            observation["container_source_filtered"] = (
                consumed_ref["digest"] != input_ref["digest"]
            )
            if observation["container_source_filtered"] and source_excludes:
                observation["container_source_excludes"] = list(source_excludes)
            if observation["container_source_filtered"]:
                if isinstance((binding or {}).get("source_ref"), dict):
                    effective_binding, eq_info = await _verify_bound_container_closure(
                        container, binding, bound_binding
                    )
                    observation.update(eq_info)
            else:
                observation["source_equivalence_status"] = "unfiltered_identical"
    except Exception as error:  # noqa: BLE001 - retain a completed test even if metadata transport fails
        # A failed metadata read cannot erase the completed test process.
        observation = {"status": "unavailable", "reason": str(error)}
        if consumed_source is not None and isinstance(
            (binding or {}).get("source_ref"), dict
        ):
            effective_binding = _downgrade_binding_source(binding)
            observation["source_equivalence_status"] = "unproven"
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
