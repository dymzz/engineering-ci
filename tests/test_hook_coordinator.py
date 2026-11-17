"""#66 Hook Event Coordinator: Invocation collect and Final Reconciliation."""

import copy
import json
import shutil
import subprocess
from pathlib import Path

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

from engineering_ci import (
    check_contract,
    expected_checks,
    hooks,
    reconciliation,
    selection,
)
from engineering_ci import hook_coordinator as coordinator

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/selection-app"


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


@pytest.fixture()
def selection_repo(tmp_path):
    target = tmp_path / "selection-repo"
    shutil.copytree(FIXTURE, target)
    git(target, "init", "-q", "-b", "main")
    git(target, "config", "user.email", "expected@example.invalid")
    git(target, "config", "user.name", "Expected Test")
    git(target, "add", "-A")
    git(target, "commit", "-qm", "base")
    return target, git(target, "rev-parse", "HEAD")


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
            f"{local_ref} {after or head} {remote_ref} "
            f"{before if before is not None else zero}"
        )
    stdin_text = "\n".join(lines) + "\n"
    event = hooks.capture_pre_push(repo, "origin", location, stdin_text)
    return hooks.persist_event(repo, event)


def capture_precommit(repo):
    event = hooks.capture_pre_commit(repo)
    return hooks.persist_event(repo, event)


def event_ref_for_binding(binding, *, ref_index=0):
    """Producer-shaped Event Ref: head_ref=remote label, base_ref=raw before OID."""
    ref = binding.event["refs"][ref_index]
    raw_before = ref["before"]
    raw_after = ref["after"]
    return {
        "base_ref": raw_before,
        "head_ref": ref["remote_ref"],
        "base_commit": raw_before,
        "head_commit": raw_after,
        "local_ref": ref["local_ref"],
        "remote_ref": ref["remote_ref"],
        "raw_before": raw_before,
        "raw_after": raw_after,
        "references": [
            {
                **dict(ref),
                "raw_before": raw_before,
                "raw_after": raw_after,
                "source_ref": ref["remote_ref"],
            }
        ],
        "ref_action": ref.get("ref_action"),
    }


def attempt_native(run, job_ref, **kwargs):
    kwargs.setdefault("tool_access", "tach/check")
    kwargs.setdefault("runner_ref", run.runner_ref)
    return native(
        instance=coordinator.attempt_result_instance(run, job_ref),
        **kwargs,
    )


def runner_report(run, job_ref, result, *, name="tach"):
    """Simulate an original #52/Host Runner Execution Report."""
    production_ref = run.jobs[job_ref]["production_ref"]
    return {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [
            {
                "name": name,
                "status": result["status"],
                "plan_authorized": True,
                "check_contract": {
                    "kind": check_contract.CHECK_CONTRACT_KIND,
                    "status": "valid",
                },
                "native_result": result,
                "native_results": [result],
            }
        ],
        "invocation_ref": run.invocation_ref,
        "run_ref": run.run_ref,
        "attempt_ref": run.attempt_ref,
        "job_ref": job_ref,
        "production_ref": production_ref,
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
    resolution = (
        make_resolution(entry, source=source) if source else make_resolution(entry)
    )
    return entry, projection, resolution


def collect_and_issue(run, job_ref, projection, result):
    report = runner_report(run, job_ref, result)
    _results, rejections, report_ref = coordinator.collect_job_report(
        run, job_ref, report
    )
    assert rejections == {}
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


def test_issue_job_receipt_requires_runner_produced_report(repo):
    reference = capture_push(repo)
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    run = coordinator.open_attempt(binding, run_id="abc")
    run.record_job("job-1")
    _entry, projection, _resolution = closed_projection(binding)
    result = attempt_native(run, "job-1")
    bare = report_for([result])
    bare.update(
        {
            "invocation_ref": run.invocation_ref,
            "run_ref": run.run_ref,
            "attempt_ref": run.attempt_ref,
            "job_ref": "job-1",
            "production_ref": run.jobs["job-1"]["production_ref"],
        }
    )
    with pytest.raises(
        coordinator.HookCoordinatorError,
        match="execution_report_not_runner_produced",
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
    entry, projection, resolution = closed_projection(binding)
    result = attempt_native(run, "job-1")
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
            "base_ref": "1" * 40,
            "head_ref": "refs/heads/other",
            "base_commit": "1" * 40,
            "head_commit": "2" * 40,
            "local_ref": "refs/heads/other",
            "remote_ref": "refs/heads/other",
            "raw_before": "1" * 40,
            "raw_after": "2" * 40,
            "references": [
                {
                    "local_ref": "refs/heads/other",
                    "remote_ref": "refs/heads/other",
                    "before": "1" * 40,
                    "after": "2" * 40,
                    "raw_before": "1" * 40,
                    "raw_after": "2" * 40,
                }
            ],
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
    _entry, projection, _resolution = closed_projection(binding_old)
    result = attempt_native(run_old, "job-1")
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
    entry_new, projection_new, resolution_new = closed_projection(binding_new)
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


def test_historical_native_cannot_be_rewrapped_for_new_attempt(repo):
    first = capture_push(repo)
    second = capture_push(repo)
    binding_old = coordinator.load_invocation_binding(first.invocation_path)
    binding_new = coordinator.load_invocation_binding(second.invocation_path)
    run_old = coordinator.open_attempt(binding_old, run_id="old")
    run_old.record_job("job-1")
    _entry, projection, _resolution = closed_projection(binding_old)
    old_result = attempt_native(run_old, "job-1")
    _report, report_ref, _receipt = collect_and_issue(
        run_old, "job-1", projection, old_result
    )
    run_new = coordinator.open_attempt(binding_new, run_id="new")
    run_new.record_job("job-1")
    # A historical Native PASS placed into a B-attributed Report body.
    rewrapped = runner_report(run_new, "job-1", old_result)
    with pytest.raises(
        coordinator.HookCoordinatorError,
        match="native_result_attempt_mismatch",
    ):
        coordinator.collect_job_report(run_new, "job-1", rewrapped)
    genuine = attempt_native(run_new, "job-1")
    report_b = runner_report(run_new, "job-1", genuine)
    _results, _rejections, genuine_ref = coordinator.collect_job_report(
        run_new, "job-1", report_b
    )
    assert genuine_ref != report_ref
    assert report_b["checks"][0]["plan_authorized"] is True
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
            report=rewrapped,
        )


def test_unauthorized_runner_report_rejection_is_preserved(repo):
    reference = capture_push(repo)
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    run = coordinator.open_attempt(binding, run_id="unauth")
    run.record_job("job-1")
    result = attempt_native(run, "job-1")
    report = runner_report(run, "job-1", result)
    report["checks"][0]["plan_authorized"] = False
    with pytest.raises(
        coordinator.HookCoordinatorError,
        match="execution_report_not_runner_produced",
    ):
        coordinator.collect_job_report(run, "job-1", report)
    # Valid structure with plan_authorized False still rejected for Collect;
    # with True but contract invalid likewise.
    report["checks"][0]["plan_authorized"] = True
    report["checks"][0]["check_contract"] = {"status": "invalid"}
    with pytest.raises(
        coordinator.HookCoordinatorError,
        match="execution_report_not_runner_produced",
    ):
        coordinator.collect_job_report(run, "job-1", report)


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
    result = attempt_native(run, "job-1")
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
    result = attempt_native(run, "job-1")
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
    assert coordinator.hook_exit_code([outcome["receipts"][0]]) != 0


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
    entry, projection, resolution = closed_projection(binding)
    failed = native(
        status="failed",
        instance="fail-1",
        runner_ref=run.runner_ref,
    )
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
    _entry, projection, _resolution = closed_projection(binding)
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


def test_producer_new_ref_matches_raw_zero_not_analyzed_baseline(selection_repo):
    path, base = selection_repo
    (path / "src/pkg/new_ref.py").write_text("X = 1\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "new-ref-tip")
    tip = git(path, "rev-parse", "HEAD")
    zero = "0" * 40
    multi = selection.changeset_multi(
        path,
        [("refs/heads/feature", tip, "refs/heads/feature", zero)],
        new_ref_baseline=base,
    )
    assert multi.references[0]["raw_before"] == zero
    assert multi.references[0]["before"] == base
    assert multi.references[0]["before"] != multi.references[0]["raw_before"]
    _label, _baseline, ref_event, ref_source = expected_checks._ref_event_and_source(
        multi.references[0]
    )
    entry = make_entry(event=ref_event, source=ref_source)
    projection = make_projection(
        [entry],
        status="complete",
        event=ref_event,
        source=ref_source,
        consumer_completeness="expected_domain_complete",
    )
    reference = capture_push(path, after=tip, before=zero, ref="refs/heads/feature")
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    assert coordinator.verify_expected_matches_invocation(binding, projection) is True
    # Using analyzed baseline as transport before must not match Invocation.
    forged = copy.deepcopy(projection)
    forged["event_ref"]["references"][0]["raw_before"] = base
    forged["checks"][0]["event_ref"] = copy.deepcopy(forged["event_ref"])
    forged["checks"][0]["expected_check_ref"] = expected_checks.entry_identity(
        forged["checks"][0]
    )
    with pytest.raises(
        coordinator.HookCoordinatorError, match="expected_event_mismatch"
    ):
        coordinator.verify_expected_matches_invocation(binding, forged)


def test_producer_delete_ref_accepts_null_analyzed_after(selection_repo):
    path, base = selection_repo
    zero = "0" * 40
    multi = selection.changeset_multi(
        path,
        [("refs/heads/main", zero, "refs/heads/main", base)],
    )
    assert multi.references[0]["ref_action"] == "delete"
    assert multi.references[0]["after"] is None
    assert multi.references[0]["raw_after"] == zero
    _label, _baseline, ref_event, ref_source = expected_checks._ref_event_and_source(
        multi.references[0]
    )
    entry = make_entry(event=ref_event, source=ref_source)
    projection = make_projection(
        [entry],
        status="complete",
        event=ref_event,
        source=ref_source,
        consumer_completeness="expected_domain_complete",
    )
    reference = capture_push(path, after=zero, before=base, ref="refs/heads/main")
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    assert binding.event["refs"][0]["ref_action"] == "delete"
    assert coordinator.verify_expected_matches_invocation(binding, projection) is True


def test_multi_ref_finalize_with_producer_expected(selection_repo):
    path, base = selection_repo
    (path / "src/pkg/leaf_a.py").write_text("A = 1\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "ref-a")
    tip_a = git(path, "rev-parse", "HEAD")
    git(path, "checkout", "-q", base)
    (path / "docs/note.md").write_text("# note\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "ref-b")
    tip_b = git(path, "rev-parse", "HEAD")
    multi = selection.changeset_multi(
        path,
        [
            ("refs/heads/a", tip_a, "refs/heads/a", base),
            ("refs/heads/b", tip_b, "refs/heads/b", base),
        ],
    )
    cfg = selection.load_config(path / "ci-selection.toml")
    sel = selection.select(multi, cfg, root=path)
    plan = selection.plan(
        sel, config=cfg, config_path=path / "ci-selection.toml", root=path
    )
    expected = plan["expected_checks"]
    assert "per_ref" in expected
    assert len(expected["per_ref"]) == 2
    reference = capture_push_multi(
        path,
        [
            ("refs/heads/a", tip_a, "refs/heads/a", base),
            ("refs/heads/b", tip_b, "refs/heads/b", base),
        ],
    )
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    assert coordinator.verify_expected_matches_invocation(binding, expected) is True

    run = coordinator.open_attempt(binding, run_id="multi")
    routing_by_scope = {}
    resolutions_by_scope = {}
    observations_by_scope = {}
    for index, scope in enumerate(expected["per_ref"]):
        job_ref = f"job-{index}"
        entry = next(
            (item for item in scope["checks"] if item["name"] == "tach"),
            None,
        )
        if entry is None:
            routing_by_scope[scope["scope_id"]] = []
            resolutions_by_scope[scope["scope_id"]] = []
            observations_by_scope[scope["scope_id"]] = []
            continue
        run.record_job(job_ref)
        result = attempt_native(run, job_ref)
        report = runner_report(run, job_ref, result, name="tach")
        _results, _rejections, report_ref = coordinator.collect_job_report(
            run, job_ref, report
        )
        job_receipt = coordinator.issue_job_receipt(
            run,
            job_ref,
            report_ref,
            event_ref=scope["event_ref"],
            scope_id=scope["scope_id"],
            expected_set_ref=scope["expected_set_ref"],
            report=report,
        )
        routing_by_scope[scope["scope_id"]] = [
            coordinator.build_routing(entry["expected_check_ref"], run, [job_ref])
        ]
        resolutions_by_scope[scope["scope_id"]] = [
            make_resolution(entry, source=entry["source_ref"])
        ]
        observations_by_scope[scope["scope_id"]] = [
            coordinator.build_observation(run, job_ref, report, job_receipt=job_receipt)
        ]

    missing = copy.deepcopy(expected)
    missing["per_ref"] = expected["per_ref"][:1]
    with pytest.raises(
        coordinator.HookCoordinatorError, match="invocation_ref_uncovered"
    ):
        coordinator.finalize_invocation(missing, binding=binding)

    outcome = coordinator.finalize_invocation(
        expected,
        binding=binding,
        routing_by_scope=routing_by_scope,
        resolutions_by_scope=resolutions_by_scope,
        observations_by_scope=observations_by_scope,
    )
    assert len(outcome["receipts"]) == 2
    assert {item["scope_id"] for item in outcome["receipts"]} == {
        scope["scope_id"] for scope in expected["per_ref"]
    }
    assert all(item.get("ref") for item in outcome["receipts"])


def test_finalize_cli_reads_invocation(repo, tmp_path):
    reference = capture_push(repo)
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    run = coordinator.open_attempt(binding, run_id="cli")
    run.record_job("job-1")
    entry, projection, resolution = closed_projection(binding)
    result = attempt_native(run, "job-1")
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
