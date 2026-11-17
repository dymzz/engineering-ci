"""#66 Hook Event Coordinator: Invocation collect and Final Reconciliation."""

import copy
import json
import subprocess
from pathlib import Path

import pytest

from engineering_ci import hooks, reconciliation
from engineering_ci import hook_coordinator as coordinator

from test_reconciliation import (
    make_entry,
    make_projection,
    matched_setup,
    native,
    report_for,
    report_ref_for,
)


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


def capture_push(repo, *, location="origin"):
    before = git(repo, "rev-parse", "HEAD")
    stdin_text = f"refs/heads/main {before} refs/heads/main {'0' * 40}\n"
    event = hooks.capture_pre_push(repo, "origin", location, stdin_text)
    return hooks.persist_event(repo, event)


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


def test_issue_job_receipt_requires_verified_report(repo):
    reference = capture_push(repo)
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    run = coordinator.open_attempt(binding, run_id="abc")
    run.record_job("job-1")
    entry, projection, _routing, _resolution, _obs, result = matched_setup()
    report = report_for([result])
    report_ref = report_ref_for(report)
    receipt = coordinator.issue_job_receipt(
        run,
        "job-1",
        report_ref,
        event_ref=projection["event_ref"],
        scope_id=projection["scope_id"],
        expected_set_ref=projection["expected_set_ref"],
        report=report,
    )
    assert receipt["verification_status"] == "upstream_verified"
    assert receipt["invocation_ref"] == binding.invocation_ref
    assert receipt["issuer_ref"] == coordinator.coordinator_issuer_ref()

    with pytest.raises(coordinator.HookCoordinatorError, match="execution_report_required"):
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
    entry, projection, _routing, resolution, _obs, result = matched_setup()
    report = report_for([result])
    report_ref = report_ref_for(report)
    job_receipt = coordinator.issue_job_receipt(
        run,
        "job-1",
        report_ref,
        event_ref=projection["event_ref"],
        scope_id=projection["scope_id"],
        expected_set_ref=projection["expected_set_ref"],
        report=report,
    )
    routing = coordinator.build_routing(entry["expected_check_ref"], run, ["job-1"])
    observation = coordinator.build_observation(
        run, "job-1", report, job_receipt=job_receipt
    )
    outcome = coordinator.finalize_invocation(
        projection,
        invocation_ref=binding.invocation_ref,
        routing=[routing],
        resolutions=[resolution],
        observations=[observation],
    )
    assert outcome["exit_code"] == 0
    assert outcome["receipts"][0]["entries"][0]["binding_status"] == "matched"
    assert outcome["receipts"][0]["expected_invocation_ref"] == binding.invocation_ref


def test_old_pass_does_not_cover_new_invocation(repo):
    first = capture_push(repo)
    second = capture_push(repo)
    assert first.event_payload_ref == second.event_payload_ref
    binding_old = coordinator.load_invocation_binding(first.invocation_path)
    binding_new = coordinator.load_invocation_binding(second.invocation_path)

    run_old = coordinator.open_attempt(binding_old, run_id="old")
    run_old.record_job("job-1")
    entry, projection, _routing, resolution, _obs, result = matched_setup()
    report = report_for([result])
    report_ref = report_ref_for(report)
    old_receipt = coordinator.issue_job_receipt(
        run_old,
        "job-1",
        report_ref,
        event_ref=projection["event_ref"],
        scope_id=projection["scope_id"],
        expected_set_ref=projection["expected_set_ref"],
        report=report,
    )
    # Replay the old Job Receipt against the new Invocation's Finalize.
    run_new = coordinator.open_attempt(binding_new, run_id="new")
    run_new.record_job("job-1")
    routing = coordinator.build_routing(entry["expected_check_ref"], run_new, ["job-1"])
    # Observation still carries the old run/attempt/receipt identities.
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
    outcome = coordinator.finalize_invocation(
        projection,
        invocation_ref=binding_new.invocation_ref,
        routing=[routing],
        resolutions=[resolution],
        observations=[observation],
    )
    assert outcome["exit_code"] != 0
    entry_receipt = outcome["receipts"][0]["entries"][0]
    assert entry_receipt["binding_status"] != "matched"


def test_filtered_job_still_runs_final_reconciliation(repo):
    reference = capture_push(repo)
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    run = coordinator.open_attempt(binding, run_id="filtered")
    entry = make_entry(
        "tach",
        rule_ref={"name": "tach", "version": "1", "sha256": "b" * 64},
    )
    projection = make_projection([entry], status="partial")
    routing = coordinator.build_routing(
        entry["expected_check_ref"],
        run,
        [],
        mapping_status="filtered",
        reason="glob-excluded",
    )
    outcome = coordinator.finalize_invocation(
        projection,
        invocation_ref=binding.invocation_ref,
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
    entry, projection, _routing, resolution, _obs, result = matched_setup()
    failed = native(status="failed", instance="fail-1")
    report = report_for([failed])
    report_ref = report_ref_for(report)
    # Terminal attempts must not receive upstream_verified receipts.
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
        invocation_ref=binding.invocation_ref,
        routing=[routing],
        resolutions=[resolution],
        observations=[observation],
    )
    assert outcome["exit_code"] != 0
    assert outcome["receipts"][0]["entries"][0]["binding_status"] != "matched"


def test_finalize_cli_reads_invocation(repo, tmp_path):
    reference = capture_push(repo)
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    run = coordinator.open_attempt(binding, run_id="cli")
    run.record_job("job-1")
    entry, projection, _routing, resolution, _obs, result = matched_setup()
    report = report_for([result])
    report_ref = report_ref_for(report)
    job_receipt = coordinator.issue_job_receipt(
        run,
        "job-1",
        report_ref,
        event_ref=projection["event_ref"],
        scope_id=projection["scope_id"],
        expected_set_ref=projection["expected_set_ref"],
        report=report,
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
