"""Read a preceding check receipt through the original freshness inspector."""

from uuid import uuid4

from .. import checks
from ..workflow_core import ToolResult, ToolSpec
from ..workflow_core.documents import WorkflowError, write
from ..workflow_core.schemas import object_schema
from .checks import RESOURCE, ChecksAdapter

SPEC = ToolSpec(
    "engineering-ci.check-inspect",
    object_schema({"report": RESOURCE}),
    object_schema(
        {"inspection": RESOURCE, "passed_for_current_input": {"type": "boolean"}}
    ),
    effect="read",
    environments=("host", "dagger"),
)


class InspectionAdapter:
    def __init__(self, root, *, inspector=None):
        self.resources = ChecksAdapter(root)
        self.inspector = inspector or checks.inspect

    def invoke(self, calls, context):
        results = {}
        for call in calls:
            resource = call.inputs["report"]
            path = self.resources.root / resource["path"]
            if self.resources.resource(path) != resource:
                raise WorkflowError("Predecessor check report was changed or lost")
            inspection, code = self.inspector(path, root=self.resources.root)
            destination = (
                context.run_dir
                / "tool-results"
                / call.step_id
                / f"inspection-{uuid4().hex}.json"
            )
            destination.resolve().relative_to(self.resources.root)
            write(destination, inspection)
            results[call.step_id] = ToolResult(
                "passed" if code == 0 else "failed",
                code,
                outputs={
                    "inspection": self.resources.resource(destination),
                    "passed_for_current_input": code == 0,
                },
                executed=True,
                complete=True,
                current=True,
                report_generated=True,
            )
        return results
