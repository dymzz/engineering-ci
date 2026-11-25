"""Official OpenTelemetry Collector Contrib ``githubreceiver`` integration (#75 / #52).

Single authority for the pinned Collector Contrib container reference,
upstream ``trace_event_handling.go`` deterministic ID formulas (with
``receiver.githubreceiver.UseCheckRunID`` enabled), GitHub HMAC-SHA256 webhook
signing, Weaver v2 expectation generation, and independent Attempt Witness
reconciliation against ``githubreceiver`` OTLP captures.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

COLLECTOR_CONTRIB_VERSION = "0.165.0"
COLLECTOR_CONTRIB_IMAGE = (
    "ghcr.io/open-telemetry/opentelemetry-collector-releases/"
    "opentelemetry-collector-contrib"
)
COLLECTOR_CONTRIB_DIGEST = (
    "sha256:e40d61f10c9a617cca1ffd1be4d7e2b3a4b0f0001798ff07e58cabe82e618cea"
)
COLLECTOR_CONTRIB_REF = f"{COLLECTOR_CONTRIB_IMAGE}@{COLLECTOR_CONTRIB_DIGEST}"

GITHUBRECEIVER_DECLARED_SEMCONV = "1.43.0"
PINNED_TARGET_SEMCONV = "1.45.0"
USE_CHECK_RUN_ID_GATE = "receiver.githubreceiver.UseCheckRunID"
ID_SCHEME = "otel-githubreceiver-usecheckrunid-v1"

COLLECTOR_CONFIG_RELPATH = "config/otel-collector-github-receiver.yaml"
WEAVER_MATCHER_RELPATH = "config/github-receiver.weaver.toml"
PIPELINE_MATCHER_ID = "gh.pipeline"
TASK_MATCHER_ID = "gh.task"
PIPELINE_SIGNAL = "cicd.pipeline.run.server"
TASK_SIGNAL = "cicd.pipeline.task.internal"

SPAN_KIND_INTERNAL = 1
SPAN_KIND_SERVER = 2

_CONCLUSION_MAP: dict[str, tuple[str, str | None]] = {
    "success": ("success", None),
    "failure": ("failure", "failure"),
    "startup_failure": ("error", "startup_failure"),
    "cancelled": ("cancellation", None),
    "canceled": ("cancellation", None),
    "skipped": ("skip", None),
    "timed_out": ("timeout", "timeout"),
}

_GITHUB_JOB_STATUSES = frozenset(
    {"queued", "in_progress", "completed", "waiting", "requested", "pending"}
)
_HEX_TRACE_RE = re.compile(r"^[0-9a-f]{32}$")
_HEX_SPAN_RE = re.compile(r"^[0-9a-f]{16}$")
_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=UTC)
_EXECUTED_WITNESS_SOURCES = frozenset(
    {
        "github_webhook_events",
        "github_workflow_job_events",
        "github_actions_witness",
        "github_api",
    }
)
_WITNESS_COLLECTION_STATUSES = frozenset({"complete", "incomplete", "partial"})


def _parse_timestamp_ns(value: Any, code: str) -> int | None:
    """Parse an ISO-8601 tz-aware timestamp or positive integer ns into UTC ns."""
    if value is None:
        return None
    if type(value) is bool:
        raise GitHubReceiverError(code)
    if type(value) is int:
        if value <= 0:
            raise GitHubReceiverError(code)
        return value
    if not isinstance(value, str):
        raise GitHubReceiverError(code)
    text = value.strip()
    if not text:
        raise GitHubReceiverError(code)
    normalized = f"{text[:-1]}+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as err:
        raise GitHubReceiverError(code) from err
    if parsed.tzinfo is None:
        raise GitHubReceiverError(code)
    delta = parsed.astimezone(UTC) - _EPOCH_UTC
    ns = (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + (
        delta.microseconds * 1_000
    )
    if ns <= 0:
        raise GitHubReceiverError(code)
    return ns


def _normalize_executed_job_ids(executed_job_ids: Any) -> set[int] | None:
    if executed_job_ids is None:
        return None
    if isinstance(executed_job_ids, (dict, str, bytes)) or not isinstance(
        executed_job_ids, (list, tuple, set, frozenset)
    ):
        raise GitHubReceiverError("executed_job_ids_invalid")
    return {_positive_int(item, "executed_job_id_invalid") for item in executed_job_ids}


def _parse_executed_job_witness(
    witness: Any, *, rid: int, att: int
) -> tuple[set[int], bool]:
    """Validate structured execution-event witness identity, source, and status.

    Because engineering-ci currently has no authenticated native producer
    closure proving an execution-event collection captured every executed job,
    a caller-supplied ``complete=True`` flag is checked for status consistency
    and contradiction detection only, never treated as certified completeness.
    """
    if not isinstance(witness, dict):
        raise GitHubReceiverError("executed_job_witness_invalid")
    source = witness.get("source")
    if not isinstance(source, str) or source not in _EXECUTED_WITNESS_SOURCES:
        raise GitHubReceiverError("executed_job_witness_source_invalid")
    if (
        _positive_int(witness.get("run_id"), "executed_job_witness_run_id_invalid")
        != rid
    ):
        raise GitHubReceiverError("executed_job_witness_run_mismatch")
    if (
        _positive_int(witness.get("attempt"), "executed_job_witness_attempt_invalid")
        != att
    ):
        raise GitHubReceiverError("executed_job_witness_attempt_mismatch")

    collection_status = witness.get("collection_status")
    if collection_status is not None and (
        not isinstance(collection_status, str)
        or collection_status not in _WITNESS_COLLECTION_STATUSES
    ):
        raise GitHubReceiverError("executed_job_witness_status_invalid")

    if "complete" in witness:
        complete_flag = witness["complete"]
        if type(complete_flag) is not bool:
            raise GitHubReceiverError("executed_job_witness_invalid")
        if collection_status is not None and (
            (complete_flag and collection_status != "complete")
            or (not complete_flag and collection_status == "complete")
        ):
            raise GitHubReceiverError("executed_job_witness_status_invalid")
        claimed_complete = complete_flag
    else:
        claimed_complete = collection_status == "complete"

    run_status = witness.get("run_status")
    if claimed_complete and run_status != "completed":
        raise GitHubReceiverError("executed_job_witness_status_invalid")

    ids = _normalize_executed_job_ids(witness.get("executed_job_ids"))
    if ids is None:
        raise GitHubReceiverError("executed_job_ids_invalid")
    return ids, claimed_complete


def _attribution_source(
    time_signal: str | None,
    event_signal: str | None,
    *,
    att: int,
    event_label: str,
) -> str:
    if time_signal is not None and event_signal is not None:
        suffix = "witness" if event_label == "executed_job_witness" else "event"
        return f"run_started_at_and_{suffix}"
    if time_signal is not None:
        return "run_started_at"
    if event_signal is not None:
        return event_label
    if att == 1:
        return "attempt_1_witness"
    return "unproven_execution_vs_reuse"


class GitHubReceiverError(ValueError):
    """Deterministic integration or reconciliation contract failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _positive_int(value: Any, code: str) -> int:
    if type(value) is bool:
        raise GitHubReceiverError(code)
    if type(value) is int:
        number = value
    elif isinstance(value, str):
        text = value.strip()
        if not text.isdigit() or text.startswith("0"):
            raise GitHubReceiverError(code)
        number = int(text)
    else:
        raise GitHubReceiverError(code)
    if number < 1:
        raise GitHubReceiverError(code)
    return number


def map_github_conclusion(conclusion: Any) -> tuple[str | None, str | None]:
    """Return ``(semconv_result, error_type)`` or ``(None, None)`` if unmapped."""
    if not isinstance(conclusion, str) or not conclusion.strip():
        return None, None
    return _CONCLUSION_MAP.get(conclusion.strip().lower(), (None, None))


def trace_id(run_id: int | str, attempt: int | str) -> str:
    """Upstream ``githubreceiver/trace_event_handling.go:newTraceID``."""
    rid = _positive_int(run_id, "invalid_run_id")
    att = _positive_int(attempt, "invalid_attempt")
    return hashlib.sha256(f"{rid}{att}t".encode()).hexdigest()[:32]


def root_span_id(run_id: int | str, attempt: int | str) -> str:
    """Upstream ``githubreceiver/trace_event_handling.go:newParentSpanID``."""
    rid = _positive_int(run_id, "invalid_run_id")
    att = _positive_int(attempt, "invalid_attempt")
    return hashlib.sha256(f"{rid}{att}s".encode()).hexdigest()[16:32]


def _span_id_from_check_run(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[16:32]


def job_span_id(check_run_id: int | str) -> str:
    """Upstream ``UseCheckRunID`` Job span ID (`{check_run_id}-j`)."""
    cid = _positive_int(check_run_id, "invalid_check_run_id")
    return _span_id_from_check_run(f"{cid}-j")


def queue_span_id(check_run_id: int | str) -> str:
    """Upstream ``UseCheckRunID`` Queue span ID (`{check_run_id}-q`)."""
    cid = _positive_int(check_run_id, "invalid_check_run_id")
    return _span_id_from_check_run(f"{cid}-q")


def step_span_id(check_run_id: int | str, step_name: str) -> str:
    """Upstream ``UseCheckRunID`` Step span ID (`{check_run_id}-{step_name}-s`)."""
    cid = _positive_int(check_run_id, "invalid_check_run_id")
    if not isinstance(step_name, str) or not step_name:
        raise GitHubReceiverError("invalid_step_name")
    return _span_id_from_check_run(f"{cid}-{step_name}-s")


def previous_attempt_trace_id(run_id: int | str, attempt: int | str) -> str | None:
    """Expected trace link target for reruns (`attempt > 1`)."""
    rid = _positive_int(run_id, "invalid_run_id")
    att = _positive_int(attempt, "invalid_attempt")
    if att <= 1:
        return None
    return trace_id(rid, att - 1)


def sign_webhook_payload(payload_bytes: bytes, secret: str) -> str:
    """Compute GitHub ``X-Hub-Signature-256`` HMAC header value."""
    if not isinstance(payload_bytes, (bytes, bytearray)):
        raise GitHubReceiverError("invalid_payload_bytes")
    if not isinstance(secret, str) or not secret:
        raise GitHubReceiverError("webhook_secret_required")
    digest = hmac.new(
        secret.encode("utf-8"), bytes(payload_bytes), hashlib.sha256
    ).hexdigest()
    return f"sha256={digest}"


def verify_webhook_signature(
    payload_bytes: bytes, secret: str, signature_header: str | None
) -> bool:
    """Constant-time validation matching GitHub webhook HMAC-SHA256."""
    if not isinstance(signature_header, str) or not signature_header.startswith(
        "sha256="
    ):
        return False
    expected = sign_webhook_payload(payload_bytes, secret)
    return hmac.compare_digest(signature_header, expected)


def webhook_headers(
    event: str,
    payload_bytes: bytes,
    *,
    secret: str,
    delivery_id: str,
    hook_id: str = "1",
) -> dict[str, str]:
    """Build standard GitHub webhook headers accepted by ``githubreceiver``."""
    if not isinstance(event, str) or not event.strip():
        raise GitHubReceiverError("invalid_event_name")
    if not isinstance(delivery_id, str) or not delivery_id.strip():
        raise GitHubReceiverError("invalid_delivery_id")
    return {
        "Content-Type": "application/json",
        "User-Agent": "GitHub-Hookshot/engineering-ci",
        "X-GitHub-Event": event.strip(),
        "X-GitHub-Delivery": delivery_id.strip(),
        "X-GitHub-Hook-ID": str(hook_id),
        "X-Hub-Signature-256": sign_webhook_payload(payload_bytes, secret),
    }


def _otlp_id_hex(value: Any, *, size_bytes: int) -> str | None:
    """Normalize OTLP hex or base64 trace/span ID into lowercase hex."""
    if not isinstance(value, str) or not value:
        return None
    target_len = size_bytes * 2
    candidate = value.strip().lower()
    if len(candidate) == target_len and all(c in "0123456789abcdef" for c in candidate):
        return None if int(candidate, 16) == 0 else candidate
    try:
        decoded = base64.b64decode(value, validate=True)
    except ValueError:
        return None
    if len(decoded) != size_bytes or int.from_bytes(decoded, "big") == 0:
        return None
    return decoded.hex()


def _otlp_attr_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return None
    for key in ("stringValue", "boolValue"):
        if key in value:
            return value[key]
    if "intValue" in value:
        raw = value["intValue"]
        if isinstance(raw, int):
            return raw
        if isinstance(raw, str):
            try:
                return int(raw)
            except ValueError:
                return raw
    if "doubleValue" in value:
        return value["doubleValue"]
    return None


def _otlp_attributes(entries: Any) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    if not isinstance(entries, list):
        return attrs
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        if not isinstance(key, str) or not key:
            continue
        val = _otlp_attr_value(entry.get("value"))
        if val is not None:
            attrs[key] = val
    return attrs


def _span_kind_int(value: Any) -> int | None:
    if type(value) is int:
        return value
    if isinstance(value, str):
        mapping = {
            "SPAN_KIND_INTERNAL": SPAN_KIND_INTERNAL,
            "SPAN_KIND_SERVER": SPAN_KIND_SERVER,
            "internal": SPAN_KIND_INTERNAL,
            "server": SPAN_KIND_SERVER,
        }
        return mapping.get(value)
    return None


def merge_otlp_json_batches(batches: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge OTLP/JSON export payloads into one hex-ID ``resourceSpans`` bundle."""
    merged: list[dict[str, Any]] = []
    for batch in batches:
        if not isinstance(batch, dict):
            continue
        for rs in batch.get("resourceSpans") or []:
            if not isinstance(rs, dict):
                continue
            normalized_scopes = []
            for scope_span in rs.get("scopeSpans") or []:
                if not isinstance(scope_span, dict):
                    continue
                spans = []
                for span in scope_span.get("spans") or []:
                    if not isinstance(span, dict):
                        continue
                    item = dict(span)
                    tid = _otlp_id_hex(item.get("traceId"), size_bytes=16)
                    sid = _otlp_id_hex(item.get("spanId"), size_bytes=8)
                    pid = _otlp_id_hex(item.get("parentSpanId"), size_bytes=8)
                    if tid:
                        item["traceId"] = tid
                    if sid:
                        item["spanId"] = sid
                    if pid:
                        item["parentSpanId"] = pid
                    elif "parentSpanId" in item:
                        item.pop("parentSpanId", None)
                    links = []
                    for link in item.get("links") or []:
                        if not isinstance(link, dict):
                            continue
                        link_item = dict(link)
                        ltid = _otlp_id_hex(link_item.get("traceId"), size_bytes=16)
                        lsid = _otlp_id_hex(link_item.get("spanId"), size_bytes=8)
                        if ltid:
                            link_item["traceId"] = ltid
                        if lsid:
                            link_item["spanId"] = lsid
                        links.append(link_item)
                    if links or "links" in item:
                        item["links"] = links
                    spans.append(item)
                normalized_scopes.append({**scope_span, "spans": spans})
            merged.append({**rs, "scopeSpans": normalized_scopes})
    return {"resourceSpans": merged}


def extract_capture_spans(capture: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten an OTLP trace capture into normalized span records with resource attrs."""
    if not isinstance(capture, dict) or not isinstance(
        capture.get("resourceSpans"), list
    ):
        raise GitHubReceiverError("capture_invalid")
    records: list[dict[str, Any]] = []
    for rs in capture["resourceSpans"]:
        if not isinstance(rs, dict):
            continue
        resource_attrs = _otlp_attributes(
            ((rs.get("resource") or {}).get("attributes")) or []
        )
        for scope_span in rs.get("scopeSpans") or []:
            if not isinstance(scope_span, dict):
                continue
            for span in scope_span.get("spans") or []:
                if not isinstance(span, dict):
                    continue
                links = []
                for link in span.get("links") or []:
                    if not isinstance(link, dict):
                        continue
                    ltid = _otlp_id_hex(link.get("traceId"), size_bytes=16)
                    if ltid:
                        links.append(ltid)
                records.append(
                    {
                        "trace_id": _otlp_id_hex(span.get("traceId"), size_bytes=16),
                        "span_id": _otlp_id_hex(span.get("spanId"), size_bytes=8),
                        "parent_span_id": _otlp_id_hex(
                            span.get("parentSpanId"), size_bytes=8
                        ),
                        "name": span.get("name"),
                        "kind": _span_kind_int(span.get("kind")),
                        "start_time_unix_nano": span.get("startTimeUnixNano"),
                        "end_time_unix_nano": span.get("endTimeUnixNano"),
                        "resource_attrs": resource_attrs,
                        "span_attrs": _otlp_attributes(span.get("attributes") or []),
                        "link_trace_ids": links,
                        "status": span.get("status") or {},
                    }
                )
    return records


def expected_matchers_for_jobs(executed_jobs: list[dict[str, Any]]) -> list[str]:
    """Return expected Weaver matchers strictly from Attempt-executed Job witness."""
    if executed_jobs:
        return [PIPELINE_MATCHER_ID, TASK_MATCHER_ID]
    return [PIPELINE_MATCHER_ID]


def partition_witness_jobs(
    jobs: list[dict[str, Any]],
    *,
    rid: int,
    att: int,
    run_started_at: Any = None,
    executed_job_ids: Any = None,
    executed_job_witness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Separate full Attempt Result Inventory from Attempt Executed Job Inventory.

    GitHub rerun API snapshots can carry ``run_attempt == N`` on historical jobs
    reused from earlier Attempts (with ``started_at < run_started_at``). Because
    ``engineering-ci`` currently has no authenticated native producer closure
    proving an execution-event collection captured every executed job, both bare
    ``executed_job_ids`` and structured ``executed_job_witness`` (even when
    caller-supplied ``complete=True``) provide positive execution evidence only
    for listed IDs. Only independent start-time bounds may classify unlisted
    ``attempt > 1`` jobs as ``reused``; ambiguous jobs stay in
    ``unknown_execution_jobs`` rather than shrinking Result Inventory.
    """
    completed, noncompleted = _validate_witness_jobs(jobs, rid=rid, att=att)
    run_started_ns = _parse_timestamp_ns(run_started_at, "invalid_run_started_at")
    bare_executed_set = _normalize_executed_job_ids(executed_job_ids)
    completed_ids = {
        _positive_int(job["id"], "missing_check_run_id") for job in completed
    }
    if bare_executed_set is not None and (bare_executed_set - completed_ids):
        raise GitHubReceiverError("wrong_reuse_attribution")

    witness_ids: set[int] | None = None
    claimed_complete = False
    # No authenticated native event-set closure producer exists in this repository yet.
    witness_complete = False
    observed_executed_set = bare_executed_set
    if executed_job_witness is not None:
        witness_ids, claimed_complete = _parse_executed_job_witness(
            executed_job_witness, rid=rid, att=att
        )
        if witness_ids - completed_ids:
            raise GitHubReceiverError("wrong_reuse_attribution")
        if (
            claimed_complete
            and bare_executed_set is not None
            and bare_executed_set != witness_ids
        ):
            raise GitHubReceiverError("wrong_reuse_attribution")
        observed_executed_set = (
            witness_ids
            if bare_executed_set is None
            else (witness_ids | bare_executed_set)
        )

    result_inventory: list[dict[str, Any]] = []
    executed_jobs: list[dict[str, Any]] = []
    reused_jobs: list[dict[str, Any]] = []
    unknown_execution_jobs: list[dict[str, Any]] = []

    for job in completed:
        cid = _positive_int(job["id"], "missing_check_run_id")
        job_started_ns = _parse_timestamp_ns(
            job.get("started_at"), "job_timestamp_invalid"
        )
        job_completed_ns = _parse_timestamp_ns(
            job.get("completed_at"), "job_timestamp_invalid"
        )
        if (
            job_started_ns is not None
            and job_completed_ns is not None
            and job_completed_ns < job_started_ns
        ):
            raise GitHubReceiverError("invalid_job_timestamps")

        time_signal: str | None = None
        if run_started_ns is not None and job_started_ns is not None:
            time_signal = "executed" if job_started_ns >= run_started_ns else "reused"

        if (
            claimed_complete
            and witness_ids is not None
            and cid not in witness_ids
            and (att == 1 or time_signal == "executed")
        ):
            raise GitHubReceiverError("wrong_reuse_attribution")

        event_signal: str | None = None
        if observed_executed_set is not None and cid in observed_executed_set:
            event_signal = "executed"

        row_event_label = (
            "executed_job_witness"
            if (witness_ids is not None and cid in witness_ids)
            else "executed_job_ids"
        )
        attr_source = _attribution_source(
            time_signal,
            event_signal,
            att=att,
            event_label=row_event_label,
        )

        if att == 1:
            if time_signal == "reused":
                raise GitHubReceiverError("wrong_reuse_attribution")
            attribution = "executed_in_attempt"
            executed_jobs.append(job)
        else:
            if (
                time_signal is not None
                and event_signal is not None
                and time_signal != event_signal
            ):
                raise GitHubReceiverError("wrong_reuse_attribution")
            effective = time_signal if time_signal is not None else event_signal
            if effective == "executed":
                attribution = "executed_in_attempt"
                executed_jobs.append(job)
            elif effective == "reused":
                attribution = "reused_from_prior_attempt"
                reused_jobs.append(job)
            else:
                attribution = "unknown"
                unknown_execution_jobs.append(job)

        result_inventory.append(
            {
                "job_id": str(cid),
                "name": job.get("name"),
                "status": job.get("status"),
                "conclusion": job.get("conclusion"),
                "started_at": job.get("started_at"),
                "completed_at": job.get("completed_at"),
                "execution_attribution": attribution,
                "execution_attribution_source": attr_source,
            }
        )

    return {
        "completed": completed,
        "noncompleted": noncompleted,
        "result_inventory": result_inventory,
        "result_completed_job_ids": [str(job["id"]) for job in completed],
        "executed_jobs": executed_jobs,
        "executed_job_ids": [str(job["id"]) for job in executed_jobs],
        "reused_jobs": reused_jobs,
        "reused_job_ids": [str(job["id"]) for job in reused_jobs],
        "unknown_execution_jobs": unknown_execution_jobs,
        "unknown_execution_job_ids": [str(job["id"]) for job in unknown_execution_jobs],
        "execution_attribution_status": (
            "unknown" if unknown_execution_jobs else "verified"
        ),
        "execution_witness_complete": witness_complete,
        "execution_witness_claimed_complete": claimed_complete,
    }


def _validate_witness_jobs(
    jobs: list[dict[str, Any]], *, rid: int, att: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(jobs, list):
        raise GitHubReceiverError("witness_jobs_invalid")
    completed: list[dict[str, Any]] = []
    noncompleted: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for job in jobs:
        if not isinstance(job, dict):
            raise GitHubReceiverError("witness_job_entry_invalid")
        job_run_id = job.get("run_id")
        if (
            job_run_id is not None
            and _positive_int(job_run_id, "job_run_id_invalid") != rid
        ):
            raise GitHubReceiverError("job_run_id_mismatch")
        job_attempt = job.get("run_attempt")
        if (
            job_attempt is not None
            and _positive_int(job_attempt, "job_attempt_invalid") != att
        ):
            raise GitHubReceiverError("job_attempt_mismatch")
        status = job.get("status")
        if status not in _GITHUB_JOB_STATUSES:
            raise GitHubReceiverError("witness_job_status_invalid")
        if status != "completed":
            noncompleted.append(job)
            continue
        cid = _positive_int(job.get("id"), "missing_check_run_id")
        if cid in seen_ids:
            raise GitHubReceiverError("duplicate_witness_job_id")
        seen_ids.add(cid)
        completed.append(job)
    return completed, noncompleted


def build_weaver_expectations(
    *,
    run_id: int | str,
    attempt: int | str,
    run_status: str,
    run_conclusion: str | None,
    jobs: list[dict[str, Any]],
    run_started_at: Any = None,
    executed_job_ids: Any = None,
    executed_job_witness: dict[str, Any] | None = None,
    allow_unproven_execution_attribution: bool = False,
) -> dict[str, Any]:
    """Build ``ci-weaver.toml`` expectations JSON for Root-dependent ``#52B`` checks.

    Applies to full ``githubreceiver`` Pipeline+Task captures (emitting one
    Root expectation plus Task expectations for ``executed_jobs``); must not be
    used to gate valid Rootless / Task-only ``#52A`` native Live runs. Never
    copies Trace or Span IDs from a capture and never treats reused
    prior-Attempt jobs or unproven ``attempt > 1`` jobs as verified
    current-Attempt Task spans. Rejects unmapped conclusions,
    incomplete/mismatched jobs, missing ``check_run_id``, wrong reuse
    attribution, and unproven rerun attribution unless explicitly allowed for
    inspection.
    """
    rid = _positive_int(run_id, "invalid_run_id")
    att = _positive_int(attempt, "invalid_attempt")
    if run_status != "completed":
        raise GitHubReceiverError("run_not_completed")
    mapped_run, _ = map_github_conclusion(run_conclusion)
    if mapped_run is None:
        raise GitHubReceiverError("unsupported_run_conclusion")

    partition = partition_witness_jobs(
        jobs,
        rid=rid,
        att=att,
        run_started_at=run_started_at,
        executed_job_ids=executed_job_ids,
        executed_job_witness=executed_job_witness,
    )
    if partition["noncompleted"]:
        raise GitHubReceiverError("attempt_has_noncompleted_jobs")
    if partition["unknown_execution_jobs"] and not allow_unproven_execution_attribution:
        raise GitHubReceiverError("execution_attribution_unknown")

    exp_trace = trace_id(rid, att)
    exp_root = root_span_id(rid, att)
    spans = [
        {
            "trace_id": exp_trace,
            "span_id": exp_root,
            "matcher": PIPELINE_MATCHER_ID,
            "signal": PIPELINE_SIGNAL,
        }
    ]
    for job in partition["executed_jobs"]:
        cid = _positive_int(job["id"], "missing_check_run_id")
        spans.append(
            {
                "trace_id": exp_trace,
                "span_id": job_span_id(cid),
                "matcher": TASK_MATCHER_ID,
                "signal": TASK_SIGNAL,
            }
        )
    composite_run_id = f"{rid}/{att}"
    gaps: list[str] = []
    if partition["unknown_execution_jobs"]:
        gaps.append("execution_attribution_unknown")
    if partition["execution_witness_claimed_complete"]:
        gaps.append("execution_witness_completeness_unverified")
    return {
        "schema_version": 1,
        "id_scheme": ID_SCHEME,
        "source": {"run_id": composite_run_id, "attempt": att},
        "result_inventory": partition["result_inventory"],
        "result_completed_jobs": partition["result_completed_job_ids"],
        "executed_jobs": partition["executed_job_ids"],
        "reused_jobs": partition["reused_job_ids"],
        "unknown_execution_jobs": partition["unknown_execution_job_ids"],
        "execution_attribution_status": partition["execution_attribution_status"],
        "execution_witness_complete": partition["execution_witness_complete"],
        "provenance_gaps": gaps,
        "expected_matchers": expected_matchers_for_jobs(partition["executed_jobs"]),
        "spans": spans,
        "terminal_evidence": {
            "run_id": composite_run_id,
            "attempt": att,
            "status": "completed",
            "result": mapped_run,
            "execution_attribution_status": partition["execution_attribution_status"],
        },
    }


def _valid_span_times(span: dict[str, Any]) -> bool:
    try:
        start = int(span.get("start_time_unix_nano"))
        end = int(span.get("end_time_unix_nano"))
    except (TypeError, ValueError):
        return False
    return start > 0 and end >= start


def reconcile_github_receiver_trace(
    *,
    run_id: int | str,
    attempt: int | str,
    run_status: str,
    run_conclusion: str | None,
    jobs: list[dict[str, Any]],
    capture: dict[str, Any],
    run_started_at: Any = None,
    executed_job_ids: Any = None,
    executed_job_witness: dict[str, Any] | None = None,
    repository_url: str | None = None,
    head_sha: str | None = None,
    expect_previous_attempt_link: bool = False,
) -> dict[str, Any]:
    """Reconcile a Root-dependent (``#52B``) ``githubreceiver`` capture against Witness.

    Requires the official ``githubreceiver`` Pipeline Root span (returning
    ``root_span_missing`` when absent); not applicable to Rootless Task-only
    ``#52A`` native Live verification. Preserves the full API Result Inventory
    while restricting expected Task spans strictly to the Attempt's proven
    Executed Job Inventory (never treating an incomplete ``executed_job_ids``
    list as reuse proof, and never inferring expectations from the capture
    being verified). Never elevates shape checks into ``#63`` trusted
    admission or ``#61`` required satisfaction.
    """
    rid = _positive_int(run_id, "invalid_run_id")
    att = _positive_int(attempt, "invalid_attempt")
    if run_status != "completed":
        raise GitHubReceiverError("run_not_completed")
    mapped_run, expected_run_err = map_github_conclusion(run_conclusion)
    if mapped_run is None:
        raise GitHubReceiverError("unsupported_run_conclusion")

    partition = partition_witness_jobs(
        jobs,
        rid=rid,
        att=att,
        run_started_at=run_started_at,
        executed_job_ids=executed_job_ids,
        executed_job_witness=executed_job_witness,
    )
    noncompleted = partition["noncompleted"]
    executed_jobs = partition["executed_jobs"]
    reused_jobs = partition["reused_jobs"]
    unknown_jobs = partition["unknown_execution_jobs"]

    spans = extract_capture_spans(capture)
    exp_trace = trace_id(rid, att)
    exp_root = root_span_id(rid, att)
    exp_prev_trace = (
        previous_attempt_trace_id(rid, att) if expect_previous_attempt_link else None
    )
    composite_run_id = f"{rid}/{att}"

    errors: list[str] = []
    if noncompleted:
        errors.append("attempt_has_noncompleted_jobs")
    if unknown_jobs:
        errors.append("execution_attribution_unknown")

    roots = [s for s in spans if s["kind"] == SPAN_KIND_SERVER]
    if len(roots) != 1:
        errors.append("root_span_missing" if not roots else "duplicate_root_span")
        root = None
    else:
        root = roots[0]
        if root["trace_id"] != exp_trace:
            errors.append("root_trace_id_mismatch")
        if root["span_id"] != exp_root:
            errors.append("root_span_id_mismatch")
        if root["parent_span_id"] is not None:
            errors.append("root_has_parent_span")
        if not _valid_span_times(root):
            errors.append("root_timestamps_invalid")
        if root["resource_attrs"].get("cicd.pipeline.run.id") != composite_run_id:
            errors.append("root_pipeline_run_id_mismatch")
        if root["span_attrs"].get("cicd.pipeline.result") != mapped_run:
            errors.append("root_pipeline_result_mismatch")
        if (
            expected_run_err is not None
            and root["span_attrs"].get("error.type") != expected_run_err
        ):
            errors.append("root_error_type_mismatch")
        if (
            repository_url
            and root["resource_attrs"].get("vcs.repository.url.full") != repository_url
        ):
            errors.append("root_repository_url_mismatch")
        if head_sha and root["resource_attrs"].get("vcs.ref.head.revision") != head_sha:
            errors.append("root_head_sha_mismatch")
        if exp_prev_trace and exp_prev_trace not in root["link_trace_ids"]:
            errors.append("previous_attempt_trace_link_missing")

    # Select direct Task spans only (exclude Queue and Step sub-spans whose parent
    # is a Job span rather than the Root span and carry no task.run.id).
    expected_by_span_id: dict[str, dict[str, Any]] = {}
    for job in executed_jobs:
        cid = _positive_int(job["id"], "missing_check_run_id")
        expected_by_span_id[job_span_id(cid)] = job

    reused_by_span_id: dict[str, str] = {
        job_span_id(_positive_int(job["id"], "missing_check_run_id")): str(job["id"])
        for job in reused_jobs
    }
    unknown_by_span_id: dict[str, str] = {
        job_span_id(_positive_int(job["id"], "missing_check_run_id")): str(job["id"])
        for job in unknown_jobs
    }

    observed_task_spans = [
        s
        for s in spans
        if s["kind"] == SPAN_KIND_INTERNAL
        and (
            s["parent_span_id"] == exp_root
            or "cicd.pipeline.task.run.id" in s["span_attrs"]
        )
    ]

    counts: dict[str, int] = {}
    by_id: dict[str, dict[str, Any]] = {}
    for span in observed_task_spans:
        sid = span["span_id"] or "<missing>"
        counts[sid] = counts.get(sid, 0) + 1
        by_id.setdefault(sid, span)

    missing_jobs: list[str] = []
    unexpected_spans: list[str] = []
    reused_in_capture: list[str] = []
    unattributed_in_capture: list[str] = []
    duplicate_spans = sorted(sid for sid, cnt in counts.items() if cnt > 1)
    mismatched_tasks: list[dict[str, str]] = []

    for exp_sid, job in expected_by_span_id.items():
        if counts.get(exp_sid, 0) == 0:
            missing_jobs.append(str(job["id"]))
            continue
        obs = by_id[exp_sid]
        reasons = _check_task_span(
            obs=obs,
            job=job,
            exp_trace=exp_trace,
            exp_root=exp_root,
            composite_run_id=composite_run_id,
            repository_url=repository_url,
            head_sha=head_sha,
        )
        if reasons:
            mismatched_tasks.append({"job_id": str(job["id"]), "reasons": reasons})

    for obs_sid in counts:
        if obs_sid in expected_by_span_id:
            continue
        if obs_sid in reused_by_span_id:
            reused_in_capture.append(reused_by_span_id[obs_sid])
            unexpected_spans.append(obs_sid)
        elif obs_sid in unknown_by_span_id:
            unattributed_in_capture.append(unknown_by_span_id[obs_sid])
        else:
            unexpected_spans.append(obs_sid)

    if missing_jobs:
        errors.append("missing_expected_task_spans")
    if reused_in_capture:
        errors.append("wrong_reuse_attribution")
    if unexpected_spans:
        errors.append("unexpected_task_spans")
    if duplicate_spans:
        errors.append("duplicate_task_spans")
    if mismatched_tasks:
        errors.append("task_span_attribute_mismatch")

    if not errors:
        task_coverage = "zero_task_confirmed" if not executed_jobs else "complete"
    elif errors == ["execution_attribution_unknown"]:
        task_coverage = "execution_attribution_unknown"
    else:
        task_coverage = "incomplete"

    return {
        "id_scheme": ID_SCHEME,
        "ok": not errors,
        "errors": errors,
        "expected_trace_id": exp_trace,
        "expected_root_span_id": exp_root,
        "expected_previous_trace_id": exp_prev_trace,
        "expected_pipeline_run_id": composite_run_id,
        "result_inventory": partition["result_inventory"],
        "result_completed_jobs": partition["result_completed_job_ids"],
        "executed_jobs": partition["executed_job_ids"],
        "reused_jobs": partition["reused_job_ids"],
        "unknown_execution_jobs": partition["unknown_execution_job_ids"],
        "execution_attribution_status": partition["execution_attribution_status"],
        "execution_witness_complete": partition["execution_witness_complete"],
        "expected_completed_jobs": partition["result_completed_job_ids"],
        "expected_executed_jobs": partition["executed_job_ids"],
        "expected_matchers": expected_matchers_for_jobs(executed_jobs),
        "task_coverage": task_coverage,
        "missing_jobs": missing_jobs,
        "unexpected_spans": unexpected_spans,
        "reused_job_spans_in_capture": reused_in_capture,
        "unattributed_capture_spans": unattributed_in_capture,
        "duplicate_spans": duplicate_spans,
        "mismatched_tasks": mismatched_tasks,
        "trusted": False,
        "required_satisfied": False,
    }


def _check_task_span(
    *,
    obs: dict[str, Any],
    job: dict[str, Any],
    exp_trace: str,
    exp_root: str,
    composite_run_id: str,
    repository_url: str | None,
    head_sha: str | None,
) -> list[str]:
    reasons: list[str] = []
    if obs["trace_id"] != exp_trace:
        reasons.append("trace_id_mismatch")
    if obs["parent_span_id"] != exp_root:
        reasons.append("parent_span_id_mismatch")
    if not _valid_span_times(obs):
        reasons.append("timestamps_invalid")
    if obs["resource_attrs"].get("cicd.pipeline.run.id") != composite_run_id:
        reasons.append("resource_run_id_mismatch")
    s_attrs = obs["span_attrs"]
    if str(s_attrs.get("cicd.pipeline.task.run.id")) != str(job["id"]):
        reasons.append("task_run_id_mismatch")
    if job.get("name") and s_attrs.get("cicd.pipeline.task.name") != job["name"]:
        reasons.append("task_name_mismatch")
    if (
        job.get("html_url")
        and s_attrs.get("cicd.pipeline.task.run.url.full") != job["html_url"]
    ):
        reasons.append("task_url_mismatch")
    mapped_job, exp_err = map_github_conclusion(job.get("conclusion"))
    if mapped_job is None or s_attrs.get("cicd.pipeline.task.run.result") != mapped_job:
        reasons.append("task_result_mismatch")
    if exp_err is not None and s_attrs.get("error.type") != exp_err:
        reasons.append("task_error_type_mismatch")
    if (
        repository_url
        and obs["resource_attrs"].get("vcs.repository.url.full") != repository_url
    ):
        reasons.append("repository_url_mismatch")
    if head_sha and obs["resource_attrs"].get("vcs.ref.head.revision") != head_sha:
        reasons.append("head_sha_mismatch")
    return reasons


def collector_config_summary(path: Path | str) -> dict[str, Any]:
    """Read key structural properties of the pinned Collector YAML config."""
    text = Path(path).read_text(encoding="utf-8")
    return {
        "has_github_receiver": "receivers:" in text and "github:" in text,
        "uses_env_webhook_secret": "${env:GITHUB_WEBHOOK_SECRET}" in text,
        "includes_span_events": "include_span_events: true" in text,
        "normalizes_semconv_v1_45_0": "transform/semconv_v1_45_0" in text,
        "drops_raw_webhook_events": "filter/drop_raw_webhook_events" in text,
        "exports_otlp_grpc": "otlp_grpc:" in text,
    }


def is_valid_trace_id(value: str | None) -> bool:
    return bool(
        isinstance(value, str)
        and _HEX_TRACE_RE.fullmatch(value)
        and int(value, 16) != 0
    )


def is_valid_span_id(value: str | None) -> bool:
    return bool(
        isinstance(value, str) and _HEX_SPAN_RE.fullmatch(value) and int(value, 16) != 0
    )
