"""An engine-neutral step/attempt Event Log from already verified workflow evidence."""

import json
from collections import defaultdict

from ..mining.analysis import cases_xes
from ..workflow_core.documents import WorkflowError, digest

EVENT_LOG_VERSION = "workflow-step-attempts/1"


def event_log(view, *, allow_incomplete=False):
    """Keep attempts ordered within a step; never linearize independent steps."""
    metadata = view["metadata"]
    completeness = metadata["observation_completeness"]
    if completeness == "incomplete" and not allow_incomplete:
        raise WorkflowError(
            "Incomplete observations require explicit --allow-incomplete"
        )
    selected, excluded, groups = [], [], defaultdict(list)
    for row in view["items"]:
        groups[row["step_id"]].append(row)
    for step in sorted(view["dependencies"]):
        members, reason = [], None
        for row in sorted(groups[step], key=lambda row: row["attempt"]):
            if row["observation_status"] != "complete":
                reason = "missing_attempt_boundary"
            elif not (
                type(row["started_ns"]) is int
                and type(row["ended_ns"]) is int
                and 0 <= row["started_ns"] <= row["ended_ns"] < 2**63
            ):
                reason = "missing_or_invalid_time"
            elif row["registered_check"] and row["check"]["status"] != "verified":
                reason = "original_check_evidence_unavailable"
            elif members and row["started_ns"] < members[-1]["end_ns"]:
                reason = "non_monotonic_adapter_clock"
            if reason:
                break
            attrs = {
                "lifecycle:transition": "complete",
                "engineering_ci:step_id": step,
                "engineering_ci:tool_id": row["tool_id"],
                "engineering_ci:contract_version": row["contract_version"],
                "engineering_ci:contract_sha256": row["contract_sha256"],
                "engineering_ci:attempt": row["attempt"],
                "engineering_ci:session_id": row["session_id"],
                "engineering_ci:input_sha256": row["input_sha256"],
                "engineering_ci:result_sha256": row["result_sha256"],
                "engineering_ci:outcome": row["result"]["status"],
                "engineering_ci:exit_code": row["result"]["exit_code"],
                "engineering_ci:start_ns": row["started_ns"],
                "engineering_ci:end_ns": row["ended_ns"],
                "engineering_ci:time_scope": row["time_scope"],
                "engineering_ci:evidence_reused": row["result"]["reused"],
                "engineering_ci:actual_check_executed": row["check"][
                    "actual_check_executed"
                ],
                "engineering_ci:check_evidence_status": row["check"]["status"],
            }
            if row["batch"]:
                attrs.update(
                    {
                        "engineering_ci:batch_id": row["batch"]["id"],
                        "engineering_ci:batch_size": row["batch"]["size"],
                    }
                )
            facts = row["check"]
            for key, value in facts.get("identity_parts", {}).items():
                attrs["engineering_ci:identity_sha256:" + key] = value
            if facts.get("report"):
                attrs["engineering_ci:report_sha256"] = facts["report"]["sha256"]
            for group, prefix in (
                ("parameters", "parameter"),
                ("tool_versions", "tool_version"),
                ("environment", "environment"),
                ("counts", "count"),
            ):
                for key, value in facts.get("details", {}).get(group, {}).items():
                    attrs[f"engineering_ci:{prefix}:{key}"] = value
            event_id = digest(
                {
                    "execution_id": metadata["execution_id"],
                    "step_id": step,
                    "attempt": row["attempt"],
                    "result_sha256": row["result_sha256"],
                }
            )
            attrs["engineering_ci:event_id"] = event_id
            members.append(
                {
                    "activity": row["tool_id"],
                    "event_id": event_id,
                    "start_ns": row["started_ns"],
                    "end_ns": row["ended_ns"],
                    "attributes": attrs,
                }
            )
        case_id = metadata["execution_id"] + ":" + step
        if reason or not members:
            excluded.append(
                {
                    "case_id": case_id,
                    "step_id": step,
                    "reason": reason or "no_observed_attempt",
                    "attempts": len(groups[step]),
                }
            )
        else:
            selected.append(
                {
                    "case_id": case_id,
                    "operations": members,
                    "attributes": {
                        "engineering_ci:execution_id": metadata["execution_id"],
                        "engineering_ci:generation_id": metadata["generation_id"],
                        "engineering_ci:definition_id": metadata["definition_id"],
                        "engineering_ci:step_id": step,
                        "engineering_ci:dependencies": json.dumps(
                            view["dependencies"][step], sort_keys=True
                        ),
                    },
                }
            )
    identity = {
        "event_log_version": EVENT_LOG_VERSION,
        **view["scope"],
        "allow_incomplete": allow_incomplete,
        "case_scope": "workflow-step",
    }
    return {
        "event_log_id": digest(identity),
        **identity,
        "completeness": completeness,
        "cases": selected,
        "excluded_cases": excluded,
        "recorded_result": metadata["recorded_result"],
        "source": metadata["source"],
        "target": metadata["target"],
        "dependencies": view["dependencies"],
        "time_scope": "adapter_invocation",
        "llm_used": False,
    }


def export_xes(log):
    """Export identical admitted cases without requiring a mining engine or database."""
    if not log["cases"]:
        raise WorkflowError(
            "No complete observed step cases are available for XES export"
        )
    attrs = {
        "engineering_ci:" + key: log[key]
        for key in (
            "event_log_id",
            "event_log_version",
            "execution_id",
            "state_sha256",
            "observations_sha256",
            "evidence_sha256",
            "case_scope",
            "completeness",
            "llm_used",
        )
    }
    attrs.update(
        {
            "engineering_ci:selected_cases": len(log["cases"]),
            "engineering_ci:excluded_cases": len(log["excluded_cases"]),
            "engineering_ci:unavailable_measurements": "individual_check_duration_ns,dagger_cache_hit,cost",
        }
    )
    return cases_xes(log["cases"], attributes=attrs)
