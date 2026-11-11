"""Materialize #52's already captured real GitHub Attempt for native Weaver.

This is a test/consumer adapter, not a producer, Registry authority or
trusted admission path. All inputs originate from committed #75/#74 evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EVIDENCE = REPO / "docs/verification/cicd-telemetry-evidence-all-completed"
FIXTURE = REPO / "tests/fixtures/weaver-cicd"
EXPECTATION = REPO / "tests/integration/real-attempt-42821429094.json"
SOURCE_COMMIT = "fb30ca17193b551473b1f43c315482918ab6987c"


class InputMismatch(ValueError):
    """Fixed source and independent expected object inventory disagree."""


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_evidence_files() -> None:
    sums = {}
    for line in (EVIDENCE / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split(maxsplit=1)
        name = name.strip().lstrip("*")
        if name in sums or "/" in name or "\\" in name:
            raise InputMismatch("invalid_evidence_manifest")
        sums[name] = digest
    for name in ("candidate-42821429094-all-completed.json",
                 "otel-42821429094-all-completed.json"):
        if name not in sums or _hash(EVIDENCE / name) != sums[name]:
            raise InputMismatch("original_evidence_digest_mismatch")


def _span_attributes(span: dict) -> dict:
    return {item["key"]: item["value"] for item in span.get("attributes", [])}


def _span_records(capture: dict) -> tuple[dict, list[dict]]:
    resources = capture.get("resourceSpans")
    if not isinstance(resources, list) or len(resources) != 1:
        raise InputMismatch("unexpected_resource_count")
    entry = resources[0]
    scopes = entry.get("scopeSpans")
    if not isinstance(scopes, list) or len(scopes) != 1:
        raise InputMismatch("unexpected_scope_count")
    return _span_attributes(entry.get("resource", {})), scopes[0].get("spans", [])


def prepare(destination: Path) -> dict:
    """Return the independent expectation after creating an isolated Source tree."""
    destination = destination.resolve()
    if destination.exists():
        raise InputMismatch("destination_already_exists")
    if destination == REPO or REPO.is_relative_to(destination):
        raise InputMismatch("destination_contains_checkout")
    _verify_evidence_files()

    witness = _json(EXPECTATION)
    candidate = _json(EVIDENCE / "candidate-42821429094-all-completed.json")
    capture = _json(EVIDENCE / "otel-42821429094-all-completed.json")
    resource, spans = _span_records(capture)

    run = witness["run"]
    expected_run = f'{run["run_id"]}/{run["attempt"]}'
    facts = candidate["facts"]
    claims = candidate["claims"]
    coverage = candidate["coverage"]
    if (
        candidate.get("source") != "github_api"
        or facts.get("run_id") != run["run_id"]
        or facts.get("run_attempt") != run["attempt"]
        or facts.get("head_sha") != run["head_sha"]
        or facts.get("repository") != run["repository"]
        or coverage.get("job_selection") != "all_completed"
        or coverage.get("completed_jobs_total") != len(witness["jobs"])
        or coverage.get("task_spans_emitted") != len(witness["jobs"])
        or coverage.get("tasks_omitted") != []
        or claims.get("github_authenticated") is not False
        or claims.get("otlp_delivered") is not False
        or claims.get("candidate_record_only") is not True
        or claims.get("weaver_pass") is not False
        or claims.get("otlp_json_exported") is not True
        or resource.get("cicd.pipeline.run.id", {}).get("stringValue") != expected_run
        or resource.get("github.actions.run_attempt", {}).get("intValue") != str(run["attempt"])
        or resource.get("vcs.ref.head.revision", {}).get("stringValue") != run["head_sha"]
        or resource.get("github.repository", {}).get("stringValue") != run["repository"]
    ):
        raise InputMismatch("candidate_or_resource_provenance_mismatch")

    if len(spans) != len(witness["jobs"]):
        raise InputMismatch("task_inventory_count_mismatch")
    seen = set()
    trace_ids = set()
    expected_spans = []
    for span in spans:
        attrs = _span_attributes(span)
        job_id = attrs.get("cicd.pipeline.task.run.id", {}).get("stringValue")
        if job_id in seen:
            raise InputMismatch("duplicate_task_identity")
        seen.add(job_id)
        job = next((j for j in witness["jobs"] if j["job_id"] == job_id), None)
        if job is None:
            raise InputMismatch("unexpected_task_identity")
        if (
            span.get("name") != job["name"]
            or span.get("spanId") != job["span_id"]
            or attrs.get("cicd.pipeline.task.run.url.full", {}).get("stringValue") != job["url"]
            or attrs.get("github.actions.run_attempt", {}).get("intValue") != str(run["attempt"])
            or span.get("kind") != 1
            or not span.get("startTimeUnixNano")
            or not span.get("endTimeUnixNano")
            or int(span["endTimeUnixNano"]) < int(span["startTimeUnixNano"])
        ):
            raise InputMismatch("task_identity_or_attributes_mismatch")
        trace_ids.add(span["traceId"])
        expected_spans.append({
            "trace_id": span["traceId"],
            "span_id": span["spanId"],
            "matcher": "eci.task",
            "signal": "cicd.pipeline.task.internal",
        })
    if seen != {j["job_id"] for j in witness["jobs"]} or len(trace_ids) != 1:
        raise InputMismatch("expected_task_missing_or_trace_mismatch")
    if any(not s["name"].startswith("task:") for s in spans):
        raise InputMismatch("non_task_span_in_task_only_capture")

    cfg = tomllib.loads((FIXTURE / "ci-weaver.toml").read_text(encoding="utf-8"))
    if cfg["origin"]["commit"] != SOURCE_COMMIT or cfg["origin"]["version"] != "1.45.0":
        raise InputMismatch("registry_upstream_pin_mismatch")
    registry = FIXTURE / cfg["registry"]
    manifest = _json(FIXTURE / cfg["registry_manifest"])
    present = {
        p.relative_to(registry).as_posix(): _hash(p)
        for p in registry.rglob("*") if p.is_file() and not p.is_symlink()
    }
    if present != manifest or any(p.is_symlink() for p in registry.rglob("*")):
        raise InputMismatch("registry_manifest_mismatch")

    # Do not change, repackage or duplicate the canonical Registry in git.
    # The materialized Directory is disposable local test input for Dagger.
    shutil.copytree(FIXTURE, destination, symlinks=False)
    try:
        (destination / "capture.json").write_text(
            json.dumps(capture, indent=2) + "\n", encoding="utf-8"
        )
        (destination / "expected.json").write_text(
            json.dumps({
                "schema_version": 1,
                "source": {"run_id": expected_run, "attempt": run["attempt"]},
                "spans": expected_spans,
            }, indent=2) + "\n", encoding="utf-8"
        )
        matcher_path = destination / ".weaver.toml"
        matcher = matcher_path.read_text(encoding="utf-8")
        old = 'when = \'name == "eci task"\''
        if matcher.count(old) != 1:
            raise InputMismatch("unexpected_fixture_matcher")
        matcher_path.write_text(
            matcher.replace(old, "when = 'name.startsWith(\"task:\")'"),
            encoding="utf-8",
        )
    except Exception:
        shutil.rmtree(destination)
        raise
    return {"run": expected_run, "tasks": len(expected_spans),
            "source": str(destination), "status": "prepared_not_executed",
            "trusted": False, "weaver_pass": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.destination), sort_keys=True))
    except (InputMismatch, OSError, ValueError, KeyError) as error:
        parser.exit(1, f"source preparation rejected: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
