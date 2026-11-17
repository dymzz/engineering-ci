"""#66 Hook Event Coordinator: Invocation collect and Final Reconciliation."""

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from test_expected_checks import (
    _attach_effective_profile_policy,
    _sample_effective_profile,
)
from test_reconciliation import (
    RULE_TACH,
    make_entry,
    make_projection,
    make_resolution,
    report_for,
    report_ref_for,
)

from engineering_ci import (
    check_contract,
    expected_checks,
    hooks,
    plan_contract,
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


def worktree_docs_plan(path, base):
    """Plan that selects only docs so Report/Expected share one source_ref."""
    from test_execution import docs_only_plan

    (path / "docs/guide.md").write_text("# hook-coordinator\n", encoding="utf-8")
    head = git(path, "rev-parse", "HEAD")
    return docs_only_plan(base, head, paths=("docs/guide.md",))


def projection_from_executed_report(binding, report):
    """Closed Expected covering every Check that produced Native Results."""
    event_ref = event_ref_for_binding(binding)
    entries = []
    resolutions = []
    primary_source = None
    for check in report.get("checks") or ():
        natives = check.get("native_results") or ()
        if not natives:
            continue
        primary = check.get("native_result") or natives[0]
        source = copy.deepcopy(primary["source_ref"])
        rule = copy.deepcopy(primary["check"].get("rule_ref"))
        baseline = copy.deepcopy(primary.get("baseline_ref"))
        entry = make_entry(
            check["name"],
            event=event_ref,
            source=source,
            rule_ref=rule,
            baseline_ref=baseline,
        )
        entries.append(entry)
        resolutions.append(
            make_resolution(entry, rule=rule, source=source, baseline=baseline)
        )
        if primary_source is None:
            primary_source = source
    projection = make_projection(
        entries,
        status="complete",
        event=event_ref,
        source=primary_source,
        consumer_completeness="expected_domain_complete",
    )
    return entries, projection, resolutions


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


def run_collect_issue(run, job_ref, path, plan, binding):
    report = coordinator.execute_activated_job(run, job_ref, plan=plan, root=path)
    entries, projection, resolutions = projection_from_executed_report(binding, report)
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
    return report, report_ref, receipt, entries, projection, resolutions


def test_same_payload_yields_distinct_invocations(repo):
    first = capture_push(repo)
    second = capture_push(repo)
    assert first.event_payload_ref == second.event_payload_ref
    assert first.invocation_ref != second.invocation_ref


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


def test_real_execute_delivery_required_for_upstream_verified(selection_repo):
    path, base = selection_repo
    plan = worktree_docs_plan(path, base)
    reference = capture_push(path)
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    run = coordinator.open_attempt(binding, run_id="abc")
    run.record_job("job-1")
    forged = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [
            {
                "name": "docs",
                "status": "passed",
                "plan_authorized": True,
                "check_contract": {
                    "kind": check_contract.CHECK_CONTRACT_KIND,
                    "status": "valid",
                },
                "native_results": [],
            }
        ],
        "invocation_ref": run.invocation_ref,
        "run_ref": run.run_ref,
        "attempt_ref": run.attempt_ref,
        "job_ref": "job-1",
        "production_ref": run.jobs["job-1"]["production_ref"],
    }
    with pytest.raises(
        coordinator.HookCoordinatorError, match="job_execution_delivery_missing"
    ):
        coordinator.collect_job_report(run, "job-1", forged)
    report, report_ref, receipt, _entries, projection, _resolutions = run_collect_issue(
        run, "job-1", path, plan, binding
    )
    assert report["status"] == "passed"
    assert receipt["verification_status"] == "upstream_verified"
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


def test_finalize_matched_invocation_exits_zero(selection_repo):
    path, base = selection_repo
    plan = worktree_docs_plan(path, base)
    reference = capture_push(path)
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    run = coordinator.open_attempt(binding, run_id="match")
    run.record_job("job-1")
    report, _ref, job_receipt, entries, projection, resolutions = run_collect_issue(
        run, "job-1", path, plan, binding
    )
    routing = [
        coordinator.build_routing(entry["expected_check_ref"], run, ["job-1"])
        for entry in entries
    ]
    observation = coordinator.build_observation(
        run, "job-1", report, job_receipt=job_receipt
    )
    outcome = coordinator.finalize_invocation(
        projection,
        binding=binding,
        routing=routing,
        resolutions=resolutions,
        observations=[observation],
    )
    assert outcome["exit_code"] == 0
    assert outcome["receipts"][0]["status"] == "matched"
    assert all(
        entry["binding_status"] == "matched"
        for entry in outcome["receipts"][0]["entries"]
    )


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


def test_old_pass_does_not_cover_new_invocation(selection_repo):
    path, base = selection_repo
    plan = worktree_docs_plan(path, base)
    first = capture_push(path)
    second = capture_push(path)
    binding_old = coordinator.load_invocation_binding(first.invocation_path)
    binding_new = coordinator.load_invocation_binding(second.invocation_path)
    run_old = coordinator.open_attempt(binding_old, run_id="old")
    run_old.record_job("job-1")
    report, report_ref, old_receipt, _entries_old, _p, _resolutions_old = (
        run_collect_issue(run_old, "job-1", path, plan, binding_old)
    )
    run_new = coordinator.open_attempt(binding_new, run_id="new")
    run_new.record_job("job-1")
    _entries_new, projection_new, resolutions_new = projection_from_executed_report(
        binding_new, report
    )
    observation = {
        "job_ref": "job-1",
        "run_ref": run_old.run_ref,
        "attempt_ref": run_old.attempt_ref,
        "runner_ref": run_old.runner_ref,
        "report": report,
        "report_ref": copy.deepcopy(report_ref),
        "native_results": [
            native
            for check in report["checks"]
            for native in (check.get("native_results") or ())
        ],
        "job_receipt": old_receipt,
    }
    routing = [
        coordinator.build_routing(entry["expected_check_ref"], run_new, ["job-1"])
        for entry in _entries_new
    ]
    outcome = coordinator.finalize_invocation(
        projection_new,
        binding=binding_new,
        routing=routing,
        resolutions=resolutions_new,
        observations=[observation],
    )
    assert outcome["exit_code"] != 0
    assert outcome["receipts"][0]["entries"][0]["binding_status"] != "matched"


def test_historical_native_cannot_be_rewrapped_for_new_attempt(selection_repo):
    path, base = selection_repo
    plan = worktree_docs_plan(path, base)
    first = capture_push(path)
    second = capture_push(path)
    binding_old = coordinator.load_invocation_binding(first.invocation_path)
    binding_new = coordinator.load_invocation_binding(second.invocation_path)
    run_old = coordinator.open_attempt(binding_old, run_id="old")
    run_old.record_job("job-1")
    report_a, report_ref_a, _receipt, _e, projection, _res = run_collect_issue(
        run_old, "job-1", path, plan, binding_old
    )
    run_new = coordinator.open_attempt(binding_new, run_id="new")
    run_new.record_job("job-1")
    # Same fields as a delivered Report for B, but no execute delivery on B.
    rewrapped = copy.deepcopy(report_a)
    rewrapped["invocation_ref"] = run_new.invocation_ref
    rewrapped["run_ref"] = run_new.run_ref
    rewrapped["attempt_ref"] = run_new.attempt_ref
    rewrapped["job_ref"] = "job-1"
    rewrapped["production_ref"] = run_new.jobs["job-1"]["production_ref"]
    with pytest.raises(
        coordinator.HookCoordinatorError, match="job_execution_delivery_missing"
    ):
        coordinator.collect_job_report(run_new, "job-1", rewrapped)
    report_b = coordinator.execute_activated_job(run_new, "job-1", plan=plan, root=path)
    _results, _rej, genuine_ref = coordinator.collect_job_report(
        run_new, "job-1", report_b
    )
    assert genuine_ref != report_ref_a
    with pytest.raises(
        coordinator.HookCoordinatorError, match="execution_report_not_collected"
    ):
        coordinator.issue_job_receipt(
            run_new,
            "job-1",
            report_ref_a,
            event_ref=projection["event_ref"],
            scope_id=projection["scope_id"],
            expected_set_ref=projection["expected_set_ref"],
            report=rewrapped,
        )


def test_partial_expected_does_not_exit_zero(selection_repo):
    path, base = selection_repo
    plan = worktree_docs_plan(path, base)
    reference = capture_push(path)
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    run = coordinator.open_attempt(binding, run_id="partial")
    run.record_job("job-1")
    report = coordinator.execute_activated_job(run, "job-1", plan=plan, root=path)
    entries, projection, resolutions = projection_from_executed_report(binding, report)
    projection["status"] = "partial"
    projection["consumer_completeness"] = "not_claimed"
    _results, _rej, report_ref = coordinator.collect_job_report(run, "job-1", report)
    job_receipt = coordinator.issue_job_receipt(
        run,
        "job-1",
        report_ref,
        event_ref=projection["event_ref"],
        scope_id=projection["scope_id"],
        expected_set_ref=projection["expected_set_ref"],
        report=report,
    )
    routing = [
        coordinator.build_routing(entry["expected_check_ref"], run, ["job-1"])
        for entry in entries
    ]
    observation = coordinator.build_observation(
        run, "job-1", report, job_receipt=job_receipt
    )
    outcome = coordinator.finalize_invocation(
        projection,
        binding=binding,
        routing=routing,
        resolutions=resolutions,
        observations=[observation],
    )
    assert outcome["receipts"][0]["status"] == "partial"
    assert outcome["exit_code"] != 0


def test_complete_not_claimed_does_not_exit_zero(selection_repo):
    path, base = selection_repo
    plan = worktree_docs_plan(path, base)
    reference = capture_push(path)
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    run = coordinator.open_attempt(binding, run_id="not-claimed")
    run.record_job("job-1")
    report = coordinator.execute_activated_job(run, "job-1", plan=plan, root=path)
    entries, projection, resolutions = projection_from_executed_report(binding, report)
    projection["consumer_completeness"] = "not_claimed"
    _results, _rej, report_ref = coordinator.collect_job_report(run, "job-1", report)
    job_receipt = coordinator.issue_job_receipt(
        run,
        "job-1",
        report_ref,
        event_ref=projection["event_ref"],
        scope_id=projection["scope_id"],
        expected_set_ref=projection["expected_set_ref"],
        report=report,
    )
    routing = [
        coordinator.build_routing(entry["expected_check_ref"], run, ["job-1"])
        for entry in entries
    ]
    observation = coordinator.build_observation(
        run, "job-1", report, job_receipt=job_receipt
    )
    outcome = coordinator.finalize_invocation(
        projection,
        binding=binding,
        routing=routing,
        resolutions=resolutions,
        observations=[observation],
    )
    assert outcome["receipts"][0]["status"] == "matched"
    assert outcome["receipts"][0]["expected_completeness"] == "not_claimed"
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
    report = report_for([])
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
    reference = capture_precommit(repo)
    binding = coordinator.load_invocation_binding(reference.invocation_path)
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
    projection = make_projection(
        [make_entry(event=event_ref, source=source)],
        status="complete",
        event=event_ref,
        source=source,
        consumer_completeness="expected_domain_complete",
    )
    assert coordinator.verify_expected_matches_invocation(binding, projection) is True


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
    _label, _baseline, ref_event, ref_source = expected_checks._ref_event_and_source(
        multi.references[0]
    )
    projection = make_projection(
        [make_entry(event=ref_event, source=ref_source)],
        status="complete",
        event=ref_event,
        source=ref_source,
        consumer_completeness="expected_domain_complete",
    )
    reference = capture_push(path, after=tip, before=zero, ref="refs/heads/feature")
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    assert coordinator.verify_expected_matches_invocation(binding, projection) is True


def test_producer_delete_ref_accepts_null_analyzed_after(selection_repo):
    path, base = selection_repo
    zero = "0" * 40
    multi = selection.changeset_multi(
        path,
        [("refs/heads/main", zero, "refs/heads/main", base)],
    )
    assert multi.references[0]["after"] is None
    assert multi.references[0]["raw_after"] == zero
    _label, _baseline, ref_event, ref_source = expected_checks._ref_event_and_source(
        multi.references[0]
    )
    projection = make_projection(
        [make_entry(event=ref_event, source=ref_source)],
        status="complete",
        event=ref_event,
        source=ref_source,
        consumer_completeness="expected_domain_complete",
    )
    reference = capture_push(path, after=zero, before=base, ref="refs/heads/main")
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    assert coordinator.verify_expected_matches_invocation(binding, projection) is True


def test_multi_ref_matched_finalize_with_profile(selection_repo):
    """P2: dual-ref producer Expected with complete scopes and exit 0."""
    path, base = selection_repo
    profile = _sample_effective_profile(
        context="push",
        action="update",
        target="refs/heads/a",
        targets=["refs/heads/a", "refs/heads/b"],
        declared_checks=["whitespace", "forwarding", "docs", "vulture", "tach"],
        required_obligations=[
            {
                "obligation_ref": "ob/docs-a",
                "kind": "check",
                "check_names": ["docs"],
                "target_refs": ["refs/heads/a"],
                "allow_inapplicable": True,
            },
            {
                "obligation_ref": "ob/docs-b",
                "kind": "check",
                "check_names": ["docs"],
                "target_refs": ["refs/heads/b"],
                "allow_inapplicable": True,
            },
        ],
        allowed_inapplicability=["no_tests", "not_applicable"],
    )
    cfg = _attach_effective_profile_policy(path, profile)
    git(path, "add", "ci-selection.toml")
    git(path, "commit", "-qm", "attach effective profile")
    base = git(path, "rev-parse", "HEAD")

    (path / "docs/one.md").write_text("# one\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "ref-a-docs")
    tip_a = git(path, "rev-parse", "HEAD")
    git(path, "checkout", "-q", base)
    (path / "docs/two.md").write_text("# two\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "ref-b-docs")
    tip_b = git(path, "rev-parse", "HEAD")
    multi = selection.changeset_multi(
        path,
        [
            ("refs/heads/a", tip_a, "refs/heads/a", base),
            ("refs/heads/b", tip_b, "refs/heads/b", base),
        ],
    )
    sel = selection.select(multi, cfg, root=path)
    plan = selection.plan(
        sel,
        config=cfg,
        config_path=path / "ci-selection.toml",
        root=path,
        effective_profile=profile,
    )
    plan_contract.validate(plan)
    expected = plan["expected_checks"]
    assert len(expected["per_ref"]) == 2
    statuses = {scope["remote_ref"]: scope["status"] for scope in expected["per_ref"]}
    assert statuses == {
        "refs/heads/a": "complete",
        "refs/heads/b": "complete",
    }, statuses
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
        tip = tip_a if index == 0 else tip_b
        git(path, "checkout", "-q", tip)
        ref_cs = selection.ref_changeset(path, multi.references[index])
        ref_sel = selection.select(ref_cs, cfg, root=path)
        ref_plan = selection.plan(
            ref_sel,
            config=cfg,
            config_path=path / "ci-selection.toml",
            root=path,
            effective_profile=profile,
        )
        run.record_job(job_ref)
        report = coordinator.execute_activated_job(
            run, job_ref, plan=ref_plan, root=path
        )
        assert any(
            item.get("native_results") and item.get("plan_authorized") is True
            for item in report.get("checks") or ()
        ), (report.get("status"), report.get("reason"), report.get("checks"))
        _results, _rej, report_ref = coordinator.collect_job_report(
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
        scope_routing = []
        scope_resolutions = []
        for entry in scope["checks"]:
            if entry.get("applicability", {}).get("status") in {
                "not_applicable",
                "no_tests",
            }:
                continue
            executed = next(
                (
                    item
                    for item in report.get("checks") or ()
                    if item.get("name") == entry["name"] and item.get("native_results")
                ),
                None,
            )
            if executed is None:
                continue
            primary = executed.get("native_result") or executed["native_results"][0]
            scope_routing.append(
                coordinator.build_routing(entry["expected_check_ref"], run, [job_ref])
            )
            scope_resolutions.append(
                make_resolution(
                    entry,
                    rule=primary["check"].get("rule_ref") or entry.get("rule_ref"),
                    source=primary["source_ref"],
                    baseline=primary.get("baseline_ref"),
                )
            )
        routing_by_scope[scope["scope_id"]] = scope_routing
        resolutions_by_scope[scope["scope_id"]] = scope_resolutions
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
    assert all(item["status"] == "matched" for item in outcome["receipts"])
    assert all(item["all_observations_reconciled"] for item in outcome["receipts"])
    for receipt, scope in zip(outcome["receipts"], expected["per_ref"], strict=True):
        docs_ref = next(
            item["expected_check_ref"]
            for item in scope["checks"]
            if item["name"] == "docs"
        )
        docs_entry = next(
            item
            for item in receipt["entries"]
            if item["expected_check_ref"] == docs_ref
        )
        assert docs_entry["binding_status"] == "matched"
    assert outcome["exit_code"] == 0


def test_finalize_cli_reads_invocation(selection_repo, tmp_path):
    path, base = selection_repo
    plan = worktree_docs_plan(path, base)
    reference = capture_push(path)
    binding = coordinator.load_invocation_binding(reference.invocation_path)
    run = coordinator.open_attempt(binding, run_id="cli")
    run.record_job("job-1")
    report, _ref, job_receipt, entries, projection, resolutions = run_collect_issue(
        run, "job-1", path, plan, binding
    )
    routing = [
        coordinator.build_routing(entry["expected_check_ref"], run, ["job-1"])
        for entry in entries
    ]
    observation = coordinator.build_observation(
        run, "job-1", report, job_receipt=job_receipt
    )
    expected_path = tmp_path / "expected.json"
    routing_path = tmp_path / "routing.json"
    resolutions_path = tmp_path / "resolutions.json"
    observations_path = tmp_path / "observations.json"
    expected_path.write_text(json.dumps(projection), encoding="utf-8")
    routing_path.write_text(json.dumps(routing), encoding="utf-8")
    resolutions_path.write_text(json.dumps(resolutions), encoding="utf-8")
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
