"""GitHub Run/Job facts → CI/CD candidate telemetry records (ECI #75)."""

import json
from pathlib import Path

from eci_core import cicd_telemetry

RUN_START = 1_700_000_000_000_000_000
RUN_END = 1_700_000_120_000_000_000
JOB_START = 1_700_000_010_000_000_000
JOB_END = 1_700_000_090_000_000_000


def complete_facts(**overrides):
    value = {
        "workflow_name": "Engineering CI",
        "run_id": "42627302972",
        "run_attempt": 1,
        "job_id": "129194268575",
        "job_name": "python-ci / checks",
        "job_url": (
            "https://github.com/dymzz/engineering-ci/actions/"
            "runs/42627302972/job/129194268575"
        ),
        "run_conclusion": "success",
        "job_conclusion": "success",
        "head_sha": "a" * 40,
        "repository": "dymzz/engineering-ci",
        "run_started_at_ns": RUN_START,
        "run_completed_at_ns": RUN_END,
        "job_started_at_ns": JOB_START,
        "job_completed_at_ns": JOB_END,
    }
    value.update(overrides)
    return cicd_telemetry.facts_from_mapping(value)


def test_produce_emits_pipeline_and_task_spans():
    report = cicd_telemetry.produce(complete_facts())
    assert report["status"] == "produced"
    assert report["evidence_class"] == "candidate_record"
    assert report["claims"]["candidate_record_only"] is True
    assert report["claims"]["otlp_delivered"] is False
    assert report["reason"] is None
    assert len(report["spans"]) == 2
    pipeline, task = report["spans"]
    assert pipeline["kind"] == cicd_telemetry.SPAN_KIND_SERVER
    assert task["kind"] == cicd_telemetry.SPAN_KIND_INTERNAL
    assert pipeline["start_time_unix_nano"] == RUN_START
    assert pipeline["end_time_unix_nano"] == RUN_END
    assert task["start_time_unix_nano"] == JOB_START
    assert task["end_time_unix_nano"] == JOB_END
    assert pipeline["attributes"]["cicd.pipeline.run.id"] == "42627302972"
    assert pipeline["attributes"]["cicd.pipeline.result"] == "success"
    assert task["parent_span_id"] == pipeline["span_id"]
    assert task["attributes"]["cicd.pipeline.task.run.id"] == "129194268575"
    assert task["attributes"]["cicd.pipeline.task.run.url.full"]


def test_job_success_does_not_imply_pipeline_success():
    report = cicd_telemetry.produce(
        complete_facts(job_conclusion="success", run_conclusion="failure")
    )
    assert report["status"] == "produced"
    pipeline, task = report["spans"]
    assert pipeline["attributes"]["cicd.pipeline.result"] == "failure"
    assert pipeline["attributes"]["error.type"] == "failure"
    assert task["attributes"]["cicd.pipeline.task.run.result"] == "success"
    assert "error.type" not in task["attributes"]


def test_job_success_with_run_in_progress_is_partial_task_only():
    report = cicd_telemetry.produce(
        complete_facts(
            run_conclusion=None,
            run_started_at_ns=None,
            run_completed_at_ns=None,
            job_conclusion="success",
        )
    )
    assert report["status"] == "partial"
    assert report["reason"] == "missing_run_terminal_result"
    assert len(report["spans"]) == 1
    assert report["spans"][0]["kind"] == cicd_telemetry.SPAN_KIND_INTERNAL
    assert (
        report["spans"][0]["attributes"]["cicd.pipeline.task.run.result"] == "success"
    )


def test_explicit_null_run_conclusion_does_not_use_job_success():
    facts = complete_facts(run_conclusion=None, job_conclusion="success")
    assert facts.run_conclusion is None
    assert facts.job_conclusion == "success"
    report = cicd_telemetry.produce(facts)
    assert report["status"] == "partial"
    assert report["reason"] == "missing_run_terminal_result"
    assert all(
        span["kind"] != cicd_telemetry.SPAN_KIND_SERVER for span in report["spans"]
    )


def test_legacy_shared_conclusion_does_not_fabricate_pipeline_or_task():
    facts = cicd_telemetry.facts_from_mapping(
        {
            "workflow_name": "Engineering CI",
            "run_id": "1",
            "run_attempt": 1,
            "job_id": "2",
            "job_name": "checks",
            "job_url": "https://example.test/job/2",
            "conclusion": "success",
            "head_sha": "d" * 40,
            "repository": "dymzz/engineering-ci",
            "run_started_at_ns": RUN_START,
            "run_completed_at_ns": RUN_END,
            "job_started_at_ns": JOB_START,
            "job_completed_at_ns": JOB_END,
        }
    )
    assert facts.run_conclusion is None
    assert facts.job_conclusion is None
    report = cicd_telemetry.produce(facts)
    assert report["status"] == "incomplete"
    assert report["spans"] == []
    assert "cicd.pipeline.result" not in json.dumps(report)


def test_missing_run_identity_is_incomplete_not_forged():
    report = cicd_telemetry.produce(complete_facts(run_id=None, run_attempt=None))
    assert report["status"] == "incomplete"
    assert report["reason"] == "missing_run_identity"
    assert report["spans"] == []


def test_missing_timestamps_refuse_timed_spans():
    report = cicd_telemetry.produce(
        complete_facts(
            run_started_at_ns=None,
            run_completed_at_ns=None,
            job_started_at_ns=None,
            job_completed_at_ns=None,
        )
    )
    assert report["status"] == "incomplete"
    assert report["reason"] == "missing_run_timestamps"
    assert report["spans"] == []


def test_reversed_run_timestamps_are_invalid():
    report = cicd_telemetry.produce(
        complete_facts(run_started_at_ns=RUN_END, run_completed_at_ns=RUN_START)
    )
    assert report["status"] == "partial"
    assert report["reason"] == "invalid_run_timestamps"
    assert all(
        span["kind"] != cicd_telemetry.SPAN_KIND_SERVER for span in report["spans"]
    )
    assert any(
        span["kind"] == cicd_telemetry.SPAN_KIND_INTERNAL for span in report["spans"]
    )


def test_reversed_job_timestamps_are_invalid():
    report = cicd_telemetry.produce(
        complete_facts(job_started_at_ns=JOB_END, job_completed_at_ns=JOB_START)
    )
    assert report["status"] == "partial"
    assert report["reason"] == "invalid_job_timestamps"
    assert len(report["spans"]) == 1
    assert report["spans"][0]["kind"] == cicd_telemetry.SPAN_KIND_SERVER


def test_equal_timestamps_are_valid_zero_duration():
    report = cicd_telemetry.produce(
        complete_facts(
            run_started_at_ns=RUN_START,
            run_completed_at_ns=RUN_START,
            job_started_at_ns=JOB_START,
            job_completed_at_ns=JOB_START,
        )
    )
    assert report["status"] == "produced"
    pipeline, task = report["spans"]
    assert pipeline["start_time_unix_nano"] == pipeline["end_time_unix_nano"]
    assert task["start_time_unix_nano"] == task["end_time_unix_nano"]


def test_missing_task_url_is_not_complete_production():
    report = cicd_telemetry.produce(complete_facts(job_url=None))
    assert report["status"] == "partial"
    assert report["reason"] == "missing_task_required_identity"
    assert len(report["spans"]) == 1
    assert report["spans"][0]["kind"] == cicd_telemetry.SPAN_KIND_SERVER


def test_result_mapping_preserves_semconv_values():
    cases = {
        "cancelled": "cancellation",
        "canceled": "cancellation",
        "skipped": "skip",
        "timed_out": "timeout",
        "failure": "failure",
        "success": "success",
    }
    for github, expected in cases.items():
        result, error_type = cicd_telemetry.map_github_result(github)
        assert result == expected
        if expected in {"failure", "error"}:
            assert error_type == github
        else:
            assert error_type is None
        report = cicd_telemetry.produce(
            complete_facts(run_conclusion=github, job_conclusion=github)
        )
        assert report["spans"][0]["attributes"]["cicd.pipeline.result"] == expected
        task = report["spans"][1]
        assert task["attributes"]["cicd.pipeline.task.run.result"] == expected
        if expected in {"failure", "error"}:
            assert report["spans"][0]["attributes"]["error.type"] == github
            assert task["attributes"]["error.type"] == github
        else:
            assert "error.type" not in report["spans"][0]["attributes"]
            assert "error.type" not in task["attributes"]


def test_task_span_ids_differ_across_jobs_same_run():
    first = cicd_telemetry.produce(complete_facts(job_id="job-a"))
    second = cicd_telemetry.produce(complete_facts(job_id="job-b"))
    assert first["spans"][0]["span_id"] == second["spans"][0]["span_id"]
    assert first["spans"][0]["trace_id"] == second["spans"][0]["trace_id"]
    assert first["spans"][1]["span_id"] != second["spans"][1]["span_id"]
    again = cicd_telemetry.produce(complete_facts(job_id="job-a"))
    assert again["spans"][1]["span_id"] == first["spans"][1]["span_id"]


def test_selection_status_is_not_mapped_as_pipeline_result():
    report = cicd_telemetry.produce(complete_facts())
    blob = json.dumps(report)
    assert "not_executed" not in blob
    assert "required_checks_not_executed" not in blob


def test_facts_from_actions_env_separates_run_and_job():
    facts = cicd_telemetry.facts_from_actions_env(
        {
            "GITHUB_WORKFLOW": "Engineering CI",
            "GITHUB_RUN_ID": "99",
            "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_JOB": "checks",
            "GITHUB_SHA": "b" * 40,
            "GITHUB_REPOSITORY": "dymzz/engineering-ci",
            "ENGINEERING_CI_JOB_ID": "job-1",
            "ENGINEERING_CI_JOB_URL": "https://example.test/job/1",
            "ENGINEERING_CI_JOB_CONCLUSION": "success",
            "ENGINEERING_CI_RUN_CONCLUSION": "failure",
            "ENGINEERING_CI_RUN_STARTED_AT": str(RUN_START),
            "ENGINEERING_CI_RUN_COMPLETED_AT": str(RUN_END),
            "ENGINEERING_CI_JOB_STARTED_AT": str(JOB_START),
            "ENGINEERING_CI_JOB_COMPLETED_AT": str(JOB_END),
        }
    )
    assert facts.job_conclusion == "success"
    assert facts.run_conclusion == "failure"
    report = cicd_telemetry.produce(facts)
    assert report["spans"][0]["attributes"]["cicd.pipeline.result"] == "failure"
    assert (
        report["spans"][1]["attributes"]["cicd.pipeline.task.run.result"] == "success"
    )


def test_cli_writes_candidate_artifact(tmp_path):
    facts_path = tmp_path / "facts.json"
    facts_path.write_text(
        json.dumps(
            {
                "workflow_name": "Engineering CI",
                "run_id": "1",
                "run_attempt": 1,
                "job_id": "2",
                "job_name": "checks",
                "job_url": "https://example.test/job/2",
                "run_conclusion": "success",
                "job_conclusion": "success",
                "head_sha": "c" * 40,
                "repository": "dymzz/engineering-ci",
                "run_started_at_ns": RUN_START,
                "run_completed_at_ns": RUN_END,
                "job_started_at_ns": JOB_START,
                "job_completed_at_ns": JOB_END,
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "out.json"
    assert (
        cicd_telemetry.main(
            ["--facts", str(facts_path), "--output", str(output), "--json"]
        )
        == 0
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["kind"] == "engineering-ci-cicd-telemetry"
    assert payload["status"] == "produced"
    assert payload["evidence_class"] == "candidate_record"
    assert Path(output).is_file()


def test_cli_incomplete_exits_3(tmp_path):
    facts_path = tmp_path / "facts.json"
    facts_path.write_text(json.dumps({"workflow_name": "x"}), encoding="utf-8")
    output = tmp_path / "out.json"
    assert (
        cicd_telemetry.main(["--facts", str(facts_path), "--output", str(output)]) == 3
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "incomplete"
