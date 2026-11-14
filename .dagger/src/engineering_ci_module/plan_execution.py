"""Native Plan result collection; Dagger owns graphs and cache."""

import asyncio
import json
from pathlib import Path

import dagger
from dagger import dag

from .plan_result import PlanExecution
from .plan_result import result as make_result


def _aggregate(report):
    """Summarize collected facts; source-wide refusals return before this."""
    tests = report.get("tests") or {}
    checks = report.get("checks") or []
    prerequisites = report.get("prerequisites") or []

    def nonzero(item, keys):
        for key in keys:
            value = item.get(key)
            if type(value) is int and value != 0:
                return value
        return None

    for children, reason in (
        ([tests], "tests_failed"),
        (checks, "checks_failed"),
        (prerequisites, "prerequisites_failed"),
    ):
        for item in children:
            status = item.get("status")
            code = nonzero(item, ("gate_exit_code", "exit_code"))
            if status in ("failed", "error", "failure") or (
                status == "passed" and code is not None
            ):
                report.update(status="failed", exit_code=code or 1, reason=reason)
                return report

    for item in checks:
        contract = item.get("check_contract")
        if isinstance(contract, dict) and contract.get("status") not in {
            "valid",
            "not_started",
        }:
            report.update(
                status="blocked",
                exit_code=5,
                reason="check_contract_invalid",
            )
            return report
    from engineering_ci import execution as _host_execution

    if any(_host_execution._unsatisfied_native(item) for item in checks):
        report.update(
            status="blocked",
            exit_code=5,
            reason="native_satisfaction_incomplete",
        )
        return report
    pending = []
    if report.get("targets") or tests.get("reason"):
        pending.append(tests)
    pending.extend(checks)
    pending.extend(prerequisites)
    for item in pending:
        if item.get("status") not in ("passed", "advisory", "not_applicable"):
            report.update(
                status="blocked",
                # A native exit from an ODB context probe does not mean the
                # unexecuted prerequisite itself failed. Keep it in the child.
                exit_code=nonzero(item, ("gate_exit_code",)) or 5,
                reason=item.get("reason") or "required_checks_not_executed",
            )
            return report
    for entry in report.get("artifact_closure") or ():
        if not entry.get("verified"):
            report.update(
                status="blocked",
                exit_code=5,
                reason=entry.get("reason") or "data_artifact_missing",
            )
            return report
    report.update(status="passed", exit_code=0, reason=None)
    return report


async def _tests(module, source, targets, framework, python_version):
    """Use the existing pytest computation, or native unittest, exactly once."""
    if framework == "pytest":
        execution = await module._run_pytest(
            source, python_version, targets[0], _targets=targets
        )
        code = execution.exit_code
        stdout = execution.output
        stderr = await execution.container.stderr()
        xml = execution.xml
        files = module._pytest_bundle(execution)
    else:
        container = module._uv_container(source, python_version).with_exec(
            ["uv", "run", "python", "-m", "unittest", *targets],
            expect=dagger.ReturnType.ANY,
        )
        code = await container.exit_code()
        stdout = await container.stdout()
        stderr = await container.stderr()
        xml = None
        files = dag.directory().with_new_file("exit-code.txt", str(code) + "\n")
    files = files.with_new_file("stdout.txt", stdout).with_new_file(
        "stderr.txt", stderr
    )
    payload = {
        "tests": {"status": "passed" if code == 0 else "failed", "exit_code": code},
        "stdout": stdout,
        "stderr": stderr,
    }
    if xml is not None:
        payload["junit"] = xml
    return payload, files


async def _weaver(module, source, value, directory, item, config_path, tree_state):
    """Read one original /out Directory; do not rescan for Report or Enforce."""
    from engineering_ci import selection

    kind = item["name"].removeprefix("weaver_")
    metadata = {}
    if kind == "terminal":
        gate = selection.validate_post_completion_binding(value, directory)
        for key in (
            "input_closure_verified",
            "plan_closure_digest",
            "actual_closure_digest",
        ):
            metadata[key] = gate.get(key)
        if gate.get("source_tree_verified") != "not_requested":
            metadata["source_tree_verified"] = gate["source_tree_verified"]
            metadata["tree_verified"] = gate["source_tree_verified"] == "verified"
        if gate.get("source_digest"):
            metadata["source_digest"] = gate["source_digest"]
        if not gate["ok"]:
            return (
                {
                    "name": item["name"],
                    "status": "not_executed",
                    "reason": gate.get("reason") or "post_completion_binding_invalid",
                    "gate_exit_code": 1,
                    "trusted": False,
                    "authenticated": False,
                    "selected": item.get("status"),
                    **metadata,
                },
                None,
                metadata,
            )
        config_path = value["post_completion"]["weaver_config"]["path"]
    files = await module.weaver_artifacts(source, kind, config_path)
    native = json.loads(await files.file("report.json").contents())
    native["selected"] = item.get("status")
    native["source_tree_verified"] = metadata.get("source_tree_verified", tree_state)
    if kind == "terminal":
        native.update(phase=selection.POST_COMPLETION_PHASE, **metadata)
    return native, files, metadata


async def _native_results(
    module,
    source,
    value,
    directory,
    selected,
    report,
    artifacts,
    python_version,
    weaver_config,
):
    """Collect fixed independent native branches; Engine owns their DAG."""
    from engineering_ci import execution

    targets = value["selection"]["tests"]
    branches = []
    requirement = execution.test_requirement_failure(value)
    if requirement:
        report["tests"] = {
            "status": "not_executed",
            "exit_code": None,
            "reason": requirement,
        }
    elif targets:
        branches.append(
            (
                "tests",
                _tests(module, source, targets, report["framework"], python_version),
            )
        )
    branches.extend(
        (
            item["name"],
            _weaver(
                module,
                source,
                value,
                directory,
                item,
                weaver_config,
                report["source_tree_verified"],
            ),
        )
        for item in selected
        if item["name"] in {"weaver_registry", "weaver_live", "weaver_terminal"}
    )
    # These awaits resolve actual Container/Directory computations, not an ECI
    # queue or topological scheduler. A failed branch cannot cancel its peers.
    outputs = await asyncio.gather(
        *(task for _, task in branches), return_exceptions=True
    )
    for (name, _), output in zip(branches, outputs, strict=True):
        if isinstance(output, BaseException):
            if not isinstance(output, Exception):
                raise output
            native = {
                "name": name,
                "status": "not_executed",
                "exit_code": None,
                "reason": "native_execution_error",
                "error_type": type(output).__name__,
                "diagnostic": str(output),
            }
            if name == "tests":
                report["tests"] = native
            else:
                report["checks"].append(native)
            continue
        if name == "tests":
            payload, files = output
            report.update(payload)
            artifacts = artifacts.with_directory("tests", files)
        else:
            native, files, metadata = output
            report.update(metadata)
            report["checks"].append(native)
            if files is not None:
                artifacts = artifacts.with_directory("checks/" + name, files)
    report["result_sharing"] = {
        "scope": "this_result_bundle",
        "native_branches": [name for name, _ in branches],
        "origin_execution_attributed": False,
    }
    return artifacts


def _overlay_files(target_dir, artifacts):
    """Materialize producer-isolated File/Directory objects."""
    updated = target_dir
    for producer_root, rel, kind in sorted(set(artifacts)):
        if kind == "directory":
            updated = updated.with_directory(
                rel, dag.host().directory(str(Path(producer_root) / rel))
            )
        else:
            updated = updated.with_file(
                rel, dag.host().file(str(Path(producer_root) / rel))
            )
    return updated


def _bundle_produced(artifacts, artifacts_files):
    updated = artifacts
    for producer_ref, producer_root, rel, kind in sorted(
        artifacts_files,
        key=lambda item: (item[0]["kind"], item[0]["name"], item[2]),
    ):
        target = f"produced/{producer_ref['kind']}/{producer_ref['name']}/{rel}"
        if kind == "directory":
            updated = updated.with_directory(
                target, dag.host().directory(str(Path(producer_root) / rel))
            )
        else:
            updated = updated.with_file(
                target, dag.host().file(str(Path(producer_root) / rel))
            )
    return updated


def _host_adapter_plan(value, selected_host_checks):
    import copy

    host_plan = copy.deepcopy(value)
    host_plan.setdefault("selection", {})
    host_plan["selection"] = dict(host_plan["selection"])
    host_plan["selection"]["tests"] = []
    host_plan["git"] = dict(host_plan.get("git") or {})
    # This is only the Host adapter's filesystem view. Preserve the
    # original Git identity in source_handoff, and do not claim an
    # exported Directory has the original ODB or verified full Tree.
    host_plan["git"]["execution_tree"] = None
    host_plan["git"]["index_tree"] = None
    host_plan["git"]["content_binding"] = None
    host_plan["git"]["snapshot"] = "worktree"
    host_plan["check_scope"] = list(selected_host_checks)
    host_plan["selection"]["checks"] = [
        item["name"] for item in selected_host_checks if item.get("name")
    ]
    return host_plan


async def _execute_dag(
    module,
    source,
    value,
    directory,
    selected_checks,
    prerequisites,
    report,
    artifacts,
    python_version,
    weaver_config,
    exported_content_id,
):
    """Map v2 pre_execution_pass and data edges directly onto async Dagger work."""
    import tempfile
    import uuid
    from pathlib import Path

    from engineering_ci import execution, selection

    chosen = value.get("selection") or {}
    targets = chosen.get("tests") or []
    framework = chosen.get("framework", "unittest")
    actions = execution._prerequisite_actions(value, None)
    incoming = execution.pre_execution_edges(value)
    weaver_checks = {"weaver_registry", "weaver_live", "weaver_terminal"}
    host_checks = [
        item for item in selected_checks if item.get("name") not in weaver_checks
    ]
    host_plan = _host_adapter_plan(value, host_checks)
    selected_by_name = {item.get("name"): item for item in selected_checks}
    run_instance = uuid.uuid4().hex
    closure_records = []
    produced_files = []

    with tempfile.TemporaryDirectory() as artifact_dir:
        artifact_root = Path(artifact_dir)
        tasks = {}

        async def resolve_incoming(consumer_key):
            edges = incoming.get(consumer_key, ())
            if not edges:
                return None, [], []
            upstream = await asyncio.gather(
                *(
                    ensure(("prerequisite_action", e["producer_ref"]["name"]))
                    if e["producer_ref"]["kind"] == "prerequisite_action"
                    else ensure(("check_result", e["producer_ref"]["name"]))
                    for e in edges
                ),
                return_exceptions=True,
            )
            block_reasons = []
            blocked_by = []
            files = []
            seen_paths = {}
            for edge, producer_out in zip(edges, upstream, strict=True):
                if isinstance(producer_out, BaseException):
                    if not isinstance(producer_out, Exception):
                        raise producer_out
                    reason = "pre_execution_prerequisite_not_executed"
                    block_reasons.append(reason)
                    blocked_by.append(
                        {"producer_ref": edge["producer_ref"], "reason": reason}
                    )
                    continue
                producer_entry = (
                    producer_out[0] if isinstance(producer_out, tuple) else producer_out
                )
                p_ref = edge["producer_ref"]
                if (
                    edge["dependency_kind"] == "data"
                    and p_ref["kind"] != "prerequisite_action"
                ):
                    reason = "data_check_producer_unsupported"
                    block_reasons.append(reason)
                    blocked_by.append({"producer_ref": p_ref, "reason": reason})
                    closure_records.append(
                        {
                            "producer_ref": p_ref,
                            "consumer_ref": edge["consumer_ref"],
                            "output_ref": edge.get("output_ref"),
                            "verified": False,
                            "reason": reason,
                            "sha256": None,
                            "materialized_files": [],
                        }
                    )
                    continue
                producer_root = (
                    execution.producer_artifact_root(artifact_root, p_ref)
                    if p_ref["kind"] == "prerequisite_action"
                    else None
                )
                ok, reason, closure = execution.evaluate_edge(
                    edge, producer_entry, artifact_root=producer_root
                )
                if closure is not None:
                    closure_records.append(closure)
                    if closure.get("verified"):
                        for obj in closure.get("materialized_objects") or ():
                            rel = obj["path"]
                            kind = obj["kind"]
                            if rel in seen_paths and seen_paths[rel] != str(
                                producer_root
                            ):
                                block_reasons.append("data_artifact_conflict")
                                blocked_by.append(
                                    {
                                        "producer_ref": p_ref,
                                        "reason": "data_artifact_conflict",
                                    }
                                )
                                continue
                            seen_paths[rel] = str(producer_root)
                            files.append((producer_root, rel, kind))
                            produced_files.append((p_ref, producer_root, rel, kind))
                if not ok:
                    block_reasons.append(reason)
                    blocked_by.append(
                        {"producer_ref": edge["producer_ref"], "reason": reason}
                    )
            return (block_reasons[0] if block_reasons else None), blocked_by, files

        async def compute_node(node_key):
            kind = node_key[0]
            if kind == "prerequisite_action":
                name = node_key[1]
                producer_ref = {"kind": "prerequisite_action", "name": name}
                return await asyncio.to_thread(
                    execution.run_prerequisite_action,
                    name,
                    actions.get(name),
                    root=Path(directory),
                    execution_context="dagger_export",
                    artifact_root=execution.producer_artifact_root(
                        artifact_root, producer_ref
                    ),
                )
            if kind == "check_result":
                name = node_key[1]
                item = selected_by_name[name]
                block_reason, blocked_by, files = await resolve_incoming(
                    ("check", name)
                )
                if block_reason is not None:
                    entry = execution._blocked_consumer_entry(
                        name, item.get("status"), block_reason, blocked_by
                    )
                    if name not in weaver_checks:
                        entry = execution._decorate_checks(
                            host_plan,
                            [entry],
                            execution_context="dagger_export",
                            run_instance=run_instance,
                        )[0]
                    return entry, None, {}
                if name in weaver_checks:
                    consumer_source = _overlay_files(source, files)
                    native, check_files, metadata = await _weaver(
                        module,
                        consumer_source,
                        value,
                        directory,
                        item,
                        weaver_config,
                        report["source_tree_verified"],
                    )
                    return native, check_files, metadata
                generated_refs = execution.generated_input_refs(
                    closure_records, {"kind": "check", "name": name}
                )
                if files:
                    temporary, consumer_root = execution.build_consumer_view(
                        Path(directory), files
                    )
                    try:
                        entry = await asyncio.to_thread(
                            execution.run_host_check,
                            host_plan,
                            item,
                            root=consumer_root,
                            execution_context="dagger_export",
                            run_instance=run_instance,
                            artifact_paths=[rel for _, rel, _ in files],
                            generated_input_refs=generated_refs,
                        )
                    finally:
                        temporary.cleanup()
                    return entry, None, {}
                entry = await asyncio.to_thread(
                    execution.run_host_check,
                    host_plan,
                    item,
                    root=Path(directory),
                    execution_context="dagger_export",
                    run_instance=run_instance,
                    generated_input_refs=generated_refs,
                )
                return entry, None, {}
            if kind == "tests":
                block_reason, blocked_by, files = await resolve_incoming("tests")
                requirement = execution.test_requirement_failure(value)
                if requirement:
                    return (
                        {
                            "tests": {
                                "status": "not_executed",
                                "exit_code": None,
                                "reason": requirement,
                            }
                        },
                        None,
                    )
                if block_reason is not None:
                    return (
                        {
                            "tests": {
                                "status": "not_executed",
                                "exit_code": None,
                                "reason": block_reason,
                                "blocked_by": blocked_by,
                            }
                        },
                        None,
                    )
                consumer_source = _overlay_files(source, files)
                return await _tests(
                    module, consumer_source, targets, framework, python_version
                )
            if kind == "report_artifacts":
                report_block, report_blocked_by, _ = await resolve_incoming(
                    ("report", "artifacts")
                )
                if report_block is not None:
                    report["artifact_delivery"] = {
                        "status": "blocked",
                        "reason": report_block,
                        "blocked_by": report_blocked_by,
                    }
                return None

        def ensure(node_key):
            if node_key not in tasks:
                tasks[node_key] = asyncio.create_task(compute_node(node_key))
            return tasks[node_key]

        pre_names = {
            edge["producer_ref"]["name"]
            for edges in incoming.values()
            for edge in edges
            if edge["producer_ref"].get("kind") == "prerequisite_action"
        }
        post_prerequisites = [name for name in prerequisites if name not in pre_names]
        scheduled = []
        native_branches = []
        if targets:
            scheduled.append(("tests", ensure(("tests",))))
            native_branches.append("tests")
        for item in selected_checks:
            name = item.get("name")
            scheduled.append((("check", name), ensure(("check_result", name))))
            if name in weaver_checks:
                native_branches.append(name)
        for name in prerequisites:
            if name not in post_prerequisites:
                scheduled.append(
                    (("prereq", name), ensure(("prerequisite_action", name)))
                )
        if ("report", "artifacts") in incoming:
            scheduled.append((("report", "artifacts"), ensure(("report_artifacts",))))

        outputs = await asyncio.gather(
            *(task for _, task in scheduled), return_exceptions=True
        )
        for (slot, _), output in zip(scheduled, outputs, strict=True):
            if slot == "tests":
                if isinstance(output, BaseException):
                    if not isinstance(output, Exception):
                        raise output
                    report["tests"] = {
                        "status": "not_executed",
                        "exit_code": None,
                        "reason": "native_execution_error",
                        "error_type": type(output).__name__,
                        "diagnostic": str(output),
                    }
                else:
                    payload, test_files = output
                    report.update(payload)
                    if test_files is not None:
                        artifacts = artifacts.with_directory("tests", test_files)
            elif isinstance(slot, tuple) and slot[0] == "check":
                name = slot[1]
                if isinstance(output, BaseException):
                    if not isinstance(output, Exception):
                        raise output
                    report["checks"].append(
                        {
                            "name": name,
                            "status": "not_executed",
                            "exit_code": None,
                            "reason": "native_execution_error",
                            "error_type": type(output).__name__,
                            "diagnostic": str(output),
                        }
                    )
                else:
                    native, check_files, metadata = output
                    report.update(metadata)
                    report["checks"].append(native)
                    if check_files is not None:
                        artifacts = artifacts.with_directory(
                            "checks/" + name, check_files
                        )
            elif isinstance(slot, tuple) and slot[0] == "prereq":
                if isinstance(output, BaseException):
                    if not isinstance(output, Exception):
                        raise output
                    report["prerequisites"].append(
                        {
                            "name": slot[1],
                            "status": "not_executed",
                            "reason": "native_execution_error",
                            "diagnostic": str(output),
                        }
                    )
                else:
                    report["prerequisites"].append(output)

        for name in post_prerequisites:
            try:
                output = await ensure(("prerequisite_action", name))
            except BaseException as error:
                if not isinstance(error, Exception):
                    raise
                report["prerequisites"].append(
                    {
                        "name": name,
                        "status": "not_executed",
                        "reason": "native_execution_error",
                        "error_type": type(error).__name__,
                        "diagnostic": str(error),
                    }
                )
            else:
                report["prerequisites"].append(output)

        if produced_files:
            artifacts = _bundle_produced(artifacts, produced_files)
        if closure_records:
            closure_records.sort(
                key=lambda entry: (
                    entry["producer_ref"].get("kind", ""),
                    entry["producer_ref"].get("name", ""),
                    entry["output_ref"].get("path", ""),
                )
            )
            report["artifact_closure"] = closure_records
            report["source_handoff"]["generated_outputs_delivered"] = all(
                entry["verified"] for entry in closure_records
            )

        report["result_sharing"] = {
            "scope": "this_result_bundle",
            "native_branches": native_branches,
            "origin_execution_attributed": False,
        }
        after_host = selection.directory_content_id(directory)
        report["source_handoff"]["after_host_content_id"] = after_host
        if after_host != exported_content_id:
            report.update(
                status="not_executed",
                reason="exported_source_changed",
                exit_code=4,
                tree_verified=False,
                source_tree_verified="failed_after_host_action",
            )
            report["source_handoff"]["generated_outputs_delivered"] = False
            report["source_handoff"]["native_results_source"] = (
                "immutable_input_before_host_action"
            )
            return report, artifacts, True
    return report, artifacts, False


async def execute(module, plan, source, python_version, weaver_config) -> PlanExecution:
    """Execute plan targets and preserve one complete native result bundle."""
    artifacts = dag.directory()
    import tempfile

    from engineering_ci import execution, plan_contract, selection

    raw = await plan.contents()
    artifacts = artifacts.with_new_file("plan.json", raw)
    try:
        value = json.loads(raw)
    except ValueError:
        return make_result(
            execution.plan_rejection(execution.ExecutionError("plan_invalid")),
            artifacts,
        )
    try:
        execution.validate_plan(value)
    except execution.ExecutionError as error:
        return make_result(execution.plan_rejection(error), artifacts)
    declaration_consistent = (
        True if value["schema_version"] == plan_contract.PLAN_VERSION else None
    )
    chosen = value.get("selection") or {}
    targets = chosen.get("tests") or []
    framework = chosen.get("framework", "unittest")
    selected_checks = [
        item
        for item in value.get("check_scope", chosen.get("check_scope", []))
        if item.get("status") in {"selected", "unknown"}
    ]
    if ("check_scope" not in value and "check_scope" not in chosen) and chosen.get(
        "checks"
    ):
        selected_checks = [
            {"name": name, "status": "unknown"} for name in chosen["checks"]
        ]
    prerequisites = list(chosen.get("prerequisites") or [])
    if value.get("git", {}).get("snapshot") == "multi":
        return make_result(
            {
                "status": "not_executed",
                "reason": "multi_ref_requires_per_ref_execution",
                "plan_declaration_consistent": declaration_consistent,
            },
            artifacts,
        )
    if not targets and not selected_checks and not prerequisites:
        return make_result(
            {
                "status": "not_executed",
                "reason": "no_selected_tests",
                "plan_declaration_consistent": declaration_consistent,
            },
            artifacts,
        )
    git = value.get("git") or {}
    source_tree_id = git.get("source_tree_id")
    execution_tree = git.get("execution_tree")
    tree_verified = False
    source_tree_state = "not_requested"
    source_digest = None
    input_closure_verified = "not_requested"
    with tempfile.TemporaryDirectory() as directory:
        await source.export(directory)
        exported_content_id = selection.directory_content_id(directory)
        if source_tree_id and execution_tree:
            source_digest = selection.directory_content_id(directory)
            tree_verified = source_digest == source_tree_id
            source_tree_state = "verified" if tree_verified else "failed"
            if not tree_verified:
                return make_result(
                    {
                        "kind": execution.REPORT_KIND,
                        "schema_version": execution.SCHEMA_VERSION,
                        "plan_declaration_consistent": declaration_consistent,
                        "source_binding_verified": "unknown",
                        "status": "not_executed",
                        "reason": "source_tree_mismatch",
                        "exit_code": 4,
                        "tree_verified": False,
                        "source_tree_verified": source_tree_state,
                        "input_closure_verified": input_closure_verified,
                        "trusted": False,
                        "plan_execution_tree": execution_tree,
                        "source_tree_id": source_tree_id,
                        "source_digest": source_digest,
                    },
                    artifacts,
                )
        report = {
            "kind": execution.REPORT_KIND,
            "schema_version": execution.SCHEMA_VERSION,
            "dependency_contract": value.get("dependency_contract"),
            "plan_declaration_consistent": declaration_consistent,
            # Preserved metadata/export identity is not a verified #66
            # Source/Prerequisite receipt or actual-config binding.
            "source_binding_verified": "unknown",
            "plan": {"generator": value.get("generator"), "git": git},
            "execution_context": "dagger_export",
            "status": "passed",
            "exit_code": 0,
            "targets": targets,
            "framework": framework,
            # tree_verified is whole-tree Source-byte equivalence only when
            # that check ran. Post-completion uses input_closure_verified.
            # Neither field is #63 Check-evidence admission.
            "tree_verified": tree_verified,
            "source_tree_verified": source_tree_state,
            "input_closure_verified": input_closure_verified,
            "trusted": False,
            "plan_execution_tree": execution_tree,
            "source_tree_id": source_tree_id,
            "source_digest": source_digest,
            "tests": {"status": "not_executed", "exit_code": 3},
            "checks": [],
            "prerequisites": [],
            "source_handoff": {
                "original_git": git,
                "exported_content_id": exported_content_id,
                "host_context": "dagger_export",
                "original_git_snapshot_preserved": True,
            },
        }
        if execution.pre_execution_edges(value):
            report, artifacts, mutated = await _execute_dag(
                module,
                source,
                value,
                directory,
                selected_checks,
                prerequisites,
                report,
                artifacts,
                python_version,
                weaver_config,
                exported_content_id,
            )
            if mutated:
                return make_result(report, artifacts)
            _aggregate(report)
            return make_result(report, artifacts)

        artifacts = await _native_results(
            module,
            source,
            value,
            directory,
            selected_checks,
            report,
            artifacts,
            python_version,
            weaver_config,
        )
        if selected_checks or prerequisites:
            weaver_checks = {"weaver_registry", "weaver_live", "weaver_terminal"}
            host_checks = [
                item
                for item in selected_checks
                if item.get("name") not in weaver_checks
            ]
            check_plan = _host_adapter_plan(value, host_checks)
            host = (
                execution.execute(
                    check_plan, root=directory, execution_context="dagger_export"
                )
                if check_plan["check_scope"] or prerequisites
                else {
                    "status": "passed",
                    "exit_code": 0,
                    "checks": [],
                    "prerequisites": [],
                }
            )
            report["checks"].extend(host.get("checks") or [])
            report["prerequisites"] = host.get("prerequisites") or []
            after_host = selection.directory_content_id(directory)
            report["source_handoff"]["after_host_content_id"] = after_host
            if after_host != exported_content_id:
                report.update(
                    status="not_executed",
                    reason="exported_source_changed",
                    exit_code=4,
                    tree_verified=False,
                    source_tree_verified="failed_after_host_action",
                )
                report["source_handoff"]["generated_outputs_delivered"] = False
                report["source_handoff"]["native_results_source"] = (
                    "immutable_input_before_host_action"
                )
                return make_result(report, artifacts)
    _aggregate(report)
    return make_result(report, artifacts)
