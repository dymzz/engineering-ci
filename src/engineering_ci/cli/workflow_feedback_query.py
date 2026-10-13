"""Read-only archived feedback lookup; independent of capability invocation and local receipts."""

from pathlib import Path

from ..tracing.feedback_query import SELECTORS, FeedbackQuery, query_feedback
from ..tracing.feedback_summary import (
    FeedbackSummary,
    query_summary,
    read_selection,
    summary_result,
)
from ..tracing.retrieval import encode
from ..tracing.store import ArchiveError
from ..workflow_core.documents import WorkflowError, digest, read, write


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
    summary = commands.add_parser(
        "feedback-summary",
        help="Summarize explicitly selected archived feedback without double counting attempts",
    )
    summary.add_argument(
        "--feedback-id",
        required=True,
        action="append",
        dest="feedback_ids",
        help="An actual feedback ID; repeat for 1–32 snapshots",
    )
    summary.add_argument("--max-bytes", type=int, default=16384)
    summary.add_argument("--config", help="Existing tracing TOML; uses VESRYN_DATABASE")
    review = commands.add_parser(
        "feedback-review",
        help="Read verified summary, original comparison and explicit call receipts for human review",
    )
    review.add_argument(
        "--summary",
        required=True,
        type=Path,
        help="Successful P3-2 summary JSON; verified against the same archive",
    )
    review.add_argument(
        "--store",
        required=True,
        type=Path,
        help="Existing capability store; never created, activated or modified",
    )
    review.add_argument(
        "--execution",
        action="append",
        default=[],
        type=Path,
        help="Existing call directory; repeat explicitly, no directory discovery",
    )
    review.add_argument("--config", help="Same tracing TOML used by the input summary")
    review.add_argument("--max-bytes", type=int, default=16384)


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
    operation = "workflow." + options.command
    result["operation"] = operation
    try:
        source = None
        if options.command == "feedback-review":
            from ..tracing.retrieval import Query, page_bounds
            from ..workflow_adapters.capabilities import inside
            from ..workflow_adapters.feedback_review import review_request

            source_path = inside(options.summary, root)
            source = read(source_path)
            request = review_request(source)
            page_bounds(Query("evidence", max_bytes=options.max_bytes))
            store_path = inside(options.store, root)
            if len(options.execution) > 32:
                raise WorkflowError(
                    "Review accepts at most 32 explicit execution directories"
                )
            execution_paths = [inside(path, root) for path in options.execution]
        else:
            request = (
                FeedbackSummary(tuple(options.feedback_ids), options.max_bytes)
                if options.command == "feedback-summary"
                else FeedbackQuery(
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
            if path.exists() and read(path).get("operation") != operation:
                raise WorkflowError(
                    "Query JSON must not overwrite original evidence or another operation"
                )
            if source is not None and (
                path == source_path
                or any(
                    path.is_relative_to(folder)
                    for folder in [store_path, *execution_paths]
                )
            ):
                raise WorkflowError(
                    "Review JSON must not overwrite its summary or original evidence"
                )
            destination = path
        request.validate()
        with open_store(load_config(options.config)) as store:
            if source is not None:
                from ..workflow_adapters.checks import catalog
                from ..workflow_adapters.feedback_review import review_feedback

                values, scope = read_selection(store, request)
                expected = summary_result(values, scope, request.max_bytes)
                if digest(expected) != digest(source):
                    raise ArchiveError(
                        "summary_changed_or_unavailable",
                        "Saved summary does not match verified archive input; query the same explicit selection again",
                    )
                result = review_feedback(
                    source,
                    values,
                    store_path,
                    execution_paths,
                    catalog(root),
                    root,
                    max_bytes=options.max_bytes,
                    destination=destination,
                )
                if digest(read(source_path)) != digest(source):
                    raise ArchiveError(
                        "summary_changed_during_review",
                        "Summary input changed during review; no review decision is current",
                    )
            else:
                result = (
                    query_summary(store, request)
                    if options.command == "feedback-summary"
                    else query_feedback(store, request)
                )
    except Exception as error:  # noqa: BLE001 -- raw driver responses may contain credentials
        if options.command == "feedback-review":
            from ..workflow_adapters.feedback_review import ProtectedReviewOutput

            if isinstance(error, ProtectedReviewOutput):
                destination = None
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
                "operation": operation,
                "status": "output_unavailable",
                "exit_code": 3,
                "reason": "Query JSON could not be saved; original archived feedback is unchanged",
            }
    print(encode(result).decode("utf-8"), end="")
    return result["exit_code"]
