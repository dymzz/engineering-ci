"""GitHub Run/Job facts → CI/CD candidate telemetry records (ECI #75)."""

import json
import subprocess
from pathlib import Path

from engineering_ci import cicd_telemetry

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
    assert report["producer"]["name"] == "eci_core.cicd_telemetry"
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
    assert pipeline["attributes"]["cicd.pipeline.run.id"] == "42627302972/1"
    assert report["resource"]["cicd.pipeline.run.id"] == "42627302972/1"
    assert pipeline["attributes"]["cicd.pipeline.result"] == "success"
    assert pipeline["status"]["code"] == cicd_telemetry.STATUS_UNSET
    assert task["status"]["code"] == cicd_telemetry.STATUS_UNSET
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
    assert pipeline["status"]["code"] == cicd_telemetry.STATUS_ERROR
    assert task["attributes"]["cicd.pipeline.task.run.result"] == "success"
    assert task["status"]["code"] == cicd_telemetry.STATUS_UNSET
    assert "error.type" not in task["attributes"]


def test_non_error_terminals_leave_span_status_unset():
    for conclusion in ("cancelled", "skipped"):
        report = cicd_telemetry.produce(
            complete_facts(run_conclusion=conclusion, job_conclusion=conclusion)
        )
        assert report["status"] == "produced"
        for span in report["spans"]:
            assert span["status"]["code"] == cicd_telemetry.STATUS_UNSET
            assert "error.type" not in span["attributes"]


def test_timed_out_keeps_timeout_result_and_records_error():
    report = cicd_telemetry.produce(
        complete_facts(run_conclusion="timed_out", job_conclusion="timed_out")
    )
    assert report["status"] == "produced"
    pipeline, task = report["spans"]
    assert pipeline["attributes"]["cicd.pipeline.result"] == "timeout"
    assert pipeline["attributes"]["error.type"] == "timeout"
    assert pipeline["status"]["code"] == cicd_telemetry.STATUS_ERROR
    assert task["attributes"]["cicd.pipeline.task.run.result"] == "timeout"
    assert task["attributes"]["error.type"] == "timeout"
    assert task["status"]["code"] == cicd_telemetry.STATUS_ERROR


def test_mixed_run_timeout_job_success_keeps_separate_terminals():
    report = cicd_telemetry.produce(
        complete_facts(run_conclusion="timed_out", job_conclusion="success")
    )
    assert report["status"] == "produced"
    pipeline, task = report["spans"]
    assert pipeline["attributes"]["cicd.pipeline.result"] == "timeout"
    assert pipeline["attributes"]["error.type"] == "timeout"
    assert pipeline["status"]["code"] == cicd_telemetry.STATUS_ERROR
    assert task["attributes"]["cicd.pipeline.task.run.result"] == "success"
    assert task["status"]["code"] == cicd_telemetry.STATUS_UNSET
    assert "error.type" not in task["attributes"]


def test_mapping_and_env_reject_non_positive_attempt():
    for attempt in (0, -1):
        try:
            cicd_telemetry.facts_from_mapping(
                {
                    "workflow_name": "Engineering CI",
                    "run_id": "1",
                    "run_attempt": attempt,
                }
            )
            raise AssertionError("expected run_attempt_invalid")
        except cicd_telemetry.TelemetryError as error:
            assert error.code == "run_attempt_invalid"
        facts = cicd_telemetry.facts_from_actions_env(
            {
                "GITHUB_WORKFLOW": "Engineering CI",
                "GITHUB_RUN_ID": "1",
                "GITHUB_RUN_ATTEMPT": str(attempt),
            }
        )
        assert facts.run_attempt is None
        assert not facts.has_run_identity
        report = cicd_telemetry.produce(facts)
        assert report["status"] == "incomplete"


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
            job_started_at_ns=RUN_START,
            job_completed_at_ns=RUN_START,
        )
    )
    assert report["status"] == "produced"
    pipeline, task = report["spans"]
    assert pipeline["start_time_unix_nano"] == pipeline["end_time_unix_nano"]
    assert task["start_time_unix_nano"] == task["end_time_unix_nano"]


def test_job_after_completed_run_is_partial_inconsistency():
    report = cicd_telemetry.produce(
        complete_facts(
            run_started_at_ns=RUN_START,
            run_completed_at_ns=RUN_END,
            job_started_at_ns=RUN_END + 1,
            job_completed_at_ns=RUN_END + 2,
        )
    )
    assert report["status"] == "partial"
    assert report["reason"] == "inconsistent_run_job_timestamps"
    assert len(report["spans"]) == 2


def test_unmapped_github_conclusions_stay_unsupported():
    for value in ("neutral", "action_required", "stale"):
        assert cicd_telemetry.map_github_result(value) == (None, None)
        report = cicd_telemetry.produce(
            complete_facts(run_conclusion=value, job_conclusion=value)
        )
        assert report["status"] == "incomplete"
        assert report["reason"] == "unsupported_run_conclusion"
        assert report["spans"] == []
        assert report["facts"]["run_conclusion"] == value
        assert report["facts"]["job_conclusion"] == value


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
        elif expected == "timeout":
            assert error_type == "timeout"
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
            assert report["spans"][0]["status"]["code"] == cicd_telemetry.STATUS_ERROR
        elif expected == "timeout":
            assert report["spans"][0]["attributes"]["error.type"] == "timeout"
            assert task["attributes"]["error.type"] == "timeout"
            assert report["spans"][0]["status"]["code"] == cicd_telemetry.STATUS_ERROR
            assert task["status"]["code"] == cicd_telemetry.STATUS_ERROR
        else:
            assert "error.type" not in report["spans"][0]["attributes"]
            assert "error.type" not in task["attributes"]
            assert report["spans"][0]["status"]["code"] == cicd_telemetry.STATUS_UNSET


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


def _api_run_job_fixture():
    run = {
        "id": 42769746408,
        "name": "Engineering CI",
        "run_attempt": 1,
        "status": "completed",
        "conclusion": "success",
        "head_sha": "e" * 40,
        "run_started_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:02:00Z",
        "html_url": "https://github.com/dymzz/engineering-ci/actions/runs/42769746408",
        "repository": {"full_name": "dymzz/engineering-ci"},
    }
    job = {
        "id": 129674497066,
        "name": "python-ci / checks",
        "status": "completed",
        "conclusion": "success",
        "started_at": "2024-01-01T00:00:10Z",
        "completed_at": "2024-01-01T00:01:40Z",
        "html_url": (
            "https://github.com/dymzz/engineering-ci/actions/runs/"
            "42769746408/job/129674497066"
        ),
    }
    return run, job


def test_facts_from_github_api_objects_separate_terminals():
    run, job = _api_run_job_fixture()
    run["conclusion"] = "failure"
    job["conclusion"] = "success"
    facts = cicd_telemetry.facts_from_github_api_objects(run, job)
    assert facts.run_conclusion == "failure"
    assert facts.job_conclusion == "success"
    assert facts.run_started_at_ns is not None
    assert facts.run_completed_at_ns is None
    assert facts.run_updated_at_ns is not None
    assert facts.run_url.endswith("/42769746408")
    assert facts.job_url.endswith("129674497066")


def test_api_updated_at_is_not_exact_completed_at():
    run, job = _api_run_job_fixture()
    # updated_at intentionally later than job end (metadata change / rerun noise).
    run["updated_at"] = "2024-01-01T12:00:00Z"
    facts = cicd_telemetry.facts_from_github_api_objects(run, job)
    assert facts.run_completed_at_ns is None
    assert facts.run_updated_at_ns == cicd_telemetry.parse_time_ns(
        "2024-01-01T12:00:00Z"
    )
    report = cicd_telemetry.produce(facts, source="github_api", github_api_fetched=True)
    assert report["status"] == "partial"
    assert report["reason"] == "run_completed_at_unproven"
    assert all(
        span["kind"] != cicd_telemetry.SPAN_KIND_SERVER for span in report["spans"]
    )


def test_fetch_github_run_job_uses_matching_attempt():
    run1, job1 = _api_run_job_fixture()
    run1["run_attempt"] = 1
    run1["conclusion"] = "failure"
    job1["run_id"] = run1["id"]
    job1["run_attempt"] = 1
    job1["conclusion"] = "failure"
    run2 = {
        **run1,
        "run_attempt": 2,
        "conclusion": "success",
        "updated_at": "2024-01-01T00:05:00Z",
        "html_url": run1["html_url"] + "/attempts/2",
    }
    job2 = {
        **job1,
        "id": 222,
        "run_attempt": 2,
        "conclusion": "success",
        "name": "python-ci / checks",
        "html_url": job1["html_url"].replace("129674497066", "222"),
    }

    def api_get(path):
        if "/attempts/1/jobs" in path:
            page = 1
            if "page=2" in path:
                page = 2
            if page == 1:
                return {"total_count": 1, "jobs": [job1]}
            return {"total_count": 1, "jobs": []}
        if "/attempts/2/jobs" in path:
            return {
                "total_count": 2,
                "jobs": [job2, {**job2, "id": 333, "name": "other"}],
            }
        if path.endswith("/attempts/1"):
            return run1
        if path.endswith("/attempts/2"):
            return run2
        if path.endswith(f"/runs/{run1['id']}"):
            # Latest view would lie if mixed with attempt-1 jobs.
            return run2
        raise AssertionError(path)

    facts, coverage = cicd_telemetry.fetch_github_run_job(
        "dymzz/engineering-ci",
        42769746408,
        attempt=1,
        job_id=129674497066,
        api_get=api_get,
    )
    assert facts.run_attempt == 1
    assert facts.run_conclusion == "failure"
    assert facts.job_conclusion == "failure"
    assert coverage["completed_jobs_total"] == 1
    assert coverage["job_selection"] == "job_id"

    facts2, coverage2 = cicd_telemetry.fetch_github_run_job(
        "dymzz/engineering-ci",
        42769746408,
        attempt=2,
        job_id=222,
        api_get=api_get,
    )
    assert facts2.run_attempt == 2
    assert facts2.run_conclusion == "success"
    assert facts2.job_id == "222"
    assert coverage2["completed_jobs_total"] == 2
    report = cicd_telemetry.produce(
        facts2,
        source="github_api",
        github_api_fetched=True,
        completed_jobs_total=coverage2["completed_jobs_total"],
        job_selection=coverage2["job_selection"],
    )
    assert report["claims"]["github_api_fetched"] is True
    assert report["claims"]["otlp_delivered"] is False
    assert report["claims"]["github_authenticated"] is False
    assert report["resource"]["cicd.pipeline.run.id"] == "42769746408/2"
    assert report["reason"] in {
        "run_completed_at_unproven",
        "partial_job_coverage",
    }
    assert report["status"] == "partial"


def test_fetch_rejects_job_not_in_attempt_jobs():
    run, job = _api_run_job_fixture()
    job["run_id"] = run["id"]
    job["run_attempt"] = 1

    def api_get(path):
        if "/jobs" in path:
            return {"total_count": 1, "jobs": [job]}
        if "/attempts/" in path:
            return run
        return run

    try:
        cicd_telemetry.fetch_github_run_job(
            "dymzz/engineering-ci",
            42769746408,
            attempt=1,
            job_id=999999,
            api_get=api_get,
        )
        raise AssertionError("expected job_not_found")
    except cicd_telemetry.TelemetryError as error:
        assert error.code == "job_not_found"


def test_jobs_snapshot_consistency_and_coverage_denominator_matrix():
    """Single matrix for P2-1 denominator + P2-2 snapshot consistency."""
    run, job = _api_run_job_fixture()
    run["run_attempt"] = 1

    def api_for(jobs_payload):
        def api_get(path):
            if "/jobs" in path:
                if callable(jobs_payload):
                    return jobs_payload(path)
                return jobs_payload
            if "/attempts/1" in path:
                return run
            raise AssertionError(path)

        return api_get

    # Same-page duplicate Job ID → refuse snapshot.
    try:
        cicd_telemetry.fetch_github_run_job(
            "dymzz/engineering-ci",
            42769746408,
            attempt=1,
            api_get=api_for(
                {
                    "total_count": 2,
                    "jobs": [
                        {**job, "id": 101, "name": "a", "run_attempt": 1},
                        {**job, "id": 101, "name": "dup", "run_attempt": 1},
                    ],
                }
            ),
        )
        raise AssertionError("expected jobs_snapshot_inconsistent")
    except cicd_telemetry.TelemetryError as error:
        assert error.code == "jobs_snapshot_inconsistent"

    # Cross-page duplicate ID with matching total → still refuse.
    def cross_page(path):
        if "page=2" in path:
            return {
                "total_count": cicd_telemetry._JOBS_PAGE_SIZE + 1,
                "jobs": [{**job, "id": 1, "name": "dup-cross", "run_attempt": 1}],
            }
        return {
            "total_count": cicd_telemetry._JOBS_PAGE_SIZE + 1,
            "jobs": [
                {
                    **job,
                    "id": index,
                    "name": f"job-{index}",
                    "run_id": run["id"],
                    "run_attempt": 1,
                }
                for index in range(1, cicd_telemetry._JOBS_PAGE_SIZE + 1)
            ],
        }

    try:
        cicd_telemetry.fetch_github_run_job(
            "dymzz/engineering-ci",
            42769746408,
            attempt=1,
            api_get=api_for(cross_page),
        )
        raise AssertionError("expected jobs_snapshot_inconsistent")
    except cicd_telemetry.TelemetryError as error:
        assert error.code == "jobs_snapshot_inconsistent"

    # Page total_count changes across pages → refuse snapshot.
    def shifting_total(path):
        if "page=2" in path:
            return {
                "total_count": cicd_telemetry._JOBS_PAGE_SIZE + 2,
                "jobs": [
                    {**job, "id": 999, "name": "extra", "run_attempt": 1},
                    {**job, "id": 1000, "name": "extra2", "run_attempt": 1},
                ],
            }
        return {
            "total_count": cicd_telemetry._JOBS_PAGE_SIZE + 1,
            "jobs": [
                {
                    **job,
                    "id": index,
                    "name": f"job-{index}",
                    "run_id": run["id"],
                    "run_attempt": 1,
                }
                for index in range(1, cicd_telemetry._JOBS_PAGE_SIZE + 1)
            ],
        }

    try:
        cicd_telemetry.fetch_github_run_job(
            "dymzz/engineering-ci",
            42769746408,
            attempt=1,
            api_get=api_for(shifting_total),
        )
        raise AssertionError("expected jobs_snapshot_inconsistent")
    except cicd_telemetry.TelemetryError as error:
        assert error.code == "jobs_snapshot_inconsistent"

    # 2 complete + 1 completed missing ID + 1 in_progress.
    mixed = [
        {
            **job,
            "id": 201,
            "name": "checks",
            "run_id": run["id"],
            "run_attempt": 1,
            "html_url": "https://github.com/dymzz/engineering-ci/actions/runs/1/job/201",
        },
        {
            **job,
            "id": 202,
            "name": "lint",
            "run_id": run["id"],
            "run_attempt": 1,
            "html_url": "https://github.com/dymzz/engineering-ci/actions/runs/1/job/202",
        },
        {
            **job,
            "id": None,
            "name": "no-id",
            "status": "completed",
            "conclusion": "success",
            "run_id": run["id"],
            "run_attempt": 1,
            "html_url": "https://github.com/dymzz/engineering-ci/actions/runs/1/job/missing",
        },
        {
            **job,
            "id": 204,
            "name": "still-running",
            "status": "in_progress",
            "conclusion": None,
            "run_id": run["id"],
            "run_attempt": 1,
            "completed_at": None,
        },
    ]
    facts, coverage = cicd_telemetry.fetch_github_run_job(
        "dymzz/engineering-ci",
        42769746408,
        attempt=1,
        api_get=api_for({"total_count": 4, "jobs": mixed}),
    )
    assert coverage["jobs_observed_total"] == 4
    assert coverage["completed_jobs_total"] == 3
    assert coverage["noncompleted_jobs_total"] == 1
    assert coverage["job_selection"] == "all_completed"
    assert len(coverage["task_facts"]) == 3
    report = cicd_telemetry.produce(
        facts,
        source="github_api",
        github_api_fetched=True,
        completed_jobs_total=coverage["completed_jobs_total"],
        job_selection=coverage["job_selection"],
        task_facts=coverage["task_facts"],
        jobs_observed_total=coverage["jobs_observed_total"],
        noncompleted_jobs_total=coverage["noncompleted_jobs_total"],
    )
    assert report["coverage"]["jobs_observed_total"] == 4
    assert report["coverage"]["completed_jobs_total"] == 3
    assert report["coverage"]["noncompleted_jobs_total"] == 1
    assert report["coverage"]["task_spans_emitted"] == 2
    assert len(report["coverage"]["tasks_omitted"]) == 1
    assert report["coverage"]["tasks_omitted"][0]["reason"] == (
        "missing_task_required_identity"
    )
    assert "source_index" in report["coverage"]["tasks_omitted"][0]
    assert "job_id" not in report["coverage"]["tasks_omitted"][0]
    assert (
        report["coverage"]["completed_jobs_total"]
        == report["coverage"]["task_spans_emitted"]
        + len(report["coverage"]["tasks_omitted"])
    )
    assert report["status"] == "partial"
    assert report["reason"] in {
        "missing_task_required_identity",
        "partial_job_coverage",
        "run_completed_at_unproven",
    }


def test_all_completed_omits_incomplete_job_and_marks_partial_coverage():
    run_facts = complete_facts(
        job_id=None, job_name=None, job_url=None, job_conclusion=None
    )
    good = complete_facts(job_id="1", job_name="ok", job_url="https://example.test/1")
    bad = complete_facts(
        job_id="2",
        job_name="bad",
        job_url=None,
        job_conclusion="success",
    )
    report = cicd_telemetry.produce(
        run_facts,
        task_facts=[good, bad],
        completed_jobs_total=2,
        job_selection="all_completed",
    )
    assert report["coverage"]["task_spans_emitted"] == 1
    assert report["coverage"]["tasks_omitted"] == [
        {"job_id": "2", "reason": "missing_task_required_identity"}
    ]
    assert report["status"] == "partial"
    assert report["reason"] in {
        "missing_task_required_identity",
        "partial_job_coverage",
    }


def test_fetch_paginates_jobs_across_pages():
    run, job = _api_run_job_fixture()
    run["run_attempt"] = 1
    page1_jobs = [
        {
            **job,
            "id": index,
            "name": f"job-{index}",
            "run_id": run["id"],
            "run_attempt": 1,
        }
        for index in range(1, cicd_telemetry._JOBS_PAGE_SIZE + 1)
    ]
    target = {
        **job,
        "id": 4242,
        "name": "page-two",
        "run_id": run["id"],
        "run_attempt": 1,
    }

    def api_get(path):
        if "/jobs" in path:
            if "page=2" in path:
                return {
                    "total_count": cicd_telemetry._JOBS_PAGE_SIZE + 1,
                    "jobs": [target],
                }
            return {
                "total_count": cicd_telemetry._JOBS_PAGE_SIZE + 1,
                "jobs": page1_jobs,
            }
        if "/attempts/1" in path:
            return run
        raise AssertionError(path)

    facts, coverage = cicd_telemetry.fetch_github_run_job(
        "dymzz/engineering-ci",
        42769746408,
        attempt=1,
        job_id=4242,
        api_get=api_get,
    )
    assert facts.job_id == "4242"
    assert facts.job_name == "page-two"
    assert coverage["completed_jobs_total"] == cicd_telemetry._JOBS_PAGE_SIZE + 1


def test_fetch_rejects_short_page_before_total_count():
    def api_get(path):
        if "page=2" in path:
            return {"total_count": cicd_telemetry._JOBS_PAGE_SIZE + 1, "jobs": []}
        return {
            "total_count": cicd_telemetry._JOBS_PAGE_SIZE + 1,
            "jobs": [{"id": index, "status": "completed"} for index in range(100)],
        }

    try:
        cicd_telemetry.fetch_attempt_jobs(
            api_get, "repos/x/y/actions/runs/1/attempts/1/jobs"
        )
        raise AssertionError("expected jobs_page_incomplete")
    except cicd_telemetry.TelemetryError as error:
        assert error.code == "jobs_page_incomplete"


def test_gh_api_get_maps_timeout_and_missing_cli():
    def timeout_runner(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["gh", "api"], timeout=60)

    def missing_runner(*_args, **_kwargs):
        raise FileNotFoundError("gh")

    try:
        cicd_telemetry.gh_api_get("repos/x/y", runner=timeout_runner)
        raise AssertionError("expected timeout")
    except cicd_telemetry.TelemetryError as error:
        assert error.code == "github_api_timeout"
    try:
        cicd_telemetry.gh_api_get("repos/x/y", runner=missing_runner)
        raise AssertionError("expected unavailable")
    except cicd_telemetry.TelemetryError as error:
        assert error.code == "github_cli_unavailable"


def test_in_progress_run_has_no_completed_bound():
    run, job = _api_run_job_fixture()
    run["status"] = "in_progress"
    run["conclusion"] = None
    run["updated_at"] = None
    facts = cicd_telemetry.facts_from_github_api_objects(run, job)
    assert facts.run_conclusion is None
    assert facts.run_completed_at_ns is None
    report = cicd_telemetry.produce(facts, source="github_api", github_api_fetched=True)
    assert report["status"] == "partial"
    assert report["reason"] == "missing_run_terminal_result"


def test_otlp_json_export_and_flush_failure(tmp_path):
    report = cicd_telemetry.produce(complete_facts())
    cicd_telemetry.attach_otlp_json(report)
    assert report["claims"]["otlp_json_prepared"] is True
    assert report["claims"]["otlp_json_exported"] is False
    assert report["otlp_json"]["resourceSpans"]
    span = report["otlp_json"]["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert span["kind"] == cicd_telemetry.SPAN_KIND_SERVER
    assert "attributes" in span
    out = tmp_path / "otel.json"
    cicd_telemetry.write_otlp_json(report, out)
    body = json.loads(out.read_text(encoding="utf-8"))
    assert body["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert report["claims"]["otlp_json_exported"] is True
    assert report["claims"]["otlp_json_sha256"]
    assert report["claims"]["otlp_delivered"] is False
    decoded = cicd_telemetry.decode_otlp_export_request(body)
    assert len(decoded.resource_spans) == 1
    resource_attrs = {
        item.key: item.value.string_value
        for item in decoded.resource_spans[0].resource.attributes
    }
    assert resource_attrs["cicd.pipeline.run.id"] == "42627302972/1"
    wire_span = body["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    proto_span = decoded.resource_spans[0].scope_spans[0].spans[0]
    assert proto_span.trace_id == bytes.fromhex(wire_span["traceId"])
    assert proto_span.span_id == bytes.fromhex(wire_span["spanId"])
    assert proto_span.start_time_unix_nano == RUN_START
    assert proto_span.end_time_unix_nano == RUN_END
    assert wire_span["status"]["code"] == cicd_telemetry.STATUS_UNSET
    assert proto_span.status.code == cicd_telemetry.STATUS_UNSET
    task = next(
        item
        for item in decoded.resource_spans[0].scope_spans[0].spans
        if item.name.startswith("task:")
    )
    task_attrs = {item.key: item.value.string_value for item in task.attributes}
    assert task_attrs["cicd.pipeline.task.run.url.full"]

    timed = cicd_telemetry.produce(
        complete_facts(run_conclusion="timed_out", job_conclusion="timed_out")
    )
    cicd_telemetry.attach_otlp_json(timed)
    timed_body = timed["otlp_json"]
    timed_decoded = cicd_telemetry.decode_otlp_export_request(timed_body)
    timed_pipeline = next(
        item
        for item in timed_decoded.resource_spans[0].scope_spans[0].spans
        if not item.name.startswith("task:")
    )
    timed_attrs = {
        item.key: item.value.string_value for item in timed_pipeline.attributes
    }
    assert timed_attrs["cicd.pipeline.result"] == "timeout"
    assert timed_attrs["error.type"] == "timeout"
    assert timed_pipeline.status.code == cicd_telemetry.STATUS_ERROR
    timed_wire = next(
        item
        for item in timed_body["resourceSpans"][0]["scopeSpans"][0]["spans"]
        if not item["name"].startswith("task:")
    )
    assert timed_wire["status"]["code"] == cicd_telemetry.STATUS_ERROR
    assert any(
        attr["key"] == "cicd.pipeline.result"
        and attr["value"]["stringValue"] == "timeout"
        for attr in timed_wire["attributes"]
    )

    # File-as-directory makes mkdir(parents=True) fail on Windows/POSIX alike.
    file_parent = tmp_path / "not-a-dir"
    file_parent.write_text("x", encoding="utf-8")
    bad = file_parent / "otel.json"
    try:
        cicd_telemetry.write_otlp_json(report, bad)
        raise AssertionError("expected flush failure")
    except cicd_telemetry.TelemetryError as error:
        assert error.code == "otlp_json_flush_failed"
        assert report["claims"]["otlp_json_exported"] is False


def test_cli_otlp_prepared_without_export_request(tmp_path):
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
                "head_sha": "f" * 40,
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
        cicd_telemetry.main(["--facts", str(facts_path), "--output", str(output)]) == 0
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["otlp_json_prepared"] is True
    assert payload["otlp_json_exported"] is False
    assert payload["claims"]["otlp_json_prepared"] is True
    assert payload["claims"]["otlp_json_exported"] is False


def test_cli_otlp_json_flush_failure_exits_3(tmp_path):
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
                "head_sha": "f" * 40,
                "repository": "dymzz/engineering-ci",
                "run_started_at_ns": RUN_START,
                "run_completed_at_ns": RUN_END,
                "job_started_at_ns": JOB_START,
                "job_completed_at_ns": JOB_END,
            }
        ),
        encoding="utf-8",
    )
    file_parent = tmp_path / "blocked"
    file_parent.write_text("x", encoding="utf-8")
    output = tmp_path / "out.json"
    code = cicd_telemetry.main(
        [
            "--facts",
            str(facts_path),
            "--output",
            str(output),
            "--otlp-json",
            str(file_parent / "otel.json"),
        ]
    )
    assert code == 3
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["otlp_json_prepared"] is True
    assert payload["otlp_json_exported"] is False
    assert payload["claims"]["otlp_json_exported"] is False


def test_cli_otlp_unavailable_for_incomplete_exits_3(tmp_path):
    facts_path = tmp_path / "facts.json"
    facts_path.write_text(json.dumps({"workflow_name": "x"}), encoding="utf-8")
    code = cicd_telemetry.main(
        [
            "--facts",
            str(facts_path),
            "--output",
            str(tmp_path / "out.json"),
            "--otlp-json",
            str(tmp_path / "otel.json"),
        ]
    )
    assert code == 3
    assert not (tmp_path / "otel.json").exists()
    payload = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert payload["otlp_json_exported"] is False
