"""#52 Producer→Weaver input binding regressions; no native Weaver re-run here."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tests/integration/prepare_real_attempt_weaver.py"
SPEC = importlib.util.spec_from_file_location("real_attempt_weaver", SCRIPT)
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


def _capture() -> dict:
    return json.loads(
        (REPO / "docs/verification/cicd-telemetry-evidence-all-completed"
         / "otel-42821429094-all-completed.json").read_text(encoding="utf-8")
    )


def _mutated_capture(monkeypatch, change):
    actual_json = adapter._json

    def read(path):
        value = actual_json(path)
        if path.name == "otel-42821429094-all-completed.json":
            change(value)
        return value

    monkeypatch.setattr(adapter, "_json", read)


def test_real_attempt_materializes_bounded_three_task_source(tmp_path):
    directory = tmp_path / "e2e-source"
    report = adapter.prepare(directory)
    assert report["status"] == "prepared_not_executed"
    assert report["tasks"] == 3
    assert report["trusted"] is False
    assert report["weaver_pass"] is False
    assert (directory / "registry/manifest.yaml").is_file()
    assert (directory / "registry-sha256.json").is_file()
    assert (directory / "advice-policies/otel.rego").is_file()
    assert "name.startsWith" in (directory / ".weaver.toml").read_text()
    expected = json.loads((directory / "expected.json").read_text())
    assert len(expected["spans"]) == 3
    assert {s["matcher"] for s in expected["spans"]} == {"eci.task"}
    assert {s["signal"] for s in expected["spans"]} == {
        "cicd.pipeline.task.internal"
    }
    with pytest.raises(adapter.InputMismatch, match="destination_already_exists"):
        adapter.prepare(directory)


@pytest.mark.parametrize(
    ("mutation", "failure"),
    [
        (lambda x: x["resourceSpans"][0]["scopeSpans"][0]["spans"].pop(),
         "task_inventory_count_mismatch"),
        (lambda x: x["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
         .update(spanId="ffffffffffffffff"), "task_identity_or_attributes_mismatch"),
        (lambda x: x["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"][1]
         .update(value={"stringValue": "wrong-job"}), "unexpected_task_identity"),
        (lambda x: x["resourceSpans"][0]["resource"]["attributes"][1]
         .update(value={"stringValue": "wrong-run/1"}), "candidate_or_resource_provenance_mismatch"),
    ],
)
def test_real_attempt_mismatch_cannot_build_source(
    tmp_path, monkeypatch, mutation, failure
):
    _mutated_capture(monkeypatch, mutation)
    with pytest.raises(adapter.InputMismatch, match=failure):
        adapter.prepare(tmp_path / "reject")
    assert not (tmp_path / "reject").exists()


def test_explicit_expectation_is_not_derived_from_receiver_sample(tmp_path, monkeypatch):
    original = adapter._json

    def mismatch(path):
        value = original(path)
        if path.name == "real-attempt-42821429094.json":
            value["jobs"][0]["job_id"] = "unobserved-but-claimed"
        return value

    monkeypatch.setattr(adapter, "_json", mismatch)
    with pytest.raises(adapter.InputMismatch, match="unexpected_task_identity"):
        adapter.prepare(tmp_path / "missing-job")


def test_missing_or_wrong_source_evidence_digest_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "_hash", lambda p: "0" * 64)
    with pytest.raises(adapter.InputMismatch, match="original_evidence_digest_mismatch"):
        adapter.prepare(tmp_path / "tampered")


def test_explicit_run_correlation_cannot_be_silently_upgraded(tmp_path, monkeypatch):
    actual_json = adapter._json

    def fake(path):
        value = actual_json(path)
        if path.name.startswith("candidate-"):
            value["claims"]["github_authenticated"] = True
        return value

    monkeypatch.setattr(adapter, "_json", fake)
    with pytest.raises(adapter.InputMismatch, match="candidate_or_resource_provenance_mismatch"):
        adapter.prepare(tmp_path / "untrusted")


def test_task_source_pins_original_evidence_and_is_not_live_capture():
    evidence = adapter._json(adapter.EXPECTATION)
    assert evidence["limits"]["weaver_pass"] is False
    assert evidence["limits"]["otlp_delivered"] is False
    assert evidence["limits"]["github_authenticated"] is False
    assert len(evidence["jobs"]) == 3
    assert "otlp" not in evidence["run"]
