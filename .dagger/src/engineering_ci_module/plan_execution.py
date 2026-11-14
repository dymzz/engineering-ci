"""Native Plan result collection; Dagger owns graphs and cache."""

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import dagger
from dagger import dag

from .plan_result import PlanExecution
from .plan_result import result as make_result


def _aggregate(report):
    """Summarize collected facts; source-wide refusals return before this."""
    delivery = report.get("artifact_delivery")
    if isinstance(delivery, dict):
        for consumer in delivery.get("consumers", []):
            consumer["failures"] = sorted(
                consumer.get("failures", []),
                key=lambda item: json.dumps(item, sort_keys=True),
            )
        delivery["consumers"] = sorted(
            delivery.get("consumers", []),
            key=lambda item: json.dumps(item, sort_keys=True),
        )
        if delivery.get("consumers") and delivery["consumers"][0].get("failures"):
            delivery["reason"] = delivery["consumers"][0]["failures"][0].get("reason")
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
    delivery = report.get("artifact_delivery") or {}
    if delivery.get("status") == "blocked":
        report.update(
            status="blocked",
            exit_code=5,
            reason=delivery.get("reason") or "data_artifact_conflict",
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


def _overlay_files(target_dir, objects):
    """Materialize producer-isolated File/Directory objects onto a Directory."""
    updated = target_dir
    for rel, kind, obj in sorted(objects, key=lambda item: (item[0], item[1])):
        if kind == "directory":
            updated = updated.with_directory(rel, obj)
        else:
            updated = updated.with_file(rel, obj)
    return updated


def _bundle_produced(artifacts, produced_objects):
    """Add deduplicated producer objects under produced/<kind>/<name>/<rel>."""
    updated = artifacts
    seen = set()
    for producer_ref, rel, kind, obj in sorted(
        produced_objects,
        key=lambda item: (item[0]["kind"], item[0]["name"], item[1], item[2]),
    ):
        target = f"produced/{producer_ref['kind']}/{producer_ref['name']}/{rel}"
        if target in seen:
            continue
        seen.add(target)
        if kind == "directory":
            updated = updated.with_directory(target, obj)
        else:
            updated = updated.with_file(target, obj)
    return updated


def _scratch_relative(target: Path, scratch_root: Path) -> str:
    """Resolve one scratch-confined path relative to current_module workdir."""
    resolved_target = target.resolve()
    resolved_root = scratch_root.resolve()
    try:
        rel = resolved_target.relative_to(resolved_root).as_posix()
    except ValueError as error:
        raise ValueError(
            f"workdir path {resolved_target!s} escapes scratch root {resolved_root!s}"
        ) from error
    if not rel or rel == ".":
        raise ValueError("workdir relative path cannot be empty")
    return rel


def _record_delivery_failure(report, consumer_ref, failures):
    """Record per-consumer artifact delivery failures without clobbering siblings."""
    delivery = report.setdefault(
        "artifact_delivery", {"status": "blocked", "consumers": []}
    )
    delivery["status"] = "blocked"
    normalized_ref = dict(consumer_ref)
    normalized_failures = [dict(failure) for failure in failures]
    existing = next(
        (
            consumer
            for consumer in delivery["consumers"]
            if consumer.get("consumer_ref") == normalized_ref
        ),
        None,
    )
    if existing is None:
        delivery["consumers"].append(
            {"consumer_ref": normalized_ref, "failures": normalized_failures}
        )
    else:
        seen = {
            json.dumps(item, sort_keys=True) for item in existing.get("failures", [])
        }
        for item in normalized_failures:
            key = json.dumps(item, sort_keys=True)
            if key not in seen:
                existing.setdefault("failures", []).append(item)
                seen.add(key)
    if normalized_failures and not delivery.get("reason"):
        delivery["reason"] = normalized_failures[0].get("reason")


def _consumer_ref_from_key(consumer_key):
    if consumer_key == "tests":
        return {"kind": "tests"}
    if isinstance(consumer_key, tuple) and len(consumer_key) == 2:
        return {"kind": consumer_key[0], "name": consumer_key[1]}
    if isinstance(consumer_key, dict):
        return dict(consumer_key)
    return {"kind": str(consumer_key)}


def _make_hygiene_runner(module, consumer_source, loop):
    """Delegate missing Lychee / read-only Git calls to Dagger containers."""
    from engineering_ci import hygiene

    _readonly_git = {"ls-files", "diff", "show", "cat-file"}

    def _runner(argv, cwd, data=None):
        argv_list = [str(item) for item in argv]
        tool = argv_list[0] if argv_list else ""
        tool_name = Path(tool).name if tool else ""
        if (
            (tool == "/usr/local/bin/lychee" or tool_name == "lychee")
            and shutil.which(tool) is None
            and hasattr(module, "_run_lychee")
        ):
            outcome = asyncio.run_coroutine_threadsafe(
                module._run_lychee(consumer_source, argv_list[1:]), loop
            ).result()
            return subprocess.CompletedProcess(
                argv_list,
                outcome["exit_code"],
                outcome["stdout"],
                outcome["stderr"],
            )
        if (
            tool_name == "git"
            and len(argv_list) > 1
            and argv_list[1] in _readonly_git
            and shutil.which(tool) is None
            and hasattr(module, "_run_git")
        ):
            outcome = asyncio.run_coroutine_threadsafe(
                module._run_git(consumer_source, argv_list[1:]), loop
            ).result()
            return subprocess.CompletedProcess(
                argv_list,
                outcome["exit_code"],
                outcome["stdout"],
                outcome["stderr"],
            )
        return hygiene._run(argv, cwd, data)

    return _runner


def _tag_command_prerequisites(entries):
    """Label module-local command prerequisites without fabricating Engine cache."""
    for entry in entries or ():
        if isinstance(entry, dict) and entry.get("type") == "command":
            entry.setdefault("execution_context", "dagger_export")
            entry.setdefault("execution_location", "dagger_module_scratch")
            entry.setdefault("result_origin", "module_local_subprocess")
            entry.setdefault("engine_cached", False)


def _host_adapter_plan(value, selected_host_checks):
    import copy

    host_plan = copy.deepcopy(value)
    host_plan.setdefault("selection", {})
    host_plan["selection"] = dict(host_plan["selection"])
    host_plan["selection"]["tests"] = []
    # Narrowed internal host-check adapter drops non-host checks/tests so
    # inherited expected_checks/selection.check_scope no longer describe its set.
    host_plan["selection"].pop("check_scope", None)
    host_plan.pop("expected_checks", None)
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
    loop = asyncio.get_running_loop()
    scratch_root = Path.cwd().resolve()
    closure_records = []
    produced_files = []
    materialized_cache = {}

    with tempfile.TemporaryDirectory(
        dir=scratch_root, prefix=".eci-artifacts-"
    ) as artifact_dir:
        artifact_root = Path(artifact_dir).resolve()
        (artifact_root / "prerequisite_action").mkdir(parents=True, exist_ok=True)
        tasks = {}

        def ensure_materialized(producer_root, rel, kind):
            key = (str(producer_root), rel, kind)
            if key not in materialized_cache:

                async def _materialize():
                    scratch_rel = _scratch_relative(
                        Path(producer_root) / rel, scratch_root
                    )
                    current = dag.current_module()
                    obj = (
                        current.workdir(scratch_rel)
                        if kind == "directory"
                        else current.workdir_file(scratch_rel)
                    )
                    return await obj.sync()

                materialized_cache[key] = asyncio.create_task(_materialize())
            return materialized_cache[key]

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
            data_failures = []
            delivered = []
            seen_paths = {}
            seen_consumer_objects = set()

            def record_data_failure(producer_ref, edge_item, reason_code, extra=None):
                block_reasons.append(reason_code)
                blocked_by.append({"producer_ref": producer_ref, "reason": reason_code})
                failure = {
                    "producer_ref": producer_ref,
                    "output_ref": edge_item.get("output_ref"),
                    "reason": reason_code,
                }
                if extra:
                    failure.update(extra)
                data_failures.append(failure)

            for edge, producer_out in zip(edges, upstream, strict=True):
                if isinstance(producer_out, BaseException):
                    if not isinstance(producer_out, Exception):
                        raise producer_out
                    reason = "pre_execution_prerequisite_not_executed"
                    if edge.get("dependency_kind") == "data":
                        record_data_failure(
                            edge["producer_ref"],
                            edge,
                            reason,
                            {
                                "error_type": type(producer_out).__name__,
                                "diagnostic": str(producer_out),
                            },
                        )
                    else:
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
                    record_data_failure(p_ref, edge, reason)
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
                        materialized_for_edge = []
                        import_error = None
                        pending_seen = {}
                        for obj in closure.get("materialized_objects") or ():
                            rel = obj["path"]
                            kind = obj["kind"]
                            if rel in seen_paths and seen_paths[rel] != str(
                                producer_root
                            ):
                                record_data_failure(
                                    p_ref, edge, "data_artifact_conflict"
                                )
                                continue
                            if any(
                                execution._artifact_paths_overlap(rel, existing)
                                and owner != str(producer_root)
                                for existing, owner in seen_paths.items()
                            ):
                                record_data_failure(
                                    p_ref, edge, "data_artifact_conflict"
                                )
                                continue
                            (outcome,) = await asyncio.gather(
                                ensure_materialized(producer_root, rel, kind),
                                return_exceptions=True,
                            )
                            if isinstance(outcome, BaseException):
                                if not isinstance(outcome, Exception):
                                    raise outcome
                                import_error = outcome
                                break
                            pending_seen[rel] = str(producer_root)
                            materialized_for_edge.append((rel, kind, outcome))
                        if import_error is not None:
                            reason = "dagger_workdir_import_failed"
                            closure["verified"] = False
                            closure["reason"] = reason
                            record_data_failure(
                                p_ref,
                                edge,
                                reason,
                                {
                                    "error_type": type(import_error).__name__,
                                    "diagnostic": str(import_error),
                                },
                            )
                            continue
                        seen_paths.update(pending_seen)
                        for rel, kind, engine_obj in materialized_for_edge:
                            obj_key = (str(producer_root), rel, kind)
                            if obj_key not in seen_consumer_objects:
                                seen_consumer_objects.add(obj_key)
                                delivered.append((producer_root, rel, kind, engine_obj))
                            produced_files.append((p_ref, rel, kind, engine_obj))
                if not ok:
                    if edge.get("dependency_kind") == "data":
                        record_data_failure(p_ref, edge, reason)
                    else:
                        block_reasons.append(reason)
                        blocked_by.append(
                            {"producer_ref": edge["producer_ref"], "reason": reason}
                        )
            if data_failures:
                _record_delivery_failure(
                    report, _consumer_ref_from_key(consumer_key), data_failures
                )
            elif block_reasons and consumer_key == ("execution_report", "artifacts"):
                _record_delivery_failure(
                    report,
                    {"kind": "execution_report", "name": "artifacts"},
                    [
                        {
                            "producer_ref": item.get("producer_ref"),
                            "reason": item.get("reason"),
                        }
                        for item in blocked_by
                    ]
                    or [{"reason": block_reasons[0]}],
                )
            return (block_reasons[0] if block_reasons else None), blocked_by, delivered

        async def compute_node(node_key):
            kind = node_key[0]
            if kind == "prerequisite_action":
                name = node_key[1]
                producer_ref = {"kind": "prerequisite_action", "name": name}
                entry = await asyncio.to_thread(
                    execution.run_prerequisite_action,
                    name,
                    actions.get(name),
                    root=Path(directory),
                    execution_context="dagger_export",
                    artifact_root=execution.producer_artifact_root(
                        artifact_root, producer_ref
                    ),
                )
                _tag_command_prerequisites([entry])
                return entry
            if kind == "check_result":
                name = node_key[1]
                item = selected_by_name[name]
                block_reason, blocked_by, delivered = await resolve_incoming(
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
                generated_refs = execution.generated_input_refs(
                    list(closure_records), {"kind": "check", "name": name}
                )
                dag_objects = [
                    (rel, obj_kind, obj) for _, rel, obj_kind, obj in delivered
                ]
                consumer_source = _overlay_files(source, dag_objects)
                if name in weaver_checks:
                    native, check_files, metadata = await _weaver(
                        module,
                        consumer_source,
                        value,
                        directory,
                        item,
                        weaver_config,
                        report["source_tree_verified"],
                    )
                    if generated_refs and isinstance(native, dict):
                        native["generated_input_refs"] = generated_refs
                    return native, check_files, metadata
                fs_files = [
                    (p_root, rel, obj_kind) for p_root, rel, obj_kind, _ in delivered
                ]
                runner = _make_hygiene_runner(module, consumer_source, loop)
                temporary = None
                consumer_root = Path(directory)
                artifact_paths = ()
                if fs_files:
                    temporary, consumer_root = execution.build_consumer_view(
                        consumer_root, fs_files
                    )
                    artifact_paths = execution.artifact_target_paths(fs_files)
                try:
                    entry = await asyncio.to_thread(
                        execution.run_host_check,
                        host_plan,
                        item,
                        root=consumer_root,
                        runner=runner,
                        execution_context="dagger_export",
                        run_instance=run_instance,
                        artifact_paths=artifact_paths,
                        generated_input_refs=generated_refs,
                    )
                finally:
                    if temporary is not None:
                        temporary.cleanup()
                return entry, None, {}
            if kind == "tests":
                block_reason, blocked_by, delivered = await resolve_incoming("tests")
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
                generated_refs = execution.generated_input_refs(
                    list(closure_records), {"kind": "tests"}
                )
                dag_objects = [
                    (rel, obj_kind, obj) for _, rel, obj_kind, obj in delivered
                ]
                consumer_source = _overlay_files(source, dag_objects)
                payload, test_files = await _tests(
                    module, consumer_source, targets, framework, python_version
                )
                if generated_refs and isinstance(payload, dict):
                    payload.setdefault("tests", {})["generated_input_refs"] = (
                        generated_refs
                    )
                return payload, test_files
            if kind == "report_artifacts":
                await resolve_incoming(("execution_report", "artifacts"))
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
        if ("execution_report", "artifacts") in incoming:
            scheduled.append(
                (
                    ("execution_report", "artifacts"),
                    ensure(("report_artifacts",)),
                )
            )

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
            elif slot == ("execution_report", "artifacts") and isinstance(
                output, BaseException
            ):
                if not isinstance(output, Exception):
                    raise output
                _record_delivery_failure(
                    report,
                    {"kind": "execution_report", "name": "artifacts"},
                    [
                        {
                            "reason": "artifact_delivery_error",
                            "error_type": type(output).__name__,
                            "diagnostic": str(output),
                        }
                    ],
                )

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

            async def _finalize_artifacts():
                return await _bundle_produced(artifacts, produced_files).sync()

            (bundle_outcome,) = await asyncio.gather(
                _finalize_artifacts(), return_exceptions=True
            )
            if isinstance(bundle_outcome, BaseException):
                if not isinstance(bundle_outcome, Exception):
                    raise bundle_outcome
                _record_delivery_failure(
                    report,
                    {"kind": "artifact_bundle"},
                    [
                        {
                            "reason": "dagger_workdir_import_failed",
                            "error_type": type(bundle_outcome).__name__,
                            "diagnostic": str(bundle_outcome),
                        }
                    ],
                )
            else:
                artifacts = bundle_outcome
        if closure_records:
            closure_records.sort(
                key=lambda entry: (
                    entry["producer_ref"].get("kind", ""),
                    entry["producer_ref"].get("name", ""),
                    entry["output_ref"].get("path", ""),
                )
            )
            report["artifact_closure"] = closure_records
            delivery_blocked = (report.get("artifact_delivery") or {}).get(
                "status"
            ) == "blocked"
            report["source_handoff"]["generated_outputs_delivered"] = (
                bool(closure_records)
                and all(entry["verified"] for entry in closure_records)
                and not delivery_blocked
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
            loop = asyncio.get_running_loop()
            host = (
                await asyncio.to_thread(
                    execution.execute,
                    check_plan,
                    root=directory,
                    execution_context="dagger_export",
                    runner=_make_hygiene_runner(module, source, loop),
                )
                if check_plan["check_scope"] or prerequisites
                else {
                    "status": "passed",
                    "exit_code": 0,
                    "checks": [],
                    "prerequisites": [],
                }
            )
            host_prerequisites = host.get("prerequisites") or []
            _tag_command_prerequisites(host_prerequisites)
            host_checks_out = host.get("checks") or []
            if host_checks_out:
                report["checks"].extend(host_checks_out)
            elif host_checks and host.get("status") in {"blocked", "not_executed"}:
                reason = host.get("reason") or "required_checks_not_executed"
                report["checks"].extend(
                    execution._blocked_consumer_entry(
                        item.get("name"),
                        item.get("status"),
                        reason,
                        [],
                    )
                    for item in host_checks
                    if item.get("name")
                )
            report["prerequisites"] = host_prerequisites
            if (
                prerequisites
                and not host_prerequisites
                and host.get("status") in {"blocked", "not_executed"}
            ):
                reason = host.get("reason") or "required_checks_not_executed"
                report["prerequisites"] = [
                    {
                        "name": name,
                        "status": "not_executed",
                        "exit_code": None,
                        "reason": reason,
                    }
                    for name in prerequisites
                ]
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
