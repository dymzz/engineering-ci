"""Actual optional OTel SDK projection; no exporter, database or Workflow dispatch."""

import json

from test_generated_workflows import Arithmetic, execution

from engineering_ci.tracing.workflow_spans import standardize
from engineering_ci.workflow_core.observations import history


def test_real_sdk_spans_use_exact_tool_identity_without_genai_or_content(tmp_path):
    runtime, directory, _ = execution(tmp_path, Arithmetic(fail=True))
    state = runtime.resume(directory)
    events, completeness, diagnostics = history(directory, state)
    projection = standardize(events, state, completeness, diagnostics)
    assert projection["dependencies"]["opentelemetry-sdk"] == "1.45.1"
    span = projection["spans"][0]
    assert span["name"] == "workflow.tool.invoke math.increment"
    assert span["status"]["status_code"] == "ERROR"
    assert span["attributes"]["process.exit.code"] == 7
    assert not any(key.startswith("gen_ai.") for key in span["attributes"])
    assert "inputs" not in json.dumps(span) and "outputs" not in json.dumps(span)
    assert projection["cost"] is None and projection["dagger_cache_hit"] is None


def test_missing_end_and_clock_regression_do_not_create_fabricated_spans(tmp_path):
    runtime, directory, _ = execution(tmp_path)
    state = runtime.resume(directory)
    events, complete, diagnostics = history(directory, state)
    received = next(event for event in events if event["event"] == "returned")
    received["observed_ns"] = 0
    result = standardize(events, state, complete, diagnostics)
    assert len(result["spans"]) == 1 and result["operations_without_valid_end"] == 1
    events = [event for event in events if event["event"] != "returned"]
    result = standardize(events, state, "incomplete", diagnostics)
    assert not result["spans"] and result["operations_without_valid_end"] == 2
