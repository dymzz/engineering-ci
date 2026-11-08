"""Repository hygiene checks with native outcomes; no re-scanning for summaries.

Checks are selected by consumer configuration. A missing tool, malformed command
or failed native check is a failure; an absent applicable input is
``not_applicable``, and advisory findings stay visibly advisory. Domain-specific
source manifests remain consumer rules rather than ECI constants.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import selection

SCHEMA_VERSION = 1
REPORT_KIND = "engineering-ci-hygiene-report"
MAX_CONFIG_BYTES = 1024 * 1024
MAX_OUTPUT = 8000
SUPPORTED_CHECKS = ("whitespace", "forwarding", "docs", "vulture", "tach")
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
    checks = tuple(value.get("checks", SUPPORTED_CHECKS))
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
    tach = _table(value.get("tach", {}), "tach", ("executable", "path", "external"))
    if type(tach.get("external", False)) is not bool:
        raise HygieneError("tach_invalid")
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
    name, status, *, exit_code=None, reason=None, stdout="", stderr="", findings=()
):
    return {
        "name": name,
        "status": status,
        "exit_code": exit_code,
        "reason": reason,
        "stdout": _bounded(stdout),
        "stderr": _bounded(stderr),
        "findings": list(findings),
    }


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
    if result.returncode == 0:
        return _result(name, "passed", exit_code=0, stdout=result.stdout)
    if result.returncode in advisory:
        return _result(
            name,
            "advisory",
            exit_code=result.returncode,
            reason=finding_reason or failure_reason,
            stdout=result.stdout,
            stderr=result.stderr,
        )
    if result.returncode == 127:
        return _result(
            name,
            "failed",
            exit_code=127,
            reason="tool_unavailable",
            stdout=result.stdout,
            stderr=result.stderr,
        )
    return _result(
        name,
        "failed",
        exit_code=result.returncode,
        reason=failure_reason,
        stdout=result.stdout,
        stderr=result.stderr,
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
    return {path for path in changed if path.lower().endswith(config.docs.suffixes)}


def _whitespace(changes, root, runner):
    result = runner(["git", "diff", "--check", changes.base_commit], root)
    if result.returncode == 0:
        return _result("whitespace", "passed", exit_code=0)
    return _result(
        "whitespace",
        "failed",
        exit_code=result.returncode,
        reason="whitespace_errors",
        stdout=result.stdout,
        stderr=result.stderr,
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


def _docs(paths, tracked, changes, root, runner, config, full):
    changed_docs = _changed_documents(set(paths), config)
    removed = any(not (root / path).exists() for path in paths)
    if not (full or changed_docs or removed):
        return _result("docs", "not_applicable", reason="no_document_changes")
    documents = _active_documents(root, tracked, set(paths), config)
    targets = documents if (full or removed) else documents & changed_docs
    if not targets:
        return _result("docs", "not_applicable", reason="no_applicable_documents")
    return _tool(
        "docs",
        [config.lychee.executable, *config.lychee.arguments],
        root,
        runner,
        data="\n".join(sorted(targets)) + "\n",
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
        return _result("tach", "failed", reason="tach_config_missing")
    return _tool(
        "tach",
        [config.tach.executable, "check"],
        root,
        runner,
        failure_reason="tach_check_failed",
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
    tracked = (
        {path for path in tracked_result.stdout.split("\0") if path}
        if tracked_result.returncode == 0
        else set()
    )
    code_changed = full or any(
        path.startswith(("src/", "scripts/", "tests/"))
        or path in {"tach.toml", "pyproject.toml"}
        for path in paths
    )
    results = []
    for name in config.checks:
        if name == "whitespace":
            results.append(_whitespace(changes, root, runner))
        elif name == "forwarding":
            results.append(_forwarding(paths, changes, root, runner, config))
        elif name == "docs":
            results.append(_docs(paths, tracked, changes, root, runner, config, full))
        elif name == "vulture":
            results.append(_vulture(root, runner, config, code_changed))
        elif name == "tach":
            results.append(_tach(paths, root, runner, config, code_changed))
    if config.tach.external and code_changed:
        results.append(
            _tool(
                "tach_external",
                [config.tach.executable, "check-external"],
                root,
                runner,
                failure_reason="tach_external_failed",
            )
        )
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
    parser = argparse.ArgumentParser(
        prog="engineering-ci hygiene",
        description="Run configured repository hygiene checks and print native evidence.",
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--config", type=Path, default=Path("ci-hygiene.toml"))
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--full", action="store_true")
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
