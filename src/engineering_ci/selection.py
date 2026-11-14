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

from engineering_ci import (
    expected_checks,
    hygiene,
    plan_contract,
    semconv_registry,
    sources,
)
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
    "push",
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
class PushPolicyConfig:
    new_ref_baseline: str | None = None
    target_baselines: dict[str, str] = field(default_factory=dict)


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
    push: PushPolicyConfig = field(default_factory=PushPolicyConfig)


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
    index_file: str | None = None
    merge_tree: str | None = None
    references: tuple[dict, ...] = ()
    base_tip: str | None = None
    source_refusal: str | None = None
    deleted_paths: tuple[str, ...] = ()
    ref_action: str | None = None
    object_type: str | None = None
    force_push: bool = False
    baseline_status: str | None = None
    non_check_obligations: tuple[str, ...] = ()

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
    push_raw = value.get("push", {})
    if not isinstance(push_raw, dict) or set(push_raw) - {
        "new_ref_baseline",
        "target_baselines",
    }:
        raise SelectionError("push_policy_invalid")
    new_ref_base = (
        _text(push_raw["new_ref_baseline"], "new_ref_baseline")
        if push_raw.get("new_ref_baseline") is not None
        else None
    )
    target_bases_raw = push_raw.get("target_baselines", {})
    if not isinstance(target_bases_raw, dict):
        raise SelectionError("push_policy_invalid")
    target_bases = {
        _text(k, "push_target_ref"): _text(v, "new_ref_baseline")
        for k, v in target_bases_raw.items()
    }
    push_policy = PushPolicyConfig(
        new_ref_baseline=new_ref_base,
        target_baselines=target_bases,
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
        push=push_policy,
    )


def resolve_new_ref_baseline(
    config=None, *, remote_ref=None, local_ref=None, explicit_baseline=None
):
    """Resolve controlled new-ref baseline from CLI override or ci-selection.toml [push]."""
    if explicit_baseline:
        return explicit_baseline
    push_cfg = getattr(config, "push", None)
    if push_cfg is None:
        return None
    target_ref = str(remote_ref or local_ref or "")
    if target_ref:
        if target_ref in push_cfg.target_baselines:
            return push_cfg.target_baselines[target_ref]
        for pattern, baseline in push_cfg.target_baselines.items():
            if pattern.endswith("/*") and target_ref.startswith(pattern[:-1]):
                return baseline
    return push_cfg.new_ref_baseline


def _git(root, *args, env=None):
    environment = {**os.environ, **env} if env else None
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=environment,
    )


def _git_bytes(root, *args, env=None):
    environment = {**os.environ, **env} if env else None
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        check=False,
        env=environment,
    )


def _hidden_worktree_refusal(
    root,
    target_prefix,
    *,
    env=None,
    skip_reason,
    assume_reason,
    error_reason,
):
    tracked = _git(root, "ls-files", "-v", "-z", env=env)
    if tracked.returncode != 0:
        return error_reason
    for entry in tracked.stdout.split("\0"):
        if not entry:
            continue
        tag, path = entry[0], entry[2:]
        if tag != "S" and not tag.islower():
            continue
        blob = _git_bytes(root, "cat-file", "blob", f"{target_prefix}{path}", env=env)
        file = Path(root) / path
        if (
            blob.returncode != 0
            or not file.is_file()
            or file.read_bytes() != blob.stdout
        ):
            return skip_reason if tag == "S" else assume_reason
    return None


def index_source_refusal(root, expected_tree, *, index_file=None):
    """One Index source judgment shared by selection and execution.

    Returns an explicit refusal code, or None only when the selected Index tree
    and the worktree bytes consumed by native tools are provably equivalent.
    """
    if not expected_tree:
        return "index_tree_missing"
    if _resolve_git_dir(root) is None:
        return "index_git_check_failed"
    env = (
        {"GIT_INDEX_FILE": str(Path(index_file).resolve())}
        if index_file is not None
        else None
    )
    written = _git(root, "write-tree", env=env)
    if written.returncode != 0:
        return "index_tree_unavailable"
    if written.stdout.strip() != expected_tree:
        return "index_tree_mismatch"
    diff = _git(root, "diff", "--quiet", "--", env=env)
    if diff.returncode == 1:
        return "index_worktree_mismatch"
    if diff.returncode != 0:
        return "index_git_check_failed"
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z", env=env)
    if untracked.returncode != 0:
        return "index_git_check_failed"
    if untracked.stdout:
        return "index_untracked_inputs"
    return _hidden_worktree_refusal(
        root,
        ":",
        env=env,
        skip_reason="index_skip_worktree_bytes_mismatch",
        assume_reason="index_assume_unchanged_bytes_mismatch",
        error_reason="index_git_check_failed",
    )


def worktree_tree_refusal(root, expected_tree):
    """Refuse immutable commit/push/pr_merge execution when the worktree diverges."""
    if not expected_tree:
        return "checkout_tree_missing"
    if _resolve_git_dir(root) is None:
        return "source_git_root_mismatch"
    if _tree_of(root, "HEAD") != expected_tree:
        return "checkout_tree_mismatch"
    diff = _git(root, "diff-index", "--quiet", expected_tree, "--")
    if diff.returncode == 1:
        return "worktree_tree_mismatch"
    if diff.returncode != 0:
        return "worktree_git_query_failed"
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    if untracked.returncode != 0:
        return "worktree_git_query_failed"
    if untracked.stdout:
        return "worktree_untracked_inputs"
    return _hidden_worktree_refusal(
        root,
        f"{expected_tree}:",
        skip_reason="checkout_skip_worktree_bytes_mismatch",
        assume_reason="checkout_assume_unchanged_bytes_mismatch",
        error_reason="worktree_git_query_failed",
    )


def _rev_parse(root, ref):
    result = _git(root, "rev-parse", "--verify", str(ref))
    if result.returncode:
        return None
    return result.stdout.strip() or None


def _object_type(root, ref):
    result = _git(root, "cat-file", "-t", str(ref))
    if result.returncode:
        return None
    return result.stdout.strip() or None


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


ZERO_OID = "0" * 40


def _is_zero_oid(value):
    return isinstance(value, str) and len(value) in {40, 64} and set(value) == {"0"}


def _resolve_git_dir(root):
    root = Path(root).resolve()
    if not (root / ".git").exists():
        return None
    top = _git(root, "rev-parse", "--show-toplevel")
    if top.returncode != 0 or not top.stdout.strip():
        return None
    if Path(top.stdout.strip()).resolve() != root:
        return None
    result = _git(root, "rev-parse", "--absolute-git-dir")
    if result.returncode == 0 and result.stdout.strip():
        candidate = Path(result.stdout.strip())
        if candidate.is_dir():
            return candidate
    git_dir = root / ".git"
    return git_dir if git_dir.is_dir() else None


def _diff_deleted_paths(root, *diff_args, env=None):
    result = _git(
        root,
        "diff",
        "--no-renames",
        "--diff-filter=D",
        "--name-only",
        "-z",
        *diff_args,
        env=env,
    )
    if result.returncode != 0:
        return ()
    return tuple(sorted({path for path in result.stdout.split("\0") if path}))


def tree_content_id(root, tree):
    """Content id for a Git tree, matching directory_content_id on a raw checkout."""
    import tempfile

    root = Path(root).resolve()
    git_dir = _resolve_git_dir(root)
    if git_dir is None:
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


def parse_pre_push_stdin(text):
    """Parse Git pre-push hook 4-tuple lines into structured ref updates."""
    updates = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 4:
            raise SelectionError("pre_push_input_invalid")
        local_ref, local_oid, remote_ref, remote_oid = parts
        updates.append(
            {
                "local_ref": local_ref,
                "local_oid": local_oid,
                "remote_ref": remote_ref,
                "remote_oid": remote_oid,
                "before": remote_oid,
                "after": local_oid,
            }
        )
    return updates


def _normalize_push_ref(item, default_baseline=None):
    if isinstance(item, dict):
        before = item.get("remote_oid", item.get("before"))
        after = item.get("local_oid", item.get("after"))
        return {
            "before": before,
            "after": after,
            "local_ref": item.get("local_ref"),
            "remote_ref": item.get("remote_ref"),
            "new_ref_baseline": item.get("new_ref_baseline", default_baseline),
        }
    if isinstance(item, (tuple, list)) and len(item) == 4:
        local_ref, local_oid, remote_ref, remote_oid = item
        return {
            "before": remote_oid,
            "after": local_oid,
            "local_ref": local_ref,
            "remote_ref": remote_ref,
            "new_ref_baseline": default_baseline,
        }
    if isinstance(item, (tuple, list)) and len(item) == 2:
        before, after = item
        return {
            "before": before,
            "after": after,
            "local_ref": None,
            "remote_ref": None,
            "new_ref_baseline": default_baseline,
        }
    raise SelectionError("multi_ref_item_invalid")


def changeset_multi(root, refs, *, new_ref_baseline=None, config=None):
    """Union of several push ref updates; execution still requires per-ref runs."""
    root = Path(root)
    normalized_refs = [_normalize_push_ref(item, new_ref_baseline) for item in refs]
    parts = [
        changeset(
            root,
            base=entry["before"] or "",
            head=entry["after"] or "HEAD",
            mode="push",
            before=entry["before"],
            after=entry["after"],
            local_ref=entry["local_ref"],
            remote_ref=entry["remote_ref"],
            new_ref_baseline=entry["new_ref_baseline"],
            config=config,
        )
        for entry in normalized_refs
    ]
    paths = sorted({path for part in parts for path in part.paths})
    deleted_paths = sorted({path for part in parts for path in part.deleted_paths})
    non_check = tuple(
        sorted({ob for part in parts for ob in part.non_check_obligations})
    )
    references = []
    for entry, part in zip(normalized_refs, parts, strict=True):
        before_id = part.base_commit or (
            entry["before"]
            if entry["before"] and not _is_zero_oid(entry["before"])
            else "0" * 40
        )
        after_id = part.head_commit or (
            entry["after"]
            if entry["after"] and not _is_zero_oid(entry["after"])
            else "0" * 40
        )
        source_ref = (
            entry["remote_ref"] or entry["local_ref"] or part.head_ref or after_id
        )
        references.append(
            {
                "local_ref": entry["local_ref"],
                "remote_ref": entry["remote_ref"],
                "source_ref": source_ref,
                "baseline_ref": f"{before_id} -> {after_id}",
                "before": part.base_commit,
                "after": part.head_commit,
                "raw_before": entry["before"],
                "raw_after": entry["after"],
                "tree": part.tree,
                "ref_action": part.ref_action,
                "object_type": part.object_type,
                "force_push": part.force_push,
                "baseline_status": part.baseline_status,
                "non_check_obligations": list(part.non_check_obligations),
                "paths": list(part.paths),
                "deleted_paths": list(part.deleted_paths),
                "complete": part.complete,
                "error": part.error,
            }
        )
    all_complete = all(part.complete for part in parts) and bool(parts)
    return ChangeSet(
        root=root,
        base_ref=",".join(str(entry["before"] or "") for entry in normalized_refs),
        head_ref=",".join(str(entry["after"] or "") for entry in normalized_refs),
        base_commit=None,
        head_commit=None,
        tree=None,
        paths=tuple(paths),
        worktree_dirty=False,
        complete=all_complete,
        error=None if all_complete else "multi_ref_incomplete",
        snapshot="multi",
        references=tuple(references),
        deleted_paths=tuple(deleted_paths),
        non_check_obligations=non_check,
    )


def _push_changeset(
    root,
    *,
    before=None,
    after=None,
    local_ref=None,
    remote_ref=None,
    new_ref_baseline=None,
):
    head_label = remote_ref or local_ref or (after or "")
    if after and _is_zero_oid(after):
        before_commit = (
            _resolve(root, before) if before and not _is_zero_oid(before) else None
        )
        return ChangeSet(
            root=root,
            base_ref=before or "",
            head_ref=head_label,
            base_commit=before_commit,
            head_commit=None,
            tree=None,
            paths=(),
            worktree_dirty=False,
            complete=True,
            error=None,
            snapshot="push",
            ref_action="delete",
            object_type="delete",
            baseline_status="not_applicable",
            non_check_obligations=("ref_deletion",),
        )
    after_oid = _rev_parse(root, after) if after else None
    if after_oid is None:
        return ChangeSet(
            root=root,
            base_ref=before or "",
            head_ref=head_label,
            base_commit=None,
            head_commit=None,
            tree=None,
            paths=(),
            worktree_dirty=False,
            complete=False,
            error="push_ref_unresolved",
            snapshot="push",
            baseline_status="unknown",
        )
    obj_type = _object_type(root, after_oid) or "unknown"
    after_commit = _resolve(root, after_oid)
    if obj_type in {"blob", "tree"} or after_commit is None:
        return ChangeSet(
            root=root,
            base_ref=before or "",
            head_ref=head_label,
            base_commit=None,
            head_commit=None,
            tree=None,
            paths=(),
            worktree_dirty=False,
            complete=False,
            error="non_commit_object",
            snapshot="push",
            object_type=obj_type,
            baseline_status="unknown",
        )
    target_tree = _tree_of(root, after_commit)
    if before and _is_zero_oid(before):
        if not new_ref_baseline:
            return ChangeSet(
                root=root,
                base_ref="",
                head_ref=head_label,
                base_commit=None,
                head_commit=after_commit,
                tree=target_tree,
                paths=(),
                worktree_dirty=False,
                complete=False,
                error="new_ref_baseline_unknown",
                snapshot="push",
                ref_action="create",
                object_type=obj_type,
                baseline_status="unknown",
            )
        before_commit = _resolve(root, new_ref_baseline)
        if before_commit is None:
            return ChangeSet(
                root=root,
                base_ref=str(new_ref_baseline),
                head_ref=head_label,
                base_commit=None,
                head_commit=after_commit,
                tree=target_tree,
                paths=(),
                worktree_dirty=False,
                complete=False,
                error="new_ref_baseline_unresolved",
                snapshot="push",
                ref_action="create",
                object_type=obj_type,
                baseline_status="unknown",
            )
        base_label = str(new_ref_baseline)
        ref_action = "create"
        force_push = False
        baseline_status = "policy"
    else:
        before_commit = _resolve(root, before) if before else None
        if before_commit is None:
            return ChangeSet(
                root=root,
                base_ref=before or "",
                head_ref=head_label,
                base_commit=None,
                head_commit=after_commit,
                tree=target_tree,
                paths=(),
                worktree_dirty=False,
                complete=False,
                error="old_object_unavailable",
                snapshot="push",
                ref_action="update",
                object_type=obj_type,
                baseline_status="old_object_unavailable",
            )
        ancestor = _git(
            root, "merge-base", "--is-ancestor", before_commit, after_commit
        )
        force_push = ancestor.returncode == 1
        ref_action = "force_update" if force_push else "update"
        base_label = before or ""
        baseline_status = "resolved"
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
    deleted = _diff_deleted_paths(root, before_commit, after_commit)
    return ChangeSet(
        root=root,
        base_ref=base_label,
        head_ref=head_label,
        base_commit=before_commit,
        head_commit=after_commit,
        tree=target_tree,
        paths=tuple(sorted(set(paths))),
        worktree_dirty=False,
        complete=diff.returncode == 0,
        error=None if diff.returncode == 0 else "push_diff_failed",
        snapshot="push",
        deleted_paths=deleted,
        ref_action=ref_action,
        object_type=obj_type,
        force_push=force_push,
        baseline_status=baseline_status,
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
    index_file=None,
    local_ref=None,
    remote_ref=None,
    new_ref_baseline=None,
    config=None,
):
    """Describe the Git snapshot behind a change set.

    Modes: worktree, index, commit, push (before/after), pr (merge tree).
    Immutable snapshots expose execution_tree; mutable ones do not.
    """
    root = Path(root)
    if mode == "push":
        resolved_baseline = resolve_new_ref_baseline(
            config,
            remote_ref=remote_ref,
            local_ref=local_ref,
            explicit_baseline=new_ref_baseline,
        )
        return _push_changeset(
            root,
            before=before,
            after=after,
            local_ref=local_ref,
            remote_ref=remote_ref,
            new_ref_baseline=resolved_baseline,
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
        deleted = (
            _diff_deleted_paths(root, base_commit, head_commit)
            if diff.returncode == 0
            else ()
        )
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
            deleted_paths=deleted,
        )
    if mode == "index":
        index_env = (
            {"GIT_INDEX_FILE": str(Path(index_file).resolve())}
            if index_file is not None
            else None
        )
        diff = _git(
            root,
            "diff",
            "--no-renames",
            "--name-only",
            "-z",
            "--cached",
            base_commit,
            env=index_env,
        )
        index_tree = None
        write_tree = _git(root, "write-tree", env=index_env)
        index_tree = (
            write_tree.stdout.strip() if write_tree.returncode == 0 else index_tree
        )
        paths = [path for path in diff.stdout.split("\0") if path]
        deleted = (
            _diff_deleted_paths(root, "--cached", base_commit, env=index_env)
            if diff.returncode == 0
            else ()
        )
        status = _git(
            root,
            "status",
            "--porcelain",
            "-z",
            "--untracked-files=all",
            env=index_env,
        )
        if index_tree is None:
            refusal = "index_tree_unavailable"
        elif diff.returncode != 0:
            refusal = "index_diff_failed"
        else:
            refusal = index_source_refusal(root, index_tree, index_file=index_file)
        return ChangeSet(
            root,
            base,
            head,
            base_commit,
            head_commit,
            tree,
            tuple(sorted(set(paths))),
            bool(status.stdout) if status.returncode == 0 else False,
            diff.returncode == 0 and refusal is None,
            refusal or (None if diff.returncode == 0 else "diff_failed"),
            snapshot="index",
            index_tree=index_tree,
            index_file=str(Path(index_file).resolve())
            if index_file is not None
            else None,
            source_refusal=refusal,
            deleted_paths=deleted,
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
        deleted = (
            _diff_deleted_paths(root, base_tip, merge_tree)
            if diff.returncode == 0
            else ()
        )
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
            deleted_paths=deleted,
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
    if getattr(changes, "source_refusal", None):
        raise SelectionError(changes.source_refusal)
    if getattr(changes, "ref_action", None) == "delete":
        return Selection(
            changeset=changes,
            tests=(),
            checks=config.checks,
            status="not_applicable",
            escalated=False,
            reasons={"skipped": ["remote_ref_deleted"]},
        )
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


def _fallback_hygiene_config(config):
    return hygiene.Config(
        schema_version=hygiene.SCHEMA_VERSION,
        checks=hygiene._DEFAULT_CHECKS,
        docs=hygiene.DocsConfig(
            suffixes=config.check_scope.docs_suffixes,
            excluded_prefixes=(),
        ),
        lychee=hygiene.LycheeConfig(
            executable="lychee",
            arguments=("--offline", "--no-progress", "--files-from", "-"),
        ),
        forwarding=hygiene.ForwardingConfig(source_prefix="src/"),
        vulture=hygiene.VultureConfig(
            executable="vulture",
            paths=("src",),
            min_confidence=60,
            advisory_exit_codes=frozenset({3}),
        ),
        tach=hygiene.TachConfig(
            executable="tach",
            path=config.tach,
            external=False,
        ),
    )


def _resolve_hygiene_input(
    root,
    config,
    *,
    hygiene_config=None,
    hygiene_config_path=None,
):
    root = Path(root)
    if hygiene_config_path is not None:
        raw_path = Path(hygiene_config_path)
        resolved = raw_path if raw_path.is_absolute() else root / raw_path
        loaded = (
            hygiene_config
            if isinstance(hygiene_config, hygiene.Config)
            else hygiene.load_config(resolved)
        )
        return loaded, hygiene.config_identity(resolved, loaded, root=root)
    if isinstance(hygiene_config, (str, Path)):
        raw_path = Path(hygiene_config)
        resolved = raw_path if raw_path.is_absolute() else root / raw_path
        loaded = hygiene.load_config(resolved)
        return loaded, hygiene.config_identity(resolved, loaded, root=root)
    default_path = root / "ci-hygiene.toml"
    if isinstance(hygiene_config, hygiene.Config):
        identity = (
            hygiene.config_identity(default_path, hygiene_config, root=root)
            if default_path.is_file()
            else None
        )
        return hygiene_config, identity
    if default_path.is_file():
        loaded = hygiene.load_config(default_path)
        return loaded, hygiene.config_identity(default_path, loaded, root=root)
    return _fallback_hygiene_config(config), None


def _resolve_sources_input(
    root,
    *,
    sources_config=None,
    sources_config_path=None,
):
    root = Path(root)
    if sources_config_path is not None:
        raw_path = Path(sources_config_path)
        resolved = raw_path if raw_path.is_absolute() else root / raw_path
        loaded = (
            sources_config
            if isinstance(sources_config, sources.Config)
            else sources.load_config(resolved)
        )
        return loaded, sources.config_identity(resolved, loaded, root=root)
    if isinstance(sources_config, (str, Path)):
        raw_path = Path(sources_config)
        resolved = raw_path if raw_path.is_absolute() else root / raw_path
        loaded = sources.load_config(resolved)
        return loaded, sources.config_identity(resolved, loaded, root=root)
    default_path = root / "ci-sources.toml"
    if isinstance(sources_config, sources.Config):
        identity = (
            sources.config_identity(default_path, sources_config, root=root)
            if default_path.is_file()
            else None
        )
        return sources_config, identity
    if default_path.is_file():
        loaded = sources.load_config(default_path)
        return loaded, sources.config_identity(default_path, loaded, root=root)
    return None, None


def _check_scope(
    selection,
    config,
    root,
    *,
    hygiene_config=None,
    sources_config=None,
    sources_identity=None,
):
    """Per-check impact aligned with authoritative Hygiene and Sources configs."""
    changes = selection.changeset
    if hygiene_config is None:
        hygiene_config, _ = _resolve_hygiene_input(root, config)
    hygiene_scopes = hygiene.check_applicability(
        changes,
        hygiene_config,
        root=root,
        full=False,
        code_prefixes=config.check_scope.code_prefixes,
    )
    is_delete = getattr(changes, "ref_action", None) == "delete"
    paths = [_normalized(path) for path in changes.paths]
    if is_delete:
        test_entry = {
            "name": config.framework,
            "status": "not_applicable",
            "inputs": [],
            "reason": "remote_ref_deleted",
        }
    else:
        test_entry = {
            "name": config.framework,
            "status": selection.status,
            "inputs": list(selection.tests),
            "reason": selection.escalation_reason,
        }
    scopes = [test_entry]
    for name in hygiene.effective_checks(hygiene_config):
        scopes.append(hygiene_scopes[name])
    if is_delete:
        scopes.append(
            {
                "name": "source",
                "status": "not_applicable",
                "inputs": [],
                "reason": "remote_ref_deleted",
            }
        )
    elif not changes.complete:
        scopes.append(
            {
                "name": "source",
                "status": "unknown",
                "inputs": [],
                "reason": "git input is incomplete; full applicability unknown",
            }
        )
    else:
        source_prefixes = list(config.check_scope.source_prefixes)
        exact_source_files = set()
        if sources_config is not None:
            for item in sources_config.acquired_roots:
                prefix = item.rstrip("/") + "/"
                if prefix not in source_prefixes:
                    source_prefixes.append(prefix)
            exact_source_files.add(_normalized(sources_config.manifest))
        if sources_identity and sources_identity.get("path"):
            exact_source_files.add(_normalized(sources_identity["path"]))
        source_inputs = [
            path
            for path in paths
            if path in exact_source_files or path.startswith(tuple(source_prefixes))
        ]
        scopes.append(
            {
                "name": "source",
                "status": "selected" if source_inputs else "not_applicable",
                "inputs": source_inputs,
                "reason": (
                    "source inputs changed" if source_inputs else "no source changes"
                ),
            }
        )
    weaver_inputs = [
        path for path in paths if path.startswith(config.check_scope.weaver_prefixes)
    ]
    # Incremental Git impact never claims Terminal readiness from filenames.
    # weaver_terminal runs only via an explicit post-completion Plan (PR #78 P1-2).
    for name in config.checks:
        if name not in {"weaver_registry", "weaver_live", "weaver_terminal"}:
            continue
        if is_delete:
            scopes.append(
                {
                    "name": name,
                    "status": "not_applicable",
                    "inputs": [],
                    "reason": "remote_ref_deleted",
                }
            )
            continue
        if not changes.complete:
            scopes.append(
                {
                    "name": name,
                    "status": "unknown",
                    "inputs": [],
                    "reason": "git input is incomplete; full applicability unknown",
                }
            )
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
    if changes.snapshot == "index":
        refusal = index_source_refusal(
            root, changes.execution_tree, index_file=changes.index_file
        )
    elif changes.execution_tree:
        refusal = worktree_tree_refusal(root, changes.execution_tree)
    else:
        refusal = None
    if refusal:
        return {
            "status": "refused",
            "exit_code": 4,
            "stdout": "",
            "stderr": f"execution refused: {refusal}",
            "reason": refusal,
            "tach_sha256": _digest(root / config.tach),
            "source_tree": changes.execution_tree or changes.tree,
            "snapshot": changes.snapshot,
        }
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
    report = {
        "status": value.get("status", "failed"),
        "exit_code": value.get("exit_code"),
        "tach_sha256": value.get("tach_sha256"),
        "source_tree": value.get("source_tree"),
    }
    if value.get("reason") is not None:
        report["reason"] = value.get("reason")
    return report


def plan(
    selection,
    *,
    config,
    config_path,
    root,
    tach_validation=None,
    hygiene_config=None,
    hygiene_config_path=None,
    sources_config=None,
    sources_config_path=None,
    effective_profile=None,
    effective_profile_path=None,
):
    """Machine-readable handoff shared by local CLI, Dagger and Actions."""
    root, config_path = Path(root), Path(config_path)
    tach_path = root / config.tach
    loaded_hygiene, hygiene_identity = _resolve_hygiene_input(
        root,
        config,
        hygiene_config=hygiene_config,
        hygiene_config_path=hygiene_config_path,
    )
    loaded_sources, sources_identity = _resolve_sources_input(
        root,
        sources_config=sources_config,
        sources_config_path=sources_config_path,
    )
    loaded_profile = effective_profile
    if loaded_profile is None and effective_profile_path is not None:
        prof_path = Path(effective_profile_path)
        if not prof_path.is_absolute():
            prof_path = root / prof_path
        loaded_profile = expected_checks.load_effective_profile(prof_path)
    profile_identity = (
        expected_checks.effective_profile_identity(
            path=effective_profile_path,
            profile=loaded_profile,
            root=root,
        )
        if loaded_profile is not None
        else None
    )
    graph = _graph_validation(selection, config, root, tach_validation)
    scope = _check_scope(
        selection,
        config,
        root,
        hygiene_config=loaded_hygiene,
        sources_config=loaded_sources,
        sources_identity=sources_identity,
    )
    content_binding = (
        worktree_content_binding(root, selection.changeset.paths)
        if selection.changeset.snapshot == "worktree" and selection.changeset.paths
        else None
    )
    weaver_prefixes_map = {
        "weaver_semantic_conventions": list(config.check_scope.weaver_prefixes)
    }
    expected = expected_checks.derive_expected_checks(
        selection.changeset,
        scope,
        config_path=config_path,
        content_binding=content_binding,
        hygiene_config=loaded_hygiene,
        hygiene_identity=hygiene_identity,
        sources_identity=sources_identity,
        effective_profile=loaded_profile,
        code_prefixes=config.check_scope.code_prefixes,
        source_prefixes=config.check_scope.source_prefixes,
        weaver_prefixes=weaver_prefixes_map,
    )
    inputs_payload = {
        "config": {"path": str(config_path), "sha256": _digest(config_path)},
        "tach": {"path": config.tach, "sha256": _digest(tach_path)},
    }
    if hygiene_identity is not None:
        inputs_payload["hygiene"] = hygiene_identity
    if sources_identity is not None:
        inputs_payload["sources"] = sources_identity
    if profile_identity is not None:
        inputs_payload["effective_profile"] = profile_identity
    generator_payload = {
        "name": "engineering-ci",
        "version": _version(),
        "config_sha256": _digest(config_path),
        "tach_sha256": _digest(tach_path),
    }
    if loaded_profile is not None:
        generator_payload["effective_profile_sha256"] = loaded_profile.get(
            "profile_sha256"
        )
    return {
        "schema_version": plan_contract.PLAN_VERSION,
        "kind": PLAN_KIND,
        "dependency_contract": plan_contract.prerequisite_contract(
            selection.prerequisites,
            config.prerequisite_actions,
            declaration={"path": str(config_path), "sha256": _digest(config_path)},
            selected_by="full"
            if selection.full_tests
            else "escalated"
            if selection.escalated
            else "targeted",
            reason=selection.escalation_reason,
        ),
        "generator": generator_payload,
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
            "index_file": selection.changeset.index_file,
            "merge_tree": selection.changeset.merge_tree,
            "execution_tree": selection.changeset.execution_tree,
            "references": list(selection.changeset.references),
            "changed_paths": list(selection.changeset.paths),
            "deleted_paths": list(selection.changeset.deleted_paths),
            "ref_action": selection.changeset.ref_action,
            "object_type": selection.changeset.object_type,
            "force_push": selection.changeset.force_push,
            "baseline_status": selection.changeset.baseline_status,
            "non_check_obligations": list(selection.changeset.non_check_obligations),
            "content_binding": content_binding,
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
            "code_prefixes": list(config.check_scope.code_prefixes),
            "source_prefixes": list(config.check_scope.source_prefixes),
            "weaver_prefixes": weaver_prefixes_map,
            "coverage_required": config.coverage_required,
            "tach_graph_validation": graph,
            "requires_tach_check": True,
            "reasons": {
                name: list(items) for name, items in sorted(selection.reasons.items())
            },
        },
        "check_scope": scope,
        "expected_checks": expected,
        "prerequisite_actions": {
            name: dict(spec)
            for name, spec in sorted(config.prerequisite_actions.items())
        },
        "inputs": inputs_payload,
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
        "schema_version": plan_contract.PLAN_VERSION,
        "kind": PLAN_KIND,
        "dependency_contract": plan_contract.prerequisite_contract(
            (),
            {},
            declaration={},
            selected_by="post_completion",
        ),
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
    index_tree=None,
    index_file=None,
    junit_path=None,
    capture=None,
):
    """Run selected targets with their native framework; return the native exit."""
    modules = tuple(modules)
    if not modules:
        return 3
    root = Path(root)
    if index_tree is not None:
        refusal = index_source_refusal(root, index_tree, index_file=index_file)
        if refusal:
            if capture is not None:
                capture["source_refusal"] = refusal
            print(f"execution refused: {refusal}", file=sys.stderr)
            return 4
    elif expected_tree is not None:
        refusal = worktree_tree_refusal(root, expected_tree)
        if refusal:
            if capture is not None:
                capture["source_refusal"] = refusal
            print(f"execution refused: {refusal}", file=sys.stderr)
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
        "--index-file",
        type=Path,
        help="Use this Git index for --mode index instead of the default index",
    )
    parser.add_argument(
        "--refs",
        help="Comma-separated before:after pairs for --mode multi",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read Git pre-push 4-tuple lines from stdin for --mode multi",
    )
    parser.add_argument(
        "--new-ref-baseline",
        help="Controlled baseline ref/commit for newly pushed remote refs",
    )
    parser.add_argument(
        "--effective-profile",
        type=Path,
        help="Authoritative EffectiveProfile JSON/TOML for Expected Checks Schema v2",
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
    preloaded_config = None
    if args.mode in {"push", "multi"} and config_path.is_file():
        try:
            preloaded_config = load_config(config_path)
        except SelectionError as error:
            parser.exit(1, f"selection rejected: {error.code}\n")
    if args.index_file is not None and args.mode != "index":
        parser.exit(2, "--index-file requires --mode index\n")
    if args.mode == "multi":
        try:
            if args.stdin:
                refs = parse_pre_push_stdin(sys.stdin.read())
                if not refs:
                    raise ValueError
            else:
                refs = [
                    tuple(item.split(":", 1))
                    for item in (args.refs or "").split(",")
                    if item
                ]
                if not refs or any(len(pair) != 2 for pair in refs):
                    raise ValueError
        except (ValueError, SelectionError):
            parser.exit(2, "multi mode requires --refs before:after,... or --stdin\n")
        changes = changeset_multi(
            root,
            refs,
            new_ref_baseline=args.new_ref_baseline,
            config=preloaded_config,
        )
    else:
        changes = changeset(
            root,
            base=args.base,
            head=args.head,
            mode=args.mode,
            before=args.before,
            after=args.after,
            index_file=args.index_file,
            new_ref_baseline=args.new_ref_baseline,
            config=preloaded_config,
        )
    if getattr(changes, "source_refusal", None):
        parser.exit(4, f"source rejected: {changes.source_refusal}\n")
    try:
        config = preloaded_config or load_config(config_path)
    except SelectionError as error:
        parser.exit(1, f"selection rejected: {error.code}\n")
    selection = select(
        changes,
        config,
        root=root,
        full=args.full,
    )
    if args.json:
        try:
            payload = plan(
                selection,
                config=config,
                config_path=config_path,
                root=root,
                tach_validation=_tach_check(root, config, selection.changeset)
                if args.validate_tach
                else None,
                effective_profile_path=args.effective_profile,
            )
        except expected_checks.ExpectedError as error:
            parser.exit(1, f"effective profile rejected: {error.code}\n")
        _print(payload)
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
            index_tree=selection.changeset.index_tree,
            index_file=selection.changeset.index_file,
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
