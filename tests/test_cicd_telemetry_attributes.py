"""Producer repository mapping and registered-only OTLP attribute contract."""

import json
from dataclasses import asdict, replace

import pytest
from test_cicd_telemetry import JOB_END, _api_run_job_fixture, complete_facts

from engineering_ci import cicd_telemetry as telemetry
from engineering_ci import github_receiver


@pytest.mark.parametrize(
    "origin", ["https://github.com", "https://git.example.test:8443"]
)
@pytest.mark.parametrize("source", ["env", "mapping", "api"])
def test_repository_url_preserves_its_real_host(origin, source):
    repository = "dymzz/engineering-ci"
    run_url = f"{origin}/{repository}/actions/runs/42627302972"
    if source == "api":
        facts = telemetry.facts_from_github_api_objects(
            {
                "id": 42627302972,
                "run_attempt": 1,
                "name": "Engineering CI",
                "html_url": run_url,
                "repository": {
                    "full_name": repository,
                    "html_url": f"{origin}/{repository}",
                },
            }
        )
    elif source == "env":
        facts = telemetry.facts_from_actions_env(
            {
                "GITHUB_RUN_ID": "42627302972",
                "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_WORKFLOW": "Engineering CI",
                "GITHUB_REPOSITORY": repository,
                "GITHUB_SERVER_URL": origin,
            }
        )
    else:
        facts = complete_facts(server_url=origin, run_url=run_url)
    report = telemetry.produce(facts)
    assert report["resource"]["vcs.repository.url.full"] == f"{origin}/{repository}"
    assert report["facts"]["repository"] == repository
    assert report["repository_identity"]["status"] == "mapped"
    assert report["repository_identity"]["authenticated"] is False


def test_run_url_alone_provides_repository_origin():
    report = telemetry.produce(
        complete_facts(
            run_url="https://ghe.example.test/dymzz/engineering-ci/actions/runs/42627302972/attempts/1"
        )
    )
    assert report["resource"]["vcs.repository.url.full"] == (
        "https://ghe.example.test/dymzz/engineering-ci"
    )
    assert report["repository_identity"]["sources"][0]["field"] == "run_url"


@pytest.mark.parametrize("source", ["env", "mapping", "api"])
def test_missing_origin_never_invents_public_github(source):
    if source == "mapping":
        facts = complete_facts()
    elif source == "env":
        facts = telemetry.facts_from_actions_env({"GITHUB_REPOSITORY": "owner/repo"})
    else:
        facts = telemetry.facts_from_github_api_objects(
            {"repository": {"full_name": "owner/repo"}}
        )
    assert facts.server_url is None
    report = telemetry.produce(
        replace(facts, workflow_name="checks", run_id="1", run_attempt=1)
    )
    assert "vcs.repository.url.full" not in report["resource"]
    assert "github.repository" not in report["resource"]
    assert report["repository_identity"]["reason"] == "repository_origin_missing"


@pytest.mark.parametrize(
    ("fields", "code"),
    [
        (
            {"repository_url": "https://github.com/other/repo"},
            "repository_url_mismatch",
        ),
        ({"repository_url": "owner/repo"}, "repository_url_invalid"),
        ({"server_url": "https://github.com/path"}, "repository_url_mismatch"),
        ({"server_url": "https://token@github.com"}, "repository_url_invalid"),
        ({"server_url": "https://github.com?token=private"}, "repository_url_invalid"),
        ({"server_url": "https://github.com:invalid"}, "repository_url_invalid"),
        ({"server_url": "https://git hub.com"}, "repository_url_invalid"),
        ({"repository": "../repo"}, "repository_invalid"),
        (
            {
                "server_url": "https://github.com",
                "repository_url": "https://ghe.example.test/dymzz/engineering-ci",
            },
            "repository_host_mismatch",
        ),
        (
            {"run_url": "https://github.com/dymzz/engineering-ci/actions/runs/99"},
            "repository_url_mismatch",
        ),
        (
            {
                "run_url": "https://github.com/dymzz/engineering-ci/actions/runs/42627302972/attempts/2"
            },
            "repository_url_mismatch",
        ),
    ],
)
def test_conflicting_or_invalid_repository_sources_refuse_export(fields, code):
    with pytest.raises(telemetry.TelemetryError, match=f"^{code}$"):
        telemetry.produce(complete_facts(**fields))


def test_cli_repository_conflict_exits_before_writing_otlp(tmp_path, capsys):
    facts = tmp_path / "facts.json"
    facts.write_text(
        json.dumps(
            asdict(complete_facts(repository_url="https://github.com/other/repo"))
        ),
        encoding="utf-8",
    )
    output = tmp_path / "candidate.json"
    capture = tmp_path / "otel.logs.json"
    with pytest.raises(SystemExit) as rejected:
        telemetry.main(
            [
                "--facts",
                str(facts),
                "--output",
                str(output),
                "--otlp-logs-json",
                str(capture),
            ]
        )
    assert rejected.value.code == 2
    assert (
        capsys.readouterr().err == "cicd-telemetry rejected: repository_url_mismatch\n"
    )
    assert not output.exists() and not capture.exists()

    legacy_otlp = tmp_path / "otel.json"
    with pytest.raises(SystemExit) as retired:
        telemetry.main(
            [
                "--facts",
                str(facts),
                "--output",
                str(output),
                "--otlp-json",
                str(legacy_otlp),
            ]
        )
    assert retired.value.code == 2
    assert (
        capsys.readouterr().err
        == "cicd-telemetry rejected: duplicate_trace_producer_retired\n"
    )
    assert not output.exists() and not legacy_otlp.exists()


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"repository": {"full_name": "other/repo"}}, "run_repository_mismatch"),
        ({"run_attempt": 2}, "run_attempt_mismatch"),
        ({"head_sha": "f" * 40}, "event_api_head_sha_conflict"),
    ],
)
def test_api_source_identity_rejection_survives_attribute_migration(changes, code):
    run, job = _api_run_job_fixture()
    run.update(changes)

    def api_get(path):
        if "/jobs" in path:
            return {"total_count": 1, "jobs": [job]}
        return run

    with pytest.raises(telemetry.TelemetryError, match=f"^{code}$"):
        telemetry.fetch_github_run_job(
            "dymzz/engineering-ci",
            42769746408,
            attempt=1,
            api_get=api_get,
            event_head_sha="e" * 40,
        )


@pytest.mark.parametrize("with_observed_timestamp", [True, False])
def test_all_signals_keep_provenance_outside_otlp_attributes(with_observed_timestamp):
    facts = complete_facts(
        server_url="https://github.com",
        run_status="completed",
        run_completed_at_ns=None,
    )
    with pytest.raises(
        telemetry.TelemetryError, match="^duplicate_trace_producer_retired$"
    ):
        telemetry.produce(
            facts,
            end_time_mode=telemetry.END_TIME_OBSERVED_UPPER_BOUND,
            terminal_observed_at_ns=JOB_END + 1,
            max_end_uncertainty_ns=1,
        )

    report = telemetry.produce(
        facts,
        end_time_mode=telemetry.END_TIME_WITNESS,
        terminal_observed_at_ns=JOB_END + 1 if with_observed_timestamp else None,
        completed_jobs_total=1,
        job_selection="all_completed",
        jobs_observed_total=1,
        noncompleted_jobs_total=0,
        phase_intervals=[
            {
                "state": "executing",
                "start_ns": facts.job_started_at_ns,
                "end_ns": facts.job_completed_at_ns,
            }
        ],
    )
    assert report["spans"] == []
    assert report["terminal"]["root_emission"] == "not_emitted"
    assert report["terminal_provenance"]["attempt"] == 1
    assert report["terminal_provenance"]["time_source_verified"] is False
    telemetry.attach_signal_candidates(report)
    telemetry.attach_otlp_json(report)
    assert report["otlp_json"] is None
    assert report["claims"]["otlp_json_prepared"] is False
    assert report["claims"]["trace_producer_retired"] is True
    assert report["signals"]["logs"][0]["source"] == "github_job_terminal"
    for signal in ("otlp_logs_json", "otlp_metrics_json"):
        payload = report[signal]

        def attribute_keys(value):
            if isinstance(value, dict):
                if "key" in value:
                    yield value["key"]
                for child in value.values():
                    yield from attribute_keys(child)
            elif isinstance(value, list):
                for child in value:
                    yield from attribute_keys(child)

        keys = set(attribute_keys(payload))
        assert keys
        assert not any(key.startswith(("github.", "eci.")) for key in keys)
    if with_observed_timestamp:
        attempt_log = report["signals"]["logs"][-1]
        assert attempt_log["source"] == "github_attempt_terminal"
        assert attempt_log["trace_id"] == github_receiver.trace_id(42627302972, 1)
        assert attempt_log["span_id"] == github_receiver.root_span_id(42627302972, 1)
    else:
        assert len(report["signals"]["logs"]) == 1


def test_composite_attempt_identity_retains_rejection_without_an_extension():
    report = telemetry.produce(complete_facts())
    expected = {"run_id": "42627302972", "attempt": 1, "job_ids": ["129194268575"]}

    def receiver_spans(attempt, job_id):
        tid = github_receiver.trace_id(42627302972, attempt)
        rid = github_receiver.root_span_id(42627302972, attempt)
        jid = github_receiver.job_span_id(job_id)
        return [
            {
                "trace_id": tid,
                "span_id": rid,
                "parent_span_id": None,
                "kind": telemetry.SPAN_KIND_INTERNAL,
                "attributes": {"cicd.pipeline.run.id": f"42627302972/{attempt}"},
            },
            {
                "trace_id": tid,
                "span_id": jid,
                "parent_span_id": rid,
                "kind": telemetry.SPAN_KIND_INTERNAL,
                "attributes": {
                    "cicd.pipeline.run.id": f"42627302972/{attempt}",
                    telemetry.ATTR_TASK_RUN_ID: str(job_id),
                },
            },
        ]

    attempt_1_spans = receiver_spans(1, 129194268575)
    assert telemetry.reconcile_expected_trace(
        **expected, observed_spans=attempt_1_spans
    )["ok"]
    mismatch_same_job = telemetry.reconcile_expected_trace(
        **expected, observed_spans=receiver_spans(2, 129194268575)
    )
    assert mismatch_same_job["ok"] is False
    assert mismatch_same_job["trace_mismatch"] and mismatch_same_job["parent_mismatch"]
    mismatch = telemetry.reconcile_expected_trace(
        **expected, observed_spans=receiver_spans(2, 129194268576)
    )
    assert mismatch["ok"] is False
    assert (
        mismatch["trace_mismatch"]
        and mismatch["parent_mismatch"]
        and mismatch["missing"]
        and mismatch["unexpected"]
    )
    assert report["spans"] == []
    assert report["producer"]["id_scheme"] == github_receiver.ID_SCHEME
    assert report["resource"]["cicd.pipeline.run.id"] == "42627302972/1"
    for span in attempt_1_spans:
        assert span["attributes"]["cicd.pipeline.run.id"] == "42627302972/1"
        assert "github.actions.run_attempt" not in span["attributes"]
