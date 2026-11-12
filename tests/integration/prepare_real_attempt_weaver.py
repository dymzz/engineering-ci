"""Prepare and inspect #52's fixed Task-only Producer -> native Weaver case.

Dagger owns execution. This consumer binds the existing #75 archive and
independent Job witness to #74 inputs and inspects the exported native result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tomllib
from collections import Counter
from pathlib import Path

from engineering_ci import cicd_telemetry, selection, semconv_registry

REPO = Path(__file__).resolve().parents[2]
EVIDENCE = REPO / "docs/verification/cicd-telemetry-evidence-all-completed"
FIXTURE = REPO / "tests/fixtures/weaver-cicd"
EXPECTATION = REPO / "tests/integration/real-attempt-42821429094.json"
TASK_MATCHER = "eci.task"
TASK_SIGNAL = "cicd.pipeline.task.internal"


class InputMismatch(ValueError):
    """Typed input/result refusal, with per-object diagnostics when available."""

    def __init__(self, code, *, inventory=None, native=None):
        super().__init__(code)
        self.code = code
        self.inventory = inventory
        self.native = native


def _json(path: Path) -> dict:
    return json.loads(path.read_bytes())


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8", newline="\n")


def _evidence(witness: dict) -> dict:
    """Hash and parse the same bytes against the separately committed witness."""
    sums = {}
    for line in (EVIDENCE / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split(maxsplit=1)
        name = name.strip().lstrip("*")
        if name in sums or "/" in name or "\\" in name or name in {".", ".."}:
            raise InputMismatch("invalid_evidence_manifest")
        sums[name] = digest
    pinned = witness["archive"]["files"]
    if set(pinned) != set(sums):
        raise InputMismatch("original_evidence_inventory_mismatch")
    result = {}
    for name, digest in pinned.items():
        raw = (EVIDENCE / name).read_bytes()
        if sums[name] != digest or hashlib.sha256(raw).hexdigest() != digest:
            raise InputMismatch("original_evidence_digest_mismatch")
        result[name.split("-", 1)[0]] = {"raw": raw, "document": json.loads(raw)}
    return result


def _registry_identity(config: dict) -> dict:
    """Consume #74's pin outside the replay Source, without another authority."""
    pin = semconv_registry.RECORD
    for key in ("repository", "commit", "version"):
        if (config.get("origin") or {}).get(key) != pin[key]:
            raise InputMismatch(f"official_source_{key}_mismatch")
    for key in ("registry", "registry_manifest"):
        if not (FIXTURE / config[key]).resolve().is_relative_to(FIXTURE.resolve()):
            raise InputMismatch("fixture_path_escape")
    if FIXTURE.is_symlink() or any(p.is_symlink() for p in FIXTURE.rglob("*")):
        raise InputMismatch("fixture_symlink_rejected")
    registry = FIXTURE / config["registry"]
    present = {
        p.relative_to(registry).as_posix(): _hash(p)
        for p in registry.rglob("*")
        if p.is_file()
    }
    if present != _json(FIXTURE / config["registry_manifest"]):
        raise InputMismatch("registry_manifest_mismatch")
    if semconv_registry.registry_tree_digest(present) != pin["tree_sha256"]:
        raise InputMismatch("official_registry_bytes_mismatch")
    return {**pin, "official_registry_verified": True}


def _span_attributes(span: dict) -> dict:
    result = {}
    for item in span.get("attributes", []):
        key = item["key"]
        if key in result:
            raise InputMismatch("duplicate_otlp_attribute")
        result[key] = item["value"]
    return result


def _span_records(capture: dict) -> tuple[dict, list[dict]]:
    resources = capture.get("resourceSpans")
    if not isinstance(resources, list) or len(resources) != 1:
        raise InputMismatch("unexpected_resource_count")
    scopes = resources[0].get("scopeSpans")
    if not isinstance(scopes, list) or len(scopes) != 1:
        raise InputMismatch("unexpected_scope_count")
    spans = scopes[0].get("spans", [])
    if not isinstance(spans, list):
        raise InputMismatch("invalid_task_inventory")
    return _span_attributes(resources[0].get("resource", {})), spans


def _expected_tasks(witness: dict) -> list[dict]:
    run = witness["run"]
    trace, parent, _ = cicd_telemetry._stable_ids(run["run_id"], run["attempt"])
    if trace != run["trace_id"] or parent != run["pipeline_span_id"]:
        raise InputMismatch("expected_trace_witness_mismatch")
    jobs = witness["jobs"]
    if not jobs or len({j["job_id"] for j in jobs}) != len(jobs):
        raise InputMismatch("expected_job_inventory_invalid")
    expected = []
    for job in jobs:
        _, _, span = cicd_telemetry._stable_ids(
            run["run_id"], run["attempt"], job["job_id"]
        )
        if span != job["span_id"]:
            raise InputMismatch("expected_span_witness_mismatch")
        expected.append({**job, "trace_id": trace, "parent_span_id": parent})
    return expected


def task_inventory(witness: dict, capture: dict) -> dict:
    """Compare raw OTLP identities to the independent Job/Producer mapping."""
    expected = _expected_tasks(witness)
    _, spans = _span_records(capture)
    by_job = {item["job_id"]: item for item in expected}
    observed = [(span, _span_attributes(span)) for span in spans]
    job_ids = [
        attrs.get("cicd.pipeline.task.run.id", {}).get("stringValue")
        for _, attrs in observed
    ]
    counts = Counter(job_ids)
    inventory = {
        "expected_count": len(expected),
        "observed_count": len(spans),
        "matched": [],
        "missing": [],
        "unexpected": [],
        "duplicate": [],
        "mismatched": [],
    }
    for job_id, count in counts.items():
        if count > 1:
            inventory["duplicate"].append({"job_id": job_id, "count": count})
    for index, ((span, attrs), job_id) in enumerate(
        zip(observed, job_ids, strict=True)
    ):
        wanted = by_job.get(job_id)
        identity = {
            "job_id": job_id,
            "trace_id": span.get("traceId"),
            "span_id": span.get("spanId"),
        }
        if wanted is None:
            inventory["unexpected"].append({**identity, "index": index})
            continue
        fields = {
            "trace_id": span.get("traceId") == wanted["trace_id"],
            "span_id": span.get("spanId") == wanted["span_id"],
            "parent_span_id": span.get("parentSpanId") == wanted["parent_span_id"],
            "name": span.get("name") == wanted["name"],
            "kind": span.get("kind") == 1,
            "url": attrs.get("cicd.pipeline.task.run.url.full", {}).get("stringValue")
            == wanted["url"],
            "attempt": attrs.get("github.actions.run_attempt", {}).get("intValue")
            == str(witness["run"]["attempt"]),
            "run_id": attrs.get("cicd.pipeline.run.id", {}).get("stringValue")
            == f"{witness['run']['run_id']}/{witness['run']['attempt']}",
        }
        try:
            start, end = int(span["startTimeUnixNano"]), int(span["endTimeUnixNano"])
            fields["completed_time"] = 0 < start <= end
        except (ValueError, TypeError, KeyError):
            fields["completed_time"] = False
        bad = [name for name, ok in fields.items() if not ok]
        if bad:
            inventory["mismatched"].append({**identity, "fields": bad})
        elif counts[job_id] == 1:
            inventory["matched"].append(identity)
    matched = {item["job_id"] for item in inventory["matched"]}
    inventory["missing"] = [item for item in expected if item["job_id"] not in matched]
    return inventory


def validate_attempt(witness: dict, candidate: dict, capture: dict) -> dict:
    run = witness["run"]
    resource, _ = _span_records(capture)
    facts, claims, coverage = (
        candidate["facts"],
        candidate["claims"],
        candidate["coverage"],
    )
    if (
        candidate.get("source") != "github_api"
        or any(
            facts.get(key) != run[key] for key in ("run_id", "head_sha", "repository")
        )
        or facts.get("run_attempt") != run["attempt"]
        or coverage.get("job_selection") != "all_completed"
        or coverage.get("completed_jobs_total") != len(witness["jobs"])
        or coverage.get("task_spans_emitted") != len(witness["jobs"])
        or coverage.get("tasks_omitted") != []
        or any(
            claims.get(key) is not False
            for key in ("github_authenticated", "otlp_delivered", "weaver_pass")
        )
        or any(
            claims.get(key) is not True
            for key in (
                "github_api_fetched",
                "candidate_record_only",
                "otlp_json_exported",
            )
        )
        or resource.get("cicd.pipeline.run.id", {}).get("stringValue")
        != f"{run['run_id']}/{run['attempt']}"
        or resource.get("github.actions.run_attempt", {}).get("intValue")
        != str(run["attempt"])
        or resource.get("vcs.ref.head.revision", {}).get("stringValue")
        != run["head_sha"]
        or resource.get("github.repository", {}).get("stringValue") != run["repository"]
    ):
        raise InputMismatch("candidate_or_resource_provenance_mismatch")
    inventory = task_inventory(witness, capture)
    for category, reason in (
        ("duplicate", "duplicate_task_identity"),
        ("unexpected", "unexpected_task_identity"),
        ("mismatched", "task_identity_or_attributes_mismatch"),
        ("missing", "expected_task_missing"),
    ):
        if inventory[category]:
            raise InputMismatch(reason, inventory=inventory)
    return inventory


def prepare(destination: Path) -> dict:
    """Materialize original bytes and independent expectations, without execution."""
    destination = destination.resolve()
    if destination.exists():
        raise InputMismatch("destination_already_exists")
    if destination == REPO or REPO.is_relative_to(destination):
        raise InputMismatch("destination_contains_checkout")
    config = tomllib.loads((FIXTURE / "ci-weaver.toml").read_text(encoding="utf-8"))
    official = _registry_identity(config)
    witness = _json(EXPECTATION)
    if any(
        witness["upstream"].get(key) != official[key]
        for key in ("repository", "commit", "version")
    ):
        raise InputMismatch("witness_registry_identity_mismatch")
    archived = _evidence(witness)
    candidate, capture = archived["candidate"]["document"], archived["otel"]["document"]
    inventory = validate_attempt(witness, candidate, capture)
    expected = {
        "schema_version": 1,
        "source": {
            **witness["run"],
            "run_id": f"{witness['run']['run_id']}/{witness['run']['attempt']}",
        },
        "spans": [
            {
                "trace_id": item["trace_id"],
                "span_id": item["span_id"],
                "matcher": TASK_MATCHER,
                "signal": TASK_SIGNAL,
            }
            for item in _expected_tasks(witness)
        ],
        "provenance": {
            "archive": witness["archive"],
            "witness_sha256": _hash(EXPECTATION),
            "producer_claims": candidate["claims"],
            "official_registry": official,
        },
    }
    shutil.copytree(FIXTURE, destination, symlinks=False)
    try:
        (destination / "capture.json").write_bytes(archived["otel"]["raw"])
        _write_json(destination / "expected.json", expected)
        _write_json(destination / "input-inventory.json", inventory)
        (destination / "attempt-witness.json").write_bytes(EXPECTATION.read_bytes())
        (destination / "producer-candidate.json").write_bytes(
            archived["candidate"]["raw"]
        )
        matcher_path = destination / config["matcher_config"]
        matcher = matcher_path.read_text(encoding="utf-8")
        old = "when = 'name == \"eci task\"'"
        if matcher.count(old) != 1:
            raise InputMismatch("unexpected_fixture_matcher")
        matcher_path.write_text(
            matcher.replace(old, "when = 'name.startsWith(\"task:\")'"),
            encoding="utf-8",
            newline="\n",
        )
        config_path = destination / "ci-weaver.toml"
        text = config_path.read_text(encoding="utf-8")
        old_matchers = 'expected_matchers = ["eci.pipeline", "eci.task"]'
        if text.count(old_matchers) != 1:
            raise InputMismatch("unexpected_fixture_expected_matchers")
        config_path.write_text(
            text.replace(old_matchers, 'expected_matchers = ["eci.task"]'),
            encoding="utf-8",
            newline="\n",
        )
    except Exception:
        shutil.rmtree(destination)
        raise
    return {
        "run": expected["source"],
        "tasks": len(expected["spans"]),
        "source": str(destination),
        "inventory": inventory,
        "official_registry": official,
        "status": "prepared_not_executed",
        "checked_signals": ["traces"],
        "trusted": False,
        "admitted": False,
        "required": False,
        "weaver_pass": False,
    }


def verify_report(
    source: Path, artifacts: Path, plan_report: Path | None = None
) -> dict:
    """Inspect existing artifacts; never execute or reinterpret native policies."""
    config = tomllib.loads((source / "ci-weaver.toml").read_text(encoding="utf-8"))
    expected = _json(source / config["expectations"])
    witness = _json(EXPECTATION)
    inventory = validate_attempt(
        witness,
        _json(source / "producer-candidate.json"),
        _json(source / config["otlp"]),
    )
    if (
        source / "attempt-witness.json"
    ).read_bytes() != EXPECTATION.read_bytes() or expected["provenance"][
        "witness_sha256"
    ] != _hash(EXPECTATION):
        raise InputMismatch("result_witness_mismatch")
    report = _json(artifacts / "report.json")
    if (
        report.get("name") != "weaver_live"
        or report.get("status") != "passed"
        or report.get("gate_exit_code") != 0
        or report.get("exit_code") != 0
    ):
        raise InputMismatch(
            "native_check_not_passed",
            inventory=inventory,
            native={
                key: report.get(key)
                for key in (
                    "name",
                    "status",
                    "reason",
                    "exit_code",
                    "gate_exit_code",
                    "source_digest",
                )
            },
        )
    coverage = report.get("coverage") or {}
    pin = semconv_registry.RECORD
    if (
        report.get("official_registry_verified") is not True
        or coverage.get("official_registry_verified") is not True
        or any(
            coverage.get(key) != pin[field]
            for key, field in (
                ("registry_version", "version"),
                ("registry_commit", "commit"),
                ("official_registry_tree_sha256", "tree_sha256"),
            )
        )
    ):
        raise InputMismatch("native_official_source_unverified")
    for key, path in (
        ("config_sha256", "ci-weaver.toml"),
        ("matcher_sha256", config["matcher_config"]),
        ("expectations_sha256", config["expectations"]),
        ("otlp_sha256", config["otlp"]),
    ):
        if report["inputs"].get(key) != _hash(source / path):
            raise InputMismatch("native_input_digest_mismatch")
    if not report.get("source_digest") or report.get("source") != expected["source"]:
        raise InputMismatch("native_source_identity_mismatch")
    native = _json(artifacts / "live_check.json")
    if native != report["native_report"]:
        raise InputMismatch("native_artifact_mismatch")
    received = [sample["span"] for sample in native["samples"] if "span" in sample]
    tasks = _expected_tasks(witness)
    by_span = {item["span_id"]: item for item in tasks}
    wanted = Counter((item["trace_id"], item["span_id"]) for item in tasks)
    if (
        Counter((item.get("trace_id"), item.get("span_id")) for item in received)
        != wanted
    ):
        raise InputMismatch("native_task_inventory_mismatch")
    for item in received:
        task = by_span[item["span_id"]]
        attributes = {
            entry["name"]: entry["value"] for entry in item.get("attributes", [])
        }
        if (
            item.get("name") != task["name"]
            or attributes.get("cicd.pipeline.task.run.id") != task["job_id"]
            or attributes.get("cicd.pipeline.task.run.url.full") != task["url"]
            or attributes.get("github.actions.run_attempt") != witness["run"]["attempt"]
        ):
            raise InputMismatch("native_task_attributes_mismatch")
        match = item["live_check_result"]["match_info"]
        if (
            match.get("signal_matcher") != TASK_MATCHER
            or match.get("signal") != TASK_SIGNAL
            or match.get("unmatched") is not False
        ):
            raise InputMismatch("native_task_match_mismatch")
    commands = report["commands"]
    if sum("live-check" in item["argv"] for item in commands) != 1:
        raise InputMismatch("native_execution_count_mismatch")
    for command in commands:
        for key in ("stdout", "stderr"):
            if not (artifacts / command[key]).is_file():
                raise InputMismatch("native_artifact_missing")
    if report.get("trusted") is not False:
        raise InputMismatch("unsupported_trust_upgrade")
    plan_state = "not_requested"
    if plan_report is not None:
        plan = _json(plan_report)
        checks = [
            item for item in plan.get("checks", []) if item.get("name") == "weaver_live"
        ]
        if (
            plan.get("status") != "passed"
            or plan.get("source_tree_verified") != "verified"
            or plan.get("source_digest") != selection.directory_content_id(source)
            or len(checks) != 1
            or any(checks[0].get(key) != value for key, value in report.items())
        ):
            raise InputMismatch("plan_report_binding_mismatch")
        plan_state = "verified"
    return {
        "status": "passed",
        "check_id": report["name"],
        "exit_code": report["exit_code"],
        "gate_exit_code": report["gate_exit_code"],
        "source_digest": report["source_digest"],
        "inventory": inventory,
        "official_registry_verified": True,
        "registry_commit": coverage["registry_commit"],
        "registry_tree_sha256": coverage["official_registry_tree_sha256"],
        "eci_policy_evaluated": report["eci_policy_evaluated"],
        "producer_claims": _json(source / "producer-candidate.json")["claims"],
        "plan_binding": plan_state,
        "checked_signals": ["traces"],
        "trusted": False,
        "admitted": False,
        "required": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--destination", type=Path)
    action.add_argument("--verify-source", type=Path)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--plan-report", type=Path)
    args = parser.parse_args()
    if args.verify_source and not args.artifacts:
        parser.error("--verify-source requires --artifacts")
    try:
        result = (
            prepare(args.destination)
            if args.destination
            else verify_report(args.verify_source, args.artifacts, args.plan_report)
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (InputMismatch, OSError, ValueError, KeyError, TypeError) as error:
        print(
            json.dumps(
                {
                    "status": "rejected",
                    "reason": str(error),
                    "inventory": getattr(error, "inventory", None),
                    "native": getattr(error, "native", None),
                    "trusted": False,
                },
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
