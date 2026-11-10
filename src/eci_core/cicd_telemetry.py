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
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 2
REPORT_KIND = "engineering-ci-cicd-telemetry"
EVIDENCE_CLASS = "candidate_record"
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


def produce(facts, *, producer="eci_core.cicd_telemetry"):
    """Build candidate telemetry records. Incomplete inputs stay incomplete/partial."""
    report = {
        "schema_version": SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "evidence_class": EVIDENCE_CLASS,
        "claims": {
            "otlp_delivered": False,
            "weaver_pass": False,
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
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def _print(value):
    sys.stdout.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="engineering-ci cicd-telemetry",
        description=(
            "Build candidate CI/CD Pipeline/Task telemetry records from GitHub "
            "Run/Job facts and write a JSON artifact. Does not claim OTLP "
            "delivery or Weaver PASS (ECI #75)."
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
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("cicd-telemetry.json"),
        help="Artifact path for candidate telemetry records",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.facts is not None:
        try:
            payload = json.loads(args.facts.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            parser.exit(2, "cicd-telemetry rejected: facts_unreadable\n")
        try:
            facts = facts_from_mapping(payload)
        except TelemetryError as error:
            parser.exit(2, f"cicd-telemetry rejected: {error.code}\n")
    else:
        facts = facts_from_actions_env()
    report = produce(facts)
    write_artifact(report, args.output)
    if args.json:
        _print(report)
    else:
        print(f"status: {report['status']}")
        print(f"evidence_class: {report['evidence_class']}")
        print(f"reason: {report['reason'] or 'ok'}")
        print(f"spans: {len(report['spans'])}")
        print(f"artifact: {args.output}")
    if report["status"] in {"produced", "partial"}:
        return 0
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
