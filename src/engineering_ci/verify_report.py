"""Bounded Lefthook summary parsing and read-only verification timing correlation."""

from __future__ import annotations

import argparse
import json
import os
import re
import xml.etree.ElementTree as ET
from contextlib import suppress
from pathlib import Path
from typing import Any

from engineering_ci import hooks, reconciliation

MAX_OUTPUT_PARSE_BYTES = 8 * 1024 * 1024
MAX_JSON_REPORT_BYTES = 16 * 1024 * 1024
MAX_XML_REPORT_BYTES = 16 * 1024 * 1024
MAX_REPORTLOG_BYTES = 32 * 1024 * 1024
MAX_REPORTLOG_LINES = 500_000
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

_FILESYSTEM_PATH_KEYS = {"bundle_directory"}
_CORRELATION_KEY_ORDER = (
    "git_commit",
    "verify_invocation_id",
    "invocation_ref",
    "event_payload_ref",
    "run_ref",
    "attempt_ref",
    "ci_run_id",
    "ci_run_attempt",
    "plan_ref",
    "execution_ref",
    "report_ref",
    "bundle_directory",
    "matched_trace_id",
    "declared_target_source_ref",
)


def _read_bounded_text(
    path: Path | None, *, max_bytes: int | None = None
) -> tuple[str, bool, int]:
    """Read UTF-8 text within a strict byte ceiling, keeping head and tail if oversized."""
    if path is None or not path.is_file():
        return "", False, 0
    limit = MAX_OUTPUT_PARSE_BYTES if max_bytes is None else max_bytes
    total_size = path.stat().st_size
    if total_size <= limit:
        raw = path.read_bytes()
        return raw.decode("utf-8", errors="replace"), False, total_size

    head_limit = limit // 4
    tail_limit = limit - head_limit
    with path.open("rb") as stream:
        head = stream.read(head_limit)
        stream.seek(max(0, total_size - tail_limit))
        tail = stream.read(tail_limit)
    if b"\n" in head:
        head = head.rsplit(b"\n", 1)[0]
    if b"\n" in tail:
        tail = tail.split(b"\n", 1)[-1]
    combined = (
        head.decode("utf-8", errors="replace")
        + "\n"
        + tail.decode("utf-8", errors="replace")
    )
    return combined, True, total_size


def _parse_seconds_label(raw: str | None) -> tuple[float | str, float | None]:
    if not raw:
        return "unknown", None
    match = _DURATION_SECONDS_RE.match(raw.strip())
    if not match:
        return "unknown", None
    return round(float(match.group(1)), 2), 0.01


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
) -> dict[str, Any]:
    cfg = cfg or {}
    return {
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


def _find_or_create_path(
    roots: list[dict[str, Any]],
    parts: list[str],
    config_map: dict[tuple[str, ...], dict[str, Any]],
    *,
    default_status: str,
    skip_reason: str | None = None,
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
            leaves.append(
                {
                    "name": node["name"],
                    "path": list(node["path"]),
                    "status": node["status"],
                    "duration_seconds": node["duration_seconds"],
                    "duration_precision_seconds": node["duration_precision_seconds"],
                    "skip_reason": node["skip_reason"],
                }
            )
    return leaves


def parse_lefthook_output(
    stdout_path: Path | None,
    stderr_path: Path | None = None,
    *,
    verify_config: dict[str, Any] | None = None,
    native_exit_code: int | None = None,
) -> dict[str, Any]:
    """Parse bounded Lefthook v2.2.2 output into hierarchical job/group timings."""
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
    if not summary_present:
        for path in unresolved_declared_jobs:
            _find_or_create_path(
                items, list(path), config_map, default_status="unknown"
            )
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
    if not path.is_file():
        return {"status": "missing", "path": str(path)}
    limit = MAX_JSON_REPORT_BYTES if max_bytes is None else max_bytes
    size = path.stat().st_size
    if size > limit:
        return {
            "status": "oversized",
            "path": str(path),
            "bytes": size,
            "max_bytes": limit,
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"status": "invalid", "path": str(path), "error": str(exc)}
    if not isinstance(payload, dict):
        return {
            "status": "invalid",
            "path": str(path),
            "error": "expected JSON object",
        }
    return {"status": "loaded", "path": str(path), "payload": payload}


def _canonical_key_value(key: str, raw: Any) -> str | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (dict, list)):
        if not raw:
            return None
        return json.dumps(raw, sort_keys=True, separators=(",", ":"))
    text = str(raw).strip()
    if not text:
        return None
    if key in _FILESYSTEM_PATH_KEYS:
        try:
            return Path(text).resolve().as_posix()
        except OSError:
            normalized = os.path.normpath(text.replace("\\", "/")).replace("\\", "/")
            return Path(normalized).as_posix() if "/" in normalized else normalized
    return text


def _extract_keys_from_mapping(
    mapping: dict[str, Any], aliases: dict[str, str], out: dict[str, str]
) -> None:
    for src_key, canon_key in aliases.items():
        if src_key in mapping:
            value = _canonical_key_value(canon_key, mapping.get(src_key))
            if value is not None and canon_key not in out:
                out[canon_key] = value


_COMMON_KEY_ALIASES = {
    "git_commit": "git_commit",
    "observed_git_head": "git_commit",
    "head_commit": "git_commit",
    "head_sha": "git_commit",
    "sha": "git_commit",
    "commit": "git_commit",
    "after": "git_commit",
    "verify_invocation_id": "verify_invocation_id",
    "invocation_ref": "invocation_ref",
    "hook_invocation_ref": "invocation_ref",
    "event_payload_ref": "event_payload_ref",
    "run_ref": "run_ref",
    "attempt_ref": "attempt_ref",
    "run_id": "ci_run_id",
    "run_attempt": "ci_run_attempt",
    "plan_ref": "plan_ref",
    "execution_ref": "execution_ref",
    "parent_execution_ref": "execution_ref",
    "report_ref": "report_ref",
    "bundle_directory": "bundle_directory",
    "bundle_dir": "bundle_directory",
    "dagger_bundle": "bundle_directory",
    "trace_id": "matched_trace_id",
    "matched_trace_id": "matched_trace_id",
    "declared_target_source_ref": "declared_target_source_ref",
    "source_tree_id": "declared_target_source_ref",
    "exported_content_id": "declared_target_source_ref",
    "source_digest": "declared_target_source_ref",
}


def _collect_nested_keys(obj: Any, out: dict[str, str], *, depth: int = 0) -> None:
    if depth > 4 or not isinstance(obj, dict):
        return
    _extract_keys_from_mapping(obj, _COMMON_KEY_ALIASES, out)
    refs = obj.get("refs")
    if isinstance(refs, list) and len(refs) == 1 and isinstance(refs[0], dict):
        _extract_keys_from_mapping(refs[0], _COMMON_KEY_ALIASES, out)
    for nested_key in (
        "correlation",
        "source_binding",
        "source_handoff",
        "original_git",
        "execution",
        "execution_identity",
        "report",
        "plan",
        "git",
        "event",
        "receipt",
        "provenance",
        "metadata",
        "target",
        "context",
    ):
        nested = obj.get(nested_key)
        if isinstance(nested, dict):
            _collect_nested_keys(nested, out, depth=depth + 1)


def _load_verify_entry(verify_dir: Path) -> dict[str, Any]:
    timing_path = verify_dir / "verify-timing.json"
    status_path = verify_dir / "verify-status.json"
    entry: dict[str, Any] = {
        "source_type": "verify",
        "path": str(verify_dir),
        "keys": {},
    }
    timing = _load_bounded_json(timing_path) if timing_path.is_file() else None
    status = _load_bounded_json(status_path) if status_path.is_file() else None
    chosen = timing if timing and timing.get("status") == "loaded" else status
    if chosen is None:
        missing = timing or status or {"status": "missing", "path": str(timing_path)}
        entry["status"] = missing["status"]
        if "error" in missing:
            entry["error"] = missing["error"]
        return entry
    if chosen.get("status") != "loaded":
        entry["status"] = chosen["status"]
        entry["artifact"] = chosen["path"]
        return entry

    payload = chosen["payload"]
    keys: dict[str, str] = {}
    if isinstance(payload.get("invocation_id"), str) and payload["invocation_id"]:
        keys["verify_invocation_id"] = payload["invocation_id"]
    _collect_nested_keys(payload, keys)
    entry.update(
        {
            "status": "completed"
            if chosen is timing
            else str(payload.get("status", "incomplete")),
            "receipt_kind": "verify-timing.json"
            if chosen is timing
            else "verify-status.json",
            "receipt_path": chosen["path"],
            "invocation_id": payload.get("invocation_id"),
            "native_exit_code": payload.get("native_exit_code"),
            "elapsed_seconds": payload.get("elapsed_seconds"),
            "stages": payload.get("stages"),
            "started_at_ns": payload.get("started_at_ns"),
            "completed_at_ns": payload.get("completed_at_ns"),
            "time_source_verified": bool(payload.get("time_source_verified", False)),
            "lefthook_summary": payload.get("lefthook_summary"),
            "keys": keys,
        }
    )
    return entry


def _resolve_explicit_hook_binding(
    *, hook_root: Path | None, invocation_ref: str | None, hook_invocation: Path | None
) -> Path | None:
    if hook_invocation is not None:
        return hook_invocation
    if hook_root is None or not invocation_ref:
        return None
    ref_path = Path(invocation_ref)
    if ref_path.is_absolute() and ref_path.is_file():
        return ref_path
    candidates = [
        hook_root / invocation_ref,
        hook_root / "invocations" / f"{invocation_ref}.json",
        hook_root / "eci-hooks" / "invocations" / f"{invocation_ref}.json",
        hook_root / ".git" / "eci-hooks" / "invocations" / f"{invocation_ref}.json",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[2] if (hook_root / "eci-hooks").is_dir() else candidates[1]


def _load_hook_entry(
    *,
    hook_root: Path | None = None,
    invocation_ref: str | None = None,
    hook_invocation: Path | None = None,
) -> dict[str, Any]:
    binding_path = _resolve_explicit_hook_binding(
        hook_root=hook_root,
        invocation_ref=invocation_ref,
        hook_invocation=hook_invocation,
    )
    entry: dict[str, Any] = {
        "source_type": "hook_invocation",
        "path": str(binding_path) if binding_path is not None else None,
        "keys": {},
    }
    if binding_path is None:
        entry["status"] = "missing_arguments"
        entry["error"] = (
            "require --hook-invocation or both --hook-root and --invocation-ref"
        )
        return entry
    binding = _load_bounded_json(binding_path)
    if binding.get("status") != "loaded":
        entry["status"] = binding["status"]
        if "error" in binding:
            entry["error"] = binding["error"]
        return entry

    payload = binding["payload"]
    keys: dict[str, str] = {}
    _collect_nested_keys(payload, keys)
    stem = binding_path.stem
    if "invocation_ref" not in keys:
        keys["invocation_ref"] = str(binding_path)
    invocations_dir = binding_path.parent

    event_payload_ref = payload.get("event_payload_ref")
    if isinstance(event_payload_ref, str) and event_payload_ref:
        event_loaded = _load_bounded_json(Path(event_payload_ref))
        if event_loaded.get("status") == "loaded":
            _collect_nested_keys(event_loaded["payload"], keys)

    sidecar_stages: list[dict[str, Any]] = []
    for sidecar in sorted(invocations_dir.glob(f"{stem}.timing-*")):
        loaded = _load_bounded_json(sidecar)
        if loaded.get("status") != "loaded":
            sidecar_stages.append(
                {"artifact": sidecar.name, "status": loaded["status"]}
            )
            continue
        stage_data = loaded["payload"]
        _collect_nested_keys(stage_data, keys)
        sidecar_stages.append(
            {
                "artifact": sidecar.name,
                "stage": stage_data.get("stage"),
                "job_ref": stage_data.get("job_ref"),
                "status": stage_data.get("status"),
                "elapsed_seconds": stage_data.get("elapsed_seconds"),
                "started_at_ns": stage_data.get("started_at_ns"),
                "completed_at_ns": stage_data.get("completed_at_ns"),
                "per_ref": stage_data.get("per_ref"),
            }
        )

    for suffix in (".plan", ".receipt"):
        candidate = invocations_dir / f"{stem}{suffix}"
        if candidate.is_file():
            loaded = _load_bounded_json(candidate)
            if loaded.get("status") == "loaded":
                _collect_nested_keys(loaded["payload"], keys)

    for exec_path in sorted(invocations_dir.glob(f"{stem}.execution*")):
        loaded = _load_bounded_json(exec_path)
        if loaded.get("status") == "loaded":
            _collect_nested_keys(loaded["payload"], keys)

    entry.update(
        {
            "status": "loaded",
            "invocation_id": payload.get("invocation_id", stem),
            "hook_name": payload.get("hook_name"),
            "stage_timings": sidecar_stages,
            "keys": keys,
        }
    )
    return entry


def _parse_junit_xml(path: Path, *, max_bytes: int | None = None) -> dict[str, Any]:
    if not path.is_file():
        return {
            "source_type": "junit_xml",
            "path": str(path),
            "status": "missing",
            "keys": {},
        }
    limit = MAX_XML_REPORT_BYTES if max_bytes is None else max_bytes
    size = path.stat().st_size
    if size > limit:
        return {
            "source_type": "junit_xml",
            "path": str(path),
            "status": "oversized",
            "bytes": size,
            "keys": {},
        }
    try:
        raw_text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {
            "source_type": "junit_xml",
            "path": str(path),
            "status": "invalid",
            "error": str(exc),
            "keys": {},
        }
    if "<!DOCTYPE" in raw_text or "<!ENTITY" in raw_text:
        return {
            "source_type": "junit_xml",
            "path": str(path),
            "status": "rejected_dtd",
            "error": "XML DOCTYPE/ENTITY declarations are forbidden",
            "keys": {},
        }
    try:
        root = ET.fromstring(raw_text)
    except ET.ParseError as exc:
        return {
            "source_type": "junit_xml",
            "path": str(path),
            "status": "invalid",
            "error": str(exc),
            "keys": {},
        }

    suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    total_time = 0.0
    has_total_time = False
    cases: list[dict[str, Any]] = []
    keys: dict[str, str] = {}

    for suite in suites:
        for attr in ("tests", "failures", "errors", "skipped"):
            raw_num = suite.attrib.get(attr)
            if raw_num is not None and raw_num.isdigit():
                totals[attr] += int(raw_num)
        raw_time = suite.attrib.get("time")
        if raw_time is not None:
            try:
                total_time += float(raw_time)
                has_total_time = True
            except ValueError:
                pass
        for prop in suite.findall("./properties/property"):
            prop_name = prop.attrib.get("name", "").strip()
            prop_val = prop.attrib.get("value", "").strip()
            if prop_name in _COMMON_KEY_ALIASES and prop_val:
                canon = _COMMON_KEY_ALIASES[prop_name]
                norm = _canonical_key_value(canon, prop_val)
                if norm is not None and canon not in keys:
                    keys[canon] = norm

    if not has_total_time and root.tag == "testsuites":
        raw_root_time = root.attrib.get("time")
        if raw_root_time is not None:
            try:
                total_time = float(raw_root_time)
                has_total_time = True
            except ValueError:
                pass
    for case in root.findall(".//testcase"):
        raw_t = case.attrib.get("time")
        try:
            dur = float(raw_t) if raw_t is not None else 0.0
        except ValueError:
            dur = 0.0
        outcome = "passed"
        if case.find("failure") is not None:
            outcome = "failed"
        elif case.find("error") is not None:
            outcome = "error"
        elif case.find("skipped") is not None:
            outcome = "skipped"
        cases.append(
            {
                "classname": case.attrib.get("classname", ""),
                "name": case.attrib.get("name", ""),
                "status": outcome,
                "duration_seconds": dur,
            }
        )
    slowest = sorted(cases, key=lambda item: item["duration_seconds"], reverse=True)[
        :MAX_SLOW_ITEMS
    ]
    return {
        "source_type": "junit_xml",
        "path": str(path),
        "status": "loaded",
        "counts": totals,
        "total_duration_seconds": total_time if has_total_time else None,
        "slowest_tests": slowest,
        "keys": keys,
    }


def _parse_pytest_reportlog(
    path: Path, *, max_bytes: int | None = None
) -> dict[str, Any]:
    if not path.is_file():
        return {
            "source_type": "pytest_reportlog",
            "path": str(path),
            "status": "missing",
            "keys": {},
        }
    limit = MAX_REPORTLOG_BYTES if max_bytes is None else max_bytes
    size = path.stat().st_size
    truncated = size > limit
    bytes_read = 0
    keys: dict[str, str] = {}
    outcome_counts = {"passed": 0, "failed": 0, "skipped": 0}
    total_reported_seconds = 0.0
    durations: list[dict[str, Any]] = []
    exit_status: int | None = None

    try:
        with path.open("rb") as stream:
            for idx, raw_line in enumerate(stream):
                if idx >= MAX_REPORTLOG_LINES or bytes_read + len(raw_line) > limit:
                    truncated = True
                    break
                bytes_read += len(raw_line)
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                _collect_nested_keys(record, keys)
                user_props = record.get("user_properties")
                if isinstance(user_props, list):
                    for pair in user_props:
                        if (
                            isinstance(pair, (list, tuple))
                            and len(pair) == 2
                            and isinstance(pair[0], str)
                            and pair[0] in _COMMON_KEY_ALIASES
                        ):
                            canon = _COMMON_KEY_ALIASES[pair[0]]
                            val = _canonical_key_value(canon, pair[1])
                            if val is not None and canon not in keys:
                                keys[canon] = val
                report_type = record.get("$report_type")
                if report_type == "SessionFinish":
                    raw_exit = record.get("exitstatus")
                    if isinstance(raw_exit, int):
                        exit_status = raw_exit
                elif report_type == "TestReport":
                    dur = record.get("duration")
                    when = record.get("when", "")
                    outcome = record.get("outcome", "")
                    if isinstance(dur, (int, float)):
                        total_reported_seconds += float(dur)
                        durations.append(
                            {
                                "nodeid": record.get("nodeid", ""),
                                "when": when,
                                "outcome": outcome,
                                "duration_seconds": float(dur),
                            }
                        )
                    if when == "call" and outcome in outcome_counts:
                        outcome_counts[outcome] += 1
                    elif when == "setup" and outcome == "skipped":
                        outcome_counts["skipped"] += 1
    except OSError as exc:
        return {
            "source_type": "pytest_reportlog",
            "path": str(path),
            "status": "invalid",
            "error": str(exc),
            "keys": {},
        }

    slowest = sorted(
        durations, key=lambda item: item["duration_seconds"], reverse=True
    )[:MAX_SLOW_ITEMS]
    return {
        "source_type": "pytest_reportlog",
        "path": str(path),
        "status": "loaded",
        "truncated": truncated,
        "exit_status": exit_status,
        "counts": outcome_counts,
        "total_duration_seconds": total_reported_seconds,
        "slowest_phases": slowest,
        "keys": keys,
    }


def _attach_execution_report_ref(payload: dict[str, Any], keys: dict[str, str]) -> None:
    """Compute canonical report_ref on native #86 reports for Hook receipt matching."""
    if "report_ref" in keys or payload.get("kind") != "engineering-ci-execution-report":
        return
    try:
        report_ref = reconciliation.execution_report_ref(payload)
    except (reconciliation.ReconciliationError, TypeError, ValueError):
        return
    value = _canonical_key_value("report_ref", report_ref)
    if value is not None:
        keys["report_ref"] = value


def _load_execution_entries(path: Path) -> list[dict[str, Any]]:
    if path.is_dir():
        bundle_key = _canonical_key_value("bundle_directory", str(path))
        plan_keys: dict[str, str] = {}
        plan_candidate = path / "plan.json"
        if plan_candidate.is_file():
            loaded_plan = _load_bounded_json(plan_candidate)
            if loaded_plan.get("status") == "loaded":
                _collect_nested_keys(loaded_plan["payload"], plan_keys)
                with suppress(hooks.HookEventError, TypeError, ValueError):
                    plan_keys.setdefault(
                        "plan_ref",
                        hooks._digest_bytes(
                            hooks.canonical_event_bytes(loaded_plan["payload"])
                        ),
                    )
        entries: list[dict[str, Any]] = []
        primary_names = ("report.json", "check-result.json")
        found_any = False
        for name in primary_names:
            candidate = path / name
            if candidate.is_file():
                found_any = True
                loaded = _load_bounded_json(candidate)
                if loaded.get("status") != "loaded":
                    entries.append(
                        {
                            "source_type": "execution_bundle",
                            "path": str(candidate),
                            "status": loaded["status"],
                            "keys": {"bundle_directory": bundle_key}
                            if bundle_key
                            else {},
                        }
                    )
                    continue
                payload = loaded["payload"]
                keys: dict[str, str] = dict(plan_keys)
                if bundle_key:
                    keys["bundle_directory"] = bundle_key
                _collect_nested_keys(payload, keys)
                _attach_execution_report_ref(payload, keys)
                entries.append(
                    {
                        "source_type": "execution_bundle",
                        "path": str(candidate),
                        "status": "loaded",
                        "bundle_directory": str(path),
                        "execution_status": payload.get("status"),
                        "elapsed_seconds": payload.get("duration_seconds")
                        or payload.get("elapsed_seconds"),
                        "summary": payload.get("checks") or payload.get("results"),
                        "keys": keys,
                    }
                )
        for child_name, parser in (
            ("junit.xml", _parse_junit_xml),
            ("pytest-reportlog.jsonl", _parse_pytest_reportlog),
        ):
            child = path / child_name
            if child.is_file():
                found_any = True
                parsed = parser(child)
                if bundle_key and "bundle_directory" not in parsed["keys"]:
                    parsed["keys"]["bundle_directory"] = bundle_key
                entries.append(parsed)
        if not found_any:
            entries.append(
                {
                    "source_type": "execution_bundle",
                    "path": str(path),
                    "status": "empty_bundle_dir",
                    "keys": {"bundle_directory": bundle_key} if bundle_key else {},
                }
            )
        return entries

    loaded = _load_bounded_json(path)
    if loaded.get("status") != "loaded":
        return [
            {
                "source_type": "execution_report",
                "path": str(path),
                "status": loaded["status"],
                "error": loaded.get("error"),
                "keys": {},
            }
        ]
    payload = loaded["payload"]
    keys = {}
    _collect_nested_keys(payload, keys)
    _attach_execution_report_ref(payload, keys)
    return [
        {
            "source_type": "execution_report",
            "path": str(path),
            "status": "loaded",
            "execution_status": payload.get("status"),
            "elapsed_seconds": payload.get("duration_seconds")
            or payload.get("elapsed_seconds"),
            "keys": keys,
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
            "keys": {},
        }
    payload = loaded["payload"]
    keys: dict[str, str] = {}
    _collect_nested_keys(payload, keys)
    return {
        "source_type": "dagger_telemetry",
        "path": str(path),
        "status": "loaded",
        "correlation": payload.get("correlation"),
        "source_binding": payload.get("source_binding"),
        "cache_observation": payload.get("cache_observation"),
        "origin_execution": payload.get("origin_execution"),
        "keys": keys,
    }


def _load_ci_performance(path: Path) -> dict[str, Any]:
    loaded = _load_bounded_json(path)
    if loaded.get("status") != "loaded":
        return {
            "source_type": "ci_performance",
            "path": str(path),
            "status": loaded["status"],
            "error": loaded.get("error"),
            "keys": {},
        }
    payload = loaded["payload"]
    keys: dict[str, str] = {}
    _collect_nested_keys(payload, keys)
    return {
        "source_type": "ci_performance",
        "path": str(path),
        "status": "loaded",
        "ci_provider_provenance": {
            "provider": "github_actions",
            "run_id": payload.get("run_id"),
            "run_attempt": payload.get("run_attempt"),
            "workflow": payload.get("workflow"),
            "event": payload.get("event"),
            "url": payload.get("url"),
            "authoritative_ci_end_state": False,
        },
        "jobs": payload.get("jobs"),
        "dagger": payload.get("dagger"),
        "keys": keys,
    }


def _evaluate_correlations(sources: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate exact shared-key matches and conflicts across all supplied sources."""
    key_values: dict[str, set[str]] = {}
    for source in sources:
        for key, value in source.get("keys", {}).items():
            key_values.setdefault(key, set()).add(value)

    global_conflicts = {
        key: sorted(values) for key, values in key_values.items() if len(values) > 1
    }

    for source in sources:
        src_keys = source.get("keys", {})
        conflicts: dict[str, list[str]] = {}
        matched_keys: dict[str, str] = {}
        for key in _CORRELATION_KEY_ORDER:
            if key not in src_keys:
                continue
            if key in global_conflicts:
                conflicts[key] = global_conflicts[key]
                continue
            other_count = sum(
                1
                for other in sources
                if other is not source and key in other.get("keys", {})
            )
            if other_count > 0:
                matched_keys[key] = src_keys[key]
        if conflicts:
            source["correlation"] = {
                "status": "mismatch",
                "matched_keys": matched_keys,
                "conflicting_keys": conflicts,
            }
        elif matched_keys:
            source["correlation"] = {
                "status": "matched",
                "matched_keys": matched_keys,
                "conflicting_keys": {},
            }
        else:
            source["correlation"] = {
                "status": "standalone_uncorrelated",
                "matched_keys": {},
                "conflicting_keys": {},
                "reason": "no_shared_correlation_keys",
            }

    statuses = [src["correlation"]["status"] for src in sources]
    if global_conflicts or "mismatch" in statuses:
        overall = "mismatch_detected"
    elif sources and all(st == "matched" for st in statuses):
        overall = "matched"
    elif any(st == "matched" for st in statuses):
        overall = "partial_correlated"
    elif sources:
        overall = "uncorrelated"
    else:
        overall = "no_sources"

    agreed_keys = {
        key: next(iter(vals))
        for key, vals in key_values.items()
        if len(vals) == 1
        and sum(1 for src in sources if key in src.get("keys", {})) >= 2
    }
    return {
        "overall_status": overall,
        "shared_keys": agreed_keys,
        "conflicting_keys": global_conflicts,
    }


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
        "schema_version": 1,
        "read_only": True,
        "commands_executed": [],
        "time_source_verified": False,
        "correlation_policy": "explicit_shared_keys_only",
        "forbidden_matching_heuristics": [
            "latest_mtime",
            "filename_similarity",
            "timestamp_proximity",
        ],
        "correlation": correlation,
        "sources": sources,
    }


def format_report_text(report: dict[str, Any]) -> str:
    """Render concise human-readable timing and correlation summary without extra IO."""
    corr = report["correlation"]
    lines = [
        f"correlation: {corr['overall_status']} (read_only={report['read_only']}, time_source_verified={report['time_source_verified']})",
    ]
    if corr["conflicting_keys"]:
        lines.append(
            f"conflicts: {json.dumps(corr['conflicting_keys'], ensure_ascii=False)}"
        )
    for src in report["sources"]:
        src_corr = src["correlation"]["status"]
        line = f"- [{src['source_type']}] status={src.get('status')} correlation={src_corr}"
        if "elapsed_seconds" in src and src["elapsed_seconds"] is not None:
            line += f" elapsed={src['elapsed_seconds']}"
        elif (
            "total_duration_seconds" in src
            and src["total_duration_seconds"] is not None
        ):
            line += f" elapsed={src['total_duration_seconds']}"
        lines.append(line)
    return "\n".join(lines)


def run_report_cli(argv: list[str] | None = None) -> int:
    """CLI entrypoint for `engineering-ci verify report`."""
    parser = argparse.ArgumentParser(
        prog="engineering-ci verify report",
        description="Read-only timing report and exact-key correlation across Verify, Hooks, pytest, Dagger, and CI artifacts.",
    )
    parser.add_argument(
        "--verify-dir", type=Path, help="Verify invocation artifact directory"
    )
    parser.add_argument(
        "--hook-root", type=Path, help="Git dir or repo root containing eci-hooks"
    )
    parser.add_argument(
        "--invocation-ref", help="Explicit Hook invocation id or JSON path"
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
        "--out", type=Path, help="Optional output file for unified JSON report"
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
        args.out.parent.mkdir(parents=True, exist_ok=True)
        temp_path = args.out.with_suffix(".json.tmp")
        temp_path.write_text(encoded, encoding="utf-8", newline="\n")
        temp_path.replace(args.out)

    if args.format == "text":
        print(format_report_text(report))
    else:
        print(encoded, end="")
    return 2 if report["correlation"]["overall_status"] == "mismatch_detected" else 0
