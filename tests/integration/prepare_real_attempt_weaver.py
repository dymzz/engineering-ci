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
REGISTERED_EVIDENCE = REPO / "tests/fixtures/registered-attempt"
REGISTERED_EXPECTATION = (
    REPO / "tests/integration/real-attempt-registered-attributes.json"
)
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


def _case_paths(case: str | None) -> tuple[Path, Path]:
    if case == "registered-attributes":
        return REGISTERED_EVIDENCE, REGISTERED_EXPECTATION
    if case in {None, "original"}:
        return EVIDENCE, EXPECTATION
    raise InputMismatch("unknown_case")


def _evidence(witness: dict, directory: Path | None = None) -> dict:
    """Hash and parse the same bytes against the separately committed witness."""
    directory = EVIDENCE if directory is None else directory
    sums = {}
    for line in (directory / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split(maxsplit=1)
        name = name.strip().lstrip("*")
        if name in sums or "/" in name or "\\" in name or name in {".", ".."}:
            raise InputMismatch("invalid_evidence_manifest")
        sums[name] = digest
    pinned = witness["archive"]["files"]
    if set(pinned) != set(sums):
        raise InputMismatch("original_evidence_inventory_mismatch")
    roles = witness["archive"].get("roles")
    if roles and (
        set(roles.values()) != set(pinned) or len(set(roles.values())) != len(roles)
    ):
        raise InputMismatch("invalid_evidence_roles")
    result = {}
    for name, digest in pinned.items():
        raw = (directory / name).read_bytes()
        if sums[name] != digest or hashlib.sha256(raw).hexdigest() != digest:
            raise InputMismatch("original_evidence_digest_mismatch")
        role = (
            next(key for key, file in roles.items() if file == name)
            if roles
            else name.split("-", 1)[0]
        )
        result[role] = {"raw": raw, "document": json.loads(raw)}
    return result


def _source_witness(witness: dict, candidate: dict, archived: dict) -> None:
    """Reconcile already pinned #75 source objects, without fetching/encoding."""
    if "producer" not in witness:
        return
    source = archived["api"]["document"]
    run, expected = source["attempt"], witness["run"]
    repository = run.get("repository") or {}
    if (
        str(run.get("id")) != expected["run_id"]
        or run.get("run_attempt") != expected["attempt"]
        or run.get("head_sha") != expected["head_sha"]
        or repository.get("full_name") != expected["repository"]
        or repository.get("html_url") != expected["repository_url"]
    ):
        raise InputMismatch("api_witness_identity_mismatch")
    pages = source["job_pages"]
    # This fixed source has three completed Jobs and no omissions/noncompleted
    # Jobs. Pagination itself remains the Producer's existing responsibility.
    jobs = [job for page in pages for job in page["jobs"]]
    expected_jobs = {job["job_id"]: job for job in witness["jobs"]}
    if Counter(str(job.get("id")) for job in jobs) != Counter(expected_jobs.keys()):
        raise InputMismatch("api_witness_job_inventory_mismatch")
    for job in jobs:
        wanted = expected_jobs[str(job["id"])]
        if (
            job.get("status") != "completed"
            or job.get("html_url") != wanted["url"]
            or "task:" + job.get("name", "") != wanted["name"]
            or job.get("conclusion") != wanted["result"]
            or str(cicd_telemetry.parse_time_ns(job.get("started_at")))
            != wanted["start_time_unix_nano"]
            or str(cicd_telemetry.parse_time_ns(job.get("completed_at")))
            != wanted["end_time_unix_nano"]
        ):
            raise InputMismatch("api_witness_job_attributes_mismatch")
    receipt, producer = archived["receipt"]["document"], witness["producer"]
    if (
        receipt.get("producer_commit") != producer["source_commit"]
        or receipt.get("producer_source_sha256") != producer["source_sha256"]
        or receipt.get("producer_schema") != producer["schema_version"]
        or archived["producer_exit"]["document"] != producer["exit_code"]
        or any(
            receipt["files_sha256"].get(witness["archive"]["roles"][role])
            != hashlib.sha256(archived[role]["raw"]).hexdigest()
            for role in ("candidate", "otel", "api")
        )
        or candidate["claims"].get("trusted") is not False
    ):
        raise InputMismatch("producer_source_receipt_mismatch")


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
    if (
        "producer" in witness
        and witness["producer"]["id_scheme"] != cicd_telemetry.ID_SCHEME
    ):
        raise InputMismatch("producer_id_scheme_mismatch")
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


def _native_expectations(witness: dict) -> list[dict]:
    return [
        {
            "trace_id": item["trace_id"],
            "span_id": item["span_id"],
            "matcher": TASK_MATCHER,
            "signal": TASK_SIGNAL,
        }
        for item in _expected_tasks(witness)
    ]


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
        "scope": "complete_task_set_for_pinned_attempt",
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
            "run_id": attrs.get("cicd.pipeline.run.id", {}).get("stringValue")
            == f"{witness['run']['run_id']}/{witness['run']['attempt']}",
        }
        if "producer" not in witness:
            fields["attempt"] = attrs.get("github.actions.run_attempt", {}).get(
                "intValue"
            ) == str(witness["run"]["attempt"])
        for field, actual in (
            ("start_time_unix_nano", span.get("startTimeUnixNano")),
            ("end_time_unix_nano", span.get("endTimeUnixNano")),
        ):
            if field in wanted:
                fields[field] = actual == wanted[field]
        if "result" in wanted:
            fields["result"] = (
                attrs.get("cicd.pipeline.task.run.result", {}).get("stringValue")
                == wanted["result"]
            )
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
    registered = "producer" in witness
    repository_key = "vcs.repository.url.full" if registered else "github.repository"
    repository_value = run["repository_url"] if registered else run["repository"]
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
        or (
            not registered
            and resource.get("github.actions.run_attempt", {}).get("intValue")
            != str(run["attempt"])
        )
        or resource.get("vcs.ref.head.revision", {}).get("stringValue")
        != run["head_sha"]
        or resource.get(repository_key, {}).get("stringValue") != repository_value
    ):
        raise InputMismatch("candidate_or_resource_provenance_mismatch")
    if registered and (
        candidate.get("schema_version") != witness["producer"]["schema_version"]
        or candidate["producer"].get("attribute_contract")
        != witness["producer"]["attribute_contract"]
        or candidate["producer"].get("id_scheme") != witness["producer"]["id_scheme"]
        or candidate.get("repository_identity", {}).get("url") != repository_value
        or facts.get("repository_url") != repository_value
        or any(
            claims.get(key) is not False
            for key in ("rpc_accepted", "receiver_observed", "trusted")
        )
    ):
        raise InputMismatch("registered_producer_contract_mismatch")
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


def prepare(destination: Path, *, case: str | None = None) -> dict:
    """Materialize original bytes and independent expectations, without execution."""
    destination = destination.resolve()
    if destination.exists():
        raise InputMismatch("destination_already_exists")
    if destination == REPO or REPO.is_relative_to(destination):
        raise InputMismatch("destination_contains_checkout")
    config = tomllib.loads((FIXTURE / "ci-weaver.toml").read_text(encoding="utf-8"))
    official = _registry_identity(config)
    directory, expectation = _case_paths(case)
    witness = _json(expectation)
    if any(
        witness["upstream"].get(key) != official[key]
        for key in ("repository", "commit", "version")
    ):
        raise InputMismatch("witness_registry_identity_mismatch")
    archived = _evidence(witness, directory)
    candidate, capture = archived["candidate"]["document"], archived["otel"]["document"]
    _source_witness(witness, candidate, archived)
    inventory = validate_attempt(witness, candidate, capture)
    expected = {
        "schema_version": 1,
        "source": {
            **witness["run"],
            "run_id": f"{witness['run']['run_id']}/{witness['run']['attempt']}",
        },
        "spans": _native_expectations(witness),
        "provenance": {
            "archive": witness["archive"],
            "witness_sha256": _hash(expectation),
            "producer_claims": candidate["claims"],
            "official_registry": official,
        },
    }
    shutil.copytree(FIXTURE, destination, symlinks=False)
    try:
        (destination / "capture.json").write_bytes(archived["otel"]["raw"])
        _write_json(destination / "expected.json", expected)
        _write_json(destination / "input-inventory.json", inventory)
        (destination / "attempt-witness.json").write_bytes(expectation.read_bytes())
        (destination / "producer-candidate.json").write_bytes(
            archived["candidate"]["raw"]
        )
        for role, filename in (
            ("api", "producer-api-witness.json"),
            ("receipt", "producer-receipt.json"),
            ("producer_exit", "producer.exit"),
        ):
            if role in archived:
                (destination / filename).write_bytes(archived[role]["raw"])
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
    source: Path,
    artifacts: Path,
    plan_report: Path | None = None,
    *,
    case: str | None = None,
) -> dict:
    """Inspect existing artifacts; never execute or reinterpret native policies."""
    config = tomllib.loads((source / "ci-weaver.toml").read_text(encoding="utf-8"))
    expected = _json(source / config["expectations"])
    directory, expectation = _case_paths(case)
    witness = _json(expectation)
    inventory = validate_attempt(
        witness,
        _json(source / "producer-candidate.json"),
        _json(source / config["otlp"]),
    )
    if (
        source / "attempt-witness.json"
    ).read_bytes() != expectation.read_bytes() or expected["provenance"][
        "witness_sha256"
    ] != _hash(expectation):
        raise InputMismatch("result_witness_mismatch")
    if (
        expected.get("source")
        != {
            **witness["run"],
            "run_id": f"{witness['run']['run_id']}/{witness['run']['attempt']}",
        }
        or expected.get("spans") != _native_expectations(witness)
        or expected["provenance"].get("archive") != witness["archive"]
    ):
        raise InputMismatch("result_expectation_mismatch")
    archived = _evidence(witness, directory)
    for role, filename in (
        ("candidate", "producer-candidate.json"),
        ("otel", config["otlp"]),
        ("api", "producer-api-witness.json"),
        ("receipt", "producer-receipt.json"),
        ("producer_exit", "producer.exit"),
    ):
        if (
            role in archived
            and (source / filename).read_bytes() != archived[role]["raw"]
        ):
            raise InputMismatch("result_producer_bytes_mismatch")
    _source_witness(witness, archived["candidate"]["document"], archived)
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
    closure = selection.compute_weaver_input_closure(source, "ci-weaver.toml")
    if (
        report.get("tool") != "weaver"
        or report.get("version") != closure["weaver_tool"]["version"]
        or report.get("tool_asset_sha256") != closure["weaver_tool"]["sha256"]
    ):
        raise InputMismatch("native_tool_identity_mismatch")
    if (
        closure["registry"]["sha256"] != pin["tree_sha256"]
        or report["inputs"].get("registry", {}).get("files")
        != closure["registry"]["files"]
        or report["inputs"]["registry"].get("manifest_sha256")
        != closure["registry"]["manifest"]["sha256"]
        # Native and Selection currently use different aggregate JSON encodings.
        # Their exact per-file hashes and paths are the common byte identity;
        # retain both aggregate digests without pretending they are equal.
        or any(
            report["inputs"].get("advice_policies", {}).get(key)
            != closure["advice_policies"][key]
            for key in ("path", "files")
        )
    ):
        raise InputMismatch("native_source_closure_mismatch")
    advice_coverage = coverage.get("conditionally_required_advice") or {}
    if (
        report.get("eci_policy_evaluated") is not True
        or advice_coverage.get("status") != "executed"
        or advice_coverage.get("loaded") is not True
        or advice_coverage.get("digest")
        != report["inputs"]["advice_policies"]["sha256"]
    ):
        raise InputMismatch("native_policy_coverage_unverified")
    if not report.get("source_digest") or report.get("source") != expected["source"]:
        raise InputMismatch("native_source_identity_mismatch")
    native = _json(artifacts / "live_check.json")
    if native != report["native_report"]:
        raise InputMismatch("native_artifact_mismatch")
    if "producer" in witness:
        resources = [
            sample["resource"] for sample in native["samples"] if "resource" in sample
        ]
        captured_resource, _ = _span_records(_json(source / config["otlp"]))
        if len(resources) != 1 or {
            entry["name"]: entry["value"] for entry in resources[0]["attributes"]
        } != {
            key: value.get("stringValue") for key, value in captured_resource.items()
        }:
            raise InputMismatch("native_resource_identity_mismatch")
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
            or attributes.get("cicd.pipeline.run.id")
            != f"{witness['run']['run_id']}/{witness['run']['attempt']}"
            or (
                "producer" not in witness
                and attributes.get("github.actions.run_attempt")
                != witness["run"]["attempt"]
            )
        ):
            raise InputMismatch("native_task_attributes_mismatch")
        if "producer" in witness and (
            item.get("parent_span_id") != task["parent_span_id"]
            or item.get("kind") != "internal"
            or str(cicd_telemetry.parse_time_ns(item.get("start_time")))
            != task["start_time_unix_nano"]
            or str(cicd_telemetry.parse_time_ns(item.get("end_time")))
            != task["end_time_unix_nano"]
            or attributes.get("cicd.pipeline.task.run.result") != task["result"]
        ):
            raise InputMismatch("native_task_source_facts_mismatch")
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
        "scope": "A / Task-only",
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
        "producer_exit_code": archived.get("producer_exit", {}).get("document"),
        "producer_terminal": archived["candidate"]["document"].get("terminal"),
        "terminal_check": "not_executed",
        "producer_metrics_not_observable": archived["candidate"]["document"]
        .get("signals", {})
        .get("metrics_not_observable", []),
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
    parser.add_argument(
        "--case", choices=("original", "registered-attributes"), default="original"
    )
    args = parser.parse_args()
    if args.verify_source and not args.artifacts:
        parser.error("--verify-source requires --artifacts")
    try:
        result = (
            prepare(args.destination, case=args.case)
            if args.destination
            else verify_report(
                args.verify_source, args.artifacts, args.plan_report, case=args.case
            )
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
