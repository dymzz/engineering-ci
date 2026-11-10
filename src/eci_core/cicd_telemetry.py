"""GitHub Actions Run/Job facts → OTel CI/CD Pipeline/Task telemetry records.

Producer decision (ECI #75 / Knowledge PR #79 C/O2):
- Source of truth: GitHub Actions environment and optional Run/Job API facts.
- Emits Pipeline Run and Task Run span *records* suitable for OTLP export or a
  verifiable JSON artifact. Does not instrument Dagger Functions.
- Missing Run ID, Attempt, or Job identity yields an incomplete report — never a
  forged complete Pipeline Span. Selection/Required statuses are not mapped into
  cicd.pipeline.result.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

SCHEMA_VERSION = 1
REPORT_KIND = "engineering-ci-cicd-telemetry"
SPAN_KIND_INTERNAL = 1

# Official CI/CD SemConv attribute names (registry pin tracked with Knowledge #80).
ATTR_PIPELINE_NAME = "cicd.pipeline.name"
ATTR_PIPELINE_RUN_ID = "cicd.pipeline.run.id"
ATTR_PIPELINE_RESULT = "cicd.pipeline.result"
ATTR_TASK_NAME = "cicd.pipeline.task.name"
ATTR_TASK_RUN_ID = "cicd.pipeline.task.run.id"
ATTR_TASK_RUN_URL = "cicd.pipeline.task.run.url.full"
ATTR_TASK_RUN_RESULT = "cicd.pipeline.task.run.result"
ATTR_VCS_HEAD_REVISION = "vcs.ref.head.revision"

# GitHub-native fact until the fixed SemConv registry confirms an official key.
ATTR_GITHUB_RUN_ATTEMPT = "github.actions.run_attempt"
ATTR_GITHUB_REPOSITORY = "github.repository"


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
    conclusion: str | None
    head_sha: str | None
    repository: str | None
    server_url: str | None = None

    @property
    def complete_for_pipeline(self):
        return bool(self.workflow_name and self.run_id and self.run_attempt is not None)

    @property
    def complete_for_task(self):
        return self.complete_for_pipeline and bool(self.job_id and self.job_name)


def facts_from_actions_env(env=None):
    """Read GitHub Actions runtime facts; missing fields stay None (no forgery)."""
    env = os.environ if env is None else env

    def text(key):
        value = env.get(key)
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    attempt_raw = text("GITHUB_RUN_ATTEMPT")
    attempt = None
    if attempt_raw is not None:
        try:
            attempt = int(attempt_raw)
        except ValueError:
            attempt = None
    # Actions env does not expose Job ID; adapters may supply ENGINEERING_CI_*.
    return GitHubRunFacts(
        workflow_name=text("GITHUB_WORKFLOW"),
        run_id=text("GITHUB_RUN_ID"),
        run_attempt=attempt,
        job_id=text("GITHUB_JOB_ID") or text("ENGINEERING_CI_JOB_ID"),
        job_name=text("GITHUB_JOB"),
        job_url=text("ENGINEERING_CI_JOB_URL"),
        conclusion=text("ENGINEERING_CI_JOB_CONCLUSION") or text("JOB_CONCLUSION"),
        head_sha=text("GITHUB_SHA"),
        repository=text("GITHUB_REPOSITORY"),
        server_url=text("GITHUB_SERVER_URL") or "https://github.com",
    )


def facts_from_mapping(value):
    """Build facts from an explicit JSON/API mapping (tests and post-job adapters)."""
    if not isinstance(value, dict):
        raise TelemetryError("facts_invalid")

    def text(key):
        item = value.get(key)
        if item is None:
            return None
        item = str(item).strip()
        return item or None

    attempt = value.get("run_attempt")
    if attempt is not None and type(attempt) is not int:
        try:
            attempt = int(attempt)
        except (TypeError, ValueError):
            raise TelemetryError("run_attempt_invalid") from None
    return GitHubRunFacts(
        workflow_name=text("workflow_name"),
        run_id=text("run_id"),
        run_attempt=attempt,
        job_id=text("job_id"),
        job_name=text("job_name"),
        job_url=text("job_url"),
        conclusion=text("conclusion"),
        head_sha=text("head_sha"),
        repository=text("repository"),
        server_url=text("server_url") or "https://github.com",
    )


def _pipeline_result(conclusion):
    if conclusion is None:
        return None
    mapping = {
        "success": "success",
        "failure": "failure",
        "cancelled": "error",
        "canceled": "error",
        "skipped": "error",
        "timed_out": "error",
        "action_required": "error",
        "neutral": "error",
        "stale": "error",
    }
    return mapping.get(conclusion)


def _status_code(attributes):
    result = attributes.get(ATTR_PIPELINE_RESULT) or attributes.get(
        ATTR_TASK_RUN_RESULT
    )
    if result == "success":
        return 1
    if result:
        return 2
    return 0


def _span(name, *, trace_id, span_id, parent_span_id, start_ns, end_ns, attributes):
    cleaned = {key: value for key, value in attributes.items() if value is not None}
    return {
        "name": name,
        "kind": SPAN_KIND_INTERNAL,
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "start_time_unix_nano": start_ns,
        "end_time_unix_nano": end_ns,
        "attributes": cleaned,
        "status": {"code": _status_code(cleaned)},
    }


def _ids_from_run(run_id, attempt):
    # Deterministic, non-secret ids for offline artifacts; not cryptographic security.
    seed = f"{run_id}:{attempt}".encode()
    digest = __import__("hashlib").sha256(seed).hexdigest()
    return digest[:32], digest[32:48], digest[48:64]


def produce(facts, *, now_ns=None, producer="eci_core.cicd_telemetry"):
    """Build a telemetry report from GitHub facts. Incomplete inputs stay incomplete."""
    now_ns = time.time_ns() if now_ns is None else now_ns
    report = {
        "schema_version": SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "producer": {
            "name": producer,
            "decision": "github_actions_env_or_api_adapter",
            "semconv_note": (
                "Attribute names follow public CI/CD SemConv; registry version "
                "pin is tracked with Knowledge #80/#81"
            ),
        },
        "status": "incomplete",
        "reason": None,
        "facts": asdict(facts),
        "resource": {},
        "spans": [],
    }
    if not facts.complete_for_pipeline:
        report["reason"] = "missing_run_identity"
        return report
    if facts.conclusion is None:
        report["reason"] = "missing_terminal_result"
        return report
    result = _pipeline_result(facts.conclusion)
    if result is None:
        report["reason"] = "unsupported_conclusion"
        return report

    trace_id, pipeline_span_id, task_span_id = _ids_from_run(
        facts.run_id, facts.run_attempt
    )
    start_ns = now_ns - 1_000_000
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

    pipeline_attrs = {
        ATTR_PIPELINE_NAME: facts.workflow_name,
        ATTR_PIPELINE_RUN_ID: facts.run_id,
        ATTR_PIPELINE_RESULT: result,
        ATTR_GITHUB_RUN_ATTEMPT: facts.run_attempt,
        ATTR_VCS_HEAD_REVISION: facts.head_sha,
    }
    spans = [
        _span(
            f"pipeline:{facts.workflow_name}",
            trace_id=trace_id,
            span_id=pipeline_span_id,
            parent_span_id=None,
            start_ns=start_ns,
            end_ns=now_ns,
            attributes=pipeline_attrs,
        )
    ]
    if facts.complete_for_task:
        task_attrs = {
            ATTR_TASK_NAME: facts.job_name,
            ATTR_TASK_RUN_ID: facts.job_id,
            ATTR_TASK_RUN_RESULT: result,
            ATTR_TASK_RUN_URL: facts.job_url,
            ATTR_PIPELINE_RUN_ID: facts.run_id,
            ATTR_GITHUB_RUN_ATTEMPT: facts.run_attempt,
        }
        spans.append(
            _span(
                f"task:{facts.job_name}",
                trace_id=trace_id,
                span_id=task_span_id,
                parent_span_id=pipeline_span_id,
                start_ns=start_ns,
                end_ns=now_ns,
                attributes=task_attrs,
            )
        )
    else:
        report["reason"] = "pipeline_without_task_identity"
    report["spans"] = spans
    report["status"] = "produced" if facts.complete_for_task else "partial"
    if facts.complete_for_task:
        report["reason"] = None
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
            "Produce CI/CD Pipeline/Task telemetry records from GitHub Actions "
            "facts; write a verifiable JSON artifact (ECI #75)."
        ),
    )
    parser.add_argument(
        "--facts",
        type=Path,
        help="JSON file with explicit GitHub Run/Job facts (overrides env)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("cicd-telemetry.json"),
        help="Artifact path for the telemetry report",
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
        print(f"reason: {report['reason'] or 'ok'}")
        print(f"spans: {len(report['spans'])}")
        print(f"artifact: {args.output}")
    if report["status"] == "produced":
        return 0
    if report["status"] == "partial":
        return 0
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
