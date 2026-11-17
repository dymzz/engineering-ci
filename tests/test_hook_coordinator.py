"""#66 Hook Event Coordinator: Invocation collect and Final Reconciliation."""

import copy
import json
import subprocess

import pytest
from test_reconciliation import (
    RULE_TACH,
    make_entry,
    make_projection,
    make_resolution,
    native,
    report_for,
    report_ref_for,
)

from engineering_ci import hook_coordinator as coordinator
from engineering_ci import hooks


def git(root, *args):
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "test")
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    git(root, "add", "a.txt")
    git(root, "commit", "-qm", "init")
    return root


def capture_push(
    repo, *, location="origin", after=None, before=None, ref="refs/heads/main"
):
    head = git(repo, "rev-parse", "HEAD")
    local_oid = after if after is not None else head
    remote_oid = before if before is not None else ("0" * 40)
    stdin_text = f"{ref} {local_oid} {ref} {remote_oid}\n"
    event = hooks.capture_pre_push(repo, "origin", location, stdin_text)
    return hooks.persist_event(repo, event)


def event_ref_for_binding(binding):
    ref = binding.event["refs"][0]
    return {
        "base_ref": ref["remote_ref"],
        "head_ref": ref["local_ref"],
        "base_commit": ref["before"],
        "head_commit": ref["after"],
        "references": [],
        "ref_action": ref.get("ref_action"),
    }


def closed_projection(binding):
    event_ref = event_ref_for_binding(binding)
    entry = make_entry(event=event_ref)
    projection = make_projection(
        [entry],
        status="complete",
        event=event_ref,
        consumer_completeness="expected_domain_complete",
    )
    resolution = make_resolution(entry)
    result = native(tool_access="tach/check")
    return entry, projection, resolution, result


def collect_and_issue(run, job_ref, projection, result):
    report = coordinator.attribute_report(run, job_ref, report_for([result]))
    _results, _rejections, report_ref = coordinator.collect_job_report(
        run, job_ref, report
    )
    receipt = coordinator.issue_job_receipt(
        run,
        job_ref,
        report_ref,
        event_ref=projection["event_ref"],
        scope_id=projection["scope_id"],
        expected_set_ref=projection["expected_set_ref"],
        report=report,
    )
    return report, report_ref, receipt


def test_same_payload_yields_distinct_invocations(repo):
    first = capture_push(repo)
    second = capture_push(repo)
    assert first.event_payload_ref == second.event_payload_ref
    assert first.invocation_ref != second.invocation_ref
    binding_a = coordinator.load_invocation_binding(first.invocation_path)
    binding_b = coordinator.load_invocation_binding(second.invocation_path)
    assert binding_a.invocation_ref == first.invocation_ref
    assert binding_b.invocation_ref == second.invocation_ref
    assert binding_a.event_payload_ref == binding_b.event_payload_ref


def test_verify_invocation_cli(repo):
    reference = capture_push(repo)
    code = coordinator.main(
        [
            "verify-invocation",
            "--invocation-path",
            str(reference.invocation_path),
            "--json",
        ]
    )
    assert code == 0


def test_issue_job_receipt_requires_collected_attributed_report(repo):
    reference = capture_push(repo)
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    run = coordinator.open_attempt(binding, run_id="abc")
    run.record_job("job-1")
    _entry, projection, _resolution, result = closed_projection(binding)
    bare = report_for([result])
    with pytest.raises(
        coordinator.HookCoordinatorError, match="execution_report_attribution_missing"
    ):
        coordinator.collect_job_report(run, "job-1", bare)
    _report, report_ref, receipt = collect_and_issue(run, "job-1", projection, result)
    assert receipt["verification_status"] == "upstream_verified"
    assert receipt["invocation_ref"] == binding.invocation_ref
    assert receipt["issuer_ref"] == coordinator.coordinator_issuer_ref()
    with pytest.raises(
        coordinator.HookCoordinatorError, match="execution_report_required"
    ):
        coordinator.issue_job_receipt(
            run,
            "job-1",
            report_ref,
            event_ref=projection["event_ref"],
            scope_id=projection["scope_id"],
            expected_set_ref=projection["expected_set_ref"],
            report=None,
        )


def test_finalize_matched_invocation_exits_zero(repo):
    reference = capture_push(repo)
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    run = coordinator.open_attempt(binding, run_id="match")
    run.record_job("job-1")
    entry, projection, resolution, result = closed_projection(binding)
    report, _report_ref, job_receipt = collect_and_issue(
        run, "job-1", projection, result
    )
    routing = coordinator.build_routing(entry["expected_check_ref"], run, ["job-1"])
    observation = coordinator.build_observation(
        run, "job-1", report, job_receipt=job_receipt
    )
    outcome = coordinator.finalize_invocation(
        projection,
        binding=binding,
        routing=[routing],
        resolutions=[resolution],
        observations=[observation],
    )
    assert outcome["exit_code"] == 0
    assert outcome["receipts"][0]["status"] == "matched"
    assert outcome["receipts"][0]["entries"][0]["binding_status"] == "matched"
    assert outcome["receipts"][0]["expected_invocation_ref"] == binding.invocation_ref


def test_expected_event_mismatch_blocks_finalize(repo):
    reference = capture_push(repo)
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    foreign = make_entry(
        event={
            "base_ref": "refs/heads/other",
            "head_ref": "refs/heads/other",
            "base_commit": "1" * 40,
            "head_commit": "2" * 40,
            "references": [],
        }
    )
    projection = make_projection(
        [foreign],
        status="complete",
        event=foreign["event_ref"],
        consumer_completeness="expected_domain_complete",
    )
    with pytest.raises(
        coordinator.HookCoordinatorError, match="expected_event_mismatch"
    ):
        coordinator.finalize_invocation(projection, binding=binding)


def test_old_pass_does_not_cover_new_invocation(repo):
    first = capture_push(repo)
    second = capture_push(repo)
    binding_old = coordinator.load_invocation_binding(first.invocation_path)
    binding_new = coordinator.load_invocation_binding(second.invocation_path)
    run_old = coordinator.open_attempt(binding_old, run_id="old")
    run_old.record_job("job-1")
    _entry, projection, _resolution, result = closed_projection(binding_old)
    report, report_ref, old_receipt = collect_and_issue(
        run_old, "job-1", projection, result
    )
    run_new = coordinator.open_attempt(binding_new, run_id="new")
    run_new.record_job("job-1")
    observation = {
        "job_ref": "job-1",
        "run_ref": run_old.run_ref,
        "attempt_ref": run_old.attempt_ref,
        "runner_ref": run_old.runner_ref,
        "report": report,
        "report_ref": copy.deepcopy(report_ref),
        "native_results": [result],
        "job_receipt": old_receipt,
    }
    # Same payload Expected must still bind to the new Invocation Event OIDs.
    entry_new, projection_new, resolution_new, _result = closed_projection(binding_new)
    routing = coordinator.build_routing(
        entry_new["expected_check_ref"], run_new, ["job-1"]
    )
    outcome = coordinator.finalize_invocation(
        projection_new,
        binding=binding_new,
        routing=[routing],
        resolutions=[resolution_new],
        observations=[observation],
    )
    assert outcome["exit_code"] != 0
    assert outcome["receipts"][0]["entries"][0]["binding_status"] != "matched"


def test_old_report_cannot_be_reissued_on_new_attempt(repo):
    first = capture_push(repo)
    second = capture_push(repo)
    binding_old = coordinator.load_invocation_binding(first.invocation_path)
    binding_new = coordinator.load_invocation_binding(second.invocation_path)
    run_old = coordinator.open_attempt(binding_old, run_id="old")
    run_old.record_job("job-1")
    _entry, projection, _resolution, result = closed_projection(binding_old)
    report, report_ref, _receipt = collect_and_issue(
        run_old, "job-1", projection, result
    )
    run_new = coordinator.open_attempt(binding_new, run_id="new")
    run_new.record_job("job-1")
    with pytest.raises(
        coordinator.HookCoordinatorError,
        match="execution_report_attribution_mismatch",
    ):
        coordinator.collect_job_report(run_new, "job-1", report)
    with pytest.raises(
        coordinator.HookCoordinatorError, match="execution_report_not_collected"
    ):
        coordinator.issue_job_receipt(
            run_new,
            "job-1",
            report_ref,
            event_ref=projection["event_ref"],
            scope_id=projection["scope_id"],
            expected_set_ref=projection["expected_set_ref"],
            report=report,
        )


def test_partial_expected_does_not_exit_zero(repo):
    reference = capture_push(repo)
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    run = coordinator.open_attempt(binding, run_id="partial")
    run.record_job("job-1")
    event_ref = event_ref_for_binding(binding)
    entry = make_entry(event=event_ref)
    projection = make_projection(
        [entry],
        status="partial",
        event=event_ref,
        consumer_completeness="not_claimed",
    )
    resolution = make_resolution(entry)
    result = native(tool_access="tach/check")
    report, _report_ref, job_receipt = collect_and_issue(
        run, "job-1", projection, result
    )
    routing = coordinator.build_routing(entry["expected_check_ref"], run, ["job-1"])
    observation = coordinator.build_observation(
        run, "job-1", report, job_receipt=job_receipt
    )
    outcome = coordinator.finalize_invocation(
        projection,
        binding=binding,
        routing=[routing],
        resolutions=[resolution],
        observations=[observation],
    )
    assert outcome["receipts"][0]["entries"][0]["binding_status"] == "matched"
    assert outcome["receipts"][0]["status"] == "partial"
    assert outcome["exit_code"] != 0


def test_filtered_job_still_runs_final_reconciliation(repo):
    reference = capture_push(repo)
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    run = coordinator.open_attempt(binding, run_id="filtered")
    event_ref = event_ref_for_binding(binding)
    entry = make_entry(event=event_ref, rule_ref=RULE_TACH)
    projection = make_projection(
        [entry],
        status="complete",
        event=event_ref,
        consumer_completeness="expected_domain_complete",
    )
    routing = coordinator.build_routing(
        entry["expected_check_ref"],
        run,
        [],
        mapping_status="filtered",
        reason="glob-excluded",
    )
    outcome = coordinator.finalize_invocation(
        projection,
        binding=binding,
        routing=[routing],
        resolutions=[],
        observations=[],
    )
    assert outcome["exit_code"] != 0
    assert len(outcome["receipts"]) == 1
    assert outcome["receipts"][0]["entries"][0]["binding_status"] in {
        "unrouted",
        "unknown",
        "missing",
    }


def test_terminal_fail_attempt_still_finalizes(repo):
    reference = capture_push(repo)
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    run = coordinator.open_attempt(binding, run_id="fail")
    run.record_job("job-1", eligibility="terminal", mapping_status="routed")
    entry, projection, resolution, _result = closed_projection(binding)
    failed = native(status="failed", instance="fail-1")
    report = coordinator.attribute_report(run, "job-1", report_for([failed]))
    report_ref = report_ref_for(report)
    with pytest.raises(coordinator.HookCoordinatorError, match="attempt_not_eligible"):
        coordinator.collect_job_report(run, "job-1", report)
    with pytest.raises(coordinator.HookCoordinatorError, match="attempt_not_eligible"):
        coordinator.issue_job_receipt(
            run,
            "job-1",
            report_ref,
            event_ref=projection["event_ref"],
            scope_id=projection["scope_id"],
            expected_set_ref=projection["expected_set_ref"],
            report=report,
        )
    routing = coordinator.build_routing(entry["expected_check_ref"], run, ["job-1"])
    observation = coordinator.build_observation(run, "job-1", report)
    outcome = coordinator.finalize_invocation(
        projection,
        binding=binding,
        routing=[routing],
        resolutions=[resolution],
        observations=[observation],
    )
    assert outcome["exit_code"] != 0
    assert outcome["receipts"][0]["entries"][0]["binding_status"] != "matched"


def test_empty_per_ref_container_is_rejected(repo):
    reference = capture_push(repo)
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    _entry, projection, _resolution, _result = closed_projection(binding)
    projection["per_ref"] = []
    with pytest.raises(
        coordinator.HookCoordinatorError, match="expected_ref_scopes_empty"
    ):
        coordinator.finalize_invocation(projection, binding=binding)


def test_finalize_cli_reads_invocation(repo, tmp_path):
    reference = capture_push(repo)
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    run = coordinator.open_attempt(binding, run_id="cli")
    run.record_job("job-1")
    entry, projection, resolution, result = closed_projection(binding)
    report, _report_ref, job_receipt = collect_and_issue(
        run, "job-1", projection, result
    )
    routing = coordinator.build_routing(entry["expected_check_ref"], run, ["job-1"])
    observation = coordinator.build_observation(
        run, "job-1", report, job_receipt=job_receipt
    )
    expected_path = tmp_path / "expected.json"
    routing_path = tmp_path / "routing.json"
    resolutions_path = tmp_path / "resolutions.json"
    observations_path = tmp_path / "observations.json"
    expected_path.write_text(json.dumps(projection), encoding="utf-8")
    routing_path.write_text(json.dumps([routing]), encoding="utf-8")
    resolutions_path.write_text(json.dumps([resolution]), encoding="utf-8")
    observations_path.write_text(json.dumps([observation]), encoding="utf-8")
    code = coordinator.main(
        [
            "finalize",
            "--invocation-path",
            str(reference.invocation_path),
            "--expected",
            str(expected_path),
            "--routing",
            str(routing_path),
            "--resolutions",
            str(resolutions_path),
            "--observations",
            str(observations_path),
            "--json",
        ]
    )
    assert code == 0
