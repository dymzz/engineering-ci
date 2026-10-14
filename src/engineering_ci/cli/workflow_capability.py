"""Capability approval, explicit selection and feedback over the existing workflow CLI."""

from pathlib import Path

from ..workflow_adapters.capabilities import (
    admission,
    approve,
    create_call,
    discover,
    inside,
)
from ..workflow_core.capabilities import CapabilityStore
from ..workflow_core.documents import WorkflowError, read

COMMANDS = {
    "approve",
    "activate",
    "deactivate",
    "capabilities",
    "select",
    "call",
    "feedback",
}


def add_commands(commands):
    approval = commands.add_parser(
        "approve",
        help="Explicitly approve a verified capability version; does not enable it",
    )
    approval.add_argument("comparison_report", type=Path)
    approval.add_argument("capability_baseline", metavar="BASELINE_DIR", type=Path)
    approval.add_argument("capability_candidate", metavar="CANDIDATE_DIR", type=Path)
    for name, help_text in {
        "activate": "Enable one explicitly reviewed capability version",
        "deactivate": "Disable future selection; preserve existing calls and receipts",
        "capabilities": "Discover bounded metadata from the explicit capability index",
        "select": "Read-only admission of the explicitly enabled version",
        "call": "Freeze selected version and execute its original registered checks",
        "feedback": "Save actual-call facts or explicitly archive them to PostgreSQL",
    }.items():
        commands.add_parser(name, help=help_text, description=help_text)
    for name in COMMANDS:
        action = commands.choices[name]
        if name != "feedback":
            action.add_argument(
                "--store", type=Path, default=Path(".test-tools/workflow-capabilities")
            )
        if name in {"approve", "activate", "deactivate"}:
            action.add_argument("--name", required=True)
        elif name in {"select", "call", "capabilities"}:
            action.add_argument("--name", required=False)
        if name in {"approve", "activate", "select", "call"}:
            action.add_argument(
                "--version",
                required=name in {"approve", "activate"},
                help="Independent capability version, e.g. 1.0.0; not the project release version",
            )
    listed = commands.choices["capabilities"]
    listed.add_argument("--limit", type=int, default=20)
    listed.add_argument("--max-bytes", type=int, default=16384)
    listed.add_argument("--after")
    commands.choices["select"].add_argument(
        "--tool", help="Direct Registry tool ID to select and bind"
    )
    commands.choices["select"].add_argument(
        "--provider",
        default="engineering-ci-dagger",
        help="Domain provider offering the selected tool",
    )
    commands.choices["select"].add_argument(
        "--out",
        type=Path,
        default=Path(".test-tools/workflow-bindings"),
        help="Directory to save the selection binding and generated workflow",
    )
    commands.choices["call"].add_argument("--out", required=True, type=Path)
    commands.choices["call"].add_argument(
        "--binding", type=Path, help="Explicit selection binding to execute"
    )
    feedback = commands.choices["feedback"]
    feedback.add_argument("execution", type=Path)
    feedback.add_argument(
        "--archive",
        action="store_true",
        help="Explicitly write the safe feedback to PostgreSQL",
    )
    feedback.add_argument("--config")


def store_path(options, root):
    """Feedback derives its store from the frozen call, with no ambiguous override."""
    if options.command == "feedback":
        from ..workflow_core.capabilities import CALL
        from ..workflow_core.schemas import validate

        binding = read(inside(options.execution, root) / "capability.json")
        validate(binding, CALL, label="capability call")
        return inside(binding["store"], root)
    return inside(options.store, root)


def execute(options, catalog, runtime, root, *, operation=None):
    """Return action facts; keep original execution exit status independent of feedback."""
    store = CapabilityStore(store_path(options, root))
    if options.json and options.json.is_relative_to(store.directory):
        raise WorkflowError("Operation JSON must stay outside the capability store")
    operation = {} if operation is None else operation
    operation.update(
        {
            "schema_version": 1,
            "operation": "workflow." + options.command,
            "store": str(store.directory),
            "exit_code": 0,
        }
    )
    next_action = lambda command, *args: [
        "engineering-ci",
        "workflow",
        command,
        *args,
        "--store",
        str(store.directory),
    ]
    if options.command == "approve":
        reference = approve(
            store,
            options.comparison_report,
            options.capability_baseline,
            options.capability_candidate,
            options.name,
            options.version,
            catalog,
            root,
        )
        operation.update(
            status="approved",
            capability=reference,
            enabled=False,
            next_actions=[
                next_action(
                    "activate", "--name", options.name, "--version", options.version
                )
            ],
        )
    elif options.command == "activate":
        value, revision = store.activate(
            options.name,
            options.version,
            catalog,
            lambda value, path: admission(value, path, catalog, root),
        )
        operation.update(
            status="enabled",
            capability={key: value[key] for key in ("name", "version", "approval_id")},
            catalog_revision=revision,
            next_actions=[
                next_action(
                    "select", "--name", options.name, "--version", options.version
                )
            ],
        )
    elif options.command == "deactivate":
        operation.update(
            status="disabled", catalog_revision=store.deactivate(options.name)
        )
    elif options.command == "capabilities":
        return discover(
            store,
            catalog,
            root,
            name=options.name,
            limit=options.limit,
            budget=options.max_bytes,
            after=options.after,
        )
    elif options.command == "select":
        if getattr(options, "tool", None):
            from ..workflow_adapters.binding import create_binding

            binding, binding_file = create_binding(
                catalog,
                options.tool,
                provider=getattr(options, "provider", "engineering-ci-dagger"),
                out_dir=options.out,
                root=root,
            )
            operation.update(
                status="selected",
                binding=binding,
                binding_file=str(binding_file.resolve()),
                tool_id=binding["tool_id"],
                provider=binding["provider"],
                version=binding["version"],
                contract_sha256=binding["contract_sha256"],
                contract_summary=binding["contract_summary"],
                scope=binding["scope"],
                current_input_validity="not_checked",
                next_actions=[
                    [
                        "engineering-ci",
                        "workflow",
                        "call",
                        "--binding",
                        str(binding_file.resolve()),
                        "--out",
                        ".test-tools/workflow-runs",
                    ]
                ],
            )
            return operation
        if not getattr(options, "name", None):
            raise WorkflowError("Specify either --tool or --name for workflow select")
        with store.locked():
            value, path, revision = store.select(options.name, options.version, catalog)
            admission(value, path, catalog, root)
        operation.update(
            status="selected",
            capability={key: value[key] for key in ("name", "version", "approval_id")},
            definition_id=value["definition_id"],
            catalog_revision=revision,
            current_input_validity="not_checked",
            next_actions=[
                next_action(
                    "call",
                    "--name",
                    value["name"],
                    "--version",
                    value["version"],
                    "--out",
                    ".test-tools/workflow-runs",
                )
            ],
        )
    elif options.command == "call":
        if getattr(options, "binding", None):
            from ..workflow_adapters.binding import call_binding

            binding_doc = read(options.binding)
            called = call_binding(binding_doc, catalog, options.out, root=root)
            directory = Path(called["execution"])
            operation.update(
                execution=str(directory),
                binding=called["binding"],
                status=called["status"],
                exit_code=called["exit_code"],
                result=called["state"],
                step_results=called["step_results"],
                next_actions=[
                    ["engineering-ci", "workflow", "status", str(directory)],
                    ["engineering-ci", "workflow", "resume", str(directory)],
                    ["engineering-ci", "workflow", "evidence", str(directory)],
                ],
            )
            print(f"Execution checkpoint: {directory}", flush=True)
            print(f'Inspect: engineering-ci workflow status "{directory}"', flush=True)
            return operation
        if not getattr(options, "name", None):
            raise WorkflowError("Specify either --binding or --name for workflow call")
        directory, binding = create_call(
            store, options.name, options.version, options.out, catalog, root
        )
        operation.update(
            execution=str(directory),
            capability=binding,
            next_actions=[
                ["engineering-ci", "workflow", "status", str(directory)],
                ["engineering-ci", "workflow", "resume", str(directory)],
            ],
        )
        print(f"Execution checkpoint: {directory}", flush=True)
        print(f'Inspect: engineering-ci workflow status "{directory}"', flush=True)
        state = runtime.resume(directory)
        operation.update(
            status=state["status"],
            exit_code=state["exit_code"],
            result=state,
            current_input_validity="see_individual_tool_results",
        )
        if state["phase"] == "UNKNOWN" or state["status"] == "blocked":
            operation["next_actions"] = operation["next_actions"][:1]
            operation["decision_required"] = state.get(
                "decision_required",
                "Verify the uncertain domain operation before continuing",
            )
        operation["feedback"] = saved_feedback(directory, catalog, root)
    elif options.command == "feedback":
        from ..workflow_adapters.feedback import archive, prepare

        value, path = prepare(options.execution, catalog, root)
        operation.update(
            status="saved",
            feedback_id=value["feedback_id"],
            output=str(path),
            execution=str(options.execution.resolve()),
            execution_exit_code=value["recorded_result"]["exit_code"],
            next_actions=[
                [
                    "engineering-ci",
                    "workflow",
                    "feedback",
                    str(options.execution.resolve()),
                    "--archive",
                ]
            ],
        )
        if options.archive:
            try:
                operation["archive"] = archive(value, options.config)
                operation["status"] = "registered"
            except Exception as error:  # noqa: BLE001 -- never expose connection parameters
                from ..tracing.store import ArchiveError

                operation.update(
                    status="archive_unavailable",
                    exit_code=3,
                    reason="Feedback is saved locally; PostgreSQL archive failed. Check trace status and schema, then repeat feedback --archive",
                    archive={
                        "status": error.status
                        if isinstance(error, ArchiveError)
                        else "unavailable"
                    },
                )
        if options.config:
            operation["next_actions"][0].extend(["--config", options.config])
    return operation


def saved_feedback(directory, catalog, root):
    """A feedback failure is reported separately from the original tool exit code."""
    from ..workflow_adapters.feedback import prepare

    try:
        value, path = prepare(directory, catalog, root)
        return {
            "status": "saved",
            "feedback_id": value["feedback_id"],
            "output": str(path),
        }
    except Exception:  # noqa: BLE001 -- private SDK diagnostics remain private
        return {
            "status": "unavailable",
            "next_action": ["engineering-ci", "workflow", "feedback", str(directory)],
        }
