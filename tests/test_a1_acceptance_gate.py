"""The A.1 acceptance gate must fail on a wrong or overstated association.

Issue #87 requires the mandatory CI step to fail when the receipt's
association is wrong, when the parse fails and when a claim exceeds the
evidence. These tests build an honest artifact folder, prove the gate accepts
it, and then forge one claim at a time to prove the gate rejects it.
"""

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / ".github" / "scripts" / "assert_dagger_native_a1.py"

SESSION = "session-0001"
ENGINE = "engine-0001"
TRACE = "a" * 32


def span(span_id, parent, name, scope):
    return {
        "traceId": TRACE,
        "spanId": span_id,
        "parentSpanId": parent or "",
        "name": name,
        "scope": scope,
        "kind": 1,
        "startTimeUnixNano": "1",
        "endTimeUnixNano": "2",
        "attributes": [],
    }


def capture_bytes(spans):
    """One capture, grouped by the scope of each span."""
    grouped = {}
    for item in spans:
        grouped.setdefault(item["scope"], []).append(item)
    payload = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "dagger.io/session.id",
                            "value": {"stringValue": SESSION},
                        },
                        {
                            "key": "dagger.io/engine.instance.id",
                            "value": {"stringValue": ENGINE},
                        },
                    ]
                },
                "scopeSpans": [
                    {"scope": {"name": scope}, "spans": grouped[scope]}
                    for scope in grouped
                ],
            }
        ]
    }
    return (json.dumps(payload, indent=2) + "\n").encode("utf-8")


def check_spans():
    return [
        span("root1", "", "POST /query", "dagger.io/engine.server"),
        span("sel1", "root1", "dag://?check=lint", "dagger.io/util/parallel"),
        span("av1", "sel1", "Artifact.value", "dagger.io/core"),
        span("core1", "av1", "EngineeringCi.lint", "dagger.io/core"),
        span(
            "call1",
            "core1",
            "engineering-ci:EngineeringCi.lint",
            "dagger.io/dagql",
        ),
    ]


def artifact_spans():
    return [
        span("root2", "", "POST /query", "dagger.io/engine.server"),
        span("core2", "root2", "EngineeringCi.repositoryRuffTools", "dagger.io/core"),
        span(
            "call2",
            "core2",
            "engineering-ci:EngineeringCi.repositoryRuffTools",
            "dagger.io/dagql",
        ),
    ]


def chain_entry(span_data, session=SESSION, engine=ENGINE):
    return {
        "trace_id": span_data["traceId"],
        "span_id": span_data["spanId"],
        "parent_span_id": span_data["parentSpanId"],
        "name": span_data["name"],
        "session_id": session,
        "engine_instance_id": engine,
    }


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(payload, indent=2) + "\n").encode("utf-8"))


def build_folder(tmp_path):
    """An honest artifact folder the gate must accept."""
    folder = tmp_path / "artifacts"
    folder.mkdir(parents=True, exist_ok=True)

    checks = check_spans()
    functions = artifact_spans()
    (folder / "capture.json").write_bytes(capture_bytes(checks))
    (folder / "capture-negative.json").write_bytes(capture_bytes(checks))
    (folder / "capture-entrypoints.json").write_bytes(capture_bytes(functions))

    native = {"check": "lint", "exit_code": 0}
    write_json(folder / "native-result.json", native)
    write_json(
        folder / "native-result-negative.json", {"check": "lint", "exit_code": 1}
    )
    (folder / "check.exit").write_text("0\n", encoding="utf-8")
    (folder / "negative-check.exit").write_text("1\n", encoding="utf-8")

    entrypoints = folder / "entrypoints"
    entrypoints.mkdir(parents=True, exist_ok=True)
    (entrypoints / "artifact.exit").write_text("0\n", encoding="utf-8")
    (entrypoints / "ruff-tools").write_bytes(b"tool-versions\n")

    check_chain = [
        chain_entry(checks[1]),
        chain_entry(checks[2]),
        chain_entry(checks[3]),
        chain_entry(checks[4]),
    ]
    function_chain = [chain_entry(functions[1]), chain_entry(functions[2])]

    receipt = {
        "kind": "engineering-ci-dagger-telemetry",
        "evidence_class": "candidate_record",
        "binding": {
            "binding_status": "bound",
            "binding_reason": None,
            "capture_isolated": True,
            "verified_parent_chain": check_chain,
        },
        "claims": {"source_associated": True},
        "source_binding": {"source_binding_status": "unknown"},
        "origin_execution": {"new_tool_execution": None},
        "raw_trace_ref": {
            "sha256": hashlib.sha256((folder / "capture.json").read_bytes()).hexdigest()
        },
        "observed_trace_span_ids": [item["span_id"] for item in check_chain],
        "native_check_result": native,
        "native_result_or_bundle_ref": {
            "sha256": hashlib.sha256(
                (folder / "native-result.json").read_bytes()
            ).hexdigest()
        },
    }
    negative = copy.deepcopy(receipt)
    negative["raw_trace_ref"]["sha256"] = hashlib.sha256(
        (folder / "capture-negative.json").read_bytes()
    ).hexdigest()
    negative["native_check_result"] = {"check": "lint", "exit_code": 1}
    negative["native_result_or_bundle_ref"]["sha256"] = hashlib.sha256(
        (folder / "native-result-negative.json").read_bytes()
    ).hexdigest()

    artifact = copy.deepcopy(receipt)
    artifact["binding"]["verified_parent_chain"] = function_chain
    artifact["observed_trace_span_ids"] = [item["span_id"] for item in function_chain]
    artifact["native_check_result"] = None
    artifact.pop("native_result_or_bundle_ref")
    artifact["raw_trace_ref"]["sha256"] = hashlib.sha256(
        (folder / "capture-entrypoints.json").read_bytes()
    ).hexdigest()

    write_json(folder / "receipt.json", receipt)
    write_json(folder / "receipt-negative.json", negative)
    write_json(folder / "receipt-entrypoints.json", artifact)
    return folder


def run_gate(folder):
    return subprocess.run(
        [sys.executable, str(GATE), str(folder)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_gate_accepts_an_honest_artifact_folder(tmp_path):
    result = run_gate(build_folder(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS A.1 pass" in result.stdout
    assert "PASS A.1 fail" in result.stdout
    assert "PASS A.1 artifact" in result.stdout


def forge(folder, name, mutate):
    receipt = json.loads((folder / f"receipt{name}.json").read_text(encoding="utf-8"))
    mutate(receipt)
    write_json(folder / f"receipt{name}.json", receipt)


def test_the_gate_rejects_an_invented_span(tmp_path):
    folder = build_folder(tmp_path)
    forge(
        folder,
        "",
        lambda receipt: receipt["binding"]["verified_parent_chain"].append(
            {
                "trace_id": TRACE,
                "span_id": "invented1",
                "parent_span_id": "call1",
                "name": "engineering-ci:EngineeringCi.invented",
                "session_id": SESSION,
                "engine_instance_id": ENGINE,
            }
        ),
    )
    result = run_gate(folder)
    assert result.returncode != 0
    assert "invented native Span" in result.stdout + result.stderr


def test_the_gate_rejects_a_broken_parent_link(tmp_path):
    folder = build_folder(tmp_path)

    def break_link(receipt):
        chain = receipt["binding"]["verified_parent_chain"]
        chain[2]["parent_span_id"] = "not-the-parent"

    forge(folder, "", break_link)
    result = run_gate(folder)
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "Parent ID" in output or "broken ancestry" in output


def test_the_gate_rejects_a_wrong_resource_identity(tmp_path):
    folder = build_folder(tmp_path)

    def wrong_identity(receipt):
        receipt["binding"]["verified_parent_chain"][1]["session_id"] = "other-session"

    forge(folder, "", wrong_identity)
    result = run_gate(folder)
    assert result.returncode != 0
    assert "wrong resource identity" in result.stdout + result.stderr


def test_the_gate_rejects_a_tool_origin_overclaim(tmp_path):
    folder = build_folder(tmp_path)

    def overclaim(receipt):
        receipt["origin_execution"]["new_tool_execution"] = True

    forge(folder, "", overclaim)
    result = run_gate(folder)
    assert result.returncode != 0
    assert "incorrectly promoted to Tool Origin" in result.stdout + result.stderr


def test_the_gate_rejects_a_verified_git_source_claim(tmp_path):
    folder = build_folder(tmp_path)

    def overclaim(receipt):
        receipt["source_binding"]["source_binding_status"] = "verified"

    forge(folder, "", overclaim)
    result = run_gate(folder)
    assert result.returncode != 0
    assert "Git Source incorrectly verified" in result.stdout + result.stderr


def test_the_gate_rejects_a_tampered_capture(tmp_path):
    folder = build_folder(tmp_path)

    def tamper(receipt):
        receipt["raw_trace_ref"]["sha256"] = "0" * 64

    forge(folder, "", tamper)
    result = run_gate(folder)
    assert result.returncode != 0
    assert "raw capture bytes disagree" in result.stdout + result.stderr


def test_the_gate_rejects_an_altered_native_result(tmp_path):
    folder = build_folder(tmp_path)

    def alter(receipt):
        receipt["native_check_result"] = {"check": "lint", "exit_code": 0}

    forge(folder, "-negative", alter)
    result = run_gate(folder)
    assert result.returncode != 0
    assert "original result changed by recorder" in result.stdout + result.stderr


def test_the_gate_rejects_a_false_check_for_the_standalone_function(tmp_path):
    folder = build_folder(tmp_path)

    def label_as_check(receipt):
        receipt["native_check_result"] = {"check": "lint", "exit_code": 0}

    forge(folder, "-entrypoints", label_as_check)
    result = run_gate(folder)
    assert result.returncode != 0
    assert "falsely labeled a Check" in result.stdout + result.stderr
