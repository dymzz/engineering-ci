"""Tests for bounded Lefthook parsing and read-only verify report correlation."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from engineering_ci import hook_checks, hooks, verify_report
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

    capsys_disabled = None
    _ = capsys_disabled
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
    assert two_hooks["overall_status"] == "content_shared_distinct_execution"

    # #86 bundle dir with colocated unverified junit.xml & host/report.json digest check
    bundle_dir = tmp_path / "bundle-86"
    bundle_dir.mkdir()
    (bundle_dir / "host").mkdir()
    host_raw = json.dumps({"status": "passed", "exit_code": 0}).encode("utf-8")
    (bundle_dir / "host" / "report.json").write_bytes(host_raw)
    plan_payload = {
        "schema_version": 2,
        "git": {"head_commit": head, "source_tree_id": "sha256:" + "c" * 64},
    }
    exec_report = {
        "kind": "engineering-ci-execution-report",
        "schema_version": 1,
        "status": "passed",
        "exit_code": 0,
        "duration_seconds": 0.4,
        "execution_identity": {
            "run_ref": "run:42",
            "attempt_ref": "att:1",
            "execution_ref": "exec:42",
        },
        "plan": {"git": plan_payload["git"]},
        "source_handoff": {
            "host_report_sha256": hashlib.sha256(host_raw).hexdigest(),
        },
    }
    (bundle_dir / "plan.json").write_bytes(hooks.canonical_event_bytes(plan_payload))
    (bundle_dir / "report.json").write_bytes(hooks.canonical_event_bytes(exec_report))
    (bundle_dir / "exit-code.txt").write_text("0\n", encoding="utf-8")
    (bundle_dir / "junit.xml").write_text(
        '<testsuite tests="1" time="0.2"><testcase name="t" time="0.2"/></testsuite>',
        encoding="utf-8",
    )

    # Also test #87 unbound telemetry preserved verbatim & split execution_reports 1-to-many
    exec_report_2 = {
        "kind": "engineering-ci-execution-report",
        "schema_version": 1,
        "status": "passed",
        "exit_code": 0,
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

    # Same execution_ref with conflicting report_ref triggers exit 2 conflict
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
        == 2
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
        '<testsuite tests="3"><testcase name="a" time="-1.5"/>'
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
    from test_native_hooks import INDEX_SELECTION, _approve_profiles, _source

    repo = tmp_path / "repo"
    repo.mkdir()
    head = _init_git_repo(repo)
    (repo / "lefthook.yml").write_text("min_version: '2.2.2'\n", encoding="utf-8")
    mod_file = _source(repo, "mod", ("whitespace", "forwarding"))
    mod_file.write_text("VALUE = 1\n", encoding="utf-8")
    _approve_profiles(
        repo,
        INDEX_SELECTION,
        [
            (
                "ci-effective-profile.json",
                "index",
                "index",
                "HEAD",
                ("whitespace", "forwarding"),
            )
        ],
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
    assert hook_checks.prepare(repo, binding, split_checks=True)["exit_code"] == 0
    assert (
        hook_checks.run_checks(repo, binding, job_ref="check-whitespace")["exit_code"]
        == 0
    )
    assert (
        hook_checks.run_checks(repo, binding, job_ref="check-forwarding")["exit_code"]
        == 0
    )
    assert hook_checks.reconcile(repo, binding)["exit_code"] == 0
    assert hook_checks.consume_index_result(repo, binding)["exit_code"] == 0

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
    assert hook_entry["query_correlation"]["strength"] == "execution_matched"
    assert bundle_entry["query_correlation"]["strength"] == "execution_matched"
    assert telem_entry["matched_session_id"] == "sess-live-1"
    assert telem_entry["anchor_span_id"] == "span-anchor-1"
    assert telem_entry["query_correlation"]["same_execution_proven"] is False
    assert len(hook_entry["executions"]) == 2
    assert hook_entry["receipt"]["native_status"] == "matched"
    assert hook_entry["receipt"]["verifiable_evidence_status"] == "verified"

    # Homogeneous execution pair without unrelated sources yields overall execution_matched.
    pure_match = verify_report.build_verify_report(
        hook_root=repo,
        invocation_ref=binding.invocation_ref,
        execution_reports=[exact_bundle],
    )
    assert pure_match["correlation"]["overall_status"] == "execution_matched"

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
    rep_c = {
        "kind": "engineering-ci-execution-report",
        "schema_version": 1,
        "status": "passed",
        "exit_code": 0,
        "execution_identity": {
            "run_ref": "run:other",
            "attempt_ref": "att:other",
            "execution_ref": "exec:other-group",
        },
    }
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
    ctx_a.write_bytes(
        hooks.canonical_event_bytes(
            {
                "kind": "engineering-ci-execution-report",
                "schema_version": 1,
                "status": "passed",
                "exit_code": 0,
                "execution_identity": {
                    "run_ref": "run:shared",
                    "attempt_ref": "att:1",
                    "execution_ref": "exec:job-1",
                },
            }
        )
    )
    ctx_b.write_bytes(
        hooks.canonical_event_bytes(
            {
                "kind": "engineering-ci-execution-report",
                "schema_version": 1,
                "status": "passed",
                "exit_code": 0,
                "execution_identity": {
                    "run_ref": "run:shared",
                    "attempt_ref": "att:1",
                    "execution_ref": "exec:job-2",
                },
            }
        )
    )
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
                "correlation": {
                    "status": "bound",
                    "matched_trace_id": "trace-shared-1",
                    "anchor_span_id": "span-1",
                }
            }
        ),
        encoding="utf-8",
    )
    trace_2.write_text(
        json.dumps(
            {
                "correlation": {
                    "status": "bound",
                    "matched_trace_id": "trace-shared-1",
                    "anchor_span_id": "span-2",
                }
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
        b'<testsuite tests="1" time="0.1"><testcase name="t_same" time="0.1">'
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
    assert (
        same_junit_bundles["correlation"]["overall_status"]
        == "partial_execution_matched"
    )
    assert (
        same_junit_bundles["correlation"]["reason"]
        == "multiple_disconnected_execution_groups"
    )
    assert len(same_junit_bundles["correlation"]["execution_groups"]) == 2
    j_sha_sources = [
        s for s in same_junit_bundles["sources"] if s["source_type"] == "junit_xml"
    ]
    assert len(j_sha_sources) == 2
    assert {j["query_correlation"]["execution_group_index"] for j in j_sha_sources} == {
        0,
        1,
    }
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
        standalone_same_junit["correlation"]["overall_status"]
        == "content_shared_distinct_execution"
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
        b'<testsuite tests="1" time="0.1"><testcase name="t1" time="0.1">'
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
        "schema_version": 2,
        "git": {
            "mode": "index",
            "head_commit": head,
            "source_tree_id": "sha256:" + "c" * 64,
            "index_tree": "1" * 40,
            "execution_tree": "1" * 40,
        },
        "selection": {"checks": ["whitespace"], "tests": []},
        "prerequisite_actions": {
            "codegen": {"type": "command"},
            "stamp": {"type": "command"},
        },
        "dependency_contract": {
            "edges": [
                {
                    "producer_ref": rec["producer_ref"],
                    "consumer_ref": rec["consumer_ref"],
                    "dependency_kind": "data",
                    "output_ref": rec["output_ref"],
                }
                for rec in closure_records
            ]
        },
    }
    plan86_bytes = hooks.canonical_event_bytes(plan86)
    (real_86 / "plan.json").write_bytes(plan86_bytes)
    exec86_ident = {
        "run_ref": "run:real-86",
        "attempt_ref": "att:1",
        "execution_ref": "exec:real-86",
    }
    handoff_payload = eci_execution.build_producer_artifact_handoff(
        plan86,
        closure_records=closure_records,
        prerequisite_results=[
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
        ],
        execution_identity=exec86_ident,
        execution_context="dagger_export",
        retention_metadata=retention_meta,
    )
    real_86_rep = {
        "kind": "engineering-ci-execution-report",
        "schema_version": 1,
        "status": "passed",
        "exit_code": 0,
        "execution_identity": exec86_ident,
        "plan": {"git": plan86["git"]},
        "source_handoff": {
            "host_report_sha256": hashlib.sha256(host_raw86).hexdigest()
        },
        "junit": junit_bytes.decode("utf-8"),
        "producer_artifact_handoff": handoff_payload,
    }
    (real_86 / "report.json").write_bytes(hooks.canonical_event_bytes(real_86_rep))
    (real_86 / "exit-code.txt").write_text("0\n", encoding="utf-8")
    real_86_res = verify_report.build_verify_report(execution_reports=[real_86])
    assert real_86_res["correlation"]["overall_status"] == "execution_matched"
    b86_src = next(
        s for s in real_86_res["sources"] if s["source_type"] == "execution_bundle"
    )
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
        b'<testsuite tests="1" time="0.1"><properties>'
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
    assert push_report["correlation"]["overall_status"] != "mismatch_detected"
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
        == "mismatch_detected"
    )

    # P1-03 standalone _pytest_bundle deduplicates core-junit.xml alias cleanly.
    std_pytest = tmp_path / "standalone-pytest-bundle"
    std_pytest.mkdir()
    std_xml = b'<testsuite tests="1" time="0.1"><testcase name="t_std" time="0.1"/></testsuite>'
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
    assert std_res["correlation"]["overall_status"] == "execution_matched"
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
