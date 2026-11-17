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

from engineering_ci import check_contract, hooks
from engineering_ci import hook_coordinator as coordinator


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


def capture_push_multi(repo, updates, *, location="origin"):
    """Capture a multi-ref pre-push. Each update is (local, after, remote, before)."""
    head = git(repo, "rev-parse", "HEAD")
    zero = "0" * 40
    lines = []
    for local_ref, after, remote_ref, before in updates:
        lines.append(
            f"{local_ref} {after or head} {remote_ref} {before if before is not None else zero}"
        )
    stdin_text = "\n".join(lines) + "\n"
    event = hooks.capture_pre_push(repo, "origin", location, stdin_text)
    return hooks.persist_event(repo, event)


def capture_precommit(repo):
    event = hooks.capture_pre_commit(repo)
    return hooks.persist_event(repo, event)


def event_ref_for_binding(binding, *, ref_index=0):
    ref = binding.event["refs"][ref_index]
    return {
        "base_ref": ref["remote_ref"],
        "head_ref": ref["local_ref"],
        "base_commit": ref["before"],
        "head_commit": ref["after"],
        "references": [dict(ref)],
        "ref_action": ref.get("ref_action"),
    }


def closed_projection(binding, *, ref_index=0, source=None):
    event_ref = event_ref_for_binding(binding, ref_index=ref_index)
    entry = make_entry(event=event_ref, source=source)
    projection = make_projection(
        [entry],
        status="complete",
        event=event_ref,
        source=source,
        consumer_completeness="expected_domain_complete",
    )
    resolution = make_resolution(entry, source=source) if source else make_resolution(entry)
    result = native(tool_access="tach/check", source=source) if source else native(
        tool_access="tach/check"
    )
    return entry, projection, resolution, result


def per_ref_scope(binding, *, ref_index, scope_suffix):
    """Build one Expected per-ref child scope for an Invocation Ref."""
    ref = binding.event["refs"][ref_index]
    event_ref = event_ref_for_binding(binding, ref_index=ref_index)
    entry = make_entry(event=event_ref)
    digest = (scope_suffix + ("0" * 64))[:64]
    return {
        "source_ref": ref["remote_ref"],
        "local_ref": ref["local_ref"],
        "remote_ref": ref["remote_ref"],
        "ref_action": ref.get("ref_action"),
        "force_push": False,
        "baseline_status": "ok",
        "event_ref": event_ref,
        "source": copy.deepcopy(entry["source_ref"]),
        "scope_id": f"sha256:{digest}",
        "expected_set_ref": f"sha256:{'b' * 64}",
        "checks": [entry],
        "non_check_obligations": [],
        "status": "complete",
        "ref": {
            "local_ref": ref["local_ref"],
            "remote_ref": ref["remote_ref"],
            "before": ref["before"],
            "after": ref["after"],
            "ref_action": ref.get("ref_action"),
        },
    }


def collect_and_issue(run, job_ref, projection, result):
    report = coordinator.build_execution_report(
        run, job_ref, native_results=[result]
    )
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


def test_issue_job_receipt_requires_built_production_report(repo):
    reference = capture_push(repo)
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    run = coordinator.open_attempt(binding, run_id="abc")
    run.record_job("job-1")
    _entry, projection, _resolution, result = closed_projection(binding)
    bare = report_for([result])
    with pytest.raises(
        coordinator.HookCoordinatorError, match="job_production_boundary_unproven"
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


def test_old_report_cannot_be_rebranded_onto_new_attempt(repo):
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
    production_ref = run_new.jobs["job-1"]["production_ref"]
    # Historical attack: stamp B's identity + production_ref onto A's Report.
    rebranded = copy.deepcopy(report)
    rebranded["invocation_ref"] = run_new.invocation_ref
    rebranded["run_ref"] = run_new.run_ref
    rebranded["attempt_ref"] = run_new.attempt_ref
    rebranded["job_ref"] = "job-1"
    rebranded["production_ref"] = production_ref
    with pytest.raises(
        coordinator.HookCoordinatorError,
        match="job_production_boundary_unproven",
    ):
        coordinator.collect_job_report(run_new, "job-1", rebranded)
    # B produces different Native Results than A; stamped A body must not Collect.
    other = native(tool_access="tach/check", instance="attempt-b")
    genuine = coordinator.build_execution_report(
        run_new, "job-1", native_results=[other]
    )
    with pytest.raises(
        coordinator.HookCoordinatorError,
        match="execution_report_production_mismatch",
    ):
        coordinator.collect_job_report(run_new, "job-1", rebranded)
    _results, _rejections, genuine_ref = coordinator.collect_job_report(
        run_new, "job-1", genuine
    )
    assert genuine_ref != report_ref
    assert genuine["production_ref"] == production_ref
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
            report=rebranded,
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


def test_complete_not_claimed_does_not_exit_zero(repo):
    """P1-4: completeness labels are checked independently of status=matched."""
    reference = capture_push(repo)
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    run = coordinator.open_attempt(binding, run_id="not-claimed")
    run.record_job("job-1")
    event_ref = event_ref_for_binding(binding)
    entry = make_entry(event=event_ref)
    projection = make_projection(
        [entry],
        status="complete",
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
    assert outcome["receipts"][0]["status"] == "matched"
    assert outcome["receipts"][0]["expected_completeness"] == "not_claimed"
    assert outcome["exit_code"] != 0
    # Direct gate: forged matched+not_claimed must also refuse Exit 0.
    forged = copy.deepcopy(outcome["receipts"][0])
    assert (
        coordinator.hook_exit_code([forged]) != 0
    )


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
    with pytest.raises(coordinator.HookCoordinatorError, match="attempt_not_eligible"):
        coordinator.build_execution_report(run, "job-1", native_results=[failed])
    report = report_for([failed])
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


def test_precommit_index_tree_matches_when_index_differs_from_head(repo):
    head = git(repo, "rev-parse", "HEAD")
    head_tree = git(repo, "rev-parse", "HEAD^{tree}")
    (repo / "a.txt").write_text("staged-change\n", encoding="utf-8")
    git(repo, "add", "a.txt")
    index_tree = git(repo, "write-tree")
    assert index_tree != head_tree
    assert index_tree != head
    reference = capture_precommit(repo)
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    assert binding.event["index"]["tree"] == index_tree
    source = check_contract.source_ref(
        "index",
        declared_target_tree=index_tree,
        index_tree=index_tree,
        head_commit=head,
        verified_scope=["a.txt"],
    )
    event_ref = {
        "base_ref": "HEAD",
        "head_ref": "INDEX",
        "base_commit": head,
        "head_commit": head,
        "references": [],
    }
    entry = make_entry(event=event_ref, source=source)
    projection = make_projection(
        [entry],
        status="complete",
        event=event_ref,
        source=source,
        consumer_completeness="expected_domain_complete",
    )
    assert coordinator.verify_expected_matches_invocation(binding, projection) is True
    wrong = check_contract.source_ref(
        "index",
        declared_target_tree=head_tree,
        index_tree=head_tree,
        head_commit=head,
        verified_scope=["a.txt"],
    )
    wrong_projection = make_projection(
        [make_entry(event=event_ref, source=wrong)],
        status="complete",
        event=event_ref,
        source=wrong,
        consumer_completeness="expected_domain_complete",
    )
    with pytest.raises(
        coordinator.HookCoordinatorError, match="expected_event_mismatch"
    ):
        coordinator.verify_expected_matches_invocation(binding, wrong_projection)


def test_multi_ref_requires_full_invocation_coverage(repo):
    head = git(repo, "rev-parse", "HEAD")
    zero = "0" * 40
    reference = capture_push_multi(
        repo,
        [
            ("refs/heads/main", head, "refs/heads/main", zero),
            ("refs/heads/topic", head, "refs/heads/topic", zero),
        ],
    )
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    assert len(binding.event["refs"]) == 2
    scope_a = per_ref_scope(binding, ref_index=0, scope_suffix="aa")
    scope_b = per_ref_scope(binding, ref_index=1, scope_suffix="bb")
    covered = make_projection(
        [],
        status="partial",
        event=event_ref_for_binding(binding, ref_index=0),
        consumer_completeness="per_ref_container_only",
        per_ref_checks=[scope_a, scope_b],
    )
    assert coordinator.verify_expected_matches_invocation(binding, covered) is True
    missing = make_projection(
        [],
        status="partial",
        event=event_ref_for_binding(binding, ref_index=0),
        consumer_completeness="per_ref_container_only",
        per_ref_checks=[scope_a],
    )
    with pytest.raises(
        coordinator.HookCoordinatorError, match="invocation_ref_uncovered"
    ):
        coordinator.verify_expected_matches_invocation(binding, missing)


def test_multi_ref_same_local_different_remote_does_not_cross_match(repo):
    head = git(repo, "rev-parse", "HEAD")
    zero = "0" * 40
    reference = capture_push_multi(
        repo,
        [
            ("refs/heads/main", head, "refs/heads/prod", zero),
            ("refs/heads/main", head, "refs/heads/staging", zero),
        ],
    )
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    # Swap remotes relative to Invocation order: same local/OID, wrong targets.
    swapped_a = per_ref_scope(binding, ref_index=0, scope_suffix="cc")
    swapped_a["remote_ref"] = "refs/heads/staging"
    swapped_a["source_ref"] = "refs/heads/staging"
    swapped_a["ref"]["remote_ref"] = "refs/heads/staging"
    swapped_b = per_ref_scope(binding, ref_index=1, scope_suffix="dd")
    swapped_b["remote_ref"] = "refs/heads/prod"
    swapped_b["source_ref"] = "refs/heads/prod"
    swapped_b["ref"]["remote_ref"] = "refs/heads/prod"
    # Each Expected identity matches the other Invocation ref — but assignment
    # must still cover exact (local, remote, before, after) pairs; swapping
    # remotes yields a valid cover only if both exact pairs still appear.
    # Construct Expected that only covers prod under staging's remote label.
    only_wrong = make_projection(
        [],
        status="partial",
        event=event_ref_for_binding(binding, ref_index=0),
        consumer_completeness="per_ref_container_only",
        per_ref_checks=[swapped_a],
    )
    with pytest.raises(
        coordinator.HookCoordinatorError,
        match="expected_event_mismatch|invocation_ref_uncovered",
    ):
        coordinator.verify_expected_matches_invocation(binding, only_wrong)
    both_exact = make_projection(
        [],
        status="partial",
        event=event_ref_for_binding(binding, ref_index=0),
        consumer_completeness="per_ref_container_only",
        per_ref_checks=[
            per_ref_scope(binding, ref_index=0, scope_suffix="ee"),
            per_ref_scope(binding, ref_index=1, scope_suffix="ff"),
        ],
    )
    assert coordinator.verify_expected_matches_invocation(binding, both_exact) is True


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
