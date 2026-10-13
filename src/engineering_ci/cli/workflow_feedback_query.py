"""Read-only archived feedback lookup; independent of capability invocation and local receipts."""

from pathlib import Path

from ..tracing.feedback_query import SELECTORS, FeedbackQuery, query_feedback
from ..tracing.retrieval import encode
from ..tracing.store import ArchiveError
from ..workflow_core.documents import WorkflowError, read, write


def add_command(commands):
    action = commands.add_parser(
        "feedback-query",
        help="Read verified PostgreSQL feedback snapshots without execution or migration",
        description="Read-only feedback snapshots, with bounded source scanning, filters and output budget.",
    )
    for key in SELECTORS:
        action.add_argument(
            "--" + key.replace("_", "-"),
            help={
                "version": "Capability version, not the engineering-ci release version",
                "result_status": "Original recorded check result status, not query success",
                "completeness": "Original observation completeness",
            }.get(key),
        )
    action.add_argument("--limit", type=int, default=20)
    action.add_argument("--max-bytes", type=int, default=16384)
    action.add_argument(
        "--after", help="page.next_after from the same source and filters"
    )
    action.add_argument("--config", help="Existing tracing TOML; uses VESRYN_DATABASE")


def main(options, root):
    """Validate before connecting and return query facts, preserving original failure outcomes."""
    from ..tracing.config import load_config
    from ..tracing.store import open_store
    from .workflow import result_path

    result = {
        "schema_version": 1,
        "operation": "workflow.feedback-query",
        "exit_code": 3,
    }
    destination = None
    try:
        request = FeedbackQuery(
            **{
                key: getattr(options, key)
                for key in (
                    *SELECTORS,
                    "limit",
                    "max_bytes",
                    "after",
                )
            }
        )
        if options.json:
            path = result_path(options.json, root)
            if not path.is_relative_to(Path(root).resolve()):
                raise WorkflowError("Query JSON must stay in the primary checkout")
            if any(
                path.is_relative_to(Path(root).resolve() / name)
                for name in (".git", ".venv")
            ):
                raise WorkflowError(
                    "Query JSON must not overwrite repository or runtime files"
                )
            if (
                path.exists()
                and read(path).get("operation") != "workflow.feedback-query"
            ):
                raise WorkflowError(
                    "Query JSON must not overwrite original evidence or another operation"
                )
            destination = path
        request.validate()
        with open_store(load_config(options.config)) as store:
            result = query_feedback(store, request)
    except Exception as error:  # noqa: BLE001 -- raw driver responses may contain credentials
        result.update(
            status=error.status
            if isinstance(error, ArchiveError)
            else "invalid_or_unavailable",
            reason=str(error)
            if isinstance(error, (ArchiveError, WorkflowError))
            else "Cannot read supported archived feedback",
            exit_code=3,
            sqlstate=error.sqlstate if isinstance(error, ArchiveError) else None,
        )
    if destination:
        try:
            write(destination, result, replace=True)
        except (WorkflowError, OSError):
            result = {
                "schema_version": 1,
                "operation": "workflow.feedback-query",
                "status": "output_unavailable",
                "exit_code": 3,
                "reason": "Query JSON could not be saved; original archived feedback is unchanged",
            }
    print(encode(result).decode("utf-8"), end="")
    return result["exit_code"]
