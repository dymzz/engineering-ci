"""GitHub Run/Job facts → CI/CD telemetry records (ECI #75)."""

import json
from pathlib import Path

from eci_core import cicd_telemetry


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
        "conclusion": "success",
        "head_sha": "a" * 40,
        "repository": "dymzz/engineering-ci",
    }
    value.update(overrides)
    return cicd_telemetry.facts_from_mapping(value)


def test_produce_emits_pipeline_and_task_spans():
    report = cicd_telemetry.produce(complete_facts(), now_ns=1_000_000_000)
    assert report["status"] == "produced"
    assert report["reason"] is None
    assert len(report["spans"]) == 2
    pipeline, task = report["spans"]
    assert pipeline["attributes"]["cicd.pipeline.run.id"] == "42627302972"
    assert pipeline["attributes"]["github.actions.run_attempt"] == 1
    assert pipeline["attributes"]["cicd.pipeline.result"] == "success"
    assert task["parent_span_id"] == pipeline["span_id"]
    assert task["attributes"]["cicd.pipeline.task.run.id"] == "129194268575"
    assert task["attributes"]["cicd.pipeline.task.name"] == "python-ci / checks"


def test_missing_run_identity_is_incomplete_not_forged():
    report = cicd_telemetry.produce(
        complete_facts(run_id=None, run_attempt=None), now_ns=1
    )
    assert report["status"] == "incomplete"
    assert report["reason"] == "missing_run_identity"
    assert report["spans"] == []


def test_missing_terminal_result_refuses_complete_pipeline_span():
    report = cicd_telemetry.produce(complete_facts(conclusion=None), now_ns=1)
    assert report["status"] == "incomplete"
    assert report["reason"] == "missing_terminal_result"
    assert report["spans"] == []


def test_pipeline_without_job_id_is_partial():
    report = cicd_telemetry.produce(complete_facts(job_id=None), now_ns=1)
    assert report["status"] == "partial"
    assert report["reason"] == "pipeline_without_task_identity"
    assert len(report["spans"]) == 1
    assert report["spans"][0]["attributes"]["cicd.pipeline.run.id"] == "42627302972"


def test_selection_status_is_not_mapped_as_pipeline_result():
    facts = complete_facts(conclusion="success")
    report = cicd_telemetry.produce(facts, now_ns=1)
    # Explicitly ensure business statuses are not stuffed into attributes.
    blob = json.dumps(report)
    assert "not_executed" not in blob
    assert "required_checks_not_executed" not in blob
    assert report["spans"][0]["attributes"]["cicd.pipeline.result"] == "success"


def test_facts_from_actions_env():
    facts = cicd_telemetry.facts_from_actions_env(
        {
            "GITHUB_WORKFLOW": "Engineering CI",
            "GITHUB_RUN_ID": "99",
            "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_JOB": "checks",
            "GITHUB_SHA": "b" * 40,
            "GITHUB_REPOSITORY": "dymzz/engineering-ci",
            "ENGINEERING_CI_JOB_ID": "job-1",
            "ENGINEERING_CI_JOB_CONCLUSION": "failure",
        }
    )
    assert facts.run_id == "99"
    assert facts.run_attempt == 2
    assert facts.job_id == "job-1"
    assert facts.conclusion == "failure"
    report = cicd_telemetry.produce(facts, now_ns=5)
    assert report["spans"][0]["attributes"]["cicd.pipeline.result"] == "failure"


def test_cli_writes_artifact(tmp_path):
    facts_path = tmp_path / "facts.json"
    facts_path.write_text(
        json.dumps(
            {
                "workflow_name": "Engineering CI",
                "run_id": "1",
                "run_attempt": 1,
                "job_id": "2",
                "job_name": "checks",
                "conclusion": "success",
                "head_sha": "c" * 40,
                "repository": "dymzz/engineering-ci",
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
