"""Repository hygiene checks with native outcomes; no re-scanning for summaries.

Checks are selected by consumer configuration. A missing tool, malformed command
or failed native check is a failure; an absent applicable input is
``not_applicable``, and advisory findings stay visibly advisory. Domain-specific
source manifests remain consumer rules rather than ECI constants.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 1
REPORT_KIND = "engineering-ci-hygiene-report"
MAX_CONFIG_BYTES = 1024 * 1024
MAX_OUTPUT = 8000
_DEFAULT_CHECKS = ("whitespace", "forwarding", "docs", "vulture", "tach")
SUPPORTED_CHECKS = (*_DEFAULT_CHECKS, "tach_external")
_DEPENDENCY_FILES = frozenset(
    {"uv.lock", "poetry.lock", "pdm.lock", "requirements.txt"}
)
_TOP_KEYS = {
    "schema_version",
    "checks",
    "docs",
    "lychee",
    "forwarding",
    "vulture",
    "tach",
}


class HygieneError(ValueError):
    """Configuration cannot produce a trustworthy hygiene run."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class DocsConfig:
    suffixes: tuple[str, ...]
    excluded_prefixes: tuple[str, ...]


@dataclass(frozen=True)
class LycheeConfig:
    executable: str
    arguments: tuple[str, ...]


@dataclass(frozen=True)
class ForwardingConfig:
    source_prefix: str


@dataclass(frozen=True)
class VultureConfig:
    executable: str
    paths: tuple[str, ...]
    min_confidence: int
    advisory_exit_codes: frozenset[int]


@dataclass(frozen=True)
class TachConfig:
    executable: str
    path: str
    external: bool


@dataclass(frozen=True)
class Config:
    schema_version: int
    checks: tuple[str, ...]
    docs: DocsConfig
    lychee: LycheeConfig
    forwarding: ForwardingConfig
    vulture: VultureConfig
    tach: TachConfig


def _text(value, name):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or any(character in value for character in "\0\r\n")
    ):
        raise HygieneError(f"{name}_invalid")
    return value


def _texts(value, name):
    if not isinstance(value, list) or len(value) > 256:
        raise HygieneError(f"{name}_invalid")
    return tuple(_text(item, name) for item in value)


def _table(value, name, keys):
    if not isinstance(value, dict) or set(value) - set(keys):
        raise HygieneError(f"{name}_invalid")
    return value


def load_config(path):
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError:
        raise HygieneError("config_missing") from None
    if len(raw) > MAX_CONFIG_BYTES:
        raise HygieneError("config_too_large")
    try:
        value = tomllib.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise HygieneError("config_invalid") from None
    if not isinstance(value, dict) or set(value) - _TOP_KEYS:
        raise HygieneError("config_invalid")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise HygieneError("config_schema_unsupported")
    tach = _table(value.get("tach", {}), "tach", ("executable", "path", "external"))
    if type(tach.get("external", False)) is not bool:
        raise HygieneError("tach_invalid")
    external_enabled = tach.get("external", False)
    if "checks" in value:
        checks = tuple(value["checks"])
    elif external_enabled:
        checks = (*_DEFAULT_CHECKS, "tach_external")
    else:
        checks = _DEFAULT_CHECKS
    if (
        not checks
        or set(checks) - set(SUPPORTED_CHECKS)
        or len(set(checks)) != len(checks)
    ):
        raise HygieneError("checks_invalid")
    docs = _table(value.get("docs", {}), "docs", ("suffixes", "excluded_prefixes"))
    suffixes = tuple(
        value for value in _texts(docs.get("suffixes", [".md", ".html"]), "suffixes")
    )
    if any(not suffix.startswith(".") for suffix in suffixes):
        raise HygieneError("docs_invalid")
    lychee = _table(value.get("lychee", {}), "lychee", ("executable", "arguments"))
    forwarding = _table(value.get("forwarding", {}), "forwarding", ("source_prefix",))
    vulture = _table(
        value.get("vulture", {}),
        "vulture",
        ("executable", "paths", "min_confidence", "advisory_exit_codes"),
    )
    confidence = vulture.get("min_confidence", 60)
    if type(confidence) is not int or not 0 <= confidence <= 100:
        raise HygieneError("vulture_invalid")
    advisory = vulture.get("advisory_exit_codes", [3])
    if not isinstance(advisory, list) or any(
        type(code) is not int or not 0 <= code <= 255 for code in advisory
    ):
        raise HygieneError("vulture_invalid")
    return Config(
        schema_version=SCHEMA_VERSION,
        checks=checks,
        docs=DocsConfig(
            suffixes=suffixes,
            excluded_prefixes=_texts(
                docs.get("excluded_prefixes", []), "excluded_prefixes"
            ),
        ),
        lychee=LycheeConfig(
            executable=_text(lychee.get("executable", "lychee"), "lychee_executable"),
            arguments=_texts(lychee.get("arguments", []), "lychee_arguments"),
        ),
        forwarding=ForwardingConfig(
            source_prefix=_text(
                forwarding.get("source_prefix", "src/"), "source_prefix"
            ).rstrip("/")
            + "/"
        ),
        vulture=VultureConfig(
            executable=_text(
                vulture.get("executable", "vulture"), "vulture_executable"
            ),
            paths=_texts(vulture.get("paths", ["src"]), "vulture_paths"),
            min_confidence=confidence,
            advisory_exit_codes=frozenset(advisory),
        ),
        tach=TachConfig(
            executable=_text(tach.get("executable", "tach"), "tach_executable"),
            path=_text(tach.get("path", "tach.toml"), "tach_path"),
            external=tach.get("external", False),
        ),
    )


def _run(argv, root, data=None):
    try:
        return subprocess.run(
            [str(item) for item in argv],
            cwd=root,
            input=data,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            argv, 127, "", f"Command not found: {argv[0]}"
        )


def _bounded(value):
    return (value or "")[-MAX_OUTPUT:]


def _result(
    name,
    status,
    *,
    exit_code=None,
    reason=None,
    stdout="",
    stderr="",
    findings=(),
    executable=None,
):
    result = {
        "name": name,
        "status": status,
        "exit_code": exit_code,
        "reason": reason,
        "stdout": _bounded(stdout),
        "stderr": _bounded(stderr),
        "findings": list(findings),
    }
    if executable is not None:
        result["executable"] = str(executable)
    return result


def _pure_reexport(content):
    tree = ast.parse(content)
    body = [
        node
        for node in tree.body
        if not (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
    ]
    return (
        bool(body)
        and any(isinstance(node, ast.ImportFrom) for node in body)
        and all(isinstance(node, (ast.Import, ast.ImportFrom)) for node in body)
    )


def _tool(
    name,
    argv,
    root,
    runner,
    *,
    data=None,
    advisory=frozenset(),
    failure_reason,
    finding_reason=None,
):
    result = runner(argv, root, data)
    executable = str(argv[0])
    if result.returncode == 0:
        return _result(
            name,
            "passed",
            exit_code=0,
            stdout=result.stdout,
            executable=executable,
        )
    if result.returncode in advisory:
        return _result(
            name,
            "advisory",
            exit_code=result.returncode,
            reason=finding_reason or failure_reason,
            stdout=result.stdout,
            stderr=result.stderr,
            executable=executable,
        )
    if result.returncode == 127:
        return _result(
            name,
            "failed",
            exit_code=127,
            reason="tool_unavailable",
            stdout=result.stdout,
            stderr=result.stderr,
            executable=executable,
        )
    return _result(
        name,
        "failed",
        exit_code=result.returncode,
        reason=failure_reason,
        stdout=result.stdout,
        stderr=result.stderr,
        executable=executable,
    )


def _active_documents(root, tracked, changed, config):
    documents = {
        path
        for path in tracked | changed
        if path.lower().endswith(config.docs.suffixes)
        and (root / path).is_file()
        and not any(path.startswith(prefix) for prefix in config.docs.excluded_prefixes)
    }
    return documents


def _changed_documents(changed, config):
    return {
        path
        for path in changed
        if path.lower().endswith(config.docs.suffixes)
        and not any(path.startswith(prefix) for prefix in config.docs.excluded_prefixes)
    }


def effective_checks(config):
    """Return configured checks, preserving explicit external enablement once."""
    if config.tach.external and "tach_external" not in config.checks:
        return (*config.checks, "tach_external")
    return tuple(config.checks)


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
        "checks": list(effective_checks(loaded)),
        "docs": {
            "suffixes": list(loaded.docs.suffixes),
            "excluded_prefixes": list(loaded.docs.excluded_prefixes),
        },
        "lychee": {
            "executable": loaded.lychee.executable,
            "arguments": list(loaded.lychee.arguments),
        },
        "forwarding": {"source_prefix": loaded.forwarding.source_prefix},
        "vulture": {
            "executable": loaded.vulture.executable,
            "paths": list(loaded.vulture.paths),
            "min_confidence": loaded.vulture.min_confidence,
            "advisory_exit_codes": sorted(loaded.vulture.advisory_exit_codes),
        },
        "tach": {
            "executable": loaded.tach.executable,
            "path": loaded.tach.path,
            "external": loaded.tach.external,
        },
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
            "name": "engineering-ci.hygiene",
            "version": str(loaded.schema_version),
            "sha256": canonical,
        },
    }


def _is_dependency_input(path):
    name = path.rsplit("/", 1)[-1]
    return path in _DEPENDENCY_FILES or (
        name.startswith("requirements") and name.endswith(".txt")
    )


def _deleted_paths(changes, paths, root):
    explicit = getattr(changes, "deleted_paths", ())
    if explicit:
        return {str(item).replace("\\", "/") for item in explicit}
    snapshot = getattr(changes, "snapshot", "worktree")
    if snapshot == "worktree":
        return {path for path in paths if not (root / path).exists()}
    target_tree = getattr(changes, "execution_tree", None) or getattr(
        changes, "tree", None
    )
    if target_tree:
        deleted = set()
        for path in paths:
            probe = _run(["git", "cat-file", "-e", f"{target_tree}:{path}"], root)
            if probe.returncode != 0:
                deleted.add(path)
        return deleted
    return {path for path in paths if not (root / path).exists()}


def check_applicability(changes, config, *, root, full=False, code_prefixes=None):
    """One authoritative applicability model shared by selection and hygiene."""
    root = Path(root)
    names = effective_checks(config)
    if getattr(changes, "ref_action", None) == "delete":
        return {
            name: {
                "name": name,
                "status": "not_applicable",
                "inputs": [],
                "reason": "remote_ref_deleted",
            }
            for name in names
        }
    if not changes.complete:
        unknown_reason = "git input is incomplete; full applicability unknown"
        return {
            name: {
                "name": name,
                "status": "unknown",
                "inputs": [],
                "reason": unknown_reason,
            }
            for name in names
        }
    paths = sorted({path.replace("\\", "/") for path in changes.paths})
    deleted = _deleted_paths(changes, paths, root)
    prefixes = tuple(code_prefixes or ("src/", "scripts/", "tests/"))
    vulture_prefixes = tuple(item.rstrip("/") + "/" for item in config.vulture.paths)
    tach_path = config.tach.path.replace("\\", "/")

    def is_code_path(path):
        if path.startswith(prefixes) or path in {tach_path, "pyproject.toml"}:
            return True
        return path.endswith(".py") and any(
            path == item.rstrip("/") or path.startswith(pfx)
            for item, pfx in zip(config.vulture.paths, vulture_prefixes, strict=True)
        )

    code_inputs = [path for path in paths if is_code_path(path)]
    dep_inputs = [path for path in paths if _is_dependency_input(path)]
    ext_inputs = sorted(set(code_inputs) | set(dep_inputs))
    changed_docs = sorted(_changed_documents(set(paths), config))
    removed = bool(deleted)
    forwarding_inputs = [
        path
        for path in paths
        if path.startswith(config.forwarding.source_prefix)
        and path.endswith(".py")
        and path not in deleted
    ]
    scopes = {}
    for name in names:
        if name == "whitespace":
            selected = full or bool(paths)
            scopes[name] = {
                "name": name,
                "status": "selected" if selected else "not_applicable",
                "inputs": paths if selected else [],
                "reason": "changed files" if selected else "no_changes",
            }
        elif name == "forwarding":
            selected = full or bool(forwarding_inputs)
            scopes[name] = {
                "name": name,
                "status": "selected" if selected else "not_applicable",
                "inputs": forwarding_inputs,
                "reason": "python source changed" if selected else "no_python_changes",
            }
        elif name == "docs":
            selected = full or bool(changed_docs) or removed
            scopes[name] = {
                "name": name,
                "status": "selected" if selected else "not_applicable",
                "inputs": changed_docs,
                "recheck_all": removed,
                "reason": (
                    "deleted path may break unchanged documents"
                    if removed
                    else "changed documents"
                    if changed_docs
                    else "no document changes"
                ),
            }
        elif name in {"vulture", "tach"}:
            selected = full or bool(code_inputs)
            scopes[name] = {
                "name": name,
                "status": "selected" if selected else "not_applicable",
                "inputs": code_inputs,
                "reason": "code changed" if selected else "no code changes",
            }
        elif name == "tach_external":
            selected = full or bool(ext_inputs)
            scopes[name] = {
                "name": name,
                "status": "selected" if selected else "not_applicable",
                "inputs": ext_inputs,
                "reason": (
                    "code or dependency inputs changed"
                    if selected
                    else "no code or dependency changes"
                ),
            }
    return scopes


def _whitespace(changes, root, runner):
    result = runner(["git", "diff", "--check", changes.base_commit], root)
    if result.returncode == 0:
        return _result("whitespace", "passed", exit_code=0, executable="git")
    return _result(
        "whitespace",
        "failed",
        exit_code=result.returncode,
        reason="whitespace_errors",
        stdout=result.stdout,
        stderr=result.stderr,
        executable="git",
    )


def _forwarding(paths, changes, root, runner, config):
    candidates = [
        path
        for path in paths
        if path.startswith(config.forwarding.source_prefix)
        and path.endswith(".py")
        and (root / path).is_file()
    ]
    if not candidates:
        return _result("forwarding", "not_applicable", reason="no_python_changes")
    findings = []
    for path in candidates:
        previous = runner(["git", "show", f"{changes.base_commit}:{path}"], root)
        if previous.returncode:
            exists = runner(
                ["git", "cat-file", "-e", f"{changes.base_commit}:{path}"], root
            )
            if exists.returncode:
                # A newly added module has no base object; it is not a regression.
                continue
            return _result(
                "forwarding",
                "failed",
                exit_code=previous.returncode,
                reason="base_content_unavailable",
            )
        try:
            previous_is_forwarder = _pure_reexport(previous.stdout)
            current_is_forwarder = _pure_reexport(
                (root / path).read_text(encoding="utf-8")
            )
        except (SyntaxError, UnicodeError):
            return _result("forwarding", "failed", reason="module_unparsable")
        if current_is_forwarder and not previous_is_forwarder:
            findings.append(
                f"{path} became a pure re-export; review the forwarding module"
            )
    if findings:
        return _result("forwarding", "advisory", findings=findings)
    return _result("forwarding", "passed")


def _docs(paths, tracked, changes, root, runner, config, full, *, inventory_ok=True):
    changed_docs = _changed_documents(set(paths), config)
    removed = any(not (root / path).exists() for path in paths)
    if not (full or changed_docs or removed):
        return _result("docs", "not_applicable", reason="no_document_changes")
    documents = _active_documents(root, tracked, set(paths), config)
    targets = documents if (full or removed) else documents & changed_docs
    if not targets:
        if not inventory_ok:
            # An unknown/failed tracked-file set is not proof that no document
            # is applicable; the check did not start and must not become a
            # clean not_applicable verdict.
            return {
                **_result("docs", "failed", reason="git_inventory_failed"),
                "execution_started": False,
                "execution_kind": "not_started",
            }
        return _result("docs", "not_applicable", reason="no_applicable_documents")
    # Lychee requires explicit inputs; passing the document set as stdin is
    # not an accepted invocation and fails before any link is checked.
    return _tool(
        "docs",
        [config.lychee.executable, *config.lychee.arguments, *sorted(targets)],
        root,
        runner,
        failure_reason="lychee_findings",
    )


def _vulture(root, runner, config, code_changed):
    if not code_changed:
        return _result("vulture", "not_applicable", reason="no_code_changes")
    argv = [
        config.vulture.executable,
        *config.vulture.paths,
        "--min-confidence",
        str(config.vulture.min_confidence),
    ]
    return _tool(
        "vulture",
        argv,
        root,
        runner,
        advisory=config.vulture.advisory_exit_codes,
        failure_reason="vulture_failed",
        finding_reason="vulture_findings",
    )


def _tach(paths, root, runner, config, code_changed):
    if not code_changed:
        return _result("tach", "not_applicable", reason="no_code_changes")
    if not (root / config.tach.path).is_file():
        # Tool has not started: preserve the preparation failure separately
        # from a native Tach execution verdict.
        return {
            **_result("tach", "failed", reason="tach_config_missing"),
            "execution_started": False,
            "execution_kind": "not_started",
        }
    return _tool(
        "tach",
        [config.tach.executable, "check"],
        root,
        runner,
        failure_reason="tach_check_failed",
    )


def _tach_external(root, runner, config, applicable):
    if not applicable:
        return _result("tach_external", "not_applicable", reason="no_code_changes")
    if not (root / config.tach.path).is_file():
        return {
            **_result("tach_external", "failed", reason="tach_config_missing"),
            "execution_started": False,
            "execution_kind": "not_started",
        }
    return _tool(
        "tach_external",
        [config.tach.executable, "check-external"],
        root,
        runner,
        failure_reason="tach_external_failed",
    )


def run(changes, config, *, root, runner=_run, full=False):
    """Run the configured checks once and return native evidence."""
    root = Path(root)
    paths = [path.replace("\\", "/") for path in changes.paths]
    report = {
        "schema_version": SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "status": "failed",
        "checks": [],
        "git": {
            "base_ref": changes.base_ref,
            "head_ref": changes.head_ref,
            "base_commit": changes.base_commit,
            "head_commit": changes.head_commit,
            "tree": changes.tree,
            "worktree_dirty": changes.worktree_dirty,
            "changed_paths": paths,
        },
    }
    if not changes.complete:
        report["checks"] = [
            _result("git", "failed", reason=f"git_input_{changes.error}")
        ]
        return report
    tracked_result = runner(["git", "ls-files", "-z"], root)
    # The tracked-file inventory is a real native input step of the docs check,
    # not a summary: keep its own exit and bounded output for the Check contract.
    report["inventory"] = {
        "tool": "git",
        "access": "ls-files",
        "argv": ["git", "ls-files", "-z"],
        "exit_code": tracked_result.returncode,
        "stdout": _bounded(tracked_result.stdout),
        "stderr": _bounded(tracked_result.stderr),
    }
    tracked = (
        {path for path in tracked_result.stdout.split("\0") if path}
        if tracked_result.returncode == 0
        else set()
    )
    inventory_ok = tracked_result.returncode == 0
    applicability = check_applicability(changes, config, root=root, full=full)
    results = []
    for name in effective_checks(config):
        applicable = applicability.get(name, {}).get("status") == "selected"
        if name == "whitespace":
            results.append(_whitespace(changes, root, runner))
        elif name == "forwarding":
            results.append(_forwarding(paths, changes, root, runner, config))
        elif name == "docs":
            results.append(
                _docs(
                    paths,
                    tracked,
                    changes,
                    root,
                    runner,
                    config,
                    full,
                    inventory_ok=inventory_ok,
                )
            )
        elif name == "vulture":
            results.append(_vulture(root, runner, config, applicable))
        elif name == "tach":
            results.append(_tach(paths, root, runner, config, applicable))
        elif name == "tach_external":
            results.append(_tach_external(root, runner, config, applicable))
    report["checks"] = results
    statuses = {item["status"] for item in results}
    if "failed" in statuses:
        report["status"] = "failed"
    elif statuses and statuses <= {"not_applicable"}:
        report["status"] = "not_applicable"
    else:
        report["status"] = "passed"
    return report


def _print(value):
    sys.stdout.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


def main(argv=None):
    from . import selection

    parser = argparse.ArgumentParser(
        prog="engineering-ci hygiene",
        description="Run configured repository hygiene checks and print native evidence.",
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--config", type=Path, default=Path("ci-hygiene.toml"))
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Run all applicable documents/code checks; it does not run tests",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    config_path = args.config if args.config.is_absolute() else root / args.config
    try:
        config = load_config(config_path)
    except HygieneError as error:
        parser.exit(1, f"hygiene rejected: {error.code}\n")
    report = run(
        selection.changeset(root, base=args.base, head=args.head),
        config,
        root=root,
        full=args.full,
    )
    if args.json:
        _print(report)
    else:
        print(f"status: {report['status']}")
        for item in report["checks"]:
            print(f"{item['name']}: {item['status']} ({item['reason'] or 'ok'})")
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
