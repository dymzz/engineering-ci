"""GitHub Run/Job facts → CI/CD candidate telemetry records (ECI #75)."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from engineering_ci import cicd_telemetry, github_receiver

RUN_START = 1_700_000_000_000_000_000
RUN_END = 1_700_000_120_000_000_000
JOB_START = 1_700_000_010_000_000_000
JOB_END = 1_700_000_090_000_000_000


def _external_githubreceiver_spans(run_id, attempt, job_ids, *, job_urls=None):
    run_int = int(run_id)
    attempt_int = int(attempt)
    trace_id = github_receiver.trace_id(run_int, attempt_int)
    root_span_id = github_receiver.root_span_id(run_int, attempt_int)
    urls = job_urls or {}
    spans = [
        {
            "trace_id": trace_id,
            "span_id": root_span_id,
            "parent_span_id": None,
            "kind": cicd_telemetry.SPAN_KIND_SERVER,
            "name": "Engineering CI",
            "start_time_unix_nano": RUN_START,
            "end_time_unix_nano": RUN_END,
            "attributes": {"cicd.pipeline.run.id": f"{run_int}/{attempt_int}"},
        }
    ]
    for job_id in job_ids:
        job_str = str(job_id)
        attrs = {
            "cicd.pipeline.run.id": f"{run_int}/{attempt_int}",
            cicd_telemetry.ATTR_TASK_RUN_ID: job_str,
        }
        if job_str in urls:
            attrs[cicd_telemetry.ATTR_TASK_RUN_URL] = urls[job_str]
        spans.append(
            {
                "trace_id": trace_id,
                "span_id": github_receiver.job_span_id(int(job_str)),
                "parent_span_id": root_span_id,
                "kind": cicd_telemetry.SPAN_KIND_INTERNAL,
                "name": f"job:{job_str}",
                "start_time_unix_nano": JOB_START,
                "end_time_unix_nano": JOB_END,
                "attributes": attrs,
            }
        )
    return spans


def _attach_external_githubreceiver_traces(report):
    facts = report["facts"]
    spans = _external_githubreceiver_spans(
        facts["run_id"], facts["run_attempt"], [facts["job_id"]]
    )
    otlp_spans = []
    for span in spans:
        item = {
            "traceId": span["trace_id"],
            "spanId": span["span_id"],
            "name": span["name"],
            "kind": span["kind"],
            "startTimeUnixNano": str(span["start_time_unix_nano"]),
            "endTimeUnixNano": str(span["end_time_unix_nano"]),
            "attributes": [
                {"key": key, "value": {"stringValue": value}}
                for key, value in span["attributes"].items()
            ],
        }
        if span["parent_span_id"]:
            item["parentSpanId"] = span["parent_span_id"]
        otlp_spans.append(item)
    report["otlp_json"] = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "service.name",
                            "value": {"stringValue": "githubreceiver"},
                        },
                        {
                            "key": "cicd.pipeline.run.id",
                            "value": {
                                "stringValue": f"{facts['run_id']}/{facts['run_attempt']}"
                            },
                        },
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "githubreceiver"},
                        "spans": otlp_spans,
                    }
                ],
            }
        ]
    }
    return spans


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
    assert report["status"] == "witness_only"
    assert report["evidence_class"] == "candidate_record"
    assert report["producer"]["name"] == "eci_core.cicd_telemetry"
    assert report["producer"]["trace_producer_retired"] is True
    assert (
        report["producer"]["trace_owner"]
        == "opentelemetry-collector-contrib/githubreceiver"
    )
    assert report["producer"]["id_scheme"] == github_receiver.ID_SCHEME
    assert report["claims"]["trace_producer_retired"] is True
    assert report["claims"]["candidate_record_only"] is True
    assert report["claims"]["otlp_delivered"] is False
    assert report["reason"] is None
    assert report["spans"] == []
    assert report["resource"]["cicd.pipeline.run.id"] == "42627302972/1"
    assert report["terminal"]["root_emission"] == "not_emitted"
    assert (
        report["terminal"]["root_emission_reason"] == "duplicate_trace_producer_retired"
    )
    assert len(report["terminal_job_records"]) == 1
    job_record = report["terminal_job_records"][0]
    assert job_record["job_id"] == "129194268575"
    assert job_record["job_result"] == "success"
    assert job_record["job_url"]
    assert job_record["trace_id"] == github_receiver.trace_id(42627302972, 1)
    assert job_record["span_id"] == github_receiver.job_span_id(129194268575)
    assert report["terminal_provenance"]["terminal_conclusion"] == "success"


def test_job_success_does_not_imply_pipeline_success():
    report = cicd_telemetry.produce(
        complete_facts(job_conclusion="success", run_conclusion="failure")
    )
    assert report["status"] == "witness_only"
    assert report["spans"] == []
    assert report["terminal_provenance"]["terminal_conclusion"] == "failure"
    assert report["terminal_job_records"][0]["job_result"] == "success"
    cicd_telemetry.attach_signal_candidates(report)
    job_log = report["signals"]["logs"][0]
    assert job_log["attributes"][cicd_telemetry.ATTR_TASK_RUN_RESULT] == "success"


def test_non_error_terminals_leave_span_status_unset():
    for conclusion in ("cancelled", "skipped"):
        report = cicd_telemetry.produce(
            complete_facts(run_conclusion=conclusion, job_conclusion=conclusion)
        )
        assert report["status"] == "witness_only"
        assert report["spans"] == []
        expected_result, error_type = cicd_telemetry.map_github_result(conclusion)
        assert error_type is None
        assert report["terminal_job_records"][0]["job_result"] == expected_result


def test_timed_out_keeps_timeout_result_and_records_error():
    report = cicd_telemetry.produce(
        complete_facts(run_conclusion="timed_out", job_conclusion="timed_out")
    )
    assert report["status"] == "witness_only"
    assert report["spans"] == []
    assert cicd_telemetry.map_github_result("timed_out") == ("timeout", "timeout")
    assert report["terminal_provenance"]["terminal_conclusion"] == "timed_out"
    assert report["terminal_job_records"][0]["job_result"] == "timeout"


def test_mixed_run_timeout_job_success_keeps_separate_terminals():
    report = cicd_telemetry.produce(
        complete_facts(run_conclusion="timed_out", job_conclusion="success")
    )
    assert report["status"] == "witness_only"
    assert report["spans"] == []
    assert report["terminal_provenance"]["terminal_conclusion"] == "timed_out"
    assert report["terminal_job_records"][0]["job_result"] == "success"
    cicd_telemetry.attach_signal_candidates(report)
    assert (
        report["signals"]["logs"][0]["attributes"][cicd_telemetry.ATTR_TASK_RUN_RESULT]
        == "success"
    )


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
    assert report["spans"] == []
    assert len(report["terminal_job_records"]) == 1
    assert report["terminal_job_records"][0]["job_result"] == "success"


def test_explicit_null_run_conclusion_does_not_use_job_success():
    facts = complete_facts(run_conclusion=None, job_conclusion="success")
    assert facts.run_conclusion is None
    assert facts.job_conclusion == "success"
    report = cicd_telemetry.produce(facts)
    assert report["status"] == "partial"
    assert report["reason"] == "missing_run_terminal_result"
    assert report["spans"] == []


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
    assert report["status"] == "partial"
    assert report["reason"] == "missing_run_terminal_result"
    assert report["spans"] == []
    assert report["terminal_job_records"] == []
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
    assert report["status"] == "partial"
    assert report["reason"] == "missing_run_timestamps"
    assert report["spans"] == []
    assert report["terminal_job_records"] == []


def test_reversed_run_timestamps_are_invalid():
    report = cicd_telemetry.produce(
        complete_facts(run_started_at_ns=RUN_END, run_completed_at_ns=RUN_START)
    )
    assert report["status"] == "partial"
    assert report["reason"] == "invalid_run_timestamps"
    assert report["spans"] == []
    assert len(report["terminal_job_records"]) == 1


def test_reversed_job_timestamps_are_invalid():
    report = cicd_telemetry.produce(
        complete_facts(job_started_at_ns=JOB_END, job_completed_at_ns=JOB_START)
    )
    assert report["status"] == "partial"
    assert report["reason"] == "invalid_job_timestamps"
    assert report["spans"] == []
    assert report["terminal_job_records"] == []
    assert report["coverage"]["tasks_omitted"] == [
        {"job_id": "129194268575", "reason": "invalid_job_timestamps"}
    ]


def test_equal_timestamps_are_valid_zero_duration():
    report = cicd_telemetry.produce(
        complete_facts(
            run_started_at_ns=RUN_START,
            run_completed_at_ns=RUN_START,
            job_started_at_ns=RUN_START,
            job_completed_at_ns=RUN_START,
        )
    )
    assert report["status"] == "witness_only"
    assert report["reason"] is None
    assert report["spans"] == []
    assert len(report["terminal_job_records"]) == 1


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
    assert report["spans"] == []
    assert len(report["terminal_job_records"]) == 1


def test_unmapped_github_conclusions_stay_unsupported():
    for value in ("neutral", "action_required", "stale"):
        assert cicd_telemetry.map_github_result(value) == (None, None)
        report = cicd_telemetry.produce(
            complete_facts(run_conclusion=value, job_conclusion=value)
        )
        assert report["status"] == "partial"
        assert report["reason"] == "unsupported_run_conclusion"
        assert report["spans"] == []
        assert report["terminal_job_records"] == []
        assert report["facts"]["run_conclusion"] == value
        assert report["facts"]["job_conclusion"] == value


def test_missing_task_url_is_not_complete_production():
    report = cicd_telemetry.produce(complete_facts(job_url=None))
    assert report["status"] == "partial"
    assert report["reason"] == "missing_task_required_identity"
    assert report["spans"] == []
    assert report["terminal_job_records"] == []


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
        assert report["status"] == "witness_only"
        assert report["spans"] == []
        assert report["terminal_provenance"]["terminal_conclusion"] == github
        assert report["terminal_job_records"][0]["job_result"] == expected
        cicd_telemetry.attach_signal_candidates(report)
        assert (
            report["signals"]["logs"][0]["attributes"][
                cicd_telemetry.ATTR_TASK_RUN_RESULT
            ]
            == expected
        )


def test_task_span_ids_differ_across_jobs_same_run():
    first = cicd_telemetry.produce(complete_facts(job_id="129194268575"))
    second = cicd_telemetry.produce(complete_facts(job_id="129194268576"))
    assert first["coverage"]["expected_trace"] == second["coverage"]["expected_trace"]
    first_job_span = first["coverage"]["expected_completed_jobs"][0][
        "expected_task_span_id"
    ]
    second_job_span = second["coverage"]["expected_completed_jobs"][0][
        "expected_task_span_id"
    ]
    assert first_job_span == github_receiver.job_span_id(129194268575)
    assert second_job_span == github_receiver.job_span_id(129194268576)
    assert first_job_span != second_job_span
    again = cicd_telemetry.produce(complete_facts(job_id="129194268575"))
    assert (
        again["coverage"]["expected_completed_jobs"][0]["expected_task_span_id"]
        == first_job_span
    )


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
            "ENGINEERING_CI_JOB_ID": "129194268575",
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
    assert report["status"] == "witness_only"
    assert report["spans"] == []
    assert report["terminal_provenance"]["terminal_conclusion"] == "failure"
    assert report["terminal_job_records"][0]["job_result"] == "success"


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
    assert payload["status"] == "witness_only"
    assert payload["spans"] == []
    assert payload["producer"]["trace_producer_retired"] is True
    assert payload["claims"]["trace_producer_retired"] is True
    assert "terminal_job_records" not in payload
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
    assert report["status"] == "witness_only"
    assert report["spans"] == []
    assert report["terminal"]["end_time_source"] is None
    assert report["terminal"]["time_source_verified"] is False
    assert report["terminal"]["root_emission"] == "not_emitted"
    assert report["terminal_provenance"]["end_time_source"] is None


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
    assert report["coverage"]["task_spans_emitted"] == 0
    assert len(report["terminal_job_records"]) == 2
    assert len(report["coverage"]["tasks_omitted"]) == 1
    assert report["coverage"]["tasks_omitted"][0]["reason"] == (
        "missing_task_required_identity"
    )
    assert "source_index" in report["coverage"]["tasks_omitted"][0]
    assert "job_id" not in report["coverage"]["tasks_omitted"][0]
    assert report["coverage"]["completed_jobs_total"] == len(
        report["terminal_job_records"]
    ) + len(report["coverage"]["tasks_omitted"])
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
    assert report["coverage"]["task_spans_emitted"] == 0
    assert len(report["terminal_job_records"]) == 1
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


def test_fetch_rejects_missing_or_illegal_total_count():
    def api_get(payload):
        def _get(_path):
            return {"jobs": [{"id": 1, "status": "completed"}], **payload}

        return _get

    for payload in (
        {},
        {"total_count": "1"},
        {"total_count": True},
        {"total_count": -1},
        {"total_count": 1.0},
    ):
        try:
            cicd_telemetry.fetch_attempt_jobs(
                api_get(payload), "repos/x/y/actions/runs/1/attempts/1/jobs"
            )
            raise AssertionError(payload)
        except cicd_telemetry.TelemetryError as error:
            assert error.code == "jobs_total_count_invalid"


def test_fetch_records_complete_page_digests():
    payload = {"total_count": 1, "jobs": [{"id": 7, "status": "completed"}]}

    def api_get(_path):
        return payload

    jobs, snapshot = cicd_telemetry.fetch_attempt_jobs(
        api_get, "repos/x/y/actions/runs/1/attempts/1/jobs"
    )
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    assert jobs[0]["id"] == 7
    assert snapshot["total_count"] == 1
    assert snapshot["completeness"] == "complete"
    assert snapshot["github_authenticated"] is False
    assert (
        snapshot["pages_sha256"][0]["sha256"] == hashlib.sha256(canonical).hexdigest()
    )
    assert snapshot["raw_pages"] == [payload]


def test_cli_api_fetch_writes_page_witness(tmp_path, monkeypatch):
    run, job = _api_run_job_fixture()
    job = {**job, "run_id": run["id"], "run_attempt": 1}
    pages = {"total_count": 1, "jobs": [job]}

    def api_get(path):
        if "/jobs" in path:
            return pages
        if "/attempts/" in path:
            return run
        raise AssertionError(path)

    monkeypatch.setattr(cicd_telemetry, "gh_api_get", api_get)
    output = tmp_path / "candidate.json"
    code = cicd_telemetry.main(
        [
            "--repository",
            "dymzz/engineering-ci",
            "--run-id",
            "42769746408",
            "--attempt",
            "1",
            "--output",
            str(output),
        ]
    )
    assert code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    witness = report["source_witness"]
    assert witness["kind"] == "github_attempt_and_job_pages"
    assert witness["completeness"] == "complete"
    assert witness["github_authenticated"] is False
    assert witness["total_count"] == 1
    raw_path = tmp_path / "candidate.json.api-pages.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    assert raw["attempt"]["id"] == run["id"]
    assert raw["job_pages"][0]["jobs"][0]["id"] == job["id"]
    body = raw_path.read_bytes()
    assert witness["sha256"] == hashlib.sha256(body).hexdigest()
    assert report["coverage"]["api_jobs_snapshot"]["github_authenticated"] is False
    assert "raw_pages" not in report["coverage"]["api_jobs_snapshot"]


def _api_attempt_get(run, pages):
    def api_get(path):
        if "/jobs" in path:
            return pages
        if "/attempts/" in path:
            return run
        raise AssertionError(path)

    return api_get


def test_witness_is_saved_before_a_rejected_export(tmp_path, monkeypatch):
    run, job = _api_run_job_fixture()
    job = {**job, "run_id": run["id"], "run_attempt": 1}
    pages = {"total_count": 1, "jobs": [job]}
    monkeypatch.setattr(cicd_telemetry, "gh_api_get", _api_attempt_get(run, pages))
    output = tmp_path / "nested" / "candidate.json"
    witness = output.with_name(output.name + ".api-pages.json")
    export = cicd_telemetry.export_otlp_grpc

    class _Partial:
        rejected_log_records = 1
        error_message = "dropped"

    class _Response:
        partial_success = _Partial()

    class _Stub:
        def __init__(self, channel):
            pass

        def Export(self, request, timeout=None):
            return _Response()

    def rejecting(report, endpoint, *, insecure=False, **kwargs):
        assert witness.is_file()
        return export(
            report,
            endpoint,
            insecure=True,
            channel_factory=lambda endpoint, insecure=False: object(),
            logs_stub_factory=_Stub,
        )

    monkeypatch.setattr(cicd_telemetry, "export_otlp_grpc", rejecting)
    code = cicd_telemetry.main(
        [
            "--repository",
            "dymzz/engineering-ci",
            "--run-id",
            str(run["id"]),
            "--attempt",
            "1",
            "--output",
            str(output),
            "--otlp-endpoint",
            "127.0.0.1:1",
            "--otlp-insecure",
        ]
    )
    assert code == 3
    assert witness.is_file()
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert (
        saved["source_witness"]["sha256"]
        == hashlib.sha256(witness.read_bytes()).hexdigest()
    )
    assert saved["source_witness"]["github_authenticated"] is False
    assert saved["claims"]["rpc_accepted"] is False
    assert saved["claims"]["otlp_logs_rpc_accepted"] is False


def test_uncreatable_output_parent_does_not_export(tmp_path, monkeypatch):
    run, job = _api_run_job_fixture()
    job = {**job, "run_id": run["id"], "run_attempt": 1}
    pages = {"total_count": 1, "jobs": [job]}
    monkeypatch.setattr(cicd_telemetry, "gh_api_get", _api_attempt_get(run, pages))
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    output = blocker / "candidate.json"
    sent = []

    def refusing(*_args, **_kwargs):
        sent.append("export")
        raise AssertionError("export started")

    monkeypatch.setattr(cicd_telemetry, "export_otlp_grpc", refusing)
    try:
        cicd_telemetry.main(
            [
                "--repository",
                "dymzz/engineering-ci",
                "--run-id",
                str(run["id"]),
                "--attempt",
                "1",
                "--output",
                str(output),
                "--otlp-endpoint",
                "127.0.0.1:1",
                "--otlp-insecure",
            ]
        )
        raise AssertionError("expected artifact_flush_failed")
    except SystemExit as error:
        assert error.code == 2
    assert sent == []
    assert not output.exists()
    assert not output.with_name(output.name + ".api-pages.json").exists()


def test_job_page_count_does_not_accept_invalid_entries():
    def fetch(jobs):
        def api_get(_path):
            return {"total_count": len(jobs), "jobs": jobs}

        try:
            cicd_telemetry.fetch_attempt_jobs(
                api_get, "repos/x/y/actions/runs/1/attempts/1/jobs"
            )
            raise AssertionError(jobs)
        except cicd_telemetry.TelemetryError as error:
            assert error.code == "jobs_payload_invalid"

    fetch([{"id": 1, "status": "completed"}, None])
    fetch([{"id": 1, "status": "completed"}, "job"])
    fetch([{"id": 1}])
    fetch([{"id": 1, "status": ""}])
    fetch([{"id": 1, "status": "not-a-status"}])

    run, job = _api_run_job_fixture()
    run["run_attempt"] = 1
    jobs = [
        {
            **job,
            "id": 11,
            "status": "completed",
            "run_id": run["id"],
            "run_attempt": 1,
        },
        {
            **job,
            "id": 12,
            "name": "still-queued",
            "status": "queued",
            "conclusion": None,
            "completed_at": None,
            "run_id": run["id"],
            "run_attempt": 1,
        },
    ]
    _facts, coverage = cicd_telemetry.fetch_github_run_job(
        "dymzz/engineering-ci",
        run["id"],
        attempt=1,
        api_get=_api_attempt_get(run, {"total_count": 2, "jobs": jobs}),
    )
    assert coverage["jobs_observed_total"] == 2
    assert coverage["completed_jobs_total"] == 1
    assert coverage["noncompleted_jobs_total"] == 1
    assert coverage["api_jobs_snapshot"]["completeness"] == "complete"


def test_missing_job_id_still_checks_provided_membership():
    run, job = _api_run_job_fixture()
    run["run_attempt"] = 1

    def fetch(jobs, *, job_id=None):
        def api_get(path):
            if "/jobs" in path:
                return {"total_count": len(jobs), "jobs": jobs}
            if "/attempts/" in path:
                return run
            raise AssertionError(path)

        return cicd_telemetry.fetch_github_run_job(
            "dymzz/engineering-ci",
            run["id"],
            job_id=job_id,
            attempt=1,
            api_get=api_get,
        )

    def expect(jobs, code, *, job_id=None):
        try:
            fetch(jobs, job_id=job_id)
            raise AssertionError(code)
        except cicd_telemetry.TelemetryError as error:
            assert error.code == code

    missing = {
        **job,
        "id": None,
        "name": "no-id",
        "run_id": run["id"],
        "run_attempt": 1,
        "html_url": None,
    }
    _facts, coverage = fetch([missing])
    assert coverage["completed_jobs_total"] == 1
    assert coverage["jobs_observed_total"] == 1
    produced = cicd_telemetry.produce(
        _facts,
        source="github_api",
        github_api_fetched=True,
        completed_jobs_total=coverage["completed_jobs_total"],
        job_selection=coverage["job_selection"],
        task_facts=coverage["task_facts"],
        jobs_observed_total=coverage["jobs_observed_total"],
    )
    assert produced["coverage"]["tasks_omitted"][0]["reason"] == (
        "missing_task_required_identity"
    )
    assert produced["coverage"]["completed_jobs_total"] == 1

    expect([{**missing, "run_id": run["id"] + 1}], "job_run_mismatch")
    expect([{**missing, "run_attempt": 2}], "job_attempt_mismatch")

    absent = {
        key: value
        for key, value in missing.items()
        if key not in {"run_id", "run_attempt"}
    }
    _facts, coverage = fetch([absent])
    assert coverage["completed_jobs_total"] == 1

    expect([{**missing, "run_id": "not-a-run"}], "job_membership_invalid")
    expect([{**missing, "run_attempt": True}], "job_membership_invalid")

    good = {**job, "id": 101, "run_id": run["id"], "run_attempt": 1}
    sibling = {**missing, "run_attempt": 9, "status": "in_progress"}
    expect([good, sibling], "job_attempt_mismatch", job_id=101)


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
    assert report["otlp_json"] is None
    assert report["claims"]["otlp_json_prepared"] is False
    assert report["claims"]["otlp_json_exported"] is False
    assert report["claims"]["trace_producer_retired"] is True
    assert report["schema_version"] == cicd_telemetry.SCHEMA_VERSION

    with pytest.raises(cicd_telemetry.TelemetryError) as to_error:
        cicd_telemetry.to_otlp_json(report)
    assert to_error.value.code == "duplicate_trace_producer_retired"

    out = tmp_path / "otel.json"
    with pytest.raises(cicd_telemetry.TelemetryError) as write_error:
        cicd_telemetry.write_otlp_json(report, out)
    assert write_error.value.code == "duplicate_trace_producer_retired"
    assert not out.exists()

    # External githubreceiver OTLP/JSON payloads still decode via official proto schema.
    _attach_external_githubreceiver_traces(report)
    decoded = cicd_telemetry.decode_otlp_export_request(report["otlp_json"])
    assert len(decoded.resource_spans) == 1
    resource_attrs = {
        item.key: item.value.string_value
        for item in decoded.resource_spans[0].resource.attributes
    }
    assert resource_attrs["cicd.pipeline.run.id"] == "42627302972/1"
    proto_scope = decoded.resource_spans[0].scope_spans[0].scope
    assert proto_scope.name == "githubreceiver"
    spans = decoded.resource_spans[0].scope_spans[0].spans
    assert len(spans) == 2
    assert spans[0].trace_id == bytes.fromhex(github_receiver.trace_id(42627302972, 1))
    assert spans[0].span_id == bytes.fromhex(
        github_receiver.root_span_id(42627302972, 1)
    )
    assert spans[1].span_id == bytes.fromhex(github_receiver.job_span_id(129194268575))

    # Companion log signal still enforces strict flush failure semantics.
    cicd_telemetry.attach_signal_candidates(report)
    file_parent = tmp_path / "not-a-dir"
    file_parent.write_text("x", encoding="utf-8")
    bad_logs = file_parent / "logs.json"
    with pytest.raises(cicd_telemetry.TelemetryError) as logs_error:
        cicd_telemetry.write_otlp_logs_json(report, bad_logs)
    assert logs_error.value.code == "otlp_logs_flush_failed"
    assert report["claims"]["otlp_logs_exported"] is False


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
    assert payload["status"] == "witness_only"
    assert payload["otlp_json_prepared"] is False
    assert payload["otlp_json_exported"] is False
    assert payload["claims"]["otlp_json_prepared"] is False
    assert payload["claims"]["otlp_json_exported"] is False
    assert payload["claims"]["trace_producer_retired"] is True
    assert payload["claims"]["otlp_logs_prepared"] is True


def test_cli_otlp_json_flush_failure_exits_3(tmp_path, capsys):
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
    with pytest.raises(SystemExit) as retired:
        cicd_telemetry.main(
            [
                "--facts",
                str(facts_path),
                "--output",
                str(output),
                "--otlp-json",
                str(file_parent / "otel.json"),
            ]
        )
    assert retired.value.code == 2
    assert (
        capsys.readouterr().err
        == "cicd-telemetry rejected: duplicate_trace_producer_retired\n"
    )
    assert not output.exists()

    code = cicd_telemetry.main(
        [
            "--facts",
            str(facts_path),
            "--output",
            str(output),
            "--otlp-logs-json",
            str(file_parent / "otel.logs.json"),
        ]
    )
    assert code == 3
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["claims"]["otlp_logs_prepared"] is True
    assert payload["claims"]["otlp_logs_exported"] is False


def test_cli_otlp_unavailable_for_incomplete_exits_3(tmp_path):
    facts_path = tmp_path / "facts.json"
    facts_path.write_text(json.dumps({"workflow_name": "x"}), encoding="utf-8")
    code = cicd_telemetry.main(
        [
            "--facts",
            str(facts_path),
            "--output",
            str(tmp_path / "out.json"),
            "--otlp-logs-json",
            str(tmp_path / "otel.logs.json"),
        ]
    )
    assert code == 3
    assert not (tmp_path / "otel.logs.json").exists()
    payload = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert payload["otlp_json_exported"] is False
    assert payload["claims"]["otlp_logs_exported"] is False


def test_observed_upper_bound_without_budget_keeps_tasks_and_withholds_root():
    facts = complete_facts(run_completed_at_ns=None, run_status="completed")
    observed = JOB_END + 5_000_000_000
    with pytest.raises(
        cicd_telemetry.TelemetryError, match="^duplicate_trace_producer_retired$"
    ):
        cicd_telemetry.produce(
            facts,
            end_time_mode=cicd_telemetry.END_TIME_OBSERVED_UPPER_BOUND,
            terminal_observed_at_ns=observed,
            terminal_status="completed",
        )

    report = cicd_telemetry.produce(
        facts,
        source="github_api",
        github_api_fetched=True,
        end_time_mode=cicd_telemetry.END_TIME_WITNESS,
        terminal_observed_at_ns=observed,
        terminal_status="completed",
    )
    assert report["status"] == "witness_only"
    assert report["reason"] is None
    assert report["terminal"]["terminal_confirmed"] is True
    assert report["terminal"]["root_emission"] == "not_emitted"
    assert (
        report["terminal"]["root_emission_reason"] == "duplicate_trace_producer_retired"
    )
    assert report["terminal"]["time_precision"] == "unproven"
    assert report["terminal"]["exact_run_duration"] is False
    assert report["terminal"]["duration_valid_for_metrics"] is False
    assert report["terminal"]["time_source_verified"] is False
    assert report["terminal"]["bounds_valid"] is True
    assert report["terminal"]["end_lower_bound_ns"] == JOB_END
    assert report["terminal"]["end_upper_bound_ns"] == observed
    assert report["terminal"]["uncertainty_ns"] == 5_000_000_000
    assert report["terminal"]["end_time_source"] is None
    assert report["coverage"]["pipeline_terminal_covered"] is False
    assert report["coverage"]["task_spans_emitted"] == 0
    assert report["spans"] == []
    assert len(report["terminal_job_records"]) == 1
    assert report["terminal_job_records"][0]["span_id"] == github_receiver.job_span_id(
        129194268575
    )
    withheld = report["coverage"]["withheld_parent_span"]
    assert withheld["exported"] is False
    assert withheld["span_id"] == github_receiver.root_span_id(42627302972, 1)
    envelope = report["coverage"]["job_execution_envelope"]
    assert envelope["quality"] == "job_execution_envelope"
    assert envelope["semconv_phase"] is None


def test_approximate_root_requires_an_uncertainty_budget():
    facts = complete_facts(run_completed_at_ns=None, run_status="completed")
    observed = JOB_END + 5_000_000_000
    with pytest.raises(
        cicd_telemetry.TelemetryError, match="^duplicate_trace_producer_retired$"
    ):
        cicd_telemetry.produce(
            facts,
            end_time_mode=cicd_telemetry.END_TIME_OBSERVED_UPPER_BOUND,
            terminal_observed_at_ns=observed,
            max_end_uncertainty_ns=5_000_000_000,
        )
    with pytest.raises(
        cicd_telemetry.TelemetryError, match="^duplicate_trace_producer_retired$"
    ):
        cicd_telemetry.produce(
            facts,
            end_time_mode=cicd_telemetry.END_TIME_WITNESS,
            terminal_observed_at_ns=observed,
            max_end_uncertainty_ns=5_000_000_000,
        )


def test_observed_upper_bound_rejects_observation_before_jobs():
    facts = complete_facts(run_completed_at_ns=None, run_status="completed")
    report = cicd_telemetry.produce(
        facts,
        source="github_api",
        github_api_fetched=True,
        end_time_mode=cicd_telemetry.END_TIME_WITNESS,
        terminal_observed_at_ns=JOB_END - 1,
        terminal_status="completed",
    )
    assert report["coverage"]["pipeline_terminal_covered"] is False
    assert report["reason"] == "terminal_observed_before_jobs"
    assert report["terminal"]["bounds_invalid_reason"] == "end_bounds_unverified"
    assert report["terminal"]["clock_quality"] == "rejected"
    assert report["spans"] == []


def test_observed_attempt_without_jobs_records_a_weak_lower_bound():
    start = 1_700_000_000_000_000_000
    observed = start + 2_000_000_000
    facts = complete_facts(
        run_started_at_ns=start,
        run_completed_at_ns=None,
        run_status="completed",
        job_id=None,
        job_name=None,
        job_url=None,
        job_conclusion=None,
        job_started_at_ns=None,
        job_completed_at_ns=None,
    )
    withheld = cicd_telemetry.produce(
        facts,
        source="github_api",
        github_api_fetched=True,
        end_time_mode=cicd_telemetry.END_TIME_WITNESS,
        terminal_observed_at_ns=observed,
        completed_jobs_total=0,
        task_facts=[],
    )
    assert withheld["terminal"]["root_emission"] == "not_emitted"
    assert withheld["terminal"]["lower_source"] == "weak_run_start"
    assert withheld["terminal"]["end_lower_bound_ns"] == start
    assert withheld["terminal"]["uncertainty_ns"] == 2_000_000_000
    assert withheld["coverage"]["terminal_check"] == "not_verified"
    assert withheld["spans"] == []
    with pytest.raises(
        cicd_telemetry.TelemetryError, match="^duplicate_trace_producer_retired$"
    ):
        cicd_telemetry.produce(
            facts,
            end_time_mode=cicd_telemetry.END_TIME_OBSERVED_UPPER_BOUND,
            terminal_observed_at_ns=observed,
            completed_jobs_total=0,
            task_facts=[],
            max_end_uncertainty_ns=2_000_000_000,
        )


def test_pipeline_only_root_when_no_task_obligation():
    facts = complete_facts(
        job_id=None,
        job_name=None,
        job_url=None,
        job_conclusion=None,
        job_started_at_ns=None,
        job_completed_at_ns=None,
    )
    report = cicd_telemetry.produce(facts, completed_jobs_total=0, task_facts=[])
    assert report["coverage"]["pipeline_terminal_covered"] is False
    assert report["coverage"]["task_coverage"] == "empty"
    assert report["status"] == "witness_only"
    assert report["spans"] == []
    assert (
        report["terminal"]["root_emission_reason"] == "duplicate_trace_producer_retired"
    )


def test_export_otlp_grpc_sets_rpc_accepted_without_delivered():
    report = cicd_telemetry.produce(complete_facts())
    _attach_external_githubreceiver_traces(report)

    class _Response:
        partial_success = None

    class _Stub:
        def __init__(self, channel):
            self.channel = channel

        def Export(self, request, timeout=None):
            assert request.resource_spans
            return _Response()

    cicd_telemetry.export_otlp_grpc(
        report,
        "localhost:4317",
        insecure=True,
        channel_factory=lambda endpoint, insecure=False: object(),
        stub_factory=_Stub,
    )
    assert report["claims"]["rpc_accepted"] is True
    assert report["claims"]["otlp_delivered"] is False
    assert report["claims"]["receiver_observed"] is False
    assert report["claims"]["weaver_pass"] is False
    assert report["claims"]["trusted"] is False


def test_export_otlp_grpc_partial_reject_fails():
    report = cicd_telemetry.produce(complete_facts())
    _attach_external_githubreceiver_traces(report)

    class _Partial:
        rejected_spans = 2
        error_message = "dropped"

    class _Response:
        partial_success = _Partial()

    class _Stub:
        def __init__(self, channel):
            pass

        def Export(self, request, timeout=None):
            return _Response()

    try:
        cicd_telemetry.export_otlp_grpc(
            report,
            "localhost:4317",
            insecure=True,
            channel_factory=lambda endpoint, insecure=False: object(),
            stub_factory=_Stub,
        )
        raise AssertionError("expected otlp_grpc_partial_reject")
    except cicd_telemetry.TelemetryError as error:
        assert error.code == "otlp_grpc_partial_reject"
    assert report["claims"]["rpc_accepted"] is False


def test_observed_root_requires_api_completed_status():
    facts = complete_facts(
        run_completed_at_ns=None,
        run_status="in_progress",
        run_conclusion="success",
    )
    report = cicd_telemetry.produce(
        facts,
        source="github_api",
        github_api_fetched=True,
        end_time_mode=cicd_telemetry.END_TIME_WITNESS,
        terminal_observed_at_ns=JOB_END + 5_000_000_000,
        terminal_status="completed",
    )
    assert report["reason"] == "run_not_completed"
    assert report["coverage"]["pipeline_terminal_covered"] is False


def test_event_api_status_conflict_blocks_observed_root():
    facts = complete_facts(run_completed_at_ns=None, run_status="completed")
    report = cicd_telemetry.produce(
        facts,
        source="github_api",
        github_api_fetched=True,
        end_time_mode=cicd_telemetry.END_TIME_WITNESS,
        terminal_observed_at_ns=JOB_END + 5_000_000_000,
        event_status="in_progress",
    )
    assert report["reason"] == "event_api_status_conflict"


def test_expected_completed_jobs_inventory_tracks_omission():
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
    stubs = [
        {"source_index": 0, "job_id": "1", "job_url": "https://example.test/1"},
        {"source_index": 1, "job_id": "2", "job_url": None},
        {"source_index": 2, "job_id": "3", "job_url": "https://example.test/3"},
    ]
    report = cicd_telemetry.produce(
        run_facts,
        task_facts=[(0, good), (1, bad)],
        completed_jobs_total=3,
        job_selection="job_id",
        expected_job_stubs=stubs,
    )
    expected = report["coverage"]["expected_completed_jobs"]
    assert len(expected) == 3
    assert expected[0]["emitted"] is False
    assert expected[0]["omitted"] is False
    assert expected[0]["expected_task_span_id"] == github_receiver.job_span_id(1)
    assert expected[1]["omitted"] is True
    assert expected[2]["omit_reason"] == "not_selected_job_id_slice"
    provenance = report["terminal_provenance"]
    assert provenance["end_time_quality"] == cicd_telemetry.TIME_PRECISION_UNPROVEN
    assert provenance["terminal_source"] == "mapping"
    assert provenance["end_time_source"] is None
    assert provenance["root_emitted"] is False
    assert provenance["time_source_verified"] is False
    assert provenance["time_precision"] == "unproven"
    assert report["spans"] == []


def test_api_observed_root_does_not_claim_native_completed_at():
    facts = complete_facts(run_completed_at_ns=None, run_status="completed")
    report = cicd_telemetry.produce(
        facts,
        source="github_api",
        github_api_fetched=True,
        end_time_mode=cicd_telemetry.END_TIME_WITNESS,
        terminal_observed_at_ns=JOB_END + 5_000_000_000,
    )
    provenance = report["terminal_provenance"]
    assert provenance["terminal_source"] == "github_api"
    assert provenance["root_emitted"] is False
    assert provenance["root_emission"] == "not_emitted"
    assert provenance["end_time_source"] is None
    assert provenance["time_precision"] == "unproven"
    assert provenance["time_source_verified"] is False
    assert provenance["exact_run_duration"] is False
    assert report["facts"]["run_completed_at_ns"] is None
    assert report["coverage"]["task_spans_emitted"] == 0
    assert len(report["terminal_job_records"]) == 1


def test_env_explicit_end_is_not_labeled_as_github_api():
    report = cicd_telemetry.produce(complete_facts(), source="env")
    provenance = report["terminal_provenance"]
    assert provenance["terminal_source"] == "env"
    assert provenance["end_time_source"] is None
    assert provenance["root_emitted"] is False
    assert provenance["time_source_verified"] is False


def test_duration_metric_requires_phase_interval():
    report = cicd_telemetry.produce(complete_facts())
    cicd_telemetry.attach_signal_candidates(report)
    assert report["signals"]["metrics"] == []
    assert report["otlp_metrics_json"] is None
    states = {
        item["state"]
        for item in report["signals"]["metrics_not_observable"]
        if item["name"] == "cicd.pipeline.run.duration"
    }
    assert states == {"pending", "executing", "finalizing"}
    assert all(
        item["reason"] == "phase_interval_unproven"
        for item in report["signals"]["metrics_not_observable"]
        if item["name"] == "cicd.pipeline.run.duration"
    )


def test_executing_histogram_omits_run_id_unless_opt_in():
    interval = {
        "state": "executing",
        "start_ns": JOB_START,
        "end_ns": JOB_END,
    }
    report = cicd_telemetry.produce(complete_facts(), phase_intervals=[interval])
    cicd_telemetry.attach_signal_candidates(report)
    metric = report["signals"]["metrics"][0]
    assert metric["name"] == "cicd.pipeline.run.duration"
    assert "gauge" not in metric
    point = metric["histogram"]["data_points"][0]
    assert point["attributes"]["cicd.pipeline.run.state"] == "executing"
    assert "cicd.pipeline.run.id" not in point["attributes"]
    assert point["sum"] == 80.0
    assert len(point["bucket_counts"]) == len(point["explicit_bounds"]) + 1
    resource_keys = [
        item["key"]
        for item in report["otlp_metrics_json"]["resourceMetrics"][0]["resource"][
            "attributes"
        ]
    ]
    assert "cicd.pipeline.run.id" not in resource_keys
    assert "github.actions.run_attempt" not in resource_keys
    assert "vcs.ref.head.revision" not in resource_keys
    decoded = cicd_telemetry.decode_otlp_metrics_request(report["otlp_metrics_json"])
    assert report["otlp_metrics_json"]["resourceMetrics"][0]["scopeMetrics"][0][
        "scope"
    ] == {"name": "eci_core.cicd_telemetry"}
    assert decoded.resource_metrics[0].scope_metrics[0].scope.version == ""
    logs = report["otlp_logs_json"]
    assert logs["resourceLogs"][0]["scopeLogs"][0]["scope"] == {
        "name": "eci_core.cicd_telemetry"
    }
    decoded_logs = cicd_telemetry.decode_otlp_logs_request(logs)
    assert decoded_logs.resource_logs[0].scope_logs[0].scope.version == ""
    histogram = decoded.resource_metrics[0].scope_metrics[0].metrics[0].histogram
    assert histogram.aggregation_temporality == 1
    assert histogram.data_points[0].count == 1
    assert histogram.data_points[0].sum == 80.0

    opted = cicd_telemetry.produce(
        complete_facts(),
        phase_intervals=[interval],
        include_run_entity_metrics=True,
    )
    cicd_telemetry.attach_signal_candidates(opted)
    opted_point = opted["signals"]["metrics"][0]["histogram"]["data_points"][0]
    assert opted_point["attributes"]["cicd.pipeline.run.id"] == "42627302972/1"


def test_invalid_phase_interval_is_not_emitted():
    report = cicd_telemetry.produce(
        complete_facts(),
        phase_intervals=[
            {"state": "finalizing", "start_ns": JOB_END, "end_ns": JOB_START}
        ],
    )
    cicd_telemetry.attach_signal_candidates(report)
    assert report["signals"]["metrics"] == []
    finalizing = next(
        item
        for item in report["signals"]["metrics_not_observable"]
        if item.get("state") == "finalizing"
    )
    assert finalizing["reason"] == "invalid_phase_interval"


def test_receiver_mark_rejects_unaccepted_rpc_and_keeps_candidate_class():
    report = cicd_telemetry.produce(complete_facts())
    spans = _attach_external_githubreceiver_traces(report)
    span = spans[0]
    try:
        cicd_telemetry.mark_receiver_observed(
            report,
            signal="traces",
            observed_ids=[item["span_id"] for item in spans],
            rpc_accepted=False,
            rejected=0,
            run_id=report["facts"]["run_id"],
            attempt=report["facts"]["run_attempt"],
            trace_id=span["trace_id"],
        )
        raise AssertionError("expected otlp_receiver_rpc_unaccepted")
    except cicd_telemetry.TelemetryError as error:
        assert error.code == "otlp_receiver_rpc_unaccepted"
    assert report["claims"]["otlp_delivered"] is False
    assert report["claims"]["candidate_record_only"] is True
    assert report["claims"]["snapshot_phase"] == "before_receiver_confirmation"


def _trace_receipt(report, spans, **overrides):
    values = {
        "signal": "traces",
        "observed_ids": [item["span_id"] for item in spans],
        "rpc_accepted": True,
        "rejected": 0,
        "run_id": report["facts"]["run_id"],
        "attempt": report["facts"]["run_attempt"],
        "trace_id": spans[0]["trace_id"],
        "repository": report["facts"]["repository"],
    }
    values.update(overrides)
    return values


def test_receiver_mark_checks_set_identity_and_duplicates():
    report = cicd_telemetry.produce(complete_facts())
    spans = _attach_external_githubreceiver_traces(report)
    report["claims"]["rpc_accepted"] = True
    ids = [item["span_id"] for item in spans]
    try:
        cicd_telemetry.mark_receiver_observed(
            report, **_trace_receipt(report, spans, observed_ids=ids[:1])
        )
        raise AssertionError("expected missing span")
    except cicd_telemetry.TelemetryError as error:
        assert error.code == "otlp_receiver_incomplete"
    try:
        cicd_telemetry.mark_receiver_observed(
            report, **_trace_receipt(report, spans, observed_ids=[*ids, ids[0]])
        )
        raise AssertionError("expected duplicate")
    except cicd_telemetry.TelemetryError as error:
        assert error.code == "otlp_receiver_duplicate"
    try:
        cicd_telemetry.mark_receiver_observed(
            report, **_trace_receipt(report, spans, attempt=9)
        )
        raise AssertionError("expected identity mismatch")
    except cicd_telemetry.TelemetryError as error:
        assert error.code == "otlp_receiver_identity_mismatch"
    try:
        cicd_telemetry.mark_receiver_observed(
            report, **_trace_receipt(report, spans, trace_id="ab" * 16)
        )
        raise AssertionError("expected trace mismatch")
    except cicd_telemetry.TelemetryError as error:
        assert error.code == "otlp_receiver_trace_mismatch"
    cicd_telemetry.mark_receiver_observed(report, **_trace_receipt(report, spans))
    assert report["claims"]["receiver_observed"] is True
    assert report["claims"]["otlp_delivered"] is True
    assert report["claims"]["candidate_record_only"] is True
    assert report["claims"]["trusted"] is False
    assert report["claims"]["weaver_pass"] is False
    assert report["claims"]["snapshot_phase"] == "receiver_confirmed"
    transport = report["claims"]["transport"]["traces"]
    assert transport["missing"] == []
    assert transport["unexpected"] == []
    assert transport["duplicate"] == []


def test_late_observation_does_not_become_a_proven_root_duration():
    start = cicd_telemetry.parse_time_ns("2026-11-10T13:06:02Z")
    job_end = cicd_telemetry.parse_time_ns("2026-11-10T13:07:59Z")
    observed = cicd_telemetry.parse_time_ns("2026-11-11T11:37:15.574469Z")
    run = complete_facts(
        run_started_at_ns=start,
        run_completed_at_ns=None,
        run_status="completed",
        job_id=None,
        job_name=None,
        job_url=None,
        job_conclusion=None,
        job_started_at_ns=None,
        job_completed_at_ns=None,
    )
    jobs = []
    for index, offset in enumerate((30, 60, 117)):
        jobs.append(
            complete_facts(
                run_started_at_ns=start,
                run_completed_at_ns=None,
                run_status="completed",
                job_id=str(index + 1),
                job_name=f"job-{index}",
                job_url=f"https://example.test/job/{index}",
                job_started_at_ns=start + offset * 1_000_000_000,
                job_completed_at_ns=job_end if index == 2 else job_end - 20_000_000_000,
            )
        )
    with pytest.raises(
        cicd_telemetry.TelemetryError, match="^duplicate_trace_producer_retired$"
    ):
        cicd_telemetry.produce(
            run,
            source="github_api",
            github_api_fetched=True,
            end_time_mode=cicd_telemetry.END_TIME_OBSERVED_UPPER_BOUND,
            terminal_observed_at_ns=observed,
            completed_jobs_total=3,
            job_selection="all_completed",
            task_facts=jobs,
            max_end_uncertainty_ns=10 * 60 * 1_000_000_000,
        )

    report = cicd_telemetry.produce(
        run,
        source="github_api",
        github_api_fetched=True,
        end_time_mode=cicd_telemetry.END_TIME_WITNESS,
        terminal_observed_at_ns=observed,
        completed_jobs_total=3,
        job_selection="all_completed",
        task_facts=jobs,
    )
    cicd_telemetry.attach_signal_candidates(report)
    assert report["terminal"]["root_emission"] == "not_emitted"
    assert (
        report["terminal"]["root_emission_reason"] == "duplicate_trace_producer_retired"
    )
    assert report["terminal"]["job_set_complete"] is True
    assert report["terminal"]["clock_quality"] == "ordered"
    assert report["terminal"]["lower_source"] == "last_completed_job_end"
    assert report["terminal"]["end_lower_bound_ns"] == job_end
    assert report["terminal"]["end_upper_bound_ns"] == observed
    assert report["coverage"]["pipeline_terminal_covered"] is False
    assert report["coverage"]["task_spans_emitted"] == 0
    assert len(report["terminal_job_records"]) == 3
    assert report["coverage"]["terminal_check"] == "not_verified"
    assert report["claims"]["weaver_pass"] is False
    assert report["otlp_metrics_json"] is None
    assert report["spans"] == []
    sources = [item["source"] for item in report["signals"]["logs"]]
    assert sources.count("github_job_terminal") == 3
    assert sources.count("github_attempt_terminal") == 1
    attempt_log = next(
        item
        for item in report["signals"]["logs"]
        if item["source"] == "github_attempt_terminal"
    )
    assert attempt_log["time_unix_nano"] == observed
    assert attempt_log["terminal_provenance"]["time_role"] == "terminal_observed_at"
    assert (
        attempt_log["span_id"] == report["coverage"]["withheld_parent_span"]["span_id"]
    )
    assert report["coverage"]["withheld_parent_span"]["exported"] is False


def test_github_api_does_not_promote_job_envelope_to_phase_histogram():
    report = cicd_telemetry.produce(
        complete_facts(),
        source="github_api",
        github_api_fetched=True,
        phase_intervals=[
            {"state": "executing", "start_ns": JOB_START, "end_ns": JOB_END}
        ],
    )
    cicd_telemetry.attach_signal_candidates(report)
    assert report["signals"]["metrics"] == []
    assert any(
        item["reason"] == "phase_source_not_native"
        for item in report["signals"]["metrics_not_observable"]
    )
    envelope = report["coverage"]["job_execution_envelope"]
    assert envelope["start_ns"] == JOB_START
    assert envelope["end_ns"] == JOB_END
    assert envelope["semconv_phase"] is None


def test_trace_delivery_survives_a_rejected_log_receipt():
    report = cicd_telemetry.produce(complete_facts())
    cicd_telemetry.attach_signal_candidates(report)
    spans = _attach_external_githubreceiver_traces(report)
    report["claims"]["rpc_accepted"] = True
    report["claims"]["otlp_logs_rpc_accepted"] = True
    facts = report["facts"]
    cicd_telemetry.mark_receiver_observed(
        report,
        signal="traces",
        observed_ids=[item["span_id"] for item in spans],
        rpc_accepted=True,
        run_id=facts["run_id"],
        attempt=facts["run_attempt"],
        trace_id=spans[0]["trace_id"],
        repository=facts["repository"],
    )
    try:
        cicd_telemetry.mark_receiver_observed(
            report,
            signal="logs",
            observed_ids=[],
            rpc_accepted=True,
            run_id=facts["run_id"],
            attempt=facts["run_attempt"],
            trace_id=spans[0]["trace_id"],
            repository=facts["repository"],
        )
        raise AssertionError("expected incomplete logs")
    except cicd_telemetry.TelemetryError as error:
        assert error.code == "otlp_receiver_incomplete"
    assert report["claims"]["transport"]["traces"]["otlp_delivered"] is True
    assert report["claims"]["otlp_delivered"] is False
    assert report["claims"]["bundle_delivery_complete"] is False
    assert report["claims"]["receiver_observed"] is False
    assert report["claims"]["otlp_logs_delivered"] is False
    assert report["claims"]["all_prepared_signals_delivered"] is False
    assert report["claims"]["snapshot_phase"] == "partial_receiver_confirmation"


def _three_job_witness():
    start = cicd_telemetry.parse_time_ns("2026-11-10T13:06:02Z")
    job_end = cicd_telemetry.parse_time_ns("2026-11-10T13:07:59Z")
    observed = cicd_telemetry.parse_time_ns("2026-11-11T11:37:15.574469Z")
    run = complete_facts(
        run_id="42821429094",
        run_attempt=1,
        run_started_at_ns=start,
        run_completed_at_ns=None,
        run_status="completed",
        job_id=None,
        job_name=None,
        job_url=None,
        job_conclusion=None,
        job_started_at_ns=None,
        job_completed_at_ns=None,
    )
    jobs = []
    for index in range(3):
        jobs.append(
            complete_facts(
                run_id="42821429094",
                run_attempt=1,
                run_started_at_ns=start,
                run_completed_at_ns=None,
                run_status="completed",
                job_id=str(129847643854 + index),
                job_name=f"job-{index}",
                job_url=f"https://example.test/job/{index}",
                job_started_at_ns=start,
                job_completed_at_ns=job_end,
            )
        )
    report = cicd_telemetry.produce(
        run,
        source="github_api",
        github_api_fetched=True,
        end_time_mode=cicd_telemetry.END_TIME_WITNESS,
        terminal_observed_at_ns=observed,
        completed_jobs_total=3,
        job_selection="all_completed",
        task_facts=jobs,
    )
    return report, [job.job_id for job in jobs]


def test_expected_trace_is_rebuilt_from_the_run_attempt_job_witness():
    report, job_ids = _three_job_witness()
    expected = report["coverage"]["expected_trace"]
    trace_id = github_receiver.trace_id(42821429094, 1)
    pipeline_span_id = github_receiver.root_span_id(42821429094, 1)
    assert report["producer"]["id_scheme"] == cicd_telemetry.ID_SCHEME
    assert expected["id_scheme"] == cicd_telemetry.ID_SCHEME
    assert expected["trusted"] is False
    assert expected["expected_trace_id"] == trace_id
    assert expected["expected_pipeline_span_id"] == pipeline_span_id
    assert expected["parent_exported"] is False
    withheld = report["coverage"]["withheld_parent_span"]
    assert withheld["span_id"] == pipeline_span_id
    assert withheld["exported"] is False
    assert report["spans"] == []
    witness_urls = {
        job_id: f"https://example.test/job/{index}"
        for index, job_id in enumerate(job_ids)
    }
    external_spans = _external_githubreceiver_spans(
        "42821429094", 1, job_ids, job_urls=witness_urls
    )
    tasks = [
        span
        for span in external_spans
        if span["kind"] == cicd_telemetry.SPAN_KIND_INTERNAL
    ]
    assert len(tasks) == 3
    for job_id, span in zip(job_ids, tasks, strict=True):
        task_span_id = github_receiver.job_span_id(int(job_id))
        assert span["span_id"] == task_span_id
        assert span["trace_id"] == trace_id
        assert span["parent_span_id"] == pipeline_span_id
    assert len(report["coverage"]["expected_completed_jobs"]) == 3
    for row in report["coverage"]["expected_completed_jobs"]:
        assert row["emitted"] is False
        assert row["omitted"] is False
        assert row["omit_reason"] is None
        assert row["job_url"] == witness_urls[row["job_id"]]
        span = next(
            item for item in tasks if item["span_id"] == row["expected_task_span_id"]
        )
        assert span["attributes"][cicd_telemetry.ATTR_TASK_RUN_ID] == row["job_id"]
        assert (
            span["attributes"][cicd_telemetry.ATTR_TASK_RUN_URL]
            == (witness_urls[row["job_id"]])
        )
    matched = cicd_telemetry.reconcile_expected_trace(
        run_id="42821429094",
        attempt=1,
        job_ids=job_ids,
        observed_spans=tasks,
    )
    assert matched["ok"] is True
    assert matched["trusted"] is False
    assert matched["verification_scope"] == "task_trace_identity"
    assert matched["source_completeness_verified"] is False
    assert matched["job_attributes_verified"] is False


def test_expected_trace_reconciliation_rejects_capture_substitutes():
    _report, job_ids = _three_job_witness()
    tasks = [
        dict(span)
        for span in _external_githubreceiver_spans("42821429094", 1, job_ids)
        if span["kind"] == cicd_telemetry.SPAN_KIND_INTERNAL
    ]
    wrong_trace = ["ab" * 16 for _ in tasks]
    for span, trace_id in zip(tasks, wrong_trace, strict=True):
        span["trace_id"] = trace_id
    rejected = cicd_telemetry.reconcile_expected_trace(
        run_id="42821429094",
        attempt=1,
        job_ids=job_ids,
        observed_spans=tasks,
    )
    assert rejected["ok"] is False
    assert len(rejected["trace_mismatch"]) == 3

    tasks[0]["trace_id"] = rejected["expected_trace_id"]
    tasks[1]["trace_id"] = rejected["expected_trace_id"]
    tasks[2]["trace_id"] = rejected["expected_trace_id"]
    tasks[1]["span_id"] = "0123456789abcdef"
    one_wrong = cicd_telemetry.reconcile_expected_trace(
        run_id="42821429094",
        attempt=1,
        job_ids=job_ids,
        observed_spans=tasks,
    )
    assert one_wrong["ok"] is False
    assert one_wrong["missing"]
    assert "0123456789abcdef" in one_wrong["unexpected"]

    original = [
        span
        for span in _external_githubreceiver_spans("42821429094", 1, job_ids)
        if span["kind"] == cicd_telemetry.SPAN_KIND_INTERNAL
    ]
    wrong_attempt = cicd_telemetry.reconcile_expected_trace(
        run_id="42821429094",
        attempt=2,
        job_ids=job_ids,
        observed_spans=original,
    )
    assert wrong_attempt["ok"] is False
    assert wrong_attempt["expected_trace_id"] != original[0]["trace_id"]
    assert wrong_attempt["trace_mismatch"]
    assert wrong_attempt["parent_mismatch"]
    assert wrong_attempt["source_completeness_verified"] is False


def test_reconcile_rejects_duplicate_and_illegal_job_ids():
    _report, job_ids = _three_job_witness()
    tasks = [
        {**span, "attributes": dict(span.get("attributes") or {})}
        for span in _external_githubreceiver_spans("42821429094", 1, job_ids)
        if span["kind"] == cicd_telemetry.SPAN_KIND_INTERNAL
    ]
    duplicated = cicd_telemetry.reconcile_expected_trace(
        run_id="42821429094",
        attempt=1,
        job_ids=[*job_ids, job_ids[0]],
        observed_spans=tasks,
    )
    assert duplicated["ok"] is False
    assert job_ids[0] in duplicated["duplicate_expected_job_ids"]
    same_number = cicd_telemetry.reconcile_expected_trace(
        run_id="42821429094",
        attempt=1,
        job_ids=[int(job_ids[0]), job_ids[0]],
        observed_spans=tasks[:1],
    )
    assert same_number["ok"] is False
    assert same_number["duplicate_expected_job_ids"] == [job_ids[0]]
    canonical = cicd_telemetry.reconcile_expected_trace(
        run_id="42821429094",
        attempt=1,
        job_ids=[int(job_ids[0])],
        observed_spans=tasks[:1],
    )
    text_form = cicd_telemetry.reconcile_expected_trace(
        run_id="42821429094",
        attempt=1,
        job_ids=[job_ids[0]],
        observed_spans=tasks[:1],
    )
    assert canonical["ok"] is True
    assert canonical["expected_trace_id"] == text_form["expected_trace_id"]
    for illegal in (None, "", "abc", "0129", 0, -3):
        refused = cicd_telemetry.reconcile_expected_trace(
            run_id="42821429094",
            attempt=1,
            job_ids=[illegal],
            observed_spans=[],
        )
        assert refused["ok"] is False
        assert refused["invalid_expected_job_ids"]
        assert refused["source_completeness_verified"] is False


def test_missing_job_url_is_an_omission_not_full_coverage():
    _report, job_ids = _three_job_witness()
    jobs = []
    for index, job_id in enumerate(job_ids):
        jobs.append(
            complete_facts(
                run_id="42821429094",
                run_attempt=1,
                job_id=job_id,
                job_name=f"job-{index}",
                job_url=None if index == 1 else f"https://example.test/job/{index}",
            )
        )
    run = complete_facts(
        run_id="42821429094",
        run_attempt=1,
        run_completed_at_ns=None,
        run_status="completed",
        job_id=None,
        job_name=None,
        job_url=None,
        job_conclusion=None,
    )
    produced = cicd_telemetry.produce(
        run,
        source="github_api",
        task_facts=jobs,
        completed_jobs_total=3,
        job_selection="all_completed",
        end_time_mode=cicd_telemetry.END_TIME_WITNESS,
        terminal_observed_at_ns=cicd_telemetry.parse_time_ns("2026-11-10T13:08:30Z"),
    )
    omitted = [
        row for row in produced["coverage"]["expected_completed_jobs"] if row["omitted"]
    ]
    non_omitted = [
        row
        for row in produced["coverage"]["expected_completed_jobs"]
        if not row["omitted"]
    ]
    assert len(omitted) == 1
    assert omitted[0]["job_id"] == job_ids[1]
    assert omitted[0]["omit_reason"] == "missing_task_required_identity"
    assert len(non_omitted) == 2
    assert all(row["emitted"] is False for row in non_omitted)
    assert produced["coverage"]["task_spans_emitted"] == 0
    assert len(produced["terminal_job_records"]) == 2
    assert len(produced["coverage"]["tasks_omitted"]) == 1
    assert produced["coverage"]["attempt_coverage"] == "complete"
    assert produced["coverage"]["task_coverage"] == "partial"
    mapping = produced["coverage"]["job_mapping"]
    assert mapping["verification_scope"] == "producer_objects"
    assert mapping["source_independence_verified"] is False
    assert mapping["observed_capture_verified"] is False


def test_job_mapping_url_mismatch_does_not_verify_the_url():
    report = cicd_telemetry.produce(complete_facts())
    spans = _external_githubreceiver_spans(
        "42627302972",
        1,
        ["129194268575"],
        job_urls={"129194268575": "https://example.test/wrong"},
    )
    rows = [
        {**row, "emitted": True}
        for row in report["coverage"]["expected_completed_jobs"]
    ]
    mapping = cicd_telemetry._audit_job_rows(spans, rows)
    assert mapping["identity_ok"] is True
    assert mapping["job_url_verified"] is False
    assert mapping["url_mismatch"]
    assert mapping["url_checked"] == 1
    assert mapping["url_matched"] == 0
    assert mapping["observed_capture_verified"] is False
    assert mapping["source_independence_verified"] is False


def test_producer_does_not_hide_a_duplicate_job_witness():
    report, job_ids = _three_job_witness()
    duplicated = report["coverage"]["witness_anomalies"]
    assert duplicated == []
    jobs = []
    for index, job_id in enumerate(job_ids):
        jobs.append(
            complete_facts(
                run_id="42821429094",
                run_attempt=1,
                job_id=job_id,
                job_name=f"job-{index}",
                job_url=f"https://example.test/job/{index}",
            )
        )
    jobs.append(jobs[0])
    run = complete_facts(
        run_id="42821429094",
        run_attempt=1,
        run_completed_at_ns=None,
        run_status="completed",
        job_id=None,
        job_name=None,
        job_url=None,
        job_conclusion=None,
    )
    produced = cicd_telemetry.produce(
        run,
        source="github_api",
        github_api_fetched=True,
        task_facts=jobs,
        completed_jobs_total=4,
        job_selection="all_completed",
        end_time_mode=cicd_telemetry.END_TIME_WITNESS,
        terminal_observed_at_ns=cicd_telemetry.parse_time_ns("2026-11-10T13:08:30Z"),
    )
    assert produced["coverage"]["attempt_coverage"] == "incomplete"
    assert produced["coverage"]["witness_anomalies"][0]["reason"] == "duplicate_job_id"
    assert produced["coverage"]["task_spans_emitted"] == 0
    assert len(produced["terminal_job_records"]) == 3
    assert produced["coverage"]["job_mapping"]["identity_ok"] is True
    assert produced["coverage"]["job_mapping"]["job_url_verified"] is False


def test_semconv_source_does_not_copy_a_second_registry_pin():
    report = cicd_telemetry.produce(complete_facts())
    source = report["producer"]["semconv_source"]
    assert source["official_source_pinned"] is False
    assert source["official_registry_verified"] is False
    assert source["registry_check"] == "not_checked"
    assert source["group_ids_role"] == "producer_mapping"
    assert "log.cicd.pipeline.terminal" in source["group_ids"]
    assert "metric.cicd.pipeline.run.duration" in source["group_ids"]
    assert "span.cicd.pipeline.run.server" not in source["group_ids"]
    assert "repository" not in source
    assert "version" not in source
    assert "full_commit" not in source
    assert report["schema_version"] == cicd_telemetry.SCHEMA_VERSION
    assert report["producer"]["id_scheme"] == cicd_telemetry.ID_SCHEME
    assert not hasattr(cicd_telemetry, "semconv_citation_verified")


def test_post_run_workflow_syncs_with_the_lockfile():
    workflow = Path(".github/workflows/cicd-telemetry-post-run.yml").read_text(
        encoding="utf-8"
    )
    assert "uv sync --locked" in workflow
    assert "uv run --locked python -m engineering_ci.cicd_telemetry" in workflow
    assert "uv sync\n" not in workflow


_ARCHIVED_V8 = Path("docs/verification/cicd-telemetry-real-run-identity-v8")


def test_v8_identity_archive_keeps_its_original_bytes():
    # Historical evidence is frozen to the original producer, never regenerated
    # with the current attribute contract. Hash the original LF Git blobs.
    digests = {
        "candidate.json": "afa9b3b18b7f5fa2ef154778ba5da8654cfc0889aa1aa0651bd8688b434f5c2c",
        "otel-logs.json": "555300eedcc0a8433a34871423be7353ca8990780a80c6aeecddfbd7debbc6ef",
        "otel-traces.json": "67b28c3ab36adb7a7400f4e8233e2d641f7f17b3ce1c7e52dbf5622e6692b60c",
    }
    for name, digest in digests.items():
        original = (_ARCHIVED_V8 / name).read_text(encoding="utf-8").encode()
        assert hashlib.sha256(original).hexdigest() == digest
    saved = json.loads((_ARCHIVED_V8 / "candidate.json").read_text(encoding="utf-8"))
    assert saved["schema_version"] == 8
    assert saved["evidence_role"] == "file_identity_check"
    assert saved["producer"]["id_scheme"] == "github-actions-attempt-sha256-v1"
    assert saved["producer"]["semconv_source"]["official_registry_verified"] is False
    assert saved["producer"]["semconv_source"]["official_source_pinned"] is False
    assert saved["coverage"]["expected_trace"]["expected_trace_id"] == (
        "21ee8206fe06a4879580f60591a3014f"
    )
    assert saved["claims"]["rpc_accepted"] is False
    traces = json.loads((_ARCHIVED_V8 / "otel-traces.json").read_text(encoding="utf-8"))
    scope = traces["resourceSpans"][0]["scopeSpans"][0]["scope"]
    assert scope == {"name": "eci_core.cicd_telemetry"}
    logs = json.loads((_ARCHIVED_V8 / "otel-logs.json").read_text(encoding="utf-8"))
    assert logs["resourceLogs"][0]["scopeLogs"][0]["scope"] == scope


def test_v8_identity_note_is_a_file_check_not_a_receiver_ack():
    # The native test Source includes evidence JSON, not Markdown. Verify the
    # actual archived candidate's note and transport claims in both environments.
    saved = json.loads((_ARCHIVED_V8 / "candidate.json").read_text(encoding="utf-8"))
    text = saved["evidence_note"]
    assert "File identity check only. Not a receiver ACK." in text
    assert "42821429094" in text
    assert saved["evidence_role"] == "file_identity_check"
    for claim in (
        "rpc_accepted",
        "receiver_observed",
        "otlp_delivered",
        "github_authenticated",
        "weaver_pass",
        "trusted",
    ):
        assert saved["claims"][claim] is False


@pytest.mark.parametrize("zero_jobs", [False, True])
def test_witness_cli_auto_captures_observation_without_flag(
    tmp_path, monkeypatch, zero_jobs
):
    run, job = _api_run_job_fixture()
    if zero_jobs:
        run = {**run, "conclusion": "cancelled"}
        pages = {"total_count": 0, "jobs": []}
    else:
        job = {**job, "run_id": run["id"], "run_attempt": 1}
        pages = {"total_count": 1, "jobs": [job]}
    monkeypatch.setattr(cicd_telemetry, "gh_api_get", _api_attempt_get(run, pages))
    fixed_ns = cicd_telemetry.parse_time_ns("2024-01-01T00:05:00Z")
    monkeypatch.setattr(cicd_telemetry, "time_ns", lambda: fixed_ns)
    output = tmp_path / "candidate.json"
    logs_out = tmp_path / "otel-logs.json"
    code = cicd_telemetry.main(
        [
            "--repository",
            "dymzz/engineering-ci",
            "--run-id",
            str(run["id"]),
            "--attempt",
            "1",
            "--output",
            str(output),
            "--otlp-logs-json",
            str(logs_out),
        ]
    )
    assert code == 0
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["claims"]["github_api_fetched"] is True
    assert saved["claims"]["github_authenticated"] is False
    assert saved["terminal"]["terminal_confirmed"] is True
    assert saved["terminal"]["observed_at_ns"] == fixed_ns
    attempt_logs = [
        item
        for item in saved["signals"]["logs"]
        if item["source"] == "github_attempt_terminal"
    ]
    assert len(attempt_logs) == 1
    assert attempt_logs[0]["time_unix_nano"] == fixed_ns
    assert logs_out.is_file()
    if zero_jobs:
        assert saved["signals"]["logs_source"] == "github_attempt_terminal"
        assert saved["terminal"]["lower_source"] == "weak_run_start"
    else:
        assert saved["signals"]["logs_source"] == "github_job_terminal"


@pytest.mark.parametrize("source", ["mapping", "env"])
def test_non_api_source_cannot_confirm_attempt_terminal(source):
    facts = complete_facts(run_completed_at_ns=None, run_status="completed")
    report = cicd_telemetry.produce(
        facts,
        source=source,
        github_api_fetched=False,
        end_time_mode=cicd_telemetry.END_TIME_WITNESS,
        terminal_observed_at_ns=JOB_END + 5_000_000_000,
    )
    cicd_telemetry.attach_signal_candidates(report)
    assert report["status"] == "partial"
    assert report["reason"] == "terminal_source_not_github_api"
    assert report["terminal"]["terminal_confirmed"] is False
    assert report["claims"]["github_authenticated"] is False
    assert report["coverage"]["terminal_check"] == "not_applicable"
    assert all(
        item["source"] != "github_attempt_terminal"
        for item in report["signals"]["logs"]
    )


@pytest.mark.parametrize("zero_jobs", [False, True])
def test_api_completed_without_run_started_at_still_emits_terminal_log(zero_jobs):
    facts = complete_facts(
        run_started_at_ns=None,
        run_completed_at_ns=None,
        run_status="completed",
        run_conclusion="cancelled" if zero_jobs else "success",
        **(
            {
                "job_id": None,
                "job_name": None,
                "job_url": None,
                "job_conclusion": None,
                "job_started_at_ns": None,
                "job_completed_at_ns": None,
            }
            if zero_jobs
            else {}
        ),
    )
    observed = JOB_END + 5_000_000_000
    report = cicd_telemetry.produce(
        facts,
        source="github_api",
        github_api_fetched=True,
        end_time_mode=cicd_telemetry.END_TIME_WITNESS,
        terminal_observed_at_ns=observed,
        completed_jobs_total=0 if zero_jobs else 1,
        job_selection="none" if zero_jobs else "all_completed",
        task_facts=[] if zero_jobs else None,
    )
    cicd_telemetry.attach_signal_candidates(report)
    assert report["status"] == "partial"
    assert report["reason"] == "missing_run_timestamps"
    assert report["terminal"]["terminal_confirmed"] is True
    assert report["terminal"]["duration_valid_for_metrics"] is False
    assert (
        report["terminal"]["time_precision"] == cicd_telemetry.TIME_PRECISION_UNPROVEN
    )
    assert report["otlp_metrics_json"] is None
    assert any(
        item["source"] == "github_attempt_terminal"
        for item in report["signals"]["logs"]
    )
    if zero_jobs:
        assert report["terminal"]["bounds_valid"] is False
        assert report["terminal"]["clock_quality"] == "unproven"
    else:
        assert report["terminal"]["bounds_valid"] is True
        assert report["terminal"]["end_lower_bound_ns"] == JOB_END


def test_production_handoff_defaults_not_verified_and_preserves_otlp_json_file(
    tmp_path,
):
    report = cicd_telemetry.produce(complete_facts())
    handoff = report["production_handoff"]
    assert handoff["status"] == "not_verified"
    assert handoff["local_e2e_proves_production_webhook"] is False
    assert len(handoff["missing_prerequisites"]) == 5

    only_local = cicd_telemetry.evaluate_receiver_production_handoff(
        webhook_subscription_verified=True,
        webhook_hmac_verified=True,
        live_receiver_ingested=True,
        otlp_traces_delivered=True,
        live_consumer_gate_verified=True,
        local_e2e_only=True,
    )
    assert only_local["status"] == "not_verified"
    workflow = Path(".github/workflows/cicd-telemetry-post-run.yml").read_text(
        encoding="utf-8"
    )
    assert "ECI_GITHUBRECEIVER_WEBHOOK_SUBSCRIPTION_VERIFIED" in workflow
    assert "select_post_run_cutover_policy" in workflow
    assert "--allow-pre-cutover-trace-fallback" in workflow

    facts_path = tmp_path / "facts.json"
    facts_path.write_text(
        json.dumps(cicd_telemetry.produce(complete_facts())["facts"]),
        encoding="utf-8",
    )
    sentinel = tmp_path / "existing-otel.json"
    sentinel.write_text('{"keep": "sentinel"}\n', encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cicd_telemetry.main(
            [
                "--facts",
                str(facts_path),
                "--output",
                str(tmp_path / "out.json"),
                "--otlp-json",
                str(sentinel),
            ]
        )
    assert exc.value.code == 2
    assert sentinel.read_text(encoding="utf-8") == '{"keep": "sentinel"}\n'


def test_post_run_cutover_behavior_not_verified_vs_verified(tmp_path, monkeypatch):
    """P1-04: not_verified retains working Trace fallback; verified activates retirement."""
    run, job = _api_run_job_fixture()
    job = {**job, "run_id": run["id"], "run_attempt": 1}
    pages = {"total_count": 1, "jobs": [job]}
    monkeypatch.setattr(cicd_telemetry, "gh_api_get", _api_attempt_get(run, pages))
    fixed_ns = cicd_telemetry.parse_time_ns("2024-01-01T00:05:00Z")
    monkeypatch.setattr(cicd_telemetry, "time_ns", lambda: fixed_ns)

    # 1) Partial / local_e2e_only readiness stays not_verified and retains otel.json Trace output.
    unverified_handoff = cicd_telemetry.evaluate_receiver_production_handoff_env(
        {
            "ECI_GITHUBRECEIVER_WEBHOOK_SUBSCRIPTION_VERIFIED": "true",
            "ECI_GITHUBRECEIVER_WEBHOOK_HMAC_VERIFIED": "true",
            "ECI_GITHUBRECEIVER_LIVE_INGESTED": "false",
            "ECI_GITHUBRECEIVER_OTLP_TRACES_DELIVERED": "false",
            "ECI_GITHUBRECEIVER_CONSUMER_VERIFIED": "false",
        }
    )
    policy_unverified = cicd_telemetry.select_post_run_cutover_policy(
        unverified_handoff
    )
    assert unverified_handoff["status"] == "not_verified"
    assert policy_unverified["allow_pre_cutover_trace_fallback"] is True

    pre_dir = tmp_path / "pre-cutover"
    pre_handoff_path = pre_dir / "production-handoff.json"
    pre_dir.mkdir(parents=True)
    pre_handoff_path.write_text(json.dumps(unverified_handoff), encoding="utf-8")
    pre_out = pre_dir / "candidate.json"
    pre_otel = pre_dir / "otel.json"
    pre_logs = pre_dir / "otel-logs.json"
    assert (
        cicd_telemetry.main(
            [
                "--repository",
                "dymzz/engineering-ci",
                "--run-id",
                str(run["id"]),
                "--attempt",
                "1",
                "--production-handoff",
                str(pre_handoff_path),
                "--allow-pre-cutover-trace-fallback",
                "--end-time-mode",
                policy_unverified["mode"],
                "--output",
                str(pre_out),
                "--otlp-json",
                str(pre_otel),
                "--otlp-logs-json",
                str(pre_logs),
            ]
        )
        == 0
    )
    pre_saved = json.loads(pre_out.read_text(encoding="utf-8"))
    assert pre_saved["claims"]["trace_producer_retired"] is False
    assert pre_saved["claims"]["otlp_json_prepared"] is True
    assert pre_saved["claims"]["otlp_json_exported"] is True
    assert pre_saved["producer"]["trace_owner"] == "eci_core.cicd_telemetry"
    assert pre_saved["producer"]["actual_trace_producer"] == "eci_core.cicd_telemetry"
    assert (
        pre_saved["producer"]["target_trace_owner"]
        == "opentelemetry-collector-contrib/githubreceiver"
    )
    assert pre_saved["production_handoff"]["status"] == "not_verified"
    assert (
        pre_saved["production_handoff"]["cutover_action"]
        == "retained_pre_cutover_trace_fallback"
    )
    assert len(pre_saved["spans"]) == 1
    assert pre_otel.is_file() and pre_logs.is_file()

    # 2) Verified production handoff enforces witness mode and refuses fallback / --otlp-json.
    verified_handoff = cicd_telemetry.evaluate_receiver_production_handoff_env(
        {
            "ECI_GITHUBRECEIVER_WEBHOOK_SUBSCRIPTION_VERIFIED": "true",
            "ECI_GITHUBRECEIVER_WEBHOOK_HMAC_VERIFIED": "true",
            "ECI_GITHUBRECEIVER_LIVE_INGESTED": "true",
            "ECI_GITHUBRECEIVER_OTLP_TRACES_DELIVERED": "true",
            "ECI_GITHUBRECEIVER_CONSUMER_VERIFIED": "true",
        }
    )
    policy_verified = cicd_telemetry.select_post_run_cutover_policy(verified_handoff)
    assert verified_handoff["status"] == "verified"
    assert policy_verified["mode"] == cicd_telemetry.END_TIME_WITNESS
    assert policy_verified["allow_pre_cutover_trace_fallback"] is False

    ver_dir = tmp_path / "verified-cutover"
    ver_dir.mkdir(parents=True)
    ver_handoff_path = ver_dir / "production-handoff.json"
    ver_handoff_path.write_text(json.dumps(verified_handoff), encoding="utf-8")
    ver_out = ver_dir / "candidate.json"
    ver_otel = ver_dir / "otel.json"
    ver_logs = ver_dir / "otel-logs.json"
    assert (
        cicd_telemetry.main(
            [
                "--repository",
                "dymzz/engineering-ci",
                "--run-id",
                str(run["id"]),
                "--attempt",
                "1",
                "--production-handoff",
                str(ver_handoff_path),
                "--end-time-mode",
                policy_verified["mode"],
                "--output",
                str(ver_out),
                "--otlp-logs-json",
                str(ver_logs),
            ]
        )
        == 0
    )
    ver_saved = json.loads(ver_out.read_text(encoding="utf-8"))
    assert ver_saved["claims"]["trace_producer_retired"] is True
    assert ver_saved["otlp_json_prepared"] is False
    assert ver_saved["spans"] == []
    assert (
        ver_saved["producer"]["actual_trace_producer"]
        == "opentelemetry-collector-contrib/githubreceiver"
    )
    assert (
        ver_saved["producer"]["target_trace_owner"]
        == "opentelemetry-collector-contrib/githubreceiver"
    )
    assert ver_saved["production_handoff"]["status"] == "verified"
    assert (
        ver_saved["production_handoff"]["cutover_action"] == "retired_to_githubreceiver"
    )
    assert any(
        item["source"] == "github_attempt_terminal"
        for item in ver_saved["signals"]["logs"]
    )
    assert not ver_otel.exists()

    with pytest.raises(SystemExit) as refused:
        cicd_telemetry.main(
            [
                "--repository",
                "dymzz/engineering-ci",
                "--run-id",
                str(run["id"]),
                "--attempt",
                "1",
                "--production-handoff",
                str(ver_handoff_path),
                "--allow-pre-cutover-trace-fallback",
                "--end-time-mode",
                cicd_telemetry.END_TIME_OBSERVED_UPPER_BOUND,
                "--output",
                str(ver_dir / "refused.json"),
                "--otlp-json",
                str(ver_otel),
            ]
        )
    assert refused.value.code == 2
    assert not ver_otel.exists()


def test_partial_rerun_api_witness_separates_companion_logs_44713410255_pattern(
    monkeypatch,
):
    """P1-06 + P2: Run 44713410255 Attempt 2 reused jobs never emit Attempt 2 Job Terminal Logs."""
    run = {
        "id": 44713410255,
        "name": "Engineering CI",
        "run_attempt": 2,
        "status": "completed",
        "conclusion": "success",
        "head_sha": "7ea85c24a62e65a9384a0abe9eb541ca54064cbc",
        "run_started_at": "2026-11-20T09:49:34Z",
        "updated_at": "2026-11-20T09:50:55Z",
        "html_url": "https://github.com/dymzz/engineering-ci/actions/runs/44713410255",
        "repository": {"full_name": "dymzz/engineering-ci"},
    }

    def mk_job(jid, name, started, completed):
        return {
            "id": jid,
            "run_id": 44713410255,
            "run_attempt": 2,
            "name": name,
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-11-20T09:49:36Z",
            "started_at": started,
            "completed_at": completed,
            "html_url": f"https://github.com/dymzz/engineering-ci/actions/runs/44713410255/job/{jid}",
        }

    exec_job = mk_job(
        136212522105, "version-metadata", "2026-11-20T09:49:39Z", "2026-11-20T09:50:49Z"
    )
    reused_jobs = [
        mk_job(
            136212523496, "python-ci", "2026-11-20T09:45:32Z", "2026-11-20T09:47:10Z"
        ),
        mk_job(
            136212524904, "dagger-gate", "2026-11-20T09:45:32Z", "2026-11-20T09:47:10Z"
        ),
        mk_job(
            136212602878,
            "sync-tool-tests",
            "2026-11-20T09:45:32Z",
            "2026-11-20T09:47:10Z",
        ),
    ]
    pages = {"total_count": 4, "jobs": [exec_job, *reused_jobs]}
    facts, cov = cicd_telemetry.fetch_github_run_job(
        "dymzz/engineering-ci",
        44713410255,
        attempt=2,
        api_get=_api_attempt_get(run, pages),
    )
    observed = cicd_telemetry.parse_time_ns("2026-11-20T09:51:00Z")
    report = cicd_telemetry.produce(
        facts,
        source="github_api",
        github_api_fetched=True,
        end_time_mode=cicd_telemetry.END_TIME_WITNESS,
        terminal_observed_at_ns=observed,
        completed_jobs_total=cov["completed_jobs_total"],
        job_selection=cov["job_selection"],
        task_facts=cov["task_facts"],
        expected_job_stubs=cov["expected_job_stubs"],
        api_jobs_snapshot=cov["api_jobs_snapshot"],
    )
    cicd_telemetry.attach_signal_candidates(report)
    assert report["status"] == "witness_only"
    assert len(report["result_inventory"]) == 4
    assert [r["job_id"] for r in report["terminal_job_records"]] == ["136212522105"]
    assert [r["job_id"] for r in report["reused_job_records"]] == [
        "136212523496",
        "136212524904",
        "136212602878",
    ]
    assert all(
        r["trace_id"] is None and r["span_id"] is None
        for r in report["reused_job_records"]
    )
    job_logs = [
        item
        for item in report["signals"]["logs"]
        if item["source"] == "github_job_terminal"
    ]
    assert len(job_logs) == 1
    assert job_logs[0]["attributes"]["cicd.pipeline.task.run.id"] == "136212522105"
    assert job_logs[0]["provenance"]["execution_attribution"] == "executed_in_attempt"
    assert job_logs[0]["provenance"]["execution_attribution_source"] == "run_started_at"
    assert any(
        item["source"] == "github_attempt_terminal"
        for item in report["signals"]["logs"]
    )

    # Without run_started_at or executed_job_ids, Attempt 2 attribution stays unproven/unknown.
    no_start_run = {k: v for k, v in run.items() if k != "run_started_at"}
    facts_u, cov_u = cicd_telemetry.fetch_github_run_job(
        "dymzz/engineering-ci",
        44713410255,
        attempt=2,
        api_get=_api_attempt_get(no_start_run, pages),
    )
    report_u = cicd_telemetry.produce(
        facts_u,
        source="github_api",
        github_api_fetched=True,
        end_time_mode=cicd_telemetry.END_TIME_WITNESS,
        terminal_observed_at_ns=observed,
        completed_jobs_total=cov_u["completed_jobs_total"],
        job_selection=cov_u["job_selection"],
        task_facts=cov_u["task_facts"],
        expected_job_stubs=cov_u["expected_job_stubs"],
        api_jobs_snapshot=cov_u["api_jobs_snapshot"],
    )
    cicd_telemetry.attach_signal_candidates(report_u)
    assert report_u["status"] == "partial"
    assert report_u["coverage"]["execution_attribution_status"] == "unknown"
    assert report_u["terminal_job_records"] == []
    assert len(report_u["unknown_execution_jobs"]) == 4
    assert all(
        item["source"] != "github_job_terminal" for item in report_u["signals"]["logs"]
    )
    assert any(
        item["source"] == "github_attempt_terminal"
        for item in report_u["signals"]["logs"]
    )

    with pytest.raises(cicd_telemetry.TelemetryError, match="wrong_reuse_attribution"):
        cicd_telemetry.produce(
            facts,
            source="github_api",
            github_api_fetched=True,
            end_time_mode=cicd_telemetry.END_TIME_WITNESS,
            terminal_observed_at_ns=observed,
            completed_jobs_total=cov["completed_jobs_total"],
            job_selection=cov["job_selection"],
            task_facts=cov["task_facts"],
            executed_job_ids=["136212523496"],
        )


def _extract_post_run_workflow_python_blocks() -> list[str]:
    workflow_text = Path(".github/workflows/cicd-telemetry-post-run.yml").read_text(
        encoding="utf-8"
    )
    import re
    import textwrap

    blocks = []
    for match in re.finditer(
        r"python -c '\n(.*?)\n\s*'", workflow_text, flags=re.DOTALL
    ):
        blocks.append(textwrap.dedent(match.group(1)))
    for match in re.finditer(r"python -c '([^\n']+)'", workflow_text):
        blocks.append(match.group(1))
    assert len(blocks) >= 2
    return blocks


@pytest.mark.parametrize("verified", [False, True])
def test_workflow_inline_python_compiles_and_executes_real_branches(
    tmp_path, monkeypatch, verified
):
    """P1-07 + P1-08 + P2: embedded workflow python -c blocks compile & execute end-to-end."""
    import sys

    blocks = _extract_post_run_workflow_python_blocks()
    for block in blocks:
        compile(block, ".github/workflows/cicd-telemetry-post-run.yml", "exec")

    env = {
        "ECI_GITHUBRECEIVER_WEBHOOK_SUBSCRIPTION_VERIFIED": (
            "true" if verified else "false"
        ),
        "ECI_GITHUBRECEIVER_WEBHOOK_HMAC_VERIFIED": "true",
        "ECI_GITHUBRECEIVER_LIVE_INGESTED": "true" if verified else "false",
        "ECI_GITHUBRECEIVER_OTLP_TRACES_DELIVERED": "true" if verified else "false",
        "ECI_GITHUBRECEIVER_CONSUMER_VERIFIED": "true" if verified else "false",
    }
    monkeypatch.chdir(tmp_path)
    (tmp_path / "artifacts" / "cicd-telemetry").mkdir(parents=True)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    src_root = str(Path(__file__).resolve().parents[1] / "src")
    proc_env = {**cicd_telemetry.os.environ, "PYTHONPATH": src_root}
    proc = subprocess.run(
        [sys.executable, "-c", blocks[0]],
        cwd=tmp_path,
        env=proc_env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    handoff_file = tmp_path / "artifacts" / "cicd-telemetry" / "production-handoff.json"
    policy_file = tmp_path / "artifacts" / "cicd-telemetry" / "end-time-policy.txt"
    status_proc = subprocess.run(
        [sys.executable, "-c", blocks[1]],
        cwd=tmp_path,
        env=proc_env,
        check=True,
        capture_output=True,
        text=True,
    )
    cutover_status = status_proc.stdout.strip()
    policy_lines = dict(
        line.split("=", 1)
        for line in policy_file.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    assert handoff_file.is_file()
    if verified:
        assert cutover_status == "verified"
        assert policy_lines["mode"] == "witness"
        assert (
            policy_lines["actual_trace_producer"]
            == "opentelemetry-collector-contrib/githubreceiver"
        )
        assert (
            policy_lines["target_trace_owner"]
            == "opentelemetry-collector-contrib/githubreceiver"
        )
        assert policy_lines["production_webhook_cutover"] == "verified"
    else:
        assert cutover_status == "not_verified"
        assert policy_lines["mode"] == "observed_upper_bound"
        assert policy_lines["actual_trace_producer"] == "eci_core.cicd_telemetry"
        assert (
            policy_lines["target_trace_owner"]
            == "opentelemetry-collector-contrib/githubreceiver"
        )
        assert policy_lines["production_webhook_cutover"] == "not_verified"


@pytest.mark.parametrize("conclusion", ["success", "cancelled"])
def test_pre_cutover_zero_job_fallback_skips_otel_json_without_blocking_logs(
    tmp_path, monkeypatch, conclusion
):
    """P1-08: not_verified fallback on zero-job completed Attempt (success/cancelled) exports Terminal Logs."""
    run, _ = _api_run_job_fixture()
    run = {**run, "status": "completed", "conclusion": conclusion}
    pages = {"total_count": 0, "jobs": []}
    monkeypatch.setattr(cicd_telemetry, "gh_api_get", _api_attempt_get(run, pages))
    fixed_ns = cicd_telemetry.parse_time_ns("2024-01-01T00:05:00Z")
    monkeypatch.setattr(cicd_telemetry, "time_ns", lambda: fixed_ns)

    handoff = cicd_telemetry.evaluate_receiver_production_handoff()
    policy = cicd_telemetry.select_post_run_cutover_policy(handoff)
    assert policy["cutover_status"] == "not_verified"

    handoff_path = tmp_path / "production-handoff.json"
    handoff_path.write_text(json.dumps(handoff), encoding="utf-8")
    out_json = tmp_path / "candidate.json"
    otel_json = tmp_path / "otel.json"
    logs_json = tmp_path / "otel-logs.json"

    code = cicd_telemetry.main(
        [
            "--repository",
            "dymzz/engineering-ci",
            "--run-id",
            str(run["id"]),
            "--attempt",
            "1",
            "--production-handoff",
            str(handoff_path),
            "--allow-pre-cutover-trace-fallback",
            "--end-time-mode",
            policy["mode"],
            "--output",
            str(out_json),
            "--otlp-json",
            str(otel_json),
            "--otlp-logs-json",
            str(logs_json),
        ]
    )
    assert code == 0
    saved = json.loads(out_json.read_text(encoding="utf-8"))
    assert saved["claims"]["otlp_json_prepared"] is False
    assert saved["claims"]["otlp_json_exported"] is False
    assert saved["claims"]["otlp_json_skip"] == "no_candidate_spans"
    assert saved["claims"]["otlp_logs_prepared"] is True
    assert saved["claims"]["otlp_logs_exported"] is True
    assert saved["producer"]["actual_trace_producer"] == "eci_core.cicd_telemetry"
    assert (
        saved["producer"]["target_trace_owner"]
        == "opentelemetry-collector-contrib/githubreceiver"
    )
    assert not otel_json.exists()
    assert logs_json.is_file()
    log_payload = json.loads(logs_json.read_text(encoding="utf-8"))
    bodies = [
        rec["body"]["stringValue"]
        for rec in log_payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
    ]
    assert any("GitHub Attempt terminal confirmed" in b for b in bodies)

    # Real trace flush error (non-zero spans failing to write otel.json) still fails exit 3
    # and prevents subsequent log export.
    _, job = _api_run_job_fixture()
    job = {**job, "run_id": run["id"], "run_attempt": 1}
    monkeypatch.setattr(
        cicd_telemetry,
        "gh_api_get",
        _api_attempt_get(run, {"total_count": 1, "jobs": [job]}),
    )
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("x", encoding="utf-8")
    blocked_logs = tmp_path / "should-not-write-logs.json"
    fail_code = cicd_telemetry.main(
        [
            "--repository",
            "dymzz/engineering-ci",
            "--run-id",
            str(run["id"]),
            "--attempt",
            "1",
            "--production-handoff",
            str(handoff_path),
            "--allow-pre-cutover-trace-fallback",
            "--end-time-mode",
            policy["mode"],
            "--output",
            str(tmp_path / "fail-candidate.json"),
            "--otlp-json",
            str(blocked / "otel.json"),
            "--otlp-logs-json",
            str(blocked_logs),
        ]
    )
    assert fail_code == 3
    assert not blocked_logs.exists()
