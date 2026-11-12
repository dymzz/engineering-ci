"""Git impact selection, machine-readable plan and native unittest execution.

The selector lives in this installable package only. Dagger and Actions consume
the plan it produces; they never re-implement impact analysis. Consumer-specific
rules (module/test mapping, non-code inputs, escalation, prerequisites) are
configuration, not code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

from engineering_ci import semconv_registry
from engineering_ci.weaver_tool import RECORD as _WEAVER_TOOL

SCHEMA_VERSION = 1
PLAN_KIND = "engineering-ci-selection-plan"
POST_COMPLETION_PHASE = "post_completion"
MAX_CONFIG_BYTES = 1024 * 1024
MAX_TEXT = 512

_FRAMEWORKS = {"unittest", "pytest"}
_CONFIG_KEYS = {
    "schema_version",
    "tach",
    "tests_dir",
    "src_root",
    "framework",
    "escalation_files",
    "escalation_prefixes",
    "ignored_files",
    "ignored_prefixes",
    "checks",
    "module_tests",
    "non_code_rules",
    "prerequisites",
    "prerequisite_actions",
    "strict_modules",
    "coverage_required",
    "check_scope",
}


class SelectionError(ValueError):
    """Configuration or Git input cannot produce a trustworthy selection."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class NonCodeRule:
    prefix: str | None
    exact: str | None
    tests: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class CheckScopeConfig:
    docs_suffixes: tuple[str, ...]
    code_prefixes: tuple[str, ...]
    source_prefixes: tuple[str, ...]
    weaver_prefixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    schema_version: int
    tach: str
    tests_dir: str
    src_root: str
    framework: str
    escalation_files: frozenset[str]
    escalation_prefixes: tuple[str, ...]
    ignored_files: frozenset[str]
    ignored_prefixes: tuple[str, ...]
    checks: tuple[str, ...]
    module_tests: dict[str, tuple[str, ...]]
    non_code_rules: tuple[NonCodeRule, ...]
    prerequisites: dict[str, tuple[str, ...]]
    prerequisite_actions: dict[str, dict]
    strict_modules: bool
    coverage_required: bool
    check_scope: CheckScopeConfig


@dataclass(frozen=True)
class ChangeSet:
    root: Path
    base_ref: str
    head_ref: str
    base_commit: str | None
    head_commit: str | None
    tree: str | None
    paths: tuple[str, ...]
    worktree_dirty: bool
    complete: bool
    error: str | None = None
    snapshot: str = "worktree"
    index_tree: str | None = None
    merge_tree: str | None = None
    references: tuple[dict, ...] = ()
    base_tip: str | None = None

    @property
    def execution_tree(self):
        if self.snapshot == "pr_merge":
            return self.merge_tree
        if self.snapshot in {"commit", "push"}:
            return self.tree
        if self.snapshot == "index":
            return self.index_tree
        return None


@dataclass(frozen=True)
class Selection:
    changeset: ChangeSet
    tests: tuple[str, ...]
    checks: tuple[str, ...]
    status: str
    escalated: bool
    escalation_reason: str | None = None
    reasons: dict[str, list[str]] = field(default_factory=dict)
    prerequisites: frozenset[str] = frozenset()
    full_tests: bool = False


def _text(value, name):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_TEXT
        or any(character in value for character in "\0\r\n")
    ):
        raise SelectionError(f"{name}_invalid")
    return value


def _string_list(value, name):
    if not isinstance(value, list) or len(value) > 256:
        raise SelectionError(f"{name}_invalid")
    result = [_text(item, name) for item in value]
    if len(set(result)) != len(result):
        raise SelectionError(f"{name}_invalid")
    return tuple(result)


def load_config(path):
    """Load one consumer selection config; unknown keys are refused."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError:
        raise SelectionError("config_missing") from None
    if len(raw) > MAX_CONFIG_BYTES:
        raise SelectionError("config_too_large")
    try:
        value = tomllib.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise SelectionError("config_invalid") from None
    unknown = set(value) - _CONFIG_KEYS
    if unknown:
        raise SelectionError("config_invalid")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise SelectionError("config_schema_unsupported")
    tach = _text(value.get("tach", "tach.toml"), "tach")
    tests_dir = _text(value.get("tests_dir", "tests"), "tests_dir")
    src_root = _text(value.get("src_root", "src"), "src_root")
    framework = value.get("framework", "unittest")
    if framework not in _FRAMEWORKS:
        raise SelectionError("framework_unsupported")
    module_tests = value.get("module_tests", {})
    if not isinstance(module_tests, dict):
        raise SelectionError("module_tests_invalid")
    module_tests = {
        _text(module, "module"): _string_list(tests, "module_tests")
        for module, tests in module_tests.items()
    }
    rules = value.get("non_code_rules", [])
    if not isinstance(rules, list):
        raise SelectionError("non_code_rules_invalid")
    parsed_rules = []
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) - {
            "prefix",
            "exact",
            "tests",
            "reason",
        }:
            raise SelectionError("non_code_rules_invalid")
        prefix, exact = rule.get("prefix"), rule.get("exact")
        if (prefix is None) == (exact is None):
            raise SelectionError("non_code_rules_invalid")
        parsed_rules.append(
            NonCodeRule(
                prefix=_text(prefix, "prefix") if prefix is not None else None,
                exact=_text(exact, "exact") if exact is not None else None,
                tests=_string_list(rule.get("tests"), "non_code_tests"),
                reason=_text(rule.get("reason", "non-code input"), "reason"),
            )
        )
    prerequisites = value.get("prerequisites", {})
    if not isinstance(prerequisites, dict):
        raise SelectionError("prerequisites_invalid")
    prerequisites = {
        _text(name, "prerequisite"): _string_list(tests, "prerequisite_tests")
        for name, tests in prerequisites.items()
    }
    actions = value.get("prerequisite_actions", {})
    if not isinstance(actions, dict):
        raise SelectionError("prerequisite_actions_invalid")
    prerequisite_actions = {}
    for name, spec in actions.items():
        key = _text(name, "prerequisite_action")
        if not isinstance(spec, dict):
            raise SelectionError("prerequisite_actions_invalid")
        kind = _text(spec.get("type", ""), "prerequisite_type")
        if kind == "git_commit_present":
            prerequisite_actions[key] = {
                "type": kind,
                "commit": _text(spec.get("commit", ""), "prerequisite_commit"),
            }
        elif kind == "command":
            argv = _string_list(spec.get("argv", []), "prerequisite_argv")
            if not argv:
                raise SelectionError("prerequisite_actions_invalid")
            prerequisite_actions[key] = {"type": kind, "argv": list(argv)}
        else:
            raise SelectionError("prerequisite_type_invalid")
    checks = _string_list(value.get("checks", ["hygiene", "tach", "vulture"]), "checks")
    strict_modules = value.get("strict_modules", True)
    if type(strict_modules) is not bool:
        raise SelectionError("strict_modules_invalid")
    coverage_required = value.get("coverage_required", False)
    if type(coverage_required) is not bool:
        raise SelectionError("coverage_required_invalid")
    scope = value.get("check_scope", {})
    if not isinstance(scope, dict) or set(scope) - {
        "docs_suffixes",
        "code_prefixes",
        "source_prefixes",
        "weaver_prefixes",
    }:
        raise SelectionError("check_scope_invalid")
    docs_suffixes = _string_list(
        scope.get("docs_suffixes", [".md", ".html"]), "docs_suffixes"
    )
    if any(not suffix.startswith(".") for suffix in docs_suffixes):
        raise SelectionError("check_scope_invalid")
    check_scope = CheckScopeConfig(
        docs_suffixes=docs_suffixes,
        code_prefixes=_string_list(
            scope.get("code_prefixes", ["src/", "scripts/", "tests/"]),
            "code_prefixes",
        ),
        source_prefixes=_string_list(
            scope.get("source_prefixes", ["data/", "sources/"]),
            "source_prefixes",
        ),
        weaver_prefixes=_string_list(
            scope.get(
                "weaver_prefixes", ["telemetry/", "ci-weaver.toml", ".weaver.toml"]
            ),
            "weaver_prefixes",
        ),
    )
    return Config(
        schema_version=SCHEMA_VERSION,
        tach=tach,
        tests_dir=tests_dir,
        src_root=src_root,
        framework=framework,
        escalation_files=frozenset(
            _string_list(value.get("escalation_files", []), "escalation_files")
        ),
        escalation_prefixes=_string_list(
            value.get("escalation_prefixes", []), "escalation_prefixes"
        ),
        ignored_files=frozenset(
            _string_list(value.get("ignored_files", []), "ignored_files")
        ),
        ignored_prefixes=_string_list(
            value.get("ignored_prefixes", []), "ignored_prefixes"
        ),
        checks=checks,
        module_tests=module_tests,
        non_code_rules=tuple(parsed_rules),
        prerequisites=prerequisites,
        prerequisite_actions=prerequisite_actions,
        strict_modules=strict_modules,
        coverage_required=coverage_required,
        check_scope=check_scope,
    )


def _git(root, *args):
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _resolve(root, ref):
    result = _git(root, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if result.returncode:
        return None
    return result.stdout.strip() or None


def _parse_porcelain(data):
    paths, tokens, index = [], data.split("\0"), 0
    while index < len(tokens):
        token = tokens[index]
        if not token:
            index += 1
            continue
        status, path = token[:2], token[3:]
        if path:
            paths.append(path)
        if ("R" in status or "C" in status) and index + 1 < len(tokens):
            index += 1
            if tokens[index]:
                paths.append(tokens[index])
        index += 1
    return paths


def _tree_of(root, commit):
    result = _git(root, "rev-parse", "--verify", f"{commit}^{{tree}}")
    return result.stdout.strip() or None if result.returncode == 0 else None


def worktree_content_binding(root, paths):
    """Digest of selected paths' current worktree bytes; None means missing."""
    root = Path(root)
    entries = {}
    for path in sorted({str(item).replace("\\", "/") for item in paths}):
        file = root / path
        if file.is_file():
            entries[path] = hashlib.sha256(file.read_bytes()).hexdigest()
        else:
            entries[path] = None
    digest = hashlib.sha256(
        json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {"algorithm": "sha256-paths", "digest": digest, "paths": entries}


def directory_content_id(root):
    """Stable content id over a directory tree (path + file bytes)."""
    root = Path(root)
    digest = hashlib.sha256()
    for path in sorted(
        root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()
    ):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == ".git" or relative.startswith(".git/"):
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def tree_content_id(root, tree):
    """Content id for a Git tree, matching directory_content_id on a raw checkout."""
    import tempfile

    root = Path(root).resolve()
    git_dir = root / ".git"
    if not git_dir.exists():
        return None
    with tempfile.TemporaryDirectory() as temporary:
        temporary = Path(temporary)
        index = temporary / "index"
        work = temporary / "work"
        work.mkdir()
        env = {
            **os.environ,
            "GIT_DIR": str(git_dir),
            "GIT_INDEX_FILE": str(index),
            "GIT_WORK_TREE": str(work),
        }
        read = subprocess.run(
            ["git", "read-tree", tree],
            cwd=root,
            env=env,
            capture_output=True,
            check=False,
        )
        if read.returncode != 0:
            return None
        checkout = subprocess.run(
            ["git", "checkout-index", "-a", "-f"],
            cwd=work,
            env=env,
            capture_output=True,
            check=False,
        )
        if checkout.returncode != 0:
            return None
        return directory_content_id(work)


def changeset_multi(root, refs):
    """Union of several push ref pairs; execution still requires per-ref runs."""
    root = Path(root)
    parts = []
    for before, after in refs:
        parts.append(
            changeset(root, base=before, mode="push", before=before, after=after)
        )
    paths = sorted({path for part in parts for path in part.paths})
    references = tuple(
        {
            "before": part.base_commit,
            "after": part.head_commit,
            "paths": list(part.paths),
            "complete": part.complete,
        }
        for part in parts
    )
    return ChangeSet(
        root=root,
        base_ref=",".join(before for before, _ in refs),
        head_ref=",".join(after for _, after in refs),
        base_commit=parts[0].base_commit if parts else None,
        head_commit=parts[-1].head_commit if parts else None,
        tree=None,
        paths=tuple(paths),
        worktree_dirty=False,
        complete=all(part.complete for part in parts) and bool(parts),
        error=None if all(part.complete for part in parts) else "multi_ref_incomplete",
        snapshot="multi",
        references=references,
    )


def changeset(
    root,
    *,
    base,
    head="HEAD",
    include_worktree=True,
    mode="worktree",
    before=None,
    after=None,
):
    """Describe the Git snapshot behind a change set.

    Modes: worktree, index, commit, push (before/after), pr (merge tree).
    Immutable snapshots expose execution_tree; mutable ones do not.
    """
    root = Path(root)
    if mode == "push":
        before_commit = _resolve(root, before) if before else None
        after_commit = _resolve(root, after) if after else None
        if before_commit is None or after_commit is None:
            return ChangeSet(
                root,
                before or "",
                after or "",
                before_commit,
                after_commit,
                None,
                (),
                False,
                False,
                "push_ref_unresolved",
                snapshot="push",
            )
        diff = _git(
            root,
            "diff",
            "--no-renames",
            "--name-only",
            "-z",
            before_commit,
            after_commit,
        )
        paths = [path for path in diff.stdout.split("\0") if path]
        return ChangeSet(
            root=root,
            base_ref=before or "",
            head_ref=after or "",
            base_commit=before_commit,
            head_commit=after_commit,
            tree=_tree_of(root, after_commit),
            paths=tuple(sorted(set(paths))),
            worktree_dirty=False,
            complete=diff.returncode == 0,
            error=None if diff.returncode == 0 else "push_diff_failed",
            snapshot="push",
        )
    head_commit = _resolve(root, head)
    if head_commit is None:
        return ChangeSet(
            root,
            base,
            head,
            None,
            None,
            None,
            (),
            False,
            False,
            "head_unresolved",
            snapshot=mode,
        )
    tree = _tree_of(root, head_commit)
    merge = _git(root, "merge-base", head_commit, base)
    base_commit = merge.stdout.strip() if merge.returncode == 0 else None
    if base_commit is None:
        return ChangeSet(
            root,
            base,
            head,
            None,
            head_commit,
            tree,
            (),
            False,
            False,
            "base_unresolved",
            snapshot=mode,
        )
    if mode == "commit":
        diff = _git(
            root, "diff", "--no-renames", "--name-only", "-z", base_commit, head_commit
        )
        paths = [path for path in diff.stdout.split("\0") if path]
        return ChangeSet(
            root,
            base,
            head,
            base_commit,
            head_commit,
            tree,
            tuple(sorted(set(paths))),
            False,
            diff.returncode == 0,
            None if diff.returncode == 0 else "diff_failed",
            snapshot="commit",
        )
    if mode == "index":
        diff = _git(
            root,
            "diff",
            "--no-renames",
            "--name-only",
            "-z",
            "--cached",
            base_commit,
        )
        index_tree = None
        write_tree = _git(root, "write-tree")
        index_tree = (
            write_tree.stdout.strip() if write_tree.returncode == 0 else index_tree
        )
        paths = [path for path in diff.stdout.split("\0") if path]
        status = _git(root, "status", "--porcelain", "-z", "--untracked-files=all")
        return ChangeSet(
            root,
            base,
            head,
            base_commit,
            head_commit,
            tree,
            tuple(sorted(set(paths))),
            bool(status.stdout) if status.returncode == 0 else False,
            diff.returncode == 0,
            None if diff.returncode == 0 else "diff_failed",
            snapshot="index",
            index_tree=index_tree,
        )
    if mode == "pr":
        base_tip = _resolve(root, base)
        if base_tip is None:
            return ChangeSet(
                root,
                base,
                head,
                base_commit,
                head_commit,
                tree,
                (),
                False,
                False,
                "base_tip_unresolved",
                snapshot="pr_merge",
            )
        merged = _git(root, "merge-tree", "--write-tree", base_tip, head_commit)
        merge_tree = merged.stdout.splitlines()[0].strip() if merged.stdout else None
        if merged.returncode != 0 or not merge_tree:
            return ChangeSet(
                root,
                base,
                head,
                base_commit,
                head_commit,
                tree,
                (),
                False,
                False,
                "merge_tree_failed" if merged.returncode else "merge_tree_missing",
                snapshot="pr_merge",
                base_tip=base_tip,
            )
        diff = _git(
            root,
            "diff",
            "--no-renames",
            "--name-only",
            "-z",
            base_tip,
            merge_tree,
        )
        paths = [path for path in diff.stdout.split("\0") if path]
        return ChangeSet(
            root,
            base,
            head,
            base_commit,
            head_commit,
            tree,
            tuple(sorted(set(paths))),
            False,
            diff.returncode == 0,
            None if diff.returncode == 0 else "diff_failed",
            snapshot="pr_merge",
            merge_tree=merge_tree,
            base_tip=base_tip,
        )
    if mode != "worktree":
        return ChangeSet(
            root,
            base,
            head,
            base_commit,
            head_commit,
            tree,
            (),
            False,
            False,
            "snapshot_mode_unsupported",
            snapshot=mode,
        )
    diff = _git(
        root, "diff", "--no-renames", "--name-only", "-z", base_commit, head_commit
    )
    paths = (
        [path for path in diff.stdout.split("\0") if path]
        if diff.returncode == 0
        else []
    )
    complete = diff.returncode == 0
    error = None if complete else "diff_failed"
    worktree_dirty = False
    if include_worktree and head_commit == _resolve(root, "HEAD"):
        status = _git(root, "status", "--porcelain", "-z", "--untracked-files=all")
        if status.returncode == 0:
            worktree_dirty = bool(status.stdout)
            paths.extend(_parse_porcelain(status.stdout))
        else:
            complete, error = False, "worktree_unresolved"
    return ChangeSet(
        root=root,
        base_ref=base,
        head_ref=head,
        base_commit=base_commit,
        head_commit=head_commit,
        tree=tree,
        paths=tuple(sorted(set(paths))),
        worktree_dirty=worktree_dirty,
        complete=complete,
        error=error,
        snapshot="worktree",
    )


def _normalized(path):
    return path.replace("\\", "/")


def _test_modules(tests_dir, prefix):
    if not tests_dir.is_dir():
        return ()
    return tuple(
        sorted(
            f"{prefix}.{'.'.join(path.relative_to(tests_dir).with_suffix('').parts)}"
            for path in tests_dir.rglob("test_*.py")
            if path.is_file()
        )
    )


def _tach_graph(path):
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise SelectionError("tach_config_invalid") from None
    modules = data.get("modules", [])
    if not isinstance(modules, list):
        raise SelectionError("tach_config_invalid")
    reverse = {}
    for module in modules:
        if not isinstance(module, dict) or "path" not in module:
            raise SelectionError("tach_config_invalid")
        name = _text(module["path"], "tach_module")
        dependencies = module.get("depends_on", [])
        if not isinstance(dependencies, list):
            raise SelectionError("tach_config_invalid")
        reverse.setdefault(name, [])
        for dependency in dependencies:
            reverse.setdefault(_text(dependency, "tach_module"), []).append(name)
    if not reverse:
        raise SelectionError("tach_config_empty")
    return tuple(sorted(reverse)), {name: list(rev) for name, rev in reverse.items()}


def _reverse_closure(changed, reverse):
    impacted, stack = set(changed), list(changed)
    while stack:
        current = stack.pop()
        for dependent in reverse.get(current, []):
            if dependent not in impacted:
                impacted.add(dependent)
                stack.append(dependent)
    return impacted


def _module_for(path, modules, src_root):
    prefix = src_root.rstrip("/") + "/"
    normalized = _normalized(path)
    if not normalized.startswith(prefix):
        return None
    candidate = normalized[len(prefix) :]
    candidate = candidate.removesuffix(".py").replace("/", ".")
    matched = None
    for module in modules:
        if (candidate == module or candidate.startswith(module + ".")) and (
            matched is None or len(module) > len(matched)
        ):
            matched = module
    return matched


def _is_ignored(path, config):
    return path in config.ignored_files or any(
        path.startswith(prefix) for prefix in config.ignored_prefixes
    )


def _rules_for(path, config):
    exact = [rule for rule in config.non_code_rules if rule.exact == path]
    if exact:
        return exact
    return [
        rule
        for rule in config.non_code_rules
        if rule.prefix is not None and path.startswith(rule.prefix)
    ]


def _escalated(changeset, config, tests, reason, reasons=None, *, full_tests=False):
    return Selection(
        changeset=changeset,
        tests=tests,
        checks=config.checks,
        status="escalated",
        escalated=True,
        escalation_reason=reason,
        reasons=reasons or {"all": [reason]},
        prerequisites=frozenset(config.prerequisites),
        full_tests=full_tests,
    )


def select(changes, config, *, root, full=False):
    """Select tests for one change set; unknown coverage escalates, never passes."""
    root = Path(root)
    tests_dir = root / config.tests_dir
    test_prefix = ".".join(Path(config.tests_dir).parts)
    all_tests = _test_modules(tests_dir, test_prefix)
    if full:
        return _escalated(
            changes,
            config,
            all_tests,
            "Explicitly requested full suite",
            full_tests=True,
        )
    if not changes.complete:
        return _escalated(
            changes,
            config,
            all_tests,
            f"Git input could not be resolved: {changes.error}",
        )
    paths = [_normalized(path) for path in changes.paths]
    if not paths:
        return Selection(
            changeset=changes,
            tests=(),
            checks=config.checks,
            status="no_changes",
            escalated=False,
            reasons={"skipped": ["No changed files detected"]},
        )
    for path in paths:
        if path in config.escalation_files or any(
            path.startswith(prefix) for prefix in config.escalation_prefixes
        ):
            return _escalated(
                changes, config, all_tests, f"Global configuration modified: {path}"
            )
    try:
        modules, reverse = _tach_graph(root / config.tach)
    except SelectionError as error:
        return _escalated(
            changes, config, all_tests, f"Tach input unavailable: {error.code}"
        )
    selected, reasons, changed_modules, unmapped = set(), {}, set(), []
    for path in paths:
        if _is_ignored(path, config):
            continue
        rules = _rules_for(path, config)
        if rules:
            for rule in rules:
                for test in rule.tests:
                    selected.add(test)
                    reasons.setdefault(test, []).append(rule.reason)
            continue
        if (
            path.startswith(config.tests_dir.rstrip("/") + "/")
            and path.endswith(".py")
            and path.split("/")[-1].startswith("test_")
        ):
            if not (root / path).is_file():
                return _escalated(
                    changes,
                    config,
                    all_tests,
                    f"Test file deleted or renamed: {path}",
                )
            module = ".".join(Path(path).with_suffix("").parts)
            selected.add(module)
            reasons.setdefault(module, []).append(
                f"Direct test file modification: {path}"
            )
            continue
        module = _module_for(path, modules, config.src_root)
        if module:
            changed_modules.add(module)
        else:
            unmapped.append(path)
    if unmapped:
        return _escalated(
            changes,
            config,
            all_tests,
            f"Unknown or unmapped path changed: {', '.join(unmapped)}",
            {"all": [f"Unmapped: {path}" for path in unmapped]},
        )
    if changed_modules:
        impacted = _reverse_closure(changed_modules, reverse)
        if config.strict_modules:
            missing = sorted(
                module for module in impacted if module not in config.module_tests
            )
            if missing:
                return _escalated(
                    changes,
                    config,
                    all_tests,
                    f"Python module(s) lack registered test mappings: {', '.join(missing)}",
                )
        for module in sorted(impacted):
            for test in config.module_tests.get(module, ()):
                selected.add(test)
                reasons.setdefault(test, []).append(
                    f"Impacted by Python module: {module}"
                )
    prerequisites = frozenset(
        name for name, tests in config.prerequisites.items() if selected & set(tests)
    )
    ordered = tuple(sorted(selected))
    return Selection(
        changeset=changes,
        tests=ordered,
        checks=config.checks,
        status="targeted" if ordered else "no_tests",
        escalated=False,
        reasons=reasons,
        prerequisites=prerequisites,
    )


def _version():
    try:
        return metadata.version("engineering-ci")
    except metadata.PackageNotFoundError:
        return "0+uninstalled"


def _digest(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _check_scope(selection, config, root):
    """Per-check impact; a documentation-only change still selects Lychee."""
    paths = [_normalized(path) for path in selection.changeset.paths]
    if not selection.changeset.complete:
        return [
            {
                "name": config.framework,
                "status": selection.status,
                "inputs": list(selection.tests),
                "reason": selection.escalation_reason,
            },
            *[
                {
                    "name": name,
                    "status": "unknown",
                    "inputs": [],
                    "reason": "git input is incomplete; full applicability unknown",
                }
                for name in (
                    "docs",
                    "tach",
                    "vulture",
                    "source",
                    *[
                        check
                        for check in config.checks
                        if check
                        in {"weaver_registry", "weaver_live", "weaver_terminal"}
                    ],
                )
            ],
        ]
    docs_changed = sorted(
        path
        for path in paths
        if path.lower().endswith(config.check_scope.docs_suffixes)
    )
    removed = any(not (root / path).exists() for path in paths)
    code_changed = any(
        path.startswith(config.check_scope.code_prefixes) for path in paths
    ) or any(path in {"tach.toml", "pyproject.toml"} for path in paths)
    source_changed = any(
        path.startswith(config.check_scope.source_prefixes) for path in paths
    )
    scopes = [
        {
            "name": config.framework,
            "status": selection.status,
            "inputs": list(selection.tests),
            "reason": selection.escalation_reason,
        },
        {
            "name": "docs",
            "status": "selected" if docs_changed or removed else "not_applicable",
            "inputs": docs_changed,
            "recheck_all": bool(removed),
            "reason": (
                "deleted path may break unchanged documents"
                if removed
                else "changed documents"
                if docs_changed
                else "no document changes"
            ),
        },
        {
            "name": "tach",
            "status": "selected" if code_changed else "not_applicable",
            "inputs": [],
            "reason": "code changed" if code_changed else "no code changes",
        },
        {
            "name": "vulture",
            "status": "selected" if code_changed else "not_applicable",
            "inputs": [],
            "reason": "code changed" if code_changed else "no code changes",
        },
        {
            "name": "source",
            "status": "selected" if source_changed else "not_applicable",
            "inputs": [
                path
                for path in paths
                if path.startswith(config.check_scope.source_prefixes)
            ],
            "reason": "source inputs changed"
            if source_changed
            else "no source changes",
        },
    ]
    weaver_inputs = [
        path for path in paths if path.startswith(config.check_scope.weaver_prefixes)
    ]
    # Incremental Git impact never claims Terminal readiness from filenames.
    # weaver_terminal runs only via an explicit post-completion Plan (PR #78 P1-2).
    for name in config.checks:
        if name not in {"weaver_registry", "weaver_live", "weaver_terminal"}:
            continue
        if name == "weaver_terminal":
            scopes.append(
                {
                    "name": name,
                    "status": "not_applicable",
                    "inputs": [],
                    "reason": "requires post-completion plan",
                }
            )
            continue
        scopes.append(
            {
                "name": name,
                "status": "selected" if weaver_inputs else "not_applicable",
                "inputs": weaver_inputs,
                "reason": (
                    "Weaver inputs changed"
                    if weaver_inputs
                    else "no Weaver input changes"
                ),
            }
        )
    return scopes


def _tach_check(root, config, changes):
    try:
        result = subprocess.run(
            ["tach", "check"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError:
        return {
            "status": "unavailable",
            "exit_code": 127,
            "stdout": "",
            "stderr": "tach not found",
            "tach_sha256": _digest(root / config.tach),
            "source_tree": changes.execution_tree or changes.tree,
            "snapshot": changes.snapshot,
        }
    return {
        "status": "passed" if result.returncode == 0 else "failed",
        "exit_code": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
        "tach_sha256": _digest(root / config.tach),
        "source_tree": changes.execution_tree or changes.tree,
        "snapshot": changes.snapshot,
    }


def _graph_validation(selection, config, root, value):
    if value is None:
        return {"status": "not_executed"}
    expected_sha = _digest(root / config.tach)
    expected_tree = selection.changeset.execution_tree or selection.changeset.tree
    if value.get("tach_sha256") != expected_sha:
        return {"status": "untrusted", "reason": "tach_config_mismatch"}
    if value.get("source_tree") != expected_tree:
        return {"status": "untrusted", "reason": "source_tree_mismatch"}
    return {
        "status": value.get("status", "failed"),
        "exit_code": value.get("exit_code"),
        "tach_sha256": value.get("tach_sha256"),
        "source_tree": value.get("source_tree"),
    }


def plan(selection, *, config, config_path, root, tach_validation=None):
    """Machine-readable handoff shared by local CLI, Dagger and Actions."""
    root, config_path = Path(root), Path(config_path)
    tach_path = root / config.tach
    graph = _graph_validation(selection, config, root, tach_validation)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": PLAN_KIND,
        "generator": {
            "name": "engineering-ci",
            "version": _version(),
            "config_sha256": _digest(config_path),
            "tach_sha256": _digest(tach_path),
        },
        "git": {
            "base_ref": selection.changeset.base_ref,
            "head_ref": selection.changeset.head_ref,
            "base_commit": selection.changeset.base_commit,
            "base_tip": selection.changeset.base_tip,
            "head_commit": selection.changeset.head_commit,
            "tree": selection.changeset.tree,
            "worktree_dirty": selection.changeset.worktree_dirty,
            "snapshot": selection.changeset.snapshot,
            "index_tree": selection.changeset.index_tree,
            "merge_tree": selection.changeset.merge_tree,
            "execution_tree": selection.changeset.execution_tree,
            "references": list(selection.changeset.references),
            "changed_paths": list(selection.changeset.paths),
            "content_binding": (
                worktree_content_binding(root, selection.changeset.paths)
                if selection.changeset.snapshot == "worktree"
                else None
            ),
            "source_tree_id": (
                tree_content_id(root, selection.changeset.execution_tree)
                if selection.changeset.execution_tree
                else None
            ),
        },
        "selection": {
            "status": selection.status,
            "tests": list(selection.tests),
            "checks": list(selection.checks),
            "prerequisites": sorted(selection.prerequisites),
            "escalated": selection.escalated,
            "escalation_reason": selection.escalation_reason,
            "full_tests": selection.full_tests,
            "framework": config.framework,
            "tests_dir": config.tests_dir,
            "src_root": config.src_root,
            "coverage_required": config.coverage_required,
            "tach_graph_validation": graph,
            "requires_tach_check": True,
            "reasons": {
                name: list(items) for name, items in sorted(selection.reasons.items())
            },
        },
        "check_scope": _check_scope(selection, config, root),
        "prerequisite_actions": {
            name: dict(spec)
            for name, spec in sorted(config.prerequisite_actions.items())
        },
        "inputs": {
            "config": {"path": str(config_path), "sha256": _digest(config_path)},
            "tach": {"path": config.tach, "sha256": _digest(tach_path)},
        },
    }


def _relative_source_path(root, path):
    root = Path(root).resolve()
    value = Path(path)
    resolved = value.resolve() if value.is_absolute() else (root / value).resolve()
    if not resolved.is_relative_to(root):
        raise SelectionError("post_completion_path_escapes_source")
    return resolved.relative_to(root).as_posix(), resolved


def _file_binding(root, path):
    relative, resolved = _relative_source_path(root, path)
    if not resolved.is_file():
        raise SelectionError("post_completion_input_missing")
    return {"path": relative, "sha256": _digest(resolved)}


def _stable_digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _registry_closure(root, registry_rel, manifest_rel):
    """Bind Registry directory via the existing per-file manifest contract."""
    manifest = _file_binding(root, manifest_rel)
    try:
        expected = json.loads((root / manifest["path"]).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise SelectionError("post_completion_registry_manifest_invalid") from None
    if not isinstance(expected, dict) or not expected:
        raise SelectionError("post_completion_registry_manifest_invalid")
    reg_rel, reg_path = _relative_source_path(root, registry_rel)
    if not reg_path.is_dir():
        raise SelectionError("post_completion_input_missing")
    actual = {}
    for path in sorted(
        reg_path.rglob("*"), key=lambda item: item.relative_to(reg_path).as_posix()
    ):
        if path.is_symlink():
            raise SelectionError("post_completion_registry_symlink")
        if path.is_file():
            actual[path.relative_to(reg_path).as_posix()] = _digest(path)
    if actual != expected:
        raise SelectionError("post_completion_registry_bytes_mismatch")
    return {
        "path": reg_rel,
        # The Registry closure digest is the canonical digest of the pinned
        # official source, so it must use that one implementation.
        "sha256": semconv_registry.registry_tree_digest(actual),
        "manifest": manifest,
        "files": actual,
    }


def _advice_closure(root, relative):
    adv_rel, adv_path = _relative_source_path(root, relative)
    if not adv_path.is_dir():
        raise SelectionError("post_completion_input_missing")
    files = {}
    for path in sorted(
        adv_path.rglob("*.rego"),
        key=lambda item: item.relative_to(adv_path).as_posix(),
    ):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(adv_path).as_posix()
        if ".." in Path(rel).parts:
            raise SelectionError("post_completion_advice_path_invalid")
        files[rel] = _digest(path)
    if not files:
        raise SelectionError("post_completion_advice_empty")
    if "otel.rego" not in files:
        raise SelectionError("post_completion_advice_default_missing")
    if "cicd-error-type.rego" not in files:
        raise SelectionError("post_completion_advice_conditional_missing")
    return {"path": adv_rel, "sha256": _stable_digest(files), "files": files}


def _capture_span_matches(capture, span):
    matches = 0
    for resource in capture.get("resourceSpans") or []:
        for scope in resource.get("scopeSpans") or []:
            for item in scope.get("spans") or []:
                if item.get("traceId") == span.get("trace_id") and item.get(
                    "spanId"
                ) == span.get("span_id"):
                    matches += 1
    return matches


def compute_weaver_input_closure(root, weaver_config):
    """Recompute the Native Weaver input closure from ``ci-weaver.toml`` alone."""
    root = Path(root)
    weaver_rel, weaver_path = _relative_source_path(root, weaver_config)
    if not weaver_path.is_file():
        raise SelectionError("post_completion_input_missing")
    try:
        data = tomllib.loads(weaver_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise SelectionError("post_completion_weaver_config_invalid") from error
    required = (
        "registry",
        "registry_manifest",
        "matcher_config",
        "otlp",
        "expectations",
        "advice_policies",
    )
    for key in required:
        if not isinstance(data.get(key), str) or not data[key]:
            raise SelectionError(f"post_completion_{key}_undeclared")
    registry = _registry_closure(root, data["registry"], data["registry_manifest"])
    matcher = _file_binding(root, data["matcher_config"])
    advice = _advice_closure(root, data["advice_policies"])
    otlp = _file_binding(root, data["otlp"])
    expectations = _file_binding(root, data["expectations"])
    closure = {
        "weaver_config": {"path": weaver_rel, "sha256": _digest(weaver_path)},
        "registry": registry,
        "matcher_config": matcher,
        "advice_policies": advice,
        "otlp": otlp,
        "expectations": expectations,
        "weaver_tool": dict(_WEAVER_TOOL),
        "origin": {
            "repository": (data.get("origin") or {}).get("repository"),
            "commit": (data.get("origin") or {}).get("commit"),
            "version": (data.get("origin") or {}).get("version"),
        },
        "expected_matchers": list(data.get("expected_matchers") or []),
    }
    closure["sha256"] = _stable_digest(
        {
            "weaver_config": closure["weaver_config"],
            "registry": {
                "path": registry["path"],
                "sha256": registry["sha256"],
                "manifest": registry["manifest"],
            },
            "matcher_config": matcher,
            "advice_policies": {
                "path": advice["path"],
                "sha256": advice["sha256"],
                "files": advice["files"],
            },
            "otlp": otlp,
            "expectations": expectations,
            "weaver_tool": closure["weaver_tool"],
        }
    )
    return closure


def post_completion_plan(
    *,
    root,
    config_path,
    weaver_config,
    run_id,
    attempt,
    result,
    source_tree_id=None,
):
    """Explicit Terminal Plan from one ``ci-weaver.toml`` input closure.

    Registry/Matcher/Advice/OTLP/Expectation paths are taken only from that
    config — no parallel overrides. ``execute-plan`` recomputes the same
    closure before Native Terminal.
    """
    root = Path(root)
    config_path = Path(config_path)
    if (
        not isinstance(run_id, str)
        or not run_id
        or type(attempt) is not int
        or attempt < 1
        or not isinstance(result, str)
        or not result
    ):
        raise SelectionError("post_completion_identity_invalid")
    closure = compute_weaver_input_closure(root, weaver_config)
    try:
        expected = json.loads(
            (root / closure["expectations"]["path"]).read_text(encoding="utf-8")
        )
        capture = json.loads(
            (root / closure["otlp"]["path"]).read_text(encoding="utf-8")
        )
    except (OSError, ValueError, UnicodeError) as error:
        raise SelectionError("post_completion_inputs_unreadable") from error
    source = expected.get("source") if isinstance(expected, dict) else None
    evidence = expected.get("terminal_evidence") if isinstance(expected, dict) else None
    if (
        not isinstance(source, dict)
        or source.get("run_id") != run_id
        or source.get("attempt") != attempt
        or not isinstance(evidence, dict)
        or evidence.get("status") != "completed"
        or evidence.get("run_id") != run_id
        or evidence.get("attempt") != attempt
        or evidence.get("result") != result
    ):
        raise SelectionError("post_completion_evidence_mismatch")
    spans = expected.get("spans") if isinstance(expected, dict) else None
    if not isinstance(spans, list) or not spans:
        raise SelectionError("post_completion_expectations_invalid")
    for span in spans:
        if not isinstance(span, dict):
            raise SelectionError("post_completion_expectations_invalid")
        matches = _capture_span_matches(capture, span)
        if matches == 0:
            raise SelectionError("post_completion_missing_capture_span")
        if matches > 1:
            raise SelectionError("post_completion_duplicate_capture_span")
    binding = {
        "run_id": run_id,
        "attempt": attempt,
        "status": "completed",
        "result": result,
        **closure,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": PLAN_KIND,
        "phase": POST_COMPLETION_PHASE,
        "generator": {
            "name": "engineering-ci",
            "version": _version(),
            "config_sha256": _digest(config_path) if config_path.is_file() else None,
        },
        "git": {
            "snapshot": "worktree",
            "changed_paths": [],
            "source_tree_id": source_tree_id,
            "execution_tree": None,
            "worktree_dirty": True,
        },
        "selection": {
            "status": "selected",
            "tests": [],
            "checks": ["weaver_terminal"],
            "prerequisites": [],
            "framework": "unittest",
            "tests_dir": "tests",
            "src_root": "src",
        },
        "check_scope": [
            {
                "name": "weaver_terminal",
                "status": "selected",
                "inputs": [
                    binding["otlp"]["path"],
                    binding["expectations"]["path"],
                    binding["weaver_config"]["path"],
                    binding["matcher_config"]["path"],
                    binding["advice_policies"]["path"],
                    binding["registry"]["path"],
                ],
                "reason": "post-completion binding",
            }
        ],
        "post_completion": binding,
        "inputs": {
            "config": {
                "path": str(config_path),
                "sha256": _digest(config_path) if config_path.is_file() else None,
            },
            "weaver": binding["weaver_config"],
            "closure_sha256": binding["sha256"],
        },
    }


def validate_post_completion_binding(plan, root):
    """Recompute the weaver.toml closure and refuse Native Terminal on drift.

    Returns a structured result for the parent Execution Report. Callers must
    treat ``ok is False`` as ``not_executed`` / ``blocked``.
    """
    root = Path(root)
    result = {
        "ok": False,
        "reason": None,
        "input_closure_verified": "failed",
        "source_tree_verified": "not_requested",
        "plan_closure_digest": None,
        "actual_closure_digest": None,
    }
    if plan.get("phase") != POST_COMPLETION_PHASE:
        result["reason"] = "post_completion_phase_required"
        return result
    binding = plan.get("post_completion")
    if not isinstance(binding, dict):
        result["reason"] = "post_completion_binding_missing"
        return result
    for key in (
        "run_id",
        "attempt",
        "status",
        "result",
        "weaver_config",
        "otlp",
        "expectations",
        "registry",
        "matcher_config",
        "advice_policies",
        "sha256",
    ):
        if key not in binding:
            result["reason"] = "post_completion_binding_incomplete"
            return result
    if binding.get("status") != "completed":
        result["reason"] = "post_completion_not_completed"
        return result
    if type(binding.get("attempt")) is not int or binding["attempt"] < 1:
        result["reason"] = "post_completion_attempt_invalid"
        return result
    if not isinstance(binding.get("run_id"), str) or not binding["run_id"]:
        result["reason"] = "post_completion_run_id_invalid"
        return result
    if not isinstance(binding.get("result"), str) or not binding["result"]:
        result["reason"] = "post_completion_result_invalid"
        return result
    weaver_cfg = binding.get("weaver_config") or {}
    if not isinstance(weaver_cfg, dict) or not weaver_cfg.get("path"):
        result["reason"] = "post_completion_weaver_config_invalid"
        return result
    result["plan_closure_digest"] = binding.get("sha256")
    try:
        actual = compute_weaver_input_closure(root, weaver_cfg["path"])
    except SelectionError as error:
        result["reason"] = str(error)
        return result
    result["actual_closure_digest"] = actual["sha256"]
    if actual["sha256"] != binding.get("sha256"):
        result["reason"] = "post_completion_closure_digest_mismatch"
        return result
    for key in (
        "weaver_config",
        "matcher_config",
        "otlp",
        "expectations",
        "advice_policies",
    ):
        planned = binding.get(key) or {}
        current = actual.get(key) or {}
        if planned.get("path") != current.get("path") or planned.get(
            "sha256"
        ) != current.get("sha256"):
            result["reason"] = f"post_completion_{key}_digest_mismatch"
            return result
        if key == "advice_policies" and planned.get("files") != current.get("files"):
            result["reason"] = "post_completion_advice_files_mismatch"
            return result
    planned_registry = binding.get("registry") or {}
    actual_registry = actual.get("registry") or {}
    if (
        planned_registry.get("path") != actual_registry.get("path")
        or planned_registry.get("sha256") != actual_registry.get("sha256")
        or (planned_registry.get("manifest") or {}).get("path")
        != (actual_registry.get("manifest") or {}).get("path")
        or (planned_registry.get("manifest") or {}).get("sha256")
        != (actual_registry.get("manifest") or {}).get("sha256")
    ):
        result["reason"] = "post_completion_registry_digest_mismatch"
        return result
    # Optional full file map: when present it must match; digest is authoritative.
    if planned_registry.get("files") is not None and planned_registry.get(
        "files"
    ) != actual_registry.get("files"):
        result["reason"] = "post_completion_registry_files_mismatch"
        return result
    if binding.get("weaver_tool") != actual.get("weaver_tool"):
        result["reason"] = "post_completion_weaver_tool_mismatch"
        return result
    try:
        expected = json.loads(
            (root / actual["expectations"]["path"]).read_text(encoding="utf-8")
        )
        capture = json.loads(
            (root / actual["otlp"]["path"]).read_text(encoding="utf-8")
        )
    except (OSError, ValueError, UnicodeError):
        result["reason"] = "post_completion_inputs_unreadable"
        return result
    source = expected.get("source") if isinstance(expected, dict) else None
    evidence = expected.get("terminal_evidence") if isinstance(expected, dict) else None
    if (
        not isinstance(source, dict)
        or source.get("run_id") != binding["run_id"]
        or source.get("attempt") != binding["attempt"]
        or not isinstance(evidence, dict)
        or evidence.get("status") != "completed"
        or evidence.get("run_id") != binding["run_id"]
        or evidence.get("attempt") != binding["attempt"]
        or evidence.get("result") != binding["result"]
    ):
        result["reason"] = "post_completion_evidence_mismatch"
        return result
    spans = expected.get("spans") if isinstance(expected, dict) else None
    if not isinstance(spans, list) or not spans:
        result["reason"] = "post_completion_spans_missing"
        return result
    for span in spans:
        if not isinstance(span, dict) or _capture_span_matches(capture, span) != 1:
            result["reason"] = "post_completion_capture_span_mismatch"
            return result
    source_tree_id = (plan.get("git") or {}).get("source_tree_id")
    if source_tree_id:
        source_digest = directory_content_id(root)
        result["source_digest"] = source_digest
        if source_digest != source_tree_id:
            result["reason"] = "source_tree_mismatch"
            result["source_tree_verified"] = "failed"
            return result
        result["source_tree_verified"] = "verified"
    result["ok"] = True
    result["reason"] = None
    result["input_closure_verified"] = "verified"
    return result


def run_tests(
    modules,
    *,
    root,
    framework="unittest",
    src_root="src",
    python=None,
    expected_tree=None,
    junit_path=None,
    capture=None,
):
    """Run selected targets with their native framework; return the native exit."""
    modules = tuple(modules)
    if not modules:
        return 3
    root = Path(root)
    if expected_tree is not None:
        current = _tree_of(root, "HEAD")
        if current != expected_tree:
            print(
                "execution refused: checkout tree does not match the selected snapshot",
                file=sys.stderr,
            )
            return 4
    environment = dict(os.environ)
    source = str((root / src_root).resolve())
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = source + (os.pathsep + existing if existing else "")
    executable = python or sys.executable
    if framework == "pytest":
        command = [executable, "-X", "utf8", "-m", "pytest", "-q"]
        if junit_path is not None:
            command.append(f"--junitxml={Path(junit_path).resolve()}")
        command.extend(modules)
    elif framework == "unittest":
        command = [executable, "-X", "utf8", "-m", "unittest", *modules, "-v"]
    else:
        raise SelectionError("framework_unsupported")
    result = subprocess.run(
        command,
        cwd=root,
        env=environment,
        check=False,
        capture_output=capture is not None,
        text=capture is not None,
        encoding="utf-8" if capture is not None else None,
        errors="replace" if capture is not None else None,
    )
    if capture is not None:
        capture["stdout"] = result.stdout or ""
        capture["stderr"] = result.stderr or ""
    return result.returncode


def _print(value):
    sys.stdout.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="engineering-ci select",
        description="Select tests impacted by a Git change set; output a reusable plan.",
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--config", type=Path, default=Path("ci-selection.toml"))
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument(
        "--mode",
        choices=("worktree", "index", "commit", "push", "pr", "multi"),
        default="worktree",
        help="Git snapshot mode; push uses --before/--after, multi uses --refs",
    )
    parser.add_argument("--before")
    parser.add_argument("--after")
    parser.add_argument(
        "--refs",
        help="Comma-separated before:after pairs for --mode multi",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Run the full discovered test suite; it does not run every hygiene check",
    )
    parser.add_argument("--allow-empty", action="store_true")
    parser.add_argument(
        "--validate-tach",
        action="store_true",
        help="Run native tach check and bind its result to the plan",
    )
    parser.add_argument(
        "--junit",
        type=Path,
        help="Write a native JUnit XML for pytest targets in this execution",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true")
    mode.add_argument("--list", action="store_true")
    mode.add_argument("--run", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    config_path = args.config if args.config.is_absolute() else root / args.config
    try:
        config = load_config(config_path)
    except SelectionError as error:
        parser.exit(1, f"selection rejected: {error.code}\n")
    if args.mode == "multi":
        try:
            refs = [
                tuple(item.split(":", 1))
                for item in (args.refs or "").split(",")
                if item
            ]
            if not refs or any(len(pair) != 2 for pair in refs):
                raise ValueError
        except ValueError:
            parser.exit(2, "multi mode requires --refs before:after,...\n")
        changes = changeset_multi(root, refs)
    else:
        changes = changeset(
            root,
            base=args.base,
            head=args.head,
            mode=args.mode,
            before=args.before,
            after=args.after,
        )
    selection = select(
        changes,
        config,
        root=root,
        full=args.full,
    )
    if args.json:
        _print(
            plan(
                selection,
                config=config,
                config_path=config_path,
                root=root,
                tach_validation=_tach_check(root, config, selection.changeset)
                if args.validate_tach
                else None,
            )
        )
        return 0
    if args.list:
        print(" ".join(selection.tests))
        return 0
    if args.run:
        if not selection.tests:
            if selection.status in {"no_changes", "no_tests"} and args.allow_empty:
                print(
                    "no applicable tests; explicitly allowed without execution",
                    file=sys.stderr,
                )
                return 0
            print(
                f"selection {selection.status}: no tests executed; refusing a pass",
                file=sys.stderr,
            )
            return 3
        return run_tests(
            selection.tests,
            root=root,
            framework=config.framework,
            src_root=config.src_root,
            expected_tree=selection.changeset.execution_tree,
            junit_path=args.junit,
        )
    print(f"status: {selection.status}")
    print(f"tests: {' '.join(selection.tests) or '(none)'}")
    print(f"full_tests: {'yes (tests only)' if selection.full_tests else 'no'}")
    if selection.escalated:
        print(f"escalation: {selection.escalation_reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
