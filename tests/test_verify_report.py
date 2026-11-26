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
    ident = rep["execution_identity"]

    # 1. Exact Hook split-check execution report bundle + bound #87 telemetry -> execution_matched
    exact_bundle = tmp_path / "bundle-exact"
    exact_bundle.mkdir()
    (exact_bundle / "report.json").write_bytes(hooks.canonical_event_bytes(rep))

    telem = tmp_path / "telem-bound.json"
    telem.write_text(
        json.dumps(
            {
                "correlation": {"status": "bound", "matched_trace_id": "trace-live-1"},
                "source_binding": {
                    "source_binding_status": "unknown",
                    "declared_target_source_ref": "sha256:" + "d" * 64,
                },
                "run_ref": ident["run_ref"],
                "attempt_ref": ident["attempt_ref"],
                "execution_ref": ident["execution_ref"],
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
    assert matched["correlation"]["overall_status"] == "execution_matched"
    assert matched["correlation"]["scoped_conflicts"] == []
    for src in matched["sources"]:
        assert src["query_correlation"]["strength"] == "execution_matched"
        assert src["query_correlation"]["same_execution_proven"] is True
    hook_entry = next(
        s for s in matched["sources"] if s["source_type"] == "hook_invocation"
    )
    assert len(hook_entry["executions"]) == 2
    assert hook_entry["receipt"]["status"] == "matched"
    assert len(hook_entry["receipt"]["report_refs"]) >= 1

    # 2. Bundle with verified source_handoff artifact manifest binds junit.xml -> execution_matched
    manifest_bundle = tmp_path / "bundle-manifest"
    manifest_bundle.mkdir()
    junit_bytes = (
        b'<testsuite tests="1" time="0.1"><testcase name="t1" time="0.1"/></testsuite>'
    )
    (manifest_bundle / "junit.xml").write_bytes(junit_bytes)
    manifest_rep = {
        "kind": "engineering-ci-execution-report",
        "schema_version": 1,
        "status": "passed",
        "exit_code": 0,
        "execution_identity": {
            "run_ref": "run:manifest-1",
            "attempt_ref": "att:1",
            "execution_ref": "exec:manifest-1",
        },
        "source_handoff": {
            "artifacts": [
                {
                    "bundle_path": "junit.xml",
                    "sha256": hashlib.sha256(junit_bytes).hexdigest(),
                }
            ]
        },
    }
    (manifest_bundle / "report.json").write_bytes(
        hooks.canonical_event_bytes(manifest_rep)
    )
    manifest_res = verify_report.build_verify_report(
        execution_reports=[manifest_bundle]
    )
    assert manifest_res["correlation"]["overall_status"] == "execution_matched"
    junit_entry = next(
        s for s in manifest_res["sources"] if s["source_type"] == "junit_xml"
    )
    assert junit_entry["bundle_binding"] == "verified_manifest_digest"
