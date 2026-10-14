"""Generate, validate and execute independent tool workflows in the primary checkout."""

import argparse
import json
from contextlib import ExitStack
from pathlib import Path

from filelock import FileLock

from .._paths import ROOT
from ..workflow_adapters.checks import catalog, recipe
from ..workflow_core import Runtime, generate, load_generation
from ..workflow_core.documents import WorkflowError, digest, read, write
from ..workflow_core.schemas import SCHEMAS

PROTECTED_RECEIPTS = frozenset(
    {
        "workflow.json",
        "provenance.json",
        "state.json",
        "result.json",
        "checks.json",
        "intent.json",
        "reuse-source.json",
        "approval.json",
        "catalog.json",
        "capability.json",
        "comparison.json",
    }
)


def result_path(path, root):
    """Share output protection without treating a query as an execution operation."""
    from ..checks import report_path

    path = report_path(path, root)
    if path.name in PROTECTED_RECEIPTS:
        raise WorkflowError(
            "Operation JSON must not overwrite a definition or execution receipt"
        )
    return path


def main(argv=None):
    import sys

    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "registry":
        try:
            from vesryn import (
                registry,
            )  # availability check for the newer optional interface
            from vesryn.cli.workflow import main as registry_main
        except ImportError:
            print(
                json.dumps(
                    {
                        "schema_version": 1,
                        "operation": "workflow.registry",
                        "status": "unavailable",
                        "exit_code": 3,
                        "reason": "Install the pinned Vesryn Harness with unified registry support",
                    }
                )
            )
            return 3
        if not callable(registry.catalog):
            return 3
        return registry_main(["--adapter", "unified", "--root", str(ROOT), *argv[1:]])
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("tools")
    commands.add_parser("schemas")
    make = commands.add_parser("generate")
    mode = make.add_mutually_exclusive_group(required=True)
    mode.add_argument("--recipe", type=Path)
    mode.add_argument("--profile", choices=("pr", "ruff", "tests", "version", "lock"))
    make.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Parent of a new unique generation directory",
    )
    make.add_argument(
        "--executor", choices=("auto", "native", "dagger"), default="auto"
    )
    make.add_argument("--coverage", action="store_true")
    make.add_argument("--junit", default="core-junit.xml")
    make.add_argument("--analysis", type=Path)
    make.add_argument("--projection", type=Path)
    make.add_argument("--analysis-result")
    make.add_argument("--candidate")
    make.add_argument("--config")
    check = commands.add_parser("validate")
    check.add_argument("workflow", type=Path)
    run = commands.add_parser("run")
    run.add_argument("workflow", type=Path)
    run.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Parent of a new unique execution directory",
    )
    run.add_argument(
        "--reuse-from",
        type=Path,
        help="Prior execution directory; every tool rechecks its evidence",
    )
    for name in ("status", "resume"):
        action = commands.add_parser(name)
        action.add_argument("execution", type=Path)
    proof = commands.add_parser("evidence")
    proof.add_argument("execution", type=Path)
    proof.add_argument("--limit", type=int, default=20)
    proof.add_argument("--max-bytes", type=int, default=16384)
    proof.add_argument("--after")
    proof.add_argument(
        "--otel-json",
        type=Path,
        help="Export observed spans using the optional trace SDK",
    )
    comparison = commands.add_parser(
        "compare", help="Read-only comparison of two controlled PR check executions"
    )
    comparison.add_argument("baseline_execution", metavar="BASELINE_DIR", type=Path)
    comparison.add_argument("candidate_execution", metavar="CANDIDATE_DIR", type=Path)
    comparison.add_argument(
        "--out", required=True, type=Path, help="New independent comparison report"
    )
    from .workflow_capability import COMMANDS, add_commands
    from .workflow_feedback_query import add_command

    add_commands(commands)
    add_command(commands)
    for action in commands.choices.values():
        action.add_argument(
            "--json",
            type=Path,
            help="Save the operation JSON separately from original tool output",
        )
    options = parser.parse_args(argv)
    if options.command in {"feedback-query", "feedback-summary", "feedback-review"}:
        from .workflow_feedback_query import main as query_main

        return query_main(options, ROOT)
    operation = {
        "schema_version": 1,
        "operation": "workflow." + options.command,
        "exit_code": 3,
    }
    result_destination = None
    try:
        if options.json:
            options.json = result_path(options.json, ROOT)
            source_paths = [
                getattr(options, name, None)
                for name in (
                    "workflow",
                    "recipe",
                    "analysis",
                    "projection",
                    "comparison_report",
                )
            ]
            if options.json in {Path(path).resolve() for path in source_paths if path}:
                raise WorkflowError("Operation JSON must not overwrite its source")
            executions = [
                getattr(options, name, None)
                for name in (
                    "execution",
                    "baseline_execution",
                    "candidate_execution",
                    "capability_baseline",
                    "capability_candidate",
                )
            ]
            if any(
                options.json.is_relative_to(path.resolve())
                for path in executions
                if path
            ):
                raise WorkflowError(
                    "Operation JSON must stay outside the execution evidence directory"
                )
            if options.command == "compare" and options.json == options.out.resolve():
                raise WorkflowError(
                    "Operation JSON must not overwrite the comparison report"
                )
            if options.command in COMMANDS:
                from .workflow_capability import store_path

                if options.json.is_relative_to(store_path(options, ROOT)):
                    raise WorkflowError(
                        "Operation JSON must stay outside the capability store"
                    )
            result_destination = options.json
        tools = catalog(ROOT)
        runtime = Runtime(tools)
        if options.command in COMMANDS:
            from .workflow_capability import execute

            operation = execute(options, tools, runtime, ROOT, operation=operation)
        elif options.command == "tools":
            operation.update(status="available", tools=tools.documents(), exit_code=0)
        elif options.command == "schemas":
            operation.update(status="available", schemas=SCHEMAS, exit_code=0)
        elif options.command == "compare":
            from ..checks import report_path
            from ..workflow_adapters.comparison import compare
            from ..workflow_adapters.evidence import execution_pair

            operation.update(
                baseline=str(options.baseline_execution.resolve()),
                candidate=str(options.candidate_execution.resolve()),
                report_generated=False,
                next_actions=[
                    ["engineering-ci", "workflow", "status", str(path.resolve())]
                    for path in (
                        options.baseline_execution,
                        options.candidate_execution,
                    )
                ],
            )
            output = report_path(options.out, ROOT)
            if not output.is_relative_to(ROOT.resolve()) or any(
                output.is_relative_to(path.resolve())
                for path in (options.baseline_execution, options.candidate_execution)
            ):
                raise WorkflowError(
                    "Comparison report must stay in the checkout outside original executions"
                )
            if output.exists():
                raise WorkflowError("Comparison output exists; choose a new file")
            operation["output"] = str(output)
            left, right = execution_pair(
                options.baseline_execution, options.candidate_execution, tools, ROOT
            )
            result = compare(left, right)
            write(output, result)
            operation.update(
                status=result["status"],
                exit_code=result["exit_code"],
                output=str(output),
                comparison_id=result["comparison_id"],
                report_generated=True,
                optimization_verified=result["optimization_verified"],
                verified_scope=result["verified_scope"],
                check_submissions_avoided=result["check_submissions_avoided"],
                recorded_quality_gate=result["recorded_quality_gate"],
                current_input_validity="not_checked",
                reasons=result["reasons"],
                next_actions=[
                    ["engineering-ci", "workflow", "evidence", str(path.resolve())]
                    for path in (
                        options.baseline_execution,
                        options.candidate_execution,
                    )
                ],
            )
        elif options.command == "generate":
            if options.recipe and (
                options.coverage
                or options.executor != "auto"
                or options.junit != "core-junit.xml"
            ):
                raise WorkflowError(
                    "Recipe owns its parameters; do not combine it with profile overrides"
                )
            source = None
            if any(
                (
                    options.analysis,
                    options.projection,
                    options.analysis_result,
                    options.candidate,
                    options.config,
                )
            ):
                if (
                    not options.candidate
                    or bool(options.analysis) == bool(options.analysis_result)
                    or bool(options.analysis) != bool(options.projection)
                ):
                    raise WorkflowError(
                        "Candidate requires one analysis/projection pair or registered analysis-result"
                    )
                from ..workflow_adapters.candidates import selected_source
                from .mine_inputs import file_evidence, registered_evidence

                with ExitStack() as stack:
                    if options.analysis_result:
                        from ..tracing.catalog import TraceCatalog
                        from ..tracing.config import load_config
                        from ..tracing.store import open_store

                        evidence = registered_evidence(
                            TraceCatalog(
                                stack.enter_context(
                                    open_store(load_config(options.config))
                                )
                            ),
                            options.analysis_result,
                        )
                    else:
                        evidence = file_evidence(options.analysis, options.projection)
                    source = selected_source(evidence, options.candidate)
            selected = (
                read(options.recipe)
                if options.recipe
                else recipe(
                    [options.profile],
                    executor=options.executor,
                    coverage=options.coverage,
                    junit=options.junit,
                )
            )
            target = None
            if source:
                from ..workflow_adapters.candidates import profile_target

                target = profile_target(selected)
            path = generate(
                selected, tools, options.out, source=source, target=target
            ).resolve()
            value, _, _ = load_generation(path, tools)
            operation.update(
                status="generated",
                output=str(path),
                generation_id=value["generation_id"],
                definition_id=value["definition_id"],
                exit_code=0,
                next_actions=[
                    ["engineering-ci", "workflow", "validate", str(path)],
                    [
                        "engineering-ci",
                        "workflow",
                        "run",
                        str(path),
                        "--out",
                        ".test-tools/workflow-runs",
                    ],
                ],
            )
        elif options.command == "validate":
            from ..workflow_adapters.candidates import verify_target

            value, provenance, plan = load_generation(options.workflow, tools)
            target_admission = verify_target(value, provenance)
            operation.update(
                status="valid",
                exit_code=0,
                generation_id=value["generation_id"],
                definition_id=value["definition_id"],
                plan=plan,
                provenance=provenance,
                target_policy=target_admission,
                execution_admission="tools verify actual environment, inputs and resources at invocation",
            )
        elif options.command == "evidence":
            from ..workflow_adapters.evidence import evidence

            operation["execution"] = str(options.execution.resolve())
            operation = evidence(
                options.execution,
                tools,
                ROOT,
                limit=options.limit,
                budget=options.max_bytes,
                after=options.after,
            )
            if options.otel_json and operation["exit_code"] == 0:
                from ..checks import report_path
                from ..tracing.workflow_spans import standardize
                from ..workflow_core.observations import history

                output = report_path(options.otel_json, ROOT)
                if (
                    output.is_relative_to(options.execution.resolve())
                    or output == options.json
                ):
                    raise WorkflowError(
                        "OTel projection must not overwrite execution or operation evidence"
                    )
                with FileLock(
                    options.execution.resolve() / "execution.lock", timeout=0
                ):
                    state = runtime.status(options.execution)
                    observations = history(options.execution.resolve(), state)
                    if operation["scope"]["state_sha256"] != digest(state) or operation[
                        "scope"
                    ]["observations_sha256"] != digest(observations[0]):
                        raise WorkflowError(
                            "Execution changed during evidence export; repeat the query"
                        )
                    write(
                        output, standardize(observations[0], state, *observations[1:])
                    )
        else:
            from ..workflow_adapters.candidates import verify_target

            if options.command == "run":
                value, provenance, _ = load_generation(options.workflow, tools)
                verify_target(value, provenance)
                directory = runtime.create(
                    options.workflow, options.out, reuse_from=options.reuse_from
                )
            else:
                directory = options.execution.resolve()
            operation["execution"] = str(directory)
            if options.command == "resume":
                value, provenance, _ = load_generation(
                    directory / "workflow.json", tools
                )
                verify_target(value, provenance)
            if options.command != "status":
                print(f"Execution checkpoint: {directory}", flush=True)
                print(
                    f'Inspect: engineering-ci workflow status "{directory}"', flush=True
                )
            state = (
                runtime.status(directory)
                if options.command == "status"
                else runtime.resume(directory)
            )
            operation.update(
                status=state["status"],
                exit_code=state["exit_code"],
                result=state,
                current_input_validity="not_checked"
                if options.command == "status"
                else "see_individual_tool_results",
            )
            from ..workflow_adapters.capabilities import call_reference

            operation["capability_call"] = call_reference(directory, state)
            if options.command != "status":
                operation["observations"] = {
                    "directory": str(directory / "observations"),
                    "write_errors": runtime.observation_errors,
                    "next_action": [
                        "engineering-ci",
                        "workflow",
                        "evidence",
                        str(directory),
                    ],
                }
                if (directory / "capability.json").exists():
                    from .workflow_capability import saved_feedback

                    operation["feedback"] = saved_feedback(directory, tools, ROOT)
            operation["next_actions"] = [
                ["engineering-ci", "workflow", "status", str(directory)]
            ]
            if state["phase"] != "UNKNOWN" and state["status"] != "blocked":
                operation["next_actions"].append(
                    ["engineering-ci", "workflow", "resume", str(directory)]
                )
            else:
                operation["decision_required"] = state.get(
                    "decision_required",
                    "Verify the uncertain domain operation; automatic re-invocation is prohibited",
                )
    except Exception as error:  # noqa: BLE001 -- host SDK failures can contain private responses
        from ..tracing.store import ArchiveError

        # Source SDK failures and raw subprocess responses can include private information.
        operation.update(
            status="invalid_or_unavailable",
            exit_code=operation["exit_code"] or 3,
            reason=str(error)
            if isinstance(error, (WorkflowError, ArchiveError))
            else "Cannot validate, persist or invoke the requested workflow",
        )
        if (
            isinstance(error, ImportError)
            and options.command == "evidence"
            and options.otel_json
        ):
            operation["reason"] = (
                "OTel projection unavailable; install the locked trace extra"
            )
        if "execution" in operation:
            operation["next_actions"] = [
                ["engineering-ci", "workflow", "status", operation["execution"]]
            ]
    if result_destination:
        try:
            write(result_destination, operation, replace=True)
        except OSError:
            operation.update(
                reason="Operation JSON could not be saved",
                exit_code=operation["exit_code"] or 3,
            )
    if options.command in {"evidence", "capabilities"}:
        from ..tracing.retrieval import encode

        print(encode(operation).decode("utf-8"), end="")
    else:
        print(json.dumps(operation, indent=2, sort_keys=True))
    return operation["exit_code"]
