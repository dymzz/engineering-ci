"""Official OpenTelemetry Collector Contrib ``githubreceiver`` integration tests (#75)."""

from __future__ import annotations

import copy
import http.client
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent import futures
from pathlib import Path

import grpc
import pytest
from google.protobuf import json_format
from opentelemetry.proto.collector.trace.v1 import (
    trace_service_pb2,
    trace_service_pb2_grpc,
)

from engineering_ci import github_receiver as gr

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR_YAML = ROOT / gr.COLLECTOR_CONFIG_RELPATH
WEAVER_MATCHER_TOML = ROOT / gr.WEAVER_MATCHER_RELPATH
WEAVER_FIXTURE_DIR = ROOT / "tests/fixtures/weaver-cicd"
SECRET = "eci-test-webhook-secret"


def _sample_job(
    *,
    job_id: int = 129847644459,
    run_id: int = 42821429094,
    attempt: int = 2,
    name: str = "python-ci / checks",
    conclusion: str = "success",
    steps: list[dict] | None = None,
) -> dict:
    return {
        "action": "completed",
        "workflow_job": {
            "id": job_id,
            "run_id": run_id,
            "run_attempt": attempt,
            "workflow_name": "Engineering CI",
            "head_branch": "main",
            "head_sha": "7ea85c24a62e65a9384a0abe9eb541ca54064cbc",
            "url": f"https://api.github.com/repos/dymzz/engineering-ci/actions/jobs/{job_id}",
            "html_url": f"https://github.com/dymzz/engineering-ci/actions/runs/{run_id}/job/{job_id}",
            "status": "completed",
            "conclusion": conclusion,
            "created_at": "2026-11-10T10:00:00Z",
            "started_at": "2026-11-10T10:00:05Z",
            "completed_at": "2026-11-10T10:01:15Z",
            "name": name,
            "runner_id": 1001 if conclusion != "cancelled" else 0,
            "runner_name": "GitHub Actions 1" if conclusion != "cancelled" else "",
            "runner_group_id": 2 if conclusion != "cancelled" else 0,
            "runner_group_name": "GitHub Actions" if conclusion != "cancelled" else "",
            "node_id": f"MDQ6Sm9i{job_id}",
            "labels": ["ubuntu-24.04"] if conclusion != "cancelled" else [],
            "steps": (
                steps
                if steps is not None
                else [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-11-10T10:00:05Z",
                        "completed_at": "2026-11-10T10:00:07Z",
                    },
                    {
                        "name": "Ruff Lint",
                        "status": "completed",
                        "conclusion": "failure"
                        if conclusion == "failure"
                        else "success",
                        "number": 2,
                        "started_at": "2026-11-10T10:00:08Z",
                        "completed_at": "2026-11-10T10:01:14Z",
                    },
                ]
            ),
        },
        "repository": {
            "id": 1,
            "name": "engineering-ci",
            "full_name": "dymzz/engineering-ci",
            "html_url": "https://github.com/dymzz/engineering-ci",
            "private": False,
        },
        "sender": {"login": "dymzz"},
    }


def _sample_run(
    *,
    run_id: int = 42821429094,
    attempt: int = 2,
    conclusion: str = "success",
    status: str = "completed",
    include_custom_props: bool = False,
) -> dict:
    repo = {
        "id": 1,
        "name": "engineering-ci",
        "full_name": "dymzz/engineering-ci",
        "html_url": "https://github.com/dymzz/engineering-ci",
        "private": False,
    }
    if include_custom_props:
        repo["custom_properties"] = {"service_name": "github-actions", "tier": "core"}
    return {
        "action": "completed",
        "workflow_run": {
            "id": run_id,
            "name": "Engineering CI",
            "head_branch": "main",
            "head_sha": "7ea85c24a62e65a9384a0abe9eb541ca54064cbc",
            "run_number": 900,
            "run_attempt": attempt,
            "event": "pull_request",
            "status": status,
            "conclusion": conclusion,
            "url": f"https://api.github.com/repos/dymzz/engineering-ci/actions/runs/{run_id}",
            "html_url": f"https://github.com/dymzz/engineering-ci/actions/runs/{run_id}",
            "previous_attempt_url": (
                f"https://api.github.com/repos/dymzz/engineering-ci/actions/runs/{run_id}/attempts/{attempt - 1}"
                if attempt > 1
                else None
            ),
            "created_at": "2026-11-10T10:00:00Z",
            "updated_at": "2026-11-10T10:01:20Z",
            "run_started_at": "2026-11-10T10:00:01Z",
            "referenced_workflows": (
                [
                    {
                        "path": "dymzz/engineering-ci/.github/workflows/reusable-python-ci.yml@main",
                        "sha": "b" * 40,
                        "ref": "refs/heads/main",
                    }
                ]
                if include_custom_props
                else []
            ),
            "head_commit": {
                "id": "7ea85c24a62e65a9384a0abe9eb541ca54064cbc",
                "committer": {"name": "dymzz", "email": "dyjane@protonmail.com"},
            },
        },
        "repository": repo,
        "sender": {"login": "dymzz"},
    }


def _normalized_span_capture(
    *,
    run_id: int,
    attempt: int,
    run_conclusion: str,
    jobs: list[dict],
    head_sha: str = "7ea85c24a62e65a9384a0abe9eb541ca54064cbc",
    repo_url: str = "https://github.com/dymzz/engineering-ci",
    include_child_spans: bool = True,
) -> dict:
    """Synthetic SemConv-normalized ``githubreceiver`` capture preserving upstream IDs."""
    tid = gr.trace_id(run_id, attempt)
    rid = gr.root_span_id(run_id, attempt)
    prev_tid = gr.previous_attempt_trace_id(run_id, attempt)
    mapped_run, run_err = gr.map_github_conclusion(run_conclusion)
    run_attrs = (
        [{"key": "cicd.pipeline.result", "value": {"stringValue": mapped_run}}]
        if mapped_run
        else []
    )
    if run_err:
        run_attrs.append({"key": "error.type", "value": {"stringValue": run_err}})

    root_span = {
        "traceId": tid,
        "spanId": rid,
        "name": "Engineering CI",
        "kind": gr.SPAN_KIND_SERVER,
        "startTimeUnixNano": "1700000000000000000",
        "endTimeUnixNano": "1700000120000000000",
        "attributes": run_attrs,
    }
    if prev_tid:
        root_span["links"] = [{"traceId": prev_tid}]

    resource_spans = [
        {
            "resource": {
                "attributes": [
                    {"key": "service.name", "value": {"stringValue": "github-actions"}},
                    {
                        "key": "cicd.pipeline.name",
                        "value": {"stringValue": "Engineering CI"},
                    },
                    {
                        "key": "cicd.pipeline.run.id",
                        "value": {"stringValue": f"{run_id}/{attempt}"},
                    },
                    {
                        "key": "vcs.repository.url.full",
                        "value": {"stringValue": repo_url},
                    },
                    {
                        "key": "vcs.ref.head.revision",
                        "value": {"stringValue": head_sha},
                    },
                ]
            },
            "scopeSpans": [{"scope": {}, "spans": [root_span]}],
        }
    ]
    for job in jobs:
        cid = int(job["id"])
        jid = gr.job_span_id(cid)
        mapped_job, job_err = gr.map_github_conclusion(job["conclusion"])
        j_attrs = [
            {"key": "cicd.pipeline.task.name", "value": {"stringValue": job["name"]}},
            {"key": "cicd.pipeline.task.run.id", "value": {"stringValue": str(cid)}},
            {
                "key": "cicd.pipeline.task.run.url.full",
                "value": {"stringValue": job["html_url"]},
            },
        ]
        if mapped_job:
            j_attrs.append(
                {
                    "key": "cicd.pipeline.task.run.result",
                    "value": {"stringValue": mapped_job},
                }
            )
        if job_err:
            j_attrs.append({"key": "error.type", "value": {"stringValue": job_err}})
        spans = [
            {
                "traceId": tid,
                "spanId": jid,
                "parentSpanId": rid,
                "name": job["name"],
                "kind": gr.SPAN_KIND_INTERNAL,
                "startTimeUnixNano": "1700000005000000000",
                "endTimeUnixNano": "1700000110000000000",
                "attributes": j_attrs,
            }
        ]
        if include_child_spans:
            spans.extend(
                [
                    {
                        "traceId": tid,
                        "spanId": gr.queue_span_id(cid),
                        "parentSpanId": jid,
                        "name": f"queue-{job['name']}",
                        "kind": gr.SPAN_KIND_INTERNAL,
                        "startTimeUnixNano": "1700000001000000000",
                        "endTimeUnixNano": "1700000005000000000",
                    },
                    {
                        "traceId": tid,
                        "spanId": gr.step_span_id(cid, "Set up job"),
                        "parentSpanId": jid,
                        "name": "Set up job",
                        "kind": gr.SPAN_KIND_INTERNAL,
                        "startTimeUnixNano": "1700000005000000000",
                        "endTimeUnixNano": "1700000007000000000",
                        "attributes": [
                            {
                                "key": "cicd.pipeline.task.name",
                                "value": {"stringValue": "Set up job"},
                            },
                            {
                                "key": "cicd.pipeline.task.run.result",
                                "value": {"stringValue": "success"},
                            },
                        ],
                    },
                ]
            )
        resource_spans.append(
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "service.name",
                            "value": {"stringValue": "github-actions"},
                        },
                        {
                            "key": "cicd.pipeline.name",
                            "value": {"stringValue": "Engineering CI"},
                        },
                        {
                            "key": "cicd.pipeline.run.id",
                            "value": {"stringValue": f"{run_id}/{attempt}"},
                        },
                        {
                            "key": "vcs.repository.url.full",
                            "value": {"stringValue": repo_url},
                        },
                        {
                            "key": "vcs.ref.head.revision",
                            "value": {"stringValue": head_sha},
                        },
                    ]
                },
                "scopeSpans": [{"scope": {}, "spans": spans}],
            }
        )
    return {"resourceSpans": resource_spans}


def test_deterministic_ids_match_upstream_githubreceiver_capture():
    # Exact values recorded from official otelcol-contrib:0.165.0 githubreceiver.
    assert gr.trace_id(42821429094, 2) == "334cd7bd6dda6b911029a4d44e6437d8"
    assert gr.root_span_id(42821429094, 2) == "b4ac9b49c02a8cff"
    assert gr.job_span_id(129847644459) == "0e295ab17224269a"
    assert gr.queue_span_id(129847644459) == "05ea474975ec2474"
    assert gr.step_span_id(129847644459, "Set up job") == "7b661fc3cce39f5c"
    assert gr.step_span_id(129847644459, "Ruff Lint") == "06f3aaeb086b1b0a"
    assert (
        gr.previous_attempt_trace_id(42821429094, 2)
        == "46cd76bda9a4a11b0b866359f039212b"
    )
    assert gr.previous_attempt_trace_id(42821429094, 1) is None


def test_invalid_id_inputs_raise_deterministically():
    for fn, args in [
        (gr.trace_id, (0, 1)),
        (gr.trace_id, ("0123", 1)),
        (gr.trace_id, (10, 0)),
        (gr.trace_id, (True, 1)),
        (gr.root_span_id, (10, "0")),
        (gr.job_span_id, (0,)),
        (gr.queue_span_id, (-1,)),
        (gr.step_span_id, (123, "")),
    ]:
        with pytest.raises(gr.GitHubReceiverError):
            fn(*args)


def test_webhook_hmac_signature_and_headers():
    body = b'{"action":"completed"}'
    hdrs = gr.webhook_headers(
        "workflow_run", body, secret=SECRET, delivery_id="deliv-1"
    )
    sig = hdrs["X-Hub-Signature-256"]
    assert gr.verify_webhook_signature(body, SECRET, sig) is True
    assert gr.verify_webhook_signature(b"{}", SECRET, sig) is False
    assert gr.verify_webhook_signature(body, "wrong", sig) is False
    assert gr.verify_webhook_signature(body, SECRET, "invalid") is False


def test_collector_and_weaver_configs_are_well_formed():
    summary = gr.collector_config_summary(COLLECTOR_YAML)
    assert all(summary.values())
    yaml_text = COLLECTOR_YAML.read_text(encoding="utf-8")
    assert SECRET not in yaml_text
    assert "finding_filters" not in WEAVER_MATCHER_TOML.read_text(encoding="utf-8")

    import tomllib

    matcher_cfg = tomllib.loads(WEAVER_MATCHER_TOML.read_text(encoding="utf-8"))
    assert set(matcher_cfg) == {"live-check"}
    ids = [m["id"] for m in matcher_cfg["live-check"]["matchers"]]
    assert ids == [gr.PIPELINE_MATCHER_ID, gr.TASK_MATCHER_ID]


@pytest.mark.parametrize(
    ("conclusion", "expected_result", "expected_error"),
    [
        ("success", "success", None),
        ("failure", "failure", "failure"),
        ("timed_out", "timeout", "timeout"),
        ("cancelled", "cancellation", None),
        ("skipped", "skip", None),
        ("startup_failure", "error", "startup_failure"),
    ],
)
def test_reconcile_positive_conclusions_and_child_spans_ignored(
    conclusion, expected_result, expected_error
):
    job = _sample_job(job_id=2001, run_id=9001, attempt=1, conclusion=conclusion)[
        "workflow_job"
    ]
    cap = _normalized_span_capture(
        run_id=9001,
        attempt=1,
        run_conclusion=conclusion,
        jobs=[job],
        include_child_spans=True,
    )
    verdict = gr.reconcile_github_receiver_trace(
        run_id=9001,
        attempt=1,
        run_status="completed",
        run_conclusion=conclusion,
        jobs=[job],
        capture=cap,
        repository_url="https://github.com/dymzz/engineering-ci",
        head_sha="7ea85c24a62e65a9384a0abe9eb541ca54064cbc",
    )
    assert verdict["ok"] is True, verdict
    assert verdict["task_coverage"] == "complete"
    assert verdict["expected_matchers"] == ["gh.pipeline", "gh.task"]
    assert verdict["trusted"] is False
    assert verdict["required_satisfied"] is False


def test_reconcile_zero_job_and_partial_rerun_scenarios():
    # Zero-job run: valid Root only
    zero_cap = _normalized_span_capture(
        run_id=9002,
        attempt=1,
        run_conclusion="cancelled",
        jobs=[],
    )
    zero_verdict = gr.reconcile_github_receiver_trace(
        run_id=9002,
        attempt=1,
        run_status="completed",
        run_conclusion="cancelled",
        jobs=[],
        capture=zero_cap,
    )
    assert zero_verdict["ok"] is True
    assert zero_verdict["task_coverage"] == "zero_task_confirmed"
    assert zero_verdict["expected_matchers"] == ["gh.pipeline"]

    # Partial rerun Attempt 2: only failed job re-ran with new check_run_id
    rerun_job = _sample_job(
        job_id=3002,
        run_id=9003,
        attempt=2,
        name="sync-tool-tests",
        conclusion="success",
    )["workflow_job"]
    rerun_cap = _normalized_span_capture(
        run_id=9003,
        attempt=2,
        run_conclusion="success",
        jobs=[rerun_job],
    )
    rerun_verdict = gr.reconcile_github_receiver_trace(
        run_id=9003,
        attempt=2,
        run_status="completed",
        run_conclusion="success",
        jobs=[rerun_job],
        capture=rerun_cap,
        run_started_at="2026-11-10T10:00:01Z",
        expect_previous_attempt_link=True,
    )
    assert rerun_verdict["ok"] is True
    assert rerun_verdict["execution_attribution_status"] == "verified"
    assert rerun_verdict["executed_jobs"] == ["3002"]
    assert rerun_verdict["result_completed_jobs"] == ["3002"]
    assert rerun_verdict["reused_jobs"] == []
    assert rerun_verdict["expected_previous_trace_id"] == gr.trace_id(9003, 1)

    # Attempt 1 job mistakenly mixed into Attempt 2 witness must fail immediately
    stale_job = _sample_job(job_id=3001, run_id=9003, attempt=1)["workflow_job"]
    with pytest.raises(gr.GitHubReceiverError, match="job_attempt_mismatch"):
        gr.reconcile_github_receiver_trace(
            run_id=9003,
            attempt=2,
            run_status="completed",
            run_conclusion="success",
            jobs=[rerun_job, stale_job],
            capture=rerun_cap,
            run_started_at="2026-11-10T10:00:01Z",
        )


def test_partial_rerun_separates_result_and_executed_inventories_44713410255_pattern():
    # Models GitHub Run 44713410255 Attempt 2:
    # - run_started_at = 2026-11-20T09:49:34Z
    # - 1 job genuinely re-ran (version-metadata, started_at 09:49:39Z >= run_started_at)
    # - 3 jobs reused from Attempt 1 carry run_attempt=2 & new check_run_id, but
    #   started_at=09:45:32Z < run_started_at.
    run_id = 44713410255
    attempt = 2
    run_started_at = "2026-11-20T09:49:34Z"

    def make_job(cid: int, name: str, started_at: str, completed_at: str) -> dict:
        job = _sample_job(
            job_id=cid,
            run_id=run_id,
            attempt=attempt,
            name=name,
            conclusion="success",
        )["workflow_job"]
        job["created_at"] = "2026-11-20T09:49:36Z"
        job["started_at"] = started_at
        job["completed_at"] = completed_at
        return job

    executed_job = make_job(
        136212522105,
        "version-metadata",
        "2026-11-20T09:49:39Z",
        "2026-11-20T09:50:40Z",
    )
    reused_sync = make_job(
        136212523496,
        "sync-tool-tests",
        "2026-11-20T09:45:32Z",
        "2026-11-20T09:47:40Z",
    )
    reused_checks = make_job(
        136212524904,
        "python-ci / checks",
        "2026-11-20T09:45:32Z",
        "2026-11-20T09:46:47Z",
    )
    reused_a1 = make_job(
        136212602878,
        "dagger-native-telemetry / a1-gate",
        "2026-11-20T09:45:32Z",
        "2026-11-20T09:45:48Z",
    )
    all_jobs = [executed_job, reused_sync, reused_checks, reused_a1]

    # Upstream githubreceiver only sees workflow_job webhook for version-metadata.
    partial_cap = _normalized_span_capture(
        run_id=run_id,
        attempt=attempt,
        run_conclusion="success",
        jobs=[executed_job],
    )

    exp = gr.build_weaver_expectations(
        run_id=run_id,
        attempt=attempt,
        run_status="completed",
        run_conclusion="success",
        jobs=all_jobs,
        run_started_at=run_started_at,
    )
    assert exp["execution_attribution_status"] == "verified"
    assert exp["result_completed_jobs"] == [
        "136212522105",
        "136212523496",
        "136212524904",
        "136212602878",
    ]
    assert len(exp["result_inventory"]) == 4
    assert exp["executed_jobs"] == ["136212522105"]
    assert exp["reused_jobs"] == ["136212523496", "136212524904", "136212602878"]
    assert exp["unknown_execution_jobs"] == []
    assert exp["expected_matchers"] == [gr.PIPELINE_MATCHER_ID, gr.TASK_MATCHER_ID]
    assert [s["span_id"] for s in exp["spans"]] == [
        gr.root_span_id(run_id, attempt),
        gr.job_span_id(136212522105),
    ]

    reused_only_exp = gr.build_weaver_expectations(
        run_id=run_id,
        attempt=attempt,
        run_status="completed",
        run_conclusion="success",
        jobs=[reused_sync, reused_checks],
        run_started_at=run_started_at,
    )
    assert reused_only_exp["executed_jobs"] == []
    assert reused_only_exp["result_completed_jobs"] == ["136212523496", "136212524904"]
    assert reused_only_exp["expected_matchers"] == [gr.PIPELINE_MATCHER_ID]
    assert [s["span_id"] for s in reused_only_exp["spans"]] == [
        gr.root_span_id(run_id, attempt)
    ]

    verdict = gr.reconcile_github_receiver_trace(
        run_id=run_id,
        attempt=attempt,
        run_status="completed",
        run_conclusion="success",
        jobs=all_jobs,
        capture=partial_cap,
        run_started_at=run_started_at,
        expect_previous_attempt_link=True,
    )
    assert verdict["ok"] is True, verdict
    assert verdict["errors"] == []
    assert verdict["task_coverage"] == "complete"
    assert verdict["execution_attribution_status"] == "verified"
    assert verdict["result_completed_jobs"] == exp["result_completed_jobs"]
    assert verdict["executed_jobs"] == ["136212522105"]
    assert verdict["reused_jobs"] == exp["reused_jobs"]
    assert verdict["missing_jobs"] == []

    # Reused-only Attempt 2 (0 executed jobs): Root-only trace passes while preserving Result Inventory
    root_only_cap = _normalized_span_capture(
        run_id=run_id, attempt=attempt, run_conclusion="success", jobs=[]
    )
    reused_only = gr.reconcile_github_receiver_trace(
        run_id=run_id,
        attempt=attempt,
        run_status="completed",
        run_conclusion="success",
        jobs=[reused_sync, reused_checks],
        capture=root_only_cap,
        run_started_at=run_started_at,
    )
    assert reused_only["ok"] is True, reused_only
    assert reused_only["task_coverage"] == "zero_task_confirmed"
    assert reused_only["executed_jobs"] == []
    assert reused_only["reused_jobs"] == ["136212523496", "136212524904"]
    assert reused_only["expected_matchers"] == ["gh.pipeline"]
    assert reused_only["result_completed_jobs"] == ["136212523496", "136212524904"]

    # Negative 1: Missing Task span for genuinely executed Attempt 2 job fails ONLY on executed job
    missing_exec = gr.reconcile_github_receiver_trace(
        run_id=run_id,
        attempt=attempt,
        run_status="completed",
        run_conclusion="success",
        jobs=all_jobs,
        capture=root_only_cap,
        run_started_at=run_started_at,
    )
    assert missing_exec["ok"] is False
    assert "missing_expected_task_spans" in missing_exec["errors"]
    assert missing_exec["missing_jobs"] == ["136212522105"]

    # Negative 2a: Wrong reuse attribution — claiming historical pre-run job ran in Attempt 2
    with pytest.raises(gr.GitHubReceiverError, match="wrong_reuse_attribution"):
        gr.build_weaver_expectations(
            run_id=run_id,
            attempt=attempt,
            run_status="completed",
            run_conclusion="success",
            jobs=all_jobs,
            run_started_at=run_started_at,
            executed_job_ids=[136212522105, 136212523496],
        )

    # Negative 2b: Wrong reuse attribution — complete witness dismissing executed job as reused
    with pytest.raises(gr.GitHubReceiverError, match="wrong_reuse_attribution"):
        gr.reconcile_github_receiver_trace(
            run_id=run_id,
            attempt=attempt,
            run_status="completed",
            run_conclusion="success",
            jobs=all_jobs,
            capture=partial_cap,
            run_started_at=run_started_at,
            executed_job_witness={
                "source": "github_webhook_events",
                "run_id": run_id,
                "attempt": attempt,
                "run_status": "completed",
                "complete": True,
                "executed_job_ids": [],
            },
        )

    # Negative 2c: Wrong reuse attribution — Capture emits a Task span for a reused job
    cap_with_reused = _normalized_span_capture(
        run_id=run_id,
        attempt=attempt,
        run_conclusion="success",
        jobs=[executed_job, reused_sync],
    )
    reused_span_verdict = gr.reconcile_github_receiver_trace(
        run_id=run_id,
        attempt=attempt,
        run_status="completed",
        run_conclusion="success",
        jobs=all_jobs,
        capture=cap_with_reused,
        run_started_at=run_started_at,
    )
    assert reused_span_verdict["ok"] is False
    assert "wrong_reuse_attribution" in reused_span_verdict["errors"]
    assert "unexpected_task_spans" in reused_span_verdict["errors"]
    assert reused_span_verdict["reused_job_spans_in_capture"] == ["136212523496"]

    # Negative 2d: Attempt 1 cannot have prior-Attempt reused jobs
    att1_job = _sample_job(job_id=7001, run_id=9100, attempt=1)["workflow_job"]
    att1_job["started_at"] = "2026-11-10T09:55:00Z"
    with pytest.raises(gr.GitHubReceiverError, match="wrong_reuse_attribution"):
        gr.reconcile_github_receiver_trace(
            run_id=9100,
            attempt=1,
            run_status="completed",
            run_conclusion="success",
            jobs=[att1_job],
            capture=_normalized_span_capture(
                run_id=9100, attempt=1, run_conclusion="success", jobs=[att1_job]
            ),
            run_started_at="2026-11-10T10:00:00Z",
        )

    # Negative 3: Unproven Attempt 2 jobs (no run_started_at and no executed_job_ids)
    # stay explicitly unknown and cannot self-certify Expected from Capture spans
    unknown_verdict = gr.reconcile_github_receiver_trace(
        run_id=run_id,
        attempt=attempt,
        run_status="completed",
        run_conclusion="success",
        jobs=[executed_job, reused_sync],
        capture=partial_cap,
    )
    assert unknown_verdict["ok"] is False
    assert unknown_verdict["execution_attribution_status"] == "unknown"
    assert unknown_verdict["task_coverage"] == "execution_attribution_unknown"
    assert "execution_attribution_unknown" in unknown_verdict["errors"]
    assert unknown_verdict["result_completed_jobs"] == ["136212522105", "136212523496"]
    assert unknown_verdict["unknown_execution_jobs"] == [
        "136212522105",
        "136212523496",
    ]
    assert unknown_verdict["executed_jobs"] == []
    assert unknown_verdict["unattributed_capture_spans"] == ["136212522105"]

    with pytest.raises(gr.GitHubReceiverError, match="execution_attribution_unknown"):
        gr.build_weaver_expectations(
            run_id=run_id,
            attempt=attempt,
            run_status="completed",
            run_conclusion="success",
            jobs=[executed_job, reused_sync],
        )

    diag_exp = gr.build_weaver_expectations(
        run_id=run_id,
        attempt=attempt,
        run_status="completed",
        run_conclusion="success",
        jobs=[executed_job, reused_sync],
        allow_unproven_execution_attribution=True,
    )
    assert diag_exp["execution_attribution_status"] == "unknown"
    assert diag_exp["provenance_gaps"] == ["execution_attribution_unknown"]
    assert diag_exp["result_completed_jobs"] == ["136212522105", "136212523496"]
    assert [s["span_id"] for s in diag_exp["spans"]] == [
        gr.root_span_id(run_id, attempt)
    ]


def test_partial_executed_job_ids_never_infers_reuse_without_complete_witness():
    """P1 + P2-A: bare executed_job_ids only proves listed IDs, not completeness."""
    run_id = 44713410255
    attempt = 2
    job_a = _sample_job(
        job_id=136212522105,
        run_id=run_id,
        attempt=attempt,
        name="version-metadata",
        conclusion="success",
    )["workflow_job"]
    job_b = _sample_job(
        job_id=136212523496,
        run_id=run_id,
        attempt=attempt,
        name="sync-tool-tests",
        conclusion="success",
    )["workflow_job"]
    cap_a_only = _normalized_span_capture(
        run_id=run_id,
        attempt=attempt,
        run_conclusion="success",
        jobs=[job_a],
    )

    # Required negative: missing start times + partial executed_job_ids=[A] + capture Root+A.
    # Unlisted job B MUST remain unknown (never inferred reused or complete).
    partial_verdict = gr.reconcile_github_receiver_trace(
        run_id=run_id,
        attempt=attempt,
        run_status="completed",
        run_conclusion="success",
        jobs=[job_a, job_b],
        capture=cap_a_only,
        executed_job_ids=[136212522105],
    )
    assert partial_verdict["ok"] is False
    assert partial_verdict["execution_attribution_status"] == "unknown"
    assert partial_verdict["execution_witness_complete"] is False
    assert partial_verdict["task_coverage"] == "execution_attribution_unknown"
    assert partial_verdict["errors"] == ["execution_attribution_unknown"]
    assert partial_verdict["executed_jobs"] == ["136212522105"]
    assert partial_verdict["reused_jobs"] == []
    assert partial_verdict["unknown_execution_jobs"] == ["136212523496"]
    assert partial_verdict["result_completed_jobs"] == ["136212522105", "136212523496"]

    with pytest.raises(gr.GitHubReceiverError, match="execution_attribution_unknown"):
        gr.build_weaver_expectations(
            run_id=run_id,
            attempt=attempt,
            run_status="completed",
            run_conclusion="success",
            jobs=[job_a, job_b],
            executed_job_ids=[136212522105],
        )

    # Incomplete structured witness also keeps unlisted B unknown.
    incomplete_witness_verdict = gr.reconcile_github_receiver_trace(
        run_id=run_id,
        attempt=attempt,
        run_status="completed",
        run_conclusion="success",
        jobs=[job_a, job_b],
        capture=cap_a_only,
        executed_job_witness={
            "source": "github_webhook_events",
            "run_id": run_id,
            "attempt": attempt,
            "run_status": "completed",
            "complete": False,
            "collection_status": "partial",
            "executed_job_ids": [136212522105],
        },
    )
    assert incomplete_witness_verdict["ok"] is False
    assert incomplete_witness_verdict["unknown_execution_jobs"] == ["136212523496"]
    assert incomplete_witness_verdict["reused_jobs"] == []

    # Caller self-reported complete=True without independent completeness proof MUST NOT
    # infer unlisted B is reused or turn missing B spans into ok=True / complete PASS.
    for witness_source in ("github_webhook_events", "github_api"):
        claimed_complete_witness = {
            "source": witness_source,
            "run_id": run_id,
            "attempt": attempt,
            "run_status": "completed",
            "complete": True,
            "collection_status": "complete",
            "executed_job_ids": [136212522105],
        }
        with pytest.raises(
            gr.GitHubReceiverError, match="execution_attribution_unknown"
        ):
            gr.build_weaver_expectations(
                run_id=run_id,
                attempt=attempt,
                run_status="completed",
                run_conclusion="success",
                jobs=[job_a, job_b],
                executed_job_witness=claimed_complete_witness,
            )

        diag_claimed_exp = gr.build_weaver_expectations(
            run_id=run_id,
            attempt=attempt,
            run_status="completed",
            run_conclusion="success",
            jobs=[job_a, job_b],
            executed_job_witness=claimed_complete_witness,
            allow_unproven_execution_attribution=True,
        )
        assert diag_claimed_exp["execution_attribution_status"] == "unknown"
        assert diag_claimed_exp["execution_witness_complete"] is False
        assert diag_claimed_exp["executed_jobs"] == ["136212522105"]
        assert diag_claimed_exp["reused_jobs"] == []
        assert diag_claimed_exp["unknown_execution_jobs"] == ["136212523496"]
        assert "execution_attribution_unknown" in diag_claimed_exp["provenance_gaps"]
        assert (
            "execution_witness_completeness_unverified"
            in diag_claimed_exp["provenance_gaps"]
        )

        claimed_complete_verdict = gr.reconcile_github_receiver_trace(
            run_id=run_id,
            attempt=attempt,
            run_status="completed",
            run_conclusion="success",
            jobs=[job_a, job_b],
            capture=cap_a_only,
            executed_job_witness=claimed_complete_witness,
        )
        assert claimed_complete_verdict["ok"] is False
        assert claimed_complete_verdict["task_coverage"] == (
            "execution_attribution_unknown"
        )
        assert claimed_complete_verdict["errors"] == ["execution_attribution_unknown"]
        assert claimed_complete_verdict["execution_witness_complete"] is False
        assert claimed_complete_verdict["executed_jobs"] == ["136212522105"]
        assert claimed_complete_verdict["reused_jobs"] == []
        assert claimed_complete_verdict["unknown_execution_jobs"] == ["136212523496"]
        assert claimed_complete_verdict["result_inventory"][0][
            "execution_attribution_source"
        ] == ("executed_job_witness")

    # Claimed complete=True with empty executed_job_ids also stays unproven without timestamps
    root_only_cap = _normalized_span_capture(
        run_id=run_id, attempt=attempt, run_conclusion="success", jobs=[]
    )
    unproven_zero_verdict = gr.reconcile_github_receiver_trace(
        run_id=run_id,
        attempt=attempt,
        run_status="completed",
        run_conclusion="success",
        jobs=[job_a, job_b],
        capture=root_only_cap,
        executed_job_witness={
            "source": "github_webhook_events",
            "run_id": run_id,
            "attempt": attempt,
            "run_status": "completed",
            "complete": True,
            "collection_status": "complete",
            "executed_job_ids": [],
        },
    )
    assert unproven_zero_verdict["ok"] is False
    assert unproven_zero_verdict["task_coverage"] == "execution_attribution_unknown"
    assert unproven_zero_verdict["execution_witness_complete"] is False
    assert unproven_zero_verdict["executed_jobs"] == []
    assert unproven_zero_verdict["reused_jobs"] == []
    assert unproven_zero_verdict["unknown_execution_jobs"] == [
        "136212522105",
        "136212523496",
    ]
    assert unproven_zero_verdict["result_completed_jobs"] == [
        "136212522105",
        "136212523496",
    ]

    # Partial executed_job_ids with valid time bounds still lets time attribution verify B.
    job_a_timed = {
        **job_a,
        "created_at": "2026-11-20T09:49:34Z",
        "started_at": "2026-11-20T09:49:39Z",
        "completed_at": "2026-11-20T09:50:10Z",
    }
    job_b_timed = {
        **job_b,
        "created_at": "2026-11-20T09:49:34Z",
        "started_at": "2026-11-20T09:49:40Z",
        "completed_at": "2026-11-20T09:50:20Z",
    }
    cap_ab = _normalized_span_capture(
        run_id=run_id,
        attempt=attempt,
        run_conclusion="success",
        jobs=[job_a_timed, job_b_timed],
    )
    partial_with_times = gr.reconcile_github_receiver_trace(
        run_id=run_id,
        attempt=attempt,
        run_status="completed",
        run_conclusion="success",
        jobs=[job_a_timed, job_b_timed],
        capture=cap_ab,
        run_started_at="2026-11-20T09:49:34Z",
        executed_job_ids=[136212522105],
    )
    assert partial_with_times["ok"] is True, partial_with_times
    assert partial_with_times["task_coverage"] == "complete"
    assert partial_with_times["executed_jobs"] == ["136212522105", "136212523496"]

    # Independent time bounds classify reused B via run_started_at even when complete=True
    # is supplied, while keeping execution_witness_complete=False.
    reused_b_timed = {
        **job_b,
        "created_at": "2026-11-20T09:40:00Z",
        "started_at": "2026-11-20T09:41:00Z",
        "completed_at": "2026-11-20T09:45:00Z",
    }
    witness_plus_times = gr.reconcile_github_receiver_trace(
        run_id=run_id,
        attempt=attempt,
        run_status="completed",
        run_conclusion="success",
        jobs=[job_a_timed, reused_b_timed],
        capture=cap_a_only,
        run_started_at="2026-11-20T09:49:34Z",
        executed_job_witness={
            "source": "github_webhook_events",
            "run_id": run_id,
            "attempt": attempt,
            "run_status": "completed",
            "complete": True,
            "collection_status": "complete",
            "executed_job_ids": [136212522105],
        },
    )
    assert witness_plus_times["ok"] is True, witness_plus_times
    assert witness_plus_times["execution_witness_complete"] is False
    assert witness_plus_times["reused_jobs"] == ["136212523496"]
    assert witness_plus_times["result_inventory"][1][
        "execution_attribution_source"
    ] == ("run_started_at")

    # Reject untrusted source, mismatched identity, missing/incomplete run_status when complete=True.
    complete_witness = {
        "source": "github_webhook_events",
        "run_id": run_id,
        "attempt": attempt,
        "run_status": "completed",
        "complete": True,
        "collection_status": "complete",
        "executed_job_ids": [136212522105],
    }
    witness_without_status = {
        k: v for k, v in complete_witness.items() if k != "run_status"
    }
    for bad_witness, err in (
        (
            {**complete_witness, "source": "otlp_capture"},
            "executed_job_witness_source_invalid",
        ),
        (
            {**complete_witness, "run_id": 999},
            "executed_job_witness_run_mismatch",
        ),
        (
            {**complete_witness, "attempt": 1},
            "executed_job_witness_attempt_mismatch",
        ),
        (
            {**complete_witness, "run_status": "in_progress"},
            "executed_job_witness_status_invalid",
        ),
        (
            witness_without_status,
            "executed_job_witness_status_invalid",
        ),
    ):
        with pytest.raises(gr.GitHubReceiverError, match=err):
            gr.partition_witness_jobs(
                [job_a, job_b],
                rid=run_id,
                att=attempt,
                executed_job_witness=bad_witness,
            )

    # Attempt 1 completed jobs omitting an entry under claimed complete=True contradicts Attempt 1.
    with pytest.raises(gr.GitHubReceiverError, match="wrong_reuse_attribution"):
        gr.partition_witness_jobs(
            [
                {**job_a, "run_attempt": 1},
                {**job_b, "run_attempt": 1},
            ],
            rid=run_id,
            att=1,
            executed_job_witness={**complete_witness, "attempt": 1},
        )


def test_reconcile_rejects_negative_tampered_captures():
    job = _sample_job(job_id=4001, run_id=9004, attempt=2, conclusion="failure")[
        "workflow_job"
    ]
    cap = _normalized_span_capture(
        run_id=9004, attempt=2, run_conclusion="failure", jobs=[job]
    )

    # 1. Unexpected extra direct Task span (e.g. rogue job)
    rogue = copy.deepcopy(cap)
    rogue_span = copy.deepcopy(rogue["resourceSpans"][1]["scopeSpans"][0]["spans"][0])
    rogue_span["spanId"] = gr.job_span_id(4999)
    rogue_span["attributes"][1]["value"]["stringValue"] = "4999"
    rogue["resourceSpans"][1]["scopeSpans"][0]["spans"].append(rogue_span)
    res_rogue = gr.reconcile_github_receiver_trace(
        run_id=9004,
        attempt=2,
        run_status="completed",
        run_conclusion="failure",
        jobs=[job],
        capture=rogue,
        run_started_at="2026-11-10T10:00:01Z",
    )
    assert res_rogue["ok"] is False
    assert "unexpected_task_spans" in res_rogue["errors"]

    # 2. Missing error.type on failure
    no_err = copy.deepcopy(cap)
    root_attrs = no_err["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"]
    no_err["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"] = [
        a for a in root_attrs if a["key"] != "error.type"
    ]
    res_no_err = gr.reconcile_github_receiver_trace(
        run_id=9004,
        attempt=2,
        run_status="completed",
        run_conclusion="failure",
        jobs=[job],
        capture=no_err,
        run_started_at="2026-11-10T10:00:01Z",
    )
    assert res_no_err["ok"] is False
    assert "root_error_type_mismatch" in res_no_err["errors"]

    # 3. Missing previous attempt link on rerun
    no_link = copy.deepcopy(cap)
    no_link["resourceSpans"][0]["scopeSpans"][0]["spans"][0].pop("links", None)
    res_no_link = gr.reconcile_github_receiver_trace(
        run_id=9004,
        attempt=2,
        run_status="completed",
        run_conclusion="failure",
        jobs=[job],
        capture=no_link,
        run_started_at="2026-11-10T10:00:01Z",
        expect_previous_attempt_link=True,
    )
    assert res_no_link["ok"] is False
    assert "previous_attempt_trace_link_missing" in res_no_link["errors"]

    # 4. Unmapped conclusion refused in expectations builder
    with pytest.raises(gr.GitHubReceiverError, match="unsupported_run_conclusion"):
        gr.build_weaver_expectations(
            run_id=9004,
            attempt=2,
            run_status="completed",
            run_conclusion="neutral",
            jobs=[job],
            run_started_at="2026-11-10T10:00:01Z",
        )


def _docker_and_dagger_available() -> bool:
    if (
        os.environ.get("ECI_RUN_LIVE_COLLECTOR_TESTS") != "1"
        or os.environ.get("ECI_SKIP_LIVE_COLLECTOR_TESTS") == "1"
    ):
        return False
    if not shutil.which("docker") or not shutil.which("dagger"):
        return False
    probe = subprocess.run(
        ["docker", "image", "inspect", gr.COLLECTOR_CONTRIB_REF],
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


class _RecordingOtlpTraceServer(trace_service_pb2_grpc.TraceServiceServicer):
    def __init__(self) -> None:
        self.batches: list[dict] = []
        self.lock = threading.Lock()

    def Export(self, request, context):
        payload = json_format.MessageToDict(request, preserving_proto_field_name=False)
        with self.lock:
            self.batches.append(payload)
        return trace_service_pb2.ExportTraceServiceResponse()


def _post_webhook(
    port: int, event: str, payload: dict, *, secret: str, delivery: str
) -> str:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/events",
        data=body,
        method="POST",
        headers=gr.webhook_headers(event, body, secret=secret, delivery_id=delivery),
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return str(resp.status)
    except urllib.error.HTTPError as err:
        return str(err.code)
    except (
        http.client.HTTPException,
        urllib.error.URLError,
        TimeoutError,
        OSError,
    ) as err:
        return type(err).__name__


def _run_dagger_weaver(
    stage_dir: Path,
    *,
    kind: str,
    capture: dict,
    expected: dict,
    expected_matchers: list[str],
) -> dict:
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)
    out_dir = stage_dir.with_name(stage_dir.name + "-out")
    if out_dir.exists():
        shutil.rmtree(out_dir)

    shutil.copytree(WEAVER_FIXTURE_DIR / "registry", stage_dir / "registry")
    shutil.copytree(
        WEAVER_FIXTURE_DIR / "advice-policies", stage_dir / "advice-policies"
    )
    shutil.copy2(
        WEAVER_FIXTURE_DIR / "registry-sha256.json", stage_dir / "registry-sha256.json"
    )
    shutil.copy2(WEAVER_MATCHER_TOML, stage_dir / ".weaver.toml")
    (stage_dir / "capture.json").write_text(
        json.dumps(capture, indent=2), encoding="utf-8"
    )
    (stage_dir / "expected.json").write_text(
        json.dumps(expected, indent=2), encoding="utf-8"
    )
    (stage_dir / "ci-weaver.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                'registry = "registry"',
                'registry_manifest = "registry-sha256.json"',
                'matcher_config = ".weaver.toml"',
                f"expected_matchers = {json.dumps(expected_matchers)}",
                'otlp = "capture.json"',
                'expectations = "expected.json"',
                'advice_policies = "advice-policies"',
                "",
                "[origin]",
                'repository = "https://github.com/open-telemetry/semantic-conventions"',
                'commit = "fb30ca17193b551473b1f43c315482918ab6987c"',
                'version = "1.45.0"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    rel_src = stage_dir.relative_to(ROOT).as_posix()
    rel_out = out_dir.relative_to(ROOT).as_posix()
    env = dict(os.environ, FORCE_DARK_MODE="1")
    subprocess.run(
        [
            "dagger",
            "call",
            "weaver-artifacts",
            f"--source={rel_src}",
            f"--kind={kind}",
            "--config-path=ci-weaver.toml",
            "export",
            f"--path={rel_out}",
        ],
        cwd=ROOT,
        env=env,
        check=True,
    )
    return json.loads((out_dir / "check-result.json").read_text(encoding="utf-8"))


@pytest.mark.skipif(
    not _docker_and_dagger_available(),
    reason="Set ECI_RUN_LIVE_COLLECTOR_TESTS=1 to run Docker + Dagger live collector E2E",
)
def test_live_collector_to_otlp_to_weaver_v1_45_0_e2e():
    """Live E2E: official otelcol-contrib:0.165.0 + pinned SemConv v1.45.0 Weaver."""
    work_root = ROOT / ".artifacts" / "pytest-githubreceiver-e2e"
    if work_root.exists():
        shutil.rmtree(work_root)
    work_root.mkdir(parents=True)

    try:
        # 1. Official config validation via otelcol-contrib validate
        cfg_dir = work_root / "cfg"
        cfg_dir.mkdir()
        val_yaml = COLLECTOR_YAML.read_text(encoding="utf-8")
        # For host loopback OTLP gRPC inside Docker test only, verify base config first:
        (cfg_dir / "prod.yaml").write_text(val_yaml, encoding="utf-8")
        val_proc = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-e",
                f"GITHUB_WEBHOOK_SECRET={SECRET}",
                "-e",
                "GITHUB_SCRAPER_ORG=dymzz",
                "-e",
                "ECI_OTLP_GRPC_ENDPOINT=127.0.0.1:4317",
                "-v",
                f"{cfg_dir}:/cfg:ro",
                gr.COLLECTOR_CONTRIB_REF,
                "validate",
                "--config=/cfg/prod.yaml",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert val_proc.returncode == 0, val_proc.stderr

        # Loopback test config appends tls.insecure=true on exporter only (identical receiver/processors)
        live_yaml = val_yaml.replace(
            "exporters:\n  otlp_grpc:\n    endpoint: ${env:ECI_OTLP_GRPC_ENDPOINT}",
            "exporters:\n  otlp_grpc:\n    endpoint: ${env:ECI_OTLP_GRPC_ENDPOINT}\n    tls:\n      insecure: true",
        )
        (cfg_dir / "live.yaml").write_text(live_yaml, encoding="utf-8")

        servicer = _RecordingOtlpTraceServer()
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
        trace_service_pb2_grpc.add_TraceServiceServicer_to_server(servicer, server)
        grpc_port = server.add_insecure_port("0.0.0.0:0")
        server.start()

        container_name = f"eci-test-ghrecv-{os.getpid()}"
        subprocess.run(
            ["docker", "rm", "-f", container_name], capture_output=True, check=False
        )
        proc = subprocess.Popen(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                container_name,
                "-p",
                "19428:19418",
                "-e",
                f"GITHUB_WEBHOOK_SECRET={SECRET}",
                "-e",
                "GITHUB_SCRAPER_ORG=dymzz",
                "-e",
                f"ECI_OTLP_GRPC_ENDPOINT=host.docker.internal:{grpc_port}",
                "-v",
                f"{cfg_dir}:/cfg:ro",
                gr.COLLECTOR_CONTRIB_REF,
                "--config=/cfg/live.yaml",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 30
            healthy = False
            while time.monotonic() < deadline:
                try:
                    with urllib.request.urlopen(
                        "http://127.0.0.1:19428/health", timeout=2
                    ) as r:
                        if r.status == 200:
                            healthy = True
                            break
                except (
                    http.client.HTTPException,
                    urllib.error.URLError,
                    TimeoutError,
                    OSError,
                ):
                    time.sleep(0.25)
            assert healthy, "Collector health endpoint timed out"

            # Rejected / ignored webhooks
            bad_req = urllib.request.Request(
                "http://127.0.0.1:19428/events",
                data=json.dumps(_sample_run(run_id=501, attempt=1)).encode("utf-8"),
                method="POST",
                headers=gr.webhook_headers(
                    "workflow_run", b"{}", secret="wrong", delivery_id="bad"
                ),
            )
            with pytest.raises(urllib.error.HTTPError) as bad_err:
                urllib.request.urlopen(bad_req, timeout=10)
            assert bad_err.value.code == 400

            assert (
                _post_webhook(
                    19428,
                    "workflow_run",
                    _sample_run(
                        run_id=501, attempt=1, status="in_progress", conclusion=None
                    ),
                    secret=SECRET,
                    delivery="ip-1",
                )
                == "204"
            )
            assert (
                _post_webhook(
                    19428,
                    "workflow_job",
                    _sample_job(job_id=0, run_id=501, attempt=1),
                    secret=SECRET,
                    delivery="zero-id-1",
                )
                == "RemoteDisconnected"
            )

            # Positive 1: Attempt 2 multi-job partial rerun (failure -> timeout -> cancelled + Run failure)
            job_fail = _sample_job(
                job_id=5101, run_id=501, attempt=2, name="lint", conclusion="failure"
            )
            job_to = _sample_job(
                job_id=5102, run_id=501, attempt=2, name="tests", conclusion="timed_out"
            )
            job_cancel = _sample_job(
                job_id=5103,
                run_id=501,
                attempt=2,
                name="deploy",
                conclusion="cancelled",
                steps=[],
            )
            run_fail = _sample_run(
                run_id=501, attempt=2, conclusion="failure", include_custom_props=True
            )
            for ev, body, did in [
                ("workflow_job", job_fail, "j-fail"),
                ("workflow_job", job_to, "j-to"),
                ("workflow_job", job_cancel, "j-cancel"),
                ("workflow_run", run_fail, "r-fail"),
            ]:
                assert (
                    _post_webhook(19428, ev, body, secret=SECRET, delivery=did) == "200"
                )

            # Positive 2: Zero-job Attempt 1 cancelled
            run_zero = _sample_run(run_id=502, attempt=1, conclusion="cancelled")
            assert (
                _post_webhook(
                    19428, "workflow_run", run_zero, secret=SECRET, delivery="r-zero"
                )
                == "200"
            )

            # Negative SemConv: unmapped conclusion ("neutral")
            run_neutral = _sample_run(run_id=503, attempt=1, conclusion="neutral")
            assert (
                _post_webhook(
                    19428,
                    "workflow_run",
                    run_neutral,
                    secret=SECRET,
                    delivery="r-neutral",
                )
                == "200"
            )

            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and len(servicer.batches) < 6:
                time.sleep(0.2)
            assert len(servicer.batches) == 6
        finally:
            subprocess.run(
                ["docker", "stop", "-t", "5", container_name],
                capture_output=True,
                check=False,
            )
            proc.communicate(timeout=15)
            server.stop(0)

        merged = gr.merge_otlp_json_batches(servicer.batches)

        def filter_trace(tid: str) -> dict:
            return {
                "resourceSpans": [
                    rs
                    for rs in merged["resourceSpans"]
                    if any(
                        sp.get("traceId") == tid
                        for sc in rs.get("scopeSpans", [])
                        for sp in sc.get("spans", [])
                    )
                ]
            }

        # Verify Case 1 through real Dagger Weaver v1.45.0 terminal + reconciliation
        jobs_501 = [
            job_fail["workflow_job"],
            job_to["workflow_job"],
            job_cancel["workflow_job"],
        ]
        cap_501 = filter_trace(gr.trace_id(501, 2))
        exp_501 = gr.build_weaver_expectations(
            run_id=501,
            attempt=2,
            run_status="completed",
            run_conclusion="failure",
            jobs=jobs_501,
            run_started_at=run_fail["workflow_run"]["run_started_at"],
        )
        weaver_501 = _run_dagger_weaver(
            work_root / "weaver-501",
            kind="terminal",
            capture=cap_501,
            expected=exp_501,
            expected_matchers=exp_501["expected_matchers"],
        )
        assert weaver_501["status"] == "passed", weaver_501
        assert weaver_501["official_registry_verified"] is True
        assert weaver_501["terminal_correlation_status"] == "passed"
        assert (
            weaver_501["native_report"]["statistics"]["advice_level_counts"].get(
                "violation", 0
            )
            == 0
        )

        rec_501 = gr.reconcile_github_receiver_trace(
            run_id=501,
            attempt=2,
            run_status="completed",
            run_conclusion="failure",
            jobs=jobs_501,
            capture=cap_501,
            run_started_at=run_fail["workflow_run"]["run_started_at"],
            repository_url="https://github.com/dymzz/engineering-ci",
            head_sha="7ea85c24a62e65a9384a0abe9eb541ca54064cbc",
            expect_previous_attempt_link=True,
        )
        assert rec_501["ok"] is True, rec_501

        # Verify Case 2 Zero-job cancelled
        cap_502 = filter_trace(gr.trace_id(502, 1))
        exp_502 = gr.build_weaver_expectations(
            run_id=502,
            attempt=1,
            run_status="completed",
            run_conclusion="cancelled",
            jobs=[],
        )
        weaver_502 = _run_dagger_weaver(
            work_root / "weaver-502",
            kind="terminal",
            capture=cap_502,
            expected=exp_502,
            expected_matchers=exp_502["expected_matchers"],
        )
        assert weaver_502["status"] == "passed", weaver_502
        assert weaver_502["terminal_correlation_status"] == "passed"
        assert (
            weaver_502["native_report"]["statistics"]["advice_level_counts"].get(
                "violation", 0
            )
            == 0
        )

        # Verify Case 3 Unmapped "neutral" fails pinned SemConv v1.45.0 Weaver natively
        cap_503 = filter_trace(gr.trace_id(503, 1))
        exp_503 = {
            "schema_version": 1,
            "source": {"run_id": "503/1", "attempt": 1},
            "spans": [
                {
                    "trace_id": gr.trace_id(503, 1),
                    "span_id": gr.root_span_id(503, 1),
                    "matcher": gr.PIPELINE_MATCHER_ID,
                    "signal": gr.PIPELINE_SIGNAL,
                }
            ],
            "terminal_evidence": {
                "run_id": "503/1",
                "attempt": 1,
                "status": "completed",
                "result": "neutral",
            },
        }
        weaver_503 = _run_dagger_weaver(
            work_root / "weaver-503",
            kind="live",
            capture=cap_503,
            expected=exp_503,
            expected_matchers=[gr.PIPELINE_MATCHER_ID],
        )
        assert weaver_503["status"] == "failed"
        assert weaver_503["reason"] == "findings"
    finally:
        if work_root.exists():
            shutil.rmtree(work_root, ignore_errors=True)
