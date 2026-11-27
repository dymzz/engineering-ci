"""Tests for bounded Lefthook parsing and read-only verify report correlation."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from engineering_ci import hook_checks, hooks, plan_contract, verify_report
from engineering_ci.cli import main


def _init_git_repo(root: Path) -> str:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)
    (root / "base.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "base.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()


def _create_split_precommit_repo(
    root: Path,
    *,
    checks: tuple[str, ...] = ("whitespace", "forwarding"),
    init_repo: bool = True,
):
    from test_native_hooks import INDEX_SELECTION, _approve_profiles, _source

    if init_repo:
        root.mkdir(parents=True, exist_ok=True)
        head = _init_git_repo(root)
    else:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
    (root / "lefthook.yml").write_text("min_version: '2.2.2'\n", encoding="utf-8")
    mod_file = _source(root, "mod", checks)
    mod_file.write_text("VALUE = 1\n", encoding="utf-8")
    _approve_profiles(
        root,
        INDEX_SELECTION,
        [("ci-effective-profile.json", "index", "index", "HEAD", checks)],
    )
    subprocess.run(
        [
            "git",
            "add",
            "ci-effective-profile.json",
            "ci-hygiene.toml",
            "ci-selection.toml",
            "lefthook.yml",
            "src",
            "tach.toml",
        ],
        cwd=root,
        check=True,
    )
    assert hooks.main(["capture", "pre-commit", "--root", str(root)]) == 0
    inv_dir = root / ".git" / "eci-hooks" / "invocations"
    inv_file = max(inv_dir.glob("*.json"), key=lambda p: p.stat().st_mtime_ns)
    binding = hooks.load_invocation_binding(inv_file)
    assert hook_checks.prepare(root, binding, split_checks=True)["exit_code"] == 0
    for chk in checks:
        assert (
            hook_checks.run_checks(root, binding, job_ref=f"check-{chk}")["exit_code"]
            == 0
        )
    assert hook_checks.reconcile(root, binding)["exit_code"] == 0
    assert hook_checks.consume_index_result(root, binding)["exit_code"] == 0
    return head, inv_file, binding


def test_parse_lefthook_missing_partial_and_truncated_summary_marks_unknown(tmp_path):
    stdout = tmp_path / "lefthook.stdout"
    stdout.write_text(
        "┃ lint ❯ \nsome noisy output\n│ skip-me (skip) by condition\n",
        encoding="utf-8",
    )
    config = {
        "parallel": False,
        "jobs": [
            {"name": "lint", "run": "echo ok"},
            {"name": "skip-me", "run": "echo no"},
            {"name": "unstarted-job", "run": "echo later"},
        ],
    }
    parsed = verify_report.parse_lefthook_output(
        stdout, verify_config=config, native_exit_code=1
    )
    assert parsed["authority"] == "diagnostic_only"
    assert parsed["summary_present"] is False
    assert parsed["total_duration_seconds"] == "unknown"
    by_name = {job["name"]: job for job in parsed["jobs"]}
    assert by_name["lint"]["status"] == "unknown"
    assert by_name["skip-me"]["status"] == "skipped"
    assert by_name["unstarted-job"]["status"] == "unknown"

    # Partial summary still populates unknown node in items & counts
    partial_stdout = tmp_path / "partial.stdout"
    partial_stdout.write_text(
        "summary: (done in 0.15 seconds)\n✓ lint (0.15 seconds)\n",
        encoding="utf-8",
    )
    parsed_partial = verify_report.parse_lefthook_output(
        partial_stdout, verify_config=config, native_exit_code=1
    )
    assert parsed_partial["summary_present"] is True
    assert parsed_partial["counts"]["unknown"] == 2
    partial_by_name = {job["name"]: job for job in parsed_partial["jobs"]}
    assert partial_by_name["skip-me"]["status"] == "unknown"
    assert partial_by_name["skip-me"]["source"] == "declared_missing_from_summary"

    truncated_stdout = tmp_path / "truncated.stdout"
    truncated_stdout.write_text(
        "summary: (done in unknown)\n✓ lint (0.1",
        encoding="utf-8",
    )
    parsed_cut = verify_report.parse_lefthook_output(
        truncated_stdout, verify_config=config
    )
    assert parsed_cut["summary_present"] is True
    assert parsed_cut["total_duration_seconds"] == "unknown"
    assert parsed_cut["output_truncated"] is True
    assert parsed_cut["jobs"][0]["name"] == "lint"
    assert parsed_cut["jobs"][0]["duration_seconds"] == "unknown"


def test_read_bounded_text_and_reportlog_line_ceilings(tmp_path, monkeypatch):
    monkeypatch.setattr(verify_report, "MAX_OUTPUT_PARSE_BYTES", 256)
    stdout = tmp_path / "huge.stdout"
    stdout.write_text(
        "│ skipped-top (skip) by condition\n"
        + ("line of build output\n" * 60)
        + "summary: (done in 1.25 seconds)\n✓ big-job (1.25 seconds)\n",
        encoding="utf-8",
    )
    parsed = verify_report.parse_lefthook_output(stdout)
    assert parsed["output_truncated"] is True
    assert parsed["summary_present"] is True
    assert parsed["total_duration_seconds"] == 1.25
    by_name = {job["name"]: job for job in parsed["jobs"]}
    assert by_name["skipped-top"]["status"] == "skipped"
    assert by_name["big-job"]["duration_seconds"] == 1.25

    monkeypatch.setattr(verify_report, "MAX_REPORTLOG_LINE_BYTES", 64)
    giant_line = tmp_path / "giant.jsonl"
    giant_line.write_text('{"x": "' + ("a" * 200) + '"}\n', encoding="utf-8")
    parsed_log = verify_report._parse_pytest_reportlog(giant_line)
    assert parsed_log["truncated"] is True
    assert parsed_log["report_completeness"] == "partial"


def test_verify_report_real_hook_linked_worktree_and_negative_paths(
    tmp_path, monkeypatch
):
    main_repo = tmp_path / "main-repo"
    main_repo.mkdir()
    _init_git_repo(main_repo)
    worktree = tmp_path / "linked-wt"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "wt-branch", str(worktree)],
        cwd=main_repo,
        check=True,
    )
    (worktree / "wt.txt").write_text("wt change\n", encoding="utf-8")
    subprocess.run(["git", "add", "wt.txt"], cwd=worktree, check=True)

    assert hooks.main(["capture", "pre-commit", "--root", str(worktree)]) == 0
    inv_dir = (
        main_repo / ".git" / "worktrees" / "linked-wt" / "eci-hooks" / "invocations"
    )
    (inv_file,) = inv_dir.glob("*.json")
    binding = hooks.load_invocation_binding(inv_file)
    assert hook_checks.prepare(worktree, binding)["exit_code"] == 0
    assert hook_checks.run_checks(worktree, binding)["exit_code"] == 0
    assert hook_checks.reconcile(worktree, binding)["exit_code"] == 0
    assert hook_checks.consume_index_result(worktree, binding)["exit_code"] == 0

    # Pure-file read-only consumption in linked worktree with zero git subprocesses
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no git subprocess")),
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no subprocess")),
    )

    out_file = tmp_path / "out" / "report.json"
    assert (
        main(
            [
                "verify",
                "report",
                "--hook-root",
                str(worktree),
                "--invocation-ref",
                binding.invocation_ref,
                "--out",
                str(out_file),
            ]
        )
        == 0
    )
    loaded = json.loads(out_file.read_text(encoding="utf-8"))
    assert loaded["read_only"] is True
    assert loaded["commands_executed"] == []
    hook_src = loaded["sources"][0]
    assert hook_src["status"] == "loaded"
    assert hook_src["hook"] == "pre-commit"
    assert hook_src["invocation_ref"] == binding.invocation_ref
    assert {s["stage"] for s in hook_src["stage_timings"]} == {
        "capture",
        "prepare",
        "run",
        "reconcile",
        "consume",
    }

    plan_path = inv_file.with_suffix(".plan")
    original_plan = plan_path.read_bytes()
    plan = json.loads(original_plan)
    del plan["event_payload_ref"]
    plan_path.write_bytes(hooks.canonical_event_bytes(plan))
    with pytest.raises(hooks.HookEventError, match="hook_plan_invocation_mismatch"):
        hook_checks._load_plan(binding)
    inspected = verify_report.build_verify_report(hook_invocation=inv_file)["sources"][
        0
    ]
    assert inspected["expected_unit_count"] is None
    assert inspected["observed_unit_count"] == 1
    assert inspected["executions"][0]["execution_binding_status"] == "unbound"
    plan_path.write_bytes(original_plan + b"\n")
    with pytest.raises(hooks.HookEventError, match="hook_artifact_invalid"):
        hook_checks._load_plan(binding)
    inspected = verify_report.build_verify_report(hook_invocation=inv_file)["sources"][
        0
    ]
    assert any(
        c.get("detail") == "hook_artifact_invalid"
        for c in inspected["internal_conflicts"]
    )
    plan_path.write_bytes(original_plan)

    # Invalid ref / bare hex rejected cleanly
    bad_ref = verify_report.build_verify_report(
        hook_root=worktree, invocation_ref=inv_file.stem
    )
    assert bad_ref["sources"][0]["status"] == "invalid_ref"

    # Tampered event_file path escape rejected cleanly
    tampered_inv = tmp_path / "tampered.json"
    bad_payload = json.loads(inv_file.read_text(encoding="utf-8"))
    bad_payload["event_file"] = "../secret.json"
    tampered_inv.write_bytes(hooks.canonical_event_bytes(bad_payload))
    bad_res = verify_report.build_verify_report(hook_invocation=tampered_inv)
    assert bad_res["sources"][0]["status"] == "invalid"


def test_verify_report_correlation_tiers_bundle_and_onetomany(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    head = _init_git_repo(repo)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)

    event_a = hooks.capture_pre_commit(repo)
    inv_a = hooks.persist_event(repo, event_a)
    inv_b = hooks.persist_event(repo, event_a)
    bind_a = hooks.load_invocation_binding(inv_a.invocation_path)
    assert hook_checks.prepare(repo, bind_a)["exit_code"] == 0
    assert hook_checks.run_checks(repo, bind_a)["exit_code"] == 0
    assert hook_checks.reconcile(repo, bind_a)["exit_code"] == 0

    # Same HEAD across Verify and CI does NOT prove execution_matched -> revision_only
    verify_dir = tmp_path / "verify"
    verify_dir.mkdir()
    raw_out = b"summary: (done in 0.05 seconds)\n"
    raw_err = b""
    (verify_dir / "lefthook.stdout").write_bytes(raw_out)
    (verify_dir / "lefthook.stderr").write_bytes(raw_err)
    (verify_dir / "verify-timing.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "invocation_id": "verify-rev",
                "observed_git_head": head,
                "native_exit_code": 0,
                "elapsed_seconds": 0.1,
                "time_source_verified": False,
                "native_outputs": {
                    "stdout": {
                        "path": "lefthook.stdout",
                        "sha256": hashlib.sha256(raw_out).hexdigest(),
                    },
                    "stderr": {
                        "path": "lefthook.stderr",
                        "sha256": hashlib.sha256(raw_err).hexdigest(),
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    ci_att1 = tmp_path / "ci-att1.json"
    ci_att1.write_text(
        json.dumps(
            {
                "run_id": 500,
                "run_attempt": 1,
                "sha": head,
                "workflow": "CI",
                "event": "push",
                "url": "https://example.invalid/runs/500",
                "jobs": [],
            }
        ),
        encoding="utf-8",
    )
    ci_att2 = tmp_path / "ci-att2.json"
    ci_att2.write_text(
        json.dumps(
            {
                "run_id": 500,
                "run_attempt": 2,
                "sha": head,
                "workflow": "CI",
                "event": "push",
                "url": "https://example.invalid/runs/500",
                "jobs": [],
            }
        ),
        encoding="utf-8",
    )

    rev_report = verify_report.build_verify_report(
        verify_dir=verify_dir, ci_performances=[ci_att1, ci_att2]
    )
    assert rev_report["correlation"]["overall_status"] == "revision_only"
    assert rev_report["correlation"]["scoped_conflicts"] == []
    assert rev_report["sources"][0]["evidence_complete"] is True
    assert (
        rev_report["sources"][0]["query_correlation"]["same_execution_proven"] is False
    )
    assert (
        rev_report["sources"][2]["ci_provider_provenance"]["rerun_witness_attribution"]
        == "unpartitioned_attempt_jobs_snapshot"
    )

    # Two distinct Hook invocations capturing identical event share content only
    shared_event_report = verify_report.build_verify_report(
        hook_invocation=inv_a.invocation_path,
        execution_reports=[],
    )
    assert shared_event_report["sources"][0]["query_correlation"]["strength"] == (
        "standalone_uncorrelated"
    )
    assert (
        verify_report.build_verify_report(
            execution_reports=[],
            pytest_reportlogs=[],
        )["correlation"]["overall_status"]
        == "no_sources"
    )

    two_hooks = verify_report._evaluate_correlations(
        [
            verify_report._load_hook_entry(hook_invocation=inv_a.invocation_path),
            verify_report._load_hook_entry(hook_invocation=inv_b.invocation_path),
        ]
    )
    assert two_hooks["overall_status"] == "content_equivalent"

    # #86 bundle dir with colocated unverified junit.xml & host/report.json digest check
    bundle_dir = tmp_path / "bundle-86"
    bundle_dir.mkdir()
    (bundle_dir / "host").mkdir()
    host_raw = json.dumps({"status": "passed", "exit_code": 0}).encode("utf-8")
    (bundle_dir / "host" / "report.json").write_bytes(host_raw)
    plan_payload = {
        "kind": plan_contract.PLAN_KIND,
        "schema_version": 2,
        "generator": {"config_sha256": "a" * 64},
        "git": {
            "snapshot": "commit",
            "head_commit": head,
            "source_tree_id": "sha256:" + "c" * 64,
        },
        "selection": {"tests": [], "prerequisites": []},
        "prerequisite_actions": {},
        "dependency_contract": plan_contract.graph_contract([]),
    }
    plan_payload_bytes = hooks.canonical_event_bytes(plan_payload)
    exec_report = {
        "kind": "engineering-ci-execution-report",
        "schema_version": 1,
        "status": "passed",
        "exit_code": 0,
        "duration_seconds": 0.4,
        "checks": [],
        "targets": [],
        "plan_ref": hooks._digest_bytes(plan_payload_bytes),
        "execution_identity": {
            "run_ref": "run:42",
            "attempt_ref": "att:1",
            "execution_ref": "exec:42",
        },
        "plan": {"generator": plan_payload["generator"], "git": plan_payload["git"]},
        "source_handoff": {
            "host_report_sha256": hashlib.sha256(host_raw).hexdigest(),
        },
    }
    (bundle_dir / "plan.json").write_bytes(plan_payload_bytes)
    (bundle_dir / "report.json").write_bytes(hooks.canonical_event_bytes(exec_report))
    (bundle_dir / "exit-code.txt").write_text("0\n", encoding="utf-8")
    (bundle_dir / "junit.xml").write_text(
        '<testsuite tests="1" failures="0" errors="0" skipped="0" time="0.2"><testcase name="t" time="0.2"/></testsuite>',
        encoding="utf-8",
    )

    # Also test #87 unbound telemetry preserved verbatim & split execution_reports 1-to-many
    exec_report_2 = {
        "kind": "engineering-ci-execution-report",
        "schema_version": 1,
        "status": "passed",
        "exit_code": 0,
        "checks": [],
        "execution_identity": {
            "run_ref": "run:42",
            "attempt_ref": "att:2",
            "execution_ref": "exec:43",
        },
    }
    exec2_path = tmp_path / "exec2.json"
    exec2_path.write_bytes(hooks.canonical_event_bytes(exec_report_2))
    telem_unbound = tmp_path / "telem.json"
    telem_unbound.write_text(
        json.dumps(
            {
                "correlation": {"status": "unbound", "matched_trace_id": "trace-z"},
                "source_binding": {
                    "source_binding_status": "unknown",
                    "declared_target_source_ref": "sha256:" + "c" * 64,
                },
                "run_ref": "run:42",
                "attempt_ref": "att:1",
            }
        ),
        encoding="utf-8",
    )

    bundle_res = verify_report.build_verify_report(
        execution_reports=[bundle_dir, exec2_path],
        dagger_telemetries=[telem_unbound],
    )
    assert bundle_res["correlation"]["scoped_conflicts"] == []
    junit_src = next(
        s for s in bundle_res["sources"] if s["source_type"] == "junit_xml"
    )
    assert junit_src["query_correlation"]["strength"] == "colocated_unverified"
    telem_src = next(
        s for s in bundle_res["sources"] if s["source_type"] == "dagger_telemetry"
    )
    assert telem_src["correlation"] == {
        "status": "unbound",
        "matched_trace_id": "trace-z",
    }
    assert telem_src["source_binding"]["source_binding_status"] == "unknown"
    assert telem_src["query_correlation"]["same_execution_proven"] is False

    # Identity declarations without native results do not create an execution conflict.
    exec_conflict = tmp_path / "exec-conflict.json"
    exec_conflict.write_bytes(
        hooks.canonical_event_bytes(
            {**exec_report_2, "status": "failed", "exit_code": 1}
        )
    )
    assert (
        main(
            [
                "verify",
                "report",
                "--execution-report",
                str(exec2_path),
                "--execution-report",
                str(exec_conflict),
            ]
        )
        == 0
    )

    # Tampered lefthook.stdout triggers digest mismatch conflict
    (verify_dir / "lefthook.stdout").write_bytes(b"tampered")
    assert main(["verify", "report", "--verify-dir", str(verify_dir)]) == 2


def test_verify_report_pytest_phase_failures_and_junit_invalid_durations(tmp_path):
    reportlog = tmp_path / "pytest-reportlog.jsonl"
    reportlog.write_text(
        "\n".join(
            [
                json.dumps({"$report_type": "SessionStart", "created_at": 100.0}),
                json.dumps(
                    {
                        "$report_type": "TestReport",
                        "nodeid": "tests/a.py::test_pass",
                        "when": "setup",
                        "outcome": "passed",
                        "duration": 0.01,
                    }
                ),
                json.dumps(
                    {
                        "$report_type": "TestReport",
                        "nodeid": "tests/a.py::test_pass",
                        "when": "call",
                        "outcome": "passed",
                        "duration": 0.05,
                    }
                ),
                json.dumps(
                    {
                        "$report_type": "TestReport",
                        "nodeid": "tests/a.py::test_pass",
                        "when": "teardown",
                        "outcome": "passed",
                        "duration": 0.01,
                    }
                ),
                json.dumps(
                    {
                        "$report_type": "TestReport",
                        "nodeid": "tests/a.py::test_setup_err",
                        "when": "setup",
                        "outcome": "failed",
                        "duration": 0.02,
                    }
                ),
                json.dumps(
                    {
                        "$report_type": "TestReport",
                        "nodeid": "tests/a.py::test_teardown_err",
                        "when": "setup",
                        "outcome": "passed",
                        "duration": 0.01,
                    }
                ),
                json.dumps(
                    {
                        "$report_type": "TestReport",
                        "nodeid": "tests/a.py::test_teardown_err",
                        "when": "call",
                        "outcome": "passed",
                        "duration": 0.03,
                    }
                ),
                json.dumps(
                    {
                        "$report_type": "TestReport",
                        "nodeid": "tests/a.py::test_teardown_err",
                        "when": "teardown",
                        "outcome": "failed",
                        "duration": 0.04,
                    }
                ),
                "{bad json line",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    parsed = verify_report._parse_pytest_reportlog(reportlog)
    assert parsed["testcase_counts"]["passed"] == 1
    assert parsed["testcase_counts"]["error"] == 2
    assert parsed["phase_counts"]["setup"]["failed"] == 1
    assert parsed["phase_counts"]["teardown"]["failed"] == 1
    assert parsed["malformed_line_count"] == 1
    assert parsed["has_session_finish"] is False
    assert parsed["session_wall_seconds"] == "unknown"
    assert parsed["report_completeness"] == "partial"

    bad_junit = tmp_path / "junit.xml"
    bad_junit.write_text(
        '<testsuite tests="3" failures="0" errors="0" skipped="0"><testcase name="a" time="-1.5"/>'
        '<testcase name="b" time="NaN"/><testcase name="c"/></testsuite>',
        encoding="utf-8",
    )
    parsed_j = verify_report._parse_junit_xml(bad_junit)
    assert parsed_j["total_duration_seconds"] == "invalid"
    assert parsed_j["invalid_duration_count"] == 2
    assert parsed_j["missing_duration_count"] == 1

    dtd_xml = tmp_path / "dtd.xml"
    dtd_xml.write_text('<!DOCTYPE foo [<!ENTITY x "y">]><testsuite/>', encoding="utf-8")
    assert verify_report._parse_junit_xml(dtd_xml)["status"] == "rejected_dtd"


def test_verify_report_out_refuses_to_overwrite_inputs_or_aliases(tmp_path, capsys):
    report_in = tmp_path / "exec.json"
    original_bytes = hooks.canonical_event_bytes(
        {
            "kind": "engineering-ci-execution-report",
            "schema_version": 1,
            "status": "passed",
            "exit_code": 0,
        }
    )
    report_in.write_bytes(original_bytes)

    # Same file directly
    assert (
        main(
            [
                "verify",
                "report",
                "--execution-report",
                str(report_in),
                "--out",
                str(report_in),
            ]
        )
        == 2
    )
    assert report_in.read_bytes() == original_bytes
    assert "refuses to overwrite" in capsys.readouterr().err

    # Symlink alias target protection when supported
    alias = tmp_path / "alias.json"
    try:
        os.symlink(report_in, alias)
    except OSError:
        alias = None
    if alias is not None:
        assert (
            main(
                [
                    "verify",
                    "report",
                    "--execution-report",
                    str(report_in),
                    "--out",
                    str(alias),
                ]
            )
            == 2
        )
        assert report_in.read_bytes() == original_bytes

    # Bundle directory / hook eci-hooks store protection
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "report.json").write_bytes(original_bytes)
    assert (
        main(
            [
                "verify",
                "report",
                "--execution-report",
                str(bundle),
                "--out",
                str(bundle / "unified.json"),
            ]
        )
        == 2
    )


def test_verify_report_full_execution_matched_across_hook_bundle_manifest_and_bound_telemetry(
    tmp_path,
):
    repo = tmp_path / "repo"
    head, inv_file, binding = _create_split_precommit_repo(repo)
    inv_dir = inv_file.parent

    exec_data = json.loads(
        inv_file.with_suffix(".execution-check-whitespace").read_text(encoding="utf-8")
    )
    rep = exec_data["report"]

    # 1. Exact split-check bundle shares concrete execution with Hook (whitespace),
    # while realistic #87 Telemetry receipt (no fabricated execution_ref/run_ref)
    # stays in upper trace context (same_execution_proven=False) with full Span/Session metadata.
    exact_bundle = tmp_path / "bundle-exact"
    exact_bundle.mkdir()
    (exact_bundle / "report.json").write_bytes(hooks.canonical_event_bytes(rep))

    telem = tmp_path / "telem-bound.json"
    telem.write_text(
        json.dumps(
            {
                "kind": verify_report.dagger_telemetry.NATIVE_TRACE_RECEIPT_KIND,
                "schema_version": verify_report.dagger_telemetry.CONTRACT_VERSION,
                "correlation": {
                    "status": "bound",
                    "matched_trace_id": "trace-live-1",
                    "matched_session_id": "sess-live-1",
                    "matched_engine_instance_id": "eng-live-1",
                    "anchor_span_id": "span-anchor-1",
                    "dagql_span_id": "span-dagql-1",
                    "core_span_id": "span-core-1",
                    "lineage_span_ids": [
                        "span-core-1",
                        "span-dagql-1",
                        "span-anchor-1",
                    ],
                },
                "source_binding": {
                    "source_binding_status": "unknown",
                    "declared_target_source_ref": "sha256:" + "d" * 64,
                },
                "git_commit": head,
            }
        ),
        encoding="utf-8",
    )

    out_report = tmp_path / "out" / "matched.json"
    assert (
        main(
            [
                "verify",
                "report",
                "--hook-root",
                str(repo),
                "--invocation-ref",
                binding.invocation_ref,
                "--execution-report",
                str(exact_bundle),
                "--dagger-telemetry",
                str(telem),
                "--out",
                str(out_report),
            ]
        )
        == 0
    )
    matched = json.loads(out_report.read_text(encoding="utf-8"))
    assert matched["correlation"]["overall_status"] == "partial_execution_matched"
    assert matched["correlation"]["scoped_conflicts"] == []
    hook_entry = next(
        s for s in matched["sources"] if s["source_type"] == "hook_invocation"
    )
    bundle_entry = next(
        s for s in matched["sources"] if s["source_type"] == "execution_bundle"
    )
    telem_entry = next(
        s for s in matched["sources"] if s["source_type"] == "dagger_telemetry"
    )
    assert hook_entry["query_correlation"]["strength"] == "multi_execution_container"
    assert hook_entry["query_correlation"]["same_execution_proven"] is False
    assert hook_entry["query_correlation"]["matched_unit_count"] == 1
    assert hook_entry["query_correlation"]["unqueried_unit_count"] == 1
    assert {
        u["scope"]: u["status"]
        for u in hook_entry["query_correlation"]["execution_units"]
    } == {
        "execution-check-whitespace": "matched",
        "execution-check-forwarding": "unqueried",
    }
    assert bundle_entry["query_correlation"]["strength"] == "execution_matched"
    assert telem_entry["matched_session_id"] == "sess-live-1"
    assert telem_entry["anchor_span_id"] == "span-anchor-1"
    assert telem_entry["query_correlation"]["same_execution_proven"] is False
    assert len(hook_entry["executions"]) == 2
    assert hook_entry["receipt"]["native_status"] == "matched"
    assert hook_entry["receipt"]["verifiable_evidence_status"] == "verified"

    # P1-01: Split-check Hook keeps consistent multi_execution_container identity across 0, 1, and 2 queried reports.
    split_zero = verify_report.build_verify_report(
        hook_root=repo,
        invocation_ref=binding.invocation_ref,
    )
    hook_zero = next(
        s for s in split_zero["sources"] if s["source_type"] == "hook_invocation"
    )
    assert split_zero["correlation"]["overall_status"] == "uncorrelated"
    assert hook_zero["query_correlation"]["strength"] == "multi_execution_container"
    assert hook_zero["query_correlation"]["same_execution_proven"] is False
    assert hook_zero["query_correlation"]["matched_unit_count"] == 0
    assert hook_zero["query_correlation"]["unqueried_unit_count"] == 2
    assert {
        u["scope"]: u["status"]
        for u in hook_zero["query_correlation"]["execution_units"]
    } == {
        "execution-check-whitespace": "unqueried",
        "execution-check-forwarding": "unqueried",
    }

    pure_match = verify_report.build_verify_report(
        hook_root=repo,
        invocation_ref=binding.invocation_ref,
        execution_reports=[exact_bundle],
    )
    assert pure_match["correlation"]["overall_status"] == "partial_execution_matched"
    assert (
        pure_match["correlation"]["reason"]
        == "multi_execution_container_partially_matched"
    )
    hook_pure = next(
        s for s in pure_match["sources"] if s["source_type"] == "hook_invocation"
    )
    bundle_pure = next(
        s for s in pure_match["sources"] if s["source_type"] == "execution_bundle"
    )
    assert hook_pure["query_correlation"]["strength"] == "multi_execution_container"
    assert hook_pure["query_correlation"]["same_execution_proven"] is False
    assert hook_pure["query_correlation"]["matched_unit_count"] == 1
    assert hook_pure["query_correlation"]["unqueried_unit_count"] == 1
    assert {
        u["scope"]: u["status"]
        for u in hook_pure["query_correlation"]["execution_units"]
    } == {
        "execution-check-whitespace": "matched",
        "execution-check-forwarding": "unqueried",
    }
    assert bundle_pure["query_correlation"]["strength"] == "execution_matched"
    assert bundle_pure["query_correlation"]["same_execution_proven"] is True

    # Identical Report copy yields overall execution_matched.
    exact_bundle_copy = tmp_path / "bundle-exact-copy"
    exact_bundle_copy.mkdir()
    (exact_bundle_copy / "report.json").write_bytes(hooks.canonical_event_bytes(rep))
    copy_match = verify_report.build_verify_report(
        execution_reports=[exact_bundle, exact_bundle_copy]
    )
    assert copy_match["correlation"]["overall_status"] == "execution_matched"
    assert all(
        s["query_correlation"]["same_execution_proven"] is True
        for s in copy_match["sources"]
    )

    # Single-execution Hook (split_checks=False) + its exact Report yields execution_matched.
    assert hooks.main(["capture", "pre-commit", "--root", str(repo)]) == 0
    single_inv_file = max(inv_dir.glob("*.json"), key=lambda p: p.stat().st_mtime_ns)
    if single_inv_file == inv_file:
        single_inv_file = next(p for p in inv_dir.glob("*.json") if p != inv_file)
    single_bind = hooks.load_invocation_binding(single_inv_file)
    assert hook_checks.prepare(repo, single_bind, split_checks=False)["exit_code"] == 0
    assert hook_checks.run_checks(repo, single_bind)["exit_code"] == 0
    assert hook_checks.reconcile(repo, single_bind)["exit_code"] == 0
    assert hook_checks.consume_index_result(repo, single_bind)["exit_code"] == 0
    single_exec = json.loads(
        single_inv_file.with_suffix(".execution").read_text(encoding="utf-8")
    )
    single_bundle = tmp_path / "bundle-single-hook"
    single_bundle.mkdir()
    (single_bundle / "report.json").write_bytes(
        hooks.canonical_event_bytes(single_exec["report"])
    )
    single_hook_match = verify_report.build_verify_report(
        hook_root=repo,
        invocation_ref=single_bind.invocation_ref,
        execution_reports=[single_bundle],
    )
    assert single_hook_match["correlation"]["overall_status"] == "execution_matched"
    assert all(
        s["query_correlation"]["strength"] == "execution_matched"
        and s["query_correlation"]["same_execution_proven"] is True
        for s in single_hook_match["sources"]
    )

    # P1-01: same split-check Hook H({whitespace, forwarding}) + BOTH native Reports MUST NOT bridge A and B.
    fwd_exec_data = json.loads(
        inv_file.with_suffix(".execution-check-forwarding").read_text(encoding="utf-8")
    )
    fwd_bundle = tmp_path / "bundle-fwd-exact"
    fwd_bundle.mkdir()
    (fwd_bundle / "report.json").write_bytes(
        hooks.canonical_event_bytes(fwd_exec_data["report"])
    )
    split_both = verify_report.build_verify_report(
        hook_root=repo,
        invocation_ref=binding.invocation_ref,
        execution_reports=[exact_bundle, fwd_bundle],
    )
    assert split_both["correlation"]["overall_status"] == "partial_execution_matched"
    assert (
        split_both["correlation"]["reason"] == "multiple_disconnected_execution_groups"
    )
    assert len(split_both["correlation"]["execution_groups"]) == 2
    for grp in split_both["correlation"]["execution_groups"]:
        assert len(grp["source_indices"]) == 2
        assert 0 in grp["source_indices"]
    hook_both = next(
        s for s in split_both["sources"] if s["source_type"] == "hook_invocation"
    )
    ws_both, fwd_both = [
        s for s in split_both["sources"] if s["source_type"] == "execution_bundle"
    ]
    assert hook_both["query_correlation"]["strength"] == "multi_execution_container"
    assert hook_both["query_correlation"]["same_execution_proven"] is False
    assert hook_both["query_correlation"]["matched_unit_count"] == 2
    assert hook_both["query_correlation"]["unqueried_unit_count"] == 0
    assert hook_both["query_correlation"]["execution_group_indices"] == [0, 1]
    assert ws_both["query_correlation"]["strength"] == "execution_matched"
    assert fwd_both["query_correlation"]["strength"] == "execution_matched"
    assert {
        ws_both["query_correlation"]["execution_group_index"],
        fwd_both["query_correlation"]["execution_group_index"],
    } == {0, 1}
    assert fwd_exec_data["report"]["execution_identity"][
        "execution_ref"
    ] not in ws_both["query_correlation"]["execution_keys"].get("execution_ref", [])

    # Disconnected pairs (A<->B and C<->D) must NEVER yield overall execution_matched.
    rep_c = single_exec["report"]
    p_c = tmp_path / "rep_c.json"
    p_d = tmp_path / "rep_d.json"
    p_c.write_bytes(hooks.canonical_event_bytes(rep_c))
    p_d.write_bytes(hooks.canonical_event_bytes(rep_c))
    disconn = verify_report.build_verify_report(
        hook_root=repo,
        invocation_ref=binding.invocation_ref,
        execution_reports=[exact_bundle, p_c, p_d],
    )
    assert disconn["correlation"]["overall_status"] == "partial_execution_matched"
    assert disconn["correlation"]["reason"] == "multiple_disconnected_execution_groups"
    assert len(disconn["correlation"]["execution_groups"]) == 2

    # Same run_attempt or same matched_trace_id alone stays shared_context_only, never execution_matched.
    ctx_a = tmp_path / "ctx_a.json"
    ctx_b = tmp_path / "ctx_b.json"
    ctx_a.write_bytes(hooks.canonical_event_bytes(rep))
    ctx_b.write_bytes(hooks.canonical_event_bytes(fwd_exec_data["report"]))
    ctx_only = verify_report.build_verify_report(execution_reports=[ctx_a, ctx_b])
    assert ctx_only["correlation"]["overall_status"] == "shared_context_only"
    assert all(
        s["query_correlation"]["same_execution_proven"] is False
        for s in ctx_only["sources"]
    )

    trace_1 = tmp_path / "trace1.json"
    trace_2 = tmp_path / "trace2.json"
    trace_1.write_text(
        json.dumps(
            {
                "kind": verify_report.dagger_telemetry.NATIVE_TRACE_RECEIPT_KIND,
                "schema_version": verify_report.dagger_telemetry.CONTRACT_VERSION,
                "correlation": {
                    "status": "bound",
                    "matched_trace_id": "trace-shared-1",
                    "anchor_span_id": "span-1",
                },
            }
        ),
        encoding="utf-8",
    )
    trace_2.write_text(
        json.dumps(
            {
                "kind": verify_report.dagger_telemetry.NATIVE_TRACE_RECEIPT_KIND,
                "schema_version": verify_report.dagger_telemetry.CONTRACT_VERSION,
                "correlation": {
                    "status": "bound",
                    "matched_trace_id": "trace-shared-1",
                    "anchor_span_id": "span-2",
                },
            }
        ),
        encoding="utf-8",
    )
    trace_res = verify_report.build_verify_report(dagger_telemetries=[trace_1, trace_2])
    assert trace_res["correlation"]["overall_status"] == "shared_context_only"

    # 2. Real #86 PlanExecution bundle: tests/ + checks/ + producer_artifact_handoff + host/report.json.
    from engineering_ci import execution as eci_execution

    # P1-02: two distinct bundles producing identical JUnit XML bytes MUST stay two separate execution groups.
    shared_junit = (
        b'<testsuite tests="1" failures="0" errors="0" skipped="0" time="0.1"><testcase name="t_same" time="0.1">'
        b'<properties><property name="verifies" value="AC-202"/></properties>'
        b"</testcase></testsuite>"
    )
    shared_junit_sha = hashlib.sha256(shared_junit).hexdigest()
    b_sha1 = tmp_path / "bundle-same-junit-1"
    b_sha2 = tmp_path / "bundle-same-junit-2"
    for b_idx, b_dir in enumerate((b_sha1, b_sha2), start=1):
        (b_dir / "tests").mkdir(parents=True)
        (b_dir / "tests" / "junit.xml").write_bytes(shared_junit)
        (b_dir / "report.json").write_bytes(
            hooks.canonical_event_bytes(
                {
                    "kind": "engineering-ci-execution-report",
                    "schema_version": 1,
                    "status": "passed",
                    "exit_code": 0,
                    "checks": [],
                    "execution_identity": {
                        "run_ref": f"run:sha-{b_idx}",
                        "attempt_ref": "att:1",
                        "execution_ref": f"exec:sha-{b_idx}",
                    },
                    "junit": shared_junit.decode("utf-8"),
                }
            )
        )
    same_junit_bundles = verify_report.build_verify_report(
        execution_reports=[b_sha1, b_sha2]
    )
    assert same_junit_bundles["correlation"]["overall_status"] == "content_equivalent"
    assert not same_junit_bundles["correlation"]["execution_groups"]
    j_sha_sources = [
        s for s in same_junit_bundles["sources"] if s["source_type"] == "junit_xml"
    ]
    assert len(j_sha_sources) == 2
    assert all(
        not j["query_correlation"]["same_execution_proven"] for j in j_sha_sources
    )
    for j in j_sha_sources:
        assert j["bundle_binding"] == "verified_manifest_digest"
        assert "artifact_digest" not in j["query_correlation"]["execution_keys"]
        assert j["query_correlation"]["content_keys"]["artifact_sha256"] == [
            shared_junit_sha
        ]
    standalone_same_junit = verify_report.build_verify_report(
        junit_xmls=[b_sha1 / "tests" / "junit.xml", b_sha2 / "tests" / "junit.xml"]
    )
    assert (
        standalone_same_junit["correlation"]["overall_status"] == "content_equivalent"
    )
    assert all(
        s["query_correlation"]["same_execution_proven"] is False
        for s in standalone_same_junit["sources"]
    )

    real_86 = tmp_path / "bundle-86-real"
    (real_86 / "tests").mkdir(parents=True)
    (real_86 / "checks" / "weaver_registry").mkdir(parents=True)
    (real_86 / "host").mkdir()
    host_raw86 = json.dumps({"status": "passed", "exit_code": 0}).encode("utf-8")
    (real_86 / "host" / "report.json").write_bytes(host_raw86)
    junit_bytes = (
        b'<testsuite tests="1" failures="0" errors="0" skipped="0" time="0.1"><testcase name="t1" time="0.1">'
        b'<properties><property name="verifies" value="AC-101"/></properties>'
        b"</testcase></testsuite>"
    )
    (real_86 / "tests" / "junit.xml").write_bytes(junit_bytes)
    (real_86 / "tests" / "report.json").write_text(
        json.dumps({"status": "passed", "exit_code": 0}), encoding="utf-8"
    )
    (real_86 / "checks" / "weaver_registry" / "check-result.json").write_text(
        json.dumps({"status": "passed", "exit_code": 0}), encoding="utf-8"
    )

    scratch_86 = tmp_path / "scratch-86-art"
    gen_dir = scratch_86 / "prerequisite_action" / "codegen" / "generated"
    gen_dir.mkdir(parents=True)
    (gen_dir / "out.py").write_text("GENERATED = 1\n", encoding="utf-8")
    (gen_dir / "helper.py").write_text("HELPER = 2\n", encoding="utf-8")
    stamp_file = scratch_86 / "prerequisite_action" / "stamp" / "meta" / "stamp.txt"
    stamp_file.parent.mkdir(parents=True)
    stamp_file.write_text("STAMP = 1\n", encoding="utf-8")

    dir_out_ref = {
        "kind": "directory",
        "path": "generated",
        "sha256": eci_execution.artifact_directory_id(gen_dir),
    }
    file_out_ref = {
        "kind": "file",
        "path": "meta/stamp.txt",
        "sha256": hashlib.sha256(stamp_file.read_bytes()).hexdigest(),
    }
    closure_records = [
        {
            "producer_ref": {"kind": "prerequisite_action", "name": "codegen"},
            "consumer_ref": {"kind": "check", "name": "whitespace"},
            "dependency_kind": "data",
            "output_ref": dir_out_ref,
            "sha256": dir_out_ref["sha256"],
            "verified": True,
        },
        {
            "producer_ref": {"kind": "prerequisite_action", "name": "stamp"},
            "consumer_ref": {"kind": "check", "name": "whitespace"},
            "dependency_kind": "data",
            "output_ref": file_out_ref,
            "sha256": file_out_ref["sha256"],
            "verified": True,
        },
    ]
    retention_meta = eci_execution.retain_closure_artifacts(
        scratch_86, real_86, closure_records
    )
    plan86 = {
        "kind": plan_contract.PLAN_KIND,
        "schema_version": 2,
        "generator": {"config_sha256": "a" * 64},
        "git": {
            "mode": "index",
            "snapshot": "index",
            "head_commit": head,
            "source_tree_id": "sha256:" + "c" * 64,
            "index_tree": "1" * 40,
            "execution_tree": "1" * 40,
        },
        "selection": {
            "checks": ["whitespace"],
            "tests": [],
            "prerequisites": ["codegen", "stamp"],
        },
        "prerequisite_actions": {
            "codegen": {"type": "command", "argv": ["python", "-c", "0"]},
            "stamp": {"type": "command", "argv": ["python", "-c", "0"]},
        },
        "dependency_contract": plan_contract.graph_contract(
            [
                {
                    "producer_ref": rec["producer_ref"],
                    "consumer_ref": rec["consumer_ref"],
                    "dependency_kind": "data",
                    "requires_pass": True,
                    "output_ref": rec["output_ref"],
                    "source_binding_ref": "git",
                    "declaration_ref": {
                        "path": "ci-selection.toml",
                        "sha256": "a" * 64,
                        "section": f"prerequisite_actions.{rec['producer_ref']['name']}",
                    },
                    "selection": {"selected_by": "test", "escalation_reason": None},
                    "execution_phase": "pre_execution",
                    "resolution": "resolved",
                    "unresolved_reason": None,
                }
                for rec in closure_records
            ]
        ),
    }
    plan86_bytes = hooks.canonical_event_bytes(plan86)
    (real_86 / "plan.json").write_bytes(plan86_bytes)
    exec86_ident = {
        "run_ref": "run:real-86",
        "attempt_ref": "att:1",
        "execution_ref": "exec:real-86",
    }
    prereq_results86 = [
        {
            "name": "codegen",
            "type": "command",
            "status": "passed",
            "exit_code": 0,
            "reason": None,
        },
        {
            "name": "stamp",
            "type": "command",
            "status": "passed",
            "exit_code": 0,
            "reason": None,
        },
    ]
    handoff_payload = eci_execution.build_producer_artifact_handoff(
        plan86,
        closure_records=closure_records,
        prerequisite_results=prereq_results86,
        execution_identity=exec86_ident,
        execution_context="dagger_export",
        retention_metadata=retention_meta,
    )
    real_86_rep = {
        "kind": "engineering-ci-execution-report",
        "schema_version": 1,
        "status": "passed",
        "exit_code": 0,
        "plan_ref": hooks._digest_bytes(plan86_bytes),
        "targets": [],
        "prerequisites": prereq_results86,
        "artifact_closure": closure_records,
        "checks": [
            {
                "name": "whitespace",
                "status": "passed",
                "plan_authorized": True,
                "check_contract": {
                    "kind": "engineering-ci-check-contract",
                    "schema_version": 1,
                    "status": "valid",
                },
            }
        ],
        "execution_identity": exec86_ident,
        "plan": {"generator": plan86["generator"], "git": plan86["git"]},
        "source_handoff": {
            "host_report_sha256": hashlib.sha256(host_raw86).hexdigest()
        },
        "junit": junit_bytes.decode("utf-8"),
        "producer_artifact_handoff": handoff_payload,
    }
    (real_86 / "report.json").write_bytes(hooks.canonical_event_bytes(real_86_rep))
    (real_86 / "exit-code.txt").write_text("0\n", encoding="utf-8")
    real_86_res = verify_report.build_verify_report(execution_reports=[real_86])
    assert real_86_res["correlation"]["execution_groups"] == []
    b86_src = next(
        s for s in real_86_res["sources"] if s["source_type"] == "execution_bundle"
    )
    assert b86_src["report_contract_status"] == "valid"
    assert b86_src["plan_binding_status"] == "declared_plan_ref_matches"
    assert b86_src["admission_authority_63"] == "not_evaluated"
    assert b86_src["producer_artifact_handoff_status"] == "verified"
    assert (
        b86_src["producer_artifact_handoff"]["admission_authority_63"]
        == "not_evaluated"
    )
    verified_paths = {
        a["bundle_path"]: set(a["file_digests"])
        for a in b86_src["producer_artifact_handoff"]["artifacts"]
    }
    assert verified_paths == {
        "produced/prerequisite_action/codegen/generated": {
            "generated/helper.py",
            "generated/out.py",
        },
        "produced/prerequisite_action/stamp/meta/stamp.txt": {"meta/stamp.txt"},
    }
    assert "tests/report.json" in b86_src["sub_reports"]
    assert "checks/weaver_registry/check-result.json" in b86_src["sub_reports"]
    j86_src = next(s for s in real_86_res["sources"] if s["source_type"] == "junit_xml")
    assert j86_src["bundle_binding"] == "verified_manifest_digest"
    assert j86_src["bundle_relative_path"] == "tests/junit.xml"
    assert j86_src["testcase_verifies"] == [
        {"classname": "", "name": "t1", "values": ["AC-101"]}
    ]
    assert (
        j86_src["testcase_properties_summary"]["verifies_authority"]
        == "raw_junit_diagnostic_only_not_c2_delivery"
    )

    # P1-03 negative: missing plan.json MUST NOT report handoff as verified.
    (real_86 / "plan.json").unlink()
    missing_plan_86 = verify_report.build_verify_report(execution_reports=[real_86])
    assert missing_plan_86["correlation"]["overall_status"] == "mismatch_detected"
    miss_b86 = next(
        s for s in missing_plan_86["sources"] if s["source_type"] == "execution_bundle"
    )
    assert miss_b86["producer_artifact_handoff_status"] == "unverified_missing_plan"
    assert any(
        c["reason"] == "bundle_producer_handoff_plan_missing"
        for c in miss_b86["internal_conflicts"]
    )
    (real_86 / "plan.json").write_bytes(plan86_bytes)

    # Artificial source_handoff.artifacts & XML run/attempt props MUST remain colocated_unverified.
    fake_bundle = tmp_path / "bundle-fake"
    (fake_bundle / "tests").mkdir(parents=True)
    fake_xml = (
        b'<testsuite tests="1" failures="0" errors="0" skipped="0" time="0.1"><properties>'
        b'<property name="run_ref" value="run:fake-1"/>'
        b'<property name="attempt_ref" value="att:1"/>'
        b'</properties><testcase name="t1" time="0.1"/></testsuite>'
    )
    (fake_bundle / "tests" / "junit.xml").write_bytes(fake_xml)
    (fake_bundle / "report.json").write_bytes(
        hooks.canonical_event_bytes(
            {
                "kind": "engineering-ci-execution-report",
                "schema_version": 1,
                "status": "passed",
                "exit_code": 0,
                "checks": [],
                "execution_identity": {
                    "run_ref": "run:fake-1",
                    "attempt_ref": "att:1",
                    "execution_ref": "exec:fake-1",
                },
                "source_handoff": {
                    "artifacts": [
                        {
                            "bundle_path": "tests/junit.xml",
                            "sha256": hashlib.sha256(fake_xml).hexdigest(),
                        }
                    ]
                },
            }
        )
    )
    fake_res = verify_report.build_verify_report(execution_reports=[fake_bundle])
    fake_j = next(s for s in fake_res["sources"] if s["source_type"] == "junit_xml")
    assert fake_j["bundle_binding"] == "colocated_unverified"
    assert fake_j["query_correlation"]["same_execution_proven"] is False

    # P1-03 negative: truncated directory, extra unexpected file, and tampered file bytes fail closed.
    retained_helper = (
        real_86
        / "produced"
        / "prerequisite_action"
        / "codegen"
        / "generated"
        / "helper.py"
    )
    saved_helper = retained_helper.read_bytes()
    retained_helper.unlink()
    trunc_86 = verify_report.build_verify_report(execution_reports=[real_86])
    assert trunc_86["correlation"]["overall_status"] == "mismatch_detected"
    assert (
        next(s for s in trunc_86["sources"] if s["source_type"] == "execution_bundle")[
            "producer_artifact_handoff"
        ]["error"]
        == "producer_directory_truncated"
    )
    retained_helper.write_bytes(saved_helper)

    extra_file = retained_helper.with_name("extra.py")
    extra_file.write_text("EXTRA = 1\n", encoding="utf-8")
    extra_86 = verify_report.build_verify_report(execution_reports=[real_86])
    assert extra_86["correlation"]["overall_status"] == "mismatch_detected"
    extra_file.unlink()

    retained_stamp = (
        real_86 / "produced" / "prerequisite_action" / "stamp" / "meta" / "stamp.txt"
    )
    retained_stamp.write_text("TAMPERED\n", encoding="utf-8")
    tampered_86 = verify_report.build_verify_report(execution_reports=[real_86])
    assert tampered_86["correlation"]["overall_status"] == "mismatch_detected"


def test_verify_report_p1_hook_integrity_multiref_push_and_p2_fixes(tmp_path):
    # P1-04: hook missing all executions & report_digest tamper & invalid receipt & P1-02 multi-ref push.
    from test_native_hooks import INDEX_SELECTION, _approve_profiles, _source

    repo = tmp_path / "push-repo"
    repo.mkdir()
    _init_git_repo(repo)
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=repo, check=True)
    mod = _source(repo, "mod", ("whitespace",))
    mod.write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
    _approve_profiles(
        repo,
        INDEX_SELECTION + '\n[push]\nnew_ref_baseline = "refs/remotes/origin/main"\n'
        'effective_profiles = ["push-alpha.json", "push-beta.json"]\n',
        [
            ("ci-effective-profile.json", "index", "index", "HEAD", ("whitespace",)),
            ("push-alpha.json", "push", "update", "refs/heads/alpha", ("whitespace",)),
            ("push-beta.json", "push", "create", "refs/heads/beta", ("whitespace",)),
        ],
    )
    subprocess.run(
        [
            "git",
            "add",
            "ci-effective-profile.json",
            "push-alpha.json",
            "push-beta.json",
            "ci-hygiene.toml",
            "ci-selection.toml",
            "src",
            "tach.toml",
        ],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "commit", "-qm", "profile"], cwd=repo, check=True)
    baseline = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", baseline],
        cwd=repo,
        check=True,
    )
    mod.write_text("VALUE = 2\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "src/mod.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "alpha"], cwd=repo, check=True)
    p1 = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    subprocess.run(
        ["git", "checkout", "-qb", "beta-branch", baseline], cwd=repo, check=True
    )
    mod.write_text("VALUE = 3\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "src/mod.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "beta"], cwd=repo, check=True)
    p2 = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()

    zero = "0" * 40
    ev = hooks.capture_pre_push(
        repo,
        "origin",
        "https://example.invalid/repo.git",
        f"refs/heads/alpha {p1} refs/heads/alpha {baseline}\nrefs/heads/beta {p2} refs/heads/beta {zero}\n",
    )
    b_push = hooks.persist_event(repo, ev)
    b_push_bind = hooks.load_invocation_binding(b_push.invocation_path)
    assert (
        hook_checks.prepare_push(
            repo,
            b_push_bind,
            effective_profiles=["push-alpha.json", "push-beta.json"],
        )["exit_code"]
        == 0
    )
    assert hook_checks.run_push_checks(repo, b_push_bind)["exit_code"] == 0
    assert hook_checks.reconcile_push(repo, b_push_bind)["exit_code"] == 0
    assert hook_checks.consume_push_result(repo, b_push_bind)["exit_code"] == 0

    push_report = verify_report.build_verify_report(
        hook_root=repo, invocation_ref=b_push.invocation_ref
    )
    push_src = push_report["sources"][0]
    assert push_report["correlation"]["scoped_conflicts"] == []
    assert push_report["correlation"]["overall_status"] == "uncorrelated"
    assert push_src["query_correlation"]["strength"] == "multi_execution_container"
    assert push_src["query_correlation"]["same_execution_proven"] is False
    assert push_src["query_correlation"]["matched_unit_count"] == 0
    assert push_src["query_correlation"]["unqueried_unit_count"] == 2
    assert len(push_src["executions"]) == 2
    assert push_src["receipt"]["native_status"] == "matched"
    assert push_src["receipt"]["validation_status"] == "valid"
    assert push_src["receipt"]["verifiable_evidence_status"] == "verified"

    # Negative P1-04: unlink ALL .execution-* files while .receipt stays matched -> missing_executions conflict!
    exec_0 = b_push.invocation_path.with_suffix(".execution-0")
    saved_0 = exec_0.read_bytes()
    exec_1 = b_push.invocation_path.with_suffix(".execution-1")
    saved_1 = exec_1.read_bytes()
    exec_0.unlink()
    exec_1.unlink()
    missing_exec_rep = verify_report.build_verify_report(
        hook_root=repo, invocation_ref=b_push.invocation_ref
    )
    assert missing_exec_rep["correlation"]["overall_status"] == "mismatch_detected"
    assert missing_exec_rep["sources"][0]["receipt"]["native_status"] == "matched"
    assert (
        missing_exec_rep["sources"][0]["receipt"]["verifiable_evidence_status"]
        == "missing_executions"
    )

    # Restore and tamper report body without recomputing report_digest -> digest mismatch!
    exec_0.write_bytes(saved_0)
    exec_1.write_bytes(saved_1)
    tampered_payload = json.loads(saved_0.decode("utf-8"))
    tampered_payload["report"]["exit_code"] = 1
    exec_0.write_bytes(hooks.canonical_event_bytes(tampered_payload))
    tampered_digest_rep = verify_report.build_verify_report(
        hook_root=repo, invocation_ref=b_push.invocation_ref
    )
    assert tampered_digest_rep["correlation"]["overall_status"] == "mismatch_detected"
    assert any(
        c["reason"] == "hook_execution_report_digest_mismatch"
        for c in tampered_digest_rep["sources"][0]["internal_conflicts"]
    )

    # P1-05: call passed without teardown, retries preserved, scoped verifies properties zero conflicts!
    rl_p105 = tmp_path / "p105.jsonl"
    rl_p105.write_text(
        "\n".join(
            [
                json.dumps({"$report_type": "SessionStart", "created_at": 10.0}),
                json.dumps(
                    {
                        "$report_type": "TestReport",
                        "nodeid": "t.py::t_incomp",
                        "when": "setup",
                        "outcome": "passed",
                        "duration": 0.01,
                    }
                ),
                json.dumps(
                    {
                        "$report_type": "TestReport",
                        "nodeid": "t.py::t_incomp",
                        "when": "call",
                        "outcome": "passed",
                        "duration": 0.02,
                    }
                ),
                json.dumps(
                    {
                        "$report_type": "TestReport",
                        "nodeid": "t.py::t_skip_setup",
                        "when": "setup",
                        "outcome": "skipped",
                        "duration": 0.01,
                    }
                ),
                json.dumps(
                    {
                        "$report_type": "TestReport",
                        "nodeid": "t.py::t_skip_setup",
                        "when": "teardown",
                        "outcome": "passed",
                        "duration": 0.01,
                    }
                ),
                json.dumps(
                    {
                        "$report_type": "TestReport",
                        "nodeid": "t.py::t_retry",
                        "when": "setup",
                        "outcome": "passed",
                        "duration": 0.01,
                        "user_properties": [["verifies", "AC-1"]],
                    }
                ),
                json.dumps(
                    {
                        "$report_type": "TestReport",
                        "nodeid": "t.py::t_retry",
                        "when": "call",
                        "outcome": "failed",
                        "duration": 0.02,
                    }
                ),
                json.dumps(
                    {
                        "$report_type": "TestReport",
                        "nodeid": "t.py::t_retry",
                        "when": "teardown",
                        "outcome": "passed",
                        "duration": 0.01,
                    }
                ),
                json.dumps(
                    {
                        "$report_type": "TestReport",
                        "nodeid": "t.py::t_retry",
                        "when": "setup",
                        "outcome": "passed",
                        "duration": 0.01,
                    }
                ),
                json.dumps(
                    {
                        "$report_type": "TestReport",
                        "nodeid": "t.py::t_retry",
                        "when": "call",
                        "outcome": "passed",
                        "duration": 0.03,
                    }
                ),
                json.dumps(
                    {
                        "$report_type": "TestReport",
                        "nodeid": "t.py::t_retry",
                        "when": "teardown",
                        "outcome": "passed",
                        "duration": 0.01,
                    }
                ),
                json.dumps(
                    {
                        "$report_type": "TestReport",
                        "nodeid": "t.py::t_prop2",
                        "when": "setup",
                        "outcome": "passed",
                        "duration": 0.01,
                        "user_properties": [["verifies", "AC-2"]],
                    }
                ),
                json.dumps(
                    {
                        "$report_type": "TestReport",
                        "nodeid": "t.py::t_prop2",
                        "when": "call",
                        "outcome": "passed",
                        "duration": 0.01,
                    }
                ),
                json.dumps(
                    {
                        "$report_type": "TestReport",
                        "nodeid": "t.py::t_prop2",
                        "when": "teardown",
                        "outcome": "passed",
                        "duration": 0.01,
                    }
                ),
                json.dumps(
                    {
                        "$report_type": "SessionFinish",
                        "exitstatus": 0,
                        "created_at": 11.0,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    p105_res = verify_report._parse_pytest_reportlog(rl_p105)
    assert p105_res["internal_conflicts"] == []
    assert p105_res["testcase_counts"]["incomplete"] == 1
    assert p105_res["outcome_breakdown"]["call_passed_teardown_missing"] == 1
    assert p105_res["testcase_counts"]["skipped"] == 1
    assert p105_res["outcome_breakdown"]["setup_skipped"] == 1
    assert p105_res["testcase_counts"]["repeated_ambiguous"] == 1
    assert p105_res["repeated_phase_record_count"] >= 3
    assert p105_res["testcase_counts"]["passed"] == 1
    assert p105_res["report_completeness"] == "partial"
    assert p105_res["testcase_properties_summary"]["property_names"] == ["verifies"]
    assert (
        p105_res["testcase_properties_summary"]["verifies_authority"]
        == "raw_reportlog_diagnostic_only_not_c2_delivery"
    )
    assert p105_res["testcase_verifies"] == [
        {"nodeid": "t.py::t_retry", "values": ["AC-1"]},
        {"nodeid": "t.py::t_prop2", "values": ["AC-2"]},
    ]
    assert p105_res["testcase_user_properties"][0]["nodeid"] == "t.py::t_retry"

    # P2-1: file between max_bytes // 4 and max_bytes must NOT truncate!
    med_stdout = tmp_path / "medium.stdout"
    med_body = (
        "output line\n" * 40
    ) + "summary: (done in 0.25 seconds)\n✓ med-job (0.25 seconds)\n"
    assert 300 < len(med_body.encode("utf-8")) < 1024
    med_stdout.write_text(med_body, encoding="utf-8")
    orig_max = verify_report.MAX_OUTPUT_PARSE_BYTES
    verify_report.MAX_OUTPUT_PARSE_BYTES = 1024
    try:
        med_parsed = verify_report.parse_lefthook_output(med_stdout)
    finally:
        verify_report.MAX_OUTPUT_PARSE_BYTES = orig_max
    assert med_parsed["output_truncated"] is False
    assert med_parsed["jobs"][0]["name"] == "med-job"

    # P2-3: missing required stream in verify-timing.json reports explicit diagnostic.
    missing_stream_dir = tmp_path / "verify-missing-stderr"
    missing_stream_dir.mkdir()
    sout = b"summary: (done in 0.1 seconds)\n"
    (missing_stream_dir / "lefthook.stdout").write_bytes(sout)
    (missing_stream_dir / "verify-timing.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "invocation_id": "v-miss",
                "native_exit_code": 0,
                "elapsed_seconds": 0.1,
                "time_source_verified": False,
                "native_outputs": {
                    "stdout": {
                        "path": "lefthook.stdout",
                        "sha256": hashlib.sha256(sout).hexdigest(),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    miss_entry = verify_report._load_verify_entry(missing_stream_dir)
    assert miss_entry["evidence_complete"] is False
    assert miss_entry["missing_native_streams"] == ["stderr"]
    assert any(
        c["reason"] == "verify_native_required_streams_missing"
        and c["missing_streams"] == ["stderr"]
        for c in miss_entry["internal_conflicts"]
    )

    # P1-02 negative: two executions claiming same push remote_ref under same run/attempt with conflicting tree -> scoped_conflicts.
    push_c1 = tmp_path / "push_c1.json"
    push_c2 = tmp_path / "push_c2.json"
    base_push_rep = {
        "kind": "engineering-ci-execution-report",
        "schema_version": 1,
        "status": "passed",
        "exit_code": 0,
        "checks": [],
        "execution_identity": {
            "run_ref": "run:p2",
            "attempt_ref": "att:1",
            "execution_ref": "exec:same-target",
        },
        "plan": {"git": {"head_commit": p1, "source_tree_id": "sha256:" + "1" * 64}},
    }
    push_c1.write_bytes(hooks.canonical_event_bytes(base_push_rep))
    push_c2.write_bytes(
        hooks.canonical_event_bytes(
            {
                **base_push_rep,
                "plan": {
                    "git": {
                        "head_commit": p1,
                        "source_tree_id": "sha256:" + "9" * 64,
                    }
                },
            }
        )
    )
    assert (
        verify_report.build_verify_report(execution_reports=[push_c1, push_c2])[
            "correlation"
        ]["overall_status"]
        == "revision_only"
    )

    # P1-03 standalone _pytest_bundle deduplicates core-junit.xml alias cleanly.
    std_pytest = tmp_path / "standalone-pytest-bundle"
    std_pytest.mkdir()
    std_xml = b'<testsuite tests="1" failures="0" errors="0" skipped="0" time="0.1"><testcase name="t_std" time="0.1"/></testsuite>'
    (std_pytest / "junit.xml").write_bytes(std_xml)
    (std_pytest / "core-junit.xml").write_bytes(std_xml)
    (std_pytest / "exit-code.txt").write_text("0\n", encoding="utf-8")
    (std_pytest / "report.json").write_bytes(
        hooks.canonical_event_bytes(
            {
                "kind": "engineering-ci-execution-report",
                "schema_version": 1,
                "status": "passed",
                "exit_code": 0,
                "checks": [],
                "junit": std_xml.decode("utf-8"),
                "execution_identity": {
                    "run_ref": "run:std",
                    "attempt_ref": "att:1",
                    "execution_ref": "exec:std",
                },
            }
        )
    )
    std_res = verify_report.build_verify_report(execution_reports=[std_pytest])
    assert std_res["correlation"]["execution_groups"] == []
    j_srcs = [s for s in std_res["sources"] if s["source_type"] == "junit_xml"]
    assert len(j_srcs) == 1
    assert j_srcs[0]["alias_paths"] == ["core-junit.xml"]

    # P1-04 tampered #66-C .receipt fails reconciliation.validate_receipt cleanly.
    exec_0.write_bytes(saved_0)
    exec_1.write_bytes(saved_1)
    rec_path = b_push.invocation_path.with_suffix(".receipt")
    good_rec_bytes = rec_path.read_bytes()
    bad_rec = json.loads(good_rec_bytes.decode("utf-8"))
    bad_rec["receipts"][0]["receipt_ref"] = {"sha256": "0" * 64}
    rec_path.write_bytes(hooks.canonical_event_bytes(bad_rec))
    bad_rec_rep = verify_report.build_verify_report(
        hook_root=repo, invocation_ref=b_push.invocation_ref
    )
    assert bad_rec_rep["correlation"]["overall_status"] == "mismatch_detected"
    assert bad_rec_rep["sources"][0]["receipt"]["validation_status"] == "invalid"
    assert any(
        c["reason"] == "hook_receipt_validation_failed"
        for c in bad_rec_rep["sources"][0]["internal_conflicts"]
    )

    # P1-04 negative: swapping Receipt[0] <-> Receipt[1] keeps digests & global refs intact,
    # yet violates Plan[i] -> Ref[i] -> Receipt[i] -> Execution[i].
    swapped_rec = json.loads(good_rec_bytes.decode("utf-8"))
    swapped_rec["receipts"] = [swapped_rec["receipts"][1], swapped_rec["receipts"][0]]
    rec_path.write_bytes(hooks.canonical_event_bytes(swapped_rec))
    swapped_rep = verify_report.build_verify_report(
        hook_root=repo, invocation_ref=b_push.invocation_ref
    )
    assert swapped_rep["correlation"]["overall_status"] == "mismatch_detected"
    assert swapped_rep["sources"][0]["receipt"]["native_status"] == "matched"
    assert swapped_rep["sources"][0]["receipt"]["validation_status"] == "valid"
    assert (
        swapped_rep["sources"][0]["receipt"]["verifiable_evidence_status"]
        == "mismatch_or_invalid"
    )
    assert any(
        c["reason"] == "hook_receipt_scope_or_report_mismatch"
        for c in swapped_rep["sources"][0]["internal_conflicts"]
    )

    # P1-04 negative: wrong job_ref with valid receipt_ref MUST fail Scope/Job correspondence.
    from engineering_ci import reconciliation

    def _replace_job_ref(node, new_job):
        if isinstance(node, list):
            return [_replace_job_ref(item, new_job) for item in node]
        if isinstance(node, dict):
            return {
                k: (
                    new_job
                    if k == "job_ref"
                    else (
                        [new_job for _ in v]
                        if k == "job_refs" and isinstance(v, list)
                        else _replace_job_ref(v, new_job)
                    )
                )
                for k, v in node.items()
            }
        return node

    wrong_job_rec = json.loads(good_rec_bytes.decode("utf-8"))
    r0_wrong = _replace_job_ref(wrong_job_rec["receipts"][0], "wrong-job")
    r0_wrong["receipt_ref"] = reconciliation._digest(
        {k: v for k, v in r0_wrong.items() if k != "receipt_ref"}
    )
    wrong_job_rec["receipts"][0] = r0_wrong
    rec_path.write_bytes(hooks.canonical_event_bytes(wrong_job_rec))
    wrong_job_rep = verify_report.build_verify_report(
        hook_root=repo, invocation_ref=b_push.invocation_ref
    )
    assert wrong_job_rep["correlation"]["overall_status"] == "mismatch_detected"
    assert wrong_job_rep["sources"][0]["receipt"]["validation_status"] == "valid"
    assert (
        wrong_job_rep["sources"][0]["receipt"]["verifiable_evidence_status"]
        == "mismatch_or_invalid"
    )
    assert any(
        c["reason"] == "hook_receipt_scope_or_report_mismatch"
        for c in wrong_job_rep["sources"][0]["internal_conflicts"]
    )

    # Restore valid .receipt, then remove only execution-1 to confirm single missing push execution is caught.
    rec_path.write_bytes(good_rec_bytes)
    exec_1.unlink()
    partial_miss_rep = verify_report.build_verify_report(
        hook_root=repo, invocation_ref=b_push.invocation_ref
    )
    assert partial_miss_rep["correlation"]["overall_status"] == "mismatch_detected"
    assert (
        partial_miss_rep["sources"][0]["receipt"]["verifiable_evidence_status"]
        == "missing_executions"
    )
    exec_1.write_bytes(saved_1)

    # P1-05: real zero-execution pre-push (delete ref + all-inapplicable ref) verifies cleanly.
    zero_repo = tmp_path / "zero-push-repo"
    zero_repo.mkdir()
    _init_git_repo(zero_repo)
    subprocess.run(
        ["git", "config", "core.autocrlf", "false"], cwd=zero_repo, check=True
    )
    _source(zero_repo, "mod", ("forwarding",))
    _approve_profiles(
        zero_repo,
        INDEX_SELECTION
        + '\n[push]\nselect_tests = false\nnew_ref_baseline = "refs/remotes/origin/main"\n'
        'effective_profiles = ["push-del.json", "push-doc.json"]\n',
        [
            ("ci-effective-profile.json", "index", "index", "HEAD", ("forwarding",)),
            (
                "push-del.json",
                "push",
                "delete",
                "refs/heads/del-branch",
                ("forwarding",),
            ),
            (
                "push-doc.json",
                "push",
                "update",
                "refs/heads/doc-branch",
                ("forwarding",),
            ),
        ],
    )
    subprocess.run(
        [
            "git",
            "add",
            "ci-effective-profile.json",
            "push-del.json",
            "push-doc.json",
            "ci-hygiene.toml",
            "ci-selection.toml",
            "src",
            "tach.toml",
        ],
        cwd=zero_repo,
        check=True,
    )
    subprocess.run(["git", "commit", "-qm", "zero-base"], cwd=zero_repo, check=True)
    base_z = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=zero_repo, text=True
    ).strip()
    subprocess.run(
        ["git", "checkout", "-qb", "doc-branch", base_z], cwd=zero_repo, check=True
    )
    (zero_repo / "README.md").write_text("docs only\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "README.md"], cwd=zero_repo, check=True)
    subprocess.run(["git", "commit", "-qm", "docs-only"], cwd=zero_repo, check=True)
    doc_head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=zero_repo, text=True
    ).strip()
    zero_ev = hooks.capture_pre_push(
        zero_repo,
        "origin",
        "https://example.invalid/repo.git",
        (
            f"refs/heads/del-branch {zero} refs/heads/del-branch {base_z}\n"
            f"refs/heads/doc-branch {doc_head} refs/heads/doc-branch {base_z}\n"
        ),
    )
    b_zero = hooks.persist_event(zero_repo, zero_ev)
    b_zero_bind = hooks.load_invocation_binding(b_zero.invocation_path)
    assert (
        hook_checks.prepare_push(
            zero_repo,
            b_zero_bind,
            effective_profiles=["push-del.json", "push-doc.json"],
        )["exit_code"]
        == 0
    )
    assert hook_checks.run_push_checks(zero_repo, b_zero_bind)["exit_code"] == 0
    assert hook_checks.reconcile_push(zero_repo, b_zero_bind)["exit_code"] == 0
    assert hook_checks.consume_push_result(zero_repo, b_zero_bind)["exit_code"] == 0

    zero_rep = verify_report.build_verify_report(
        hook_root=zero_repo, invocation_ref=b_zero.invocation_ref
    )
    assert zero_rep["correlation"]["overall_status"] != "mismatch_detected"
    zero_src = zero_rep["sources"][0]
    assert zero_src["executions"] == []
    assert zero_src["internal_conflicts"] == []
    assert zero_src["receipt"]["native_status"] == "matched"
    assert zero_src["receipt"]["validation_status"] == "valid"
    assert zero_src["receipt"]["verifiable_evidence_status"] == "verified"

    # P1-05 negative: zero-execution delete push claiming a phantom report_ref in receipt MUST fail.
    zero_rec_path = b_zero.invocation_path.with_suffix(".receipt")
    phantom_rec = json.loads(zero_rec_path.read_text(encoding="utf-8"))
    phantom_rec["receipts"][0]["entries"][0]["observed_results"] = [
        {
            "job_ref": "pre-push",
            "report_ref": {"algorithm": "sha256", "sha256": "f" * 64},
            "status": "passed",
            "exit_code": 0,
        }
    ]
    zero_rec_path.write_bytes(hooks.canonical_event_bytes(phantom_rec))
    phantom_rep = verify_report.build_verify_report(
        hook_root=zero_repo, invocation_ref=b_zero.invocation_ref
    )
    assert phantom_rep["correlation"]["overall_status"] == "mismatch_detected"
    assert (
        phantom_rep["sources"][0]["receipt"]["verifiable_evidence_status"]
        == "mismatch_or_invalid"
    )

    # P2-2 single-ceiling oversized check inside hooks.load_invocation_binding(..., max_bytes=...).
    orig_json_max = verify_report.MAX_JSON_REPORT_BYTES
    verify_report.MAX_JSON_REPORT_BYTES = 16
    try:
        oversized_entry = verify_report._load_hook_entry(
            hook_root=repo, invocation_ref=b_push.invocation_ref
        )
    finally:
        verify_report.MAX_JSON_REPORT_BYTES = orig_json_max
    assert oversized_entry["status"] == "oversized"
    assert oversized_entry["error"] == "invocation_payload_oversized"


def test_verify_report_p1_contract_handoff_failure_and_plan_binding(tmp_path):
    from engineering_ci import check_contract, native_producer
    from engineering_ci import execution as eci_execution

    head = "a" * 40
    source_tree_id = "sha256:" + "b" * 64
    git_snap = {
        "mode": "index",
        "snapshot": "index",
        "head_commit": head,
        "source_tree_id": source_tree_id,
        "index_tree": "1" * 40,
        "execution_tree": "1" * 40,
    }
    p102_ident = {
        "run_ref": "run:p102",
        "attempt_ref": "att:1",
        "execution_ref": "exec:p102-fail",
    }
    impl, _ = native_producer.implementation("test", {})
    failed_check = native_producer.test_check(
        {"status": "failed", "exit_code": 1},
        framework="pytest",
        targets=["tests/a.py"],
        observation={"executable": "pytest", "tool_version": "9.0.0"},
        source=native_producer.source_reference({"git": git_snap}, actual="worktree"),
        context=p102_ident,
        impl=impl,
        origin="host",
        artifact_prefix=None,
        plan_authorized=True,
    )

    # --- P1-02 negative: missing checks, wrong kind, unsupported schema_version, invalid NativeResult ---
    valid_failed_report = {
        "kind": "engineering-ci-execution-report",
        "schema_version": 1,
        "status": "failed",
        "exit_code": 1,
        "execution_identity": p102_ident,
        "plan": {"git": git_snap},
        "checks": [{"name": "pytest", **failed_check}],
    }
    valid_failed_a = tmp_path / "valid_failed_a.json"
    valid_failed_b = tmp_path / "valid_failed_b.json"
    valid_failed_a.write_bytes(hooks.canonical_event_bytes(valid_failed_report))
    valid_failed_b.write_bytes(hooks.canonical_event_bytes(valid_failed_report))
    failed_pair = verify_report.build_verify_report(
        execution_reports=[valid_failed_a, valid_failed_b]
    )
    assert failed_pair["correlation"]["overall_status"] == "execution_matched"
    for src in failed_pair["sources"]:
        assert src["report_contract_status"] == "valid"
        assert src["verifiable_execution_proven"] is True
        assert src["admission_authority_63"] == "not_evaluated"
        assert src["report_contract_rejections"] == {}

    valid_not_exec = {
        "kind": "engineering-ci-execution-report",
        "schema_version": 1,
        "status": "not_executed",
        "exit_code": 4,
        "reason": "source_tree_mismatch",
        "checks": [],
        "execution_identity": {
            "run_ref": "run:p102",
            "attempt_ref": "att:1",
            "execution_ref": "exec:p102-not-exec",
        },
    }
    p_not_exec = tmp_path / "valid_not_exec.json"
    p_not_exec.write_bytes(hooks.canonical_event_bytes(valid_not_exec))
    not_exec_res = verify_report.build_verify_report(execution_reports=[p_not_exec])
    assert not_exec_res["sources"][0]["report_contract_status"] == "valid"
    assert not_exec_res["sources"][0]["verifiable_execution_proven"] is False
    assert not_exec_res["sources"][0]["admission_authority_63"] == "not_evaluated"
    assert not_exec_res["sources"][0]["internal_conflicts"] == []

    missing_checks = {
        "kind": "engineering-ci-execution-report",
        "schema_version": 1,
        "status": "passed",
        "exit_code": 0,
        "execution_identity": valid_failed_report["execution_identity"],
    }
    bad_kind = {**valid_failed_report, "kind": "arbitrary-json"}
    bad_schema = {**valid_failed_report, "schema_version": 99}
    bad_native = {
        **valid_failed_report,
        "checks": [
            {
                "name": "whitespace",
                "status": "passed",
                "plan_authorized": True,
                "check_contract": {
                    "kind": check_contract.CHECK_CONTRACT_KIND,
                    "schema_version": check_contract.CONTRACT_VERSION,
                    "status": "valid",
                },
                "native_results": [{"result_id": "broken"}],
            }
        ],
    }
    for label, bad_payload in (
        ("missing_checks", missing_checks),
        ("bad_kind", bad_kind),
        ("bad_schema", bad_schema),
        ("bad_native", bad_native),
    ):
        bad_file = tmp_path / f"{label}.json"
        bad_file.write_bytes(hooks.canonical_event_bytes(bad_payload))
        bad_bundle = tmp_path / f"bundle_{label}"
        bad_bundle.mkdir()
        (bad_bundle / "report.json").write_bytes(
            hooks.canonical_event_bytes(bad_payload)
        )
        for target in (bad_file, bad_bundle):
            rep_res = verify_report.build_verify_report(
                execution_reports=[target, valid_failed_a]
            )
            assert rep_res["correlation"]["overall_status"] == "mismatch_detected"
            assert rep_res["correlation"]["execution_groups"] == []
            bad_src = rep_res["sources"][0]
            good_src = rep_res["sources"][1]
            assert bad_src["status"] == "loaded"
            assert bad_src["report_contract_status"] == "invalid"
            assert bad_src["verifiable_execution_proven"] is False
            assert bad_src["admission_authority_63"] == "not_evaluated"
            assert (
                bad_src["declared_identity"]["execution_ref"]
                == valid_failed_report["execution_identity"]["execution_ref"]
            )
            assert bad_src["execution_ref"] is None
            assert bad_src["query_correlation"]["strength"] == "conflict"
            assert bad_src["query_correlation"]["same_execution_proven"] is False
            assert bad_src["query_correlation"]["execution_keys"] == {}
            assert good_src["query_correlation"]["same_execution_proven"] is False
            assert any(
                c["reason"] == "execution_report_contract_invalid"
                for c in bad_src["internal_conflicts"]
            )

    # Real non-ECI pytest dir remains readable cleanly with no false contract rejection.
    plain_dir = tmp_path / "plain-non-eci-pytest"
    plain_dir.mkdir()
    (plain_dir / "junit.xml").write_text(
        '<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase name="t_plain" time="0.1"/></testsuite>',
        encoding="utf-8",
    )
    plain_res = verify_report.build_verify_report(execution_reports=[plain_dir])
    assert plain_res["sources"][0]["source_type"] == "execution_bundle"
    assert plain_res["sources"][0]["status"] == "missing_report_json"
    assert plain_res["sources"][0]["internal_conflicts"] == []
    assert plain_res["sources"][1]["source_type"] == "junit_xml"

    # --- P1-03 genuine Producer failure/blocking vs dishonest omission ---
    failed_bundle = tmp_path / "bundle-producer-failed"
    failed_bundle.mkdir()
    data_edge = {
        "producer_ref": {"kind": "prerequisite_action", "name": "codegen"},
        "consumer_ref": {"kind": "check", "name": "whitespace"},
        "dependency_kind": "data",
        "requires_pass": True,
        "output_ref": {
            "kind": "file",
            "path": "generated/out.py",
            "sha256": "0" * 64,
        },
        "source_binding_ref": "git",
        "declaration_ref": {
            "path": "ci-selection.toml",
            "sha256": "a" * 64,
            "section": "prerequisite_actions.codegen",
        },
        "selection": {"selected_by": "test", "escalation_reason": None},
        "execution_phase": "pre_execution",
        "resolution": "resolved",
        "unresolved_reason": None,
    }
    plan_dep = {
        "kind": "engineering-ci-selection-plan",
        "schema_version": 2,
        "generator": {"config_sha256": "a" * 64},
        "git": git_snap,
        "selection": {
            "checks": ["whitespace"],
            "tests": [],
            "prerequisites": ["codegen"],
        },
        "prerequisite_actions": {
            "codegen": {"type": "command", "argv": ["python", "-c", "0"]}
        },
        "dependency_contract": plan_contract.graph_contract([data_edge]),
    }
    plan_dep_bytes = hooks.canonical_event_bytes(plan_dep)
    (failed_bundle / "plan.json").write_bytes(plan_dep_bytes)
    failed_closure = [
        {
            "producer_ref": data_edge["producer_ref"],
            "consumer_ref": data_edge["consumer_ref"],
            "dependency_kind": "data",
            "output_ref": data_edge["output_ref"],
            "sha256": data_edge["output_ref"]["sha256"],
            "verified": False,
            "reason": "producer_execution_failed",
        }
    ]
    failed_prereq_res = [
        {
            "name": "codegen",
            "type": "command",
            "status": "failed",
            "exit_code": 2,
            "reason": "command_failed",
        }
    ]
    failed_ident = {
        "run_ref": "run:prod-fail",
        "attempt_ref": "att:1",
        "execution_ref": "exec:prod-fail",
    }
    failed_handoff = eci_execution.build_producer_artifact_handoff(
        plan_dep,
        closure_records=failed_closure,
        prerequisite_results=failed_prereq_res,
        execution_identity=failed_ident,
        execution_context="dagger_export",
    )
    failed_bundle_rep = {
        "kind": "engineering-ci-execution-report",
        "schema_version": 1,
        "status": "failed",
        "exit_code": 1,
        "reason": "prerequisites_failed",
        "execution_identity": failed_ident,
        "plan": {"generator": plan_dep["generator"], "git": git_snap},
        "targets": [],
        "framework": "unittest",
        "dependency_contract": plan_dep["dependency_contract"],
        "prerequisites": failed_prereq_res,
        "artifact_closure": failed_closure,
        "checks": [
            {
                "name": "whitespace",
                "status": "not_executed",
                "plan_authorized": True,
                "reason": "producer_execution_failed",
                "blocked_by": [
                    {
                        "producer_ref": data_edge["producer_ref"],
                        "reason": "producer_execution_failed",
                    }
                ],
                "check_contract": {
                    "kind": check_contract.CHECK_CONTRACT_KIND,
                    "schema_version": check_contract.CONTRACT_VERSION,
                    "status": "valid",
                },
            }
        ],
        "producer_artifact_handoff": failed_handoff,
    }
    (failed_bundle / "report.json").write_bytes(
        hooks.canonical_event_bytes(failed_bundle_rep)
    )
    failed_prod_res = verify_report.build_verify_report(
        execution_reports=[failed_bundle]
    )
    assert failed_prod_res["correlation"]["overall_status"] == "uncorrelated"
    assert failed_prod_res["correlation"]["scoped_conflicts"] == []
    fb_src = failed_prod_res["sources"][0]
    assert fb_src["internal_conflicts"] == []
    assert fb_src["execution_status"] == "failed"
    assert fb_src["plan_binding_status"] == "declared_plan_ref_matches"
    assert fb_src["producer_artifact_handoff_status"] == "not_delivered_producer_failed"
    assert (
        fb_src["producer_artifact_handoff"]["delivery_status"]
        == "not_delivered_producer_failed"
    )
    assert (
        fb_src["producer_artifact_handoff"]["retention_status"]
        == "not_retained_producer_failed"
    )
    assert (
        fb_src["producer_artifact_handoff"]["non_produced_edges"][0]["producer_status"]
        == "failed"
    )

    # Honest blocked/not_executed prerequisite also yields not_delivered_producer_blocked with zero conflicts.
    blocked_prereq_res = [
        {
            "name": "codegen",
            "type": "command",
            "status": "not_executed",
            "exit_code": None,
            "reason": "upstream_blocked",
        }
    ]
    blocked_closure = [
        {**failed_closure[0], "reason": "producer_execution_not_verified"}
    ]
    blocked_handoff = eci_execution.build_producer_artifact_handoff(
        plan_dep,
        closure_records=blocked_closure,
        prerequisite_results=blocked_prereq_res,
        execution_identity=failed_ident,
        execution_context="dagger_export",
    )
    (failed_bundle / "report.json").write_bytes(
        hooks.canonical_event_bytes(
            {
                **failed_bundle_rep,
                "status": "blocked",
                "exit_code": 5,
                "prerequisites": blocked_prereq_res,
                "artifact_closure": blocked_closure,
                "producer_artifact_handoff": blocked_handoff,
            }
        )
    )
    blocked_prod_res = verify_report.build_verify_report(
        execution_reports=[failed_bundle]
    )
    bb_src = blocked_prod_res["sources"][0]
    assert bb_src["internal_conflicts"] == []
    assert (
        bb_src["producer_artifact_handoff_status"] == "not_delivered_producer_blocked"
    )
    assert (
        bb_src["producer_artifact_handoff"]["retention_status"]
        == "not_retained_producer_blocked"
    )

    # Dishonest report claiming "passed" without retained artifacts STILL fails closed.
    (failed_bundle / "report.json").write_bytes(
        hooks.canonical_event_bytes(
            {**failed_bundle_rep, "status": "passed", "exit_code": 0}
        )
    )
    lying_res = verify_report.build_verify_report(execution_reports=[failed_bundle])
    assert lying_res["correlation"]["overall_status"] == "mismatch_detected"
    assert any(
        c["reason"] == "bundle_producer_handoff_failed"
        for c in lying_res["sources"][0]["internal_conflicts"]
    )

    # --- P1-04 Plan <-> Report correspondence with identical Git + source_tree_id ---
    plan_a = {
        "kind": "engineering-ci-selection-plan",
        "schema_version": 2,
        "generator": {"config_sha256": "a" * 64},
        "git": git_snap,
        "plan_execution_tree": {"source_tree_id": source_tree_id},
        "selection": {
            "framework": "pytest",
            "tests": ["tests/test_alpha.py"],
            "checks": ["whitespace"],
            "prerequisites": [],
        },
        "prerequisite_actions": {},
        "dependency_contract": plan_contract.graph_contract([]),
    }
    plan_b_swapped = {
        **plan_a,
        "selection": {
            "framework": "pytest",
            "tests": ["tests/test_beta.py"],
            "checks": ["forwarding"],
            "prerequisites": [],
        },
    }
    plan_a_ref = hooks._digest_bytes(hooks.canonical_event_bytes(plan_a))
    rep_a_for_plan = {
        "kind": "engineering-ci-execution-report",
        "schema_version": 1,
        "status": "passed",
        "exit_code": 0,
        "plan_ref": plan_a_ref,
        "plan": {"generator": plan_a["generator"], "git": git_snap},
        "plan_execution_tree": plan_a["plan_execution_tree"],
        "source_tree_id": source_tree_id,
        "targets": ["tests/test_alpha.py"],
        "framework": "pytest",
        "execution_identity": {
            "run_ref": "run:p104",
            "attempt_ref": "att:1",
            "execution_ref": "exec:p104",
        },
        "checks": [
            {
                "name": "whitespace",
                "status": "passed",
                "plan_authorized": True,
                "check_contract": {
                    "kind": check_contract.CHECK_CONTRACT_KIND,
                    "schema_version": check_contract.CONTRACT_VERSION,
                    "status": "valid",
                },
            }
        ],
    }
    bundle_p104 = tmp_path / "bundle-p104"
    bundle_p104.mkdir()
    (bundle_p104 / "plan.json").write_bytes(hooks.canonical_event_bytes(plan_a))
    (bundle_p104 / "report.json").write_bytes(
        hooks.canonical_event_bytes(rep_a_for_plan)
    )
    paired_p104 = verify_report.build_verify_report(execution_reports=[bundle_p104])
    assert paired_p104["sources"][0]["internal_conflicts"] == []
    assert (
        paired_p104["sources"][0]["plan_binding_status"] == "declared_plan_ref_matches"
    )
    assert paired_p104["sources"][0]["observed_plan_file_ref"] == plan_a_ref
    assert paired_p104["sources"][0]["declared_report_plan_ref"] == plan_a_ref

    # Swapping plan.json (same Git/Tree, differing tests & checks) even without declared plan_ref -> mismatch!
    rep_no_plan_ref = {k: v for k, v in rep_a_for_plan.items() if k != "plan_ref"}
    (bundle_p104 / "report.json").write_bytes(
        hooks.canonical_event_bytes(rep_no_plan_ref)
    )
    (bundle_p104 / "plan.json").write_bytes(hooks.canonical_event_bytes(plan_b_swapped))
    swapped_p104 = verify_report.build_verify_report(execution_reports=[bundle_p104])
    assert swapped_p104["correlation"]["overall_status"] == "mismatch_detected"
    assert swapped_p104["sources"][0]["plan_binding_status"] == "mismatch"
    reasons_swapped = {
        c["reason"] for c in swapped_p104["sources"][0]["internal_conflicts"]
    }
    assert {
        "bundle_plan_targets_mismatch",
        "bundle_plan_unexpected_check",
        "bundle_plan_selected_checks_mismatch",
    }.issubset(reasons_swapped)

    # Restoring plan_a with wrong declared plan_ref -> bundle_plan_ref_mismatch!
    (bundle_p104 / "plan.json").write_bytes(hooks.canonical_event_bytes(plan_a))
    (bundle_p104 / "report.json").write_bytes(
        hooks.canonical_event_bytes(
            {**rep_a_for_plan, "plan_ref": "sha256:" + "e" * 64}
        )
    )
    bad_ref_p104 = verify_report.build_verify_report(execution_reports=[bundle_p104])
    assert bad_ref_p104["correlation"]["overall_status"] == "mismatch_detected"
    assert bad_ref_p104["sources"][0]["plan_binding_status"] == "mismatch"
    assert bad_ref_p104["sources"][0]["observed_plan_file_ref"] == plan_a_ref
    assert (
        bad_ref_p104["sources"][0]["declared_report_plan_ref"] == "sha256:" + "e" * 64
    )
    assert any(
        c["reason"] == "bundle_plan_ref_mismatch"
        for c in bad_ref_p104["sources"][0]["internal_conflicts"]
    )

    # Missing plan.json -> unknown_missing_plan!
    (bundle_p104 / "plan.json").unlink()
    (bundle_p104 / "report.json").write_bytes(
        hooks.canonical_event_bytes(rep_a_for_plan)
    )
    miss_p104 = verify_report.build_verify_report(execution_reports=[bundle_p104])
    assert miss_p104["sources"][0]["plan_binding_status"] == "unknown_missing_plan"
    assert miss_p104["sources"][0]["observed_plan_file_ref"] is None
    assert miss_p104["sources"][0]["declared_report_plan_ref"] == plan_a_ref

    # Round-5 P1-04: empty {} plan.json -> invalid_plan (NEVER verified).
    (bundle_p104 / "plan.json").write_bytes(hooks.canonical_event_bytes({}))
    (bundle_p104 / "report.json").write_bytes(
        hooks.canonical_event_bytes(rep_no_plan_ref)
    )
    empty_plan_p104 = verify_report.build_verify_report(execution_reports=[bundle_p104])
    assert empty_plan_p104["correlation"]["overall_status"] == "mismatch_detected"
    empty_src = empty_plan_p104["sources"][0]
    assert empty_src["plan_contract_status"] == "invalid"
    assert empty_src["plan_binding_status"] == "invalid_plan"
    assert empty_src["plan_binding_status"] != "verified"

    # Round-5 P1-04: valid plan.json + matching projection WITHOUT declared plan_ref -> unverified_plan_ref_undeclared.
    (bundle_p104 / "plan.json").write_bytes(hooks.canonical_event_bytes(plan_a))
    (bundle_p104 / "report.json").write_bytes(
        hooks.canonical_event_bytes(rep_no_plan_ref)
    )
    undecl_p104 = verify_report.build_verify_report(execution_reports=[bundle_p104])
    undecl_src = undecl_p104["sources"][0]
    assert undecl_src["internal_conflicts"] == []
    assert undecl_src["plan_contract_status"] == "valid"
    assert undecl_src["plan_projection_status"] == "matched"
    assert undecl_src["plan_binding_status"] == "unverified_plan_ref_undeclared"
    assert undecl_src["observed_plan_file_ref"] == plan_a_ref
    assert undecl_src["declared_report_plan_ref"] is None


def test_verify_report_round5_native_pytest_subset_handoff_binding_and_unit_disqualification(
    tmp_path,
):
    from engineering_ci import check_contract, native_producer
    from engineering_ci import execution as eci_execution

    # --- Section A: P1-02a native Dagger pytest schema v1 bundles & check-result.json ---
    np_pass = tmp_path / "np-pass"
    np_pass.mkdir()
    (np_pass / "exit-code.txt").write_text("0\n", encoding="utf-8")
    (np_pass / "junit.xml").write_text(
        '<testsuite tests="2" failures="0" errors="0" skipped="0" time="0.2"><testcase name="a"/><testcase name="b"/></testsuite>',
        encoding="utf-8",
    )
    (np_pass / "report.json").write_bytes(
        hooks.canonical_event_bytes(
            {
                "schema_version": 1,
                "exit_code": 0,
                "reason": "passed",
                "status": "passed",
                "report_available": True,
                "counts": {
                    "tests": 2,
                    "failures": 0,
                    "errors": 0,
                    "skipped": 0,
                    "passed": 2,
                },
                "failures": [],
                "omitted_failures": 0,
                "diagnostic": None,
            }
        )
    )
    np_pass_res = verify_report.build_verify_report(execution_reports=[np_pass])
    assert np_pass_res["correlation"]["overall_status"] == "uncorrelated"
    pass_src = next(
        s for s in np_pass_res["sources"] if s["source_type"] == "execution_bundle"
    )
    assert pass_src["internal_conflicts"] == []
    assert pass_src["report_contract_status"] == "valid"
    assert pass_src["native_execution_status"] == "passed"
    assert pass_src["report_delivery_status"] == "available"
    assert pass_src["execution_status"] == "passed"
    assert pass_src["plan_binding_status"] == "not_applicable"

    np_fail = tmp_path / "np-fail"
    np_fail.mkdir()
    (np_fail / "exit-code.txt").write_text("1\n", encoding="utf-8")
    (np_fail / "report.json").write_bytes(
        hooks.canonical_event_bytes(
            {
                "schema_version": 1,
                "exit_code": 1,
                "reason": "failed",
                "status": "failed",
                "report_available": True,
                "counts": {
                    "tests": 2,
                    "failures": 1,
                    "errors": 0,
                    "skipped": 0,
                    "passed": 1,
                },
                "failures": [
                    {
                        "nodeid": "tests/t.py::test_fail",
                        "outcome": "failed",
                        "message": "assertion failed",
                    }
                ],
                "omitted_failures": 0,
                "diagnostic": None,
            }
        )
    )
    np_fail_res = verify_report.build_verify_report(execution_reports=[np_fail])
    fail_src = np_fail_res["sources"][0]
    assert fail_src["internal_conflicts"] == []
    assert fail_src["report_contract_status"] == "valid"
    assert fail_src["native_execution_status"] == "failed"
    assert fail_src["report_delivery_status"] == "available"
    assert fail_src["execution_status"] == "failed"

    # exit_code == 0 with report_available == False (XML missing or malformed): execution succeeded, report unavailable.
    np_xml_missing = tmp_path / "np-xml-missing"
    np_xml_missing.mkdir()
    (np_xml_missing / "exit-code.txt").write_text("0\n", encoding="utf-8")
    (np_xml_missing / "junit.xml").write_text("<broken", encoding="utf-8")
    (np_xml_missing / "report.json").write_bytes(
        hooks.canonical_event_bytes(
            {
                "schema_version": 1,
                "exit_code": 0,
                "reason": "invalid_junit_xml",
                "status": "error",
                "report_available": False,
                "counts": None,
                "failures": [],
                "omitted_failures": 0,
                "diagnostic": "ParseError: unclosed token",
            }
        )
    )
    np_unavail_res = verify_report.build_verify_report(
        execution_reports=[np_xml_missing]
    )
    assert np_unavail_res["correlation"]["overall_status"] == "uncorrelated"
    unavail_src = next(
        s for s in np_unavail_res["sources"] if s["source_type"] == "execution_bundle"
    )
    assert unavail_src["internal_conflicts"] == []
    assert unavail_src["report_contract_status"] == "valid"
    assert unavail_src["native_execution_status"] == "passed"
    assert unavail_src["report_available"] is False
    assert unavail_src["report_delivery_status"] == "unavailable"
    assert unavail_src["execution_status"] == "error"
    assert unavail_src["exit_code"] == 0
    assert unavail_src["plan_binding_status"] == "not_applicable"
    broken_junit_src = next(
        s for s in np_unavail_res["sources"] if s["source_type"] == "junit_xml"
    )
    assert broken_junit_src["status"] == "invalid"
    assert broken_junit_src["internal_conflicts"] == []

    # Malformed native pytest v1 & unknown JSON fail contract validation.
    for label, payload in (
        (
            "bad_np_status",
            {
                "schema_version": 1,
                "exit_code": 0,
                "reason": "passed",
                "status": "bogus",
                "report_available": True,
                "counts": {"total": 1},
                "failures": [],
                "omitted_failures": 0,
                "diagnostic": None,
            },
        ),
        ("unknown_json", {"hello": "world"}),
    ):
        bad_dir = tmp_path / label
        bad_dir.mkdir()
        (bad_dir / "report.json").write_bytes(hooks.canonical_event_bytes(payload))
        bad_out = verify_report.build_verify_report(execution_reports=[bad_dir])
        assert bad_out["correlation"]["overall_status"] == "mismatch_detected"
        assert bad_out["sources"][0]["report_contract_status"] == "invalid"

    # Valid standalone check-result.json passes contract validation.
    repo = tmp_path / "repo-r5"
    repo.mkdir()
    head = _init_git_repo(repo)
    git_snap = {
        "mode": "index",
        "snapshot": "index",
        "head_commit": head,
        "source_tree_id": "sha256:" + "b" * 64,
        "index_tree": "1" * 40,
        "execution_tree": "1" * 40,
    }
    impl, _ = native_producer.implementation("test", {})
    native_check = native_producer.test_check(
        {"status": "passed", "exit_code": 0},
        framework="pytest",
        targets=["tests/a.py"],
        observation={"executable": "pytest", "tool_version": "9.0.0"},
        source=native_producer.source_reference({"git": git_snap}, actual="worktree"),
        context={
            "run_ref": "run:r5-cr",
            "attempt_ref": "att:1",
            "execution_ref": "exec:r5-cr",
        },
        impl=impl,
        origin="host",
        artifact_prefix=None,
        plan_authorized=True,
    )
    cr_path = tmp_path / "check-result.json"
    cr_path.write_bytes(hooks.canonical_event_bytes({"name": "pytest", **native_check}))
    cr_res = verify_report.build_verify_report(execution_reports=[cr_path])
    assert cr_res["correlation"]["overall_status"] == "uncorrelated"
    assert cr_res["sources"][0]["internal_conflicts"] == []
    assert cr_res["sources"][0]["report_contract_status"] == "valid"

    # --- Section B: P1-04 real hook_checks split-job subset vs shared Plan ---
    _head_b, inv_file, binding = _create_split_precommit_repo(repo, init_repo=False)

    ws_exec = json.loads(
        inv_file.with_suffix(".execution-check-whitespace").read_text(encoding="utf-8")
    )
    fwd_exec = json.loads(
        inv_file.with_suffix(".execution-check-forwarding").read_text(encoding="utf-8")
    )
    shared_plan_sidecar = json.loads(
        inv_file.with_suffix(".plan").read_text(encoding="utf-8")
    )
    shared_plan = shared_plan_sidecar["plan"]
    # First check unmodified real split-job Plan <-> check-whitespace subset Report.
    real_subset_plan_bytes = hooks.canonical_event_bytes(shared_plan)
    subset_bundle = tmp_path / "bundle-subset-ws"
    subset_bundle.mkdir()
    (subset_bundle / "plan.json").write_bytes(real_subset_plan_bytes)
    (subset_bundle / "report.json").write_bytes(
        hooks.canonical_event_bytes(ws_exec["report"])
    )
    subset_res = verify_report.build_verify_report(execution_reports=[subset_bundle])
    subset_src = subset_res["sources"][0]
    assert subset_src["internal_conflicts"] == []
    assert subset_src["plan_contract_status"] == "valid"
    assert subset_src["plan_execution_scope"] == "subset_of_plan"
    assert subset_src["plan_projection_status"] == "subset_compatible"
    assert subset_src["plan_binding_status"] == "unverified_plan_ref_undeclared"
    assert subset_src["declared_report_plan_ref"] is None

    # Include both pytest tests and multiple checks in a valid shared Plan to confirm partial check_names subsets never trigger targets/checks mismatch.
    full_subset_plan = {
        **shared_plan,
        "selection": {
            **shared_plan["selection"],
            "framework": shared_plan["selection"].get("framework", "unittest"),
            "tests": ["tests/test_alpha.py"],
            "checks": ["whitespace", "forwarding"],
        },
    }
    full_subset_plan_bytes = hooks.canonical_event_bytes(full_subset_plan)
    (subset_bundle / "plan.json").write_bytes(full_subset_plan_bytes)
    (subset_bundle / "report.json").write_bytes(
        hooks.canonical_event_bytes(
            {
                **ws_exec["report"],
                "plan_ref": hooks._digest_bytes(full_subset_plan_bytes),
            }
        )
    )
    subset_res_tests = verify_report.build_verify_report(
        execution_reports=[subset_bundle]
    )
    subset_tests_src = subset_res_tests["sources"][0]
    assert subset_tests_src["internal_conflicts"] == []
    assert subset_tests_src["plan_contract_status"] == "valid"
    assert subset_tests_src["plan_execution_scope"] == "subset_of_plan"
    assert subset_tests_src["plan_projection_status"] == "subset_compatible"
    assert subset_tests_src["plan_binding_status"] == "verified"

    # --- Section C: P1-03a closure_failed (producer passed, closure verified=False) & P1-03b Handoff identity/hash bindings ---
    scratch_p103 = tmp_path / "scratch-p103"
    gen_dir = scratch_p103 / "prerequisite_action" / "codegen" / "generated"
    gen_dir.mkdir(parents=True)
    gen_file = gen_dir / "out.py"
    gen_file.write_text("X = 1\n", encoding="utf-8")
    out_ref = {
        "kind": "file",
        "path": "generated/out.py",
        "sha256": hashlib.sha256(gen_file.read_bytes()).hexdigest(),
    }
    data_edge = {
        "producer_ref": {"kind": "prerequisite_action", "name": "codegen"},
        "consumer_ref": {"kind": "check", "name": "whitespace"},
        "dependency_kind": "data",
        "requires_pass": True,
        "output_ref": out_ref,
        "source_binding_ref": "git",
        "declaration_ref": {
            "path": "ci-selection.toml",
            "sha256": "a" * 64,
            "section": "prerequisite_actions.codegen",
        },
        "selection": {"selected_by": "test", "escalation_reason": None},
        "execution_phase": "pre_execution",
        "resolution": "resolved",
        "unresolved_reason": None,
    }
    plan_p103 = {
        "kind": plan_contract.PLAN_KIND,
        "schema_version": 2,
        "generator": {"config_sha256": "a" * 64},
        "git": git_snap,
        "selection": {
            "checks": ["whitespace"],
            "tests": [],
            "prerequisites": ["codegen"],
        },
        "prerequisite_actions": {
            "codegen": {"type": "command", "argv": ["python", "-c", "0"]}
        },
        "dependency_contract": plan_contract.graph_contract([data_edge]),
    }
    plan_p103_bytes = hooks.canonical_event_bytes(plan_p103)
    plan_p103_ref = hooks._digest_bytes(plan_p103_bytes)

    prereq_pass = [
        {
            "name": "codegen",
            "type": "command",
            "status": "passed",
            "exit_code": 0,
            "reason": None,
        }
    ]
    closure_fail_records = [
        {
            "producer_ref": data_edge["producer_ref"],
            "consumer_ref": data_edge["consumer_ref"],
            "dependency_kind": "data",
            "output_ref": out_ref,
            "sha256": out_ref["sha256"],
            "verified": False,
            "reason": "closure_artifact_missing",
        }
    ]
    closure_fail_ident = {
        "run_ref": "run:r5-p103",
        "attempt_ref": "att:1",
        "execution_ref": "exec:r5-p103a",
    }
    closure_fail_handoff = eci_execution.build_producer_artifact_handoff(
        plan_p103,
        closure_records=closure_fail_records,
        prerequisite_results=prereq_pass,
        execution_identity=closure_fail_ident,
        execution_context="dagger_export",
    )
    b_p103a = tmp_path / "bundle-p103a"
    b_p103a.mkdir()
    (b_p103a / "plan.json").write_bytes(plan_p103_bytes)
    (b_p103a / "report.json").write_bytes(
        hooks.canonical_event_bytes(
            {
                "kind": "engineering-ci-execution-report",
                "schema_version": 1,
                "status": "blocked",
                "exit_code": 5,
                "reason": "artifact_closure_failed",
                "plan_ref": plan_p103_ref,
                "plan": {"generator": plan_p103["generator"], "git": git_snap},
                "targets": [],
                "framework": "unittest",
                "execution_identity": closure_fail_ident,
                "dependency_contract": plan_p103["dependency_contract"],
                "prerequisites": prereq_pass,
                "artifact_closure": closure_fail_records,
                "artifact_delivery": {
                    "status": "blocked",
                    "reason": "closure_artifact_missing",
                    "consumers": [
                        {
                            "consumer_ref": data_edge["consumer_ref"],
                            "status": "blocked",
                            "reason": "closure_artifact_missing",
                        }
                    ],
                },
                "checks": [
                    {
                        "name": "whitespace",
                        "status": "not_executed",
                        "plan_authorized": True,
                        "reason": "closure_artifact_missing",
                        "check_contract": {
                            "kind": check_contract.CHECK_CONTRACT_KIND,
                            "schema_version": check_contract.CONTRACT_VERSION,
                            "status": "valid",
                        },
                    }
                ],
                "producer_artifact_handoff": closure_fail_handoff,
            }
        )
    )
    p103a_res = verify_report.build_verify_report(execution_reports=[b_p103a])
    p103a_src = p103a_res["sources"][0]
    assert p103a_src["internal_conflicts"] == []
    assert (
        p103a_src["producer_artifact_handoff_status"] == "not_delivered_closure_failed"
    )
    assert (
        p103a_src["producer_artifact_handoff"]["delivery_status"]
        == "not_delivered_closure_failed"
    )
    assert (
        p103a_src["producer_artifact_handoff"]["retention_status"]
        == "not_retained_closure_failed"
    )
    assert (
        p103a_src["producer_artifact_handoff"]["non_produced_edges"][0][
            "producer_status"
        ]
        == "passed"
    )
    assert p103a_src["producer_artifact_handoff"]["consumer_delivery"]

    # P1-03b: swapped handoff (identical plan/run/attempt, different execution_ref) & hash binding checks.
    closure_ok = [
        {
            "producer_ref": data_edge["producer_ref"],
            "consumer_ref": data_edge["consumer_ref"],
            "dependency_kind": "data",
            "output_ref": out_ref,
            "sha256": out_ref["sha256"],
            "verified": True,
        }
    ]
    prod_bundle_1 = tmp_path / "bundle-prod-1"
    prod_bundle_1.mkdir()
    ret_1 = eci_execution.retain_closure_artifacts(
        scratch_p103, prod_bundle_1, closure_ok
    )
    ident_1 = {
        "run_ref": "run:r5-p103b",
        "attempt_ref": "att:1",
        "execution_ref": "exec:r5-p103b-1",
    }
    ident_2 = {
        "run_ref": "run:r5-p103b",
        "attempt_ref": "att:1",
        "execution_ref": "exec:r5-p103b-2",
    }
    handoff_1 = eci_execution.build_producer_artifact_handoff(
        plan_p103,
        closure_records=closure_ok,
        prerequisite_results=prereq_pass,
        execution_identity=ident_1,
        execution_context="dagger_export",
        retention_metadata=ret_1,
    )
    handoff_2 = eci_execution.build_producer_artifact_handoff(
        plan_p103,
        closure_records=closure_ok,
        prerequisite_results=prereq_pass,
        execution_identity=ident_2,
        execution_context="dagger_export",
        retention_metadata=ret_1,
    )
    base_rep_p103b = {
        "kind": "engineering-ci-execution-report",
        "schema_version": 1,
        "status": "passed",
        "exit_code": 0,
        "plan_ref": plan_p103_ref,
        "plan": {"generator": plan_p103["generator"], "git": git_snap},
        "targets": [],
        "execution_identity": ident_1,
        "prerequisites": prereq_pass,
        "artifact_closure": closure_ok,
        "checks": [
            {
                "name": "whitespace",
                "status": "passed",
                "plan_authorized": True,
                "check_contract": {
                    "kind": check_contract.CHECK_CONTRACT_KIND,
                    "schema_version": check_contract.CONTRACT_VERSION,
                    "status": "valid",
                },
            }
        ],
        "producer_artifact_handoff": handoff_1,
    }
    (prod_bundle_1 / "plan.json").write_bytes(plan_p103_bytes)
    (prod_bundle_1 / "report.json").write_bytes(
        hooks.canonical_event_bytes(base_rep_p103b)
    )
    ok_p103b = verify_report.build_verify_report(execution_reports=[prod_bundle_1])
    assert ok_p103b["sources"][0]["internal_conflicts"] == []
    assert ok_p103b["sources"][0]["producer_artifact_handoff_status"] == "verified"

    def _rehash_handoff(h):
        body = {k: v for k, v in h.items() if k != "handoff_digest"}
        return {**body, "handoff_digest": eci_execution._canonical_sha256(body)}

    dup_handoff = _rehash_handoff(
        {
            **handoff_1,
            "artifacts": [handoff_1["artifacts"][0], handoff_1["artifacts"][0]],
        }
    )
    unexp_closure = {
        **closure_ok[0],
        "consumer_ref": {"kind": "check", "name": "forwarding"},
    }
    unexp_handoff = _rehash_handoff(
        {
            **handoff_1,
            "artifacts": [
                *handoff_1["artifacts"],
                {
                    **handoff_1["artifacts"][0],
                    "consumer_ref": {"kind": "check", "name": "forwarding"},
                    "closure_sha256": eci_execution._canonical_sha256(unexp_closure),
                },
            ],
        }
    )

    for label, mutated_rep, expected_detail in (
        (
            "swapped_handoff",
            {**base_rep_p103b, "producer_artifact_handoff": handoff_2},
            "producer_handoff_execution_mismatch",
        ),
        (
            "duplicate_handoff_artifact",
            {**base_rep_p103b, "producer_artifact_handoff": dup_handoff},
            "producer_handoff_duplicate_artifact",
        ),
        (
            "unexpected_handoff_artifact",
            {
                **base_rep_p103b,
                "artifact_closure": [*closure_ok, unexp_closure],
                "producer_artifact_handoff": unexp_handoff,
            },
            "producer_handoff_unexpected_artifact",
        ),
        (
            "mutated_prereq",
            {**base_rep_p103b, "prerequisites": [{**prereq_pass[0], "exit_code": 99}]},
            "producer_handoff_prerequisite_mismatch",
        ),
        (
            "mutated_closure",
            {
                **base_rep_p103b,
                "artifact_closure": [{**closure_ok[0], "sha256": "f" * 64}],
            },
            "producer_handoff_closure_mismatch",
        ),
    ):
        (prod_bundle_1 / "report.json").write_bytes(
            hooks.canonical_event_bytes(mutated_rep)
        )
        bad_p103b = verify_report.build_verify_report(execution_reports=[prod_bundle_1])
        assert bad_p103b["correlation"]["overall_status"] == "mismatch_detected", label
        assert any(
            c["reason"] == "bundle_producer_handoff_failed"
            and expected_detail in c.get("error", "")
            for c in bad_p103b["sources"][0]["internal_conflicts"]
        ), (label, bad_p103b["sources"][0]["internal_conflicts"])

    # --- Section D: P1-05 & P2-02 conflicted Hook unit disqualification & conflict_unit_count ---
    ws_bundle = tmp_path / "bundle-ws-r5"
    ws_bundle.mkdir()
    (ws_bundle / "report.json").write_bytes(
        hooks.canonical_event_bytes(ws_exec["report"])
    )
    fwd_bundle = tmp_path / "bundle-fwd-r5"
    fwd_bundle.mkdir()
    (fwd_bundle / "report.json").write_bytes(
        hooks.canonical_event_bytes(fwd_exec["report"])
    )

    # Tamper whitespace hook execution body without recomputing report_digest -> only whitespace unit is conflicted!
    ws_sidecar = inv_file.with_suffix(".execution-check-whitespace")
    tampered_ws = json.loads(ws_sidecar.read_text(encoding="utf-8"))
    tampered_ws["report"]["exit_code"] = 99
    ws_sidecar.write_bytes(hooks.canonical_event_bytes(tampered_ws))

    p105_res = verify_report.build_verify_report(
        hook_root=repo,
        invocation_ref=binding.invocation_ref,
        execution_reports=[ws_bundle, fwd_bundle],
    )
    assert p105_res["correlation"]["overall_status"] == "mismatch_detected"
    hook_src = next(
        s for s in p105_res["sources"] if s["source_type"] == "hook_invocation"
    )
    ws_src = next(
        s for s in p105_res["sources"] if str(ws_bundle) in str(s.get("path", ""))
    )
    fwd_src = next(
        s for s in p105_res["sources"] if str(fwd_bundle) in str(s.get("path", ""))
    )
    assert hook_src["query_correlation"]["matched_unit_count"] == 1
    assert hook_src["query_correlation"]["conflict_unit_count"] == 1
    assert hook_src["query_correlation"]["unqueried_unit_count"] == 0
    assert {
        u["scope"]: u["status"]
        for u in hook_src["query_correlation"]["execution_units"]
    } == {
        "execution-check-forwarding": "matched",
        "execution-check-whitespace": "conflict",
    }
    assert ws_src["query_correlation"]["same_execution_proven"] is False
    assert ws_src["query_correlation"]["strength"] != "execution_matched"
    assert fwd_src["query_correlation"]["same_execution_proven"] is True
    assert fwd_src["query_correlation"]["strength"] == "execution_matched"
    groups = p105_res["correlation"]["execution_groups"]
    assert len(groups) == 1
    assert sorted(groups[0]["source_indices"]) == [0, 2]


def test_verify_report_round6_il01_il02_il03_and_ci_performance_metrics(tmp_path):
    # I-L01: Same run_id & run_attempt across disjoint jobs stays shared_context_only (NEVER execution_matched).
    ci_j1 = tmp_path / "ci-j1.json"
    ci_j2 = tmp_path / "ci-j2.json"
    sha_hex = "a" * 40
    ci_j1.write_text(
        json.dumps(
            {
                "run_id": 500,
                "run_attempt": 2,
                "sha": sha_hex,
                "workflow": "CI",
                "event": "push",
                "conclusion": "success",
                "queued_before_first_job_seconds": 3.5,
                "checks_execution_window_seconds": 12.25,
                "submission_to_checks_complete_seconds": 15.75,
                "critical_finishing_job": "checks",
                "parallel_runner_seconds": 24.0,
                "first_failed_step_completion_seconds": None,
                "limitations": ["attempt_jobs_snapshot"],
                "jobs": [
                    {
                        "id": 1,
                        "run_id": 500,
                        "run_attempt": 2,
                        "name": "checks",
                        "status": "completed",
                        "conclusion": "success",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    ci_j2.write_text(
        json.dumps(
            {
                "run_id": 500,
                "run_attempt": 2,
                "sha": sha_hex,
                "workflow": "CI",
                "event": "push",
                "jobs": [{"id": 2, "run_id": 500, "run_attempt": 2}],
            }
        ),
        encoding="utf-8",
    )
    il01_res = verify_report.build_verify_report(ci_performances=[ci_j1, ci_j2])
    assert il01_res["correlation"]["overall_status"] == "shared_context_only"
    assert il01_res["correlation"]["execution_groups"] == []
    assert len(il01_res["correlation"]["context_groups"]) == 1
    assert all(
        s["query_correlation"]["same_execution_proven"] is False
        for s in il01_res["sources"]
    )
    c1_src = il01_res["sources"][0]
    assert c1_src["queued_before_first_job_seconds"] == 3.5
    assert c1_src["checks_execution_window_seconds"] == 12.25
    assert c1_src["submission_to_checks_complete_seconds"] == 15.75
    assert c1_src["critical_finishing_job"] == "checks"
    assert c1_src["parallel_runner_seconds"] == 24.0
    assert c1_src["first_failed_step_completion_seconds"] is None
    assert c1_src["limitations"] == ["attempt_jobs_snapshot"]
    assert c1_src["conclusion"] == "success"
    assert c1_src["job_ids"] == [1]

    # Same job id with conflicting conclusions triggers ci_job scoped conflict; invalid membership rejected.
    ci_bad = tmp_path / "ci-bad-jobs.json"
    ci_bad.write_text(
        json.dumps(
            {
                "run_id": 500,
                "run_attempt": 2,
                "jobs": [
                    {"id": 10, "run_id": 999, "run_attempt": 2},
                    {"id": 10, "run_id": 500, "run_attempt": 2},
                ],
            }
        ),
        encoding="utf-8",
    )
    bad_job_res = verify_report.build_verify_report(ci_performances=[ci_bad])
    assert bad_job_res["correlation"]["overall_status"] == "mismatch_detected"
    bad_reasons = {c["reason"] for c in bad_job_res["sources"][0]["internal_conflicts"]}
    assert "ci_performance_duplicate_job_id" in bad_reasons
    assert "ci_performance_job_run_mismatch" in bad_reasons

    # I-L02: Non-int run_id / run_attempt must be invalid and never produce execution_matched.
    ci_str1 = tmp_path / "ci-str1.json"
    ci_str2 = tmp_path / "ci-str2.json"
    ci_str1.write_text('{"run_id": "x", "run_attempt": "y"}', encoding="utf-8")
    ci_str2.write_text('{"run_id": "x", "run_attempt": "y"}', encoding="utf-8")
    il02_res = verify_report.build_verify_report(ci_performances=[ci_str1, ci_str2])
    assert il02_res["correlation"]["overall_status"] == "mismatch_detected"
    for src in il02_res["sources"]:
        assert src["status"] == "invalid"
        assert src["ci_run_id"] is None
        assert src["ci_run_attempt"] is None
        assert src["query_correlation"]["same_execution_proven"] is False
        assert src["query_correlation"]["strength"] == "conflict"

    # I-L03: #87 A.1 bound receipts with self-reported execution_ref (no #87 B proof) never yield execution_matched.
    t1 = tmp_path / "trace-one.json"
    t2 = tmp_path / "trace-two.json"
    t1.write_text(
        json.dumps(
            {
                "correlation": {"status": "bound", "matched_trace_id": "trace-one"},
                "execution_ref": "execution:e",
            }
        ),
        encoding="utf-8",
    )
    t2.write_text(
        json.dumps(
            {
                "correlation": {"status": "bound", "matched_trace_id": "trace-two"},
                "execution_ref": "execution:e",
            }
        ),
        encoding="utf-8",
    )
    il03_res = verify_report.build_verify_report(dagger_telemetries=[t1, t2])
    assert il03_res["correlation"]["overall_status"] == "uncorrelated"
    for src in il03_res["sources"]:
        assert src["status"] == "loaded"
        assert src["execution_ref"] is None
        assert src["declared_identity"]["execution_ref"] == "execution:e"
        assert (
            src["execution_provenance_status"]
            == "unverified_self_reported_execution_ref_without_b_proof"
        )
        assert src["query_correlation"]["same_execution_proven"] is False

    # Self-reported #87 B fields cannot establish an execution binding.
    t_b_ok = tmp_path / "trace-b-ok.json"
    t_b_non_iso = tmp_path / "trace-b-non-iso.json"
    rep_b = tmp_path / "exec-b.json"
    rep_b.write_bytes(
        hooks.canonical_event_bytes(
            {
                "kind": "engineering-ci-execution-report",
                "schema_version": 1,
                "status": "passed",
                "exit_code": 0,
                "checks": [],
                "execution_identity": {
                    "run_ref": "run:b",
                    "attempt_ref": "att:1",
                    "execution_ref": "exec:b",
                },
            }
        )
    )
    t_b_ok.write_text(
        json.dumps(
            {
                "correlation": {
                    "status": "bound",
                    "matched_trace_id": "trace-b",
                    "isolated_capture": True,
                },
                "source_binding": {"source_binding_status": "verified"},
                "origin_execution": {"status": "verified"},
                "cache_observation": {"isolated_capture": True},
                "execution_ref": "exec:b",
            }
        ),
        encoding="utf-8",
    )
    t_b_non_iso.write_text(
        json.dumps(
            {
                "correlation": {
                    "status": "bound",
                    "matched_trace_id": "trace-c",
                    "isolated_capture": False,
                },
                "source_binding": {"source_binding_status": "verified"},
                "origin_execution": {"status": "verified"},
                "cache_observation": {"isolated_capture": False},
                "execution_ref": "exec:b",
            }
        ),
        encoding="utf-8",
    )
    b_match = verify_report.build_verify_report(
        execution_reports=[rep_b], dagger_telemetries=[t_b_ok, t_b_non_iso]
    )
    assert b_match["correlation"]["execution_groups"] == []
    assert not b_match["sources"][0]["verifiable_execution_proven"]
    assert b_match["sources"][1]["execution_ref"] is None
    assert b_match["sources"][2]["execution_provenance_status"] == "cache_not_isolated"
    assert b_match["sources"][2]["execution_ref"] is None


def test_verify_report_round6_q14_keys_missing_siblings_parent_conflict_and_activation(
    tmp_path,
):
    # Canonical tuple key collision guard: 'a|b'+'c' != 'a'+'b|c'.
    col_1 = tmp_path / "col1.json"
    col_2 = tmp_path / "col2.json"
    for target, rref, aref, eref in (
        (col_1, "run:a|b", "att:c", "exec:c1"),
        (col_2, "run:a", "att:b|c", "exec:c2"),
    ):
        target.write_bytes(
            hooks.canonical_event_bytes(
                {
                    "kind": "engineering-ci-execution-report",
                    "schema_version": 1,
                    "status": "passed",
                    "exit_code": 0,
                    "checks": [],
                    "execution_identity": {
                        "run_ref": rref,
                        "attempt_ref": aref,
                        "execution_ref": eref,
                    },
                }
            )
        )
    assert (
        verify_report.build_verify_report(execution_reports=[col_1, col_2])[
            "correlation"
        ]["overall_status"]
        == "uncorrelated"
    )

    # Mixed int vs str field values in scoped conflicts never raise TypeError.
    mix_1 = tmp_path / "mix1.json"
    mix_2 = tmp_path / "mix2.json"
    mix_1.write_bytes(col_1.read_bytes())
    mix_2.write_bytes(
        hooks.canonical_event_bytes(
            {
                "kind": "engineering-ci-execution-report",
                "schema_version": 1,
                "status": "failed",
                "exit_code": "1",
                "checks": [],
                "execution_identity": {
                    "run_ref": "run:a|b",
                    "attempt_ref": "att:c",
                    "execution_ref": "exec:c1",
                },
            }
        )
    )
    mix_res = verify_report.build_verify_report(execution_reports=[mix_1, mix_2])
    assert mix_res["correlation"]["overall_status"] == "uncorrelated"
    assert not mix_res["correlation"]["scoped_conflicts"]

    # Split pre-commit missing sibling preserves multi_execution_hook & catches shared activation conflict.
    repo = tmp_path / "split-miss"
    _head, inv_file, binding = _create_split_precommit_repo(repo)
    ws_exec = json.loads(
        inv_file.with_suffix(".execution-check-whitespace").read_text(encoding="utf-8")
    )
    ws_bundle = tmp_path / "ws-miss-bundle"
    ws_bundle.mkdir()
    (ws_bundle / "report.json").write_bytes(
        hooks.canonical_event_bytes(ws_exec["report"])
    )
    fwd_sidecar = inv_file.with_suffix(".execution-check-forwarding")
    fwd_bytes = fwd_sidecar.read_bytes()
    fwd_sidecar.unlink()
    shared_act = inv_file.with_suffix(".activation")
    shared_act.write_bytes(
        inv_file.with_suffix(".activation-check-whitespace").read_bytes()
    )
    miss_res = verify_report.build_verify_report(
        hook_root=repo,
        invocation_ref=binding.invocation_ref,
        execution_reports=[ws_bundle],
    )
    assert miss_res["correlation"]["overall_status"] == "mismatch_detected"
    h_src = miss_res["sources"][0]
    assert h_src["expected_unit_count"] == 2
    assert h_src["query_correlation"]["container_kind"] == "multi_execution_hook"
    assert h_src["query_correlation"]["same_execution_proven"] is False
    h_reasons = {c["reason"] for c in h_src["internal_conflicts"]}
    assert "hook_expected_execution_missing" in h_reasons
    assert "hook_shared_activation_conflict" in h_reasons
    fwd_sidecar.write_bytes(fwd_bytes)
    shared_act.unlink()

    # Unverified tampered hook claiming healthy bundle execution_ref must NOT inject scoped_conflict onto healthy bundle.
    ws_sidecar = inv_file.with_suffix(".execution-check-whitespace")
    tampered_ws = json.loads(ws_sidecar.read_text(encoding="utf-8"))
    tampered_ws["report"]["exit_code"] = 77
    ws_sidecar.write_bytes(hooks.canonical_event_bytes(tampered_ws))
    iso_res = verify_report.build_verify_report(
        hook_root=repo,
        invocation_ref=binding.invocation_ref,
        execution_reports=[ws_bundle],
    )
    assert iso_res["correlation"]["scoped_conflicts"] == []
    assert (
        iso_res["sources"][1]["query_correlation"]["strength"] == "shared_context_only"
    )
    assert iso_res["sources"][1]["query_correlation"]["same_execution_proven"] is False

    # Parent execution_bundle integrity conflict revokes child verified_manifest_digest.
    bad_parent = tmp_path / "parent-conflict-bundle"
    bad_parent.mkdir()
    j_xml = b'<testsuite tests="1" failures="0" errors="0" skipped="0" time="0.1"><testcase name="t" time="0.1"/></testsuite>'
    (bad_parent / "junit.xml").write_bytes(j_xml)
    (bad_parent / "exit-code.txt").write_text("99\n", encoding="utf-8")
    (bad_parent / "report.json").write_bytes(
        hooks.canonical_event_bytes(
            {
                "kind": "engineering-ci-execution-report",
                "schema_version": 1,
                "status": "passed",
                "exit_code": 0,
                "checks": [],
                "junit": j_xml.decode("utf-8"),
                "execution_identity": {
                    "run_ref": "run:pc",
                    "attempt_ref": "att:1",
                    "execution_ref": "exec:pc",
                },
            }
        )
    )
    pc_res = verify_report.build_verify_report(execution_reports=[bad_parent])
    assert pc_res["correlation"]["overall_status"] == "mismatch_detected"
    child_j = next(s for s in pc_res["sources"] if s["source_type"] == "junit_xml")
    assert child_j["bundle_binding"] == "colocated_unverified"
    assert (
        child_j["revoked_manifest_binding_reason"] == "parent_bundle_integrity_conflict"
    )
    assert child_j["query_correlation"]["same_execution_proven"] is False


def test_verify_report_round6_core_junit_manifest_and_post_check_source_rejection(
    tmp_path, monkeypatch
):
    # core-junit.xml matching junit.xml bytes MUST still fail if manifest expects different sha256.
    b_core = tmp_path / "core-alias-mismatch"
    b_core.mkdir()
    j_bytes = b'<testsuite tests="1" failures="0" errors="0" skipped="0" time="0.1"><testcase name="t" time="0.1"/></testsuite>'
    j_sha = hashlib.sha256(j_bytes).hexdigest()
    (b_core / "junit.xml").write_bytes(j_bytes)
    (b_core / "core-junit.xml").write_bytes(j_bytes)
    (b_core / "report.json").write_bytes(
        hooks.canonical_event_bytes(
            {
                "kind": "engineering-ci-execution-report",
                "schema_version": 1,
                "status": "passed",
                "exit_code": 0,
                "checks": [],
                "execution_identity": {
                    "run_ref": "run:ca",
                    "attempt_ref": "att:1",
                    "execution_ref": "exec:ca",
                },
                "artifacts_manifest": [
                    {"path": "junit.xml", "sha256": j_sha},
                    {"path": "core-junit.xml", "sha256": "0" * 64},
                ],
            }
        )
    )
    core_res = verify_report.build_verify_report(execution_reports=[b_core])
    assert core_res["correlation"]["overall_status"] == "mismatch_detected"
    assert any(
        s.get("bundle_relative_path") == "core-junit.xml"
        and any(
            c["reason"] == "bundle_artifact_sha256_mismatch"
            for c in s["internal_conflicts"]
        )
        for s in core_res["sources"]
    )

    # A native result stays visible even if restoring the Index lets a later
    # consumer re-open the original invocation.
    import pytest
    from test_native_hooks import INDEX_SELECTION, _approve_profiles, _source

    repo = tmp_path / "post-rej-repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "lefthook.yml").write_text("min_version: '2.2.2'\n", encoding="utf-8")
    mod = _source(repo, "mod", ("whitespace",))
    mod.write_text("VALUE = 1\n", encoding="utf-8")
    _approve_profiles(
        repo,
        INDEX_SELECTION,
        [("ci-effective-profile.json", "index", "index", "HEAD", ("whitespace",))],
    )
    subprocess.run(
        [
            "git",
            "add",
            "ci-effective-profile.json",
            "ci-hygiene.toml",
            "ci-selection.toml",
            "lefthook.yml",
            "src",
            "tach.toml",
        ],
        cwd=repo,
        check=True,
    )
    assert hooks.main(["capture", "pre-commit", "--root", str(repo)]) == 0
    inv_dir = repo / ".git" / "eci-hooks" / "invocations"
    (inv_file,) = inv_dir.glob("*.json")
    binding = hooks.load_invocation_binding(inv_file)
    assert hook_checks.prepare(repo, binding)["exit_code"] == 0

    index = repo / ".git" / "index"
    captured_index = index.read_bytes()
    orig_worker = hook_checks._source_worker

    def mutating_worker(root, tree, head, op, payload):
        res = orig_worker(root, tree, head, op, payload)
        (root / "drifted.txt").write_text("drift\n", encoding="utf-8")
        subprocess.run(["git", "add", "drifted.txt"], cwd=root, check=True)
        return res

    monkeypatch.setattr(hook_checks, "_source_worker", mutating_worker)
    with pytest.raises(hooks.HookEventError, match="index_changed_during_check"):
        hook_checks.run_checks(repo, binding)

    assert not inv_file.with_suffix(".execution").exists()
    assert inv_file.with_suffix(".observation").is_file()
    timing_run = json.loads(
        inv_file.with_suffix(".timing-run").read_text(encoding="utf-8")
    )
    assert timing_run["status"] == "failed"
    assert timing_run["source_rejection"] == "index_changed_during_check"
    assert timing_run["exit_code"] == 0
    monkeypatch.setattr(hook_checks, "_source_worker", orig_worker)
    index.write_bytes(captured_index)
    assert hook_checks.reconcile(repo, binding)["exit_code"] != 0
    with pytest.raises(hooks.HookEventError, match="hook_report_missing"):
        hook_checks.consume_index_result(repo, binding)

    post_rep = verify_report.build_verify_report(
        hook_root=repo, invocation_ref=binding.invocation_ref
    )
    hook_source = post_rep["sources"][0]
    assert not hook_source["executions"]
    assert hook_source["raw_observations"][0]["exit_code"] == 0
    assert hook_source["missing_expected_executions"] == [
        inv_file.with_suffix(".execution").name
    ]

    another = hooks.persist_event(repo, hooks.capture_pre_commit(repo))
    another_binding = hooks.load_invocation_binding(another.invocation_path)
    assert hook_checks.prepare(repo, another_binding)["exit_code"] == 0
    original_artifact = hook_checks._artifact

    def fail_seal(binding, suffix, payload=None, *, exclusive=False):
        if suffix == ".execution" and payload is not None:
            raise OSError("execution write failed")
        return original_artifact(binding, suffix, payload, exclusive=exclusive)

    monkeypatch.setattr(hook_checks, "_artifact", fail_seal)
    with pytest.raises(OSError, match="execution write failed"):
        hook_checks.run_checks(repo, another_binding)
    observed = verify_report.build_verify_report(
        hook_invocation=another.invocation_path
    )["sources"][0]
    assert observed["raw_observations"][0]["exit_code"] == 0
    assert not observed["executions"]
    run_stage = next(
        stage for stage in observed["stage_timings"] if stage["stage"] == "run"
    )
    assert run_stage["exit_code"] == 0
    assert run_stage["report_status"] == "seal_failed"


def test_verify_report_round6_lifecycle_lefthook_pytest_contracts_168_and_cli(
    tmp_path,
):
    import importlib.util
    import sys

    import pytest

    from engineering_ci import pytest_contracts

    # 1. verify-timing.json + verify-status.json coexistence & corrupt-timing fallback.
    coexist = tmp_path / "coexist"
    coexist.mkdir()
    out_b = b"summary: (done in 0.10 seconds)\n"
    err_b = b""
    (coexist / "lefthook.stdout").write_bytes(out_b)
    (coexist / "lefthook.stderr").write_bytes(err_b)
    (coexist / "verify-timing.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "invocation_id": "v-co",
                "observed_git_head": "a" * 40,
                "native_started": True,
                "native_exit_code": 0,
                "native_exit_source": "natural_exit",
                "outer_exit_code": 0,
                "elapsed_seconds": 0.1,
                "time_source_verified": False,
                "native_outputs": {
                    "stdout": {
                        "path": "lefthook.stdout",
                        "sha256": hashlib.sha256(out_b).hexdigest(),
                    },
                    "stderr": {
                        "path": "lefthook.stderr",
                        "sha256": hashlib.sha256(err_b).hexdigest(),
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    (coexist / "verify-status.json").write_text(
        json.dumps({"status": "seal_failed", "native_exit_code": 0}), encoding="utf-8"
    )
    co_rep = verify_report.build_verify_report(verify_dir=coexist)
    assert co_rep["correlation"]["overall_status"] == "mismatch_detected"
    assert co_rep["sources"][0]["evidence_complete"] is False
    assert co_rep["sources"][0]["timing_receipt_present"] is True
    assert co_rep["sources"][0]["status_receipt_present"] is True
    assert any(
        c["reason"] == "verify_timing_status_conflict"
        for c in co_rep["sources"][0]["internal_conflicts"]
    )

    corrupt_v = tmp_path / "corrupt-v"
    corrupt_v.mkdir()
    (corrupt_v / "verify-timing.json").write_text("{corrupt", encoding="utf-8")
    (corrupt_v / "verify-status.json").write_text(
        json.dumps(
            {
                "status": "seal_failed",
                "native_started": True,
                "native_exit_code": 2,
                "native_exit_source": "natural_exit",
                "outer_exit_code": 2,
            }
        ),
        encoding="utf-8",
    )
    cor_rep = verify_report.build_verify_report(verify_dir=corrupt_v)
    c_reasons = {c["reason"] for c in cor_rep["sources"][0]["internal_conflicts"]}
    assert "verify_timing_invalid" in c_reasons
    assert "verify_timing_status_conflict" in c_reasons
    assert cor_rep["sources"][0]["status"] == "seal_failed"
    assert cor_rep["sources"][0]["native_exit_code"] == 2

    # 2. Lefthook Group.Duration cumulative_child_durations, paren job name, and bytes_parsed.
    lh_log = tmp_path / "lh.stdout"
    lh_text = (
        "summary: (done in 1.50 seconds)\n"
        "✔️ grp (2.20 seconds)\n"
        "  ✔️ lint (ruff) (1.20 seconds)\n"
        "  ✔️ test (1.00 seconds)\n"
    )
    lh_log.write_text(lh_text, encoding="utf-8", newline="\n")
    lh_parsed = verify_report.parse_lefthook_output(
        lh_log,
        verify_config={
            "parallel": True,
            "jobs": [
                {
                    "name": "grp",
                    "group": {
                        "parallel": True,
                        "jobs": [{"name": "lint (ruff)"}, {"name": "test"}],
                    },
                }
            ],
        },
    )
    assert lh_parsed["total_duration_semantics"] == "lefthook_reported_wall_seconds"
    assert lh_parsed["bytes_parsed"]["stdout"] == len(lh_text.encode("utf-8"))
    grp_node = lh_parsed["items"][0]
    assert grp_node["kind"] == "group"
    assert grp_node["duration_semantics"] == "cumulative_child_durations"
    assert grp_node["wall_clock_seconds"] == "unknown"
    assert grp_node["children"][0]["name"] == "lint (ruff)"

    # 3. Strict JUnit + ReportLog completeness + pytest_contracts parity with Dagger pytest_report.py.
    for fname, bad_xml in (
        ("nonjunit.xml", "<foo/>"),
        ("negcount.xml", '<testsuite tests="-5"/>'),
    ):
        xp = tmp_path / fname
        xp.write_text(bad_xml, encoding="utf-8")
        assert verify_report._parse_junit_xml(xp)["status"] == "invalid"

    nested_xml = tmp_path / "nested.xml"
    nested_xml.write_text(
        '<testsuites><testsuite tests="2" failures="0" errors="0" skipped="0">'
        '<testsuite tests="1" failures="1" errors="0" skipped="0"/>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    assert verify_report._parse_junit_xml(nested_xml)["counts"]["tests"] == 2
    assert verify_report._parse_junit_xml(nested_xml)["counts"]["failures"] == 0

    fin_only = tmp_path / "fin-only.jsonl"
    fin_only.write_text(
        json.dumps({"$report_type": "SessionFinish", "exitstatus": 0}) + "\n",
        encoding="utf-8",
    )
    assert (
        verify_report._parse_pytest_reportlog(fin_only)["report_completeness"]
        == "partial"
    )

    empty_session = tmp_path / "empty-session.jsonl"
    empty_session.write_text(
        json.dumps({"$report_type": "SessionStart"})
        + "\n"
        + json.dumps({"$report_type": "SessionFinish", "exitstatus": 0})
        + "\n",
        encoding="utf-8",
    )
    assert (
        verify_report._parse_pytest_reportlog(empty_session)["report_completeness"]
        == "partial"
    )

    collect_err = tmp_path / "collect-err.jsonl"
    collect_err.write_text(
        "\n".join(
            [
                json.dumps({"$report_type": "SessionStart", "created_at": 1.0}),
                json.dumps(
                    {
                        "$report_type": "CollectReport",
                        "nodeid": "tests/test_bad.py",
                        "when": "collect",
                        "outcome": "failed",
                    }
                ),
                json.dumps(
                    {
                        "$report_type": "SessionFinish",
                        "exitstatus": 2,
                        "created_at": 2.0,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    ce_parsed = verify_report._parse_pytest_reportlog(collect_err)
    assert ce_parsed["report_completeness"] == "unverified_collection"
    assert ce_parsed["has_collection"] is True
    assert ce_parsed["collection_failed_count"] == 1
    assert ce_parsed["slowest_phases_scope"] == "top_25_phases_not_exhaustive"

    parser_path = (
        Path(__file__).resolve().parents[1]
        / ".dagger"
        / "src"
        / "engineering_ci_module"
        / "pytest_report.py"
    )
    spec = importlib.util.spec_from_file_location("pytest_report_parity", parser_path)
    assert spec is not None and spec.loader is not None
    pr_mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = pr_mod
    spec.loader.exec_module(pr_mod)
    for xml_in, code_in, out_in in (
        ('<testsuite tests="1" failures="0" errors="0" skipped="0"/>', 0, "ok"),
        (
            '<testsuite tests="1" failures="1" errors="0" skipped="0"><testcase classname="a" name="b"><failure>assert 1 == 2</failure></testcase></testsuite>',
            1,
            "fail",
        ),
        ("", 2, "crash"),
        ('<testsuite tests="0" failures="0" errors="0" skipped="0"/>', 5, "empty"),
    ):
        dagger_payload = pr_mod.summarize_pytest(xml_in, code_in, out_in)
        assert pytest_contracts.is_native_pytest_v1_shape(dagger_payload) is True
        assert pytest_contracts.validate_native_pytest_v1(dagger_payload) is None

    # 4. #168 explicit pytest invocation directory + no latest scan + CLI arg checks.
    p168_parent = tmp_path / "artifacts-pytest"
    p168_inv = p168_parent / "pytest-inv-1"
    p168_inv.mkdir(parents=True)
    meta_168 = {
        "kind": "engineering-ci-pytest-invocation",
        "schema_version": 1,
        "invocation_id": "pytest-inv-1",
        "pytest_version": "9.0.3",
        "collect_only": False,
        "selected_items": 4,
        "deselected_items": 1,
        "wall_time_seconds": 0.42,
        "exitstatus": 0,
        "session_finished": True,
        "report_log": str(p168_inv / "custom-report.jsonl"),
        "junit_xml": str(p168_inv / "junit.xml"),
        "argv": ["-q"],
    }
    (p168_inv / "metadata.json").write_text(json.dumps(meta_168), encoding="utf-8")
    (p168_inv / "summary.json").write_text(json.dumps(meta_168), encoding="utf-8")
    (p168_inv / "custom-report.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"$report_type": "SessionStart", "created_at": 1.0}),
                json.dumps(
                    {
                        "$report_type": "TestReport",
                        "nodeid": "tests/a.py::t1",
                        "when": "setup",
                        "outcome": "passed",
                        "duration": 0.01,
                    }
                ),
                json.dumps(
                    {
                        "$report_type": "TestReport",
                        "nodeid": "tests/a.py::t1",
                        "when": "call",
                        "outcome": "passed",
                        "duration": 0.02,
                    }
                ),
                json.dumps(
                    {
                        "$report_type": "TestReport",
                        "nodeid": "tests/a.py::t1",
                        "when": "teardown",
                        "outcome": "passed",
                        "duration": 0.01,
                    }
                ),
                json.dumps(
                    {
                        "$report_type": "SessionFinish",
                        "exitstatus": 0,
                        "created_at": 1.5,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    parent_scan = verify_report.build_verify_report(pytest_invocations=[p168_parent])
    assert parent_scan["sources"][0]["status"] == "missing_invocation_metadata"
    assert (
        parent_scan["sources"][0]["note"]
        == "directory_scan_for_latest_forbidden_pass_explicit_invocation_dir"
    )

    out_168 = tmp_path / "out-168.json"
    assert (
        main(
            [
                "verify",
                "report",
                "--pytest-invocation",
                str(p168_inv),
                "--out",
                str(out_168),
            ]
        )
        == 0
    )
    loaded_168 = json.loads(out_168.read_text(encoding="utf-8"))
    assert loaded_168["correlation"]["overall_status"] == "shared_context_only"
    inv_src = next(
        s for s in loaded_168["sources"] if s["source_type"] == "pytest_invocation"
    )
    assert inv_src["selected_items"] == 4
    assert inv_src["deselected_items"] == 1
    assert inv_src["wall_time_seconds"] == 0.42
    child_src = next(
        s for s in loaded_168["sources"] if s["source_type"] == "pytest_reportlog"
    )
    assert child_src["bundle_relative_path"] == "custom-report.jsonl"
    assert child_src["report_completeness"] == "unverified_collection"

    # Refuse overwriting files inside --pytest-invocation store.
    assert (
        main(
            [
                "verify",
                "report",
                "--pytest-invocation",
                str(p168_inv),
                "--out",
                str(p168_inv / "derived.json"),
            ]
        )
        == 2
    )

    # CLI argument pairing / mutual-exclusion validation.
    for bad_argv in (
        ["--hook-root", str(tmp_path)],
        ["--invocation-ref", "invocation:" + ("0" * 32)],
        [
            "--hook-invocation",
            str(tmp_path / "inv.json"),
            "--hook-root",
            str(tmp_path),
            "--invocation-ref",
            "invocation:" + ("0" * 32),
        ],
    ):
        with pytest.raises(SystemExit) as exc_info:
            verify_report.run_report_cli(bad_argv)
        assert exc_info.value.code == 2
