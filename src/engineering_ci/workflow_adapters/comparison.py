"""Controlled comparison of verified PR checks, separate from mining and execution."""

from ..workflow_core.documents import WorkflowError, digest
from .candidates import profile_target

COMPARISON_VERSION = "pr-check-submission-reuse/2"
IDENTITY_PARTS = {"inputs", "tools", "parameters", "environment"}


def sample(view):
    """Require one observed attempt for every original required PR check."""
    reasons = []
    try:
        target = profile_target(view["workflow"])
    except WorkflowError:
        target = None
        reasons.append("incomplete_or_unsupported_pr_profile")
    metadata = view["metadata"]
    if metadata["target_policy"] == "stale":
        reasons.append("stale_target_policy")
    if metadata["observation_completeness"] != "complete":
        reasons.append("observation_history_not_complete")
    if metadata["recorded_result"]["phase"] != "FINISHED":
        reasons.append("execution_not_finished")
    rows = {}
    for row in view["items"]:
        step = row["step_id"]
        if step in rows:
            reasons.append("multiple_attempts_require_a_new_controlled_execution")
        rows[step] = row
    if set(rows) != set(view["dependencies"]):
        reasons.append("required_attempts_missing")
    for row in rows.values():
        if (
            row["observation_status"] != "complete"
            or row["invocation_started"] is not True
        ):
            reasons.append("attempt_boundary_missing")
        check = row["check"]
        if not row["registered_check"] or check["status"] != "verified":
            reasons.append("original_check_evidence_unavailable")
        if (
            type(check.get("actual_check_executed")) is not bool
            or type(check.get("evidence_reused")) is not bool
        ):
            reasons.append("check_submission_measurement_unavailable")
        if not IDENTITY_PARTS <= check.get(
            "identity_parts", {}
        ).keys() or not check.get("identity_sha256"):
            reasons.append("execution_identity_unavailable")
    return rows, target, sorted(set(reasons))


def gate(row):
    """A report or adapter invocation cannot replace the original quality result."""
    if row is None or row["check"]["status"] != "verified" or row["result"] is None:
        return False
    result, original = row["result"], row["check"].get("recorded_gate") or {}
    return (
        result["status"] == original.get("status") == "passed"
        and result["exit_code"] == original.get("exit_code") == 0
        and all(
            result.get(key) is True
            for key in ("executed", "complete", "current", "report_generated")
        )
        and all(
            original.get(key) is True
            for key in ("executed", "input_stable", "passed_for_recorded_input")
        )
        and (row["step_id"] != "pytest" or original.get("pytest_complete") is True)
    )


def metrics(view):
    """Count original check submission markers; cache misses and durations remain unknown."""
    rows = view["items"]
    complete = view["metadata"]["observation_completeness"] == "complete"
    submissions = [row["check"].get("actual_check_executed") for row in rows]
    reused = [row["check"].get("evidence_reused") for row in rows]
    return {
        "required_checks": len(view["workflow"]["steps"]),
        "observed_check_attempts": len(rows),
        "check_submissions": sum(submissions)
        if complete and all(type(item) is bool for item in submissions)
        else None,
        "reused_checks": sum(reused)
        if complete and all(type(item) is bool for item in reused)
        else None,
        "adapter_batches": len(
            {(row["session_id"], row["batch"]["id"]) for row in rows}
        )
        if complete and all(row["batch"] for row in rows)
        else None,
        "individual_check_duration_ns": None,
        "dagger_cache_hit": None,
        "cost": None,
    }


def context(view, measured):
    """Keep exact execution references and recorded outcomes, without private payloads."""
    metadata = view["metadata"]
    return {
        "evidence": view["scope"],
        "execution": metadata["execution"],
        "generation_id": metadata["generation_id"],
        "definition_id": metadata["definition_id"],
        "source": metadata["source"],
        "target": metadata["target"],
        "recorded_result": metadata["recorded_result"],
        "metrics": measured,
    }


def compare(baseline, candidate):
    """Verify same-input submission reduction only; never grant approval or current CI success."""
    left, left_target, left_reasons = sample(baseline)
    right, right_target, right_reasons = sample(candidate)
    reasons = [
        {"side": side, "reason": reason}
        for side, values in (("baseline", left_reasons), ("candidate", right_reasons))
        for reason in values
    ]
    before, after = baseline["metadata"], candidate["metadata"]
    if baseline["scope"]["execution_id"] == candidate["scope"]["execution_id"]:
        reasons.append({"reason": "same_execution_is_not_an_independent_trial"})
    if before["definition_id"] != after["definition_id"] or left_target != right_target:
        reasons.append({"reason": "different_definition_or_target"})
    if before["source"] != after["source"]:
        reasons.append({"reason": "different_candidate_source"})
    if any(
        row["check"].get("actual_check_executed") is not True
        or row["check"].get("evidence_reused") is not False
        for row in left.values()
    ):
        reasons.append(
            {"side": "baseline", "reason": "baseline_is_not_a_fresh_check_sample"}
        )
    pairs = []
    for step in sorted(set(left) | set(right)):
        old, new = left.get(step), right.get(step)
        mismatches = []
        if old is None or new is None:
            mismatches.append("step_missing")
        else:
            if any(
                row["observation_status"] != "complete"
                or row["invocation_started"] is not True
                for row in (old, new)
            ):
                mismatches.append("attempt_boundary_missing")
            if any(
                not IDENTITY_PARTS <= row["check"].get("identity_parts", {}).keys()
                or not row["check"].get("identity_sha256")
                for row in (old, new)
            ):
                mismatches.append("execution_identity_unavailable")
            for key in ("tool_id", "contract_sha256", "input_sha256"):
                if old[key] != new[key]:
                    mismatches.append("different_" + key)
            for key in IDENTITY_PARTS | {"executor"}:
                if old["check"].get("identity_parts", {}).get(key) != new["check"].get(
                    "identity_parts", {}
                ).get(key):
                    mismatches.append("different_" + key)
            if old["check"].get("identity_sha256") != new["check"].get(
                "identity_sha256"
            ):
                mismatches.append("different_full_execution_identity")
            if (
                old["check"]["status"] != "verified"
                or new["check"]["status"] != "verified"
            ):
                mismatches.append("original_check_evidence_unavailable")
            if old["check"].get("details", {}).get("counts") != new["check"].get(
                "details", {}
            ).get("counts"):
                mismatches.append("different_recorded_test_counts")
            reused = new["check"].get("evidence_reused") is True
            submitted = new["check"].get("actual_check_executed")
            if (
                reused
                and submitted is not False
                or not reused
                and submitted is not True
            ):
                mismatches.append("inconsistent_submission_and_reuse_markers")
            if reused:
                expected = {
                    "execution_id": before["execution_id"],
                    "generation_id": before["generation_id"],
                    "definition_id": before["definition_id"],
                    "step_id": step,
                    "attempt": old["attempt"],
                    "result_sha256": old["result_sha256"],
                }
                parent = after["reuse_source"]
                if (
                    new["reuse_source"] != expected
                    or not parent
                    or parent["state_sha256"] != baseline["scope"]["state_sha256"]
                ):
                    mismatches.append("reuse_is_not_bound_to_this_baseline")
                ordered = lambda values: sorted(
                    values, key=lambda item: (item["path"], item["sha256"])
                )
                if ordered(old["check"].get("resources", [])) != ordered(
                    new["check"].get("resources", [])
                ):
                    mismatches.append("reused_resources_differ_from_baseline")
        if mismatches:
            reasons.extend(
                {"step_id": step, "reason": reason}
                for reason in sorted(set(mismatches))
            )
        pairs.append(
            {
                "step_id": step,
                "tool_id": (old or new)["tool_id"],
                "status": "not_comparable" if mismatches else "compared",
                "reasons": sorted(set(mismatches)),
                "baseline_attempt": old["attempt"] if old else None,
                "candidate_attempt": new["attempt"] if new else None,
                "baseline_result": old["result"] if old else None,
                "candidate_result": new["result"] if new else None,
                "baseline_result_sha256": old["result_sha256"] if old else None,
                "candidate_result_sha256": new["result_sha256"] if new else None,
                "baseline_gate_passed": gate(old),
                "candidate_gate_passed": gate(new),
            }
        )
    left_metrics, right_metrics = metrics(baseline), metrics(candidate)
    recorded_passes = all(
        gate(row) for row in (*left.values(), *right.values())
    ) and bool(pairs)
    recorded_passes = recorded_passes and all(
        view["metadata"]["recorded_result"]
        == {"phase": "FINISHED", "status": "passed", "exit_code": 0}
        for view in (baseline, candidate)
    )
    comparable = not reasons
    reduction = (
        left_metrics["check_submissions"] - right_metrics["check_submissions"]
        if comparable
        else None
    )
    verified = comparable and recorded_passes and reduction > 0
    identity = {
        "comparison_version": COMPARISON_VERSION,
        "baseline": baseline["scope"],
        "candidate": candidate["scope"],
        "target": left_target,
        "scope": "same-input-pr-check-submission-reuse",
    }
    return {
        "schema_version": 1,
        "kind": "workflow-comparison",
        "comparison_id": digest(identity),
        **identity,
        "status": "compared" if comparable else "not_comparable",
        "exit_code": 0 if comparable and recorded_passes else 3,
        "baseline": context(baseline, left_metrics),
        "candidate": context(candidate, right_metrics),
        "checks": pairs,
        "reasons": reasons,
        "recorded_quality_gate": "passed" if recorded_passes else "not_passed",
        "current_input_validity": "not_checked",
        "quality_gate_replaced": False,
        "measurement_basis": "original checks.executed_this_invocation, not OS processes or Dagger cache misses",
        "cache_policy": "backend-managed; cache-hit state unmeasured",
        "check_submissions_avoided": reduction,
        "optimization_verified": verified,
        "verified_scope": "check-submission-reuse" if verified else None,
        "approved": False,
        "llm_used": False,
        "limitations": [
            "Historical matched-input trial; it cannot qualify current code or a Release",
            "No speed, cache-miss, token, cost or overall Agent improvement is asserted",
        ],
    }
