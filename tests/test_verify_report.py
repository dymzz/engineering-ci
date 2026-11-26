"""Tests for bounded Lefthook parsing and read-only verify report correlation."""

from __future__ import annotations

import json
import os
import subprocess

from engineering_ci import hooks, reconciliation, verify_report
from engineering_ci.cli import main


def test_parse_lefthook_missing_and_truncated_summary_marks_unknown(tmp_path):
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
    assert parsed["summary_present"] is False
    assert parsed["total_duration_seconds"] == "unknown"
    by_name = {job["name"]: job for job in parsed["jobs"]}
    assert by_name["lint"]["status"] == "unknown"
    assert by_name["lint"]["duration_seconds"] == "unknown"
    assert by_name["skip-me"]["status"] == "skipped"
    assert by_name["skip-me"]["duration_seconds"] == "unknown"
    assert by_name["unstarted-job"]["status"] == "unknown"
    assert by_name["unstarted-job"]["duration_seconds"] == "unknown"

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


def test_read_bounded_text_preserves_head_and_tail_beyond_limit(tmp_path, monkeypatch):
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
    assert by_name["big-job"]["status"] == "passed"
    assert by_name["big-job"]["duration_seconds"] == 1.25


def test_verify_report_correlates_explicit_keys_readonly_without_running_commands(
    tmp_path, monkeypatch
):
    commit_sha = "a" * 40
    source_tree_id = "sha256:" + "b" * 64
    trace_id = "trace-abc-123"
    run_ref = "hook-run:invocation:" + "1" * 32
    attempt_ref = "hook-attempt:invocation:" + "1" * 32
    execution_ref = "exec-ref-999"

    # 1. Verify artifact dir
    verify_dir = tmp_path / "verify-run"
    verify_dir.mkdir()
    (verify_dir / "verify-timing.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "invocation_id": "verify-1111",
                "observed_git_head": commit_sha,
                "started_at_ns": 1000,
                "completed_at_ns": 2000,
                "time_source_verified": False,
                "native_exit_code": 0,
                "elapsed_seconds": 1.25,
                "stages": {"cli_preflight": {"elapsed_seconds": 0.1}},
            }
        ),
        encoding="utf-8",
    )

    # 2. #86 bundle dir containing plan, report, junit, pytest-reportlog
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    plan_payload = {
        "schema_version": 2,
        "git": {"head_commit": commit_sha, "source_tree_id": source_tree_id},
    }
    plan_ref = hooks._digest_bytes(hooks.canonical_event_bytes(plan_payload))
    report_payload = {
        "kind": "engineering-ci-execution-report",
        "schema_version": 1,
        "status": "passed",
        "exit_code": 0,
        "duration_seconds": 0.9,
        "execution_identity": {
            "run_ref": run_ref,
            "attempt_ref": attempt_ref,
            "execution_ref": execution_ref,
        },
        "source_tree_id": source_tree_id,
    }
    report_ref = reconciliation.execution_report_ref(report_payload)
    (bundle_dir / "plan.json").write_bytes(hooks.canonical_event_bytes(plan_payload))
    (bundle_dir / "report.json").write_bytes(
        hooks.canonical_event_bytes(report_payload)
    )
    (bundle_dir / "junit.xml").write_text(
        f"""<testsuite tests="2" failures="0" time="0.45">
  <properties><property name="git_commit" value="{commit_sha}"/></properties>
  <testcase classname="t" name="test_fast" time="0.05"/>
  <testcase classname="t" name="test_slow" time="0.40"/>
</testsuite>""",
        encoding="utf-8",
    )
    (bundle_dir / "pytest-reportlog.jsonl").write_text(
        json.dumps({"$report_type": "SessionStart", "run_ref": run_ref})
        + "\n"
        + json.dumps(
            {
                "$report_type": "TestReport",
                "nodeid": "tests/t.py::test_slow",
                "when": "call",
                "outcome": "passed",
                "duration": 0.4,
            }
        )
        + "\n"
        + json.dumps({"$report_type": "SessionFinish", "exitstatus": 0})
        + "\n",
        encoding="utf-8",
    )

    # 3. #153 Hook invocation dir with capture..consume sidecars
    hook_git_dir = tmp_path / "repo.git"
    inv_dir = hook_git_dir / "eci-hooks" / "invocations"
    evt_dir = hook_git_dir / "eci-hooks" / "events"
    inv_dir.mkdir(parents=True)
    evt_dir.mkdir(parents=True)
    inv_id = "1" * 32
    inv_ref = f"invocation:{inv_id}"
    event_file = evt_dir / "evt.json"
    event_file.write_text(
        json.dumps({"hook": "pre-commit", "head_commit": commit_sha}),
        encoding="utf-8",
    )
    (inv_dir / f"{inv_id}.json").write_text(
        json.dumps(
            {
                "invocation_ref": inv_ref,
                "event_payload_ref": "event:sha256:" + "d" * 64,
                "event_file": str(event_file),
                "hook_name": "pre-commit",
            }
        ),
        encoding="utf-8",
    )
    (inv_dir / f"{inv_id}.plan").write_text(
        json.dumps({"invocation_ref": inv_ref, "plan_ref": plan_ref}),
        encoding="utf-8",
    )
    (inv_dir / f"{inv_id}.execution").write_text(
        json.dumps(
            {
                "invocation_ref": inv_ref,
                "plan_ref": plan_ref,
                "report": {
                    "execution_identity": {
                        "run_ref": run_ref,
                        "attempt_ref": attempt_ref,
                        "execution_ref": execution_ref,
                    },
                    "dagger_bundle": str(bundle_dir),
                },
            }
        ),
        encoding="utf-8",
    )
    (inv_dir / f"{inv_id}.receipt").write_text(
        json.dumps({"invocation_ref": inv_ref, "report_ref": report_ref}),
        encoding="utf-8",
    )
    for stage in ("capture", "prepare", "run", "reconcile", "consume"):
        (inv_dir / f"{inv_id}.timing-{stage}").write_text(
            json.dumps(
                {
                    "invocation_ref": inv_ref,
                    "stage": stage,
                    "elapsed_seconds": 0.1,
                    "status": "passed",
                }
            ),
            encoding="utf-8",
        )

    # 4. #87 Dagger Telemetry & #131 CI Performance
    telem_path = tmp_path / "dagger-telemetry.json"
    telem_path.write_text(
        json.dumps(
            {
                "correlation": {"matched_trace_id": trace_id},
                "source_binding": {"declared_target_source_ref": source_tree_id},
                "run_ref": run_ref,
                "attempt_ref": attempt_ref,
                "git_commit": commit_sha,
            }
        ),
        encoding="utf-8",
    )
    ci_perf_path = tmp_path / "ci-perf.json"
    ci_perf_path.write_text(
        json.dumps(
            {
                "run_id": 777,
                "run_attempt": 1,
                "sha": commit_sha,
                "workflow": "CI",
                "event": "pull_request",
                "url": "https://github.com/example/repo/actions/runs/777",
                "jobs": [{"name": "checks", "duration_seconds": 42.0}],
                "dagger": {"native_spans": 3},
            }
        ),
        encoding="utf-8",
    )

    out_report = tmp_path / "out" / "unified.json"
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must stay read-only")),
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must stay read-only")),
    )

    rc = main(
        [
            "verify",
            "report",
            "--verify-dir",
            str(verify_dir),
            "--hook-root",
            str(hook_git_dir),
            "--invocation-ref",
            inv_id,
            "--execution-report",
            str(bundle_dir),
            "--dagger-telemetry",
            str(telem_path),
            "--ci-performance",
            str(ci_perf_path),
            "--out",
            str(out_report),
        ]
    )
    assert rc == 0
    unified = json.loads(out_report.read_text(encoding="utf-8"))
    assert unified["read_only"] is True
    assert unified["commands_executed"] == []
    assert unified["time_source_verified"] is False
    assert unified["correlation"]["overall_status"] == "matched"
    assert unified["correlation"]["conflicting_keys"] == {}
    ci_src = next(s for s in unified["sources"] if s["source_type"] == "ci_performance")
    assert ci_src["ci_provider_provenance"]["authoritative_ci_end_state"] is False
    hook_src = next(
        s for s in unified["sources"] if s["source_type"] == "hook_invocation"
    )
    assert {s["stage"] for s in hook_src["stage_timings"]} == {
        "capture",
        "prepare",
        "run",
        "reconcile",
        "consume",
    }


def test_verify_report_never_matches_by_mtime_or_filename_and_flags_conflicts(
    tmp_path, capsys
):
    verify_dir = tmp_path / "same-ts"
    verify_dir.mkdir()
    (verify_dir / "verify-timing.json").write_text(
        json.dumps(
            {
                "invocation_id": "verify-1",
                "observed_git_head": "a" * 40,
                "elapsed_seconds": 1.0,
            }
        ),
        encoding="utf-8",
    )
    # Create a newer hook invocation in hook_root that must NEVER be auto-picked
    hook_git = tmp_path / "git"
    inv_dir = hook_git / "eci-hooks" / "invocations"
    inv_dir.mkdir(parents=True)
    newer = inv_dir / ("e" * 32 + ".json")
    newer.write_text(json.dumps({"invocation_ref": "invocation:" + "e" * 32}))

    standalone_junit = tmp_path / "verify-1-junit.xml"
    standalone_junit.write_text(
        '<testsuite tests="1" time="0.2"><testcase name="t1" time="0.2"/></testsuite>',
        encoding="utf-8",
    )
    os.utime(standalone_junit, (1_700_000_000, 1_700_000_000))
    os.utime(verify_dir / "verify-timing.json", (1_700_000_000, 1_700_000_000))

    partial = verify_report.build_verify_report(
        verify_dir=verify_dir,
        hook_root=hook_git,
        invocation_ref="f" * 32,
        junit_xmls=[standalone_junit],
    )
    assert partial["correlation"]["overall_status"] == "uncorrelated"
    hook_entry = next(
        s for s in partial["sources"] if s["source_type"] == "hook_invocation"
    )
    assert hook_entry["status"] == "missing"
    junit_entry = next(s for s in partial["sources"] if s["source_type"] == "junit_xml")
    assert junit_entry["correlation"]["status"] == "standalone_uncorrelated"

    # Mismatch on git_commit -> exit 2 and conflict recorded
    bad_ci = tmp_path / "bad-ci.json"
    bad_ci.write_text(json.dumps({"sha": "f" * 40, "run_id": 1}), encoding="utf-8")
    assert (
        main(
            [
                "verify",
                "report",
                "--verify-dir",
                str(verify_dir),
                "--ci-performance",
                str(bad_ci),
                "--format",
                "text",
            ]
        )
        == 2
    )
    out = capsys.readouterr().out
    assert "correlation: mismatch_detected" in out

    # DTD XML safely rejected
    dtd_xml = tmp_path / "dtd.xml"
    dtd_xml.write_text('<!DOCTYPE foo [<!ENTITY x "y">]><testsuite/>', encoding="utf-8")
    assert verify_report._parse_junit_xml(dtd_xml)["status"] == "rejected_dtd"
