"""Small protocol fixtures for host adapter tests; portable behavior tests live in Vesryn."""

from vesryn.mining.analysis import ANALYSIS_VERSION, analysis_identity
from vesryn.mining.comparison import AnalysisEvidence
from vesryn.tracing.projection import (
    ACTIVITIES,
    PROJECTION_VERSION,
    projection_identity,
)
from vesryn.tracing.results import canonical, digest
from vesryn.workflow_core import Catalog, Runtime, ToolResult, ToolSpec, generate
from vesryn.workflow_core.schemas import object_schema

NUMBER = object_schema({"value": {"type": "integer"}})


class Arithmetic:
    def __init__(self, *, fail=False, interrupt=False, malformed=False):
        self.calls, self.fail, self.interrupt, self.malformed = (
            [],
            fail,
            interrupt,
            malformed,
        )

    def invoke(self, calls, context):
        assert context.execution_id == context.run_dir.name
        self.calls.extend(
            (call.step_id, call.inputs, context.execution_id) for call in calls
        )
        if self.interrupt:
            raise KeyboardInterrupt
        return {
            call.step_id: ToolResult(
                "failed" if self.fail else "passed",
                7 if self.fail else 0,
                outputs={}
                if self.malformed
                else {
                    "value": call.inputs["value"]
                    + (1 if call.tool_id == "math.increment" else 10)
                },
                executed=True,
                complete=True,
                current=True,
                report_generated=True,
            )
            for call in calls
        }


def tools(adapter=None, *, effect="check"):
    adapter = adapter or Arithmetic()
    result = Catalog()
    for name in ("math.increment", "math.add-ten"):
        result.register(
            ToolSpec(
                name,
                NUMBER,
                NUMBER,
                effect=effect,
                recovery="manual" if effect == "write" else "restartable",
            ),
            adapter,
        )
    return result, adapter


def recipe():
    return {
        "schema_version": 1,
        "kind": "workflow-recipe",
        "steps": [
            {"id": "first", "tool": "math.increment", "inputs": {"value": 2}},
            {
                "id": "second",
                "tool": "math.add-ten",
                "inputs": {"value": {"$from": "first", "pointer": "/value"}},
            },
        ],
    }


def execution(tmp_path, adapter=None, **kwargs):
    registered, adapter = tools(adapter, **kwargs)
    path = generate(recipe(), registered, tmp_path / "generated")
    runtime = Runtime(registered)
    return runtime, runtime.create(path, tmp_path / "runs"), adapter


def projection():
    value = {
        "schema_version": 1,
        "snapshot_id": "a" * 64,
        "projection_version": PROJECTION_VERSION,
        "mapping": ACTIVITIES,
        "dependencies": {
            "opentelemetry-sdk": "1.45.1",
            "opentelemetry-semantic-conventions": "0.66b1",
        },
        "format": "opentelemetry-sdk-span-json",
        "completeness": "unknown",
        "llm_used": False,
        "operations": [],
        "turns": {},
        "spans": [],
        "diagnostics": {"invalid_records": 0},
    }
    value["projection_id"] = digest(canonical(projection_identity(value)).rstrip(b"\n"))
    return value


def analysis(parent, *, legacy=False):
    value = {
        "schema_version": 1,
        "snapshot_id": parent["snapshot_id"],
        "projection_id": parent["projection_id"],
        "input_sha256": digest(canonical(parent)),
        "analysis_version": "observed-turn-variants/1" if legacy else ANALYSIS_VERSION,
        "dependencies": {"pm4py": "2.7.23.9", "pandas": "3.0.6", "numpy": "2.5.4"}
        if legacy
        else {"r4pm": "0.6.2"},
        "case_scope": "turn",
        "allow_incomplete": False,
        "status": "unavailable",
        "completeness": "unknown",
        "llm_used": False,
        "selected_cases": [],
        "excluded_cases": [],
        "variants": [],
        "dfg": [],
    }
    if not legacy:
        value["engine"] = "rust4pm"
    value["analysis_id"] = digest(canonical(analysis_identity(value)).rstrip(b"\n"))
    return value


def evidence(*, legacy=False):
    """Three admitted turns and two excluded turns with explicit expected discovery."""
    parent = projection()
    parent["turns"] = {
        turn: {
            "started": True,
            "terminal": "cancelled" if turn == "t3" else "completed",
        }
        for turn in ("t1", "t2", "t3", "open", "overlap")
    }
    parent["turns"]["open"]["terminal"] = None
    for turn, last in (
        ("t1", "file_change"),
        ("t2", "mcp_tool_call"),
        ("t3", "file_change"),
    ):
        for i, activity in enumerate(("command_execution", "command_execution", last)):
            parent["operations"].append(
                {
                    "turn_id": turn,
                    "event_id": str(len(parent["operations"]) + 1),
                    "activity": activity,
                    "start_ns": i * 100,
                    "end_ns": i * 100 + 50,
                    "outcome": ("unknown" if turn == "t3" else "failed")
                    if i == 0
                    else "passed",
                }
            )
    parent["operations"] += [
        {
            "turn_id": "open",
            "event_id": "10",
            "activity": "command_execution",
            "start_ns": 0,
            "end_ns": 50,
            "outcome": "failed",
        },
        *[
            {
                "turn_id": "overlap",
                "event_id": str(i),
                "activity": "command_execution",
                "start_ns": 0,
                "end_ns": 50,
                "outcome": "unknown",
            }
            for i in (11, 12)
        ],
    ]
    value = analysis(parent, legacy=legacy)
    value.update(
        status="passed",
        selected_cases=[
            {
                "turn_id": turn,
                "boundary": parent["turns"][turn]["terminal"],
                "event_ids": [str(i) for i in range(start, start + 3)],
            }
            for turn, start in (("t1", 1), ("t2", 4), ("t3", 7))
        ],
        excluded_cases=[
            {
                "turn_id": "open",
                "reason": "open_or_missing_turn_boundary",
                "operations": 1,
            },
            {
                "turn_id": "overlap",
                "reason": "overlap_or_timestamp_tie",
                "operations": 2,
            },
        ],
        variants=[
            {
                "activities": ["command_execution", "command_execution", "file_change"],
                "cases": 2,
            },
            {
                "activities": [
                    "command_execution",
                    "command_execution",
                    "mcp_tool_call",
                ],
                "cases": 1,
            },
        ],
        dfg=[
            {"from": "command_execution", "to": "command_execution", "count": 3},
            {"from": "command_execution", "to": "file_change", "count": 2},
            {"from": "command_execution", "to": "mcp_tool_call", "count": 1},
        ],
        start_activities={"command_execution": 3},
        end_activities={"file_change": 2, "mcp_tool_call": 1},
        activity_counts={"command_execution": 9, "file_change": 2, "mcp_tool_call": 1},
        outcome_counts={"failed": 3, "passed": 6, "unknown": 3},
    )
    return AnalysisEvidence(value, parent, "file", True)


def refreshed(value):
    """Keep test input identity correct when changing the evidence payload."""
    value.analysis["input_sha256"] = digest(canonical(value.projection))
    value.analysis["analysis_id"] = digest(
        canonical(analysis_identity(value.analysis)).rstrip(b"\n")
    )
    return value
