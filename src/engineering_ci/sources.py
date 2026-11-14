"""Source-manifest reconciliation: enumeration, set diff, digests, attributes.

The algorithm is consumer-agnostic. A consumer declares only field names, the
manifest path, acquired roots, exemptions and whether Git ``-text`` is required;
Knowledge-specific source semantics stay in that consumer's configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 1
REPORT_KIND = "engineering-ci-sources-report"
MAX_CONFIG_BYTES = 1024 * 1024
_KEYS = {
    "schema_version",
    "manifest",
    "entries_key",
    "path_field",
    "sha256_field",
    "size_field",
    "acquired_roots",
    "exempt_paths",
    "environment_prefixes",
    "require_text_unset",
    "require_acquired_roots",
    "environment_requires_no_digest",
    "require_under_acquired_roots",
}


class SourcesError(ValueError):
    """The source configuration or manifest is not trustworthy."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Config:
    schema_version: int
    manifest: str
    entries_key: str
    path_field: str
    sha256_field: str
    size_field: str
    acquired_roots: tuple[str, ...]
    exempt_paths: tuple[str, ...]
    environment_prefixes: tuple[str, ...]
    require_text_unset: bool
    require_acquired_roots: bool
    environment_requires_no_digest: bool
    require_under_acquired_roots: bool


@dataclass(frozen=True)
class Report:
    status: str
    counts: dict
    failures: tuple[dict, ...]


def _text(value, name):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or any(character in value for character in "\0\r\n")
    ):
        raise SourcesError(f"{name}_invalid")
    return value


def _texts(value, name):
    if not isinstance(value, list) or len(value) > 512:
        raise SourcesError(f"{name}_invalid")
    return tuple(_text(item, name) for item in value)


def load_config(path):
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError:
        raise SourcesError("config_missing") from None
    if len(raw) > MAX_CONFIG_BYTES:
        raise SourcesError("config_too_large")
    try:
        value = tomllib.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise SourcesError("config_invalid") from None
    if not isinstance(value, dict) or set(value) - _KEYS:
        raise SourcesError("config_invalid")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise SourcesError("config_schema_unsupported")
    roots = _texts(value.get("acquired_roots"), "acquired_roots")
    if not roots:
        raise SourcesError("acquired_roots_invalid")
    require = value.get("require_text_unset", True)
    require_roots = value.get("require_acquired_roots", True)
    environment_no_digest = value.get("environment_requires_no_digest", True)
    under_roots = value.get("require_under_acquired_roots", True)
    for flag in (require, require_roots, environment_no_digest, under_roots):
        if type(flag) is not bool:
            raise SourcesError("boolean_invalid")
    return Config(
        schema_version=SCHEMA_VERSION,
        manifest=_text(value.get("manifest"), "manifest"),
        entries_key=_text(value.get("entries_key", ""), "entries_key")
        if value.get("entries_key")
        else "",
        path_field=_text(value.get("path_field", "path"), "path_field"),
        sha256_field=_text(value.get("sha256_field", "sha256"), "sha256_field"),
        size_field=_text(value.get("size_field", "size_bytes"), "size_field")
        if value.get("size_field", "size_bytes")
        else "",
        acquired_roots=roots,
        exempt_paths=_texts(value.get("exempt_paths", []), "exempt_paths"),
        environment_prefixes=_texts(
            value.get("environment_prefixes", []), "environment_prefixes"
        ),
        require_text_unset=require,
        require_acquired_roots=require_roots,
        environment_requires_no_digest=environment_no_digest,
        require_under_acquired_roots=under_roots,
    )


def config_identity(path, config=None, *, root=None):
    """Canonical config path, file digest, and normalized check-config digest."""
    resolved = Path(path).resolve()
    raw_sha = hashlib.sha256(resolved.read_bytes()).hexdigest()
    loaded = load_config(resolved) if config is None else config
    stored_path = str(resolved)
    if root is not None:
        base = Path(root).resolve()
        if resolved.is_relative_to(base):
            stored_path = resolved.relative_to(base).as_posix()
    normalized = {
        "schema_version": loaded.schema_version,
        "manifest": loaded.manifest,
        "entries_key": loaded.entries_key,
        "path_field": loaded.path_field,
        "sha256_field": loaded.sha256_field,
        "size_field": loaded.size_field,
        "acquired_roots": list(loaded.acquired_roots),
        "exempt_paths": list(loaded.exempt_paths),
        "environment_prefixes": list(loaded.environment_prefixes),
        "require_text_unset": loaded.require_text_unset,
        "require_acquired_roots": loaded.require_acquired_roots,
        "environment_requires_no_digest": loaded.environment_requires_no_digest,
        "require_under_acquired_roots": loaded.require_under_acquired_roots,
    }
    canonical = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    return {
        "path": stored_path,
        "sha256": raw_sha,
        "schema_version": loaded.schema_version,
        "effective_check_config_ref": {
            "name": "engineering-ci.sources",
            "version": str(loaded.schema_version),
            "sha256": canonical,
        },
    }


def _safe_path(value):
    path = _text(value, "manifest_path")
    if "\\" in path or path.startswith("/") or ":" in path:
        raise SourcesError("manifest_path_invalid")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise SourcesError("manifest_path_invalid")
    return path


def _manifest(config, root):
    path = root / config.manifest
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise SourcesError("manifest_unreadable") from None
    if config.entries_key:
        if not isinstance(data, dict) or not isinstance(
            data.get(config.entries_key), list
        ):
            raise SourcesError("manifest_invalid")
        data = data[config.entries_key]
    if not isinstance(data, list):
        raise SourcesError("manifest_invalid")
    return data


def _entries(config, root):
    expected, failures = {}, []
    for entry in _manifest(config, root):
        if not isinstance(entry, dict):
            failures.append(
                {"code": "entry_invalid", "path": None, "detail": "not an object"}
            )
            continue
        raw_path = entry.get(config.path_field)
        sha = entry.get(config.sha256_field)
        size = entry.get(config.size_field) if config.size_field else None
        if raw_path is None:
            if sha is not None or size is not None:
                failures.append(
                    {
                        "code": "path_missing_evidence",
                        "path": None,
                        "detail": "byte evidence without a local path",
                    }
                )
            continue
        try:
            path = _safe_path(raw_path)
        except SourcesError:
            failures.append(
                {
                    "code": "path_invalid",
                    "path": None,
                    "detail": repr(raw_path),
                }
            )
            continue
        environment = any(
            path.startswith(prefix) for prefix in config.environment_prefixes
        )
        if environment:
            if config.environment_requires_no_digest and (
                sha is not None or size is not None
            ):
                failures.append(
                    {
                        "code": "environment_has_digest",
                        "path": path,
                        "detail": "environment-managed entry must not claim bytes",
                    }
                )
                continue
            if sha is None and size is None:
                continue
        if config.require_under_acquired_roots and not any(
            path.startswith(root.rstrip("/") + "/") for root in config.acquired_roots
        ):
            failures.append(
                {
                    "code": "path_outside_roots",
                    "path": path,
                    "detail": "not under an acquired root",
                }
            )
            continue
        if not isinstance(sha, str) or len(sha) != 64:
            failures.append(
                {"code": "digest_invalid", "path": path, "detail": "sha256"}
            )
            continue
        if size is not None and (type(size) is not int or size < 0):
            failures.append(
                {"code": "size_invalid", "path": path, "detail": "size_bytes"}
            )
            continue
        if path in expected:
            prior_sha, prior_size = expected[path]
            code = "conflict" if (prior_sha, prior_size) != (sha, size) else "duplicate"
            failures.append(
                {"code": code, "path": path, "detail": "manifest path repeated"}
            )
            continue
        expected[path] = (sha, size)
    return expected, failures


def _run(argv, root):
    try:
        return subprocess.run(
            [str(item) for item in argv],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            argv, 127, "", f"Command not found: {argv[0]}"
        )


def _attributes(paths, root, runner):
    failures = []
    for start in range(0, len(paths), 100):
        chunk = paths[start : start + 100]
        result = runner(["git", "check-attr", "-z", "text", "--", *chunk], root)
        if result.returncode != 0:
            failures.append(
                {
                    "code": "attributes_unavailable",
                    "path": None,
                    "detail": result.stderr.strip() or "git check-attr failed",
                }
            )
            continue
        fields = result.stdout.split("\0")
        if fields and fields[-1] == "":
            fields.pop()
        if len(fields) % 3:
            failures.append(
                {
                    "code": "attributes_invalid",
                    "path": None,
                    "detail": "unexpected output",
                }
            )
            continue
        for path, _attribute, value in zip(fields[::3], fields[1::3], fields[2::3]):
            if value != "unset":
                failures.append(
                    {"code": "attributes", "path": path, "detail": f"text={value}"}
                )
    return failures


def reconcile(config, *, root, runner=_run):
    """Compare the declared manifest with actual files and native Git attributes."""
    root = Path(root)
    if not (root / config.manifest).is_file():
        expected, failures = (
            {},
            [
                {
                    "code": "manifest_missing",
                    "path": config.manifest,
                    "detail": "not found",
                }
            ],
        )
    else:
        try:
            expected, failures = _entries(config, root)
        except SourcesError as error:
            expected, failures = (
                {},
                [{"code": error.code, "path": config.manifest, "detail": "manifest"}],
            )
    exempt = set(config.exempt_paths)
    actual = set()
    for directory in config.acquired_roots:
        base = root / directory
        if not base.is_dir():
            if config.require_acquired_roots:
                failures.append(
                    {"code": "root_missing", "path": directory, "detail": "not found"}
                )
            continue
        for path in base.rglob("*"):
            if path.is_file():
                actual.add(path.relative_to(root).as_posix())
    actual = {
        path
        for path in actual
        if path not in exempt
        and not any(path.startswith(prefix) for prefix in config.environment_prefixes)
    }
    existing_expected = []
    for path, (sha, size) in sorted(expected.items()):
        if any(path.startswith(prefix) for prefix in config.environment_prefixes):
            continue
        file = root / path
        if not file.is_file():
            failures.append({"code": "missing", "path": path, "detail": "not found"})
            continue
        existing_expected.append(path)
        try:
            content = file.read_bytes()
        except OSError:
            failures.append(
                {"code": "unreadable", "path": path, "detail": "read failed"}
            )
            continue
        if size is not None and len(content) != size:
            failures.append(
                {"code": "size", "path": path, "detail": f"{len(content)} != {size}"}
            )
        actual_sha = hashlib.sha256(content).hexdigest()
        if actual_sha != sha:
            failures.append(
                {"code": "digest", "path": path, "detail": f"{actual_sha} != {sha}"}
            )
    for path in sorted(actual - set(expected)):
        failures.append(
            {"code": "unregistered", "path": path, "detail": "not in manifest"}
        )
    if config.require_text_unset and existing_expected:
        failures.extend(_attributes(existing_expected, root, runner))
    counts = {
        "expected": len(expected),
        "actual": len(actual),
        "checked": len(existing_expected),
        "missing": sum(item["code"] == "missing" for item in failures),
        "unregistered": sum(item["code"] == "unregistered" for item in failures),
        "duplicates": sum(item["code"] == "duplicate" for item in failures),
        "conflicts": sum(item["code"] == "conflict" for item in failures),
        "digest_errors": sum(item["code"] == "digest" for item in failures),
        "size_errors": sum(item["code"] == "size" for item in failures),
        "attribute_errors": sum(
            item["code"] in {"attributes", "attributes_invalid"} for item in failures
        ),
    }
    return Report(
        status="failed" if failures else "passed",
        counts=counts,
        failures=tuple(failures),
    )


def _print(value):
    sys.stdout.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="engineering-ci sources",
        description="Reconcile a declared source manifest with actual files and Git attributes.",
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--config", type=Path, default=Path("ci-sources.toml"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    config_path = args.config if args.config.is_absolute() else root / args.config
    try:
        config = load_config(config_path)
    except SourcesError as error:
        parser.exit(1, f"sources rejected: {error.code}\n")
    report = reconcile(config, root=root)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "status": report.status,
        "counts": report.counts,
        "failures": list(report.failures),
    }
    if args.json:
        _print(payload)
    else:
        print(f"status: {report.status}")
        print(f"counts: {report.counts}")
        for failure in report.failures:
            print(f"{failure['code']}: {failure['path']} ({failure['detail']})")
    return 1 if report.status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
