"""GitHub Actions Run/Job facts → OTel CI/CD Pipeline/Task candidate records.

Producer decision (ECI #75 / Knowledge PR #79 C/O2):
- Source of truth: separate Run and Job facts (env or explicit mapping/API).
- Emits candidate Pipeline/Task span records for a JSON artifact only.
- Does not prove OTLP delivery, Weaver PASS, or authenticated GitHub provenance.
- Does not instrument Dagger Functions.
- Missing identity, terminal results, required SemConv fields, or real timestamps
  yield incomplete/partial reports — never forged complete spans.

Pipeline Run identity (ECI #75 / PR #77 review P2-2):
- Each GitHub Actions *Attempt* is one distinct pipeline execution (own Trace).
- SemConv `cicd.pipeline.run.id` is the stable canonical form
  `{github_run_id}/{run_attempt}` — same value on Resource, Pipeline Span, and
  Task Span correlation. Raw GitHub Run ID and Attempt remain in facts and as
  the `github.actions.run_attempt` extension (not a Required SemConv attribute).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

SCHEMA_VERSION = 7
REPORT_KIND = "engineering-ci-cicd-telemetry"
EVIDENCE_CLASS = "candidate_record"
OTLP_SCOPE_NAME = "eci_core.cicd_telemetry"
# CLI mode: emit Root only when an explicit completed-at input closes the bounds.
# The recorded precision is exact_input, which is not a verified GitHub timestamp.
END_TIME_EXACT = "exact"
END_TIME_OBSERVED_UPPER_BOUND = "observed_upper_bound"
END_TIME_UNPROVEN = "unproven"
TIME_PRECISION_EXACT_INPUT = "exact_input"
TIME_PRECISION_OBSERVED_UPPER_BOUND = END_TIME_OBSERVED_UPPER_BOUND
TIME_PRECISION_UNPROVEN = END_TIME_UNPROVEN
PHASE_PENDING = "pending"
PHASE_EXECUTING = "executing"
PHASE_FINALIZING = "finalizing"
_PHASE_STATES = frozenset({PHASE_PENDING, PHASE_EXECUTING, PHASE_FINALIZING})
DURATION_METRIC_NAME = "cicd.pipeline.run.duration"
ACTIVE_METRIC_NAME = "cicd.pipeline.run.active"
# Explicit bounds (seconds) for one phase observation. Last bucket is +Inf.
_DURATION_BOUNDS_S = (0.01, 0.1, 1, 2, 5, 10, 30, 60, 120, 300, 600, 1800, 3600)
# opentelemetry.proto.metrics.v1.AggregationTemporality.DELTA
_HISTOGRAM_DELTA = 1
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
ATTR_PIPELINE_RUN_URL = "cicd.pipeline.run.url.full"
ATTR_PIPELINE_RESULT = "cicd.pipeline.result"
ATTR_PIPELINE_RUN_STATE = "cicd.pipeline.run.state"
ATTR_TASK_NAME = "cicd.pipeline.task.name"
ATTR_TASK_RUN_ID = "cicd.pipeline.task.run.id"
ATTR_TASK_RUN_URL = "cicd.pipeline.task.run.url.full"
ATTR_TASK_RUN_RESULT = "cicd.pipeline.task.run.result"
ATTR_ERROR_TYPE = "error.type"
ATTR_VCS_HEAD_REVISION = "vcs.ref.head.revision"

# GitHub-native fact until the fixed SemConv registry confirms an official key.
ATTR_GITHUB_RUN_ATTEMPT = "github.actions.run_attempt"
ATTR_GITHUB_REPOSITORY = "github.repository"
# ECI extension attributes (not official SemConv Required keys).
ATTR_ECI_END_TIME_QUALITY = "eci.cicd.end_time_quality"
ATTR_ECI_EXACT_RUN_DURATION = "eci.cicd.exact_run_duration"
ATTR_ECI_DURATION_VALID_FOR_METRICS = "eci.cicd.duration_valid_for_metrics"
ATTR_ECI_ROOT_EMISSION = "eci.cicd.root_emission"

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

_JOBS_PAGE_SIZE = 100


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
    # Provenance only — GitHub Workflow Run `updated_at` is object update time,
    # not a proven exact Attempt completed-at (PR #77 P1-2).
    run_updated_at_ns: int | None = None
    run_url: str | None = None
    # Native Attempt `status` from GitHub API (e.g. completed / in_progress).
    # Required for observed_upper_bound Root; never invent from conclusion alone.
    run_status: str | None = None

    @property
    def has_run_identity(self):
        # Each GitHub Attempt is an independent Pipeline Run; attempt must be >= 1.
        return bool(
            self.workflow_name
            and self.run_id
            and type(self.run_attempt) is int
            and self.run_attempt >= 1
        )

    @property
    def has_job_identity(self):
        return bool(self.job_id and self.job_name and self.job_url)

    @property
    def pipeline_run_id(self):
        """SemConv Resource/Span pipeline-run id for this Attempt execution."""
        if not self.has_run_identity:
            return None
        return f"{self.run_id}/{self.run_attempt}"

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
    # GitHub timed_out is an interrupted/error operation; keep SemConv result
    # "timeout" and record error.type (Recording Errors), never rewrite as failure.
    if result == "timeout":
        return result, "timeout"
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
        # Invalid Attempt is not a Run identity; leave unset (same as missing).
        if attempt is not None and attempt < 1:
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
        run_updated_at_ns=parse_time_ns(_text(env, "ENGINEERING_CI_RUN_UPDATED_AT")),
        run_url=_text(env, "ENGINEERING_CI_RUN_URL"),
        run_status=_text(env, "ENGINEERING_CI_RUN_STATUS"),
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
    if attempt is not None and attempt < 1:
        raise TelemetryError("run_attempt_invalid")
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
        run_updated_at_ns=parse_time_ns(
            value.get("run_updated_at_ns", value.get("run_updated_at"))
        ),
        run_url=_text(value, "run_url"),
        run_status=_text(value, "run_status"),
    )


def _status_code(result):
    """OTel Span Status for CI/CD operations (Recording Errors).

    Success and non-error terminals (cancellation/skip) leave Status UNSET.
    Confirmed error operations — failure, error, and GitHub timed_out → timeout —
    use STATUS_ERROR. Business results stay on cicd.pipeline(.task.run).result
    — never encode success as OK or rewrite timeout as failure.
    """
    if result in {"failure", "error", "timeout"}:
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


def _explicit_run_end_source(source):
    """Name the input that closed Run bounds. None of these are GitHub-native."""
    if source == "env":
        return "explicit_env"
    if source == "github_api":
        # Attempt API has no official run-completed timestamp.
        return "not_native"
    return "explicit_mapping"


def _normalize_phase_intervals(value):
    """Keep caller-supplied phase windows. Do not invent missing ones."""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise TelemetryError("phase_intervals_invalid")
    cleaned = []
    for item in value:
        if not isinstance(item, dict):
            raise TelemetryError("phase_intervals_invalid")
        state = item.get("state")
        if state not in _PHASE_STATES:
            raise TelemetryError("phase_state_invalid")
        start = item.get("start_ns")
        end = item.get("end_ns")
        if type(start) is not int or type(end) is not int:
            raise TelemetryError("phase_interval_invalid")
        entry = {
            "state": state,
            "start_ns": start,
            "end_ns": end,
            "valid": end >= start,
            "source": item.get("source"),
            "run_id": item.get("run_id"),
            "attempt": item.get("attempt"),
            "quality": item.get("quality"),
        }
        if item.get("result") is not None:
            entry["result"] = item["result"]
        cleaned.append(entry)
    return cleaned


def _bind_phase_intervals(intervals, facts, source):
    """Attach provenance. GitHub API facts do not invent SemConv phases."""
    bound = []
    for item in intervals:
        entry = dict(item)
        entry["source"] = entry.get("source") or source
        entry["run_id"] = entry.get("run_id") or facts.run_id
        entry["attempt"] = (
            facts.run_attempt if entry.get("attempt") is None else entry["attempt"]
        )
        entry["quality"] = entry.get("quality") or "explicit_phase_input"
        reject = None
        if source == "github_api" and entry["quality"] != "native_phase_transition":
            reject = "phase_source_not_native"
        elif (
            entry["source"] != source
            or str(entry["run_id"]) != str(facts.run_id)
            or entry["attempt"] != facts.run_attempt
        ):
            reject = "phase_source_mismatch"
        elif entry["quality"] == "job_execution_envelope":
            reject = "job_envelope_is_not_pipeline_phase"
        if reject:
            entry["valid"] = False
            entry["reject"] = reject
        bound.append(entry)
    return bound


def _job_set_complete(job_sources, completed_jobs_total, job_selection, omitted_count):
    if job_selection == "job_id" or omitted_count:
        return False
    if completed_jobs_total is not None and completed_jobs_total != len(job_sources):
        return False
    return all(
        job.job_started_at_ns is not None and job.job_completed_at_ns is not None
        for _, job in job_sources
    )


def _job_execution_envelope(
    job_sources,
    *,
    source,
    facts,
    completed_jobs_total,
    job_selection,
    omitted_count,
):
    """Job start/end envelope. This is not a SemConv pipeline phase."""
    starts = []
    ends = []
    for _, job in job_sources:
        if job.job_started_at_ns is not None:
            starts.append(job.job_started_at_ns)
        if job.job_completed_at_ns is not None:
            ends.append(job.job_completed_at_ns)
    if not starts or not ends:
        return None
    complete = (
        _job_set_complete(
            job_sources, completed_jobs_total, job_selection, omitted_count
        )
        and len(starts) == len(job_sources)
        and len(ends) == len(job_sources)
    )
    return {
        "start_ns": min(starts),
        "end_ns": max(ends),
        "quality": "job_execution_envelope",
        "source": source,
        "run_id": facts.run_id,
        "attempt": facts.run_attempt,
        "complete": complete,
        "semconv_phase": None,
    }


def _observation_interval(
    job_sources,
    *,
    observed_ns,
    run_started_ns,
    completed_jobs_total,
    job_selection,
    omitted_count,
):
    """Constraint range for a confirmed terminal. Not a proven end instant."""
    ends = []
    missing_end = False
    for _, job in job_sources:
        if job.job_completed_at_ns is None:
            missing_end = True
        else:
            ends.append(job.job_completed_at_ns)
    job_count = len(job_sources)
    lower = None
    lower_source = None
    invalid = None
    if job_count == 0 and completed_jobs_total in {None, 0}:
        lower = run_started_ns
        lower_source = "weak_run_start"
    elif (
        job_selection == "job_id"
        or (completed_jobs_total is not None and completed_jobs_total != job_count)
        or omitted_count
        or missing_end
        or not ends
    ):
        invalid = "incomplete_job_set"
    else:
        lower = max(ends)
        lower_source = "last_completed_job_end"
    if invalid:
        return _bound_fields(
            valid=False,
            reason=invalid,
            lower=None,
            lower_source=None,
            upper=observed_ns,
            uncertainty=None,
            job_set_complete=False,
            clock_quality="not_assessed",
        )
    if lower is None or observed_ns < lower:
        return _bound_fields(
            valid=False,
            reason="end_bounds_unverified",
            lower=lower,
            lower_source=lower_source,
            upper=observed_ns,
            uncertainty=None,
            job_set_complete=True,
            clock_quality="rejected",
        )
    return _bound_fields(
        valid=True,
        reason=None,
        lower=lower,
        lower_source=lower_source,
        upper=observed_ns,
        uncertainty=observed_ns - lower,
        job_set_complete=True,
        clock_quality="ordered",
    )


def _bound_fields(
    *,
    valid,
    reason,
    lower,
    lower_source,
    upper,
    uncertainty,
    job_set_complete,
    clock_quality,
):
    return {
        "bounds_valid": valid,
        "bounds_invalid_reason": reason,
        "end_lower_bound_ns": lower,
        "end_lower_bound_source": lower_source,
        "lower_source": lower_source,
        "end_upper_bound_ns": upper,
        "end_upper_bound_source": "terminal_observed_at",
        "upper_source": "terminal_observed_at",
        "uncertainty_ns": uncertainty,
        "job_set_complete": job_set_complete,
        "clock_quality": clock_quality,
    }


def _histogram_buckets(duration_s):
    bounds = list(_DURATION_BOUNDS_S)
    counts = [0] * (len(bounds) + 1)
    index = len(bounds)
    for position, bound in enumerate(bounds):
        if duration_s <= bound:
            index = position
            break
    counts[index] = 1
    return bounds, counts


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


def _task_omit_reason(job_facts):
    """Return omission reason for one Job, or None when a Task Span can be emitted."""
    job_result, _error = map_github_result(job_facts.job_conclusion)
    if not job_facts.has_job_identity:
        return "missing_task_required_identity"
    if job_result is None and job_facts.job_conclusion is not None:
        return "unsupported_job_conclusion"
    if job_facts.job_conclusion is None:
        return "missing_job_terminal_result"
    if job_facts.job_bounds_state == "missing":
        return "missing_job_timestamps"
    if job_facts.job_bounds_state == "invalid":
        return "invalid_job_timestamps"
    return None


def produce(
    facts,
    *,
    producer="eci_core.cicd_telemetry",
    source="mapping",
    github_api_fetched=False,
    completed_jobs_total=None,
    job_selection=None,
    task_facts=None,
    jobs_observed_total=None,
    noncompleted_jobs_total=None,
    terminal_status=None,
    terminal_observed_at_ns=None,
    end_time_mode=None,
    event_status=None,
    event_conclusion=None,
    expected_job_stubs=None,
    phase_intervals=None,
    include_run_entity_metrics=False,
    max_end_uncertainty_ns=None,
):
    """Build candidate telemetry records. Incomplete inputs stay incomplete/partial.

    ``task_facts`` (optional) is the Attempt's Job set to emit as Task Spans.
    When omitted, a single Job carried on ``facts`` is used (mapping/env/diagnostic
    ``--job-id`` path). Full Attempt coverage passes every completed Job here.

    Pipeline Root end-time modes (PR #81):
    - default / exact: only emit SERVER Root when ``run_completed_at_ns`` is an
      explicit input. Recorded precision is ``exact_input``. That closed numeric
      bound is not a verified GitHub completion timestamp.
    - ``observed_upper_bound``: after Attempt ``run_status=completed``, record a
      constraint interval (last Job end or weak Run start, through the
      observation time). That interval is not a proven end. An approximate Root
      is emitted only when ``max_end_uncertainty_ns`` is set and the interval
      fits that budget. A late observation is not written as the standard Root
      end. ``terminal_status`` / event fields are not substitutes for API status.

    ``phase_intervals`` are already-proven ``pending`` / ``executing`` /
    ``finalizing`` windows. Missing windows stay ``not_observable``.
    ``include_run_entity_metrics`` defaults off (SemConv run-entity opt-in).
    """
    # Persisted producer identity predates the package move; it is not an import path.
    report = {
        "schema_version": SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "evidence_class": EVIDENCE_CLASS,
        "source": source,
        "claims": {
            "otlp_delivered": False,
            "otlp_json_prepared": False,
            "otlp_json_exported": False,
            "otlp_logs_prepared": False,
            "otlp_logs_exported": False,
            "otlp_logs_rpc_accepted": False,
            "otlp_logs_receiver_observed": False,
            "otlp_logs_delivered": False,
            "otlp_metrics_prepared": False,
            "otlp_metrics_exported": False,
            "otlp_metrics_rpc_accepted": False,
            "otlp_metrics_receiver_observed": False,
            "otlp_metrics_delivered": False,
            "rpc_accepted": False,
            "receiver_observed": False,
            "snapshot_phase": "before_receiver_confirmation",
            "all_prepared_signals_delivered": False,
            "bundle_delivery_complete": False,
            "transport": {},
            "weaver_pass": False,
            "trusted": False,
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
            "pipeline_run_id_form": "{github_run_id}/{run_attempt}",
        },
        "status": "incomplete",
        "reason": None,
        "facts": asdict(facts),
        "terminal": {
            "status": terminal_status,
            "conclusion": facts.run_conclusion,
            "observed_at_ns": terminal_observed_at_ns,
            "run_completed_at_ns": facts.run_completed_at_ns,
            "end_time_source": None,
            "end_time_quality": TIME_PRECISION_UNPROVEN,
            "time_precision": TIME_PRECISION_UNPROVEN,
            "time_source_verified": False,
            "exact_run_duration": False,
            "duration_valid_for_metrics": False,
            "terminal_confirmed": False,
            "root_emission": "not_emitted",
            "root_emission_reason": None,
            "end_lower_bound_ns": None,
            "end_lower_bound_source": None,
            "end_upper_bound_ns": None,
            "end_upper_bound_source": None,
            "uncertainty_ns": None,
            "bounds_valid": False,
            "bounds_invalid_reason": None,
            "max_end_uncertainty_ns": max_end_uncertainty_ns,
            "reviewed_at_ns": terminal_observed_at_ns,
        },
        "phase_intervals": _normalize_phase_intervals(phase_intervals),
        "include_run_entity_metrics": bool(include_run_entity_metrics),
        "coverage": {
            "jobs_observed_total": jobs_observed_total,
            "completed_jobs_total": completed_jobs_total,
            "noncompleted_jobs_total": noncompleted_jobs_total,
            "job_selection": job_selection,
            "task_spans_emitted": 0,
            "tasks_omitted": [],
            "pipeline_terminal_covered": False,
            "task_coverage": "none",
            "expected_completed_jobs": [],
        },
        "terminal_provenance": None,
        "signals": {
            "logs": [],
            "metrics": [],
            "metrics_not_observable": [],
            "logs_source": None,
        },
        "resource": {},
        "spans": [],
        "otlp_json": None,
        "otlp_logs_json": None,
        "otlp_metrics_json": None,
    }
    # Authoritative terminal status is native Attempt status on facts, not CLI hints.
    report["terminal"]["status"] = facts.run_status or terminal_status
    if facts.has_run_identity:
        report["phase_intervals"] = _bind_phase_intervals(
            report["phase_intervals"], facts, source
        )
    if not facts.has_run_identity:
        report["reason"] = "missing_run_identity"
        return report

    pipeline_run_id = facts.pipeline_run_id
    resource = {
        ATTR_PIPELINE_NAME: facts.workflow_name,
        ATTR_PIPELINE_RUN_ID: pipeline_run_id,
        ATTR_GITHUB_RUN_ATTEMPT: facts.run_attempt,
    }
    if facts.run_url:
        resource[ATTR_PIPELINE_RUN_URL] = facts.run_url
    if facts.repository:
        resource[ATTR_GITHUB_REPOSITORY] = facts.repository
    if facts.head_sha:
        resource[ATTR_VCS_HEAD_REVISION] = facts.head_sha
    report["resource"] = resource

    trace_id, pipeline_span_id, _ = _stable_ids(facts.run_id, facts.run_attempt)
    spans = []
    reasons = []

    if task_facts is not None:
        # Entries are GitHubRunFacts or (source_index, GitHubRunFacts).
        job_sources = []
        for entry in task_facts:
            if isinstance(entry, tuple) and len(entry) == 2:
                job_sources.append(entry)
            else:
                job_sources.append((None, entry))
    elif facts.job_id or facts.job_name or facts.job_conclusion is not None:
        job_sources = [(None, facts)]
    else:
        job_sources = []

    run_result, run_error_type = map_github_result(facts.run_conclusion)
    root_end_ns = None
    root_emission = "not_emitted"
    root_emission_reason = None
    duration_valid_for_metrics = False
    observation_ready = False
    time_precision = TIME_PRECISION_UNPROVEN
    # Numeric closure and native-source verification are separate facts.
    time_source_verified = False
    run_end_source = None
    if run_result is None and facts.run_conclusion is not None:
        reasons.append("unsupported_run_conclusion")
    elif facts.run_conclusion is None:
        reasons.append("missing_run_terminal_result")
    elif facts.run_bounds_state == "ok":
        root_end_ns = facts.run_completed_at_ns
        time_precision = TIME_PRECISION_EXACT_INPUT
        time_source_verified = False
        run_end_source = _explicit_run_end_source(source)
        root_emission = "explicit_end"
        duration_valid_for_metrics = True
    elif end_time_mode == END_TIME_OBSERVED_UPPER_BOUND:
        api_status = facts.run_status
        if api_status is None:
            reasons.append("terminal_status_unverified")
        elif api_status != "completed":
            reasons.append("run_not_completed")
        elif event_status is not None and event_status != api_status:
            reasons.append("event_api_status_conflict")
        elif (
            event_conclusion is not None
            and facts.run_conclusion is not None
            and event_conclusion != facts.run_conclusion
        ):
            reasons.append("event_api_conclusion_conflict")
        elif facts.run_started_at_ns is None:
            reasons.append("missing_run_timestamps")
        elif terminal_observed_at_ns is None:
            reasons.append("missing_terminal_observed_at")
        elif terminal_observed_at_ns < facts.run_started_at_ns:
            reasons.append("invalid_terminal_observed_at")
        else:
            # Observed upper bound must not precede any Job end in the set.
            max_job_end = None
            for _, job_facts in job_sources:
                if job_facts.job_completed_at_ns is None:
                    continue
                if max_job_end is None or job_facts.job_completed_at_ns > max_job_end:
                    max_job_end = job_facts.job_completed_at_ns
            if max_job_end is not None and terminal_observed_at_ns < max_job_end:
                reasons.append("terminal_observed_before_jobs")
                reasons.append("end_bounds_unverified")
                report["terminal"]["bounds_valid"] = False
                report["terminal"]["bounds_invalid_reason"] = "end_bounds_unverified"
                report["terminal"]["clock_quality"] = "rejected"
                report["terminal"]["job_set_complete"] = True
            else:
                # Confirmed terminal only. The observation is not a span end yet.
                observation_ready = True
                report["terminal"]["terminal_confirmed"] = True
    elif facts.run_bounds_state == "missing":
        # Prefer explicit missing completed-at over fabricating from updated_at.
        if facts.run_updated_at_ns is not None and facts.run_completed_at_ns is None:
            reasons.append("run_completed_at_unproven")
        else:
            reasons.append("missing_run_timestamps")
    elif facts.run_bounds_state == "invalid":
        reasons.append("invalid_run_timestamps")

    report["terminal"]["end_time_quality"] = time_precision
    report["terminal"]["end_time_source"] = run_end_source
    report["terminal"]["time_precision"] = time_precision
    report["terminal"]["time_source_verified"] = time_source_verified
    report["terminal"]["exact_run_duration"] = (
        time_precision == TIME_PRECISION_EXACT_INPUT
    )

    if root_end_ns is not None and facts.run_started_at_ns is not None:
        pipeline_attrs = {
            ATTR_PIPELINE_NAME: facts.workflow_name,
            ATTR_PIPELINE_RUN_ID: pipeline_run_id,
            ATTR_PIPELINE_RUN_URL: facts.run_url,
            ATTR_PIPELINE_RESULT: run_result,
            ATTR_GITHUB_RUN_ATTEMPT: facts.run_attempt,
            ATTR_VCS_HEAD_REVISION: facts.head_sha,
            ATTR_ERROR_TYPE: run_error_type,
            # ECI extensions for Terminal consumers; not official SemConv.
            ATTR_ECI_END_TIME_QUALITY: time_precision,
            ATTR_ECI_EXACT_RUN_DURATION: time_precision == TIME_PRECISION_EXACT_INPUT,
            ATTR_ECI_DURATION_VALID_FOR_METRICS: duration_valid_for_metrics,
            ATTR_ECI_ROOT_EMISSION: root_emission,
        }
        spans.append(
            _span(
                f"RUN {facts.workflow_name}",
                kind=SPAN_KIND_SERVER,
                trace_id=trace_id,
                span_id=pipeline_span_id,
                parent_span_id=None,
                start_ns=facts.run_started_at_ns,
                end_ns=root_end_ns,
                attributes=pipeline_attrs,
            )
        )

    omitted = []
    expected_by_key = {}
    for source_index, job_facts in job_sources:
        omit = _task_omit_reason(job_facts)
        expected_key = (
            ("id", job_facts.job_id)
            if job_facts.job_id is not None
            else ("index", source_index)
        )
        if omit is not None:
            entry = {"reason": omit}
            if job_facts.job_id is not None:
                entry["job_id"] = job_facts.job_id
            elif source_index is not None:
                entry["source_index"] = source_index
            omitted.append(entry)
            if omit not in reasons:
                reasons.append(omit)
            expected_by_key[expected_key] = {
                "source_index": source_index,
                "job_id": job_facts.job_id,
                "job_url": job_facts.job_url,
                "expected_task_span_id": None,
                "emitted": False,
                "omitted": True,
                "omit_reason": omit,
            }
            continue
        job_result, job_error_type = map_github_result(job_facts.job_conclusion)
        _, _, task_span_id = _stable_ids(
            facts.run_id, facts.run_attempt, job_facts.job_id
        )
        task_attrs = {
            ATTR_TASK_NAME: job_facts.job_name,
            ATTR_TASK_RUN_ID: job_facts.job_id,
            ATTR_TASK_RUN_RESULT: job_result,
            ATTR_TASK_RUN_URL: job_facts.job_url,
            ATTR_PIPELINE_RUN_ID: pipeline_run_id,
            ATTR_GITHUB_RUN_ATTEMPT: facts.run_attempt,
            ATTR_ERROR_TYPE: job_error_type,
        }
        spans.append(
            _span(
                f"task:{job_facts.job_name}",
                kind=SPAN_KIND_INTERNAL,
                trace_id=trace_id,
                span_id=task_span_id,
                parent_span_id=pipeline_span_id,
                start_ns=job_facts.job_started_at_ns,
                end_ns=job_facts.job_completed_at_ns,
                attributes=task_attrs,
            )
        )
        expected_by_key[expected_key] = {
            "source_index": source_index,
            "job_id": job_facts.job_id,
            "job_url": job_facts.job_url,
            "expected_task_span_id": task_span_id,
            "emitted": True,
            "omitted": False,
            "omit_reason": None,
        }
        # Exact Run bounds: Job after completed Run is inconsistent.
        # Observed upper-bound Root already refused earlier if observed < job end.
        if (
            time_precision == TIME_PRECISION_EXACT_INPUT
            and run_job_temporal_inconsistency(job_facts)
            and "inconsistent_run_job_timestamps" not in reasons
        ):
            reasons.append("inconsistent_run_job_timestamps")

    expected_completed = []
    if expected_job_stubs:
        for stub in expected_job_stubs:
            source_index = stub.get("source_index")
            job_id = stub.get("job_id")
            key = ("id", job_id) if job_id is not None else ("index", source_index)
            detail = expected_by_key.get(key)
            if detail is None:
                # Diagnostic --job-id path: unselected completed Jobs are coverage
                # gaps, not fabricated omissions.
                _, _, expected_span = _stable_ids(
                    facts.run_id, facts.run_attempt, job_id
                )
                expected_completed.append(
                    {
                        "source_index": source_index,
                        "job_id": job_id,
                        "job_url": stub.get("job_url"),
                        "expected_task_span_id": expected_span,
                        "emitted": False,
                        "omitted": False,
                        "omit_reason": "not_selected_job_id_slice",
                    }
                )
            else:
                expected_completed.append(detail)
    else:
        expected_completed = list(expected_by_key.values())

    report["spans"] = spans
    report["coverage"]["tasks_omitted"] = omitted
    report["coverage"]["expected_completed_jobs"] = expected_completed
    report["coverage"]["task_spans_emitted"] = sum(
        1 for span in spans if span["kind"] == SPAN_KIND_INTERNAL
    )
    emitted = report["coverage"]["task_spans_emitted"]
    if (
        completed_jobs_total is not None
        and completed_jobs_total > emitted
        and job_selection in {"job_id", "all_completed"}
        and "partial_job_coverage" not in reasons
    ):
        # --job-id is a diagnostic slice; all_completed may omit incomplete Jobs.
        reasons.append("partial_job_coverage")

    if observation_ready:
        time_precision = TIME_PRECISION_OBSERVED_UPPER_BOUND
        time_source_verified = False
        interval = _observation_interval(
            job_sources,
            observed_ns=terminal_observed_at_ns,
            run_started_ns=facts.run_started_at_ns,
            completed_jobs_total=completed_jobs_total,
            job_selection=job_selection,
            omitted_count=len(omitted),
        )
        report["terminal"].update(interval)
        if not interval["bounds_valid"]:
            root_emission = "not_emitted"
            root_emission_reason = interval["bounds_invalid_reason"]
            reasons.append("end_time_uncertain")
        elif max_end_uncertainty_ns is None:
            root_emission = "not_emitted"
            root_emission_reason = "end_time_uncertain"
            reasons.append("end_time_uncertain")
        elif interval["uncertainty_ns"] > max_end_uncertainty_ns:
            root_emission = "not_emitted"
            root_emission_reason = "uncertainty_exceeds_budget"
            reasons.append("end_time_uncertain")
        else:
            root_end_ns = interval["end_upper_bound_ns"]
            root_emission = "approximate_root"
            run_end_source = "terminal_observed_at"
            duration_valid_for_metrics = False
            reasons.append("temporal_precision_unverified")
            spans.insert(
                0,
                _span(
                    f"RUN {facts.workflow_name}",
                    kind=SPAN_KIND_SERVER,
                    trace_id=trace_id,
                    span_id=pipeline_span_id,
                    parent_span_id=None,
                    start_ns=facts.run_started_at_ns,
                    end_ns=root_end_ns,
                    attributes={
                        ATTR_PIPELINE_NAME: facts.workflow_name,
                        ATTR_PIPELINE_RUN_ID: pipeline_run_id,
                        ATTR_PIPELINE_RUN_URL: facts.run_url,
                        ATTR_PIPELINE_RESULT: run_result,
                        ATTR_GITHUB_RUN_ATTEMPT: facts.run_attempt,
                        ATTR_VCS_HEAD_REVISION: facts.head_sha,
                        ATTR_ERROR_TYPE: run_error_type,
                        ATTR_ECI_END_TIME_QUALITY: time_precision,
                        ATTR_ECI_EXACT_RUN_DURATION: False,
                        ATTR_ECI_DURATION_VALID_FOR_METRICS: False,
                        ATTR_ECI_ROOT_EMISSION: "approximate_root",
                    },
                ),
            )
    report["terminal"]["root_emission"] = root_emission
    report["terminal"]["root_emission_reason"] = root_emission_reason
    report["terminal"]["duration_valid_for_metrics"] = duration_valid_for_metrics
    report["terminal"]["end_time_quality"] = time_precision
    report["terminal"]["end_time_source"] = run_end_source
    report["terminal"]["time_precision"] = time_precision
    report["terminal"]["time_source_verified"] = time_source_verified
    report["terminal"]["exact_run_duration"] = (
        time_precision == TIME_PRECISION_EXACT_INPUT
    )
    report["coverage"]["job_execution_envelope"] = _job_execution_envelope(
        job_sources,
        source=source,
        facts=facts,
        completed_jobs_total=completed_jobs_total,
        job_selection=job_selection,
        omitted_count=len(omitted),
    )

    pipeline_ok = any(span["kind"] == SPAN_KIND_SERVER for span in spans)
    task_ok = any(span["kind"] == SPAN_KIND_INTERNAL for span in spans)
    report["coverage"]["pipeline_terminal_covered"] = pipeline_ok
    if task_ok and not pipeline_ok:
        report["coverage"]["withheld_parent_span"] = {
            "trace_id": trace_id,
            "span_id": pipeline_span_id,
            "exported": False,
            "reason": root_emission_reason or "root_not_emitted",
        }
    else:
        report["coverage"]["withheld_parent_span"] = None
    if pipeline_ok:
        report["coverage"]["terminal_check"] = "not_checked"
    elif report["terminal"].get("terminal_confirmed"):
        report["coverage"]["terminal_check"] = "not_verified"
    else:
        report["coverage"]["terminal_check"] = "not_applicable"
    if completed_jobs_total == 0 and not job_sources:
        report["coverage"]["task_coverage"] = "empty"
    elif (
        completed_jobs_total is not None
        and completed_jobs_total == emitted
        and not omitted
        and job_selection == "all_completed"
    ):
        report["coverage"]["task_coverage"] = "complete"
    elif task_ok or omitted:
        report["coverage"]["task_coverage"] = "partial"
    else:
        report["coverage"]["task_coverage"] = "none"

    if pipeline_ok or report["terminal"].get("terminal_confirmed"):
        report["terminal_provenance"] = {
            "repository": facts.repository,
            "run_id": facts.run_id,
            "attempt": facts.run_attempt,
            "root_trace_id": trace_id,
            "root_span_id": pipeline_span_id if pipeline_ok else None,
            "root_emitted": pipeline_ok,
            "terminal_source": source,
            "terminal_status": facts.run_status,
            "terminal_conclusion": facts.run_conclusion,
            "terminal_observed_at_ns": terminal_observed_at_ns,
            "end_time_source": run_end_source,
            "end_time_quality": time_precision,
            "time_precision": time_precision,
            "time_source_verified": time_source_verified,
            "exact_run_duration": time_precision == TIME_PRECISION_EXACT_INPUT,
            "duration_valid_for_metrics": duration_valid_for_metrics,
            "root_emission": root_emission,
            "root_emission_reason": root_emission_reason,
            "end_lower_bound_ns": report["terminal"].get("end_lower_bound_ns"),
            "end_upper_bound_ns": report["terminal"].get("end_upper_bound_ns"),
            "uncertainty_ns": report["terminal"].get("uncertainty_ns"),
            "bounds_valid": report["terminal"].get("bounds_valid"),
            "otlp_sha256": None,
        }

    if not spans:
        report["status"] = "incomplete"
        report["reason"] = reasons[0] if reasons else "no_candidate_spans"
        return report

    # Pipeline-only Root is valid when the Attempt has no Task obligation
    # (e.g. cancelled before Jobs). Exact dual coverage without reasons → produced.
    no_task_obligation = not job_sources and completed_jobs_total in {None, 0}
    if pipeline_ok and task_ok and not reasons:
        report["status"] = "produced"
        report["reason"] = None
        return report
    if pipeline_ok and no_task_obligation and not reasons:
        report["status"] = "produced"
        report["reason"] = None
        return report
    report["status"] = "partial"
    report["reason"] = reasons[0] if reasons else "partial_candidate_spans"
    return report


def write_artifact(report, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Do not embed nested OTLP documents twice in the candidate artifact.
    skip = {"otlp_json", "otlp_logs_json", "otlp_metrics_json"}
    payload = {key: value for key, value in report.items() if key not in skip}
    # Authoritative export claim lives in claims.*; mirror consistently.
    claims = report.get("claims") or {}
    payload["otlp_json_prepared"] = bool(claims.get("otlp_json_prepared"))
    payload["otlp_json_exported"] = bool(claims.get("otlp_json_exported"))
    payload["otlp_logs_prepared"] = bool(claims.get("otlp_logs_prepared"))
    payload["otlp_logs_exported"] = bool(claims.get("otlp_logs_exported"))
    payload["otlp_metrics_prepared"] = bool(claims.get("otlp_metrics_prepared"))
    payload["otlp_metrics_exported"] = bool(claims.get("otlp_metrics_exported"))
    # Binary write keeps LF so recorded digests match file bytes on Windows.
    path.write_bytes(
        (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
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
        report["claims"]["otlp_json_prepared"] = False
        report["claims"]["otlp_json_exported"] = False
        return report
    report["otlp_json"] = to_otlp_json(report)
    report["claims"]["otlp_json_prepared"] = True
    # Prepared ≠ exported; file flush alone may set exported.
    report["claims"]["otlp_json_exported"] = False
    return report


def write_otlp_json(report, path):
    """Flush OTLP JSON artifact. Failure is an explicit export error, not produced."""
    if not report.get("otlp_json"):
        raise TelemetryError("otlp_json_unavailable")
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = (
            json.dumps(report["otlp_json"], indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        # Binary write keeps LF so claims.otlp_json_sha256 matches file bytes.
        path.write_bytes(body)
    except OSError as error:
        report["claims"]["otlp_json_exported"] = False
        raise TelemetryError("otlp_json_flush_failed") from error
    digest = hashlib.sha256(body).hexdigest()
    report["claims"]["otlp_json_exported"] = True
    report["claims"]["otlp_json_path"] = str(path)
    report["claims"]["otlp_json_sha256"] = digest
    provenance = report.get("terminal_provenance")
    if isinstance(provenance, dict):
        provenance["otlp_sha256"] = digest
    # File artifact only — not live OTLP/gRPC delivery.
    if report["claims"].get("snapshot_phase") == "before_receiver_confirmation":
        report["claims"]["otlp_delivered"] = False
    return path


_EXPORT_KEYS = {
    "traces": (
        "rejected_spans",
        "rpc_accepted",
        "otlp_rpc_rejected_spans",
        "otlp_rpc_warning",
        "otlp_delivered",
        "receiver_observed",
    ),
    "logs": (
        "rejected_log_records",
        "otlp_logs_rpc_accepted",
        "otlp_logs_rpc_rejected",
        "otlp_logs_rpc_warning",
        "otlp_logs_delivered",
        "otlp_logs_receiver_observed",
    ),
    "metrics": (
        "rejected_data_points",
        "otlp_metrics_rpc_accepted",
        "otlp_metrics_rpc_rejected",
        "otlp_metrics_rpc_warning",
        "otlp_metrics_delivered",
        "otlp_metrics_receiver_observed",
    ),
}


def _grpc_telemetry_error(error):
    code = getattr(error, "code", None)
    detail = code() if callable(code) else None
    name = getattr(detail, "name", None) if detail is not None else None
    if name == "DEADLINE_EXCEEDED":
        return TelemetryError("otlp_grpc_timeout")
    if name in {"UNAVAILABLE", "FAILED_PRECONDITION"}:
        return TelemetryError("otlp_grpc_unavailable_endpoint")
    return TelemetryError("otlp_grpc_failed")


def _service_stub(signal, channel, factories):
    if signal == "traces" and factories.get("traces") is not None:
        return factories["traces"](channel)
    if signal == "logs" and factories.get("logs") is not None:
        return factories["logs"](channel)
    if signal == "metrics" and factories.get("metrics") is not None:
        return factories["metrics"](channel)
    if signal == "traces":
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2_grpc import (
            TraceServiceStub,
        )

        return TraceServiceStub(channel)
    if signal == "logs":
        from opentelemetry.proto.collector.logs.v1.logs_service_pb2_grpc import (
            LogsServiceStub,
        )

        return LogsServiceStub(channel)
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2_grpc import (
        MetricsServiceStub,
    )

    return MetricsServiceStub(channel)


def _export_signal(report, stub, request, signal, endpoint, timeout):
    field, accepted, rejected_key, warning_key, delivered, observed = _EXPORT_KEYS[
        signal
    ]
    try:
        response = stub.Export(request, timeout=timeout)
    except TelemetryError:
        raise
    except Exception as error:
        report["claims"][accepted] = False
        report["claims"][delivered] = False
        report["claims"][observed] = False
        raise _grpc_telemetry_error(error) from error
    partial = getattr(response, "partial_success", None)
    rejected = int(getattr(partial, field, 0) or 0) if partial else 0
    warning = str(getattr(partial, "error_message", "") or "") if partial else ""
    if signal == "traces":
        report["claims"]["otlp_rpc_endpoint"] = endpoint
    else:
        report["claims"][f"otlp_{signal}_rpc_endpoint"] = endpoint
    report["claims"][rejected_key] = rejected
    if warning:
        report["claims"][warning_key] = warning
    if rejected > 0:
        report["claims"][accepted] = False
        report["claims"][delivered] = False
        report["claims"][observed] = False
        raise TelemetryError("otlp_grpc_partial_reject")
    report["claims"][accepted] = True
    # ACK is not receiver observation, delivery, Weaver PASS, or trust.
    report["claims"][delivered] = False
    report["claims"][observed] = False
    return response


def export_otlp_grpc(
    report,
    endpoint,
    *,
    insecure=False,
    timeout=30.0,
    channel_factory=None,
    stub_factory=None,
    logs_stub_factory=None,
    metrics_stub_factory=None,
):
    """Export prepared Traces and any prepared Logs/Metrics on one gRPC channel.

    Each signal records its own RPC acceptance. None of them set receiver
    observation, controlled delivery, Weaver PASS, or trust.
    """
    planned = []
    if report.get("otlp_json"):
        planned.append(("traces", decode_otlp_export_request(report["otlp_json"])))
    if report.get("otlp_logs_json"):
        planned.append(("logs", decode_otlp_logs_request(report["otlp_logs_json"])))
    if report.get("otlp_metrics_json"):
        planned.append(
            ("metrics", decode_otlp_metrics_request(report["otlp_metrics_json"]))
        )
    if not planned:
        raise TelemetryError("otlp_json_unavailable")
    if not endpoint or not str(endpoint).strip():
        raise TelemetryError("otlp_endpoint_invalid")
    endpoint = str(endpoint).strip()
    try:
        import grpc
    except ImportError as error:
        raise TelemetryError("otlp_grpc_unavailable") from error

    factories = {
        "traces": stub_factory,
        "logs": logs_stub_factory,
        "metrics": metrics_stub_factory,
    }
    channel = None
    close_channel = False
    responses = {}
    try:
        if channel_factory is not None:
            channel = channel_factory(endpoint, insecure=insecure)
        elif insecure:
            channel = grpc.insecure_channel(endpoint)
            close_channel = True
        else:
            channel = grpc.secure_channel(endpoint, grpc.ssl_channel_credentials())
            close_channel = True
        for signal, request in planned:
            stub = _service_stub(signal, channel, factories)
            responses[signal] = _export_signal(
                report, stub, request, signal, endpoint, timeout
            )
    finally:
        if close_channel and channel is not None:
            channel.close()
    return responses


def _id_counts(items):
    counts = {}
    for item in items:
        text = str(item)
        counts[text] = counts.get(text, 0) + 1
    return counts


def _reconcile_ids(expected_ids, observed_ids):
    expected = _id_counts(expected_ids)
    observed = _id_counts(observed_ids)
    matched, missing, unexpected, duplicate = [], [], [], []
    for key in sorted(set(expected) | set(observed)):
        want = expected.get(key, 0)
        seen = observed.get(key, 0)
        if want == 0:
            unexpected.extend([key] * seen)
            continue
        if seen > want:
            duplicate.append(key)
            matched.extend([key] * want)
            continue
        matched.extend([key] * seen)
        if seen < want:
            missing.extend([key] * (want - seen))
    return matched, missing, unexpected, duplicate


def _expected_signal_ids(report, signal):
    if signal == "traces":
        return [
            span["span_id"] for span in report.get("spans") or [] if span.get("span_id")
        ]
    if signal == "logs":
        logs = (report.get("signals") or {}).get("logs") or []
        return [item["span_id"] for item in logs if item.get("span_id")]
    if signal == "metrics":
        identities = []
        for metric in (report.get("signals") or {}).get("metrics") or []:
            points = (metric.get("histogram") or {}).get("data_points") or []
            for point in points:
                state = (point.get("attributes") or {}).get(ATTR_PIPELINE_RUN_STATE)
                identities.append(f"{metric.get('name')}|{state}")
        return identities
    raise TelemetryError("otlp_receiver_signal_invalid")


def _single_trace_id(report, signal):
    if signal == "traces":
        values = [span.get("trace_id") for span in report.get("spans") or []]
    elif signal == "logs":
        logs = (report.get("signals") or {}).get("logs") or []
        values = [item.get("trace_id") for item in logs]
    else:
        return None
    unique = {item for item in values if item}
    if len(unique) != 1:
        return None
    return next(iter(unique))


def _identity_matches(report, *, run_id, attempt, repository):
    facts = report.get("facts") or {}
    if str(run_id) != str(facts.get("run_id")):
        return False
    try:
        attempt_value = int(attempt)
    except (TypeError, ValueError):
        return False
    if attempt_value != facts.get("run_attempt"):
        return False
    return repository is None or repository == facts.get("repository")


def _prepared_signals(report):
    names = []
    if report.get("otlp_json"):
        names.append("traces")
    if report.get("otlp_logs_json"):
        names.append("logs")
    if report.get("otlp_metrics_json"):
        names.append("metrics")
    return names


def _sync_delivery_claims(report):
    """Top-level ``otlp_delivered`` is the historical whole-bundle aggregate.

    It is true only when every prepared signal was observed. Per-signal
    delivery remains ``transport.<signal>.otlp_delivered``.
    ``bundle_delivery_complete`` is the explicit name for the same aggregate.
    """
    claims = report["claims"]
    transport = claims.setdefault("transport", {})
    prepared = _prepared_signals(report)
    confirmed = [
        name
        for name in prepared
        if (transport.get(name) or {}).get("receiver_observed")
    ]
    all_delivered = bool(prepared) and len(confirmed) == len(prepared)
    claims["all_prepared_signals_delivered"] = all_delivered
    claims["bundle_delivery_complete"] = all_delivered
    claims["otlp_delivered"] = all_delivered
    if not confirmed:
        claims["snapshot_phase"] = "before_receiver_confirmation"
    elif all_delivered:
        claims["snapshot_phase"] = "receiver_confirmed"
    else:
        claims["snapshot_phase"] = "partial_receiver_confirmation"
    traces = transport.get("traces") or {}
    if "traces" in prepared:
        claims["receiver_observed"] = all_delivered and bool(
            traces.get("receiver_observed")
        )
    claims["candidate_record_only"] = True
    claims["trusted"] = False
    claims["weaver_pass"] = False


def mark_receiver_observed(
    report,
    *,
    signal,
    observed_ids,
    rpc_accepted,
    rejected=0,
    run_id,
    attempt,
    trace_id=None,
    repository=None,
):
    """Record one signal's receiver capture. Does not grant trust or Weaver PASS.

    ``observed_ids`` are the identities the receiver actually stored: Trace Span
    IDs, LogRecord Span IDs, or ``metric|state`` keys. A caller-supplied list is
    only accepted together with RPC acceptance, zero rejects, and the same
    Run/Attempt (and Trace ID for Traces and Logs).
    """
    if signal not in _EXPORT_KEYS:
        raise TelemetryError("otlp_receiver_signal_invalid")
    _field, accepted_key, _rejected_key, _warning, delivered_key, observed_key = (
        _EXPORT_KEYS[signal]
    )
    expected_ids = _expected_signal_ids(report, signal)
    matched, missing, unexpected, duplicate = _reconcile_ids(expected_ids, observed_ids)
    claims = report["claims"]
    reason = None
    if not rpc_accepted or not claims.get(accepted_key) or int(rejected or 0) != 0:
        reason = "otlp_receiver_rpc_unaccepted"
    elif not _identity_matches(
        report, run_id=run_id, attempt=attempt, repository=repository
    ):
        reason = "otlp_receiver_identity_mismatch"
    elif signal != "metrics":
        expected_trace = _single_trace_id(report, signal)
        if not expected_trace or trace_id != expected_trace:
            reason = "otlp_receiver_trace_mismatch"
    elif not expected_ids or missing:
        reason = "otlp_receiver_incomplete"
    elif unexpected:
        reason = "otlp_receiver_unexpected"
    elif duplicate:
        reason = "otlp_receiver_duplicate"
    if signal != "metrics" and reason is None:
        if not expected_ids or missing:
            reason = "otlp_receiver_incomplete"
        elif unexpected:
            reason = "otlp_receiver_unexpected"
        elif duplicate:
            reason = "otlp_receiver_duplicate"
    success = reason is None
    record = {
        "snapshot_phase": (
            "receiver_confirmed" if success else "before_receiver_confirmation"
        ),
        "rpc_accepted": bool(claims.get(accepted_key)) and bool(rpc_accepted),
        "rejected": int(rejected or 0),
        "run_id": str(run_id),
        "attempt": attempt,
        "trace_id": trace_id,
        "expected": expected_ids,
        "matched": matched,
        "missing": missing,
        "duplicate": duplicate,
        "unexpected": unexpected,
        "receiver_observed": success,
        "otlp_delivered": success,
        "reason": reason,
    }
    claims.setdefault("transport", {})[signal] = record
    if signal != "traces":
        claims[observed_key] = success
        claims[delivered_key] = success
    _sync_delivery_claims(report)
    if not success:
        raise TelemetryError(reason)
    return report


def _severity_for_result(result):
    if result in {"failure", "error", "timeout"}:
        return 17, "ERROR"
    if result in {"cancellation", "skip"}:
        return 13, "WARN"
    if result == "success":
        return 9, "INFO"
    return 0, "UNSPECIFIED"


def build_job_terminal_log_records(report):
    """Build LogRecords from emitted Task Spans (GitHub Job terminal facts).

    Source is the Attempt Job API terminal already mapped into Task Spans — not
    a fabricated stdout stream. Full Job log archives remain on GitHub Actions.
    When the Pipeline Root span is withheld, one Attempt-terminal LogRecord
    carries the confirmed status and the end-time bounds. It is not a duration.
    """
    records = []
    for span in report.get("spans") or []:
        if span.get("kind") != SPAN_KIND_INTERNAL:
            continue
        attrs = span.get("attributes") or {}
        result = attrs.get(ATTR_TASK_RUN_RESULT)
        severity_number, severity_text = _severity_for_result(result)
        body = f"GitHub Job terminal {attrs.get(ATTR_TASK_NAME)} conclusion={result}"
        records.append(
            {
                "time_unix_nano": span.get("end_time_unix_nano"),
                "severity_number": severity_number,
                "severity_text": severity_text,
                "body": body,
                "attributes": {
                    ATTR_TASK_NAME: attrs.get(ATTR_TASK_NAME),
                    ATTR_TASK_RUN_ID: attrs.get(ATTR_TASK_RUN_ID),
                    ATTR_TASK_RUN_RESULT: result,
                    ATTR_TASK_RUN_URL: attrs.get(ATTR_TASK_RUN_URL),
                    ATTR_PIPELINE_RUN_ID: attrs.get(ATTR_PIPELINE_RUN_ID),
                    ATTR_GITHUB_RUN_ATTEMPT: attrs.get(ATTR_GITHUB_RUN_ATTEMPT),
                    "eci.log.source": "github_job_terminal",
                },
                "trace_id": span.get("trace_id"),
                "span_id": span.get("span_id"),
            }
        )
    records.extend(_attempt_terminal_log(report))
    return records


def _attempt_terminal_log(report):
    """One correlated terminal fact when no Root span was exported."""
    terminal = report.get("terminal") or {}
    if terminal.get("root_emission") != "not_emitted":
        return []
    if not terminal.get("terminal_confirmed"):
        return []
    observed = terminal.get("observed_at_ns")
    if type(observed) is not int:
        return []
    facts = report.get("facts") or {}
    trace_id, span_id, _ = _stable_ids(facts.get("run_id"), facts.get("run_attempt"))
    resource = report.get("resource") or {}
    return [
        {
            "time_unix_nano": observed,
            "severity_number": 9,
            "severity_text": "INFO",
            "body": (
                "GitHub Attempt terminal confirmed; "
                "pipeline end time is an unbound observation, not a span end"
            ),
            "attributes": {
                ATTR_PIPELINE_RUN_ID: resource.get(ATTR_PIPELINE_RUN_ID),
                ATTR_GITHUB_RUN_ATTEMPT: facts.get("run_attempt"),
                "eci.log.source": "github_attempt_terminal",
                "eci.cicd.time_role": "terminal_observed_at",
                "eci.cicd.root_emission": "not_emitted",
                "eci.cicd.end_lower_bound_ns": terminal.get("end_lower_bound_ns"),
                "eci.cicd.end_upper_bound_ns": terminal.get("end_upper_bound_ns"),
                "eci.cicd.uncertainty_ns": terminal.get("uncertainty_ns"),
                "eci.cicd.parent_span_exported": False,
            },
            "trace_id": trace_id,
            "span_id": span_id,
        }
    ]


def _pipeline_span(report):
    return next(
        (
            span
            for span in report.get("spans") or []
            if span.get("kind") == SPAN_KIND_SERVER
        ),
        None,
    )


def _duration_not_observable(state, reason):
    return {
        "name": DURATION_METRIC_NAME,
        "state": state,
        "status": "not_observable",
        "reason": reason,
    }


def build_metrics_plan(report):
    """Histogram points for proven phase intervals only.

    A whole-run start/end is not ``finalizing``. States without a supplied
    window are ``not_observable``. Run id is attached only when opted in.
    """
    not_observable = []
    points = []
    resource = report.get("resource") or {}
    pipeline_attrs = (_pipeline_span(report) or {}).get("attributes") or {}
    include_run = bool(report.get("include_run_entity_metrics"))
    grouped = {PHASE_PENDING: [], PHASE_EXECUTING: [], PHASE_FINALIZING: []}
    for item in report.get("phase_intervals") or []:
        state = item.get("state")
        if state in grouped:
            grouped[state].append(item)
    for state in (PHASE_PENDING, PHASE_EXECUTING, PHASE_FINALIZING):
        group = grouped[state]
        if not group:
            not_observable.append(
                _duration_not_observable(state, "phase_interval_unproven")
            )
            continue
        rejected = next(
            (item.get("reject") for item in group if item.get("reject")), None
        )
        if rejected:
            not_observable.append(_duration_not_observable(state, rejected))
            continue
        if any(not item.get("valid") for item in group):
            not_observable.append(
                _duration_not_observable(state, "invalid_phase_interval")
            )
            continue
        for item in group:
            start = item["start_ns"]
            end = item["end_ns"]
            duration_s = (end - start) / 1_000_000_000
            attributes = {
                ATTR_PIPELINE_NAME: resource.get(ATTR_PIPELINE_NAME),
                ATTR_PIPELINE_RUN_STATE: state,
            }
            result = item.get("result")
            if result is None and state == PHASE_FINALIZING:
                result = pipeline_attrs.get(ATTR_PIPELINE_RESULT)
            if result is not None:
                attributes[ATTR_PIPELINE_RESULT] = result
            if result in {"failure", "error", "timeout"}:
                error_type = pipeline_attrs.get(ATTR_ERROR_TYPE)
                if error_type is not None:
                    attributes[ATTR_ERROR_TYPE] = error_type
            if include_run and resource.get(ATTR_PIPELINE_RUN_ID) is not None:
                attributes[ATTR_PIPELINE_RUN_ID] = resource[ATTR_PIPELINE_RUN_ID]
            bounds, counts = _histogram_buckets(duration_s)
            points.append(
                {
                    "start_time_unix_nano": start,
                    "time_unix_nano": end,
                    "count": 1,
                    "sum": duration_s,
                    "min": duration_s,
                    "max": duration_s,
                    "bucket_counts": counts,
                    "explicit_bounds": bounds,
                    "attributes": attributes,
                }
            )
    metrics = []
    if points:
        metrics.append(
            {
                "name": DURATION_METRIC_NAME,
                "unit": "s",
                "description": (
                    "Duration of a pipeline run grouped by pipeline, state and result."
                ),
                "histogram": {
                    "aggregation_temporality": "delta",
                    "data_points": points,
                },
            }
        )
    not_observable.append(
        {
            "name": ACTIVE_METRIC_NAME,
            "status": "not_observable",
            "reason": "active_count_unproven",
        }
    )
    return metrics, not_observable


def attach_signal_candidates(report):
    """Attach O2/O3 candidate LogRecords and Metrics decisions to the report."""
    logs = build_job_terminal_log_records(report)
    metrics, not_observable = build_metrics_plan(report)
    report["signals"] = {
        "logs": logs,
        "metrics": metrics,
        "metrics_not_observable": not_observable,
        "logs_source": "github_job_terminal" if logs else None,
    }
    report["otlp_logs_json"] = to_otlp_logs_json(report) if logs else None
    report["otlp_metrics_json"] = to_otlp_metrics_json(report) if metrics else None
    report["claims"]["otlp_logs_prepared"] = bool(report["otlp_logs_json"])
    report["claims"]["otlp_metrics_prepared"] = bool(report["otlp_metrics_json"])
    report["claims"]["otlp_logs_exported"] = False
    report["claims"]["otlp_metrics_exported"] = False
    return report


def to_otlp_logs_json(report):
    """Encode Job-terminal LogRecords as ExportLogsServiceRequest JSON."""
    resource_attrs = [
        _otlp_attr(key, value) for key, value in report.get("resource", {}).items()
    ]
    log_records = []
    for item in (report.get("signals") or {}).get("logs") or []:
        record = {
            "timeUnixNano": str(item["time_unix_nano"]),
            "severityNumber": item["severity_number"],
            "severityText": item["severity_text"],
            "body": {"stringValue": item["body"]},
            "attributes": [
                _otlp_attr(key, value)
                for key, value in item["attributes"].items()
                if value is not None
            ],
        }
        if item.get("trace_id"):
            record["traceId"] = item["trace_id"]
        if item.get("span_id"):
            record["spanId"] = item["span_id"]
        log_records.append(record)
    return {
        "resourceLogs": [
            {
                "resource": {"attributes": resource_attrs},
                "scopeLogs": [
                    {
                        "scope": {
                            "name": OTLP_SCOPE_NAME,
                            "version": str(SCHEMA_VERSION),
                        },
                        "logRecords": log_records,
                    }
                ],
            }
        ]
    }


_RUN_LEVEL_METRIC_ATTRS = (
    ATTR_PIPELINE_RUN_ID,
    ATTR_PIPELINE_RUN_URL,
    ATTR_GITHUB_RUN_ATTEMPT,
    ATTR_VCS_HEAD_REVISION,
)


def _metrics_resource(report):
    """Copy resource for Metrics. Run identity stays opt-in."""
    resource = dict(report.get("resource") or {})
    if not report.get("include_run_entity_metrics"):
        for key in _RUN_LEVEL_METRIC_ATTRS:
            resource.pop(key, None)
    return resource


def to_otlp_metrics_json(report):
    """Encode phase Histograms as ExportMetricsServiceRequest JSON."""
    resource_attrs = [
        _otlp_attr(key, value) for key, value in _metrics_resource(report).items()
    ]
    metrics = []
    for item in (report.get("signals") or {}).get("metrics") or []:
        histogram = item["histogram"]
        data_points = []
        for point in histogram["data_points"]:
            data_points.append(
                {
                    "attributes": [
                        _otlp_attr(key, value)
                        for key, value in point["attributes"].items()
                        if value is not None
                    ],
                    "startTimeUnixNano": str(point["start_time_unix_nano"]),
                    "timeUnixNano": str(point["time_unix_nano"]),
                    "count": str(point["count"]),
                    "sum": point["sum"],
                    "min": point["min"],
                    "max": point["max"],
                    "bucketCounts": [str(count) for count in point["bucket_counts"]],
                    "explicitBounds": list(point["explicit_bounds"]),
                }
            )
        metrics.append(
            {
                "name": item["name"],
                "description": item.get("description") or "",
                "unit": item.get("unit") or "",
                "histogram": {
                    "aggregationTemporality": _HISTOGRAM_DELTA,
                    "dataPoints": data_points,
                },
            }
        )
    return {
        "resourceMetrics": [
            {
                "resource": {"attributes": resource_attrs},
                "scopeMetrics": [
                    {
                        "scope": {
                            "name": OTLP_SCOPE_NAME,
                            "version": str(SCHEMA_VERSION),
                        },
                        "metrics": metrics,
                    }
                ],
            }
        ]
    }


def write_otlp_logs_json(report, path):
    if not report.get("otlp_logs_json"):
        raise TelemetryError("otlp_logs_unavailable")
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = (
            json.dumps(report["otlp_logs_json"], indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        path.write_bytes(body)
    except OSError as error:
        report["claims"]["otlp_logs_exported"] = False
        raise TelemetryError("otlp_logs_flush_failed") from error
    report["claims"]["otlp_logs_exported"] = True
    report["claims"]["otlp_logs_path"] = str(path)
    report["claims"]["otlp_logs_sha256"] = hashlib.sha256(body).hexdigest()
    return path


def write_otlp_metrics_json(report, path):
    if not report.get("otlp_metrics_json"):
        raise TelemetryError("otlp_metrics_unavailable")
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = (
            json.dumps(report["otlp_metrics_json"], indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        path.write_bytes(body)
    except OSError as error:
        report["claims"]["otlp_metrics_exported"] = False
        raise TelemetryError("otlp_metrics_flush_failed") from error
    report["claims"]["otlp_metrics_exported"] = True
    report["claims"]["otlp_metrics_path"] = str(path)
    report["claims"]["otlp_metrics_sha256"] = hashlib.sha256(body).hexdigest()
    return path


def otlp_wire_to_protobuf_json(payload):
    """Copy OTLP/JSON wire form → protobuf JSON (hex IDs → base64 bytes).

    Matches the deterministic conversion used by the #74 Weaver replay path.
    Does not claim Weaver PASS or live delivery.
    """
    if not isinstance(payload, dict):
        raise TelemetryError("otlp_json_invalid")
    capture = json.loads(json.dumps(payload))

    def rewrite(record, key, size):
        value = record.get(key)
        if not value:
            return
        try:
            decoded = bytes.fromhex(value)
        except (TypeError, ValueError) as error:
            raise TelemetryError("otlp_id_invalid") from error
        if len(decoded) != size:
            raise TelemetryError("otlp_id_invalid")
        record[key] = base64.b64encode(decoded).decode()

    for resource in capture.get("resourceSpans") or []:
        for scope in resource.get("scopeSpans") or []:
            for span in scope.get("spans") or []:
                for record in [span, *(span.get("links") or [])]:
                    rewrite(record, "traceId", 16)
                    rewrite(record, "spanId", 8)
                    rewrite(record, "parentSpanId", 8)
    for resource in capture.get("resourceLogs") or []:
        for scope in resource.get("scopeLogs") or []:
            for record in scope.get("logRecords") or []:
                rewrite(record, "traceId", 16)
                rewrite(record, "spanId", 8)
    return capture


def decode_otlp_export_request(payload):
    """Parse producer OTLP/JSON with the official ExportTraceServiceRequest schema.

    Requires ``opentelemetry-proto`` (dev/CI). Converts wire hex IDs first.
    """
    try:
        from google.protobuf import json_format
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
            ExportTraceServiceRequest,
        )
    except ImportError as error:
        raise TelemetryError("otlp_proto_unavailable") from error
    converted = otlp_wire_to_protobuf_json(payload)
    try:
        return json_format.ParseDict(converted, ExportTraceServiceRequest())
    except json_format.ParseError as error:
        raise TelemetryError("otlp_json_invalid") from error


def decode_otlp_logs_request(payload):
    """Parse ExportLogsServiceRequest. Converts wire hex IDs first."""
    try:
        from google.protobuf import json_format
        from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
            ExportLogsServiceRequest,
        )
    except ImportError as error:
        raise TelemetryError("otlp_proto_unavailable") from error
    converted = otlp_wire_to_protobuf_json(payload)
    try:
        return json_format.ParseDict(converted, ExportLogsServiceRequest())
    except json_format.ParseError as error:
        raise TelemetryError("otlp_logs_invalid") from error


def decode_otlp_metrics_request(payload):
    """Parse ExportMetricsServiceRequest, including Histogram datapoints."""
    try:
        from google.protobuf import json_format
        from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
            ExportMetricsServiceRequest,
        )
    except ImportError as error:
        raise TelemetryError("otlp_proto_unavailable") from error
    try:
        return json_format.ParseDict(payload, ExportMetricsServiceRequest())
    except json_format.ParseError as error:
        raise TelemetryError("otlp_metrics_invalid") from error


def _server_url_from_html(html):
    if not isinstance(html, str) or not html.startswith("http"):
        return "https://github.com"
    parts = urlsplit(html)
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return "https://github.com"


def facts_from_github_api_objects(run, job=None):
    """Map GitHub Actions Run Attempt (+ optional Job) API objects to facts.

    Run `updated_at` is retained as `run_updated_at_ns` provenance only. It is
    **not** assigned to `run_completed_at_ns`: GitHub does not define
    `updated_at` as the Attempt's exact completion instant (PR #77 P1-2).
    Exact Pipeline end bounds require an explicit completed-at source
    (`ENGINEERING_CI_RUN_COMPLETED_AT` / mapping). Job bounds use Job
    `started_at` / `completed_at`.
    """
    if not isinstance(run, dict):
        raise TelemetryError("run_payload_invalid")
    repository = None
    remote = run.get("repository")
    if isinstance(remote, dict):
        repository = remote.get("full_name")
    repository = repository or run.get("repository_full_name")
    html = run.get("html_url")
    server = _server_url_from_html(html)
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
        # Never fabricate exact completed-at from updated_at.
        run_completed_at_ns=None,
        job_started_at_ns=job_started,
        job_completed_at_ns=job_completed,
        run_updated_at_ns=parse_time_ns(run.get("updated_at")),
        run_url=html if isinstance(html, str) else None,
        run_status=run.get("status") if isinstance(run.get("status"), str) else None,
    )


def _completed_jobs(jobs):
    if not isinstance(jobs, list):
        raise TelemetryError("jobs_payload_invalid")
    return [
        job
        for job in jobs
        if isinstance(job, dict) and job.get("status") == "completed" and job.get("id")
    ]


def _select_job(jobs, job_id=None):
    """Resolve one Job for ``--job-id``, or the sole completed Job when unambiguous."""
    if not isinstance(jobs, list):
        raise TelemetryError("jobs_payload_invalid")
    if job_id is not None:
        wanted = str(job_id)
        for job in jobs:
            if str(job.get("id")) == wanted:
                return job
        raise TelemetryError("job_not_found")
    completed = _completed_jobs(jobs)
    if len(completed) == 1:
        return completed[0]
    return None


def gh_api_get(path, *, runner=None):
    """GET a GitHub API path through native gh. Injectable for tests."""
    runner = subprocess.run if runner is None else runner
    try:
        result = runner(
            ["gh", "api", path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=60,
        )
    except subprocess.TimeoutExpired as error:
        raise TelemetryError("github_api_timeout") from error
    except OSError as error:
        # Missing gh binary / spawn failure — stable source-unavailable code.
        raise TelemetryError("github_cli_unavailable") from error
    if result.returncode:
        raise TelemetryError("github_api_failed")
    try:
        return json.loads(result.stdout)
    except (TypeError, ValueError) as error:
        raise TelemetryError("github_api_invalid_json") from error


def _jobs_path_page(base_path, page):
    separator = "&" if "?" in base_path else "?"
    return f"{base_path}{separator}per_page={_JOBS_PAGE_SIZE}&page={page}"


def fetch_attempt_jobs(api_get, jobs_path):
    """Fetch the complete Job list for one Attempt using GitHub pagination.

    Validates page ``total_count`` stability and Job ID uniqueness across the
    snapshot. Duplicate IDs or shifting totals refuse the snapshot rather than
    silently deduplicating (PR #80 P2-2).
    """
    page = 1
    jobs = []
    total_count = None
    while True:
        payload = api_get(_jobs_path_page(jobs_path, page))
        if not isinstance(payload, dict):
            raise TelemetryError("jobs_payload_invalid")
        batch = payload.get("jobs")
        if not isinstance(batch, list):
            raise TelemetryError("jobs_payload_invalid")
        page_total = payload.get("total_count")
        if type(page_total) is int:
            if total_count is None:
                total_count = page_total
            elif page_total != total_count:
                raise TelemetryError("jobs_snapshot_inconsistent")
        jobs.extend(batch)
        if total_count is not None and len(jobs) >= total_count:
            break
        if len(batch) < _JOBS_PAGE_SIZE:
            break
        page += 1
    if total_count is not None and len(jobs) != total_count:
        raise TelemetryError("jobs_page_incomplete")
    seen_ids = set()
    for job in jobs:
        if not isinstance(job, dict):
            continue
        job_id = job.get("id")
        if job_id is None:
            continue
        key = str(job_id)
        if key in seen_ids:
            raise TelemetryError("jobs_snapshot_inconsistent")
        seen_ids.add(key)
    return jobs


def fetch_github_run_job(
    repository,
    run_id,
    *,
    job_id=None,
    attempt=None,
    api_get=None,
    event_status=None,
    event_conclusion=None,
    event_head_sha=None,
):
    """Fetch matching Run Attempt (+ Job) facts from GitHub Actions API via gh.

    Always loads `GET .../runs/{id}/attempts/{N}` together with that Attempt's
    Jobs. Never mixes the latest Run view with an older Attempt's Jobs
    (PR #77 P1-1).

    Coverage denominators use the raw paginated snapshot: completed Jobs are
    counted by ``status == "completed"`` without requiring an ID. Missing IDs
    become omissions located by source index (PR #80 P2-1).

    Optional ``event_*`` values from workflow_run are compared to the Attempt
    API payload; conflicts refuse the fetch rather than trust the event alone.
    """
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository or ""):
        raise TelemetryError("repository_invalid")
    if type(run_id) is not int:
        try:
            run_id = int(run_id)
        except (TypeError, ValueError):
            raise TelemetryError("run_id_invalid") from None
    if run_id <= 0:
        raise TelemetryError("run_id_invalid")
    if attempt is not None:
        if type(attempt) is not int:
            try:
                attempt = int(attempt)
            except (TypeError, ValueError):
                raise TelemetryError("run_attempt_invalid") from None
        if attempt <= 0:
            raise TelemetryError("run_attempt_invalid")
    api_get = gh_api_get if api_get is None else api_get

    if attempt is None:
        latest = api_get(f"repos/{repository}/actions/runs/{run_id}")
        if not isinstance(latest, dict):
            raise TelemetryError("run_payload_invalid")
        run_attempt = latest.get("run_attempt")
        if run_attempt is None:
            raise TelemetryError("missing_run_attempt")
        if type(run_attempt) is not int:
            try:
                run_attempt = int(run_attempt)
            except (TypeError, ValueError):
                raise TelemetryError("run_attempt_invalid") from None
    else:
        run_attempt = attempt

    run = api_get(f"repos/{repository}/actions/runs/{run_id}/attempts/{run_attempt}")
    if not isinstance(run, dict):
        raise TelemetryError("run_payload_invalid")
    remote = run.get("repository")
    full_name = remote.get("full_name") if isinstance(remote, dict) else None
    full_name = full_name or run.get("repository_full_name")
    if full_name and full_name.lower() != repository.lower():
        raise TelemetryError("run_repository_mismatch")
    if run.get("id") is not None and int(run["id"]) != run_id:
        raise TelemetryError("run_id_mismatch")
    observed_attempt = run.get("run_attempt")
    if observed_attempt is None:
        raise TelemetryError("missing_run_attempt")
    if type(observed_attempt) is not int:
        try:
            observed_attempt = int(observed_attempt)
        except (TypeError, ValueError):
            raise TelemetryError("run_attempt_invalid") from None
    if observed_attempt != run_attempt:
        raise TelemetryError("run_attempt_mismatch")
    api_status = run.get("status") if isinstance(run.get("status"), str) else None
    api_conclusion = (
        run.get("conclusion") if isinstance(run.get("conclusion"), str) else None
    )
    api_head = run.get("head_sha") if isinstance(run.get("head_sha"), str) else None
    if event_status and api_status and event_status != api_status:
        raise TelemetryError("event_api_status_conflict")
    if event_conclusion and api_conclusion and event_conclusion != api_conclusion:
        raise TelemetryError("event_api_conclusion_conflict")
    if event_head_sha and api_head and event_head_sha.lower() != api_head.lower():
        raise TelemetryError("event_api_head_sha_conflict")

    jobs_path = f"repos/{repository}/actions/runs/{run_id}/attempts/{run_attempt}/jobs"
    jobs = fetch_attempt_jobs(api_get, jobs_path)
    jobs_observed_total = len(jobs)
    completed_indexes = [
        index
        for index, job in enumerate(jobs)
        if isinstance(job, dict) and job.get("status") == "completed"
    ]
    completed_total = len(completed_indexes)
    noncompleted_total = jobs_observed_total - completed_total
    expected_job_stubs = []
    for index in completed_indexes:
        job = jobs[index]
        job_id_value = job.get("id")
        job_url = job.get("html_url")
        expected_job_stubs.append(
            {
                "source_index": index,
                "job_id": str(job_id_value) if job_id_value is not None else None,
                "job_url": job_url if isinstance(job_url, str) else None,
            }
        )

    def _validate_job_membership(job):
        if job.get("run_id") is not None and int(job["run_id"]) != run_id:
            raise TelemetryError("job_run_mismatch")
        if (
            job.get("run_attempt") is not None
            and int(job["run_attempt"]) != run_attempt
        ):
            raise TelemetryError("job_attempt_mismatch")

    if job_id is not None:
        job = _select_job(jobs, job_id=job_id)
        _validate_job_membership(job)
        facts = facts_from_github_api_objects(run, job)
        task_facts = [facts]
        selection = "job_id"
    else:
        # Full Attempt coverage: every completed Job enters the same
        # facts → omit-reason → Task/omission pipeline. Missing ID stays an
        # omission located by source index — never dropped from the denominator.
        task_facts = []
        for index in completed_indexes:
            job = jobs[index]
            if job.get("id") is not None:
                _validate_job_membership(job)
            task_facts.append(
                (
                    index,
                    facts_from_github_api_objects(run, job),
                )
            )
        facts = facts_from_github_api_objects(run, None)
        selection = "all_completed" if completed_total else "none"
    return facts, {
        "jobs_observed_total": jobs_observed_total,
        "completed_jobs_total": completed_total,
        "noncompleted_jobs_total": noncompleted_total,
        "job_selection": selection,
        "task_facts": task_facts,
        "expected_job_stubs": expected_job_stubs,
        "api_run_status": api_status,
        "api_run_conclusion": api_conclusion,
    }


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
    parser.add_argument(
        "--otlp-logs-json",
        type=Path,
        help="Write OTLP/JSON ExportLogsServiceRequest from Job-terminal LogRecords",
    )
    parser.add_argument(
        "--otlp-metrics-json",
        type=Path,
        help="Write OTLP/JSON ExportMetricsServiceRequest when Metrics are observable",
    )
    parser.add_argument(
        "--otlp-endpoint",
        help=(
            "OTLP/gRPC host:port for prepared Traces, Logs, and Metrics "
            "(ACK is not Weaver PASS)"
        ),
    )
    parser.add_argument(
        "--otlp-insecure",
        action="store_true",
        help="Use insecure gRPC channel (controlled local receivers only)",
    )
    parser.add_argument(
        "--end-time-mode",
        choices=[END_TIME_EXACT, END_TIME_OBSERVED_UPPER_BOUND],
        help=(
            "exact uses an explicit completed-at. observed_upper_bound records "
            "a constraint interval and emits an approximate Root only when "
            "--max-end-uncertainty-ns covers that interval"
        ),
    )
    parser.add_argument(
        "--max-end-uncertainty-ns",
        type=int,
        help=(
            "Acceptance budget for an approximate observed Root. "
            "Omitted means the Root is not emitted"
        ),
    )
    parser.add_argument(
        "--terminal-observed-at",
        help="RFC3339 / unix-ns observation time for post-run Root upper bound",
    )
    parser.add_argument(
        "--terminal-status",
        help=(
            "Optional event hint only; observed Root requires Attempt API "
            "run_status=completed (not this flag alone)"
        ),
    )
    parser.add_argument(
        "--event-status",
        help="workflow_run event status for conflict check against Attempt API",
    )
    parser.add_argument(
        "--event-conclusion",
        help="workflow_run event conclusion for conflict check against Attempt API",
    )
    parser.add_argument(
        "--event-head-sha",
        help="workflow_run event head_sha for conflict check against Attempt API",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    source = "env"
    github_api_fetched = False
    completed_jobs_total = None
    job_selection = None
    task_facts = None
    jobs_observed_total = None
    noncompleted_jobs_total = None
    expected_job_stubs = None
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
            facts, coverage = fetch_github_run_job(
                repository,
                args.run_id,
                job_id=args.job_id,
                attempt=args.attempt,
                event_status=args.event_status,
                event_conclusion=args.event_conclusion,
                event_head_sha=args.event_head_sha,
            )
        except TelemetryError as error:
            parser.exit(2, f"cicd-telemetry rejected: {error.code}\n")
        source = "github_api"
        github_api_fetched = True
        completed_jobs_total = coverage["completed_jobs_total"]
        job_selection = coverage["job_selection"]
        task_facts = coverage.get("task_facts")
        jobs_observed_total = coverage.get("jobs_observed_total")
        noncompleted_jobs_total = coverage.get("noncompleted_jobs_total")
        expected_job_stubs = coverage.get("expected_job_stubs")
    else:
        facts = facts_from_actions_env()
        source = "env"
    # Prefer observation time after API terminal verification when auto-now.
    if args.terminal_observed_at:
        terminal_observed_at_ns = parse_time_ns(args.terminal_observed_at)
        if terminal_observed_at_ns is None:
            parser.exit(2, "cicd-telemetry rejected: terminal_observed_at_invalid\n")
    elif args.end_time_mode == END_TIME_OBSERVED_UPPER_BOUND:
        terminal_observed_at_ns = int(datetime.now(tz=UTC).timestamp() * 1_000_000_000)
    else:
        terminal_observed_at_ns = None
    if args.max_end_uncertainty_ns is not None and args.max_end_uncertainty_ns < 0:
        parser.exit(2, "cicd-telemetry rejected: max_end_uncertainty_invalid\n")
    report = produce(
        facts,
        source=source,
        github_api_fetched=github_api_fetched,
        completed_jobs_total=completed_jobs_total,
        job_selection=job_selection,
        task_facts=task_facts,
        jobs_observed_total=jobs_observed_total,
        noncompleted_jobs_total=noncompleted_jobs_total,
        terminal_status=args.terminal_status,
        terminal_observed_at_ns=terminal_observed_at_ns,
        end_time_mode=args.end_time_mode,
        event_status=args.event_status,
        event_conclusion=args.event_conclusion,
        expected_job_stubs=expected_job_stubs,
        max_end_uncertainty_ns=args.max_end_uncertainty_ns,
    )
    attach_signal_candidates(report)
    attach_otlp_json(report)
    export_error = None
    if args.otlp_json is not None:
        try:
            write_otlp_json(report, args.otlp_json)
        except TelemetryError as error:
            export_error = error
            sys.stderr.write(f"cicd-telemetry export failed: {error.code}\n")
    if args.otlp_logs_json is not None and export_error is None:
        if report.get("otlp_logs_json"):
            try:
                write_otlp_logs_json(report, args.otlp_logs_json)
            except TelemetryError as error:
                export_error = error
                sys.stderr.write(f"cicd-telemetry logs export failed: {error.code}\n")
        else:
            report["claims"]["otlp_logs_skip"] = "no_log_records"
    if args.otlp_metrics_json is not None and export_error is None:
        if report.get("otlp_metrics_json"):
            try:
                write_otlp_metrics_json(report, args.otlp_metrics_json)
            except TelemetryError as error:
                export_error = error
                sys.stderr.write(
                    f"cicd-telemetry metrics export failed: {error.code}\n"
                )
        else:
            report["claims"]["otlp_metrics_skip"] = "not_observable"
    if args.otlp_endpoint is not None and export_error is None:
        try:
            export_otlp_grpc(
                report,
                args.otlp_endpoint,
                insecure=args.otlp_insecure,
            )
        except TelemetryError as error:
            export_error = error
            sys.stderr.write(f"cicd-telemetry grpc failed: {error.code}\n")
    # Persist candidate after export attempt so claims match actual flush outcome.
    try:
        write_artifact(report, args.output)
    except OSError:
        parser.exit(2, "cicd-telemetry rejected: artifact_flush_failed\n")
    if export_error is not None:
        return 3
    if args.json:
        skip = {"otlp_json", "otlp_logs_json", "otlp_metrics_json"}
        printable = {key: value for key, value in report.items() if key not in skip}
        printable["otlp_json_present"] = report.get("otlp_json") is not None
        printable["otlp_logs_present"] = report.get("otlp_logs_json") is not None
        printable["otlp_metrics_present"] = report.get("otlp_metrics_json") is not None
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
        if args.otlp_endpoint is not None:
            print(f"otlp_endpoint: {args.otlp_endpoint}")
            print(f"rpc_accepted: {report['claims'].get('rpc_accepted')}")
    if report["status"] in {"produced", "partial"}:
        return 0
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
