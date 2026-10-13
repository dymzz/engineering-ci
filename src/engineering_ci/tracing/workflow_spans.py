"""Official SDK spans from observed invocation boundaries, without GenAI inference."""

import hashlib
import json
from importlib.metadata import version


def standardize(events, state, completeness, diagnostics):
    """Project actual wrapper timing; missing ends and tool/cache costs stay unknown."""
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.util.instrumentation import InstrumentationScope
    from opentelemetry.trace import (
        SpanContext,
        SpanKind,
        Status,
        StatusCode,
        TraceFlags,
    )

    pending, spans, missing = {}, [], 0
    for event in events:
        if event["event"] not in {"invoking", "returned"}:
            continue
        key = (event["session_id"], event["step_id"], event["attempt"])
        if event["event"] == "invoking":
            pending[key] = event
            continue
        start = pending.pop(key, None)
        if (
            start is None
            or not 0 <= start["observed_ns"] <= event["observed_ns"] < 2**63
        ):
            missing += 1
            continue
        result = event["result"]
        attrs = {
            "engineering_ci.workflow.execution_id": state["execution_id"],
            "engineering_ci.workflow.generation_id": state["generation_id"],
            "engineering_ci.workflow.definition_id": state["definition_id"],
            "engineering_ci.workflow.step_id": event["step_id"],
            "engineering_ci.workflow.tool_id": event["tool_id"],
            "engineering_ci.workflow.contract_sha256": event["contract_sha256"],
            "engineering_ci.workflow.attempt": event["attempt"],
            "engineering_ci.operation.outcome": result["status"],
            "engineering_ci.operation.evidence_reused": result["reused"],
            "engineering_ci.operation.result_sha256": event["result_sha256"],
        }
        if result["exit_code"] is not None:
            attrs["process.exit.code"] = result["exit_code"]
        if "input_sha256" in event:
            attrs["engineering_ci.operation.input_sha256"] = event["input_sha256"]
        span = ReadableSpan(
            name="workflow.tool.invoke " + event["tool_id"],
            context=SpanContext(
                int.from_bytes(
                    hashlib.sha256(state["execution_id"].encode()).digest()[:16], "big"
                )
                or 1,
                int.from_bytes(
                    hashlib.sha256(":".join(map(str, key)).encode()).digest()[:8], "big"
                )
                or 1,
                False,
                TraceFlags(TraceFlags.SAMPLED),
            ),
            kind=SpanKind.INTERNAL,
            resource=Resource({"service.name": "engineering-ci.workflow"}),
            start_time=start["observed_ns"],
            end_time=event["observed_ns"],
            attributes=attrs,
            status=Status(
                StatusCode.ERROR if result["status"] != "passed" else StatusCode.UNSET
            ),
            instrumentation_scope=InstrumentationScope("engineering_ci.workflow", "1"),
        )
        spans.append(json.loads(span.to_json()))
    return {
        "schema_version": 1,
        "kind": "workflow-otel-projection",
        "execution_id": state["execution_id"],
        "generation_id": state["generation_id"],
        "definition_id": state["definition_id"],
        "spans": spans,
        "observation_completeness": completeness,
        "operations_without_valid_end": missing + len(pending),
        "diagnostics": diagnostics,
        "time_semantics": "observed adapter invocation boundaries; batched spans may overlap",
        "dependencies": {"opentelemetry-sdk": version("opentelemetry-sdk")},
        "cost": None,
        "dagger_cache_hit": None,
        "llm_used": False,
    }
