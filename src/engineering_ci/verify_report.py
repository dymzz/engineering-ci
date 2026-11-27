"""Bounded Lefthook summary parsing and read-only verification timing correlation."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from contextlib import suppress
from pathlib import Path
from typing import Any

from engineering_ci import (
    cicd_telemetry,
    execution,
    hook_checks,
    hooks,
    plan_contract,
    pytest_contracts,
    reconciliation,
)

MAX_OUTPUT_PARSE_BYTES = 8 * 1024 * 1024
MAX_JSON_REPORT_BYTES = 16 * 1024 * 1024
MAX_XML_REPORT_BYTES = 16 * 1024 * 1024
MAX_REPORTLOG_BYTES = 32 * 1024 * 1024
MAX_REPORTLOG_LINES = 500_000
MAX_REPORTLOG_LINE_BYTES = 1024 * 1024
MAX_GITDIR_POINTER_BYTES = 4096
MAX_SLOW_ITEMS = 25
MAX_TESTCASE_PROPERTY_RECORDS = 500

_ANSI_ESCAPE_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
)
_SUMMARY_HEADER_RE = re.compile(
    r"^(?P<indent> *)summary:\s+\(done in (?P<dur>.+?)\)\s*$"
)
_SUMMARY_HEADER_NO_DUR_RE = re.compile(
    r"^(?P<indent> *)summary:(?:\s*(?P<rest>.*))?\s*$"
)
_SUMMARY_ITEM_WITH_DUR_RE = re.compile(
    r"^(?P<indent> *)(?P<icon>✔️|✔\ufe0f?|✓|🥊|✗|✖|✘)\s+"
    r"(?P<name>.+?)\s+\((?P<dur>[^()]+)\)\s*$"
)
_SUMMARY_ITEM_NO_DUR_RE = re.compile(
    r"^(?P<indent> *)(?P<icon>✔️|✔\ufe0f?|✓|🥊|✗|✖|✘)\s+(?P<name>.+?)\s*$"
)
_SKIP_LINE_RE = re.compile(
    r"^\s*│\s+(?P<target>.+?)\s+\(skip\)(?:\s+(?P<reason>.+?))?\s*$"
)
_STARTED_LINE_RE = re.compile(r"^\s*┃\s+(?P<target>.+?)\s+❯\s*(?:.*)?$")
_DURATION_SECONDS_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)\s+seconds?$")

_PASS_ICONS = {"✔️", "✔", "✓"}
_FAIL_ICONS = {"🥊", "✗", "✖", "✘"}


def _read_bounded_bytes(
    path: Path, *, limit: int
) -> tuple[bytes | None, str | None, int]:
    """Read binary contents using an exact read ceiling instead of trusting pre-stat."""
    if path.is_symlink():
        return None, "symlink_rejected", 0
    if not path.is_file():
        return None, "missing", 0
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
    except OSError:
        return None, "unreadable", 0
    if len(raw) > limit:
        return None, "oversized", len(raw)
    return raw, None, len(raw)


def _read_bounded_text(
    path: Path | None, *, max_bytes: int | None = None
) -> tuple[str, bool, int]:
    """Read UTF-8 text within a strict stream byte ceiling, keeping head and tail only when exceeded."""
    if path is None or path.is_symlink() or not path.is_file():
        return "", False, 0
    limit = MAX_OUTPUT_PARSE_BYTES if max_bytes is None else max_bytes
    if limit <= 0:
        return "", True, 0
    head_limit = max(1, limit // 4)
    tail_limit = max(1, limit - head_limit)
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
            if len(raw) <= limit:
                return raw.decode("utf-8", errors="replace"), False, len(raw)
            head = raw[:head_limit]
            end_pos = stream.seek(0, os.SEEK_END)
            tail_start = max(len(head), end_pos - tail_limit)
            stream.seek(tail_start)
            tail = stream.read(tail_limit)
    except OSError:
        return "", False, 0

    if b"\n" in head:
        head = head.rsplit(b"\n", 1)[0]
    if b"\n" in tail:
        tail = tail.split(b"\n", 1)[-1]
    combined = (
        head.decode("utf-8", errors="replace")
        + "\n"
        + tail.decode("utf-8", errors="replace")
    )
    return combined, True, end_pos


def _parse_seconds_label(raw: str | None) -> tuple[float | str, float | None]:
    if not raw:
        return "unknown", None
    match = _DURATION_SECONDS_RE.match(raw.strip())
    if not match:
        return "unknown", None
    value = float(match.group(1))
    if not math.isfinite(value) or value < 0:
        return "unknown", None
    return round(value, 2), 0.01


def _strip_incomplete_duration_suffix(name: str) -> tuple[str, bool]:
    """Strip an unbalanced truncated duration tail like 'job-a (0.' cleanly."""
    if "(" in name and ")" not in name.rsplit("(", 1)[-1]:
        cleaned = name.rsplit("(", 1)[0].rstrip()
        if cleaned:
            return cleaned, True
    return name, False


def _collect_config_tree(
    config_section: Any, prefix: tuple[str, ...] = ()
) -> tuple[dict[tuple[str, ...], dict[str, Any]], list[tuple[str, ...]]]:
    """Extract declared job/group paths and parallel settings from lefthook dump JSON."""
    if not isinstance(config_section, dict):
        return {}, []
    mapping: dict[tuple[str, ...], dict[str, Any]] = {}
    leaf_paths: list[tuple[str, ...]] = []
    jobs = config_section.get("jobs")
    if not isinstance(jobs, list):
        return mapping, leaf_paths
    for idx, entry in enumerate(jobs):
        if not isinstance(entry, dict):
            continue
        group = entry.get("group")
        is_group = isinstance(group, dict)
        default_name = f"group ({idx})" if is_group else f"job ({idx})"
        raw_name = entry.get("name")
        name = (
            raw_name.strip()
            if isinstance(raw_name, str) and raw_name.strip()
            else default_name
        )
        path = (*prefix, name)
        parallel = bool(group.get("parallel", False)) if is_group else None
        mapping[path] = {"is_group": is_group, "parallel": parallel}
        if is_group:
            child_map, child_leaves = _collect_config_tree(group, path)
            mapping.update(child_map)
            leaf_paths.extend(child_leaves)
        else:
            leaf_paths.append(path)
    return mapping, leaf_paths


def _make_node(
    name: str,
    path: list[str],
    status: str,
    *,
    cfg: dict[str, Any] | None = None,
    duration_seconds: float | str = "unknown",
    duration_raw: str | None = None,
    precision_seconds: float | None = None,
    skip_reason: str | None = None,
    source: str | None = None,
    identity_source: str | None = None,
) -> dict[str, Any]:
    cfg = cfg or {}
    is_group = bool(cfg.get("is_group"))
    if duration_seconds == "unknown":
        duration_semantics = "unknown"
    elif is_group:
        duration_semantics = "cumulative_child_durations"
    else:
        duration_semantics = "job_result_duration"
    node: dict[str, Any] = {
        "name": name,
        "path": path,
        "status": status,
        "kind": "group" if is_group else "job",
        "parallel": cfg.get("parallel"),
        "duration_seconds": duration_seconds,
        "duration_raw": duration_raw,
        "duration_precision_seconds": precision_seconds,
        "duration_semantics": duration_semantics,
        "skip_reason": skip_reason,
        "children": [],
    }
    if is_group:
        node["wall_clock_seconds"] = "unknown"
    if identity_source is not None:
        node["identity_source"] = identity_source
    if source is not None:
        node["source"] = source
    return node


def _find_or_create_path(
    roots: list[dict[str, Any]],
    parts: list[str],
    config_map: dict[tuple[str, ...], dict[str, Any]],
    *,
    default_status: str,
    skip_reason: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Locate or insert a path inside the hierarchical job tree without duplicating nodes."""
    siblings = roots
    prefix: list[str] = []
    node: dict[str, Any] | None = None
    for idx, part in enumerate(parts):
        prefix.append(part)
        is_last = idx == len(parts) - 1
        existing = next((item for item in siblings if item["name"] == part), None)
        tprefix = tuple(prefix)
        cfg = config_map.get(tprefix, {})
        id_src = (
            "declared_config_name" if tprefix in config_map else "stream_display_label"
        )
        if existing is None:
            existing = _make_node(
                part,
                list(prefix),
                default_status,
                cfg=cfg if is_last else {**cfg, "is_group": True},
                skip_reason=skip_reason if is_last else None,
                source=source if is_last else None,
                identity_source=id_src,
            )
            siblings.append(existing)
        if not is_last:
            existing["kind"] = "group"
            existing["duration_semantics"] = (
                "cumulative_child_durations"
                if existing["duration_seconds"] != "unknown"
                else "unknown"
            )
            existing["wall_clock_seconds"] = "unknown"
            if existing["parallel"] is None and "parallel" in cfg:
                existing["parallel"] = cfg["parallel"]
        node = existing
        siblings = existing["children"]
    assert node is not None
    return node


def _parse_summary_block(
    lines: list[str],
    start_idx: int,
    config_map: dict[tuple[str, ...], dict[str, Any]],
) -> dict[str, Any]:
    header_line = lines[start_idx]
    match = _SUMMARY_HEADER_RE.match(header_line)
    if match:
        total_raw = match.group("dur").strip()
        total_seconds, total_precision = _parse_seconds_label(total_raw)
    else:
        total_raw = None
        total_seconds, total_precision = "unknown", None

    items: list[dict[str, Any]] = []
    stack: list[tuple[int, dict[str, Any]]] = []
    partial_line_cut = False

    for line in lines[start_idx + 1 :]:
        if not line.strip():
            break
        item_match = _SUMMARY_ITEM_WITH_DUR_RE.match(line)
        dur_raw: str | None = None
        if item_match:
            indent_len = len(item_match.group("indent"))
            icon = item_match.group("icon")
            candidate_dur = item_match.group("dur").strip()
            dur_val, precision = _parse_seconds_label(candidate_dur)
            if dur_val != "unknown":
                name = item_match.group("name").strip()
                dur_raw = candidate_dur
            else:
                # Non-duration parenthesized suffix belongs to display label, not duration.
                name = f"{item_match.group('name').strip()} ({candidate_dur})"
                dur_raw = None
        else:
            no_dur_match = _SUMMARY_ITEM_NO_DUR_RE.match(line)
            if not no_dur_match:
                break
            indent_len = len(no_dur_match.group("indent"))
            icon = no_dur_match.group("icon")
            name, stripped_incomplete = _strip_incomplete_duration_suffix(
                no_dur_match.group("name").strip()
            )
            partial_line_cut = partial_line_cut or stripped_incomplete
            dur_val, precision = "unknown", None

        status = (
            "passed"
            if icon in _PASS_ICONS
            else ("failed" if icon in _FAIL_ICONS else "unknown")
        )
        while stack and stack[-1][0] >= indent_len:
            stack.pop()
        parent = stack[-1][1] if stack else None
        path = [*parent["path"], name] if parent else [name]
        tpath = tuple(path)
        cfg = config_map.get(tpath, {})
        node = _make_node(
            name,
            path,
            status,
            cfg=cfg,
            duration_seconds=dur_val,
            duration_raw=dur_raw,
            precision_seconds=precision,
            identity_source=(
                "declared_config_name"
                if tpath in config_map
                else "summary_display_label"
            ),
        )
        if parent is None:
            items.append(node)
        else:
            parent["kind"] = "group"
            # Lefthook v2.2.2 Group.Duration sums child Result durations rather than measuring group wall clock.
            parent["duration_semantics"] = (
                "cumulative_child_durations"
                if parent["duration_seconds"] != "unknown"
                else "unknown"
            )
            parent["wall_clock_seconds"] = "unknown"
            if parent["parallel"] is None:
                parent_cfg = config_map.get(tuple(parent["path"]), {})
                parent["parallel"] = parent_cfg.get("parallel")
            parent["children"].append(node)
        stack.append((indent_len, node))

    return {
        "total_duration_seconds": total_seconds,
        "total_duration_raw": total_raw,
        "total_duration_precision_seconds": total_precision,
        "partial_line_cut": partial_line_cut,
        "items": items,
    }


def _flatten_leaf_jobs(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    leaves: list[dict[str, Any]] = []
    for node in nodes:
        if node["children"]:
            leaves.extend(_flatten_leaf_jobs(node["children"]))
        else:
            entry = {
                "name": node["name"],
                "path": list(node["path"]),
                "status": node["status"],
                "duration_seconds": node["duration_seconds"],
                "duration_precision_seconds": node["duration_precision_seconds"],
                "duration_semantics": node.get("duration_semantics", "unknown"),
                "skip_reason": node["skip_reason"],
            }
            if "identity_source" in node:
                entry["identity_source"] = node["identity_source"]
            if "source" in node:
                entry["source"] = node["source"]
            leaves.append(entry)
    return leaves


def parse_lefthook_output(
    stdout_path: Path | None,
    stderr_path: Path | None = None,
    *,
    verify_config: dict[str, Any] | None = None,
    native_exit_code: int | None = None,
) -> dict[str, Any]:
    """Parse bounded Lefthook v2.2.2 output into diagnostic hierarchical job timings."""
    stdout_text, stdout_truncated, stdout_bytes = _read_bounded_text(stdout_path)
    stderr_text, stderr_truncated, stderr_bytes = _read_bounded_text(stderr_path)
    stdout_bytes_parsed = (
        stdout_bytes if not stdout_truncated else len(stdout_text.encode("utf-8"))
    )
    stderr_bytes_parsed = (
        stderr_bytes if not stderr_truncated else len(stderr_text.encode("utf-8"))
    )
    output_truncated = stdout_truncated or stderr_truncated

    clean_stdout = _ANSI_ESCAPE_RE.sub("", stdout_text)
    clean_stderr = _ANSI_ESCAPE_RE.sub("", stderr_text)

    stdout_lines = clean_stdout.splitlines()
    stderr_lines = clean_stderr.splitlines()
    all_lines = stdout_lines + stderr_lines

    config_map, declared_leaves = _collect_config_tree(verify_config)
    root_parallel = (
        bool(verify_config.get("parallel", False))
        if isinstance(verify_config, dict)
        else None
    )

    skipped_events: list[tuple[list[str], str | None]] = []
    started_paths: list[list[str]] = []
    summary_idx: int | None = None
    summary_source_lines = stdout_lines

    for line in all_lines:
        skip_match = _SKIP_LINE_RE.match(line)
        if skip_match:
            parts = [
                part.strip()
                for part in skip_match.group("target").split(" ❯ ")
                if part.strip()
            ]
            if parts:
                skipped_events.append((parts, skip_match.group("reason")))
            continue
        start_match = _STARTED_LINE_RE.match(line)
        if start_match:
            parts = [
                part.strip()
                for part in start_match.group("target").split(" ❯ ")
                if part.strip()
            ]
            if parts and parts not in started_paths:
                started_paths.append(parts)

    for lines_candidate in (stdout_lines, stderr_lines):
        found_idx: int | None = None
        for idx, line in enumerate(lines_candidate):
            if _SUMMARY_HEADER_RE.match(line) or _SUMMARY_HEADER_NO_DUR_RE.match(line):
                found_idx = idx
        if found_idx is not None:
            summary_idx = found_idx
            summary_source_lines = lines_candidate
            break

    if summary_idx is not None:
        parsed_summary = _parse_summary_block(
            summary_source_lines, summary_idx, config_map
        )
        items = parsed_summary["items"]
        summary_present = True
        total_duration_seconds = parsed_summary["total_duration_seconds"]
        total_duration_raw = parsed_summary["total_duration_raw"]
        total_precision = parsed_summary["total_duration_precision_seconds"]
        output_truncated = output_truncated or parsed_summary["partial_line_cut"]
    else:
        items = []
        summary_present = False
        total_duration_seconds = "unknown"
        total_duration_raw = None
        total_precision = None
        default_started_status = (
            "unknown" if native_exit_code is not None else "incomplete"
        )
        for parts in started_paths:
            _find_or_create_path(
                items, parts, config_map, default_status=default_started_status
            )

    for parts, reason in skipped_events:
        _find_or_create_path(
            items,
            parts,
            config_map,
            default_status="skipped",
            skip_reason=reason or "skip",
        )

    flat_jobs = _flatten_leaf_jobs(items)
    seen_paths = {tuple(job["path"]) for job in flat_jobs}
    unresolved_declared_jobs = [
        list(path) for path in declared_leaves if path not in seen_paths
    ]
    for path in unresolved_declared_jobs:
        _find_or_create_path(
            items,
            list(path),
            config_map,
            default_status="unknown",
            source="declared_missing_from_summary",
        )
    if unresolved_declared_jobs:
        flat_jobs = _flatten_leaf_jobs(items)

    counts = {"passed": 0, "failed": 0, "skipped": 0, "unknown": 0}
    for job in flat_jobs:
        status = job["status"]
        if status in counts:
            counts[status] += 1
        else:
            counts["unknown"] += 1

    return {
        "parser": "lefthook-v2.2.2-bounded",
        "authority": "diagnostic_only",
        "not_authoritative_for": [
            "check_satisfaction",
            "admission_63",
            "release_gate_61",
        ],
        "summary_present": summary_present,
        "root_parallel": root_parallel,
        "total_duration_seconds": total_duration_seconds,
        "total_duration_raw": total_duration_raw,
        "total_duration_precision_seconds": total_precision,
        "total_duration_semantics": (
            "lefthook_reported_wall_seconds"
            if summary_present and total_duration_seconds != "unknown"
            else "unknown"
        ),
        "output_truncated": output_truncated,
        "stream_bytes_observed": {"stdout": stdout_bytes, "stderr": stderr_bytes},
        "bytes_parsed": {
            "stdout": stdout_bytes_parsed,
            "stderr": stderr_bytes_parsed,
            "limit": MAX_OUTPUT_PARSE_BYTES,
        },
        "counts": counts,
        "items": items,
        "jobs": flat_jobs,
        "unresolved_declared_jobs": unresolved_declared_jobs,
    }


def _load_bounded_json(path: Path, *, max_bytes: int | None = None) -> dict[str, Any]:
    limit = MAX_JSON_REPORT_BYTES if max_bytes is None else max_bytes
    raw, err, size = _read_bounded_bytes(path, limit=limit)
    if err is not None:
        out: dict[str, Any] = {"status": err, "path": str(path)}
        if err == "oversized":
            out.update(bytes=size, max_bytes=limit)
        return out
    assert raw is not None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"status": "invalid", "path": str(path), "error": str(exc)}
    if not isinstance(payload, dict):
        return {
            "status": "invalid",
            "path": str(path),
            "error": "expected JSON object",
        }
    return {"status": "loaded", "path": str(path), "payload": payload, "raw": raw}


def _safe_child_path(root: Path, rel: Any) -> Path | None:
    """Resolve a controlled relative file inside ``root`` without escape or symlinks."""
    if not isinstance(rel, str) or not rel:
        return None
    rel_path = Path(rel)
    if rel_path.is_absolute() or ".." in rel_path.parts:
        return None
    root_resolved = root.resolve()
    current = root_resolved
    for part in rel_path.parts:
        current = current / part
        if current.is_symlink() or current.is_junction():
            return None
    candidate = current.resolve()
    if not candidate.is_relative_to(root_resolved):
        return None
    return candidate


def _stream_sha256(path: Path) -> str | None:
    if path.is_symlink() or path.is_junction() or not path.is_file():
        return None
    try:
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError:
        return None


def _verify_native_output_entry(verify_dir: Path, spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        return {"status": "invalid_spec"}
    rel = spec.get("path")
    expected_sha = spec.get("sha256")
    if not isinstance(expected_sha, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_sha
    ):
        return {"status": "invalid_spec", "path": rel}
    target = _safe_child_path(verify_dir, rel)
    if target is None:
        return {"status": "path_unsafe", "path": rel, "expected_sha256": expected_sha}
    if not target.is_file():
        return {"status": "missing", "path": rel, "expected_sha256": expected_sha}
    observed_sha = _stream_sha256(target)
    if observed_sha is None:
        return {"status": "unreadable", "path": rel, "expected_sha256": expected_sha}
    if observed_sha != expected_sha:
        return {
            "status": "digest_mismatch",
            "path": rel,
            "expected_sha256": expected_sha,
            "observed_sha256": observed_sha,
            "resolved_path": str(target),
        }
    return {
        "status": "verified",
        "path": rel,
        "sha256": observed_sha,
        "resolved_path": str(target),
    }


def _norm_optional_str(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    return text or None


def _load_verify_entry(verify_dir: Path) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "source_type": "verify",
        "path": str(verify_dir),
        "internal_conflicts": [],
        "consumed_files": [],
    }
    if verify_dir.is_symlink() or not verify_dir.is_dir():
        entry["status"] = "missing" if not verify_dir.exists() else "path_unsafe"
        return entry
    timing_path = verify_dir / "verify-timing.json"
    status_path = verify_dir / "verify-status.json"
    timing = (
        _load_bounded_json(timing_path)
        if (timing_path.exists() or timing_path.is_symlink())
        else None
    )
    status = (
        _load_bounded_json(status_path)
        if (status_path.exists() or status_path.is_symlink())
        else None
    )
    if timing is not None:
        entry["consumed_files"].append(timing["path"])
        if timing.get("status") != "loaded":
            t_conflict: dict[str, Any] = {
                "reason": "verify_timing_invalid",
                "artifact": "verify-timing.json",
                "detail": timing.get("status"),
            }
            if "error" in timing:
                t_conflict["error"] = timing["error"]
            entry["internal_conflicts"].append(t_conflict)
    if status is not None:
        entry["consumed_files"].append(status["path"])
        if status.get("status") != "loaded":
            s_conflict: dict[str, Any] = {
                "reason": "verify_status_invalid",
                "artifact": "verify-status.json",
                "detail": status.get("status"),
            }
            if "error" in status:
                s_conflict["error"] = status["error"]
            entry["internal_conflicts"].append(s_conflict)
    if timing is not None and status is not None:
        status_state = (
            status["payload"].get("status")
            if status.get("status") == "loaded"
            and isinstance(status.get("payload"), dict)
            else status.get("status")
        )
        entry["internal_conflicts"].append(
            {
                "reason": "verify_timing_status_conflict",
                "artifacts": ["verify-timing.json", "verify-status.json"],
                "status_receipt_state": status_state,
            }
        )

    chosen = (
        timing
        if timing is not None and timing.get("status") == "loaded"
        else (
            status if status is not None and status.get("status") == "loaded" else None
        )
    )
    if chosen is None:
        missing = timing or status or {"status": "missing", "path": str(timing_path)}
        entry["status"] = missing["status"]
        if "error" in missing:
            entry["error"] = missing["error"]
        if missing is not timing and missing.get("path"):
            entry["artifact"] = missing["path"]
        return entry

    payload = chosen["payload"]
    native_outputs = payload.get("native_outputs")
    verified_outputs: dict[str, dict[str, Any]] = {}
    missing_streams: list[str] = []
    if isinstance(native_outputs, dict):
        for stream_name in ("stdout", "stderr"):
            if (
                stream_name in native_outputs
                and native_outputs[stream_name] is not None
            ):
                v_result = _verify_native_output_entry(
                    verify_dir, native_outputs[stream_name]
                )
                verified_outputs[stream_name] = v_result
                if "resolved_path" in v_result:
                    entry["consumed_files"].append(v_result["resolved_path"])
                if v_result["status"] != "verified":
                    entry["internal_conflicts"].append(
                        {
                            "reason": f"verify_native_{stream_name}_{v_result['status']}",
                            "detail": v_result,
                        }
                    )
            else:
                missing_streams.append(stream_name)
                verified_outputs[stream_name] = {"status": "missing_stream"}
        if missing_streams and (
            chosen is timing
            or payload.get("native_started") is True
            or len(missing_streams) == 1
        ):
            entry["internal_conflicts"].append(
                {
                    "reason": "verify_native_required_streams_missing",
                    "missing_streams": missing_streams,
                }
            )
    else:
        missing_streams = ["stdout", "stderr"]
        if chosen is timing or payload.get("native_started") is True:
            entry["internal_conflicts"].append(
                {
                    "reason": "verify_native_outputs_missing",
                    "missing_streams": missing_streams,
                }
            )

    outputs_complete = (
        chosen is timing
        and not missing_streams
        and all(
            verified_outputs.get(name, {}).get("status") == "verified"
            for name in ("stdout", "stderr")
        )
        and not entry["internal_conflicts"]
    )
    native_exit = payload.get("native_exit_code")
    native_exit_source = _norm_optional_str(payload.get("native_exit_source"))
    if native_exit_source is None and chosen is timing and native_exit is not None:
        native_exit_source = "natural_exit"
    outer_exit_code = payload.get("outer_exit_code")
    if outer_exit_code is None and chosen is timing and type(native_exit) is int:
        outer_exit_code = native_exit if native_exit >= 0 else 128 - native_exit
    native_started_val = payload.get("native_started")
    if native_started_val is None and chosen is timing:
        native_started_val = True

    child_references = payload.get("child_references")
    if not isinstance(child_references, dict):
        child_references = {"status": "not_recorded"}

    entry.update(
        {
            "status": (
                "completed"
                if chosen is timing
                else str(payload.get("status", "incomplete"))
            ),
            "receipt_kind": (
                "verify-timing.json" if chosen is timing else "verify-status.json"
            ),
            "receipt_path": chosen["path"],
            "invocation_id": _norm_optional_str(payload.get("invocation_id")),
            "git_commit": _norm_optional_str(payload.get("observed_git_head")),
            "observed_lefthook_config_sha256": _norm_optional_str(
                payload.get("observed_lefthook_config_sha256")
            ),
            "native_started": native_started_val,
            "native_exit_code": native_exit,
            "native_exit_source": native_exit_source,
            "outer_exit_code": outer_exit_code,
            "elapsed_seconds": payload.get("elapsed_seconds"),
            "stages": payload.get("stages"),
            "started_at_ns": payload.get("started_at_ns"),
            "completed_at_ns": payload.get("completed_at_ns"),
            "time_source_verified": False,
            "native_outputs_verification": verified_outputs,
            "missing_native_streams": missing_streams,
            "evidence_complete": outputs_complete,
            "timing_receipt_present": timing is not None,
            "status_receipt_present": status is not None,
            "child_references": child_references,
            "lefthook_summary": payload.get("lefthook_summary"),
        }
    )
    if payload.get("signal") is not None:
        entry["signal"] = payload.get("signal")
    if payload.get("error") is not None:
        entry["error"] = payload.get("error")
    return entry


def _resolve_git_dir_readonly(root: Path) -> Path | None:
    """Resolve normal or linked-worktree Git dir using file IO only (zero git subprocess)."""
    if root.is_symlink() or not root.exists():
        return None
    if root.is_dir() and (root / "HEAD").is_file():
        return root.resolve()
    dot_git = root / ".git"
    if dot_git.is_symlink():
        return None
    if dot_git.is_dir() and (dot_git / "HEAD").is_file():
        return dot_git.resolve()
    if dot_git.is_file():
        raw, err, _ = _read_bounded_bytes(dot_git, limit=MAX_GITDIR_POINTER_BYTES)
        if err is not None or raw is None:
            return None
        first_line = raw.decode("utf-8", errors="replace").splitlines()[:1]
        if not first_line or not first_line[0].startswith("gitdir:"):
            return None
        target_raw = first_line[0][7:].strip()
        if not target_raw:
            return None
        target = Path(target_raw)
        if not target.is_absolute():
            target = root / target
        resolved = target.resolve()
        if resolved.is_dir() and (resolved / "HEAD").is_file():
            return resolved
    return None


def _report_ref_key(report_ref: Any) -> str | None:
    if isinstance(report_ref, dict):
        try:
            normalized = reconciliation._report_ref(report_ref, "invalid_report_ref")
            return json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        except (reconciliation.ReconciliationError, TypeError, ValueError):
            return json.dumps(report_ref, sort_keys=True, separators=(",", ":"))
    if isinstance(report_ref, str) and report_ref.strip():
        return report_ref.strip()
    return None


def _compute_execution_report_ref_key(report_payload: dict[str, Any]) -> str | None:
    if report_payload.get("kind") != execution.REPORT_KIND:
        return None
    try:
        ref_obj = reconciliation.execution_report_ref(report_payload)
        return _report_ref_key(ref_obj)
    except (reconciliation.ReconciliationError, TypeError, ValueError):
        return None


def _load_hook_entry(
    *,
    hook_root: Path | None = None,
    invocation_ref: str | None = None,
    hook_invocation: Path | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "source_type": "hook_invocation",
        "path": None,
        "internal_conflicts": [],
        "consumed_files": [],
        "consumed_dirs": [],
    }
    git_dir: Path | None = None
    inv_path: Path | None = None

    if hook_invocation is not None:
        if hook_invocation.is_symlink():
            entry["status"] = "path_unsafe"
            entry["path"] = str(hook_invocation)
            return entry
        inv_path = hook_invocation
        entry["path"] = str(inv_path)
        if inv_path.is_file():
            with suppress(hooks.HookEventError):
                git_dir = hooks._invocation_git_dir(inv_path)
    elif hook_root is not None or invocation_ref is not None:
        if hook_root is None or not invocation_ref:
            entry["status"] = "missing_arguments"
            entry["error"] = (
                "require --hook-invocation or both --hook-root and --invocation-ref"
            )
            return entry
        match = (
            hooks._INVOCATION_REF.fullmatch(invocation_ref)
            if isinstance(invocation_ref, str)
            else None
        )
        if match is None:
            entry["status"] = "invalid_ref"
            entry["error"] = "invocation_ref_invalid"
            entry["invocation_ref"] = invocation_ref
            return entry
        git_dir = _resolve_git_dir_readonly(hook_root)
        if git_dir is None:
            entry["status"] = "git_dir_unavailable"
            entry["error"] = "git_directory_unavailable"
            entry["invocation_ref"] = invocation_ref
            return entry
        invocations_dir = (git_dir / "eci-hooks" / "invocations").resolve()
        inv_path = invocations_dir / f"{match[1]}.json"
        entry["path"] = str(inv_path)
        if inv_path.is_symlink() or inv_path.resolve().parent != invocations_dir:
            entry["status"] = "path_unsafe"
            return entry
    else:
        entry["status"] = "missing_arguments"
        return entry

    if git_dir is not None:
        hooks_dir = (git_dir / "eci-hooks").resolve()
        if hooks_dir.is_dir():
            entry["consumed_dirs"].append(str(hooks_dir))
    assert inv_path is not None
    if not inv_path.is_file():
        entry["status"] = "missing"
        return entry

    try:
        binding = hooks.load_invocation_binding(
            inv_path, max_bytes=MAX_JSON_REPORT_BYTES
        )
    except hooks.HookEventError as exc:
        if exc.code in ("invocation_payload_oversized", "event_payload_oversized"):
            entry["status"] = "oversized"
            entry["error"] = exc.code
        else:
            entry["status"] = "invalid"
            entry["error"] = exc.code
        return entry
    except (OSError, ValueError) as exc:
        entry["status"] = "invalid"
        entry["error"] = str(exc)
        return entry

    if not entry["consumed_dirs"]:
        hooks_dir = binding.invocation_path.parent.parent.resolve()
        if hooks_dir.is_dir():
            entry["consumed_dirs"].append(str(hooks_dir))
    entry["consumed_files"].extend(
        [str(binding.invocation_path), str(binding.event_path)]
    )
    inspected = hook_checks.inspect_invocation_offline(
        binding, max_bytes=MAX_JSON_REPORT_BYTES
    )
    entry["consumed_files"] = list(
        dict.fromkeys([*entry["consumed_files"], *inspected["consumed_files"]])
    )
    entry["internal_conflicts"].extend(inspected["internal_conflicts"])
    entry.update(
        {
            "status": "loaded",
            "invocation_ref": binding.invocation_ref,
            "event_payload_ref": binding.event_payload_ref,
            "hook": binding.hook,
            "event_file": str(binding.event_path),
            "plan_ref": inspected["plan_ref"],
            "plan_scope": inspected["plan_scope"],
            "expected_unit_count": inspected["expected_unit_count"],
            "expected_execution_artifacts": inspected["expected_execution_artifacts"],
            "missing_expected_executions": inspected["missing_expected_executions"],
            "git_commits": inspected["git_commits"],
            "source_tree_ids": inspected["source_tree_ids"],
            "push_references": inspected["push_references"],
            "executions": inspected["executions"],
            "receipt": inspected["receipt"],
            "stage_timings": inspected["stage_timings"],
        }
    )
    return entry


def _parse_valid_nonneg_float(raw: Any) -> tuple[float | str, str | None]:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return "unknown", "missing"
    if isinstance(raw, bool):
        return "invalid", "boolean"
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return "invalid", "unparseable"
    if not math.isfinite(val) or val < 0:
        return "invalid", "negative_or_nonfinite"
    return val, None


_CORRELATION_PROP_KEYS = frozenset(
    {
        "git_commit",
        "sha",
        "invocation_ref",
        "run_ref",
        "attempt_ref",
        "execution_ref",
        "matched_trace_id",
        "trace_id",
        "plan_ref",
    }
)


def _extract_xml_properties(root: ET.Element) -> dict[str, Any]:
    """Extract suite/session correlation properties separately from per-testcase properties."""
    suite_props: dict[str, list[str]] = {}
    suite_nodes = (
        [root] if root.tag == "testsuite" else list(root.findall(".//testsuite"))
    )
    if root.tag == "testsuites":
        for prop in root.findall("./properties/property"):
            name = (prop.attrib.get("name") or "").strip()
            val = (prop.attrib.get("value") or "").strip()
            if name and val:
                suite_props.setdefault(name, []).append(val)
    for suite in suite_nodes:
        for prop in suite.findall("./properties/property"):
            name = (prop.attrib.get("name") or "").strip()
            val = (prop.attrib.get("value") or "").strip()
            if name and val:
                suite_props.setdefault(name, []).append(val)

    testcase_prop_names: set[str] = set()
    testcases_with_props = 0
    testcase_properties: list[dict[str, Any]] = []
    testcase_verifies: list[dict[str, Any]] = []
    testcase_props_truncated = False
    for case in root.findall(".//testcase"):
        case_props = case.findall("./properties/property")
        if case_props:
            testcases_with_props += 1
        per_case_map: dict[str, list[str]] = {}
        for prop in case_props:
            name = (prop.attrib.get("name") or "").strip()
            val = (prop.attrib.get("value") or "").strip()
            if name and val:
                testcase_prop_names.add(name)
                per_case_map.setdefault(name, []).append(val)
        if per_case_map:
            if len(testcase_properties) < MAX_TESTCASE_PROPERTY_RECORDS:
                unique_map = {
                    k: list(dict.fromkeys(v)) for k, v in per_case_map.items()
                }
                cls_name = (case.attrib.get("classname") or "").strip()
                tc_name = (case.attrib.get("name") or "").strip()
                testcase_properties.append(
                    {
                        "classname": cls_name,
                        "name": tc_name,
                        "properties": unique_map,
                    }
                )
                if "verifies" in unique_map:
                    testcase_verifies.append(
                        {
                            "classname": cls_name,
                            "name": tc_name,
                            "values": unique_map["verifies"],
                        }
                    )
            else:
                testcase_props_truncated = True

    single_props: dict[str, str] = {}
    conflicts: list[dict[str, Any]] = []
    for name, vals in suite_props.items():
        unique = list(dict.fromkeys(vals))
        if len(unique) == 1:
            single_props[name] = unique[0]
        elif name in _CORRELATION_PROP_KEYS:
            conflicts.append({"property": name, "scope": "testsuite", "values": unique})
    return {
        "values": single_props,
        "conflicts": conflicts,
        "testcase_properties_summary": {
            "testcases_with_properties": testcases_with_props,
            "property_names": sorted(testcase_prop_names),
            "truncated": testcase_props_truncated,
            "verifies_authority": "raw_junit_diagnostic_only_not_c2_delivery",
        },
        "testcase_properties": testcase_properties,
        "testcase_verifies": testcase_verifies,
    }


def _parse_junit_xml(path: Path, *, max_bytes: int | None = None) -> dict[str, Any]:
    limit = MAX_XML_REPORT_BYTES if max_bytes is None else max_bytes
    raw, err, size = _read_bounded_bytes(path, limit=limit)
    if err is not None:
        out: dict[str, Any] = {
            "source_type": "junit_xml",
            "path": str(path),
            "status": err,
            "internal_conflicts": [],
            "consumed_files": [str(path)] if err != "missing" else [],
        }
        if err == "oversized":
            out.update(bytes=size, max_bytes=limit)
        return out
    assert raw is not None
    raw_sha256 = hashlib.sha256(raw).hexdigest()
    try:
        raw_text = raw.decode("utf-8", errors="replace")
    except ValueError as exc:
        return {
            "source_type": "junit_xml",
            "path": str(path),
            "status": "invalid",
            "error": str(exc),
            "internal_conflicts": [],
            "consumed_files": [str(path)],
        }
    if "<!DOCTYPE" in raw_text or "<!ENTITY" in raw_text:
        return {
            "source_type": "junit_xml",
            "path": str(path),
            "status": "rejected_dtd",
            "error": "XML DOCTYPE/ENTITY declarations are forbidden",
            "internal_conflicts": [],
            "consumed_files": [str(path)],
        }
    try:
        root = ET.fromstring(raw_text)
    except ET.ParseError as exc:
        return {
            "source_type": "junit_xml",
            "path": str(path),
            "status": "invalid",
            "error": str(exc),
            "internal_conflicts": [],
            "consumed_files": [str(path)],
        }

    try:
        suites, totals = pytest_contracts.parse_junit_suites_and_counts(root)
    except ValueError as exc:
        return {
            "source_type": "junit_xml",
            "path": str(path),
            "status": "invalid",
            "error": str(exc),
            "internal_conflicts": [],
            "consumed_files": [str(path)],
        }
    total_time = 0.0
    has_valid_suite_time = False
    invalid_durations = 0
    missing_durations = 0

    for suite in suites:
        raw_time = suite.attrib.get("time")
        if raw_time is None:
            continue
        parsed_time, _reason = _parse_valid_nonneg_float(raw_time)
        if isinstance(parsed_time, float):
            total_time += parsed_time
            has_valid_suite_time = True
        elif parsed_time == "invalid":
            invalid_durations += 1
        elif parsed_time == "unknown":
            missing_durations += 1

    if not has_valid_suite_time and root.tag == "testsuites":
        parsed_root, _reason = _parse_valid_nonneg_float(root.attrib.get("time"))
        if isinstance(parsed_root, float):
            total_time = parsed_root
            has_valid_suite_time = True
        elif parsed_root == "invalid":
            invalid_durations += 1

    numeric_cases: list[dict[str, Any]] = []
    for suite in suites:
        for case in suite.findall(".//testcase"):
            parsed_dur, _ = _parse_valid_nonneg_float(case.attrib.get("time"))
            if parsed_dur == "invalid":
                invalid_durations += 1
            elif parsed_dur == "unknown":
                missing_durations += 1
            if not isinstance(parsed_dur, float):
                continue
            outcome = "passed"
            if case.find("failure") is not None:
                outcome = "failed"
            elif case.find("error") is not None:
                outcome = "error"
            elif case.find("skipped") is not None:
                outcome = "skipped"
            numeric_cases.append(
                {
                    "classname": case.attrib.get("classname", ""),
                    "name": case.attrib.get("name", ""),
                    "status": outcome,
                    "duration_seconds": parsed_dur,
                }
            )

    slowest = sorted(
        numeric_cases, key=lambda item: float(item["duration_seconds"]), reverse=True
    )[:MAX_SLOW_ITEMS]
    prop_info = _extract_xml_properties(root)
    internal_conflicts = [
        {"reason": "junit_property_conflict", **c} for c in prop_info["conflicts"]
    ]
    total_duration: float | str
    if invalid_durations and not has_valid_suite_time:
        total_duration = "invalid"
    elif has_valid_suite_time:
        total_duration = total_time
    else:
        total_duration = "unknown"

    return {
        "source_type": "junit_xml",
        "path": str(path),
        "status": "loaded",
        "sha256": raw_sha256,
        "counts": totals,
        "total_duration_seconds": total_duration,
        "invalid_duration_count": invalid_durations,
        "missing_duration_count": missing_durations,
        "slowest_tests": slowest,
        "properties": prop_info["values"],
        "testcase_properties_summary": prop_info["testcase_properties_summary"],
        "testcase_properties": prop_info["testcase_properties"],
        "testcase_verifies": prop_info["testcase_verifies"],
        "internal_conflicts": internal_conflicts,
        "consumed_files": [str(path)],
    }


def _extract_reportlog_props(
    record: dict[str, Any],
    session_corr: dict[str, list[str]],
    node_user_props: dict[str, dict[str, list[str]]],
) -> None:
    nodeid = _norm_optional_str(record.get("nodeid")) or "<session>"
    user_props = record.get("user_properties")
    if isinstance(user_props, list):
        target_node = node_user_props.setdefault(nodeid, {})
        for pair in user_props:
            if (
                isinstance(pair, (list, tuple))
                and len(pair) == 2
                and isinstance(pair[0], str)
            ):
                val = _norm_optional_str(pair[1])
                if val is not None:
                    target_node.setdefault(pair[0], []).append(val)
    for key in _CORRELATION_PROP_KEYS:
        val = _norm_optional_str(record.get(key))
        if val is not None:
            session_corr.setdefault(key, []).append(val)


def _derive_testcase_verdict(
    phases: dict[str, dict[str, Any]],
    *,
    breakdown: dict[str, int] | None = None,
) -> str:
    """Compute final testcase conclusion faithfully across setup/call/teardown phases."""
    setup = phases.get("setup")
    call = phases.get("call")
    teardown = phases.get("teardown")

    def _bump(key: str) -> None:
        if breakdown is not None:
            breakdown[key] = breakdown.get(key, 0) + 1

    if setup is not None:
        if setup["outcome"] == "failed":
            _bump("setup_error")
            return "error"
        if setup["outcome"] == "skipped":
            if teardown is None:
                _bump("setup_skipped_teardown_missing")
                return "incomplete"
            if teardown["outcome"] == "failed":
                _bump("setup_skipped_teardown_error")
                return "error"
            if setup.get("wasxfail"):
                _bump("setup_skipped_xfailed")
                return "xfailed"
            _bump("setup_skipped")
            return "skipped"

    if teardown is not None and teardown["outcome"] == "failed":
        _bump("teardown_error")
        return "error"

    if call is not None:
        if call["outcome"] == "failed":
            if teardown is None:
                _bump("call_failed_teardown_missing")
            else:
                _bump("call_failed")
            return "failed"
        if call["outcome"] == "skipped":
            if teardown is None:
                _bump("call_skipped_teardown_missing")
                return "incomplete"
            if call.get("wasxfail"):
                _bump("call_skipped_xfailed")
                return "xfailed"
            _bump("call_skipped")
            return "skipped"
        if call["outcome"] == "passed":
            if teardown is None:
                _bump("call_passed_teardown_missing")
                return "incomplete"
            if call.get("wasxfail"):
                _bump("call_xpassed")
                return "xpassed"
            _bump("passed")
            return "passed"

    _bump("incomplete_other")
    return "incomplete"


def _partition_node_attempts(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, dict[str, Any]]], list[tuple[str, list[str]]]]:
    """Split chronological TestReport records for one nodeid without overwriting retries."""
    attempts: list[dict[str, dict[str, Any]]] = []
    by_when: dict[str, list[str]] = {}
    current: dict[str, dict[str, Any]] = {}
    for rec in records:
        when = rec["when"]
        by_when.setdefault(when, []).append(rec["outcome"])
        if when in current or (
            when == "setup" and ("call" in current or "teardown" in current)
        ):
            attempts.append(current)
            current = {}
        current[when] = rec
    if current:
        attempts.append(current)
    repeated = [
        (when, outcomes) for when, outcomes in by_when.items() if len(outcomes) > 1
    ]
    return attempts, repeated


def _parse_pytest_reportlog(
    path: Path, *, max_bytes: int | None = None
) -> dict[str, Any]:
    if path.is_symlink():
        return {
            "source_type": "pytest_reportlog",
            "path": str(path),
            "status": "symlink_rejected",
            "internal_conflicts": [],
            "consumed_files": [],
        }
    if not path.is_file():
        return {
            "source_type": "pytest_reportlog",
            "path": str(path),
            "status": "missing",
            "internal_conflicts": [],
            "consumed_files": [],
        }
    limit = MAX_REPORTLOG_BYTES if max_bytes is None else max_bytes
    bytes_read = 0
    line_count = 0
    truncated = False
    malformed_line_count = 0
    invalid_duration_count = 0
    session_corr_props: dict[str, list[str]] = {}
    node_user_props: dict[str, dict[str, list[str]]] = {}
    phase_counts: dict[str, dict[str, int]] = {
        "setup": {"passed": 0, "failed": 0, "skipped": 0, "other": 0},
        "call": {"passed": 0, "failed": 0, "skipped": 0, "other": 0},
        "teardown": {"passed": 0, "failed": 0, "skipped": 0, "other": 0},
    }
    node_records: dict[str, list[dict[str, Any]]] = {}
    durations: list[dict[str, Any]] = []
    cumulative_phase_seconds = 0.0
    has_session_start = False
    has_session_finish = False
    has_collection = False
    collection_failed_count = 0
    collection_errors: list[dict[str, Any]] = []
    session_start_ts: float | None = None
    session_finish_ts: float | None = None
    exit_status: int | None = None
    hasher = hashlib.sha256()

    def _consume_line(raw_line: bytes) -> None:
        nonlocal malformed_line_count, invalid_duration_count, cumulative_phase_seconds
        nonlocal has_session_start, has_session_finish, has_collection
        nonlocal collection_failed_count, session_start_ts
        nonlocal session_finish_ts, exit_status
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            return
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            malformed_line_count += 1
            return
        if not isinstance(record, dict):
            malformed_line_count += 1
            return
        _extract_reportlog_props(record, session_corr_props, node_user_props)
        report_type = record.get("$report_type")
        if report_type == "SessionStart":
            has_session_start = True
            created = record.get("created_at") or record.get("start")
            if isinstance(created, (int, float)) and math.isfinite(float(created)):
                session_start_ts = float(created)
        elif report_type == "SessionFinish":
            has_session_finish = True
            raw_exit = record.get("exitstatus")
            if type(raw_exit) is int:
                exit_status = raw_exit
            finished = record.get("created_at") or record.get("stop")
            if isinstance(finished, (int, float)) and math.isfinite(float(finished)):
                session_finish_ts = float(finished)
        elif report_type == "CollectReport":
            has_collection = True
            if record.get("outcome") == "failed":
                collection_failed_count += 1
                if len(collection_errors) < MAX_SLOW_ITEMS:
                    collection_errors.append(
                        {
                            "nodeid": str(record.get("nodeid", "")),
                            "when": record.get("when"),
                        }
                    )
        elif report_type == "TestReport":
            when = record.get("when", "")
            outcome = record.get("outcome", "")
            nodeid = str(record.get("nodeid", ""))
            wasxfail = bool(record.get("wasxfail"))
            if when in phase_counts:
                bucket = (
                    outcome if outcome in ("passed", "failed", "skipped") else "other"
                )
                phase_counts[when][bucket] += 1
            parsed_dur, _ = _parse_valid_nonneg_float(record.get("duration"))
            if isinstance(parsed_dur, float):
                cumulative_phase_seconds += parsed_dur
                durations.append(
                    {
                        "nodeid": nodeid,
                        "when": when,
                        "outcome": outcome,
                        "duration_seconds": parsed_dur,
                    }
                )
            elif parsed_dur == "invalid":
                invalid_duration_count += 1
            if when in ("setup", "call", "teardown") and nodeid:
                node_records.setdefault(nodeid, []).append(
                    {
                        "when": when,
                        "outcome": outcome,
                        "wasxfail": wasxfail,
                    }
                )

    try:
        with path.open("rb") as stream:
            buf = b""
            while not truncated:
                remaining = limit - bytes_read
                if remaining <= 0:
                    if stream.read(1):
                        truncated = True
                    break
                chunk = stream.read(min(65536, remaining))
                if not chunk:
                    break
                bytes_read += len(chunk)
                hasher.update(chunk)
                buf += chunk
                while b"\n" in buf:
                    raw_line, buf = buf.split(b"\n", 1)
                    if len(raw_line) > MAX_REPORTLOG_LINE_BYTES:
                        truncated = True
                        break
                    line_count += 1
                    if line_count > MAX_REPORTLOG_LINES:
                        truncated = True
                        break
                    _consume_line(raw_line)
                if len(buf) > MAX_REPORTLOG_LINE_BYTES:
                    truncated = True
            if not truncated and buf:
                if (
                    len(buf) > MAX_REPORTLOG_LINE_BYTES
                    or line_count >= MAX_REPORTLOG_LINES
                ):
                    truncated = True
                else:
                    _consume_line(buf)
    except OSError as exc:
        return {
            "source_type": "pytest_reportlog",
            "path": str(path),
            "status": "invalid",
            "error": str(exc),
            "internal_conflicts": [],
            "consumed_files": [str(path)],
        }

    testcase_counts = {
        "passed": 0,
        "failed": 0,
        "error": 0,
        "skipped": 0,
        "xfailed": 0,
        "xpassed": 0,
        "incomplete": 0,
        "repeated_ambiguous": 0,
    }
    outcome_breakdown: dict[str, int] = {}
    attempt_verdict_counts: dict[str, int] = {}
    repeated_phases: list[dict[str, Any]] = []
    repeated_phase_record_count = 0
    for nodeid, records in node_records.items():
        attempts, repeated = _partition_node_attempts(records)
        if repeated:
            for when, outcomes in repeated:
                repeated_phase_record_count += len(outcomes) - 1
                if len(repeated_phases) < MAX_SLOW_ITEMS:
                    repeated_phases.append(
                        {
                            "nodeid": nodeid,
                            "when": when,
                            "count": len(outcomes),
                            "outcomes": outcomes,
                        }
                    )
        attempt_verdicts = [
            _derive_testcase_verdict(att, breakdown=outcome_breakdown)
            for att in attempts
        ]
        for av in attempt_verdicts:
            attempt_verdict_counts[av] = attempt_verdict_counts.get(av, 0) + 1
        if len(attempts) > 1:
            testcase_counts["repeated_ambiguous"] += 1
        else:
            testcase_counts[attempt_verdicts[0]] += 1

    # Keep correlation keys session-scoped while per-node user_properties like
    # verifies stay strictly testcase-scoped without cross-node false conflicts.
    prop_values: dict[str, str] = {}
    internal_conflicts: list[dict[str, Any]] = []
    for key, vals in session_corr_props.items():
        unique = list(dict.fromkeys(vals))
        if len(unique) == 1:
            prop_values[key] = unique[0]
        else:
            internal_conflicts.append(
                {
                    "reason": "reportlog_property_conflict",
                    "property": key,
                    "scope": "session_correlation",
                    "values": unique,
                }
            )
    user_prop_names: set[str] = set()
    user_corr_vals: dict[str, list[str]] = {}
    testcase_user_properties: list[dict[str, Any]] = []
    testcase_verifies: list[dict[str, Any]] = []
    testcase_props_truncated = False
    for nid, props_map in node_user_props.items():
        unique_map: dict[str, list[str]] = {}
        for pname, vals in props_map.items():
            user_prop_names.add(pname)
            unique_map[pname] = list(dict.fromkeys(vals))
            if pname in _CORRELATION_PROP_KEYS:
                user_corr_vals.setdefault(pname, []).extend(vals)
        if unique_map:
            if len(testcase_user_properties) < MAX_TESTCASE_PROPERTY_RECORDS:
                testcase_user_properties.append(
                    {"nodeid": nid, "properties": unique_map}
                )
                if "verifies" in unique_map:
                    testcase_verifies.append(
                        {"nodeid": nid, "values": unique_map["verifies"]}
                    )
            else:
                testcase_props_truncated = True
    for key, vals in user_corr_vals.items():
        unique = list(dict.fromkeys(vals))
        if key in prop_values and any(v != prop_values[key] for v in unique):
            internal_conflicts.append(
                {
                    "reason": "reportlog_property_conflict",
                    "property": key,
                    "scope": "user_properties_correlation",
                    "values": sorted({prop_values[key], *unique}),
                }
            )
        elif key not in prop_values and len(unique) == 1:
            prop_values[key] = unique[0]

    if (
        has_session_start
        and has_session_finish
        and session_start_ts is not None
        and session_finish_ts is not None
        and session_finish_ts >= session_start_ts
    ):
        session_wall_seconds: float | str = session_finish_ts - session_start_ts
    else:
        session_wall_seconds = "unknown"

    unsupported_semantics: list[str] = []
    if repeated_phases:
        unsupported_semantics.append(
            "repeated_phase_records_observed_without_retry_or_subtest_contract"
        )

    has_meaningful_pytest_evidence = (
        bool(node_records) or has_collection or exit_status == 5
    )
    if malformed_line_count and not (has_session_finish or testcase_counts["passed"]):
        completeness = "corrupted"
    elif (
        has_session_start
        and has_session_finish
        and exit_status is not None
        and not truncated
        and malformed_line_count == 0
        and invalid_duration_count == 0
        and testcase_counts["incomplete"] == 0
        and testcase_counts["repeated_ambiguous"] == 0
        and has_meaningful_pytest_evidence
    ):
        completeness = "complete"
    else:
        completeness = "partial"

    slowest = sorted(
        durations, key=lambda item: float(item["duration_seconds"]), reverse=True
    )[:MAX_SLOW_ITEMS]
    return {
        "source_type": "pytest_reportlog",
        "path": str(path),
        "status": "loaded",
        "sha256": None if truncated else hasher.hexdigest(),
        "report_completeness": completeness,
        "truncated": truncated,
        "line_count": line_count,
        "bytes_read": bytes_read,
        "has_session_start": has_session_start,
        "has_session_finish": has_session_finish,
        "has_collection": has_collection,
        "collection_failed_count": collection_failed_count,
        "collection_errors": collection_errors,
        "exit_status": exit_status,
        "malformed_line_count": malformed_line_count,
        "invalid_duration_count": invalid_duration_count,
        "testcase_counts": testcase_counts,
        "outcome_breakdown": outcome_breakdown,
        "attempt_verdict_counts": attempt_verdict_counts,
        "repeated_phase_record_count": repeated_phase_record_count,
        "repeated_phases": repeated_phases,
        "unsupported_semantics": unsupported_semantics,
        "phase_counts": phase_counts,
        "cumulative_phase_duration_seconds": cumulative_phase_seconds,
        "session_wall_seconds": session_wall_seconds,
        "slowest_phases_scope": "top_25_phases_not_exhaustive",
        "slowest_phases": slowest,
        "properties": prop_values,
        "testcase_properties_summary": {
            "nodes_with_user_properties": len(node_user_props),
            "property_names": sorted(user_prop_names),
            "truncated": testcase_props_truncated,
            "verifies_authority": "raw_reportlog_diagnostic_only_not_c2_delivery",
        },
        "testcase_user_properties": testcase_user_properties,
        "testcase_verifies": testcase_verifies,
        "internal_conflicts": internal_conflicts,
        "consumed_files": [str(path)],
    }


def _norm_sha256_value(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip().removeprefix("sha256:")
    if re.fullmatch(r"[0-9a-f]{64}", cleaned):
        return cleaned
    return None


def _build_bundle_manifest_index(
    report_payload: dict[str, Any],
    *,
    bundle_dir: Path,
    meta: dict[str, Any],
    plan_payload: dict[str, Any] | None,
    internal_conflicts: list[dict[str, Any]],
    consumed_files: list[str],
) -> tuple[dict[str, dict[str, str]], dict[str, Any] | None]:
    """Verify #86 Producer Artifact Handoff via execution.py and index declared report artifacts."""
    index: dict[str, dict[str, str]] = {}
    handoff_summary: dict[str, Any] | None = None

    handoff = report_payload.get("producer_artifact_handoff")
    if handoff is not None:
        try:
            verified = execution.verify_producer_handoff_bundle(
                bundle_dir,
                handoff,
                plan=plan_payload,
                report=report_payload,
                expected_run_ref=meta.get("run_ref"),
                expected_attempt_ref=meta.get("attempt_ref"),
                expected_execution_ref=meta.get("execution_ref"),
                allow_failed_producers=True,
            )
            delivery_status = verified.get("delivery_status")
            handoff_status = (
                delivery_status
                if delivery_status is not None
                else (
                    "verified"
                    if verified["plan_envelope_verified"]
                    else "unverified_missing_plan"
                )
            )
            if not verified["plan_envelope_verified"]:
                internal_conflicts.append(
                    {"reason": "bundle_producer_handoff_plan_missing"}
                )
            for art in verified["verified_artifacts"]:
                bpath = art["bundle_path"]
                p_obj = _safe_child_path(bundle_dir, bpath)
                if p_obj is not None and p_obj.is_file():
                    consumed_files.append(str(p_obj))
                p_root_rel = execution.retained_producer_root(
                    Path("."), art["producer_ref"]
                ).as_posix()
                for f_rel, f_sha in art["file_digests"].items():
                    rel_posix = f"{p_root_rel}/{f_rel}"
                    norm_sha = _norm_sha256_value(f_sha)
                    if norm_sha:
                        index[rel_posix] = {"sha256": norm_sha}
                    f_path = _safe_child_path(bundle_dir, rel_posix)
                    if f_path is not None:
                        consumed_files.append(str(f_path))
            handoff_summary = {
                "status": handoff_status,
                "handoff_id": verified["handoff_id"],
                "plan_envelope_verified": verified["plan_envelope_verified"],
                "admission_authority_63": verified["admission_authority_63"],
                "delivery_status": delivery_status,
                "retention_status": verified.get("retention_status"),
                "prerequisite_statuses": verified.get("prerequisite_statuses", []),
                "closure_statuses": verified.get("closure_statuses", []),
                "consumer_delivery": verified.get("consumer_delivery"),
                "non_produced_edges": verified.get("non_produced_edges", []),
                "artifacts": verified["verified_artifacts"],
            }
        except execution.ExecutionError as exc:
            err_code = str(exc)
            handoff_summary = {
                "status": "failed",
                "error": err_code,
                "admission_authority_63": "not_evaluated",
            }
            internal_conflicts.append(
                {"reason": "bundle_producer_handoff_failed", "error": err_code}
            )

    manifest = report_payload.get("artifacts_manifest")
    if isinstance(manifest, list):
        for rec in manifest:
            if not isinstance(rec, dict):
                continue
            rpath = rec.get("path")
            sha = _norm_sha256_value(rec.get("sha256"))
            if isinstance(rpath, str) and sha:
                index[Path(rpath).as_posix()] = {"sha256": sha}

    embedded_junit = report_payload.get("junit")
    if isinstance(embedded_junit, str) and embedded_junit:
        junit_sha = hashlib.sha256(embedded_junit.encode("utf-8")).hexdigest()
        for rel_junit in ("tests/junit.xml", "junit.xml"):
            index.setdefault(rel_junit, {"sha256": junit_sha})

    return index, handoff_summary


def _is_native_pytest_v1_shape(payload: dict[str, Any]) -> bool:
    """Return True when payload matches Dagger python_test_artifacts schema v1 JSON."""
    return pytest_contracts.is_native_pytest_v1_shape(payload)


def _validate_native_pytest_v1(payload: dict[str, Any]) -> str | None:
    """Validate native Dagger pytest schema v1 contract per docs/architecture/testing/pytest-reporting.md."""
    return pytest_contracts.validate_native_pytest_v1(payload)


def _validate_execution_report_contract(
    payload: dict[str, Any], *, is_single_check_artifact: bool = False
) -> dict[str, Any]:
    """Validate Native Execution Report, single check-result, or native pytest v1 contracts."""
    if not isinstance(payload, dict):
        return {
            "report_format": "unknown",
            "status": "invalid",
            "error": "execution_report_invalid",
            "rejections": {},
            "native_result_count": 0,
        }
    if not is_single_check_artifact and _is_native_pytest_v1_shape(payload):
        err = _validate_native_pytest_v1(payload)
        return {
            "report_format": "native_pytest_v1",
            "status": "valid" if err is None else "invalid",
            "error": err,
            "rejections": {},
            "native_result_count": 0,
        }

    report_format = "eci_execution_report"
    target: dict[str, Any] = payload
    if is_single_check_artifact and "kind" not in payload and "checks" not in payload:
        report_format = "native_check_result"
        has_check_shape = isinstance(payload.get("name"), str) and bool(
            payload.get("name")
        )
        has_contract_or_result = (
            isinstance(payload.get("check_contract"), dict)
            or payload.get("native_results") is not None
            or payload.get("native_result") is not None
        )
        if not (has_check_shape and has_contract_or_result):
            return {
                "report_format": "unknown",
                "status": "invalid",
                "error": "execution_report_invalid",
                "rejections": {},
                "native_result_count": 0,
            }
        target = {
            "kind": execution.REPORT_KIND,
            "schema_version": execution.SCHEMA_VERSION,
            "check_contract": payload.get("check_contract"),
            "checks": [payload],
        }
    else:
        if payload.get("kind") != execution.REPORT_KIND:
            return {
                "report_format": "unknown",
                "status": "invalid",
                "error": "execution_report_invalid",
                "rejections": {},
                "native_result_count": 0,
            }
        if payload.get("schema_version") != execution.SCHEMA_VERSION:
            return {
                "report_format": report_format,
                "status": "invalid",
                "error": "execution_report_schema_unsupported",
                "rejections": {},
                "native_result_count": 0,
            }
    try:
        results, rejections, _ = reconciliation.verify_execution_report(target)
    except reconciliation.ReconciliationError as exc:
        err = (
            f"{exc.code}:{exc.native_code}"
            if getattr(exc, "native_code", None)
            else exc.code
        )
        return {
            "report_format": report_format,
            "status": "invalid",
            "error": err,
            "rejections": {},
            "native_result_count": 0,
        }
    return {
        "report_format": report_format,
        "status": "valid",
        "error": None,
        "rejections": dict(rejections),
        "native_result_count": len(results),
    }


def _collect_declared_plan_refs(
    payload: dict[str, Any], *, internal_conflicts: list[dict[str, Any]]
) -> str | None:
    """Collect declared plan_ref values from report/handoff/identity and flag internal conflicts."""
    candidates: list[tuple[str, str]] = []
    for label, raw in (
        ("report.plan_ref", payload.get("plan_ref")),
        (
            "execution_identity.plan_ref",
            (payload.get("execution_identity") or {}).get("plan_ref")
            if isinstance(payload.get("execution_identity"), dict)
            else None,
        ),
        (
            "source_handoff.plan_ref",
            (payload.get("source_handoff") or {}).get("plan_ref")
            if isinstance(payload.get("source_handoff"), dict)
            else None,
        ),
        (
            "producer_artifact_handoff.plan_ref",
            (payload.get("producer_artifact_handoff") or {}).get("plan_ref")
            if isinstance(payload.get("producer_artifact_handoff"), dict)
            else None,
        ),
    ):
        norm = _norm_optional_str(raw)
        if norm is not None:
            candidates.append((label, norm))
    unique_vals = sorted({val for _, val in candidates})
    if len(unique_vals) > 1:
        internal_conflicts.append(
            {
                "reason": "bundle_declared_plan_ref_conflict",
                "values": unique_vals,
                "sources": {label: val for label, val in candidates},
            }
        )
        return unique_vals[0]
    return unique_vals[0] if unique_vals else None


def _verify_plan_report_correspondence(
    plan_payload: dict[str, Any],
    report_payload: dict[str, Any],
    *,
    internal_conflicts: list[dict[str, Any]],
) -> str:
    """Verify validated bundle plan.json matches report projection fields (full or host subset)."""
    conflict_before = len(internal_conflicts)

    plan_git = plan_payload.get("git")
    rep_plan = report_payload.get("plan")
    rep_plan_dict = rep_plan if isinstance(rep_plan, dict) else {}
    rep_git = rep_plan_dict.get("git")
    if isinstance(plan_git, dict) and isinstance(rep_git, dict) and plan_git != rep_git:
        internal_conflicts.append({"reason": "bundle_plan_git_mismatch"})
    handoff = report_payload.get("source_handoff")
    orig_git = handoff.get("original_git") if isinstance(handoff, dict) else None
    if (
        isinstance(plan_git, dict)
        and isinstance(orig_git, dict)
        and orig_git != plan_git
    ):
        internal_conflicts.append({"reason": "bundle_plan_git_mismatch"})

    plan_gen = plan_payload.get("generator")
    rep_gen = rep_plan_dict.get("generator")
    if plan_gen is not None and rep_gen is not None and plan_gen != rep_gen:
        internal_conflicts.append({"reason": "bundle_plan_generator_mismatch"})

    plan_sel = (
        plan_payload.get("selection")
        if isinstance(plan_payload.get("selection"), dict)
        else {}
    )
    plan_tests = plan_sel.get("tests")
    has_fw = isinstance(plan_sel.get("framework"), str)
    expected_fw = (
        plan_sel.get("framework") if has_fw else ("unittest" if plan_sel else None)
    )
    if expected_fw is not None and "framework" in report_payload:
        rep_fw = report_payload.get("framework")
        if rep_fw != expected_fw:
            internal_conflicts.append(
                {
                    "reason": "bundle_plan_framework_mismatch",
                    "expected": expected_fw,
                    "observed": rep_fw,
                }
            )

    sel_checks = [c for c in (plan_sel.get("checks") or ()) if isinstance(c, str)]
    scope_items = plan_payload.get("check_scope") or plan_sel.get("check_scope") or []
    selected_scope_names = [
        item.get("name")
        for item in scope_items
        if isinstance(item, dict)
        and item.get("status") == "selected"
        and isinstance(item.get("name"), str)
    ]
    allowed_scope_names = {
        item.get("name")
        for item in scope_items
        if isinstance(item, dict)
        and item.get("status") in {"selected", "unknown"}
        and isinstance(item.get("name"), str)
    }
    expected_obj = (
        plan_payload.get("expected_checks")
        if isinstance(plan_payload.get("expected_checks"), dict)
        else None
    )
    expected_entries = (
        [item for item in (expected_obj.get("checks") or ()) if isinstance(item, dict)]
        if expected_obj is not None
        else []
    )
    expected_names = {
        item.get("name")
        for item in expected_entries
        if isinstance(item.get("name"), str)
    }
    plan_declares_checks = bool(
        "checks" in plan_sel or scope_items or expected_obj is not None
    )
    allowed_checks = set(sel_checks) | allowed_scope_names | expected_names
    if plan_tests and expected_fw:
        allowed_checks.add(expected_fw)

    plan_prereqs = (
        list(plan_sel.get("prerequisites") or ())
        if isinstance(plan_sel.get("prerequisites"), list)
        else (
            list((plan_payload.get("prerequisite_actions") or {}).keys())
            if isinstance(plan_payload.get("prerequisite_actions"), dict)
            else []
        )
    )
    plan_edges = (
        list((plan_payload.get("dependency_contract") or {}).get("edges") or ())
        if isinstance(plan_payload.get("dependency_contract"), dict)
        else []
    )

    rep_checks = report_payload.get("checks")
    rep_check_names = (
        [
            item.get("name")
            for item in rep_checks
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        ]
        if isinstance(rep_checks, list)
        else []
    )
    required_non_fw = {
        name
        for name in (set(sel_checks) | set(selected_scope_names))
        if not (plan_tests and name == expected_fw)
    }
    is_host_subset = (
        report_payload.get("execution_context") == "host"
        and not plan_prereqs
        and not plan_edges
        and bool(rep_check_names)
        and set(rep_check_names).issubset(allowed_checks)
        and (
            set(rep_check_names) < required_non_fw
            or (
                bool(plan_tests)
                and expected_fw is not None
                and expected_fw not in rep_check_names
            )
        )
    )

    if isinstance(plan_tests, list) and "targets" in report_payload:
        rep_targets = report_payload.get("targets")
        targets_ok_as_non_test_subset = (
            is_host_subset and expected_fw not in rep_check_names and rep_targets == []
        )
        if not targets_ok_as_non_test_subset and (
            not isinstance(rep_targets, list) or list(rep_targets) != list(plan_tests)
        ):
            internal_conflicts.append(
                {
                    "reason": "bundle_plan_targets_mismatch",
                    "expected": list(plan_tests),
                    "observed": rep_targets,
                }
            )

    if plan_declares_checks and isinstance(rep_checks, list):
        if rep_check_names:
            unexpected = sorted(
                {name for name in rep_check_names if name not in allowed_checks}
            )
            if unexpected:
                internal_conflicts.append(
                    {
                        "reason": "bundle_plan_unexpected_check",
                        "unexpected": unexpected,
                        "allowed": sorted(allowed_checks),
                    }
                )
        if (
            report_payload.get("status") not in ("not_executed", "rejected")
            and not is_host_subset
            and required_non_fw
        ):
            missing = sorted(required_non_fw - set(rep_check_names))
            if missing:
                internal_conflicts.append(
                    {
                        "reason": "bundle_plan_selected_checks_mismatch",
                        "missing": missing,
                        "observed": sorted(set(rep_check_names)),
                    }
                )
        if expected_entries and rep_checks:
            expected_by_name = {
                item["name"]: item
                for item in expected_entries
                if isinstance(item.get("name"), str)
            }
            for item in rep_checks:
                if not isinstance(item, dict):
                    continue
                cname = item.get("name")
                exp_item = expected_by_name.get(cname)
                if (
                    exp_item is not None
                    and exp_item.get("definition_status") == "resolved"
                ):
                    rep_cid = item.get("check_id")
                    exp_cid = exp_item.get("check_id")
                    if (
                        isinstance(rep_cid, str)
                        and isinstance(exp_cid, str)
                        and rep_cid != exp_cid
                    ):
                        internal_conflicts.append(
                            {
                                "reason": "bundle_plan_expected_check_id_mismatch",
                                "check": cname,
                                "expected": exp_cid,
                                "observed": rep_cid,
                            }
                        )

    if (
        expected_obj is not None
        and isinstance(report_payload.get("expected_set_ref"), str)
        and report_payload.get("expected_set_ref")
        != expected_obj.get("expected_set_ref")
    ):
        internal_conflicts.append(
            {
                "reason": "bundle_plan_expected_checks_mismatch",
                "expected": expected_obj.get("expected_set_ref"),
                "observed": report_payload.get("expected_set_ref"),
            }
        )

    rep_prereqs = report_payload.get("prerequisites")
    if isinstance(rep_prereqs, list) and rep_prereqs:
        rep_pnames = {
            item.get("name")
            for item in rep_prereqs
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        extra_p = sorted(rep_pnames - set(plan_prereqs))
        if extra_p:
            internal_conflicts.append(
                {
                    "reason": "bundle_plan_prerequisites_mismatch",
                    "unexpected": extra_p,
                    "expected": list(plan_prereqs),
                }
            )

    if (
        "dependency_contract" in plan_payload
        and "dependency_contract" in report_payload
        and plan_payload.get("dependency_contract")
        != report_payload.get("dependency_contract")
    ):
        internal_conflicts.append(
            {"reason": "bundle_plan_dependency_contract_mismatch"}
        )

    if (
        plan_payload.get("plan_execution_tree") is not None
        and report_payload.get("plan_execution_tree") is not None
        and plan_payload.get("plan_execution_tree")
        != report_payload.get("plan_execution_tree")
    ):
        internal_conflicts.append({"reason": "bundle_plan_execution_tree_mismatch"})

    plan_tree_id = _norm_optional_str(
        plan_git.get("source_tree_id") if isinstance(plan_git, dict) else None
    ) or _norm_optional_str(
        (plan_payload.get("plan_execution_tree") or {}).get("source_tree_id")
        if isinstance(plan_payload.get("plan_execution_tree"), dict)
        else None
    )
    rep_tree_id = _norm_optional_str(
        rep_git.get("source_tree_id") if isinstance(rep_git, dict) else None
    ) or _norm_optional_str(report_payload.get("source_tree_id"))
    if (
        plan_tree_id is not None
        and rep_tree_id is not None
        and plan_tree_id != rep_tree_id
    ):
        internal_conflicts.append(
            {
                "reason": "bundle_plan_source_tree_id_mismatch",
                "expected": plan_tree_id,
                "observed": rep_tree_id,
            }
        )
    if len(internal_conflicts) > conflict_before:
        return "mismatch"
    return "subset_of_plan" if is_host_subset else "full_plan"


def _extract_execution_metadata(
    payload: dict[str, Any],
    *,
    internal_conflicts: list[dict[str, Any]],
    is_single_check_artifact: bool = False,
) -> dict[str, Any]:
    contract = _validate_execution_report_contract(
        payload, is_single_check_artifact=is_single_check_artifact
    )
    report_format = contract["report_format"]
    contract_valid = contract["status"] == "valid"
    if not contract_valid:
        internal_conflicts.append(
            {
                "reason": "execution_report_contract_invalid",
                "error": contract["error"],
            }
        )

    if report_format == "native_pytest_v1":
        exit_code = payload.get("exit_code")
        native_exec_status = (
            ("passed" if exit_code == 0 else "failed")
            if type(exit_code) is int
            else None
        )
        rep_avail = (
            payload.get("report_available")
            if type(payload.get("report_available")) is bool
            else None
        )
        out_pytest: dict[str, Any] = {
            "report_format": "native_pytest_v1",
            "report_contract_status": contract["status"],
            "report_contract_rejections": {},
            "native_result_count": 0,
            "native_execution_status": native_exec_status,
            "report_available": rep_avail,
            "report_delivery_status": (
                ("available" if rep_avail else "unavailable")
                if rep_avail is not None
                else None
            ),
            "pytest_reason": _norm_optional_str(payload.get("reason")),
            "pytest_counts": payload.get("counts"),
            "pytest_diagnostic": payload.get("diagnostic"),
            "selected_targets": payload.get("selected_targets"),
            "declared_identity": {
                "run_ref": None,
                "attempt_ref": None,
                "execution_ref": None,
                "report_ref": None,
                "plan_ref": None,
                "git_commit": None,
                "source_tree_id": None,
            },
            "declared_report_plan_ref": None,
            "verifiable_execution_proven": False,
            "admission_authority_63": "not_evaluated",
            "run_ref": None,
            "attempt_ref": None,
            "execution_ref": None,
            "report_ref": None,
            "plan_ref": None,
            "git_commit": None,
            "source_tree_id": None,
        }
        if contract["error"] is not None:
            out_pytest["report_contract_error"] = contract["error"]
        return out_pytest

    ident = payload.get("execution_identity")
    handoff = payload.get("source_handoff")
    run_ref = (
        _norm_optional_str(ident.get("run_ref")) if isinstance(ident, dict) else None
    )
    attempt_ref = (
        _norm_optional_str(ident.get("attempt_ref"))
        if isinstance(ident, dict)
        else None
    )
    execution_ref = (
        _norm_optional_str(ident.get("execution_ref"))
        if isinstance(ident, dict)
        else None
    )
    if isinstance(handoff, dict):
        h_exec = _norm_optional_str(handoff.get("parent_execution_ref"))
        h_run = _norm_optional_str(handoff.get("run_ref"))
        h_att = _norm_optional_str(handoff.get("attempt_ref"))
        if h_exec is not None and execution_ref is not None and h_exec != execution_ref:
            internal_conflicts.append(
                {
                    "reason": "execution_handoff_execution_ref_mismatch",
                    "expected": execution_ref,
                    "observed": h_exec,
                }
            )
        if h_run is not None and run_ref is not None and h_run != run_ref:
            internal_conflicts.append(
                {
                    "reason": "execution_handoff_run_ref_mismatch",
                    "expected": run_ref,
                    "observed": h_run,
                }
            )
        if h_att is not None and attempt_ref is not None and h_att != attempt_ref:
            internal_conflicts.append(
                {
                    "reason": "execution_handoff_attempt_ref_mismatch",
                    "expected": attempt_ref,
                    "observed": h_att,
                }
            )
    plan_git = (payload.get("plan") or {}).get("git")
    git_commit = (
        _norm_optional_str(plan_git.get("head_commit"))
        if isinstance(plan_git, dict)
        else None
    )
    source_tree_id = (
        _norm_optional_str(plan_git.get("source_tree_id"))
        if isinstance(plan_git, dict)
        else None
    ) or _norm_optional_str(payload.get("source_tree_id"))
    declared_plan_ref = _collect_declared_plan_refs(
        payload, internal_conflicts=internal_conflicts
    )
    report_ref = _compute_execution_report_ref_key(payload)
    declared_identity = {
        "run_ref": run_ref,
        "attempt_ref": attempt_ref,
        "execution_ref": execution_ref,
        "report_ref": report_ref,
        "plan_ref": declared_plan_ref,
        "git_commit": git_commit,
        "source_tree_id": source_tree_id,
    }

    out: dict[str, Any] = {
        "report_format": report_format,
        "report_contract_status": contract["status"],
        "report_contract_rejections": contract["rejections"],
        "native_result_count": contract["native_result_count"],
        "declared_identity": declared_identity,
        "declared_report_plan_ref": declared_plan_ref,
        "verifiable_execution_proven": contract_valid
        and report_format in ("eci_execution_report", "native_check_result"),
        "admission_authority_63": "not_evaluated",
        "run_ref": run_ref if contract_valid else None,
        "attempt_ref": attempt_ref if contract_valid else None,
        "execution_ref": execution_ref if contract_valid else None,
        "report_ref": report_ref if contract_valid else None,
        "plan_ref": declared_plan_ref if contract_valid else None,
        "git_commit": git_commit,
        "source_tree_id": source_tree_id,
    }
    if contract["error"] is not None:
        out["report_contract_error"] = contract["error"]
    return out


def _load_pytest_invocation_entry(path: Path) -> list[dict[str, Any]]:
    """Read-only loader for an explicit #168 pytest invocation directory (no latest scan)."""
    if path.is_symlink():
        return [
            {
                "source_type": "pytest_invocation",
                "path": str(path),
                "status": "symlink_rejected",
                "internal_conflicts": [],
                "consumed_files": [],
            }
        ]
    if not path.exists():
        return [
            {
                "source_type": "pytest_invocation",
                "path": str(path),
                "status": "missing",
                "internal_conflicts": [],
                "consumed_files": [],
            }
        ]
    if not path.is_dir():
        return [
            {
                "source_type": "pytest_invocation",
                "path": str(path),
                "status": "invalid",
                "error": "expected_pytest_invocation_directory",
                "internal_conflicts": [],
                "consumed_files": [],
            }
        ]

    inv_dir = path.resolve()
    consumed_dirs = [str(inv_dir)]
    consumed_files: list[str] = []
    internal_conflicts: list[dict[str, Any]] = []
    meta_records: dict[str, dict[str, Any]] = {}

    for fname in ("metadata.json", "summary.json"):
        candidate = _safe_child_path(inv_dir, fname)
        if candidate is None or not (candidate.exists() or candidate.is_symlink()):
            continue
        consumed_files.append(str(candidate))
        loaded = _load_bounded_json(candidate)
        if loaded.get("status") != "loaded":
            internal_conflicts.append(
                {
                    "reason": "pytest_invocation_metadata_invalid",
                    "artifact": fname,
                    "detail": loaded.get("status"),
                }
            )
            continue
        payload = loaded["payload"]
        if not pytest_contracts.is_pytest_invocation_metadata(payload):
            internal_conflicts.append(
                {
                    "reason": "pytest_invocation_metadata_contract_invalid",
                    "artifact": fname,
                }
            )
            continue
        meta_records[fname] = payload

    if not meta_records:
        return [
            {
                "source_type": "pytest_invocation",
                "path": str(inv_dir),
                "status": (
                    "invalid" if internal_conflicts else "missing_invocation_metadata"
                ),
                "note": "directory_scan_for_latest_forbidden_pass_explicit_invocation_dir",
                "internal_conflicts": internal_conflicts,
                "consumed_files": list(dict.fromkeys(consumed_files)),
                "consumed_dirs": consumed_dirs,
            }
        ]

    if "metadata.json" in meta_records and "summary.json" in meta_records:
        m_doc = meta_records["metadata.json"]
        s_doc = meta_records["summary.json"]
        for field in ("invocation_id", "pytest_version"):
            mv = m_doc.get(field)
            sv = s_doc.get(field)
            if mv is not None and sv is not None and mv != sv:
                internal_conflicts.append(
                    {
                        "reason": f"pytest_invocation_{field}_conflict",
                        "metadata": mv,
                        "summary": sv,
                    }
                )
        m_exit = (
            m_doc.get("exitstatus") if "exitstatus" in m_doc else m_doc.get("exit_code")
        )
        s_exit = (
            s_doc.get("exitstatus") if "exitstatus" in s_doc else s_doc.get("exit_code")
        )
        if m_exit is not None and s_exit is not None and m_exit != s_exit:
            internal_conflicts.append(
                {
                    "reason": "pytest_invocation_exitstatus_conflict",
                    "metadata": m_exit,
                    "summary": s_exit,
                }
            )

    primary = meta_records.get("summary.json") or meta_records["metadata.json"]
    secondary = meta_records.get("metadata.json") or {}
    inv_id = primary["invocation_id"]
    exitstatus = (
        primary.get("exitstatus")
        if "exitstatus" in primary
        else (
            primary.get("exit_code")
            if "exit_code" in primary
            else (
                secondary.get("exitstatus")
                if "exitstatus" in secondary
                else secondary.get("exit_code")
            )
        )
    )
    wall_time = (
        primary.get("wall_time_seconds")
        if primary.get("wall_time_seconds") is not None
        else (
            primary.get("elapsed_seconds")
            if primary.get("elapsed_seconds") is not None
            else secondary.get("wall_time_seconds")
        )
    )
    git_commit = _norm_optional_str(
        primary.get("git_commit")
        or primary.get("sha")
        or secondary.get("git_commit")
        or secondary.get("sha")
    )

    child_entries: list[dict[str, Any]] = []
    for rel_name, parser in (
        ("report.jsonl", _parse_pytest_reportlog),
        ("pytest-reportlog.jsonl", _parse_pytest_reportlog),
        ("junit.xml", _parse_junit_xml),
    ):
        child = _safe_child_path(inv_dir, rel_name)
        if child is None or not (child.exists() or child.is_symlink()):
            continue
        parsed = parser(child)
        consumed_files.append(str(child))
        parsed["bundle_binding"] = "colocated_unverified"
        parsed["colocated_directory"] = inv_dir.as_posix()
        parsed["bundle_relative_path"] = rel_name
        parsed["pytest_invocation_id"] = inv_id
        if (
            parsed.get("source_type") == "pytest_reportlog"
            and parsed.get("status") == "loaded"
            and type(exitstatus) is int
            and type(parsed.get("exit_status")) is int
            and parsed["exit_status"] != exitstatus
        ):
            internal_conflicts.append(
                {
                    "reason": "pytest_invocation_exit_mismatch",
                    "metadata_exitstatus": exitstatus,
                    "reportlog_exit_status": parsed["exit_status"],
                    "artifact": rel_name,
                }
            )
        child_entries.append(parsed)

    entry: dict[str, Any] = {
        "source_type": "pytest_invocation",
        "path": str(inv_dir),
        "status": "loaded",
        "invocation_id": inv_id,
        "kind": primary.get("kind"),
        "schema_version": primary.get("schema_version"),
        "pytest_version": primary.get("pytest_version")
        or secondary.get("pytest_version"),
        "collect_only": (
            primary["collect_only"]
            if "collect_only" in primary
            else secondary.get("collect_only")
        ),
        "selected_items": (
            primary.get("selected_items")
            if primary.get("selected_items") is not None
            else (
                primary.get("selected_count")
                if primary.get("selected_count") is not None
                else secondary.get("selected_items")
            )
        ),
        "deselected_items": (
            primary.get("deselected_items")
            if primary.get("deselected_items") is not None
            else (
                primary.get("deselected_count")
                if primary.get("deselected_count") is not None
                else secondary.get("deselected_items")
            )
        ),
        "wall_time_seconds": wall_time,
        "elapsed_seconds": wall_time,
        "exitstatus": exitstatus,
        "session_finished": (
            primary["session_finished"]
            if "session_finished" in primary
            else secondary.get("session_finished")
        ),
        "argv": (
            primary.get("argv")[:MAX_SLOW_ITEMS]
            if isinstance(primary.get("argv"), list)
            else (
                secondary.get("argv")[:MAX_SLOW_ITEMS]
                if isinstance(secondary.get("argv"), list)
                else None
            )
        ),
        "git_commit": git_commit,
        "internal_conflicts": internal_conflicts,
        "consumed_files": list(dict.fromkeys(consumed_files)),
        "consumed_dirs": consumed_dirs,
    }
    return [entry, *child_entries]


def _load_execution_entries(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink():
        return [
            {
                "source_type": "execution_report",
                "path": str(path),
                "status": "symlink_rejected",
                "internal_conflicts": [],
                "consumed_files": [],
            }
        ]
    if path.is_dir():
        bundle_dir = path.resolve()
        consumed_dirs = [str(bundle_dir)]
        consumed_files: list[str] = []
        internal_conflicts: list[dict[str, Any]] = []
        entries: list[dict[str, Any]] = []

        plan_payload: dict[str, Any] | None = None
        plan_ref: str | None = None
        plan_contract_status = "missing"
        plan_contract_error: str | None = None
        plan_candidate = bundle_dir / "plan.json"
        if plan_candidate.exists():
            consumed_files.append(str(plan_candidate))
            loaded_plan = _load_bounded_json(plan_candidate)
            if loaded_plan.get("status") == "loaded":
                raw_plan = loaded_plan["payload"]
                plan_ref = hooks._digest_bytes(hooks.canonical_event_bytes(raw_plan))
                try:
                    plan_contract.validate(raw_plan)
                except plan_contract.PlanError as exc:
                    plan_contract_status = "invalid"
                    plan_contract_error = exc.code
                    internal_conflicts.append(
                        {"reason": "bundle_plan_invalid", "detail": exc.code}
                    )
                else:
                    plan_contract_status = "valid"
                    plan_payload = raw_plan
            else:
                plan_contract_status = "invalid"
                plan_contract_error = str(loaded_plan.get("status"))
                internal_conflicts.append(
                    {
                        "reason": "bundle_plan_invalid",
                        "detail": loaded_plan.get("status"),
                    }
                )

        report_candidate = bundle_dir / "report.json"
        if not report_candidate.exists():
            alt_candidate = bundle_dir / "check-result.json"
            if alt_candidate.exists():
                report_candidate = alt_candidate

        report_payload: dict[str, Any] | None = None
        meta: dict[str, Any] = {}
        manifest_index: dict[str, dict[str, str]] = {}
        sub_reports: dict[str, Any] = {}
        if report_candidate.exists():
            consumed_files.append(str(report_candidate))
            loaded_rep = _load_bounded_json(report_candidate)
            if loaded_rep.get("status") != "loaded":
                return [
                    {
                        "source_type": "execution_bundle",
                        "path": str(report_candidate),
                        "bundle_directory": bundle_dir.as_posix(),
                        "status": loaded_rep["status"],
                        "internal_conflicts": internal_conflicts,
                        "consumed_files": consumed_files,
                        "consumed_dirs": consumed_dirs,
                    }
                ]
            report_payload = loaded_rep["payload"]
            meta = _extract_execution_metadata(
                report_payload,
                internal_conflicts=internal_conflicts,
                is_single_check_artifact=(report_candidate.name == "check-result.json"),
            )
            contract_valid = meta.get("report_contract_status") == "valid"
            declared_plan_ref = meta.get("declared_report_plan_ref")
            if (
                plan_ref is not None
                and declared_plan_ref is not None
                and plan_ref != declared_plan_ref
            ):
                internal_conflicts.append(
                    {
                        "reason": "bundle_plan_ref_mismatch",
                        "expected": declared_plan_ref,
                        "observed": plan_ref,
                    }
                )

            plan_projection_status = "not_evaluated"
            plan_execution_scope: str | None = None
            has_plan_ref_conflict = any(
                c.get("reason")
                in ("bundle_plan_ref_mismatch", "bundle_declared_plan_ref_conflict")
                for c in internal_conflicts
            )
            if plan_contract_status == "valid" and isinstance(plan_payload, dict):
                scope_res = _verify_plan_report_correspondence(
                    plan_payload,
                    report_payload,
                    internal_conflicts=internal_conflicts,
                )
                plan_git = plan_payload.get("git")
                if isinstance(plan_git, dict) and meta.get("git_commit") is None:
                    meta["git_commit"] = _norm_optional_str(plan_git.get("head_commit"))
                if isinstance(plan_git, dict) and meta.get("source_tree_id") is None:
                    meta["source_tree_id"] = _norm_optional_str(
                        plan_git.get("source_tree_id")
                    )
                if scope_res == "mismatch":
                    plan_projection_status = "mismatch"
                    plan_binding_status = "mismatch"
                elif has_plan_ref_conflict:
                    plan_projection_status = (
                        "subset_compatible"
                        if scope_res == "subset_of_plan"
                        else "matched"
                    )
                    plan_execution_scope = scope_res
                    plan_binding_status = "mismatch"
                elif declared_plan_ref is not None and plan_ref == declared_plan_ref:
                    plan_projection_status = (
                        "subset_compatible"
                        if scope_res == "subset_of_plan"
                        else "matched"
                    )
                    plan_execution_scope = scope_res
                    plan_binding_status = "verified"
                    if contract_valid and meta.get("plan_ref") is None:
                        meta["plan_ref"] = plan_ref
                else:
                    plan_projection_status = (
                        "subset_compatible"
                        if scope_res == "subset_of_plan"
                        else "matched"
                    )
                    plan_execution_scope = scope_res
                    plan_binding_status = "unverified_plan_ref_undeclared"
            elif plan_contract_status == "invalid":
                plan_binding_status = "invalid_plan"
            elif any(
                c.get("reason") == "bundle_declared_plan_ref_conflict"
                for c in internal_conflicts
            ):
                plan_binding_status = "mismatch"
            elif (
                meta.get("report_format") == "native_pytest_v1"
                and declared_plan_ref is None
            ):
                plan_binding_status = "not_applicable"
                plan_projection_status = "not_applicable"
            else:
                plan_binding_status = "unknown_missing_plan"
            meta["observed_plan_file_ref"] = plan_ref
            meta["plan_contract_status"] = plan_contract_status
            if plan_contract_error is not None:
                meta["plan_contract_error"] = plan_contract_error
            meta["plan_projection_status"] = plan_projection_status
            if plan_execution_scope is not None:
                meta["plan_execution_scope"] = plan_execution_scope
            meta["plan_binding_status"] = plan_binding_status

            exit_file = bundle_dir / "exit-code.txt"
            if exit_file.exists():
                consumed_files.append(str(exit_file))
                raw_exit, exit_err, _ = _read_bounded_bytes(exit_file, limit=64)
                if exit_err is not None or raw_exit is None:
                    internal_conflicts.append({"reason": "bundle_exit_code_unreadable"})
                else:
                    try:
                        exit_num = int(raw_exit.decode("utf-8").strip())
                    except ValueError:
                        internal_conflicts.append(
                            {"reason": "bundle_exit_code_invalid"}
                        )
                    else:
                        if exit_num != report_payload.get("exit_code"):
                            internal_conflicts.append(
                                {
                                    "reason": "bundle_exit_code_mismatch",
                                    "expected": report_payload.get("exit_code"),
                                    "observed": exit_num,
                                }
                            )

            host_report = _safe_child_path(bundle_dir, "host/report.json")
            if host_report is not None and host_report.is_file():
                consumed_files.append(str(host_report))
                expected_host_sha = (report_payload.get("source_handoff") or {}).get(
                    "host_report_sha256"
                )
                observed_host_sha = _stream_sha256(host_report)
                if (
                    isinstance(expected_host_sha, str)
                    and observed_host_sha != expected_host_sha
                ):
                    internal_conflicts.append(
                        {
                            "reason": "bundle_host_report_sha256_mismatch",
                            "expected": expected_host_sha,
                            "observed": observed_host_sha,
                        }
                    )

            manifest_index, handoff_summary = _build_bundle_manifest_index(
                report_payload,
                bundle_dir=bundle_dir,
                meta=meta,
                plan_payload=plan_payload,
                internal_conflicts=internal_conflicts,
                consumed_files=consumed_files,
            )

            # Inspect declared #86 sub-nodes under tests/ and checks/<name>/.
            for sub_rel in ("tests/report.json", "tests/check-result.json"):
                sub_file = _safe_child_path(bundle_dir, sub_rel)
                if sub_file is not None and sub_file.is_file():
                    consumed_files.append(str(sub_file))
                    loaded_sub = _load_bounded_json(sub_file)
                    if loaded_sub.get("status") == "loaded":
                        sub_p = loaded_sub["payload"]
                        sub_reports[sub_rel] = {
                            "status": sub_p.get("status"),
                            "exit_code": sub_p.get("exit_code"),
                        }
            checks_dir = _safe_child_path(bundle_dir, "checks")
            if checks_dir is not None and checks_dir.is_dir():
                for child_dir in sorted(checks_dir.iterdir()):
                    if child_dir.is_symlink() or not child_dir.is_dir():
                        continue
                    for cname in ("check-result.json", "report.json"):
                        cfile = _safe_child_path(child_dir, cname)
                        if cfile is not None and cfile.is_file():
                            consumed_files.append(str(cfile))
                            loaded_sub = _load_bounded_json(cfile)
                            if loaded_sub.get("status") == "loaded":
                                sub_p = loaded_sub["payload"]
                                sub_reports[f"checks/{child_dir.name}/{cname}"] = {
                                    "status": sub_p.get("status"),
                                    "exit_code": sub_p.get("exit_code"),
                                }
                            break

            entries.append(
                {
                    "source_type": "execution_bundle",
                    "path": str(report_candidate),
                    "bundle_directory": (
                        bundle_dir.as_posix()
                        if meta.get("report_contract_status") == "valid"
                        and meta.get("verifiable_execution_proven")
                        else None
                    ),
                    "colocated_directory": (
                        None
                        if meta.get("report_contract_status") == "valid"
                        and meta.get("verifiable_execution_proven")
                        else bundle_dir.as_posix()
                    ),
                    "status": "loaded",
                    "execution_status": report_payload.get("status"),
                    "exit_code": report_payload.get("exit_code"),
                    "elapsed_seconds": report_payload.get("duration_seconds")
                    or report_payload.get("elapsed_seconds"),
                    **meta,
                    "producer_artifact_handoff_status": (
                        handoff_summary["status"]
                        if handoff_summary is not None
                        else None
                    ),
                    "producer_artifact_handoff": handoff_summary,
                    "sub_reports": sub_reports,
                    "internal_conflicts": internal_conflicts,
                    "consumed_files": list(dict.fromkeys(consumed_files)),
                    "consumed_dirs": list(consumed_dirs),
                }
            )
        else:
            for meta_name in ("summary.json", "metadata.json"):
                mcand = _safe_child_path(bundle_dir, meta_name)
                if mcand is not None and mcand.is_file():
                    mload = _load_bounded_json(mcand)
                    if mload.get(
                        "status"
                    ) == "loaded" and pytest_contracts.is_pytest_invocation_metadata(
                        mload["payload"]
                    ):
                        return _load_pytest_invocation_entry(bundle_dir)
            entries.append(
                {
                    "source_type": "execution_bundle",
                    "path": str(bundle_dir),
                    "bundle_directory": bundle_dir.as_posix(),
                    "status": "missing_report_json",
                    "internal_conflicts": internal_conflicts,
                    "consumed_files": list(dict.fromkeys(consumed_files)),
                    "consumed_dirs": list(consumed_dirs),
                }
            )

        parent_integrity_ok = (
            bool(entries)
            and entries[0].get("source_type") == "execution_bundle"
            and entries[0].get("status") == "loaded"
            and meta.get("report_contract_status") == "valid"
            and bool(meta.get("verifiable_execution_proven"))
            and not internal_conflicts
            and meta.get("plan_binding_status") not in ("mismatch", "invalid_plan")
        )

        # Support standalone root pytest bundles and #86 PlanExecution tests/ bundles.
        seen_test_shas: dict[str, dict[str, Any]] = {}
        for rel_name, parser in (
            ("junit.xml", _parse_junit_xml),
            ("core-junit.xml", _parse_junit_xml),
            ("pytest-reportlog.jsonl", _parse_pytest_reportlog),
            ("tests/junit.xml", _parse_junit_xml),
            ("tests/core-junit.xml", _parse_junit_xml),
            ("tests/pytest-reportlog.jsonl", _parse_pytest_reportlog),
        ):
            child = _safe_child_path(bundle_dir, rel_name)
            if child is None or not child.is_file():
                continue
            parsed = parser(child)
            sha = parsed.get("sha256")
            manifest_rec = manifest_index.get(rel_name)
            digest_verified = False
            manifest_mismatch = False
            if manifest_rec is not None:
                expected_sha = manifest_rec.get("sha256")
                if isinstance(sha, str) and sha == expected_sha:
                    digest_verified = True
                else:
                    manifest_mismatch = True
                    parsed["internal_conflicts"].append(
                        {
                            "reason": "bundle_artifact_sha256_mismatch",
                            "path": rel_name,
                            "expected": expected_sha,
                            "observed": sha,
                        }
                    )
            if (
                not manifest_mismatch
                and rel_name.endswith("core-junit.xml")
                and isinstance(sha, str)
                and sha in seen_test_shas
                and (
                    manifest_rec is None
                    or seen_test_shas[sha].get("bundle_binding")
                    == "verified_manifest_digest"
                )
            ):
                target_rec = seen_test_shas[sha]
                target_rec.setdefault("alias_paths", []).append(rel_name)
                target_rec.setdefault("alias_manifest_verifications", {})[rel_name] = (
                    digest_verified and parent_integrity_ok
                )
                if entries and str(child) not in entries[0]["consumed_files"]:
                    entries[0]["consumed_files"].append(str(child))
                continue
            if digest_verified and parent_integrity_ok:
                # Only concrete native digest bindings on unconflicted, contract-valid bundles prove membership.
                parsed["bundle_binding"] = "verified_manifest_digest"
                parsed["bound_bundle_directory"] = bundle_dir.as_posix()
                parsed["bound_artifact_digest"] = sha
            else:
                parsed["bundle_binding"] = "colocated_unverified"
                parsed["colocated_directory"] = bundle_dir.as_posix()
                if digest_verified and not parent_integrity_ok:
                    parsed["revoked_manifest_binding_reason"] = (
                        "parent_bundle_integrity_conflict"
                    )
            parsed["bundle_relative_path"] = rel_name
            if isinstance(sha, str):
                seen_test_shas[sha] = parsed
            entries.append(parsed)
        return entries

    loaded = _load_bounded_json(path)
    if loaded.get("status") != "loaded":
        return [
            {
                "source_type": "execution_report",
                "path": str(path),
                "status": loaded["status"],
                "error": loaded.get("error"),
                "internal_conflicts": [],
                "consumed_files": [str(path)] if path.exists() else [],
            }
        ]
    payload = loaded["payload"]
    internal_conflicts = []
    meta = _extract_execution_metadata(
        payload,
        internal_conflicts=internal_conflicts,
        is_single_check_artifact=(path.name == "check-result.json"),
    )
    return [
        {
            "source_type": "execution_report",
            "path": str(path),
            "status": "loaded",
            "execution_status": payload.get("status"),
            "exit_code": payload.get("exit_code"),
            "elapsed_seconds": payload.get("duration_seconds")
            or payload.get("elapsed_seconds"),
            **meta,
            "internal_conflicts": internal_conflicts,
            "consumed_files": [str(path)],
        }
    ]


def _load_dagger_telemetry(path: Path) -> dict[str, Any]:
    loaded = _load_bounded_json(path)
    if loaded.get("status") != "loaded":
        return {
            "source_type": "dagger_telemetry",
            "path": str(path),
            "status": loaded["status"],
            "error": loaded.get("error"),
            "internal_conflicts": [],
            "consumed_files": [str(path)] if path.exists() else [],
        }
    payload = loaded["payload"]
    native_corr = (
        dict(payload["correlation"])
        if isinstance(payload.get("correlation"), dict)
        else payload.get("correlation")
    )
    native_sb = (
        dict(payload["source_binding"])
        if isinstance(payload.get("source_binding"), dict)
        else payload.get("source_binding")
    )
    corr_status = native_corr.get("status") if isinstance(native_corr, dict) else None
    matched_trace_id = (
        _norm_optional_str(native_corr.get("matched_trace_id"))
        if isinstance(native_corr, dict)
        else _norm_optional_str(payload.get("trace_id"))
    )
    matched_session_id = (
        _norm_optional_str(native_corr.get("matched_session_id"))
        if isinstance(native_corr, dict)
        else None
    )
    matched_engine_instance_id = (
        _norm_optional_str(native_corr.get("matched_engine_instance_id"))
        if isinstance(native_corr, dict)
        else None
    )
    anchor_span_id = (
        _norm_optional_str(native_corr.get("anchor_span_id"))
        if isinstance(native_corr, dict)
        else None
    )
    dagql_span_id = (
        _norm_optional_str(native_corr.get("dagql_span_id"))
        if isinstance(native_corr, dict)
        else None
    )
    core_span_id = (
        _norm_optional_str(native_corr.get("core_span_id"))
        if isinstance(native_corr, dict)
        else None
    )
    lineage_span_ids = (
        [
            str(s)
            for s in native_corr.get("lineage_span_ids") or ()
            if _norm_optional_str(s)
        ]
        if isinstance(native_corr, dict)
        else []
    )
    declared_source_ref = (
        _norm_optional_str(native_sb.get("declared_target_source_ref"))
        if isinstance(native_sb, dict)
        else None
    )
    source_binding_status = (
        _norm_optional_str(native_sb.get("source_binding_status"))
        if isinstance(native_sb, dict)
        else None
    )
    origin_exec = payload.get("origin_execution")
    origin_status = (
        _norm_optional_str(origin_exec.get("status"))
        if isinstance(origin_exec, dict)
        else None
    )
    cache_obs = payload.get("cache_observation")
    isolated_capture = (
        cache_obs.get("isolated_capture") if isinstance(cache_obs, dict) else None
    )
    declared_run_ref = _norm_optional_str(payload.get("run_ref"))
    declared_attempt_ref = _norm_optional_str(payload.get("attempt_ref"))
    declared_exec_ref = _norm_optional_str(payload.get("execution_ref"))

    # #87 A.1 bound only links trace <-> invocation; proving ECI execution requires verified #87 B source & origin bindings.
    proven_b_binding = (
        corr_status == "bound"
        and source_binding_status == "verified"
        and origin_status == "verified"
        and isolated_capture is not False
    )
    if proven_b_binding:
        exec_prov_status = "verified"
    elif isolated_capture is False:
        exec_prov_status = "cache_not_isolated"
    elif declared_exec_ref is not None:
        exec_prov_status = "unverified_self_reported_execution_ref_without_b_proof"
    else:
        exec_prov_status = "trace_context_only_no_b_execution_binding"

    return {
        "source_type": "dagger_telemetry",
        "path": str(path),
        "status": "loaded",
        "correlation": native_corr,
        "source_binding": native_sb,
        "cache_observation": cache_obs,
        "origin_execution": origin_exec,
        "observability_gate": payload.get("observability_gate"),
        "matched_trace_id": matched_trace_id,
        "matched_session_id": matched_session_id,
        "matched_engine_instance_id": matched_engine_instance_id,
        "anchor_span_id": anchor_span_id,
        "dagql_span_id": dagql_span_id,
        "core_span_id": core_span_id,
        "lineage_span_ids": lineage_span_ids,
        "native_correlation_status": corr_status,
        "source_binding_status": source_binding_status,
        "origin_execution_status": origin_status,
        "execution_provenance_status": exec_prov_status,
        "declared_target_source_ref": declared_source_ref,
        "declared_identity": {
            "run_ref": declared_run_ref,
            "attempt_ref": declared_attempt_ref,
            "execution_ref": declared_exec_ref,
            "target_source_ref": declared_source_ref,
        },
        "run_ref": declared_run_ref if proven_b_binding else None,
        "attempt_ref": declared_attempt_ref if proven_b_binding else None,
        "execution_ref": declared_exec_ref if proven_b_binding else None,
        "git_commit": _norm_optional_str(
            payload.get("git_commit") or payload.get("sha")
        ),
        "internal_conflicts": [],
        "consumed_files": [str(path)],
    }


def _load_ci_performance(path: Path) -> dict[str, Any]:
    loaded = _load_bounded_json(path)
    if loaded.get("status") != "loaded":
        return {
            "source_type": "ci_performance",
            "path": str(path),
            "status": loaded["status"],
            "error": loaded.get("error"),
            "internal_conflicts": [],
            "consumed_files": [str(path)] if path.exists() else [],
        }
    payload = loaded["payload"]
    internal_conflicts: list[dict[str, Any]] = []
    run_id = payload.get("run_id")
    run_attempt = payload.get("run_attempt")
    valid_run_id = type(run_id) is int and run_id >= 1
    valid_attempt = run_attempt is None or (
        type(run_attempt) is int and run_attempt >= 1
    )
    if not valid_run_id:
        internal_conflicts.append(
            {"reason": "ci_performance_invalid_run_id", "observed": run_id}
        )
    if not valid_attempt:
        internal_conflicts.append(
            {"reason": "ci_performance_invalid_run_attempt", "observed": run_attempt}
        )

    raw_jobs = payload.get("jobs")
    job_ids: list[int] = []
    seen_job_ids: set[int] = set()
    if raw_jobs is not None and not isinstance(raw_jobs, list):
        internal_conflicts.append(
            {
                "reason": "ci_performance_jobs_invalid_type",
                "observed": type(raw_jobs).__name__,
            }
        )
    elif isinstance(raw_jobs, list):
        for j_idx, job in enumerate(raw_jobs):
            if not isinstance(job, dict):
                internal_conflicts.append(
                    {"reason": "ci_performance_job_entry_invalid", "index": j_idx}
                )
                continue
            jid = job.get("id")
            if jid is not None:
                if type(jid) is not int or jid <= 0:
                    internal_conflicts.append(
                        {
                            "reason": "ci_performance_invalid_job_id",
                            "index": j_idx,
                            "observed": jid,
                        }
                    )
                elif jid in seen_job_ids:
                    internal_conflicts.append(
                        {"reason": "ci_performance_duplicate_job_id", "job_id": jid}
                    )
                else:
                    seen_job_ids.add(jid)
                    job_ids.append(jid)
            if valid_run_id and (run_attempt is None or valid_attempt):
                try:
                    cicd_telemetry._validate_job_membership(
                        job,
                        run_id=run_id,
                        run_attempt=run_attempt if run_attempt is not None else 1,
                    )
                except cicd_telemetry.TelemetryError as exc:
                    if not (run_attempt is None and exc.code == "job_attempt_mismatch"):
                        internal_conflicts.append(
                            {
                                "reason": f"ci_performance_{exc.code}",
                                "index": j_idx,
                                "job_id": jid if type(jid) is int else None,
                            }
                        )

    attempt_int = run_attempt if type(run_attempt) is int else None
    rerun_attribution = (
        "unpartitioned_attempt_jobs_snapshot"
        if attempt_int is not None and attempt_int > 1
        else "attempt_1_or_unverified"
    )
    has_conflict = bool(internal_conflicts)
    window_sec = payload.get("checks_execution_window_seconds")
    sub_complete_sec = payload.get("submission_to_checks_complete_seconds")
    elapsed_proj = (
        window_sec
        if isinstance(window_sec, (int, float))
        else (sub_complete_sec if isinstance(sub_complete_sec, (int, float)) else None)
    )
    return {
        "source_type": "ci_performance",
        "path": str(path),
        "status": "invalid" if has_conflict else "loaded",
        "ci_run_id": None if has_conflict else _norm_optional_str(run_id),
        "ci_run_attempt": None if has_conflict else _norm_optional_str(run_attempt),
        "job_ids": [] if has_conflict else sorted(job_ids),
        "declared_identity": {"run_id": run_id, "run_attempt": run_attempt},
        "git_commit": _norm_optional_str(payload.get("sha")),
        "conclusion": _norm_optional_str(payload.get("conclusion")),
        "event": _norm_optional_str(payload.get("event")),
        "workflow": _norm_optional_str(payload.get("workflow")),
        "url": _norm_optional_str(payload.get("url")),
        "queued_before_first_job_seconds": payload.get(
            "queued_before_first_job_seconds"
        ),
        "checks_execution_window_seconds": window_sec,
        "submission_to_checks_complete_seconds": sub_complete_sec,
        "elapsed_seconds": elapsed_proj,
        "critical_finishing_job": payload.get("critical_finishing_job"),
        "parallel_runner_seconds": payload.get("parallel_runner_seconds"),
        "first_failed_step_completion_seconds": payload.get(
            "first_failed_step_completion_seconds"
        ),
        "limitations": payload.get("limitations"),
        "ci_provider_provenance": {
            "provider": "github_actions",
            "run_id": run_id,
            "run_attempt": run_attempt,
            "workflow": payload.get("workflow"),
            "event": payload.get("event"),
            "url": payload.get("url"),
            "authoritative_ci_end_state": False,
            "rerun_witness_attribution": rerun_attribution,
        },
        "jobs": raw_jobs,
        "dagger": payload.get("dagger"),
        "internal_conflicts": internal_conflicts,
        "consumed_files": [str(path)],
    }


def _sort_mixed_values(vals: Any) -> list[Any]:
    return sorted(
        vals,
        key=lambda v: (
            type(v).__name__,
            json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else str(v),
        ),
    )


def _source_identities(src: dict[str, Any]) -> dict[str, Any]:
    """Extract scoped concrete execution, upper context, content, and revision identifiers."""
    stype = src["source_type"]
    concrete_keys: dict[str, set[str]] = {
        "execution_ref": set(),
        "report_ref": set(),
        "bundle_directory": set(),
    }
    context_keys: dict[str, set[str]] = {
        "invocation_ref": set(),
        "run_attempt_pair": set(),
        "ci_attempt_context": set(),
        "pytest_invocation_id": set(),
        "matched_trace_id": set(),
        "trace_anchor_span": set(),
        "trace_dagql_span": set(),
        "trace_core_span": set(),
    }
    content_keys: dict[str, set[str]] = {
        "event_payload_ref": set(),
        "plan_ref": set(),
        "source_tree_id": set(),
        "declared_target_source_ref": set(),
        "artifact_sha256": set(),
    }
    revisions: set[str] = set()
    has_conflicts = bool(src.get("internal_conflicts"))

    if stype == "verify":
        if src.get("git_commit") and not has_conflicts:
            revisions.add(src["git_commit"])
    elif stype == "pytest_invocation":
        if src.get("status") == "loaded" and not has_conflicts:
            if src.get("invocation_id"):
                context_keys["pytest_invocation_id"].add(src["invocation_id"])
            if src.get("git_commit"):
                revisions.add(src["git_commit"])
    elif stype == "hook_invocation":
        if src.get("invocation_ref"):
            context_keys["invocation_ref"].add(src["invocation_ref"])
        if src.get("event_payload_ref"):
            content_keys["event_payload_ref"].add(src["event_payload_ref"])
        if src.get("plan_ref"):
            content_keys["plan_ref"].add(src["plan_ref"])
        for st in src.get("source_tree_ids") or ():
            content_keys["source_tree_id"].add(st)
        for gc in src.get("git_commits") or ():
            revisions.add(gc)
        for ex in src.get("executions") or ():
            if not ex.get("report_digest_verified", True):
                continue
            if ex.get("run_ref") and ex.get("attempt_ref"):
                context_keys["run_attempt_pair"].add(
                    pytest_contracts.canonical_tuple_key(
                        ex["run_ref"], ex["attempt_ref"]
                    )
                )
            if ex.get("execution_ref"):
                concrete_keys["execution_ref"].add(ex["execution_ref"])
            if ex.get("report_ref"):
                concrete_keys["report_ref"].add(ex["report_ref"])
            if ex.get("bundle_directory"):
                concrete_keys["bundle_directory"].add(ex["bundle_directory"])
            if ex.get("git_commit"):
                revisions.add(ex["git_commit"])
            if ex.get("source_tree_id"):
                content_keys["source_tree_id"].add(ex["source_tree_id"])
    elif stype in ("execution_bundle", "execution_report"):
        if (
            src.get("report_contract_status") == "valid"
            and src.get("verifiable_execution_proven")
            and not has_conflicts
        ):
            if src.get("run_ref") and src.get("attempt_ref"):
                context_keys["run_attempt_pair"].add(
                    pytest_contracts.canonical_tuple_key(
                        src["run_ref"], src["attempt_ref"]
                    )
                )
            if src.get("execution_ref"):
                concrete_keys["execution_ref"].add(src["execution_ref"])
            if src.get("report_ref"):
                concrete_keys["report_ref"].add(src["report_ref"])
            if src.get("bundle_directory"):
                concrete_keys["bundle_directory"].add(src["bundle_directory"])
            if src.get("plan_ref"):
                content_keys["plan_ref"].add(src["plan_ref"])
            if src.get("source_tree_id"):
                content_keys["source_tree_id"].add(src["source_tree_id"])
        if src.get("git_commit") and not has_conflicts:
            revisions.add(src["git_commit"])
    elif stype in ("junit_xml", "pytest_reportlog"):
        if src.get("pytest_invocation_id") and not has_conflicts:
            context_keys["pytest_invocation_id"].add(src["pytest_invocation_id"])
        if (
            src.get("bundle_binding") == "verified_manifest_digest"
            and src.get("bound_bundle_directory")
            and not has_conflicts
        ):
            concrete_keys["bundle_directory"].add(src["bound_bundle_directory"])
        if src.get("sha256"):
            # Identical JUnit/reportlog bytes across distinct bundles are content equivalence only.
            content_keys["artifact_sha256"].add(src["sha256"])
        if not has_conflicts:
            props = src.get("properties") or {}
            rref = _norm_optional_str(props.get("run_ref"))
            aref = _norm_optional_str(props.get("attempt_ref"))
            if rref and aref:
                context_keys["run_attempt_pair"].add(
                    pytest_contracts.canonical_tuple_key(rref, aref)
                )
            inv_v = _norm_optional_str(props.get("invocation_ref"))
            if inv_v:
                context_keys["invocation_ref"].add(inv_v)
            pref = _norm_optional_str(props.get("plan_ref"))
            if pref:
                content_keys["plan_ref"].add(pref)
            gc = _norm_optional_str(props.get("git_commit") or props.get("sha"))
            if gc:
                revisions.add(gc)
    elif stype == "dagger_telemetry":
        bound = src.get("native_correlation_status") == "bound" and not has_conflicts
        b_verified = bound and src.get("execution_provenance_status") == "verified"
        if bound and src.get("matched_trace_id"):
            tid = src["matched_trace_id"]
            context_keys["matched_trace_id"].add(tid)
            if src.get("anchor_span_id"):
                context_keys["trace_anchor_span"].add(
                    pytest_contracts.canonical_tuple_key(
                        tid,
                        src.get("matched_session_id") or "",
                        src.get("matched_engine_instance_id") or "",
                        src["anchor_span_id"],
                    )
                )
            if src.get("dagql_span_id"):
                context_keys["trace_dagql_span"].add(
                    pytest_contracts.canonical_tuple_key(tid, src["dagql_span_id"])
                )
            if src.get("core_span_id"):
                context_keys["trace_core_span"].add(
                    pytest_contracts.canonical_tuple_key(tid, src["core_span_id"])
                )
        if b_verified and src.get("run_ref") and src.get("attempt_ref"):
            context_keys["run_attempt_pair"].add(
                pytest_contracts.canonical_tuple_key(src["run_ref"], src["attempt_ref"])
            )
        if b_verified and src.get("execution_ref"):
            concrete_keys["execution_ref"].add(src["execution_ref"])
        if src.get("declared_target_source_ref"):
            # Declared source ref is not verified Tree equivalence until #87 B delivers.
            content_keys["declared_target_source_ref"].add(
                src["declared_target_source_ref"]
            )
        if src.get("git_commit") and not has_conflicts:
            revisions.add(src["git_commit"])
    elif stype == "ci_performance":
        if (
            src.get("status") == "loaded"
            and not has_conflicts
            and src.get("ci_run_id")
            and src.get("ci_run_attempt")
        ):
            context_keys["ci_attempt_context"].add(
                pytest_contracts.canonical_tuple_key(
                    str(src["ci_run_id"]), str(src["ci_run_attempt"])
                )
            )
        if (
            src.get("status") == "loaded"
            and not has_conflicts
            and src.get("git_commit")
        ):
            revisions.add(src["git_commit"])

    return {
        "execution": concrete_keys,
        "context": context_keys,
        "content": content_keys,
        "revision": revisions,
    }


def _check_scoped_cross_conflicts(
    sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Detect conflicts only within the exact verified object/target scope."""
    conflicts: list[dict[str, Any]] = []
    anchors: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for src in sources:
        if src.get("status") not in ("loaded", "completed"):
            continue
        src_internal = list(src.get("internal_conflicts") or ())
        stype = src["source_type"]
        if stype in ("execution_bundle", "execution_report"):
            if (
                src.get("report_contract_status") != "valid"
                or not src.get("verifiable_execution_proven")
                or src_internal
            ):
                continue
            if src.get("execution_ref"):
                anchors.setdefault(("execution_ref", src["execution_ref"]), []).append(
                    src
                )
            if src.get("bundle_directory"):
                anchors.setdefault(
                    ("bundle_directory", src["bundle_directory"]), []
                ).append(src)
        elif stype == "hook_invocation":
            inv_ref = src.get("invocation_ref")
            stem = Path(src["path"]).stem if isinstance(src.get("path"), str) else None
            for idx_e, ex in enumerate(src.get("executions") or ()):
                if not ex.get("report_digest_verified", True):
                    continue
                unit_stub = {
                    "execution_index": idx_e,
                    "artifact": ex.get("artifact"),
                    "scope": ex.get("scope"),
                    "keys": {},
                }
                if any(
                    _conflict_targets_unit(c, unit_stub, ex, stem=stem)
                    for c in src_internal
                ):
                    continue
                hook_exec = {
                    "source_type": "hook_execution",
                    "invocation_ref": inv_ref,
                    **ex,
                }
                if ex.get("execution_ref"):
                    anchors.setdefault(
                        ("execution_ref", ex["execution_ref"]), []
                    ).append(hook_exec)
                if ex.get("bundle_directory"):
                    anchors.setdefault(
                        ("bundle_directory", ex["bundle_directory"]), []
                    ).append(hook_exec)
                if ex.get("run_ref") and ex.get("attempt_ref"):
                    anchors.setdefault(
                        (
                            "run_attempt_binding",
                            pytest_contracts.canonical_tuple_key(
                                ex["run_ref"], ex["attempt_ref"]
                            ),
                        ),
                        [],
                    ).append(hook_exec)
                    if ex.get("remote_ref"):
                        anchors.setdefault(
                            (
                                "push_ref_target",
                                pytest_contracts.canonical_tuple_key(
                                    ex["run_ref"], ex["attempt_ref"], ex["remote_ref"]
                                ),
                            ),
                            [],
                        ).append(hook_exec)
        elif (
            stype == "dagger_telemetry"
            and src.get("native_correlation_status") == "bound"
            and src.get("execution_provenance_status") == "verified"
            and not src_internal
        ):
            if src.get("execution_ref"):
                anchors.setdefault(("execution_ref", src["execution_ref"]), []).append(
                    src
                )
        elif (
            stype == "ci_performance"
            and src.get("status") == "loaded"
            and not src_internal
            and src.get("ci_run_id")
            and src.get("ci_run_attempt")
        ):
            anchors.setdefault(
                (
                    "ci_attempt",
                    pytest_contracts.canonical_tuple_key(
                        str(src["ci_run_id"]), str(src["ci_run_attempt"])
                    ),
                ),
                [],
            ).append(src)
            for job in src.get("jobs") or ():
                if isinstance(job, dict) and type(job.get("id")) is int:
                    anchors.setdefault(
                        (
                            "ci_job",
                            pytest_contracts.canonical_tuple_key(
                                str(src["ci_run_id"]),
                                str(src["ci_run_attempt"]),
                                job["id"],
                            ),
                        ),
                        [],
                    ).append(job)

    # Note: run_attempt does NOT force single commit/tree/plan because one pre-push
    # invocation legitimately targets multiple refs with distinct trees and plans.
    anchor_invariants = {
        "execution_ref": ("exit_code", "git_commit", "source_tree_id", "report_ref"),
        "bundle_directory": ("exit_code", "git_commit", "source_tree_id", "report_ref"),
        "push_ref_target": ("exit_code", "git_commit", "source_tree_id"),
        "run_attempt_binding": ("invocation_ref",),
        "ci_attempt": ("git_commit", "workflow", "event"),
        "ci_job": ("name", "status", "conclusion", "started_at", "completed_at"),
    }
    for (anchor_kind, anchor_val), group in anchors.items():
        if len(group) < 2:
            continue
        for field in anchor_invariants.get(anchor_kind, ()):
            raw_vals = [
                item[field]
                for item in group
                if field in item and item[field] is not None
            ]
            # Deduplicate with canonical representation since values may be unhashable or mixed types.
            distinct_by_repr: dict[str, Any] = {}
            for val in raw_vals:
                rkey = json.dumps([type(val).__name__, val], sort_keys=True)
                distinct_by_repr.setdefault(rkey, val)
            if len(distinct_by_repr) > 1:
                conflicts.append(
                    {
                        "anchor": {anchor_kind: anchor_val},
                        "field": field,
                        "values": _sort_mixed_values(distinct_by_repr.values()),
                    }
                )
        if anchor_kind in ("execution_ref", "bundle_directory"):
            plan_scopes = {
                item.get("plan_scope") for item in group if item.get("plan_scope")
            }
            if len(plan_scopes) <= 1:
                pvals = {
                    item["plan_ref"]
                    for item in group
                    if item.get("plan_ref") is not None
                }
                if len(pvals) > 1:
                    conflicts.append(
                        {
                            "anchor": {anchor_kind: anchor_val},
                            "field": "plan_ref",
                            "values": _sort_mixed_values(pvals),
                        }
                    )
    return conflicts


def _connected_components(
    node_count: int, edges: dict[tuple[int, int], dict[str, list[str]]]
) -> list[list[int]]:
    adj: dict[int, set[int]] = {i: set() for i in range(node_count)}
    for i, j in edges:
        adj[i].add(j)
        adj[j].add(i)
    seen: set[int] = set()
    components: list[list[int]] = []
    for start in range(node_count):
        if start in seen:
            continue
        stack = [start]
        comp: list[int] = []
        seen.add(start)
        while stack:
            cur = stack.pop()
            comp.append(cur)
            for nxt in sorted(adj[cur]):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        components.append(sorted(comp))
    return components


def _conflict_targets_unit(
    conflict: dict[str, Any],
    unit: dict[str, Any],
    exec_item: dict[str, Any],
    *,
    stem: str | None = None,
) -> bool:
    """Return True if conflict applies to this specific execution unit rather than an unrelated sibling."""
    if unit.get("execution_index") is None:
        return True
    if isinstance(conflict.get("anchor"), dict):
        return _anchor_matches_unit(conflict["anchor"], unit, exec_item)
    unit_art = unit.get("artifact")
    affected = conflict.get("affected_artifacts")
    if isinstance(affected, list) and affected:
        return unit_art in affected
    if conflict.get("exec_artifact") is not None:
        return unit_art == conflict.get("exec_artifact")
    c_art = conflict.get("artifact")
    if isinstance(c_art, str) and c_art:
        if c_art == unit_art:
            return True
        if stem and c_art.startswith(
            (f"{stem}.execution", f"{stem}.activation", f"{stem}.timing-")
        ):
            return False
    return True


def _concrete_execution_units(
    src_idx: int,
    src: dict[str, Any],
    ids: dict[str, Any],
    *,
    scoped_conflicts: list[dict[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Build per-concrete-execution nodes so conflicted or multi-exec units never forge matches."""
    src_conflicts = [
        *list(src.get("internal_conflicts") or ()),
        *[c for c in scoped_conflicts if _anchor_matches_source(c["anchor"], ids)],
    ]
    if src.get("source_type") != "hook_invocation":
        has_conflict = bool(src_conflicts)
        return [
            {
                "unit_index": -1,
                "src_idx": src_idx,
                "execution_index": None,
                "verified": not has_conflict,
                "declared_keys": ids["execution"],
                "keys": (
                    {k: set() for k in ids["execution"]}
                    if has_conflict
                    else ids["execution"]
                ),
            }
        ]
    execs = src.get("executions") or ()
    stem = Path(src["path"]).stem if isinstance(src.get("path"), str) else None
    units: list[dict[str, Any]] = []
    for exec_idx, ex in enumerate(execs):
        keys: dict[str, set[str]] = {
            "execution_ref": set(),
            "report_ref": set(),
            "bundle_directory": set(),
        }
        if ex.get("execution_ref"):
            keys["execution_ref"].add(ex["execution_ref"])
        if ex.get("report_ref"):
            keys["report_ref"].add(ex["report_ref"])
        if ex.get("bundle_directory"):
            keys["bundle_directory"].add(ex["bundle_directory"])
        unit_stub = {
            "unit_index": -1,
            "src_idx": src_idx,
            "execution_index": exec_idx,
            "artifact": ex.get("artifact"),
            "scope": ex.get("scope"),
            "keys": keys,
        }
        u_conflicts = [
            c
            for c in src_conflicts
            if _conflict_targets_unit(c, unit_stub, ex, stem=stem)
        ]
        unit_verified = bool(ex.get("report_digest_verified", True)) and not u_conflicts
        unit_stub["verified"] = unit_verified
        unit_stub["declared_keys"] = keys
        if not unit_verified:
            unit_stub["keys"] = {
                "execution_ref": set(),
                "report_ref": set(),
                "bundle_directory": set(),
            }
        units.append(unit_stub)
    return units


def _pairwise_shared_tier_edges(
    items: list[dict[str, Any]],
    *,
    tier: str,
    verified_only: bool = False,
    src_index_fn: Any = None,
) -> dict[tuple[int, int], dict[str, list[str]]]:
    """Build deterministic undirected shared-key edges using an inverted key index."""
    buckets: dict[tuple[str, str], list[int]] = {}
    for idx, entry in enumerate(items):
        if verified_only and not entry.get("verified", True):
            continue
        key_map = entry[tier]
        for k_name, vals in key_map.items():
            for val in vals:
                buckets.setdefault((k_name, val), []).append(idx)
    raw_edges: dict[tuple[int, int], dict[str, set[str]]] = {}
    for (k_name, val), idxs in buckets.items():
        if len(idxs) < 2:
            continue
        for pos_a, u in enumerate(idxs):
            for v in idxs[pos_a + 1 :]:
                if src_index_fn is not None and src_index_fn(u) == src_index_fn(v):
                    continue
                pair = (u, v) if u < v else (v, u)
                raw_edges.setdefault(pair, {}).setdefault(k_name, set()).add(val)
    return {
        pair: {k: sorted(vs) for k, vs in sorted(kmap.items())}
        for pair, kmap in sorted(raw_edges.items())
    }


def _evaluate_correlations(sources: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify per-source relationships across concrete-execution/context/content/revision tiers."""
    identities = [_source_identities(src) for src in sources]
    scoped_conflicts = _check_scoped_cross_conflicts(sources)

    units: list[dict[str, Any]] = []
    src_unit_indices: dict[int, list[int]] = {}
    for idx, (src, ids) in enumerate(zip(sources, identities, strict=True)):
        built = _concrete_execution_units(
            idx, src, ids, scoped_conflicts=scoped_conflicts
        )
        for u in built:
            u["unit_index"] = len(units)
            src_unit_indices.setdefault(idx, []).append(u["unit_index"])
            units.append(u)

    unit_edges = _pairwise_shared_tier_edges(
        units,
        tier="keys",
        verified_only=True,
        src_index_fn=lambda u_idx: units[u_idx]["src_idx"],
    )
    ctx_edges = _pairwise_shared_tier_edges(identities, tier="context")

    raw_unit_components = _connected_components(len(units), unit_edges)
    execution_groups: list[dict[str, Any]] = []
    unit_to_exec_group: dict[int, int] = {}
    src_to_exec_groups: dict[int, list[int]] = {}
    for comp in raw_unit_components:
        if len(comp) < 2:
            continue
        gidx = len(execution_groups)
        group_keys: dict[str, set[str]] = {}
        group_sources: set[int] = set()
        group_units: list[dict[str, Any]] = []
        for u in comp:
            unit_to_exec_group[u] = gidx
            sidx = units[u]["src_idx"]
            group_sources.add(sidx)
            src_to_exec_groups.setdefault(sidx, [])
            if gidx not in src_to_exec_groups[sidx]:
                src_to_exec_groups[sidx].append(gidx)
            u_desc: dict[str, Any] = {"source_index": sidx}
            if units[u]["execution_index"] is not None:
                u_desc["execution_index"] = units[u]["execution_index"]
                if units[u].get("artifact") is not None:
                    u_desc["artifact"] = units[u]["artifact"]
                if units[u].get("scope") is not None:
                    u_desc["scope"] = units[u]["scope"]
            group_units.append(u_desc)
            for v in comp:
                if u < v and (u, v) in unit_edges:
                    for k, vals in unit_edges[(u, v)].items():
                        group_keys.setdefault(k, set()).update(vals)
        execution_groups.append(
            {
                "group_index": gidx,
                "source_indices": sorted(group_sources),
                "execution_units": group_units,
                "matched_keys": {k: sorted(v) for k, v in sorted(group_keys.items())},
            }
        )

    unit_matches: dict[int, dict[str, list[str]]] = {}
    for u_idx in range(len(units)):
        matched_u: dict[str, set[str]] = {}
        for (a, b), eshared in unit_edges.items():
            if a == u_idx or b == u_idx:
                for k, vals in eshared.items():
                    matched_u.setdefault(k, set()).update(vals)
        if matched_u:
            unit_matches[u_idx] = {k: sorted(v) for k, v in sorted(matched_u.items())}

    raw_ctx_components = _connected_components(len(sources), ctx_edges)
    context_groups: list[dict[str, Any]] = []
    for comp in raw_ctx_components:
        if len(comp) < 2:
            continue
        gkeys: dict[str, set[str]] = {}
        for i in comp:
            for j in comp:
                if i < j and (i, j) in ctx_edges:
                    for k, vals in ctx_edges[(i, j)].items():
                        gkeys.setdefault(k, set()).update(vals)
        context_groups.append(
            {
                "source_indices": comp,
                "matched_keys": {k: sorted(v) for k, v in sorted(gkeys.items())},
            }
        )

    content_edges = _pairwise_shared_tier_edges(identities, tier="content")
    rev_buckets: dict[str, list[int]] = {}
    for idx, entry in enumerate(identities):
        for rval in entry["revision"]:
            rev_buckets.setdefault(rval, []).append(idx)

    for idx, src in enumerate(sources):
        mine = identities[idx]
        internal_conflicts = list(src.get("internal_conflicts") or ())
        src_conflicts = [
            *internal_conflicts,
            *[c for c in scoped_conflicts if _anchor_matches_source(c["anchor"], mine)],
        ]

        exec_agg: dict[str, set[str]] = {}
        for u_idx in src_unit_indices.get(idx, ()):
            for key, vals in unit_matches.get(u_idx, {}).items():
                exec_agg.setdefault(key, set()).update(vals)
        exec_matches: dict[str, list[str]] = {
            k: sorted(v) for k, v in sorted(exec_agg.items())
        }
        ctx_agg: dict[str, set[str]] = {}
        for (a, b), cshared in ctx_edges.items():
            if a == idx or b == idx:
                for key, vals in cshared.items():
                    ctx_agg.setdefault(key, set()).update(vals)
        ctx_matches: dict[str, list[str]] = {
            k: sorted(v) for k, v in sorted(ctx_agg.items())
        }
        content_agg: dict[str, set[str]] = {}
        for (a, b), kshared in content_edges.items():
            if a == idx or b == idx:
                for key, vals in kshared.items():
                    content_agg.setdefault(key, set()).update(vals)
        content_matches: dict[str, list[str]] = {
            k: sorted(v) for k, v in sorted(content_agg.items())
        }
        rev_list = sorted(
            rval for rval in mine["revision"] if len(rev_buckets.get(rval, ())) >= 2
        )

        hook_u_idxs = (
            src_unit_indices.get(idx, [])
            if src.get("source_type") == "hook_invocation"
            else []
        )
        expected_unit_count = (
            src.get("expected_unit_count", len(hook_u_idxs))
            if src.get("source_type") == "hook_invocation"
            else 0
        )
        missing_expected = (
            list(src.get("missing_expected_executions") or ())
            if src.get("source_type") == "hook_invocation"
            else []
        )
        is_multi_exec_hook = (
            src.get("source_type") == "hook_invocation"
            and max(len(hook_u_idxs), expected_unit_count) > 1
        )
        stem = Path(src["path"]).stem if isinstance(src.get("path"), str) else None
        hook_execs = src.get("executions") or []
        hook_unit_summaries: list[dict[str, Any]] = []
        for u_idx in hook_u_idxs:
            ex_idx = units[u_idx]["execution_index"]
            ex_item = (
                hook_execs[ex_idx]
                if isinstance(ex_idx, int) and 0 <= ex_idx < len(hook_execs)
                else {}
            )
            decl_unit = {
                **units[u_idx],
                "keys": units[u_idx].get("declared_keys", units[u_idx]["keys"]),
            }
            u_conflicts = [
                c
                for c in src_conflicts
                if _conflict_targets_unit(c, decl_unit, ex_item, stem=stem)
            ]
            u_matched = bool(unit_matches.get(u_idx))
            u_status = (
                "conflict"
                if (u_conflicts or not ex_item.get("report_digest_verified", True))
                else ("matched" if u_matched else "unqueried")
            )
            hook_unit_summaries.append(
                {
                    "execution_index": ex_idx,
                    "artifact": units[u_idx].get("artifact"),
                    "scope": units[u_idx].get("scope"),
                    "status": u_status,
                    "same_execution_proven": u_status == "matched",
                    "execution_group_index": unit_to_exec_group.get(u_idx),
                    "execution_keys": unit_matches.get(u_idx, {}),
                }
            )
        group_indices = src_to_exec_groups.get(idx, [])
        matched_unit_count = sum(
            1 for item in hook_unit_summaries if item["status"] == "matched"
        )
        conflict_unit_count = sum(
            1 for item in hook_unit_summaries if item["status"] == "conflict"
        )
        unqueried_unit_count = sum(
            1 for item in hook_unit_summaries if item["status"] == "unqueried"
        )

        if src_conflicts:
            verdict = {
                "strength": "conflict",
                "same_execution_proven": False,
                "execution_keys": exec_matches,
                "context_keys": ctx_matches,
                "content_keys": content_matches,
                "revision_keys": rev_list,
                "conflicts": src_conflicts,
            }
            if is_multi_exec_hook:
                verdict["container_kind"] = "multi_execution_hook"
                verdict["execution_group_indices"] = group_indices
                verdict["expected_unit_count"] = expected_unit_count
                verdict["matched_unit_count"] = matched_unit_count
                verdict["conflict_unit_count"] = conflict_unit_count
                verdict["unqueried_unit_count"] = unqueried_unit_count
                verdict["missing_unit_count"] = len(missing_expected)
                verdict["execution_units"] = hook_unit_summaries
            elif hook_unit_summaries:
                verdict["expected_unit_count"] = expected_unit_count
                verdict["conflict_unit_count"] = conflict_unit_count
                verdict["missing_unit_count"] = len(missing_expected)
                verdict["execution_units"] = hook_unit_summaries
        elif src.get("bundle_binding") == "colocated_unverified":
            verdict = {
                "strength": "colocated_unverified",
                "same_execution_proven": False,
                "reason": "directory_coexistence_without_manifest_digest",
                "colocated_directory": src.get("colocated_directory"),
                "execution_keys": {},
                "context_keys": ctx_matches,
                "content_keys": content_matches,
                "revision_keys": rev_list,
            }
        elif is_multi_exec_hook:
            verdict = {
                "strength": "multi_execution_container",
                "container_kind": "multi_execution_hook",
                "same_execution_proven": False,
                "reason": "hook_invocation_contains_multiple_independent_executions",
                "execution_group_indices": group_indices,
                "expected_unit_count": expected_unit_count,
                "matched_unit_count": matched_unit_count,
                "conflict_unit_count": conflict_unit_count,
                "unqueried_unit_count": unqueried_unit_count,
                "missing_unit_count": len(missing_expected),
                "execution_units": hook_unit_summaries,
                "execution_keys": exec_matches,
                "context_keys": ctx_matches,
                "content_keys": content_matches,
                "revision_keys": rev_list,
            }
        elif missing_expected:
            verdict = {
                "strength": "standalone_uncorrelated",
                "same_execution_proven": False,
                "reason": "expected_hook_execution_missing_from_invocation",
                "expected_unit_count": expected_unit_count,
                "missing_unit_count": len(missing_expected),
                "missing_expected_executions": missing_expected,
                "execution_keys": {},
                "context_keys": ctx_matches,
                "content_keys": content_matches,
                "revision_keys": rev_list,
            }
        elif exec_matches:
            verdict = {
                "strength": "execution_matched",
                "same_execution_proven": True,
                "execution_group_index": group_indices[0] if group_indices else None,
                "execution_keys": exec_matches,
                "context_keys": ctx_matches,
                "content_keys": content_matches,
                "revision_keys": rev_list,
            }
            if hook_unit_summaries:
                verdict["execution_units"] = hook_unit_summaries
        elif ctx_matches:
            verdict = {
                "strength": "shared_context_only",
                "same_execution_proven": False,
                "reason": "shared_invocation_run_or_trace_context_does_not_prove_same_execution",
                "execution_keys": {},
                "context_keys": ctx_matches,
                "content_keys": content_matches,
                "revision_keys": rev_list,
            }
        elif content_matches:
            verdict = {
                "strength": "content_equivalent",
                "same_execution_proven": False,
                "reason": "shared_content_identity_does_not_prove_same_or_distinct_execution",
                "execution_keys": {},
                "context_keys": {},
                "content_keys": content_matches,
                "revision_keys": rev_list,
            }
        elif rev_list:
            verdict = {
                "strength": "revision_only",
                "same_execution_proven": False,
                "reason": "git_commit_shared_without_execution_instance_proof",
                "execution_keys": {},
                "context_keys": {},
                "content_keys": {},
                "revision_keys": rev_list,
            }
        else:
            verdict = {
                "strength": "standalone_uncorrelated",
                "same_execution_proven": False,
                "reason": "no_shared_correlation_keys",
                "execution_keys": {},
                "context_keys": {},
                "content_keys": {},
                "revision_keys": [],
            }
        src["query_correlation"] = verdict

    strengths = [src["query_correlation"]["strength"] for src in sources]
    has_internal_or_scoped_conflict = bool(scoped_conflicts) or any(
        src.get("internal_conflicts") for src in sources
    )
    overall_reason: str | None = None
    if has_internal_or_scoped_conflict or "conflict" in strengths:
        overall = "mismatch_detected"
    elif not sources:
        overall = "no_sources"
    elif (
        len(sources) >= 2
        and len(execution_groups) == 1
        and len(execution_groups[0]["source_indices"]) == len(sources)
        and all(s == "execution_matched" for s in strengths)
    ):
        overall = "execution_matched"
    elif execution_groups or any(s == "execution_matched" for s in strengths):
        overall = "partial_execution_matched"
        if len(execution_groups) > 1:
            overall_reason = "multiple_disconnected_execution_groups"
        elif "multi_execution_container" in strengths:
            overall_reason = "multi_execution_container_partially_matched"
    elif any(s == "shared_context_only" for s in strengths):
        overall = "shared_context_only"
    elif any(s == "content_equivalent" for s in strengths):
        overall = "content_equivalent"
    elif any(s == "revision_only" for s in strengths):
        overall = "revision_only"
    else:
        overall = "uncorrelated"

    out: dict[str, Any] = {
        "overall_status": overall,
        "execution_groups": execution_groups,
        "context_groups": context_groups,
        "scoped_conflicts": scoped_conflicts,
    }
    if overall_reason is not None:
        out["reason"] = overall_reason
    return out


def _anchor_matches_unit(
    anchor: dict[str, str], unit: dict[str, Any], exec_item: dict[str, Any]
) -> bool:
    keys = unit.get("keys") or {}
    if "execution_ref" in anchor and anchor["execution_ref"] in keys.get(
        "execution_ref", ()
    ):
        return True
    if "bundle_directory" in anchor and anchor["bundle_directory"] in keys.get(
        "bundle_directory", ()
    ):
        return True
    rref = exec_item.get("run_ref")
    aref = exec_item.get("attempt_ref")
    rem = exec_item.get("remote_ref")
    if "push_ref_target" in anchor and rref and aref and rem:
        return anchor["push_ref_target"] == pytest_contracts.canonical_tuple_key(
            rref, aref, rem
        )
    if "run_attempt_binding" in anchor and rref and aref:
        return anchor["run_attempt_binding"] == pytest_contracts.canonical_tuple_key(
            rref, aref
        )
    return False


def _anchor_matches_source(anchor: dict[str, str], ids: dict[str, Any]) -> bool:
    if (
        "execution_ref" in anchor
        and anchor["execution_ref"] in ids["execution"]["execution_ref"]
    ):
        return True
    if (
        "bundle_directory" in anchor
        and anchor["bundle_directory"] in ids["execution"]["bundle_directory"]
    ):
        return True
    if (
        "run_attempt_binding" in anchor
        and anchor["run_attempt_binding"] in ids["context"]["run_attempt_pair"]
    ):
        return True
    if "push_ref_target" in anchor:
        try:
            parts = json.loads(anchor["push_ref_target"])
        except (TypeError, ValueError, json.JSONDecodeError):
            parts = None
        if (
            isinstance(parts, list)
            and len(parts) >= 2
            and pytest_contracts.canonical_tuple_key(parts[0], parts[1])
            in ids["context"]["run_attempt_pair"]
        ):
            return True
    if (
        "ci_attempt" in anchor
        and anchor["ci_attempt"] in ids["context"]["ci_attempt_context"]
    ):
        return True
    if "ci_job" in anchor:
        try:
            jparts = json.loads(anchor["ci_job"])
        except (TypeError, ValueError, json.JSONDecodeError):
            jparts = None
        if isinstance(jparts, list) and len(jparts) >= 2:
            return (
                pytest_contracts.canonical_tuple_key(jparts[0], jparts[1])
                in ids["context"]["ci_attempt_context"]
            )
    return False


def build_verify_report(
    *,
    verify_dir: Path | None = None,
    hook_root: Path | None = None,
    invocation_ref: str | None = None,
    hook_invocation: Path | None = None,
    execution_reports: tuple[Path, ...] | list[Path] = (),
    pytest_invocations: tuple[Path, ...] | list[Path] = (),
    junit_xmls: tuple[Path, ...] | list[Path] = (),
    pytest_reportlogs: tuple[Path, ...] | list[Path] = (),
    dagger_telemetries: tuple[Path, ...] | list[Path] = (),
    ci_performances: tuple[Path, ...] | list[Path] = (),
) -> dict[str, Any]:
    """Build a strictly read-only timing & correlation report across supplied artifacts."""
    sources: list[dict[str, Any]] = []
    if verify_dir is not None:
        sources.append(_load_verify_entry(verify_dir))
    if (
        hook_invocation is not None
        or hook_root is not None
        or invocation_ref is not None
    ):
        sources.append(
            _load_hook_entry(
                hook_root=hook_root,
                invocation_ref=invocation_ref,
                hook_invocation=hook_invocation,
            )
        )
    for item in execution_reports:
        sources.extend(_load_execution_entries(item))
    for item in pytest_invocations:
        sources.extend(_load_pytest_invocation_entry(item))
    for item in junit_xmls:
        sources.append(_parse_junit_xml(item))
    for item in pytest_reportlogs:
        sources.append(_parse_pytest_reportlog(item))
    for item in dagger_telemetries:
        sources.append(_load_dagger_telemetry(item))
    for item in ci_performances:
        sources.append(_load_ci_performance(item))

    correlation = _evaluate_correlations(sources)
    return {
        "schema_version": 2,
        "read_only": True,
        "commands_executed": [],
        "time_source_verified": False,
        "correlation_policy": "scoped_execution_over_content_over_revision",
        "forbidden_matching_heuristics": [
            "latest_mtime",
            "filename_similarity",
            "timestamp_proximity",
            "directory_coexistence",
            "revision_or_content_equivalence_as_same_execution",
        ],
        "correlation": correlation,
        "sources": sources,
    }


def _collect_protected_paths(
    report: dict[str, Any],
    *,
    verify_dir: Path | None,
    hook_root: Path | None,
    hook_invocation: Path | None,
    execution_reports: tuple[Path, ...] | list[Path],
    pytest_invocations: tuple[Path, ...] | list[Path],
    junit_xmls: tuple[Path, ...] | list[Path],
    pytest_reportlogs: tuple[Path, ...] | list[Path],
    dagger_telemetries: tuple[Path, ...] | list[Path],
    ci_performances: tuple[Path, ...] | list[Path],
) -> tuple[set[Path], set[Path]]:
    """Collect resolved input files and artifact directories that --out must never overwrite."""
    protected_files: set[Path] = set()
    protected_dirs: set[Path] = set()

    def _add_path(raw: str | Path | None) -> None:
        if raw is None:
            return
        p = Path(raw)
        if p.exists():
            if p.is_dir():
                protected_dirs.add(p.resolve())
            else:
                protected_files.add(p.resolve())
        with suppress(OSError):
            protected_files.add(p.resolve())

    _add_path(verify_dir)
    _add_path(hook_invocation)
    if hook_root is not None:
        gd = _resolve_git_dir_readonly(hook_root)
        if gd is not None:
            protected_dirs.add((gd / "eci-hooks").resolve())
    for group in (
        execution_reports,
        pytest_invocations,
        junit_xmls,
        pytest_reportlogs,
        dagger_telemetries,
        ci_performances,
    ):
        for item in group:
            _add_path(item)

    for src in report.get("sources", []):
        _add_path(src.get("path"))
        for cf in src.get("consumed_files") or ():
            _add_path(cf)
        for cd in src.get("consumed_dirs") or ():
            p = Path(cd)
            with suppress(OSError):
                protected_dirs.add(p.resolve())

    return protected_files, protected_dirs


def _validate_output_destination(
    out: Path,
    protected_files: set[Path],
    protected_dirs: set[Path],
) -> None:
    if out.is_symlink():
        raise ValueError("verify report --out cannot target a symlink")
    resolved_out = out.resolve()
    if resolved_out.exists() and resolved_out.is_dir():
        raise ValueError("verify report --out cannot target a directory")
    if resolved_out in protected_files:
        raise ValueError(
            f"verify report --out refuses to overwrite consumed input: {resolved_out}"
        )
    for pdir in protected_dirs:
        if resolved_out == pdir or resolved_out.is_relative_to(pdir):
            raise ValueError(
                f"verify report --out cannot write inside artifact store: {pdir}"
            )


def write_text_atomic(out: Path, encoded: str) -> None:
    """Atomically write UTF-8 LF text via a sibling temporary file and fsync."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=out.parent,
            prefix=f".{out.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            with suppress(OSError):
                os.fsync(handle.fileno())
        tmp_path.replace(out)
    finally:
        if tmp_path is not None:
            with suppress(OSError):
                tmp_path.unlink(missing_ok=True)


def _write_report_atomic(out: Path, encoded: str) -> None:
    write_text_atomic(out, encoded)


def format_report_text(report: dict[str, Any]) -> str:
    """Render concise human-readable timing and correlation summary without extra IO."""
    corr = report["correlation"]
    lines = [
        f"correlation: {corr['overall_status']} (read_only={report['read_only']}, time_source_verified={report['time_source_verified']})",
    ]
    if corr["scoped_conflicts"]:
        lines.append(
            f"conflicts: {json.dumps(corr['scoped_conflicts'], ensure_ascii=False)}"
        )
    for src in report["sources"]:
        qcorr = src["query_correlation"]
        line = (
            f"- [{src['source_type']}] status={src.get('status')} "
            f"strength={qcorr['strength']}"
        )
        if "elapsed_seconds" in src and src["elapsed_seconds"] is not None:
            line += f" elapsed={src['elapsed_seconds']}"
        elif (
            "total_duration_seconds" in src
            and src["total_duration_seconds"] is not None
        ):
            line += f" elapsed={src['total_duration_seconds']}"
        elif (
            "cumulative_phase_duration_seconds" in src
            and src["cumulative_phase_duration_seconds"] is not None
        ):
            line += f" phase_elapsed={src['cumulative_phase_duration_seconds']}"
        lines.append(line)
    return "\n".join(lines)


def run_report_cli(argv: list[str] | None = None) -> int:
    """CLI entrypoint for `engineering-ci verify report`."""
    parser = argparse.ArgumentParser(
        prog="engineering-ci verify report",
        description="Read-only timing report and tiered explicit-key correlation across Verify, Hooks, pytest, Dagger, and CI artifacts.",
    )
    parser.add_argument(
        "--verify-dir", type=Path, help="Verify invocation artifact directory"
    )
    parser.add_argument(
        "--hook-root", type=Path, help="Git dir, repo root, or linked worktree"
    )
    parser.add_argument(
        "--invocation-ref", help="Explicit standard Hook ref invocation:<32hex>"
    )
    parser.add_argument(
        "--hook-invocation", type=Path, help="Explicit Hook invocation JSON file"
    )
    parser.add_argument(
        "--execution-report",
        type=Path,
        action="append",
        default=[],
        help="Execution JSON report or #86 artifact bundle directory (repeatable)",
    )
    parser.add_argument(
        "--pytest-invocation",
        type=Path,
        action="append",
        default=[],
        help="Explicit #168 pytest invocation artifact directory (repeatable; no latest scan)",
    )
    parser.add_argument(
        "--junit-xml",
        type=Path,
        action="append",
        default=[],
        help="JUnit XML timing report (#167 R3, repeatable)",
    )
    parser.add_argument(
        "--pytest-reportlog",
        type=Path,
        action="append",
        default=[],
        help="Pytest reportlog JSONL path (#167 R3, repeatable)",
    )
    parser.add_argument(
        "--dagger-telemetry",
        type=Path,
        action="append",
        default=[],
        help="Dagger telemetry trace receipt JSON (#87, repeatable)",
    )
    parser.add_argument(
        "--ci-performance",
        type=Path,
        action="append",
        default=[],
        help="Actions timing JSON report from tools/performance.py (#131, repeatable)",
    )
    parser.add_argument(
        "--out", type=Path, help="Optional isolated output file for unified JSON report"
    )
    parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="Stdout output format (default: json)",
    )
    args = parser.parse_args(argv)
    if (args.hook_root is None) != (args.invocation_ref is None):
        parser.error("--hook-root and --invocation-ref must be provided together")
    if args.hook_invocation is not None and (
        args.hook_root is not None or args.invocation_ref is not None
    ):
        parser.error(
            "--hook-invocation cannot be combined with --hook-root / --invocation-ref"
        )
    if not any(
        (
            args.verify_dir,
            args.hook_invocation,
            args.hook_root,
            args.invocation_ref,
            args.execution_report,
            args.pytest_invocation,
            args.junit_xml,
            args.pytest_reportlog,
            args.dagger_telemetry,
            args.ci_performance,
        )
    ):
        parser.error("at least one explicit artifact source is required")

    report = build_verify_report(
        verify_dir=args.verify_dir,
        hook_root=args.hook_root,
        invocation_ref=args.invocation_ref,
        hook_invocation=args.hook_invocation,
        execution_reports=args.execution_report,
        pytest_invocations=args.pytest_invocation,
        junit_xmls=args.junit_xml,
        pytest_reportlogs=args.pytest_reportlog,
        dagger_telemetries=args.dagger_telemetry,
        ci_performances=args.ci_performance,
    )
    encoded = json.dumps(report, indent=2) + "\n"
    if args.out is not None:
        protected_files, protected_dirs = _collect_protected_paths(
            report,
            verify_dir=args.verify_dir,
            hook_root=args.hook_root,
            hook_invocation=args.hook_invocation,
            execution_reports=args.execution_report,
            pytest_invocations=args.pytest_invocation,
            junit_xmls=args.junit_xml,
            pytest_reportlogs=args.pytest_reportlog,
            dagger_telemetries=args.dagger_telemetry,
            ci_performances=args.ci_performance,
        )
        try:
            _validate_output_destination(args.out, protected_files, protected_dirs)
            _write_report_atomic(args.out, encoded)
        except (OSError, ValueError) as exc:
            import sys

            print(f"verify report: {exc}", file=sys.stderr)
            return 2

    if args.format == "text":
        print(format_report_text(report))
    else:
        print(encoded, end="")
    return 2 if report["correlation"]["overall_status"] == "mismatch_detected" else 0
