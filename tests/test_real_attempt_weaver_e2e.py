"""Fixed #52 consumer boundary tests; native execution is verified separately."""

import copy
import importlib.util
import shutil
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tests/integration/prepare_real_attempt_weaver.py"
SPEC = importlib.util.spec_from_file_location("real_attempt_weaver", SCRIPT)
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


@pytest.fixture
def attempt():
    witness = adapter._json(adapter.EXPECTATION)
    evidence = adapter._evidence(witness)
    return witness, evidence["candidate"]["document"], evidence["otel"]["document"]


def _spans(capture):
    return capture["resourceSpans"][0]["scopeSpans"][0]["spans"]


def _set_attribute(owner, key, value):
    entry = next(item for item in owner["attributes"] if item["key"] == key)
    entry["value"] = value


def test_materialized_source_preserves_capture_bytes_and_independent_expected(
    tmp_path, attempt
):
    witness, candidate, _ = attempt
    directory = tmp_path / "source"
    result = adapter.prepare(directory)
    assert result["status"] == "prepared_not_executed"
    assert (
        result["weaver_pass"]
        is result["trusted"]
        is result["admitted"]
        is result["required"]
        is False
    )
    assert result["official_registry"]["official_registry_verified"] is True
    assert len(result["inventory"]["matched"]) == 3
    assert not any(
        result["inventory"][key]
        for key in ("missing", "unexpected", "duplicate", "mismatched")
    )
    assert (directory / "capture.json").read_bytes() == (
        adapter.EVIDENCE / "otel-42821429094-all-completed.json"
    ).read_bytes()
    expected = adapter._json(directory / "expected.json")
    assert {item["trace_id"] for item in expected["spans"]} == {
        witness["run"]["trace_id"]
    }
    assert {item["span_id"] for item in expected["spans"]} == {
        job["span_id"] for job in witness["jobs"]
    }
    assert {item["matcher"] for item in expected["spans"]} == {"eci.task"}
    assert expected["provenance"]["producer_claims"] == candidate["claims"]
    assert tomllib.loads((directory / "ci-weaver.toml").read_text())[
        "expected_matchers"
    ] == ["eci.task"]
    assert "name.startsWith" in (directory / ".weaver.toml").read_text()
    assert "terminal_evidence" not in expected
    with pytest.raises(adapter.InputMismatch, match="destination_already_exists"):
        adapter.prepare(directory)


@pytest.mark.parametrize(
    "mutation,reason,category",
    [
        (lambda c: _spans(c).pop(), "expected_task_missing", "missing"),
        (
            lambda c: _spans(c).append(copy.deepcopy(_spans(c)[0])),
            "duplicate_task_identity",
            "duplicate",
        ),
        (
            lambda c: _set_attribute(
                _spans(c)[0],
                "cicd.pipeline.task.run.id",
                {"stringValue": "unknown-job"},
            ),
            "unexpected_task_identity",
            "unexpected",
        ),
        (
            lambda c: _spans(c)[0].update(spanId="f" * 16),
            "task_identity_or_attributes_mismatch",
            "mismatched",
        ),
        (
            lambda c: [_span.update(traceId="f" * 32) for _span in _spans(c)],
            "task_identity_or_attributes_mismatch",
            "mismatched",
        ),
        (
            lambda c: _set_attribute(
                _spans(c)[0], "github.actions.run_attempt", {"intValue": "2"}
            ),
            "task_identity_or_attributes_mismatch",
            "mismatched",
        ),
    ],
)
def test_per_task_refusals_are_located(attempt, mutation, reason, category):
    witness, candidate, capture = attempt
    mutation(capture)
    with pytest.raises(adapter.InputMismatch, match=reason) as error:
        adapter.validate_attempt(witness, candidate, capture)
    assert error.value.inventory[category]
    assert error.value.inventory["expected_count"] == 3


def test_every_task_on_the_same_wrong_trace_still_fails(attempt):
    witness, candidate, capture = attempt
    for span in _spans(capture):
        span["traceId"] = "f" * 32
    assert len({s["traceId"] for s in _spans(capture)}) == 1
    with pytest.raises(adapter.InputMismatch) as error:
        adapter.validate_attempt(witness, candidate, capture)
    inventory = error.value.inventory
    assert inventory["matched"] == []
    assert len(inventory["mismatched"]) == len(inventory["missing"]) == 3
    assert all(item["fields"] == ["trace_id"] for item in inventory["mismatched"])
    assert adapter._expected_tasks(witness)[0]["trace_id"] != "f" * 32


@pytest.mark.parametrize(
    "key,value",
    [
        ("github.actions.run_attempt", {"intValue": "2"}),
        ("vcs.ref.head.revision", {"stringValue": "f" * 40}),
        ("github.repository", {"stringValue": "wrong/repository"}),
        ("cicd.pipeline.run.id", {"stringValue": "wrong/1"}),
    ],
)
def test_wrong_resource_identity_is_rejected(attempt, key, value):
    witness, candidate, capture = attempt
    _set_attribute(capture["resourceSpans"][0]["resource"], key, value)
    with pytest.raises(
        adapter.InputMismatch, match="candidate_or_resource_provenance_mismatch"
    ):
        adapter.validate_attempt(witness, candidate, capture)


@pytest.mark.parametrize(
    "claim", ["github_authenticated", "otlp_delivered", "weaver_pass"]
)
def test_archived_claims_cannot_be_upgraded(attempt, claim):
    witness, candidate, capture = attempt
    candidate["claims"][claim] = True
    with pytest.raises(
        adapter.InputMismatch, match="candidate_or_resource_provenance_mismatch"
    ):
        adapter.validate_attempt(witness, candidate, capture)


def test_expected_mapping_is_not_taken_from_actual_capture(attempt):
    witness, _, capture = attempt
    witness["run"]["trace_id"] = _spans(capture)[0]["traceId"] = "f" * 32
    with pytest.raises(adapter.InputMismatch, match="expected_trace_witness_mismatch"):
        adapter.task_inventory(witness, capture)


def test_coordinated_archive_and_local_manifest_tamper_rejected(
    tmp_path, monkeypatch, attempt
):
    witness, _, _ = attempt
    archive = tmp_path / "archive"
    shutil.copytree(adapter.EVIDENCE, archive)
    target = archive / "otel-42821429094-all-completed.json"
    capture = adapter._json(target)
    for span in _spans(capture):
        span["traceId"] = "f" * 32
    adapter._write_json(target, capture)
    manifest = archive / "SHA256SUMS"
    manifest.write_text(
        manifest.read_text().replace(
            witness["archive"]["files"][target.name], adapter._hash(target)
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(adapter, "EVIDENCE", archive)
    with pytest.raises(
        adapter.InputMismatch, match="original_evidence_digest_mismatch"
    ):
        adapter._evidence(witness)


@pytest.fixture
def registry_copy(tmp_path, monkeypatch):
    fixture = tmp_path / "fixture"
    shutil.copytree(adapter.FIXTURE, fixture)
    monkeypatch.setattr(adapter, "FIXTURE", fixture)
    return fixture


def test_coordinated_registry_and_manifest_tamper_rejected(registry_copy):
    cfg = tomllib.loads((registry_copy / "ci-weaver.toml").read_text())
    model = registry_copy / "registry/cicd/spans.yaml"
    model.write_bytes(model.read_bytes() + b"\n# coordinated local edit\n")
    manifest_path = registry_copy / "registry-sha256.json"
    manifest = adapter._json(manifest_path)
    manifest["cicd/spans.yaml"] = adapter._hash(model)
    adapter._write_json(manifest_path, manifest)
    with pytest.raises(adapter.InputMismatch, match="official_registry_bytes_mismatch"):
        adapter._registry_identity(cfg)


@pytest.mark.parametrize(
    "key,value",
    [
        ("version", "9.9.9"),
        ("commit", "f" * 40),
        ("repository", "https://example.test/semconv"),
    ],
)
def test_unverified_registry_declaration_rejected(key, value):
    cfg = tomllib.loads((adapter.FIXTURE / "ci-weaver.toml").read_text())
    cfg["origin"][key] = value
    with pytest.raises(adapter.InputMismatch, match=f"official_source_{key}_mismatch"):
        adapter._registry_identity(cfg)


@pytest.fixture
def exported_result(tmp_path, attempt):
    """Synthetic contract input for result-refusal tests, never native evidence."""
    source, artifacts = tmp_path / "source", tmp_path / "artifacts"
    adapter.prepare(source)
    artifacts.mkdir()
    cfg = tomllib.loads((source / "ci-weaver.toml").read_text())
    expected = adapter._json(source / "expected.json")
    native = {
        "samples": [
            {
                "span": {
                    "trace_id": item["trace_id"],
                    "span_id": item["span_id"],
                    "live_check_result": {
                        "match_info": {
                            "signal_matcher": "eci.task",
                            "signal": adapter.TASK_SIGNAL,
                            "unmatched": False,
                        }
                    },
                }
            }
            for item in expected["spans"]
        ]
    }
    for sample, job in zip(native["samples"], attempt[0]["jobs"], strict=True):
        sample["span"]["name"] = job["name"]
        sample["span"]["attributes"] = [
            {"name": "cicd.pipeline.task.run.id", "value": job["job_id"]},
            {"name": "cicd.pipeline.task.run.url.full", "value": job["url"]},
            {
                "name": "github.actions.run_attempt",
                "value": attempt[0]["run"]["attempt"],
            },
        ]
    report = {
        "name": "weaver_live",
        "status": "passed",
        "exit_code": 0,
        "gate_exit_code": 0,
        "trusted": False,
        "source": expected["source"],
        "source_digest": "synthetic-unit-test-source",
        "official_registry_verified": True,
        "eci_policy_evaluated": True,
        "native_report": native,
        "coverage": {
            "official_registry_verified": True,
            "registry_version": adapter.semconv_registry.VERSION,
            "registry_commit": adapter.semconv_registry.COMMIT,
            "official_registry_tree_sha256": adapter.semconv_registry.TREE_SHA256,
        },
        "inputs": {
            key: adapter._hash(source / name)
            for key, name in (
                ("config_sha256", "ci-weaver.toml"),
                ("matcher_sha256", cfg["matcher_config"]),
                ("expectations_sha256", cfg["expectations"]),
                ("otlp_sha256", cfg["otlp"]),
            )
        },
        "commands": [
            {
                "argv": ["weaver", "registry", "live-check"],
                "stdout": "live.stdout",
                "stderr": "live.stderr",
            }
        ],
    }
    adapter._write_json(artifacts / "live_check.json", native)
    adapter._write_json(artifacts / "report.json", report)
    (artifacts / "live.stdout").write_text("")
    (artifacts / "live.stderr").write_text("")
    return source, artifacts, report


def test_result_consumer_keeps_producer_claims_and_requires_plan_binding(
    exported_result, tmp_path
):
    source, artifacts, report = exported_result
    result = adapter.verify_report(source, artifacts)
    assert result["status"] == "passed"
    assert result["producer_claims"]["github_authenticated"] is False
    assert result["producer_claims"]["otlp_delivered"] is False
    assert result["required"] is result["admitted"] is result["trusted"] is False
    plan = tmp_path / "plan-result.json"
    adapter._write_json(
        plan,
        {
            "status": "passed",
            "source_tree_verified": "verified",
            "source_digest": adapter.selection.directory_content_id(source),
            "checks": [{**report, "selected": "selected"}],
        },
    )
    assert adapter.verify_report(source, artifacts, plan)["plan_binding"] == "verified"
    policy = source / "advice-policies/cicd-error-type.rego"
    original = policy.read_bytes()
    policy.write_bytes(original + b"\n# changed after execution\n")
    with pytest.raises(adapter.InputMismatch, match="plan_report_binding_mismatch"):
        adapter.verify_report(source, artifacts, plan)
    policy.write_bytes(original)
    adapter._write_json(
        plan, {"status": "not_executed", "reason": "source_tree_mismatch", "checks": []}
    )
    with pytest.raises(adapter.InputMismatch, match="plan_report_binding_mismatch"):
        adapter.verify_report(source, artifacts, plan)


@pytest.mark.parametrize(
    "change,reason",
    [
        (lambda r: r.update(status="not_executed"), "native_check_not_passed"),
        (lambda r: r.update(gate_exit_code=1), "native_check_not_passed"),
        (
            lambda r: r.update(official_registry_verified=False),
            "native_official_source_unverified",
        ),
        (
            lambda r: r["coverage"].update(registry_version="9.9.9"),
            "native_official_source_unverified",
        ),
        (
            lambda r: r["inputs"].update(expectations_sha256="f" * 64),
            "native_input_digest_mismatch",
        ),
        (lambda r: r.update(trusted=True), "unsupported_trust_upgrade"),
        (
            lambda r: r["commands"].append(copy.deepcopy(r["commands"][0])),
            "native_execution_count_mismatch",
        ),
    ],
)
def test_report_generation_or_zero_cli_exit_cannot_override_refusal(
    exported_result, change, reason
):
    source, artifacts, report = exported_result
    change(report)
    adapter._write_json(artifacts / "report.json", report)
    with pytest.raises(adapter.InputMismatch, match=reason):
        adapter.verify_report(source, artifacts)


def test_missing_native_artifact_cannot_pass(exported_result):
    source, artifacts, _ = exported_result
    (artifacts / "live.stderr").unlink()
    with pytest.raises(adapter.InputMismatch, match="native_artifact_missing"):
        adapter.verify_report(source, artifacts)


def test_same_native_ids_with_wrong_job_attributes_are_rejected(exported_result):
    source, artifacts, report = exported_result
    report["native_report"]["samples"][0]["span"]["attributes"][0]["value"] = (
        "foreign-job"
    )
    adapter._write_json(artifacts / "live_check.json", report["native_report"])
    adapter._write_json(artifacts / "report.json", report)
    with pytest.raises(adapter.InputMismatch, match="native_task_attributes_mismatch"):
        adapter.verify_report(source, artifacts)


def test_forged_coverage_count_cannot_hide_duplicate_received_task(exported_result):
    source, artifacts, report = exported_result
    report["native_report"]["samples"].append(
        copy.deepcopy(report["native_report"]["samples"][0])
    )
    report["coverage"]["matched_spans"] = 3
    adapter._write_json(artifacts / "live_check.json", report["native_report"])
    adapter._write_json(artifacts / "report.json", report)
    with pytest.raises(adapter.InputMismatch, match="native_task_inventory_mismatch"):
        adapter.verify_report(source, artifacts)
