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

from engineering_ci import execution, hooks, reconciliation

MAX_OUTPUT_PARSE_BYTES = 8 * 1024 * 1024
MAX_JSON_REPORT_BYTES = 16 * 1024 * 1024
MAX_XML_REPORT_BYTES = 16 * 1024 * 1024
MAX_REPORTLOG_BYTES = 32 * 1024 * 1024
MAX_REPORTLOG_LINES = 500_000
MAX_REPORTLOG_LINE_BYTES = 1024 * 1024
MAX_GITDIR_POINTER_BYTES = 4096
MAX_SLOW_ITEMS = 25

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
) -> dict[str, Any]:
    cfg = cfg or {}
    node: dict[str, Any] = {
        "name": name,
        "path": path,
        "status": status,
        "kind": "group" if cfg.get("is_group") else "job",
        "parallel": cfg.get("parallel"),
        "duration_seconds": duration_seconds,
        "duration_raw": duration_raw,
        "duration_precision_seconds": precision_seconds,
        "skip_reason": skip_reason,
        "children": [],
    }
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
        cfg = config_map.get(tuple(prefix), {})
        if existing is None:
            existing = _make_node(
                part,
                list(prefix),
                default_status,
                cfg=cfg if is_last else {**cfg, "is_group": True},
                skip_reason=skip_reason if is_last else None,
                source=source if is_last else None,
            )
            siblings.append(existing)
        if not is_last:
            existing["kind"] = "group"
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
            name = item_match.group("name").strip()
            dur_raw = item_match.group("dur").strip()
            dur_val, precision = _parse_seconds_label(dur_raw)
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
        cfg = config_map.get(tuple(path), {})
        node = _make_node(
            name,
            path,
            status,
            cfg=cfg,
            duration_seconds=dur_val,
            duration_raw=dur_raw,
            precision_seconds=precision,
        )
        if parent is None:
            items.append(node)
        else:
            parent["kind"] = "group"
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
                "skip_reason": node["skip_reason"],
            }
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
        "output_truncated": output_truncated,
        "stream_bytes_observed": {"stdout": stdout_bytes, "stderr": stderr_bytes},
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
    timing = _load_bounded_json(timing_path) if timing_path.exists() else None
    status = _load_bounded_json(status_path) if status_path.exists() else None
    chosen = timing if timing and timing.get("status") == "loaded" else status
    if chosen is None:
        missing = timing or status or {"status": "missing", "path": str(timing_path)}
        entry["status"] = missing["status"]
        if "error" in missing:
            entry["error"] = missing["error"]
        return entry
    entry["consumed_files"].append(chosen["path"])
    if chosen.get("status") != "loaded":
        entry["status"] = chosen["status"]
        entry["artifact"] = chosen["path"]
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
            "native_exit_code": payload.get("native_exit_code"),
            "elapsed_seconds": payload.get("elapsed_seconds"),
            "stages": payload.get("stages"),
            "started_at_ns": payload.get("started_at_ns"),
            "completed_at_ns": payload.get("completed_at_ns"),
            "time_source_verified": False,
            "native_outputs_verification": verified_outputs,
            "missing_native_streams": missing_streams,
            "evidence_complete": outputs_complete,
            "lefthook_summary": payload.get("lefthook_summary"),
        }
    )
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
    stem = binding.invocation_path.stem
    invocations_dir = binding.invocation_path.parent
    event = binding.event
    identity = {
        "invocation_ref": binding.invocation_ref,
        "event_payload_ref": binding.event_payload_ref,
    }
    expected_run = f"hook-run:{binding.invocation_ref}"
    expected_att = f"hook-attempt:{binding.invocation_ref}"

    def _load_sidecar(sidecar_path: Path) -> dict[str, Any] | None:
        if (
            sidecar_path.is_symlink()
            or sidecar_path.resolve().parent != invocations_dir
        ):
            entry["internal_conflicts"].append(
                {"reason": "hook_sidecar_outside_store", "artifact": sidecar_path.name}
            )
            return None
        loaded = _load_bounded_json(sidecar_path)
        entry["consumed_files"].append(str(sidecar_path))
        if loaded.get("status") != "loaded":
            entry["internal_conflicts"].append(
                {
                    "reason": "hook_sidecar_invalid",
                    "artifact": sidecar_path.name,
                    "detail": loaded.get("status"),
                }
            )
            return None
        payload = loaded["payload"]
        for key in ("invocation_ref", "event_payload_ref"):
            if key in payload and payload[key] != identity[key]:
                entry["internal_conflicts"].append(
                    {
                        "reason": f"hook_sidecar_{key}_mismatch",
                        "artifact": sidecar_path.name,
                        "expected": identity[key],
                        "observed": payload[key],
                    }
                )
        return payload

    plan_ref: str | None = None
    plan_payload_obj = None
    has_plan_key = False
    plan_path = invocations_dir / f"{stem}.plan"
    if plan_path.exists():
        plan_data = _load_sidecar(plan_path)
        if plan_data is not None:
            plan_ref = _norm_optional_str(plan_data.get("plan_ref"))
            plan_payload_obj = plan_data.get("plan")
            has_plan_key = "plan" in plan_data or plan_payload_obj is not None
            if has_plan_key:
                expected_plan_ref = hooks._digest_bytes(
                    hooks.canonical_event_bytes(plan_payload_obj)
                )
                if plan_ref != expected_plan_ref:
                    entry["internal_conflicts"].append(
                        {
                            "reason": "hook_plan_digest_mismatch",
                            "expected": expected_plan_ref,
                            "observed": plan_ref,
                        }
                    )
    plan_scope = (
        "push_multi_ref_aggregate"
        if isinstance(plan_payload_obj, list)
        else ("single_plan" if isinstance(plan_payload_obj, dict) else "none")
    )

    push_refs: list[dict[str, Any]] = []
    push_source_path = invocations_dir / f"{stem}.push-source"
    if push_source_path.exists():
        ps = _load_sidecar(push_source_path)
        if ps is not None and isinstance(ps.get("references"), list):
            push_refs = list(ps["references"])

    # Load one-to-many execution artifacts with digest, activation, and report verification.
    executions: list[dict[str, Any]] = []
    seen_exec_paths: set[Path] = set()
    activation_cache: dict[str, bool] = {}

    def _expected_activation_name(exec_name: str) -> str:
        prefix = f"{stem}.execution-"
        if exec_name.startswith(prefix):
            suffix_part = exec_name[len(prefix) :]
            if suffix_part.startswith("check-"):
                return f"{stem}.activation-{suffix_part}"
        return f"{stem}.activation"

    def _verify_activation(exec_name: str) -> bool:
        act_name = _expected_activation_name(exec_name)
        if act_name in activation_cache:
            return activation_cache[act_name]
        act_path = invocations_dir / act_name
        if not act_path.exists():
            entry["internal_conflicts"].append(
                {
                    "reason": "hook_execution_activation_missing",
                    "artifact": exec_name,
                    "expected_activation": act_name,
                }
            )
            activation_cache[act_name] = False
            return False
        act_data = _load_sidecar(act_path)
        if act_data is None:
            activation_cache[act_name] = False
            return False
        ok = (
            act_data.get("run_ref") == expected_run
            and act_data.get("attempt_ref") == expected_att
        )
        if not ok:
            entry["internal_conflicts"].append(
                {
                    "reason": "hook_activation_identity_mismatch",
                    "artifact": act_name,
                    "expected_run_ref": expected_run,
                    "expected_attempt_ref": expected_att,
                }
            )
        activation_cache[act_name] = ok
        return ok

    for pattern in (
        f"{stem}.execution",
        f"{stem}.execution-[0-9]*",
        f"{stem}.execution-check-*",
    ):
        for exec_path in sorted(invocations_dir.glob(pattern)):
            resolved_exec = exec_path.resolve()
            if resolved_exec in seen_exec_paths:
                continue
            seen_exec_paths.add(resolved_exec)
            exec_data = _load_sidecar(exec_path)
            if exec_data is None:
                continue
            _verify_activation(exec_path.name)
            exec_plan_ref = _norm_optional_str(exec_data.get("plan_ref"))
            if plan_ref is not None and exec_plan_ref != plan_ref:
                entry["internal_conflicts"].append(
                    {
                        "reason": "hook_execution_plan_ref_mismatch",
                        "artifact": exec_path.name,
                        "expected": plan_ref,
                        "observed": exec_plan_ref,
                    }
                )
            report = exec_data.get("report")
            report_ref_val: str | None = None
            report_digest_verified = False
            run_ref: str | None = None
            attempt_ref: str | None = None
            execution_ref: str | None = None
            bundle_dir: str | None = None
            git_commit: str | None = None
            source_tree_id: str | None = None
            remote_ref: str | None = None
            if isinstance(report, dict):
                actual_digest = hooks._digest_bytes(hooks.canonical_event_bytes(report))
                declared_digest = _norm_optional_str(exec_data.get("report_digest"))
                if declared_digest == actual_digest:
                    report_digest_verified = True
                else:
                    entry["internal_conflicts"].append(
                        {
                            "reason": "hook_execution_report_digest_mismatch",
                            "artifact": exec_path.name,
                            "expected": declared_digest,
                            "observed": actual_digest,
                        }
                    )
                if report.get("kind") == execution.REPORT_KIND:
                    try:
                        _res, _rej, verified_ref = (
                            reconciliation.verify_execution_report(report)
                        )
                        report_ref_val = _report_ref_key(verified_ref)
                    except (
                        reconciliation.ReconciliationError,
                        TypeError,
                        ValueError,
                    ) as exc:
                        report_digest_verified = False
                        entry["internal_conflicts"].append(
                            {
                                "reason": "hook_execution_report_invalid",
                                "artifact": exec_path.name,
                                "error": str(exc),
                            }
                        )
                ident = report.get("execution_identity")
                if isinstance(ident, dict):
                    run_ref = _norm_optional_str(ident.get("run_ref"))
                    attempt_ref = _norm_optional_str(ident.get("attempt_ref"))
                    execution_ref = _norm_optional_str(ident.get("execution_ref"))
                    if run_ref is not None and run_ref != expected_run:
                        entry["internal_conflicts"].append(
                            {
                                "reason": "hook_execution_run_ref_mismatch",
                                "artifact": exec_path.name,
                                "expected": expected_run,
                                "observed": run_ref,
                            }
                        )
                    if attempt_ref is not None and attempt_ref != expected_att:
                        entry["internal_conflicts"].append(
                            {
                                "reason": "hook_execution_attempt_ref_mismatch",
                                "artifact": exec_path.name,
                                "expected": expected_att,
                                "observed": attempt_ref,
                            }
                        )
                plan_git = (report.get("plan") or {}).get("git")
                if isinstance(plan_git, dict):
                    git_commit = _norm_optional_str(plan_git.get("head_commit"))
                    source_tree_id = _norm_optional_str(plan_git.get("source_tree_id"))
                    remote_ref = _norm_optional_str(plan_git.get("remote_ref"))
                raw_dagger_dir = _norm_optional_str(report.get("dagger_bundle"))
                if raw_dagger_dir is not None:
                    with suppress(OSError):
                        bundle_dir = Path(raw_dagger_dir).resolve().as_posix()
            else:
                entry["internal_conflicts"].append(
                    {
                        "reason": "hook_execution_report_missing",
                        "artifact": exec_path.name,
                    }
                )
            executions.append(
                {
                    "artifact": exec_path.name,
                    "scope": exec_path.suffix.lstrip("."),
                    "plan_ref": exec_plan_ref,
                    "plan_scope": plan_scope,
                    "remote_ref": remote_ref,
                    "run_ref": run_ref,
                    "attempt_ref": attempt_ref,
                    "execution_ref": execution_ref,
                    "report_ref": report_ref_val,
                    "report_digest_verified": report_digest_verified,
                    "bundle_directory": bundle_dir,
                    "git_commit": git_commit,
                    "source_tree_id": source_tree_id,
                    "exit_code": report.get("exit_code")
                    if isinstance(report, dict)
                    else None,
                }
            )

    receipt_path = invocations_dir / f"{stem}.receipt"
    receipt_summary: dict[str, Any] | None = None
    if receipt_path.exists():
        receipt_data = _load_sidecar(receipt_path)
        if receipt_data is not None:
            receipt_plan_ref = _norm_optional_str(receipt_data.get("plan_ref"))
            if (
                receipt_plan_ref is not None
                and plan_ref is not None
                and receipt_plan_ref != plan_ref
            ):
                entry["internal_conflicts"].append(
                    {
                        "reason": "hook_receipt_plan_ref_mismatch",
                        "expected": plan_ref,
                        "observed": receipt_plan_ref,
                    }
                )
            receipt_report_refs: list[str] = []
            validation_status = "valid"
            native_receipt_status: str | None = None

            def _scan_receipt_obj(obj: dict[str, Any]) -> None:
                exp_inv = _norm_optional_str(obj.get("expected_invocation_ref"))
                if exp_inv is not None and exp_inv != binding.invocation_ref:
                    entry["internal_conflicts"].append(
                        {
                            "reason": "hook_receipt_invocation_ref_mismatch",
                            "expected": binding.invocation_ref,
                            "observed": exp_inv,
                        }
                    )
                direct_rref = _report_ref_key(obj.get("report_ref"))
                if direct_rref:
                    receipt_report_refs.append(direct_rref)
                for seq_key in ("entries", "results", "references"):
                    seq = obj.get(seq_key)
                    if not isinstance(seq, list):
                        continue
                    for item in seq:
                        if not isinstance(item, dict):
                            continue
                        rr = _report_ref_key(item.get("report_ref"))
                        if rr:
                            receipt_report_refs.append(rr)
                        for sub_key in (
                            "evidence",
                            "job_instances",
                            "rejected_observations",
                        ):
                            sub = item.get(sub_key)
                            if isinstance(sub, list):
                                for sub_item in sub:
                                    if isinstance(sub_item, dict):
                                        srr = _report_ref_key(
                                            sub_item.get("report_ref")
                                        )
                                        if srr:
                                            receipt_report_refs.append(srr)
                sub_receipts = obj.get("receipts")
                if isinstance(sub_receipts, list):
                    for sub_rec in sub_receipts:
                        if isinstance(sub_rec, dict):
                            _scan_receipt_obj(sub_rec)

            def _validate_reconciliation_receipt(rec_obj: dict[str, Any]) -> bool:
                try:
                    reconciliation.validate_receipt(rec_obj)
                    return True
                except reconciliation.ReconciliationError as exc:
                    entry["internal_conflicts"].append(
                        {
                            "reason": "hook_receipt_validation_failed",
                            "error": str(exc),
                        }
                    )
                    return False

            is_fallback_exit_only = (
                plan_payload_obj is None
                and has_plan_key
                and "kind" not in receipt_data
                and "receipts" not in receipt_data
                and "status" not in receipt_data
                and "exit_code" in receipt_data
            )
            if is_fallback_exit_only:
                validation_status = "fallback"
                native_receipt_status = "fallback_exit_only"
                if (
                    receipt_data.get("run_ref") != expected_run
                    or receipt_data.get("attempt_ref") != expected_att
                ):
                    entry["internal_conflicts"].append(
                        {
                            "reason": "hook_receipt_fallback_identity_mismatch",
                            "expected_run_ref": expected_run,
                            "expected_attempt_ref": expected_att,
                        }
                    )
                if not executions:
                    entry["internal_conflicts"].append(
                        {"reason": "hook_receipt_executions_missing"}
                    )
                elif receipt_data.get("exit_code") != executions[0].get("exit_code"):
                    entry["internal_conflicts"].append(
                        {
                            "reason": "hook_receipt_fallback_exit_mismatch",
                            "expected": executions[0].get("exit_code"),
                            "observed": receipt_data.get("exit_code"),
                        }
                    )
            elif "receipts" in receipt_data:
                sub_receipts = receipt_data.get("receipts")
                if not isinstance(sub_receipts, list):
                    validation_status = "invalid"
                    entry["internal_conflicts"].append(
                        {"reason": "hook_receipt_wrapper_invalid"}
                    )
                else:
                    if isinstance(plan_payload_obj, list) and len(sub_receipts) != len(
                        plan_payload_obj
                    ):
                        validation_status = "invalid"
                        entry["internal_conflicts"].append(
                            {
                                "reason": "hook_receipt_ref_count_mismatch",
                                "expected": len(plan_payload_obj),
                                "observed": len(sub_receipts),
                            }
                        )
                    sub_statuses: list[str] = []
                    for sub_rec in sub_receipts:
                        if not _validate_reconciliation_receipt(sub_rec):
                            validation_status = "invalid"
                        if isinstance(sub_rec, dict) and isinstance(
                            sub_rec.get("status"), str
                        ):
                            sub_statuses.append(sub_rec["status"])
                    if not sub_receipts and binding.hook == "pre-push":
                        native_receipt_status = (
                            "matched" if not push_refs else "partial"
                        )
                    elif sub_statuses and all(s == "matched" for s in sub_statuses):
                        native_receipt_status = "matched"
                    elif sub_statuses and len(set(sub_statuses)) == 1:
                        native_receipt_status = sub_statuses[0]
                    else:
                        native_receipt_status = "partial"
            else:
                if not _validate_reconciliation_receipt(receipt_data):
                    validation_status = "invalid"
                native_receipt_status = (
                    str(receipt_data.get("status"))
                    if receipt_data.get("status") is not None
                    else None
                )

            _scan_receipt_obj(receipt_data)
            valid_exec_report_refs = {
                ex["report_ref"]
                for ex in executions
                if ex.get("report_ref") and ex.get("report_digest_verified")
            }
            missing_exec_for_receipt = False
            # Always check receipt report references, including when executions == [].
            for rr in receipt_report_refs:
                if rr not in valid_exec_report_refs:
                    missing_exec_for_receipt = True
                    entry["internal_conflicts"].append(
                        {
                            "reason": "hook_receipt_report_ref_not_in_executions",
                            "observed": rr,
                        }
                    )
            if (
                native_receipt_status == "matched"
                and not executions
                and (binding.hook != "pre-push" or bool(push_refs))
            ):
                missing_exec_for_receipt = True
                entry["internal_conflicts"].append(
                    {"reason": "hook_receipt_executions_missing"}
                )

            has_integrity_conflict = any(
                c.get("reason", "").startswith(
                    (
                        "hook_execution_",
                        "hook_activation_",
                        "hook_receipt_",
                        "hook_plan_",
                        "hook_sidecar_",
                    )
                )
                for c in entry["internal_conflicts"]
            )
            if missing_exec_for_receipt:
                verifiable_evidence_status = "missing_executions"
            elif validation_status == "invalid" or has_integrity_conflict:
                verifiable_evidence_status = "mismatch_or_invalid"
            elif native_receipt_status in ("matched", "fallback_exit_only"):
                verifiable_evidence_status = "verified"
            else:
                verifiable_evidence_status = "partial_or_unverified"

            receipt_summary = {
                "status": native_receipt_status,
                "native_status": native_receipt_status,
                "validation_status": validation_status,
                "verifiable_evidence_status": verifiable_evidence_status,
                "receipt_ref": receipt_data.get("receipt_ref"),
                "plan_ref": receipt_plan_ref,
                "report_refs": sorted(set(receipt_report_refs)),
                "exit_code": receipt_data.get("exit_code"),
            }

    sidecar_stages: list[dict[str, Any]] = []
    for sidecar in sorted(invocations_dir.glob(f"{stem}.timing-*")):
        stage_data = _load_sidecar(sidecar)
        if stage_data is None:
            continue
        stage_plan_ref = _norm_optional_str(stage_data.get("plan_ref"))
        if (
            stage_plan_ref is not None
            and plan_ref is not None
            and stage_plan_ref != plan_ref
        ):
            entry["internal_conflicts"].append(
                {
                    "reason": "hook_timing_plan_ref_mismatch",
                    "artifact": sidecar.name,
                    "expected": plan_ref,
                    "observed": stage_plan_ref,
                }
            )
        sidecar_stages.append(
            {
                "artifact": sidecar.name,
                "hook": stage_data.get("hook") or stage_data.get("hook_name"),
                "stage": stage_data.get("stage"),
                "job_ref": stage_data.get("job_ref"),
                "status": stage_data.get("status"),
                "elapsed_seconds": stage_data.get("elapsed_seconds"),
                "started_at_ns": stage_data.get("started_at_ns"),
                "completed_at_ns": stage_data.get("completed_at_ns"),
                "attempt_count": stage_data.get("attempt_count"),
                "duplicate_attempts": stage_data.get("duplicate_attempts"),
                "primary_record_policy": stage_data.get("primary_record_policy"),
                "per_ref": stage_data.get("per_ref"),
            }
        )

    git_commits: list[str] = []
    if binding.hook == "pre-commit":
        hc = _norm_optional_str(event.get("head_commit"))
        if hc:
            git_commits.append(hc)
    elif binding.hook == "pre-push":
        for ref_item in event.get("refs") or ():
            if isinstance(ref_item, dict):
                after = _norm_optional_str(ref_item.get("after"))
                if after and after not in git_commits:
                    git_commits.append(after)

    source_tree_ids: list[str] = []
    if isinstance(plan_payload_obj, dict):
        st = _norm_optional_str(
            (plan_payload_obj.get("git") or {}).get("source_tree_id")
        )
        if st:
            source_tree_ids.append(st)
    elif isinstance(plan_payload_obj, list):
        for p_item in plan_payload_obj:
            if isinstance(p_item, dict):
                st = _norm_optional_str((p_item.get("git") or {}).get("source_tree_id"))
                if st and st not in source_tree_ids:
                    source_tree_ids.append(st)

    entry.update(
        {
            "status": "loaded",
            "invocation_ref": binding.invocation_ref,
            "event_payload_ref": binding.event_payload_ref,
            "hook": binding.hook,
            "event_file": str(binding.event_path),
            "plan_ref": plan_ref,
            "git_commits": git_commits,
            "source_tree_ids": source_tree_ids,
            "push_references": push_refs,
            "executions": executions,
            "receipt": receipt_summary,
            "stage_timings": sidecar_stages,
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
    for case in root.findall(".//testcase"):
        case_props = case.findall("./properties/property")
        if case_props:
            testcases_with_props += 1
        for prop in case_props:
            name = (prop.attrib.get("name") or "").strip()
            val = (prop.attrib.get("value") or "").strip()
            if name and val:
                testcase_prop_names.add(name)

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
        },
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

    suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    total_time = 0.0
    has_valid_suite_time = False
    invalid_durations = 0
    missing_durations = 0

    for suite in suites:
        for attr in ("tests", "failures", "errors", "skipped"):
            raw_num = suite.attrib.get(attr)
            if raw_num is not None and raw_num.isdigit():
                totals[attr] += int(raw_num)
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

    cases: list[dict[str, Any]] = []
    numeric_cases: list[dict[str, Any]] = []
    for case in root.findall(".//testcase"):
        parsed_dur, _ = _parse_valid_nonneg_float(case.attrib.get("time"))
        if parsed_dur == "invalid":
            invalid_durations += 1
        elif parsed_dur == "unknown":
            missing_durations += 1
        outcome = "passed"
        if case.find("failure") is not None:
            outcome = "failed"
        elif case.find("error") is not None:
            outcome = "error"
        elif case.find("skipped") is not None:
            outcome = "skipped"
        item = {
            "classname": case.attrib.get("classname", ""),
            "name": case.attrib.get("name", ""),
            "status": outcome,
            "duration_seconds": parsed_dur,
        }
        cases.append(item)
        if isinstance(parsed_dur, float):
            numeric_cases.append(item)

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
    session_start_ts: float | None = None
    session_finish_ts: float | None = None
    exit_status: int | None = None
    hasher = hashlib.sha256()

    def _consume_line(raw_line: bytes) -> None:
        nonlocal malformed_line_count, invalid_duration_count, cumulative_phase_seconds
        nonlocal has_session_start, has_session_finish, session_start_ts
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
    for props_map in node_user_props.values():
        for pname, vals in props_map.items():
            user_prop_names.add(pname)
            if pname in _CORRELATION_PROP_KEYS:
                user_corr_vals.setdefault(pname, []).extend(vals)
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

    if malformed_line_count and not (has_session_finish or testcase_counts["passed"]):
        completeness = "corrupted"
    elif (
        has_session_finish
        and exit_status is not None
        and not truncated
        and malformed_line_count == 0
        and invalid_duration_count == 0
        and testcase_counts["incomplete"] == 0
        and testcase_counts["repeated_ambiguous"] == 0
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
        "slowest_phases": slowest,
        "properties": prop_values,
        "testcase_properties_summary": {
            "nodes_with_user_properties": len(node_user_props),
            "property_names": sorted(user_prop_names),
        },
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
    internal_conflicts: list[dict[str, Any]],
    consumed_files: list[str],
) -> tuple[dict[str, dict[str, str]], str | None]:
    """Extract verified relative-path -> sha256 bindings from #86 Producer Artifact Handoff / native Report."""
    index: dict[str, dict[str, str]] = {}
    handoff_status: str | None = None

    handoff = report_payload.get("producer_artifact_handoff")
    if handoff is not None:
        if (
            not isinstance(handoff, dict)
            or handoff.get("kind") != execution.PRODUCER_HANDOFF_KIND
        ):
            handoff_status = "invalid"
            internal_conflicts.append({"reason": "bundle_producer_handoff_invalid"})
        else:
            handoff_digest = _norm_optional_str(handoff.get("handoff_digest"))
            payload_without_digest = {
                k: v for k, v in handoff.items() if k != "handoff_digest"
            }
            expected_digest = execution._canonical_sha256(payload_without_digest)
            if handoff_digest != expected_digest:
                handoff_status = "digest_mismatch"
                internal_conflicts.append(
                    {
                        "reason": "bundle_producer_handoff_digest_mismatch",
                        "expected": expected_digest,
                        "observed": handoff_digest,
                    }
                )
            else:
                handoff_status = "verified"
            h_plan_ref = _norm_optional_str(handoff.get("plan_ref"))
            if h_plan_ref and meta.get("plan_ref") and h_plan_ref != meta["plan_ref"]:
                handoff_status = "identity_mismatch"
                internal_conflicts.append(
                    {
                        "reason": "bundle_producer_handoff_plan_mismatch",
                        "expected": meta["plan_ref"],
                        "observed": h_plan_ref,
                    }
                )
            h_ident = handoff.get("execution_identity")
            if isinstance(h_ident, dict):
                for key in ("run_ref", "attempt_ref", "execution_ref"):
                    hv = _norm_optional_str(h_ident.get(key))
                    mv = meta.get(key)
                    if hv and mv and hv != mv:
                        handoff_status = "identity_mismatch"
                        internal_conflicts.append(
                            {
                                "reason": f"bundle_producer_handoff_{key}_mismatch",
                                "expected": mv,
                                "observed": hv,
                            }
                        )
            for rec in handoff.get("artifacts") or ():
                if not isinstance(rec, dict):
                    continue
                bpath = rec.get("bundle_path")
                if not isinstance(bpath, str) or not bpath:
                    continue
                file_digests = rec.get("file_digests")
                if isinstance(file_digests, dict) and file_digests:
                    for rel, d_val in file_digests.items():
                        sha = _norm_sha256_value(d_val)
                        if isinstance(rel, str) and sha:
                            rel_posix = (Path(bpath) / Path(rel)).as_posix()
                            index[rel_posix] = {"sha256": sha}
                            child = _safe_child_path(bundle_dir, rel_posix)
                            if child is None or not child.is_file():
                                handoff_status = "artifact_missing"
                                internal_conflicts.append(
                                    {
                                        "reason": "bundle_produced_artifact_missing",
                                        "path": rel_posix,
                                    }
                                )
                            else:
                                consumed_files.append(str(child))
                                obs_sha = _stream_sha256(child)
                                if obs_sha != sha:
                                    handoff_status = "artifact_digest_mismatch"
                                    internal_conflicts.append(
                                        {
                                            "reason": "bundle_artifact_sha256_mismatch",
                                            "path": rel_posix,
                                            "expected": sha,
                                            "observed": obs_sha,
                                        }
                                    )
                else:
                    sha = _norm_sha256_value(rec.get("sha256"))
                    rel_posix = Path(bpath).as_posix()
                    if sha and (
                        rec.get("kind") == "file" or not rec.get("materialized_files")
                    ):
                        index[rel_posix] = {"sha256": sha}
                        child = _safe_child_path(bundle_dir, rel_posix)
                        if child is None or not child.is_file():
                            handoff_status = "artifact_missing"
                            internal_conflicts.append(
                                {
                                    "reason": "bundle_produced_artifact_missing",
                                    "path": rel_posix,
                                }
                            )
                        else:
                            consumed_files.append(str(child))
                            obs_sha = _stream_sha256(child)
                            if obs_sha != sha:
                                handoff_status = "artifact_digest_mismatch"
                                internal_conflicts.append(
                                    {
                                        "reason": "bundle_artifact_sha256_mismatch",
                                        "path": rel_posix,
                                        "expected": sha,
                                        "observed": obs_sha,
                                    }
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

    return index, handoff_status


def _extract_execution_metadata(
    payload: dict[str, Any], *, internal_conflicts: list[dict[str, Any]]
) -> dict[str, Any]:
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

    return {
        "run_ref": run_ref,
        "attempt_ref": attempt_ref,
        "execution_ref": execution_ref,
        "report_ref": _compute_execution_report_ref_key(payload),
        "plan_ref": _norm_optional_str(payload.get("plan_ref")),
        "git_commit": git_commit,
        "source_tree_id": source_tree_id,
    }


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
        plan_candidate = bundle_dir / "plan.json"
        if plan_candidate.exists():
            consumed_files.append(str(plan_candidate))
            loaded_plan = _load_bounded_json(plan_candidate)
            if loaded_plan.get("status") == "loaded":
                plan_payload = loaded_plan["payload"]
                plan_ref = hooks._digest_bytes(
                    hooks.canonical_event_bytes(plan_payload)
                )
            else:
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
        handoff_status: str | None = None
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
                report_payload, internal_conflicts=internal_conflicts
            )
            if plan_ref is not None and meta.get("plan_ref") is None:
                meta["plan_ref"] = plan_ref
            if isinstance(plan_payload, dict):
                plan_git = plan_payload.get("git")
                rep_git = (report_payload.get("plan") or {}).get("git")
                if (
                    isinstance(plan_git, dict)
                    and isinstance(rep_git, dict)
                    and plan_git != rep_git
                ):
                    internal_conflicts.append({"reason": "bundle_plan_git_mismatch"})
                if isinstance(plan_git, dict) and meta.get("git_commit") is None:
                    meta["git_commit"] = _norm_optional_str(plan_git.get("head_commit"))
                if isinstance(plan_git, dict) and meta.get("source_tree_id") is None:
                    meta["source_tree_id"] = _norm_optional_str(
                        plan_git.get("source_tree_id")
                    )

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

            manifest_index, handoff_status = _build_bundle_manifest_index(
                report_payload,
                bundle_dir=bundle_dir,
                meta=meta,
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
                    "bundle_directory": bundle_dir.as_posix(),
                    "status": "loaded",
                    "execution_status": report_payload.get("status"),
                    "exit_code": report_payload.get("exit_code"),
                    "elapsed_seconds": report_payload.get("duration_seconds")
                    or report_payload.get("elapsed_seconds"),
                    **meta,
                    "producer_artifact_handoff_status": handoff_status,
                    "sub_reports": sub_reports,
                    "internal_conflicts": internal_conflicts,
                    "consumed_files": list(dict.fromkeys(consumed_files)),
                    "consumed_dirs": list(consumed_dirs),
                }
            )
        else:
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
            if (
                rel_name.endswith("core-junit.xml")
                and isinstance(sha, str)
                and sha in seen_test_shas
            ):
                seen_test_shas[sha].setdefault("alias_paths", []).append(rel_name)
                if entries and str(child) not in entries[0]["consumed_files"]:
                    entries[0]["consumed_files"].append(str(child))
                continue
            manifest_rec = manifest_index.get(rel_name)
            digest_verified = False
            if manifest_rec is not None:
                expected_sha = manifest_rec.get("sha256")
                if isinstance(sha, str) and sha == expected_sha:
                    digest_verified = True
                else:
                    parsed["internal_conflicts"].append(
                        {
                            "reason": "bundle_artifact_sha256_mismatch",
                            "path": rel_name,
                            "expected": expected_sha,
                            "observed": sha,
                        }
                    )
            if digest_verified:
                # Only concrete native digest bindings (handoff or embedded report junit) prove bundle membership.
                parsed["bundle_binding"] = "verified_manifest_digest"
                parsed["bound_bundle_directory"] = bundle_dir.as_posix()
                parsed["bound_artifact_digest"] = sha
            else:
                parsed["bundle_binding"] = "colocated_unverified"
                parsed["colocated_directory"] = bundle_dir.as_posix()
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
    meta = _extract_execution_metadata(payload, internal_conflicts=internal_conflicts)
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
    return {
        "source_type": "dagger_telemetry",
        "path": str(path),
        "status": "loaded",
        "correlation": native_corr,
        "source_binding": native_sb,
        "cache_observation": payload.get("cache_observation"),
        "origin_execution": payload.get("origin_execution"),
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
        "declared_target_source_ref": declared_source_ref,
        "run_ref": _norm_optional_str(payload.get("run_ref")),
        "attempt_ref": _norm_optional_str(payload.get("attempt_ref")),
        "execution_ref": _norm_optional_str(payload.get("execution_ref")),
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
    run_id = payload.get("run_id")
    run_attempt = payload.get("run_attempt")
    attempt_int = run_attempt if type(run_attempt) is int else None
    rerun_attribution = (
        "unpartitioned_attempt_jobs_snapshot"
        if attempt_int is not None and attempt_int > 1
        else "attempt_1_or_unverified"
    )
    return {
        "source_type": "ci_performance",
        "path": str(path),
        "status": "loaded",
        "ci_run_id": _norm_optional_str(run_id),
        "ci_run_attempt": _norm_optional_str(run_attempt),
        "git_commit": _norm_optional_str(payload.get("sha")),
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
        "jobs": payload.get("jobs"),
        "dagger": payload.get("dagger"),
        "internal_conflicts": [],
        "consumed_files": [str(path)],
    }


def _source_identities(src: dict[str, Any]) -> dict[str, Any]:
    """Extract scoped concrete execution, upper context, content, and revision identifiers."""
    stype = src["source_type"]
    concrete_keys: dict[str, set[str]] = {
        "execution_ref": set(),
        "report_ref": set(),
        "bundle_directory": set(),
        "artifact_digest": set(),
        "ci_attempt_pair": set(),
    }
    context_keys: dict[str, set[str]] = {
        "invocation_ref": set(),
        "run_attempt_pair": set(),
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
    }
    revisions: set[str] = set()

    if stype == "verify":
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
            if ex.get("run_ref") and ex.get("attempt_ref"):
                context_keys["run_attempt_pair"].add(
                    f"{ex['run_ref']}|{ex['attempt_ref']}"
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
        if src.get("run_ref") and src.get("attempt_ref"):
            context_keys["run_attempt_pair"].add(
                f"{src['run_ref']}|{src['attempt_ref']}"
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
        if src.get("git_commit"):
            revisions.add(src["git_commit"])
    elif stype in ("junit_xml", "pytest_reportlog"):
        props = src.get("properties") or {}
        if src.get("bundle_binding") == "verified_manifest_digest":
            if src.get("bound_bundle_directory"):
                concrete_keys["bundle_directory"].add(src["bound_bundle_directory"])
            if src.get("bound_artifact_digest"):
                concrete_keys["artifact_digest"].add(src["bound_artifact_digest"])
        rref = _norm_optional_str(props.get("run_ref"))
        aref = _norm_optional_str(props.get("attempt_ref"))
        if rref and aref:
            context_keys["run_attempt_pair"].add(f"{rref}|{aref}")
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
        bound = src.get("native_correlation_status") == "bound"
        if bound and src.get("matched_trace_id"):
            tid = src["matched_trace_id"]
            context_keys["matched_trace_id"].add(tid)
            if src.get("anchor_span_id"):
                context_keys["trace_anchor_span"].add(
                    f"{tid}|{src.get('matched_session_id') or ''}|{src.get('matched_engine_instance_id') or ''}|{src['anchor_span_id']}"
                )
            if src.get("dagql_span_id"):
                context_keys["trace_dagql_span"].add(f"{tid}|{src['dagql_span_id']}")
            if src.get("core_span_id"):
                context_keys["trace_core_span"].add(f"{tid}|{src['core_span_id']}")
        if bound and src.get("run_ref") and src.get("attempt_ref"):
            context_keys["run_attempt_pair"].add(
                f"{src['run_ref']}|{src['attempt_ref']}"
            )
        if bound and src.get("execution_ref"):
            concrete_keys["execution_ref"].add(src["execution_ref"])
        if src.get("declared_target_source_ref"):
            # Declared source ref is not verified Tree equivalence until #87 B delivers.
            content_keys["declared_target_source_ref"].add(
                src["declared_target_source_ref"]
            )
        if src.get("git_commit"):
            revisions.add(src["git_commit"])
    elif stype == "ci_performance":
        if src.get("ci_run_id") and src.get("ci_run_attempt"):
            concrete_keys["ci_attempt_pair"].add(
                f"{src['ci_run_id']}|{src['ci_run_attempt']}"
            )
        if src.get("git_commit"):
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
    """Detect conflicts only within the exact matching object/target scope."""
    conflicts: list[dict[str, Any]] = []
    anchors: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for src in sources:
        if src.get("status") not in ("loaded", "completed"):
            continue
        stype = src["source_type"]
        if stype in ("execution_bundle", "execution_report"):
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
            for ex in src.get("executions") or ():
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
                        ("run_attempt_binding", f"{ex['run_ref']}|{ex['attempt_ref']}"),
                        [],
                    ).append(hook_exec)
                    if ex.get("remote_ref"):
                        anchors.setdefault(
                            (
                                "push_ref_target",
                                f"{ex['run_ref']}|{ex['attempt_ref']}|{ex['remote_ref']}",
                            ),
                            [],
                        ).append(hook_exec)
        elif (
            stype == "dagger_telemetry"
            and src.get("native_correlation_status") == "bound"
        ):
            if src.get("execution_ref"):
                anchors.setdefault(("execution_ref", src["execution_ref"]), []).append(
                    src
                )
        elif (
            stype == "ci_performance"
            and src.get("ci_run_id")
            and src.get("ci_run_attempt")
        ):
            anchors.setdefault(
                ("ci_attempt", f"{src['ci_run_id']}|{src['ci_run_attempt']}"), []
            ).append(src)

    # Note: run_attempt does NOT force single commit/tree/plan because one pre-push
    # invocation legitimately targets multiple refs with distinct trees and plans.
    anchor_invariants = {
        "execution_ref": ("exit_code", "git_commit", "source_tree_id", "report_ref"),
        "bundle_directory": ("exit_code", "git_commit", "source_tree_id", "report_ref"),
        "push_ref_target": ("exit_code", "git_commit", "source_tree_id"),
        "run_attempt_binding": ("invocation_ref",),
        "ci_attempt": ("git_commit",),
    }
    for (anchor_kind, anchor_val), group in anchors.items():
        if len(group) < 2:
            continue
        for field in anchor_invariants.get(anchor_kind, ()):
            vals = {
                item[field]
                for item in group
                if field in item and item[field] is not None
            }
            if len(vals) > 1:
                conflicts.append(
                    {
                        "anchor": {anchor_kind: anchor_val},
                        "field": field,
                        "values": sorted(vals),
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
                            "values": sorted(pvals),
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


def _evaluate_correlations(sources: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify per-source relationships across concrete-execution/context/content/revision tiers."""
    identities = [_source_identities(src) for src in sources]
    scoped_conflicts = _check_scoped_cross_conflicts(sources)

    exec_edges: dict[tuple[int, int], dict[str, list[str]]] = {}
    ctx_edges: dict[tuple[int, int], dict[str, list[str]]] = {}
    for i in range(len(sources)):
        for j in range(i + 1, len(sources)):
            eshared: dict[str, list[str]] = {}
            for key, vals in identities[i]["execution"].items():
                inter = sorted(vals & identities[j]["execution"][key])
                if inter:
                    eshared[key] = inter
            if eshared:
                exec_edges[(i, j)] = eshared
            cshared: dict[str, list[str]] = {}
            for key, vals in identities[i]["context"].items():
                inter = sorted(vals & identities[j]["context"][key])
                if inter:
                    cshared[key] = inter
            if cshared:
                ctx_edges[(i, j)] = cshared

    raw_exec_components = _connected_components(len(sources), exec_edges)
    execution_groups: list[dict[str, Any]] = []
    idx_to_exec_group: dict[int, int] = {}
    for comp in raw_exec_components:
        if len(comp) < 2:
            continue
        gidx = len(execution_groups)
        group_keys: dict[str, set[str]] = {}
        for i in comp:
            idx_to_exec_group[i] = gidx
            for j in comp:
                if i < j and (i, j) in exec_edges:
                    for k, vals in exec_edges[(i, j)].items():
                        group_keys.setdefault(k, set()).update(vals)
        execution_groups.append(
            {
                "group_index": gidx,
                "source_indices": comp,
                "matched_keys": {k: sorted(v) for k, v in sorted(group_keys.items())},
            }
        )

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

    for idx, src in enumerate(sources):
        mine = identities[idx]
        internal_conflicts = list(src.get("internal_conflicts") or ())
        src_conflicts = [
            *internal_conflicts,
            *[c for c in scoped_conflicts if _anchor_matches_source(c["anchor"], mine)],
        ]

        exec_matches: dict[str, list[str]] = {}
        ctx_matches: dict[str, list[str]] = {}
        content_matches: dict[str, list[str]] = {}
        rev_matches: set[str] = set()

        for jdx, other_ids in enumerate(identities):
            if jdx == idx:
                continue
            for key, vals in mine["execution"].items():
                shared = vals & other_ids["execution"][key]
                if shared:
                    exec_matches.setdefault(key, []).extend(sorted(shared))
            for key, vals in mine["context"].items():
                shared = vals & other_ids["context"][key]
                if shared:
                    ctx_matches.setdefault(key, []).extend(sorted(shared))
            for key, vals in mine["content"].items():
                shared = vals & other_ids["content"][key]
                if shared:
                    content_matches.setdefault(key, []).extend(sorted(shared))
            rev_matches.update(mine["revision"] & other_ids["revision"])

        exec_matches = {k: sorted(set(v)) for k, v in exec_matches.items()}
        ctx_matches = {k: sorted(set(v)) for k, v in ctx_matches.items()}
        content_matches = {k: sorted(set(v)) for k, v in content_matches.items()}
        rev_list = sorted(rev_matches)

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
        elif exec_matches:
            verdict = {
                "strength": "execution_matched",
                "same_execution_proven": True,
                "execution_group_index": idx_to_exec_group.get(idx),
                "execution_keys": exec_matches,
                "context_keys": ctx_matches,
                "content_keys": content_matches,
                "revision_keys": rev_list,
            }
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
                "strength": "content_shared_distinct_execution",
                "same_execution_proven": False,
                "reason": "content_identity_reusable_across_independent_executions",
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
    elif any(s == "shared_context_only" for s in strengths):
        overall = "shared_context_only"
    elif any(s == "content_shared_distinct_execution" for s in strengths):
        overall = "content_shared_distinct_execution"
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
    if (
        "push_ref_target" in anchor
        and anchor["push_ref_target"].rsplit("|", 1)[0]
        in ids["context"]["run_attempt_pair"]
    ):
        return True
    return (
        "ci_attempt" in anchor
        and anchor["ci_attempt"] in ids["execution"]["ci_attempt_pair"]
    )


def build_verify_report(
    *,
    verify_dir: Path | None = None,
    hook_root: Path | None = None,
    invocation_ref: str | None = None,
    hook_invocation: Path | None = None,
    execution_reports: tuple[Path, ...] | list[Path] = (),
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


def _write_report_atomic(out: Path, encoded: str) -> None:
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
    if not any(
        (
            args.verify_dir,
            args.hook_invocation,
            args.hook_root,
            args.invocation_ref,
            args.execution_report,
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
