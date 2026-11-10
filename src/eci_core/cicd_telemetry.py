"""GitHub Actions Run/Job facts → OTel CI/CD Pipeline/Task candidate records.

Producer decision (ECI #75 / Knowledge PR #79 C/O2):
- Source of truth: separate Run and Job facts (env or explicit mapping/API).
- Emits candidate Pipeline/Task span records for a JSON artifact only.
- Does not prove OTLP delivery, Weaver PASS, or authenticated GitHub provenance.
- Does not instrument Dagger Functions.
- Missing identity, terminal results, required SemConv fields, or real timestamps
  yield incomplete/partial reports — never forged complete spans.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 3
REPORT_KIND = "engineering-ci-cicd-telemetry"
EVIDENCE_CLASS = "candidate_record"
OTLP_SCOPE_NAME = "eci_core.cicd_telemetry"
# OTLP SpanKind numeric values.
SPAN_KIND_INTERNAL = 1
SPAN_KIND_SERVER = 2
# OTLP StatusCode.
STATUS_UNSET = 0
STATUS_OK = 1
STATUS_ERROR = 2

# Public CI/CD SemConv attribute names (registry pin tracked with Knowledge #80).
ATTR_PIPELINE_NAME = "cicd.pipeline.name"
ATTR_PIPELINE_RUN_ID = "cicd.pipeline.run.id"
ATTR_PIPELINE_RESULT = "cicd.pipeline.result"
ATTR_TASK_NAME = "cicd.pipeline.task.name"
ATTR_TASK_RUN_ID = "cicd.pipeline.task.run.id"
ATTR_TASK_RUN_URL = "cicd.pipeline.task.run.url.full"
ATTR_TASK_RUN_RESULT = "cicd.pipeline.task.run.result"
ATTR_ERROR_TYPE = "error.type"
ATTR_VCS_HEAD_REVISION = "vcs.ref.head.revision"

# GitHub-native fact until the fixed SemConv registry confirms an official key.
ATTR_GITHUB_RUN_ATTEMPT = "github.actions.run_attempt"
ATTR_GITHUB_REPOSITORY = "github.repository"

# Justified GitHub conclusion → SemConv cicd.*.result only.
# Native GitHub values remain in facts.*; neutral/action_required/stale stay
# unsupported until a fixed SemConv registry mapping is cited.
_RESULT_MAP = {
    "success": "success",
    "failure": "failure",
    "cancelled": "cancellation",
    "canceled": "cancellation",
    "skipped": "skip",
    "timed_out": "timeout",
}


class TelemetryError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class GitHubRunFacts:
    workflow_name: str | None
    run_id: str | None
    run_attempt: int | None
    job_id: str | None
    job_name: str | None
    job_url: str | None
    run_conclusion: str | None
    job_conclusion: str | None
    head_sha: str | None
    repository: str | None
    server_url: str | None = None
    run_started_at_ns: int | None = None
    run_completed_at_ns: int | None = None
    job_started_at_ns: int | None = None
    job_completed_at_ns: int | None = None

    @property
    def has_run_identity(self):
        return bool(self.workflow_name and self.run_id and self.run_attempt is not None)

    @property
    def has_job_identity(self):
        return bool(self.job_id and self.job_name and self.job_url)

    @property
    def run_bounds_state(self):
        """missing | invalid | ok. Equal start/end is a valid zero-duration bound."""
        if self.run_started_at_ns is None or self.run_completed_at_ns is None:
            return "missing"
        if self.run_started_at_ns > self.run_completed_at_ns:
            return "invalid"
        return "ok"

    @property
    def job_bounds_state(self):
        """missing | invalid | ok. Equal start/end is a valid zero-duration bound."""
        if self.job_started_at_ns is None or self.job_completed_at_ns is None:
            return "missing"
        if self.job_started_at_ns > self.job_completed_at_ns:
            return "invalid"
        return "ok"

    @property
    def has_run_bounds(self):
        return self.run_bounds_state == "ok"

    @property
    def has_job_bounds(self):
        return self.job_bounds_state == "ok"


def _text(mapping, key):
    item = mapping.get(key)
    if item is None:
        return None
    item = str(item).strip()
    return item or None


def parse_time_ns(value):
    """Accept unix-ns int/str or RFC3339; never invent a substitute."""
    if value is None:
        return None
    if type(value) is int:
        return value if value >= 0 else None
    if type(value) is float:
        if value < 0:
            return None
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp() * 1_000_000_000)


def map_github_result(conclusion):
    """Return (semconv_result, error_type_or_none). Unmapped values → None."""
    if conclusion is None:
        return None, None
    result = _RESULT_MAP.get(conclusion)
    if result is None:
        return None, None
    if result in {"failure", "error"}:
        return result, conclusion
    return result, None


def run_job_temporal_inconsistency(facts):
    """Detect Job starting/finishing after a completed parent Run.

    Source semantics for this producer: ENGINEERING_CI_RUN_* / mapping fields
    stand for the completed Workflow Run attempt bounds, and ENGINEERING_CI_JOB_*
    for the matching Job `started_at`/`completed_at`. GitHub Jobs belonging to a
    completed Run must not start or finish after that Run's completed_at.
    """
    if facts.run_bounds_state != "ok" or facts.job_bounds_state != "ok":
        return False
    if facts.job_started_at_ns > facts.run_completed_at_ns:
        return True
    return facts.job_completed_at_ns > facts.run_completed_at_ns


def facts_from_actions_env(env=None):
    """Read GitHub Actions runtime facts; missing fields stay None (no forgery)."""
    env = os.environ if env is None else env
    attempt_raw = _text(env, "GITHUB_RUN_ATTEMPT")
    attempt = None
    if attempt_raw is not None:
        try:
            attempt = int(attempt_raw)
        except ValueError:
            attempt = None
    return GitHubRunFacts(
        workflow_name=_text(env, "GITHUB_WORKFLOW"),
        run_id=_text(env, "GITHUB_RUN_ID"),
        run_attempt=attempt,
        job_id=_text(env, "GITHUB_JOB_ID") or _text(env, "ENGINEERING_CI_JOB_ID"),
        job_name=_text(env, "GITHUB_JOB") or _text(env, "ENGINEERING_CI_JOB_NAME"),
        job_url=_text(env, "ENGINEERING_CI_JOB_URL"),
        run_conclusion=_text(env, "ENGINEERING_CI_RUN_CONCLUSION")
        or _text(env, "GITHUB_RUN_CONCLUSION"),
        job_conclusion=_text(env, "ENGINEERING_CI_JOB_CONCLUSION")
        or _text(env, "JOB_CONCLUSION"),
        head_sha=_text(env, "GITHUB_SHA"),
        repository=_text(env, "GITHUB_REPOSITORY"),
        server_url=_text(env, "GITHUB_SERVER_URL") or "https://github.com",
        run_started_at_ns=parse_time_ns(_text(env, "ENGINEERING_CI_RUN_STARTED_AT")),
        run_completed_at_ns=parse_time_ns(
            _text(env, "ENGINEERING_CI_RUN_COMPLETED_AT")
        ),
        job_started_at_ns=parse_time_ns(_text(env, "ENGINEERING_CI_JOB_STARTED_AT")),
        job_completed_at_ns=parse_time_ns(
            _text(env, "ENGINEERING_CI_JOB_COMPLETED_AT")
        ),
    )


def facts_from_mapping(value):
    """Build facts from an explicit JSON/API mapping (untrusted caller input)."""
    if not isinstance(value, dict):
        raise TelemetryError("facts_invalid")
    attempt = value.get("run_attempt")
    if attempt is not None and type(attempt) is not int:
        try:
            attempt = int(attempt)
        except (TypeError, ValueError):
            raise TelemetryError("run_attempt_invalid") from None
    # Run and Job terminals are independent; never copy a shared "conclusion".
    return GitHubRunFacts(
        workflow_name=_text(value, "workflow_name"),
        run_id=_text(value, "run_id"),
        run_attempt=attempt,
        job_id=_text(value, "job_id"),
        job_name=_text(value, "job_name"),
        job_url=_text(value, "job_url"),
        run_conclusion=_text(value, "run_conclusion"),
        job_conclusion=_text(value, "job_conclusion"),
        head_sha=_text(value, "head_sha"),
        repository=_text(value, "repository"),
        server_url=_text(value, "server_url") or "https://github.com",
        run_started_at_ns=parse_time_ns(
            value.get("run_started_at_ns", value.get("run_started_at"))
        ),
        run_completed_at_ns=parse_time_ns(
            value.get("run_completed_at_ns", value.get("run_completed_at"))
        ),
        job_started_at_ns=parse_time_ns(
            value.get("job_started_at_ns", value.get("job_started_at"))
        ),
        job_completed_at_ns=parse_time_ns(
            value.get("job_completed_at_ns", value.get("job_completed_at"))
        ),
    )


def _status_code(result):
    if result == "success":
        return STATUS_OK
    if result in {"failure", "error"}:
        return STATUS_ERROR
    return STATUS_UNSET


def _span(
    name, *, kind, trace_id, span_id, parent_span_id, start_ns, end_ns, attributes
):
    cleaned = {key: value for key, value in attributes.items() if value is not None}
    result = cleaned.get(ATTR_PIPELINE_RESULT) or cleaned.get(ATTR_TASK_RUN_RESULT)
    return {
        "name": name,
        "kind": kind,
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "start_time_unix_nano": start_ns,
        "end_time_unix_nano": end_ns,
        "attributes": cleaned,
        "status": {"code": _status_code(result)},
    }


def _stable_ids(run_id, attempt, job_id=None):
    """Deterministic reconstruction ids; not provenance authentication."""
    pipeline_seed = f"pipeline:{run_id}:{attempt}".encode()
    digest = hashlib.sha256(pipeline_seed).hexdigest()
    trace_id = digest[:32]
    pipeline_span_id = digest[32:48]
    if job_id is None:
        return trace_id, pipeline_span_id, None
    task_seed = f"task:{run_id}:{attempt}:{job_id}".encode()
    task_span_id = hashlib.sha256(task_seed).hexdigest()[:16]
    return trace_id, pipeline_span_id, task_span_id


def produce(
    facts,
    *,
    producer="eci_core.cicd_telemetry",
    source="mapping",
    github_api_fetched=False,
):
    """Build candidate telemetry records. Incomplete inputs stay incomplete/partial."""
    report = {
        "schema_version": SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "evidence_class": EVIDENCE_CLASS,
        "source": source,
        "claims": {
            "otlp_delivered": False,
            "otlp_json_exported": False,
            "weaver_pass": False,
            "github_api_fetched": bool(github_api_fetched),
            # API fetch is not full #75 trusted provenance / Weaver evidence.
            "github_authenticated": False,
            "candidate_record_only": True,
        },
        "producer": {
            "name": producer,
            "decision": "github_actions_env_or_api_adapter",
            "semconv_note": (
                "Attribute names follow public CI/CD SemConv Pipeline/Task Run; "
                "registry version pin is tracked with Knowledge #80/#81"
            ),
        },
        "status": "incomplete",
        "reason": None,
        "facts": asdict(facts),
        "resource": {},
        "spans": [],
        "otlp_json": None,
    }
    if not facts.has_run_identity:
        report["reason"] = "missing_run_identity"
        return report

    resource = {
        ATTR_PIPELINE_NAME: facts.workflow_name,
        ATTR_PIPELINE_RUN_ID: facts.run_id,
        ATTR_GITHUB_RUN_ATTEMPT: facts.run_attempt,
    }
    if facts.repository:
        resource[ATTR_GITHUB_REPOSITORY] = facts.repository
    if facts.head_sha:
        resource[ATTR_VCS_HEAD_REVISION] = facts.head_sha
    report["resource"] = resource

    trace_id, pipeline_span_id, task_span_id = _stable_ids(
        facts.run_id, facts.run_attempt, facts.job_id
    )
    spans = []
    reasons = []

    run_result, run_error_type = map_github_result(facts.run_conclusion)
    if run_result is None and facts.run_conclusion is not None:
        reasons.append("unsupported_run_conclusion")
    elif facts.run_conclusion is None:
        reasons.append("missing_run_terminal_result")
    elif facts.run_bounds_state == "missing":
        reasons.append("missing_run_timestamps")
    elif facts.run_bounds_state == "invalid":
        reasons.append("invalid_run_timestamps")
    else:
        pipeline_attrs = {
            ATTR_PIPELINE_NAME: facts.workflow_name,
            ATTR_PIPELINE_RUN_ID: facts.run_id,
            ATTR_PIPELINE_RESULT: run_result,
            ATTR_GITHUB_RUN_ATTEMPT: facts.run_attempt,
            ATTR_VCS_HEAD_REVISION: facts.head_sha,
            ATTR_ERROR_TYPE: run_error_type,
        }
        spans.append(
            _span(
                f"RUN {facts.workflow_name}",
                kind=SPAN_KIND_SERVER,
                trace_id=trace_id,
                span_id=pipeline_span_id,
                parent_span_id=None,
                start_ns=facts.run_started_at_ns,
                end_ns=facts.run_completed_at_ns,
                attributes=pipeline_attrs,
            )
        )

    job_result, job_error_type = map_github_result(facts.job_conclusion)
    if facts.job_id or facts.job_name or facts.job_conclusion is not None:
        if not facts.has_job_identity:
            reasons.append("missing_task_required_identity")
        elif job_result is None and facts.job_conclusion is not None:
            reasons.append("unsupported_job_conclusion")
        elif facts.job_conclusion is None:
            reasons.append("missing_job_terminal_result")
        elif facts.job_bounds_state == "missing":
            reasons.append("missing_job_timestamps")
        elif facts.job_bounds_state == "invalid":
            reasons.append("invalid_job_timestamps")
        else:
            task_attrs = {
                ATTR_TASK_NAME: facts.job_name,
                ATTR_TASK_RUN_ID: facts.job_id,
                ATTR_TASK_RUN_RESULT: job_result,
                ATTR_TASK_RUN_URL: facts.job_url,
                ATTR_PIPELINE_RUN_ID: facts.run_id,
                ATTR_GITHUB_RUN_ATTEMPT: facts.run_attempt,
                ATTR_ERROR_TYPE: job_error_type,
            }
            spans.append(
                _span(
                    f"task:{facts.job_name}",
                    kind=SPAN_KIND_INTERNAL,
                    trace_id=trace_id,
                    span_id=task_span_id,
                    parent_span_id=pipeline_span_id,
                    start_ns=facts.job_started_at_ns,
                    end_ns=facts.job_completed_at_ns,
                    attributes=task_attrs,
                )
            )

    report["spans"] = spans
    if not spans:
        report["status"] = "incomplete"
        report["reason"] = reasons[0] if reasons else "no_candidate_spans"
        return report

    pipeline_ok = any(span["kind"] == SPAN_KIND_SERVER for span in spans)
    task_ok = any(span["kind"] == SPAN_KIND_INTERNAL for span in spans)
    if pipeline_ok and task_ok and run_job_temporal_inconsistency(facts):
        # Keep individually valid candidate spans; do not claim produced.
        reasons.append("inconsistent_run_job_timestamps")
    if pipeline_ok and task_ok and not reasons:
        report["status"] = "produced"
        report["reason"] = None
        return report
    report["status"] = "partial"
    report["reason"] = reasons[0] if reasons else "partial_candidate_spans"
    return report


def write_artifact(report, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Do not embed the nested OTLP document twice in the candidate artifact.
    payload = {key: value for key, value in report.items() if key != "otlp_json"}
    if report.get("otlp_json") is not None:
        payload["otlp_json_exported"] = True
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def _otlp_attr(key, value):
    if type(value) is bool:
        return {"key": key, "value": {"boolValue": value}}
    if type(value) is int:
        return {"key": key, "value": {"intValue": str(value)}}
    return {"key": key, "value": {"stringValue": str(value)}}


def to_otlp_json(report):
    """Encode candidate spans as OTLP/JSON ExportTraceServiceRequest body.

    This is a file-shaped OTLP JSON artifact for a neutral receiver/replay path.
    It is not proof of OTLP/gRPC delivery or Weaver sample compatibility.
    """
    resource_attrs = [
        _otlp_attr(key, value) for key, value in report.get("resource", {}).items()
    ]
    spans = []
    for item in report.get("spans", []):
        span = {
            "traceId": item["trace_id"],
            "spanId": item["span_id"],
            "name": item["name"],
            "kind": item["kind"],
            "startTimeUnixNano": str(item["start_time_unix_nano"]),
            "endTimeUnixNano": str(item["end_time_unix_nano"]),
            "attributes": [
                _otlp_attr(key, value) for key, value in item["attributes"].items()
            ],
            "status": {"code": item["status"]["code"]},
        }
        parent = item.get("parent_span_id")
        if parent:
            span["parentSpanId"] = parent
        spans.append(span)
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": resource_attrs},
                "scopeSpans": [
                    {
                        "scope": {
                            "name": OTLP_SCOPE_NAME,
                            "version": str(SCHEMA_VERSION),
                        },
                        "spans": spans,
                    }
                ],
            }
        ]
    }


def attach_otlp_json(report):
    """Attach OTLP JSON body when candidate spans exist; never invent spans."""
    if not report.get("spans"):
        report["otlp_json"] = None
        report["claims"]["otlp_json_exported"] = False
        return report
    report["otlp_json"] = to_otlp_json(report)
    return report


def write_otlp_json(report, path):
    """Flush OTLP JSON artifact. Failure is an explicit export error, not produced."""
    if not report.get("otlp_json"):
        raise TelemetryError("otlp_json_unavailable")
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report["otlp_json"], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        raise TelemetryError("otlp_json_flush_failed") from error
    report["claims"]["otlp_json_exported"] = True
    # File artifact only — not live OTLP/gRPC delivery.
    report["claims"]["otlp_delivered"] = False
    return path


def facts_from_github_api_objects(run, job=None):
    """Map GitHub Actions Run (+ optional Job) API objects to facts.

    Run bounds use `run_started_at` and, when `status==completed`, `updated_at`
    as the completed-at equivalent (Workflow Run has no separate completed_at).
    Job bounds use Job `started_at` / `completed_at`.
    """
    if not isinstance(run, dict):
        raise TelemetryError("run_payload_invalid")
    repository = None
    remote = run.get("repository")
    if isinstance(remote, dict):
        repository = remote.get("full_name")
    repository = repository or run.get("repository_full_name")
    server = "https://github.com"
    html = run.get("html_url")
    if isinstance(html, str) and html.startswith("http"):
        parts = html.split("/")
        if len(parts) >= 3:
            server = f"{parts[0]}//{parts[2]}"
    run_completed = None
    if run.get("status") == "completed":
        run_completed = parse_time_ns(run.get("updated_at"))
    attempt = run.get("run_attempt")
    if attempt is not None and type(attempt) is not int:
        try:
            attempt = int(attempt)
        except (TypeError, ValueError):
            attempt = None
    run_id = run.get("id")
    job_id = job_name = job_url = job_conclusion = None
    job_started = job_completed = None
    if job is not None:
        if not isinstance(job, dict):
            raise TelemetryError("job_payload_invalid")
        job_id = job.get("id")
        job_name = job.get("name")
        job_url = job.get("html_url")
        job_conclusion = job.get("conclusion")
        job_started = parse_time_ns(job.get("started_at"))
        job_completed = parse_time_ns(job.get("completed_at"))
    return GitHubRunFacts(
        workflow_name=run.get("name") or run.get("display_title"),
        run_id=str(run_id) if run_id is not None else None,
        run_attempt=attempt,
        job_id=str(job_id) if job_id is not None else None,
        job_name=job_name,
        job_url=job_url,
        run_conclusion=run.get("conclusion"),
        job_conclusion=job_conclusion,
        head_sha=run.get("head_sha"),
        repository=repository,
        server_url=server,
        run_started_at_ns=parse_time_ns(run.get("run_started_at")),
        run_completed_at_ns=run_completed,
        job_started_at_ns=job_started,
        job_completed_at_ns=job_completed,
    )


def _select_job(jobs, job_id=None):
    if not isinstance(jobs, list):
        raise TelemetryError("jobs_payload_invalid")
    if job_id is not None:
        wanted = str(job_id)
        for job in jobs:
            if str(job.get("id")) == wanted:
                return job
        raise TelemetryError("job_not_found")
    completed = [
        job
        for job in jobs
        if isinstance(job, dict) and job.get("status") == "completed" and job.get("id")
    ]
    if len(completed) == 1:
        return completed[0]
    if not completed:
        return None
    raise TelemetryError("job_id_required")


def gh_api_get(path, *, runner=None):
    """GET a GitHub API path through native gh. Injectable for tests."""
    runner = subprocess.run if runner is None else runner
    result = runner(
        ["gh", "api", path],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    if result.returncode:
        raise TelemetryError("github_api_failed")
    try:
        return json.loads(result.stdout)
    except (TypeError, ValueError) as error:
        raise TelemetryError("github_api_invalid_json") from error


def fetch_github_run_job(
    repository,
    run_id,
    *,
    job_id=None,
    attempt=None,
    api_get=None,
):
    """Fetch Run (+ Job) facts from GitHub Actions API via gh."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository or ""):
        raise TelemetryError("repository_invalid")
    if type(run_id) is not int:
        try:
            run_id = int(run_id)
        except (TypeError, ValueError):
            raise TelemetryError("run_id_invalid") from None
    if run_id <= 0:
        raise TelemetryError("run_id_invalid")
    api_get = gh_api_get if api_get is None else api_get
    run = api_get(f"repos/{repository}/actions/runs/{run_id}")
    if not isinstance(run, dict):
        raise TelemetryError("run_payload_invalid")
    remote = run.get("repository")
    full_name = remote.get("full_name") if isinstance(remote, dict) else None
    if full_name and full_name.lower() != repository.lower():
        raise TelemetryError("run_repository_mismatch")
    run_attempt = attempt if attempt is not None else run.get("run_attempt")
    if run_attempt is None:
        raise TelemetryError("missing_run_attempt")
    jobs_path = f"repos/{repository}/actions/runs/{run_id}/attempts/{run_attempt}/jobs"
    jobs_payload = api_get(jobs_path)
    jobs = jobs_payload.get("jobs") if isinstance(jobs_payload, dict) else jobs_payload
    job = _select_job(jobs, job_id=job_id)
    return facts_from_github_api_objects(run, job)


def _print(value):
    sys.stdout.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="engineering-ci cicd-telemetry",
        description=(
            "Build candidate CI/CD Pipeline/Task telemetry records from GitHub "
            "Run/Job facts or Actions API, optionally write OTLP/JSON. Does not "
            "claim live OTLP delivery or Weaver PASS (ECI #75)."
        ),
    )
    parser.add_argument(
        "--facts",
        type=Path,
        help=(
            "JSON file with explicit Run/Job facts (untrusted caller input; "
            "not authenticated GitHub provenance)"
        ),
    )
    parser.add_argument("--repository", help="owner/repo for GitHub Actions API fetch")
    parser.add_argument("--run-id", type=int, help="Workflow Run ID for API fetch")
    parser.add_argument("--job-id", type=int, help="Job ID when multiple Jobs exist")
    parser.add_argument("--attempt", type=int, help="Run attempt; default from Run")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("cicd-telemetry.json"),
        help="Artifact path for candidate telemetry records",
    )
    parser.add_argument(
        "--otlp-json",
        type=Path,
        help="Write OTLP/JSON ExportTraceServiceRequest body (file flush only)",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    source = "env"
    github_api_fetched = False
    if args.facts is not None and args.run_id is not None:
        parser.exit(2, "cicd-telemetry rejected: facts_and_run_id_conflict\n")
    if args.facts is not None:
        try:
            payload = json.loads(args.facts.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            parser.exit(2, "cicd-telemetry rejected: facts_unreadable\n")
        try:
            facts = facts_from_mapping(payload)
        except TelemetryError as error:
            parser.exit(2, f"cicd-telemetry rejected: {error.code}\n")
        source = "mapping"
    elif args.run_id is not None:
        repository = args.repository
        if not repository:
            parser.exit(2, "cicd-telemetry rejected: repository_required\n")
        try:
            facts = fetch_github_run_job(
                repository,
                args.run_id,
                job_id=args.job_id,
                attempt=args.attempt,
            )
        except TelemetryError as error:
            parser.exit(2, f"cicd-telemetry rejected: {error.code}\n")
        source = "github_api"
        github_api_fetched = True
    else:
        facts = facts_from_actions_env()
        source = "env"
    report = produce(facts, source=source, github_api_fetched=github_api_fetched)
    attach_otlp_json(report)
    try:
        write_artifact(report, args.output)
    except OSError:
        parser.exit(2, "cicd-telemetry rejected: artifact_flush_failed\n")
    if args.otlp_json is not None:
        try:
            write_otlp_json(report, args.otlp_json)
        except TelemetryError as error:
            sys.stderr.write(f"cicd-telemetry export failed: {error.code}\n")
            return 3
    if args.json:
        printable = {key: value for key, value in report.items() if key != "otlp_json"}
        printable["otlp_json_present"] = report.get("otlp_json") is not None
        _print(printable)
    else:
        print(f"status: {report['status']}")
        print(f"evidence_class: {report['evidence_class']}")
        print(f"source: {report['source']}")
        print(f"reason: {report['reason'] or 'ok'}")
        print(f"spans: {len(report['spans'])}")
        print(f"artifact: {args.output}")
        if args.otlp_json is not None:
            print(f"otlp_json: {args.otlp_json}")
            print(f"otlp_json_exported: {report['claims']['otlp_json_exported']}")
    if report["status"] in {"produced", "partial"}:
        return 0
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
