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
from dataclasses import dataclass, field, replace
from importlib import metadata
from pathlib import Path

from engineering_ci import (
    check_contract,
    expected_checks,
    hygiene,
    plan_contract,
    semconv_registry,
    sources,
    workflow_analysis,
)
from engineering_ci._paths import (
    SourcePathError,
    read_internal_linked_source,
    read_source_entry,
    read_source_file,
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
    "dependency_edges",
    "strict_modules",
    "coverage_required",
    "check_scope",
    "push",
    "effective_profile",
    "workflow_checks",
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
    workflow_prefixes: tuple[str, ...] = (".github/workflows/",)
    shell_prefixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowChecksConfig:
    actionlint_config: str = ""
    shellcheck_config: str = ""
    zizmor_config: str = ""
    zizmor_persona: str = ""
    zizmor_offline: bool = True
    shellcheck_inline: bool = True


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
    dependency_edges: tuple[dict, ...] = ()
    push: PushPolicyConfig = field(default_factory=PushPolicyConfig)
    effective_profile_policy: dict | None = None
    workflow_checks: WorkflowChecksConfig = field(default_factory=WorkflowChecksConfig)


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
    deleted_paths_resolved: bool = False
    generated_paths: tuple[str, ...] = ()
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
    per_ref: tuple[dict, ...] = ()
    base_prerequisites: frozenset[str] | None = None


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


def _parse_dependency_edges(raw_edges):
    if not isinstance(raw_edges, list) or len(raw_edges) > 256:
        raise SelectionError("dependency_edges_invalid")
    allowed_keys = {
        "producer_ref",
        "consumer_ref",
        "dependency_kind",
        "requires_pass",
        "output_ref",
    }
    parsed = []
    pre_pairs = []
    for edge in raw_edges:
        if not isinstance(edge, dict) or set(edge) - allowed_keys:
            raise SelectionError("dependency_edges_invalid")
        kind = edge.get("dependency_kind")
        if kind not in {"pre_execution_pass", "data"}:
            raise SelectionError("dependency_edges_invalid")
        producer = edge.get("producer_ref")
        if (
            not isinstance(producer, dict)
            or set(producer) != {"kind", "name"}
            or producer.get("kind") not in {"prerequisite_action", "check"}
        ):
            raise SelectionError("dependency_edges_invalid")
        p_kind = producer["kind"]
        p_name = _text(producer["name"], "dependency_producer")
        if kind == "data" and p_kind != "prerequisite_action":
            raise SelectionError("dependency_edges_invalid")
        consumer = edge.get("consumer_ref")
        if not isinstance(consumer, dict):
            raise SelectionError("dependency_edges_invalid")
        c_kind = consumer.get("kind")
        if c_kind == "tests" and set(consumer) == {"kind"}:
            normalized_consumer = {"kind": "tests"}
        elif c_kind == "check" and set(consumer) == {"kind", "name"}:
            normalized_consumer = {
                "kind": "check",
                "name": _text(consumer["name"], "dependency_consumer"),
            }
        elif kind == "data" and consumer == {
            "kind": "execution_report",
            "name": "artifacts",
        }:
            normalized_consumer = {
                "kind": "execution_report",
                "name": "artifacts",
            }
        else:
            raise SelectionError("dependency_edges_invalid")
        if kind == "pre_execution_pass":
            requires_pass = edge.get("requires_pass", True)
            if requires_pass is not True or edge.get("output_ref") is not None:
                raise SelectionError("dependency_edges_invalid")
            normalized_output = None
        else:
            requires_pass = edge.get("requires_pass", True)
            if type(requires_pass) is not bool:
                raise SelectionError("dependency_edges_invalid")
            output_ref = edge.get("output_ref")
            try:
                plan_contract._validate_output_ref(output_ref)
            except plan_contract.PlanError:
                raise SelectionError("dependency_edges_invalid") from None
            normalized_output = {
                "kind": output_ref["kind"],
                "path": output_ref["path"],
            }
            if output_ref.get("sha256") is not None:
                normalized_output["sha256"] = output_ref["sha256"]
        normalized_producer = {"kind": p_kind, "name": p_name}
        parsed.append(
            {
                "producer_ref": normalized_producer,
                "consumer_ref": normalized_consumer,
                "dependency_kind": kind,
                "requires_pass": requires_pass,
                "output_ref": normalized_output,
            }
        )
        pre_pairs.append(
            (
                plan_contract._node_key(normalized_producer),
                plan_contract._node_key(normalized_consumer),
            )
        )
    try:
        plan_contract._check_cycles(pre_pairs)
    except plan_contract.PlanError:
        raise SelectionError("dependency_edges_invalid") from None
    return tuple(parsed)


def _parse_config_bytes(raw):
    """Parse and validate raw TOML bytes for one consumer selection config."""
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
        elif kind in {"command", "dagger_command"}:
            argv = _string_list(spec.get("argv", []), "prerequisite_argv")
            if not argv:
                raise SelectionError("prerequisite_actions_invalid")
            prerequisite_actions[key] = {"type": kind, "argv": list(argv)}
            if kind == "dagger_command":
                try:
                    plan_contract.validate_dagger_command(spec)
                except plan_contract.PlanError as error:
                    raise SelectionError(error.code) from error
                prerequisite_actions[key] = dict(spec)
        else:
            raise SelectionError("prerequisite_type_invalid")
    dependency_edges = _parse_dependency_edges(value.get("dependency_edges", []))
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
        "workflow_prefixes",
        "shell_prefixes",
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
        workflow_prefixes=_string_list(
            scope.get("workflow_prefixes", [".github/workflows/"]),
            "workflow_prefixes",
        ),
        shell_prefixes=_string_list(
            scope.get("shell_prefixes", []),
            "shell_prefixes",
        ),
    )
    wf_raw = value.get("workflow_checks", {})
    if not isinstance(wf_raw, dict) or set(wf_raw) - {
        "actionlint_config",
        "shellcheck_config",
        "zizmor_config",
        "zizmor_persona",
        "zizmor_offline",
        "shellcheck_inline",
    }:
        raise SelectionError("workflow_checks_invalid")
    z_offline = wf_raw.get("zizmor_offline", True)
    sc_inline = wf_raw.get("shellcheck_inline", True)
    if type(z_offline) is not bool or type(sc_inline) is not bool:
        raise SelectionError("workflow_checks_invalid")
    z_persona = (
        _text(wf_raw["zizmor_persona"], "zizmor_persona")
        if wf_raw.get("zizmor_persona") is not None
        else ""
    )
    if z_persona not in {"", "regular", "pedantic", "auditor"}:
        raise SelectionError("workflow_checks_invalid")
    workflow_checks = WorkflowChecksConfig(
        actionlint_config=(
            _text(wf_raw["actionlint_config"], "actionlint_config")
            if wf_raw.get("actionlint_config")
            else ""
        ),
        shellcheck_config=(
            _text(wf_raw["shellcheck_config"], "shellcheck_config")
            if wf_raw.get("shellcheck_config")
            else ""
        ),
        zizmor_config=(
            _text(wf_raw["zizmor_config"], "zizmor_config")
            if wf_raw.get("zizmor_config")
            else ""
        ),
        zizmor_persona=z_persona,
        zizmor_offline=z_offline,
        shellcheck_inline=sc_inline,
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
    effective_profile_policy = _parse_effective_profile_policy(
        value.get("effective_profile")
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
        dependency_edges=dependency_edges,
        push=push_policy,
        effective_profile_policy=effective_profile_policy,
        workflow_checks=workflow_checks,
    )


def load_config(path):
    """Load one consumer selection config; unknown keys are refused."""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        raise SelectionError("config_missing") from None
    return _parse_config_bytes(raw)


def _parse_effective_profile_policy(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) - {
        "profile_ref",
        "profile_revision",
        "approval_source",
        "approval_digest",
        "effective_at",
        "valid_from",
        "valid_until",
        "domain_complete",
        "declared_checks",
        "governed_rules",
        "phase",
    }:
        raise SelectionError("effective_profile_policy_invalid")
    prof_ref = raw.get("profile_ref")
    if (
        not isinstance(prof_ref, dict)
        or not isinstance(prof_ref.get("name"), str)
        or not prof_ref["name"].strip()
        or not isinstance(prof_ref.get("version"), str)
        or not prof_ref["version"].strip()
    ):
        raise SelectionError("effective_profile_policy_invalid")
    rev = _text(raw.get("profile_revision", ""), "profile_revision")
    source = _text(raw.get("approval_source", ""), "approval_source")
    digest = _text(raw.get("approval_digest", ""), "approval_digest")
    domain_complete = raw.get("domain_complete", True)
    if type(domain_complete) is not bool:
        raise SelectionError("effective_profile_policy_invalid")
    declared = (
        list(_string_list(raw["declared_checks"], "declared_checks"))
        if "declared_checks" in raw and raw["declared_checks"] is not None
        else None
    )
    gov = raw.get("governed_rules")
    if gov is not None and not isinstance(gov, dict):
        raise SelectionError("effective_profile_policy_invalid")
    return {
        "profile_ref": {
            "name": prof_ref["name"].strip(),
            "version": prof_ref["version"].strip(),
        },
        "profile_revision": rev,
        "approval_source": source,
        "approval_digest": digest,
        "effective_at": (
            _text(raw["effective_at"], "effective_at")
            if raw.get("effective_at") is not None
            else None
        ),
        "valid_from": (
            _text(raw["valid_from"], "valid_from")
            if raw.get("valid_from") is not None
            else None
        ),
        "valid_until": (
            _text(raw["valid_until"], "valid_until")
            if raw.get("valid_until") is not None
            else None
        ),
        "domain_complete": domain_complete,
        "declared_checks": declared,
        "governed_rules": dict(gov) if isinstance(gov, dict) else None,
        "phase": (
            _text(raw["phase"], "phase") if raw.get("phase") is not None else None
        ),
    }


def _json_sha256(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _policy_file_scope(path, *, root=None):
    if root is None or not path:
        return "repo_target_tree"
    root_resolved = Path(root).resolve()
    raw = Path(path)
    resolved = raw.resolve() if raw.is_absolute() else (root_resolved / raw).resolve()
    try:
        resolved.relative_to(root_resolved)
        return "repo_target_tree"
    except ValueError:
        return "event_external"


def _normalize_config(config):
    return {
        "schema_version": config.schema_version,
        "tach": config.tach,
        "tests_dir": config.tests_dir,
        "src_root": config.src_root,
        "framework": config.framework,
        "escalation_files": sorted(config.escalation_files),
        "escalation_prefixes": list(config.escalation_prefixes),
        "ignored_files": sorted(config.ignored_files),
        "ignored_prefixes": list(config.ignored_prefixes),
        "checks": list(config.checks),
        "module_tests": {k: list(v) for k, v in sorted(config.module_tests.items())},
        "non_code_rules": [
            {
                "prefix": r.prefix,
                "exact": r.exact,
                "tests": list(r.tests),
                "reason": r.reason,
            }
            for r in config.non_code_rules
        ],
        "prerequisites": {k: list(v) for k, v in sorted(config.prerequisites.items())},
        "prerequisite_actions": {
            k: dict(v) for k, v in sorted(config.prerequisite_actions.items())
        },
        "strict_modules": config.strict_modules,
        "coverage_required": config.coverage_required,
        "check_scope": {
            "docs_suffixes": list(config.check_scope.docs_suffixes),
            "code_prefixes": list(config.check_scope.code_prefixes),
            "source_prefixes": list(config.check_scope.source_prefixes),
            "weaver_prefixes": list(config.check_scope.weaver_prefixes),
            "workflow_prefixes": list(config.check_scope.workflow_prefixes),
            "shell_prefixes": list(config.check_scope.shell_prefixes),
        },
        "workflow_checks": {
            "actionlint_config": config.workflow_checks.actionlint_config,
            "shellcheck_config": config.workflow_checks.shellcheck_config,
            "zizmor_config": config.workflow_checks.zizmor_config,
            "zizmor_persona": config.workflow_checks.zizmor_persona,
            "zizmor_offline": config.workflow_checks.zizmor_offline,
            "shellcheck_inline": config.workflow_checks.shellcheck_inline,
        },
        "push": {
            "new_ref_baseline": config.push.new_ref_baseline,
            "target_baselines": dict(sorted(config.push.target_baselines.items())),
        },
        "effective_profile": config.effective_profile_policy,
        "dependency_edges": json.loads(
            json.dumps(list(config.dependency_edges), sort_keys=True)
        ),
    }


def config_identity(path, config, *, root=None):
    """Build a canonical, content-addressed identity for ci-selection.toml."""
    resolved = Path(path)
    if not resolved.is_absolute() and root is not None:
        resolved = (Path(root) / resolved).resolve()
    normalized = _normalize_config(config)
    if resolved.is_file() and config is not None:
        file_config = load_config(resolved)
        if _normalize_config(file_config) != normalized:
            raise SelectionError("config_identity_file_mismatch")
    sha = _digest(resolved) if resolved.is_file() else _digest(path)
    payload = {
        "path": str(path),
        "sha256": sha,
        "schema_version": config.schema_version,
        "policy_scope": _policy_file_scope(path, root=root),
        "normalized": normalized,
    }
    ref_digest = hashlib.sha256(
        json.dumps(
            {"sha256": sha, "normalized": normalized},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    stored_path = str(path)
    if root is not None:
        base = Path(root).resolve()
        try:
            abs_resolved = resolved.resolve()
            if abs_resolved.is_relative_to(base):
                stored_path = abs_resolved.relative_to(base).as_posix()
        except OSError:
            pass
    result = {
        **payload,
        "effective_config_ref": {
            "name": "engineering-ci.selection",
            "version": str(config.schema_version),
            "sha256": f"sha256:{ref_digest}",
        },
    }
    if isinstance(sha, str) and len(sha) == 64:
        result["effective_check_config_ref"] = check_contract.rule_ref(
            "engineering-ci.selection",
            config.schema_version,
            path=stored_path,
            digest=sha,
        )
    return result


def verify_config_identity_matches_file(identity, *, root=None, require_file=False):
    """Verify inputs.config normalized fields match disk TOML; fail closed on drift or missing required file."""
    config = config_from_identity(identity)
    raw_path = identity.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        if require_file:
            raise SelectionError("config_identity_file_missing")
        return config
    candidate = Path(raw_path)
    if not candidate.is_absolute() and root is not None:
        candidate = (Path(root) / candidate).resolve()
    elif require_file and not candidate.is_absolute():
        raise SelectionError("config_identity_file_missing")
    if not candidate.is_file():
        if require_file:
            raise SelectionError("config_identity_file_missing")
        return config
    if identity.get("policy_scope") == "repo_target_tree" and root is not None:
        resolved_root = Path(root).resolve()
        try:
            in_root = candidate.resolve().is_relative_to(resolved_root)
        except OSError:
            in_root = False
        if not in_root:
            if require_file:
                raise SelectionError("config_identity_file_outside_root")
            return config
    if _digest(candidate) != identity.get("sha256"):
        raise SelectionError("config_identity_file_mismatch")
    file_config = load_config(candidate)
    if _normalize_config(file_config) != identity["normalized"]:
        raise SelectionError("config_identity_file_mismatch")
    return config


def config_from_identity(identity):
    """Reconstruct a validated Config from inputs.config identity."""
    if not isinstance(identity, dict):
        raise SelectionError("config_identity_invalid")
    norm = identity.get("normalized")
    if not isinstance(norm, dict):
        raise SelectionError("config_identity_invalid")
    sha = identity.get("sha256")
    eff_ref = identity.get("effective_config_ref")
    expected_digest = hashlib.sha256(
        json.dumps(
            {"sha256": sha, "normalized": norm},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if (
        not isinstance(eff_ref, dict)
        or eff_ref.get("sha256") != f"sha256:{expected_digest}"
    ):
        raise SelectionError("config_identity_mismatch")
    scope = norm.get("check_scope") or {}
    push_raw = norm.get("push") or {}
    return Config(
        schema_version=int(norm.get("schema_version", SCHEMA_VERSION)),
        tach=str(norm.get("tach", "tach.toml")),
        tests_dir=str(norm.get("tests_dir", "tests")),
        src_root=str(norm.get("src_root", "src")),
        framework=str(norm.get("framework", "unittest")),
        escalation_files=frozenset(norm.get("escalation_files") or ()),
        escalation_prefixes=tuple(norm.get("escalation_prefixes") or ()),
        ignored_files=frozenset(norm.get("ignored_files") or ()),
        ignored_prefixes=tuple(norm.get("ignored_prefixes") or ()),
        checks=tuple(norm.get("checks") or ()),
        module_tests={
            str(k): tuple(v) for k, v in (norm.get("module_tests") or {}).items()
        },
        non_code_rules=tuple(
            NonCodeRule(
                prefix=r.get("prefix"),
                exact=r.get("exact"),
                tests=tuple(r.get("tests") or ()),
                reason=str(r.get("reason", "non-code input")),
            )
            for r in (norm.get("non_code_rules") or ())
            if isinstance(r, dict)
        ),
        prerequisites={
            str(k): tuple(v) for k, v in (norm.get("prerequisites") or {}).items()
        },
        prerequisite_actions={
            str(k): dict(v)
            for k, v in (norm.get("prerequisite_actions") or {}).items()
            if isinstance(v, dict)
        },
        strict_modules=bool(norm.get("strict_modules", True)),
        coverage_required=bool(norm.get("coverage_required", False)),
        check_scope=CheckScopeConfig(
            docs_suffixes=tuple(scope.get("docs_suffixes") or (".md", ".html")),
            code_prefixes=tuple(
                scope.get("code_prefixes") or ("src/", "scripts/", "tests/")
            ),
            source_prefixes=tuple(
                scope.get("source_prefixes") or ("data/", "sources/")
            ),
            weaver_prefixes=tuple(scope.get("weaver_prefixes") or ()),
            workflow_prefixes=tuple(
                scope.get("workflow_prefixes") or (".github/workflows/",)
            ),
            shell_prefixes=tuple(scope.get("shell_prefixes") or ()),
        ),
        push=PushPolicyConfig(
            new_ref_baseline=push_raw.get("new_ref_baseline"),
            target_baselines=dict(push_raw.get("target_baselines") or {}),
        ),
        effective_profile_policy=norm.get("effective_profile"),
        workflow_checks=WorkflowChecksConfig(
            actionlint_config=str(
                (norm.get("workflow_checks") or {}).get("actionlint_config") or ""
            ),
            shellcheck_config=str(
                (norm.get("workflow_checks") or {}).get("shellcheck_config") or ""
            ),
            zizmor_config=str(
                (norm.get("workflow_checks") or {}).get("zizmor_config") or ""
            ),
            zizmor_persona=str(
                (norm.get("workflow_checks") or {}).get("zizmor_persona") or ""
            ),
            zizmor_offline=bool(
                (norm.get("workflow_checks") or {}).get("zizmor_offline", True)
            ),
            shellcheck_inline=bool(
                (norm.get("workflow_checks") or {}).get("shellcheck_inline", True)
            ),
        ),
        dependency_edges=tuple(
            dict(e) for e in (norm.get("dependency_edges") or ()) if isinstance(e, dict)
        ),
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
    try:
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
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            ["git", *args], 127, "", "missing executable: git"
        )


def _git_bytes(root, *args, env=None):
    environment = {**os.environ, **env} if env else None
    try:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            check=False,
            env=environment,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            ["git", *args], 127, b"", b"missing executable: git"
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
        try:
            content, _ = read_source_entry(root, path)
        except OSError:
            content = None
        if blob.returncode != 0 or content != blob.stdout:
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


def worktree_content_binding(root, paths, *, require_files=False):
    """Bind regular bytes, requiring each listed member for an immutable snapshot."""
    root = Path(root).resolve()
    entries = {}
    unverified_paths = []
    link_bindings = {}
    for path in sorted({str(item).replace("\\", "/") for item in paths}):
        try:
            entries[path] = hashlib.sha256(read_source_file(root, path)).hexdigest()
        except (FileNotFoundError, NotADirectoryError):
            entries[path] = None
            if require_files:
                unverified_paths.append(path)
        except SourcePathError as error:
            entries[path] = None
            unverified_paths.append(path)
            if error.kind == "link":
                try:
                    raw = os.fsencode(os.readlink(root / error.component))
                    link_bindings[path] = {
                        "components": [
                            {
                                "path": error.component.as_posix(),
                                "sha256": hashlib.sha256(raw).hexdigest(),
                            }
                        ]
                    }
                    if not require_files:
                        content, target, links = read_internal_linked_source(root, path)
                        entries[path] = hashlib.sha256(content).hexdigest()
                        link_bindings[path] = {
                            "components": [
                                {
                                    "path": component,
                                    "sha256": hashlib.sha256(raw_target).hexdigest(),
                                }
                                for component, raw_target in links
                            ],
                            "target_path": target,
                        }
                except (OSError, RuntimeError):
                    pass
        except OSError:
            entries[path] = None
            unverified_paths.append(path)
    payload = (
        {"paths": entries, "unverified_paths": unverified_paths}
        if unverified_paths
        else entries
    )
    if link_bindings:
        payload["link_bindings"] = link_bindings
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    binding = {"algorithm": "sha256-paths", "digest": digest, "paths": entries}
    if unverified_paths:
        binding["unverified_paths"] = unverified_paths
    if link_bindings:
        binding["link_bindings"] = link_bindings
    return binding


def directory_content_id(root):
    """Bind file bytes and literal link entries in a materialized Source."""
    root = Path(root).resolve()
    digest = hashlib.sha256()
    paths = []
    for directory, subdirs, names in root.walk():
        if directory == root:
            subdirs[:] = [name for name in subdirs if name != ".git"]
            names = [name for name in names if name != ".git"]
        for name in subdirs[:]:
            if (directory / name).is_junction():
                subdirs.remove(name)
                names.append(name)
        paths.extend((directory / name).relative_to(root).as_posix() for name in names)
    for relative in sorted(paths):
        try:
            content, link = read_source_entry(root, relative)
        except SourcePathError as error:
            if error.kind == "non_regular":
                continue
            raise
        if link:
            digest.update(b"\0symlink\0")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
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
        deleted_paths_resolved=True,
        non_check_obligations=non_check,
    )


def ref_changeset(root, ref):
    """Build an authoritative per-ref ChangeSet from a multi-ref reference entry."""
    root = Path(root) if root is not None else Path(".")
    ref_action = ref.get("ref_action")
    is_delete = ref_action == "delete"
    ref_complete = bool(ref.get("complete", True))
    if not is_delete and (
        ref.get("baseline_status") in {"unknown", "old_object_unavailable"}
        or ref.get("object_type") in {"blob", "tree", "unknown"}
        or not ref.get("before")
        or not ref.get("after")
        or not ref.get("tree")
    ):
        ref_complete = False
    raw_paths = ref.get("paths")
    raw_deleted = ref.get("deleted_paths")
    return ChangeSet(
        root=root,
        base_ref=str(ref.get("raw_before") or ref.get("before") or ""),
        head_ref=str(
            ref.get("remote_ref")
            or ref.get("local_ref")
            or ref.get("source_ref")
            or ref.get("after")
            or ""
        ),
        base_commit=ref.get("before"),
        head_commit=None if is_delete else ref.get("after"),
        tree=None if is_delete else ref.get("tree"),
        paths=()
        if not isinstance(raw_paths, (list, tuple))
        else tuple(str(p).replace("\\", "/") for p in raw_paths),
        worktree_dirty=False,
        complete=True if is_delete else ref_complete,
        error=ref.get("error")
        or (None if (is_delete or ref_complete) else "ref_incomplete"),
        snapshot="push",
        deleted_paths=()
        if not isinstance(raw_deleted, (list, tuple))
        else tuple(str(p).replace("\\", "/") for p in raw_deleted),
        deleted_paths_resolved="deleted_paths" in ref,
        ref_action=ref_action,
        object_type=ref.get("object_type"),
        force_push=bool(ref.get("force_push", False)),
        baseline_status=ref.get("baseline_status"),
        non_check_obligations=tuple(ref.get("non_check_obligations") or ()),
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
        deleted_paths_resolved=True,
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


def _tree_test_modules(root, tree, tests_dir_rel, prefix):
    rel_dir = _normalized(tests_dir_rel).strip("/")
    ls = _git(root, "ls-tree", "-r", "--name-only", "-z", tree, "--", rel_dir)
    if ls.returncode != 0:
        return None
    modules = []
    base_parts = Path(rel_dir).parts
    for item in ls.stdout.split("\0"):
        norm = _normalized(item).strip("/")
        if not norm.endswith(".py"):
            continue
        p = Path(norm)
        if not p.name.startswith("test_"):
            continue
        try:
            rel_parts = p.relative_to(Path(*base_parts)).with_suffix("").parts
        except ValueError:
            continue
        modules.append(f"{prefix}.{'.'.join(rel_parts)}")
    return tuple(sorted(modules))


def _tach_graph_text(text):
    try:
        data = tomllib.loads(text)
    except ValueError:
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


def _tach_graph(path):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise SelectionError("tach_config_invalid") from None
    return _tach_graph_text(text)


def _tree_tach_graph(root, tree, tach_rel_path):
    cat = _git(root, "cat-file", "-p", f"{tree}:{_normalized(tach_rel_path)}")
    if cat.returncode != 0:
        raise SelectionError("tach_config_invalid")
    return _tach_graph_text(cat.stdout)


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


def _active_check_names(
    changes,
    config,
    root,
    *,
    hygiene_config=None,
    sources_config=None,
    sources_identity=None,
    full=False,
):
    return {
        item["name"]
        for item in _non_test_check_scopes(
            changes,
            config,
            root,
            hygiene_config=hygiene_config,
            sources_config=sources_config,
            sources_identity=sources_identity,
            full=full,
        )
        if item.get("status") in {"selected", "unknown"}
    }


def _selected_dependency_edges(tests, active_checks, base_prerequisites, config):
    if not config.dependency_edges:
        return (), set(base_prerequisites)
    activated = set(base_prerequisites)
    primary = []
    report_edges = []
    non_report_producers = set()
    for edge in config.dependency_edges:
        p_ref = edge["producer_ref"]
        c_ref = edge["consumer_ref"]
        p_key = (p_ref["kind"], p_ref["name"])
        if c_ref["kind"] == "execution_report":
            report_edges.append(edge)
            continue
        non_report_producers.add(p_key)
        if p_ref["kind"] == "check" and p_ref["name"] not in active_checks:
            continue
        consumer_active = (c_ref["kind"] == "tests" and bool(tests)) or (
            c_ref["kind"] == "check" and c_ref["name"] in active_checks
        )
        if not consumer_active:
            continue
        primary.append(edge)
        if p_ref["kind"] == "prerequisite_action":
            activated.add(p_ref["name"])
    active = list(primary)
    has_active_work = bool(tests) or bool(active_checks)
    for edge in report_edges:
        p_ref = edge["producer_ref"]
        p_name = p_ref["name"]
        p_key = (p_ref["kind"], p_name)
        if p_name in activated or (
            p_name not in config.prerequisites
            and p_key not in non_report_producers
            and has_active_work
        ):
            active.append(edge)
            activated.add(p_name)
    return tuple(active), activated


def _escalated(
    changeset, config, tests, reason, reasons=None, *, full_tests=False, root=None
):
    base_prereqs = frozenset(config.prerequisites)
    activated = set(base_prereqs)
    if config.dependency_edges and root is not None:
        active_checks = _active_check_names(changeset, config, root, full=full_tests)
        _, activated = _selected_dependency_edges(
            tests, active_checks, base_prereqs, config
        )
    return Selection(
        changeset=changeset,
        tests=tests,
        checks=config.checks,
        status="escalated",
        escalated=True,
        escalation_reason=reason,
        reasons=reasons or {"all": [reason]},
        prerequisites=frozenset(activated),
        full_tests=full_tests,
        base_prerequisites=base_prereqs,
    )


def select(changes, config, *, root, full=False, tach_graph=None, all_tests=None):
    """Select tests for one change set; unknown coverage escalates, never passes."""
    if getattr(changes, "source_refusal", None):
        raise SelectionError(changes.source_refusal)
    root = Path(root)
    if getattr(changes, "snapshot", None) == "multi" and getattr(
        changes, "references", ()
    ):
        ref_sels = []
        per_ref_entries = []
        for index, ref in enumerate(changes.references):
            if not isinstance(ref, dict):
                continue
            ref_cs = ref_changeset(root, ref)
            ref_sel = select(
                ref_cs,
                config,
                root=root,
                full=full,
                tach_graph=tach_graph,
                all_tests=all_tests,
            )
            ref_sels.append(ref_sel)
            source_label = (
                ref.get("source_ref")
                or ref.get("remote_ref")
                or ref.get("local_ref")
                or ref.get("after")
            )
            per_ref_entries.append(
                {
                    "index": index,
                    "source_ref": source_label,
                    "remote_ref": ref.get("remote_ref"),
                    "local_ref": ref.get("local_ref"),
                    "baseline_ref": ref.get("baseline_ref"),
                    "before": ref.get("before"),
                    "after": ref.get("after"),
                    "tree": ref.get("tree"),
                    "ref_action": ref.get("ref_action"),
                    "status": ref_sel.status,
                    "tests": list(ref_sel.tests),
                    "checks": list(ref_sel.checks),
                    "prerequisites": sorted(ref_sel.prerequisites),
                    "escalated": ref_sel.escalated,
                    "escalation_reason": ref_sel.escalation_reason,
                    "full_tests": ref_sel.full_tests,
                    "reasons": {k: list(v) for k, v in sorted(ref_sel.reasons.items())},
                }
            )
        merged_tests = tuple(sorted({t for r in ref_sels for t in r.tests}))
        merged_prereqs = frozenset(p for r in ref_sels for p in r.prerequisites)
        merged_base_prereqs = frozenset(
            p
            for r in ref_sels
            for p in (
                r.prerequisites
                if r.base_prerequisites is None
                else r.base_prerequisites
            )
        )
        any_escalated = any(r.escalated for r in ref_sels)
        any_full = any(r.full_tests for r in ref_sels)
        esc_reasons = list(
            dict.fromkeys(r.escalation_reason for r in ref_sels if r.escalation_reason)
        )
        esc_reason = "; ".join(esc_reasons) if esc_reasons else None
        merged_reasons = {}
        for r in ref_sels:
            for k, items in r.reasons.items():
                bucket = merged_reasons.setdefault(k, [])
                for item in items:
                    if item not in bucket:
                        bucket.append(item)
        if any_escalated:
            agg_status = "escalated"
        elif merged_tests:
            agg_status = "targeted"
        elif ref_sels and all(r.status == "not_applicable" for r in ref_sels):
            agg_status = "not_applicable"
        elif any(r.status == "no_tests" for r in ref_sels):
            agg_status = "no_tests"
        else:
            agg_status = "no_changes"
        return Selection(
            changeset=changes,
            tests=merged_tests,
            checks=config.checks,
            status=agg_status,
            escalated=any_escalated,
            escalation_reason=esc_reason,
            reasons=merged_reasons,
            prerequisites=merged_prereqs,
            full_tests=any_full,
            per_ref=tuple(per_ref_entries),
            base_prerequisites=merged_base_prereqs,
        )
    if getattr(changes, "ref_action", None) == "delete":
        return Selection(
            changeset=changes,
            tests=(),
            checks=config.checks,
            status="not_applicable",
            escalated=False,
            reasons={"skipped": ["remote_ref_deleted"]},
            base_prerequisites=frozenset(),
        )
    tests_dir = root / config.tests_dir
    test_prefix = ".".join(Path(config.tests_dir).parts)
    exec_tree = getattr(changes, "execution_tree", None)
    tree_in_odb = (
        bool(exec_tree)
        and _git(root, "cat-file", "-e", f"{exec_tree}^{{tree}}").returncode == 0
    )
    if tree_in_odb:
        tree_tests = _tree_test_modules(root, exec_tree, config.tests_dir, test_prefix)
        resolved_all_tests = (
            tree_tests
            if tree_tests is not None
            else (
                tuple(all_tests)
                if all_tests is not None
                else _test_modules(tests_dir, test_prefix)
            )
        )
    elif all_tests is not None:
        resolved_all_tests = tuple(all_tests)
    else:
        resolved_all_tests = _test_modules(tests_dir, test_prefix)
    if full:
        return _escalated(
            changes,
            config,
            resolved_all_tests,
            "Explicitly requested full suite",
            full_tests=True,
            root=root,
        )
    if not changes.complete:
        return _escalated(
            changes,
            config,
            resolved_all_tests,
            f"Git input could not be resolved: {changes.error}",
            root=root,
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
                changes,
                config,
                resolved_all_tests,
                f"Global configuration modified: {path}",
                root=root,
            )
    try:
        if tree_in_odb:
            modules, reverse = _tree_tach_graph(root, exec_tree, config.tach)
        elif tach_graph is not None:
            modules, reverse = tach_graph
        else:
            modules, reverse = _tach_graph(root / config.tach)
    except SelectionError as error:
        return _escalated(
            changes,
            config,
            resolved_all_tests,
            f"Tach input unavailable: {error.code}",
            root=root,
        )
    deleted_set = {_normalized(item) for item in getattr(changes, "deleted_paths", ())}
    deleted_resolved = bool(getattr(changes, "deleted_paths_resolved", False))
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
            if deleted_resolved:
                is_deleted_test = path in deleted_set
            elif path in deleted_set:
                is_deleted_test = True
            elif tree_in_odb:
                is_deleted_test = (
                    _git(root, "cat-file", "-e", f"{exec_tree}:{path}").returncode != 0
                )
            else:
                is_deleted_test = not (root / path).is_file()
            if is_deleted_test:
                return _escalated(
                    changes,
                    config,
                    resolved_all_tests,
                    f"Test file deleted or renamed: {path}",
                    root=root,
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
            resolved_all_tests,
            f"Unknown or unmapped path changed: {', '.join(unmapped)}",
            {"all": [f"Unmapped: {path}" for path in unmapped]},
            root=root,
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
                    resolved_all_tests,
                    f"Python module(s) lack registered test mappings: {', '.join(missing)}",
                    root=root,
                )
        for module in sorted(impacted):
            for test in config.module_tests.get(module, ()):
                selected.add(test)
                reasons.setdefault(test, []).append(
                    f"Impacted by Python module: {module}"
                )
    ordered = tuple(sorted(selected))
    base_prereqs = frozenset(
        name for name, tests in config.prerequisites.items() if selected & set(tests)
    )
    prerequisites = set(base_prereqs)
    if config.dependency_edges:
        active_checks = _active_check_names(changes, config, root)
        _, prerequisites = _selected_dependency_edges(
            ordered, active_checks, base_prereqs, config
        )
    return Selection(
        changeset=changes,
        tests=ordered,
        checks=config.checks,
        status="targeted" if ordered else "no_tests",
        escalated=False,
        reasons=reasons,
        prerequisites=frozenset(prerequisites),
        base_prerequisites=base_prereqs,
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


def source_check_applicability(
    changes,
    config,
    *,
    sources_config=None,
    sources_identity=None,
):
    """Single authoritative applicability rule for the `source` check."""
    is_delete = getattr(changes, "ref_action", None) == "delete"
    if is_delete:
        return {
            "name": "source",
            "status": "not_applicable",
            "inputs": [],
            "reason": "remote_ref_deleted",
        }
    if not changes.complete:
        return {
            "name": "source",
            "status": "unknown",
            "inputs": [],
            "reason": "git input is incomplete; full applicability unknown",
        }
    paths = [_normalized(path) for path in changes.paths]
    source_prefixes = list(config.check_scope.source_prefixes)
    exact_source_files = {"ci-sources.toml"}
    if sources_config is not None:
        for item in sources_config.acquired_roots:
            prefix = _normalized(item).rstrip("/") + "/"
            if prefix not in source_prefixes:
                source_prefixes.append(prefix)
        exact_source_files.add(_normalized(sources_config.manifest))
    if isinstance(sources_identity, dict):
        if sources_identity.get("path"):
            exact_source_files.add(_normalized(str(sources_identity["path"])))
        normalized = sources_identity.get("normalized")
        if isinstance(normalized, dict):
            for item in normalized.get("acquired_roots") or ():
                if isinstance(item, str) and item.strip():
                    prefix = _normalized(item.strip()).rstrip("/") + "/"
                    if prefix not in source_prefixes:
                        source_prefixes.append(prefix)
            manifest = normalized.get("manifest")
            if isinstance(manifest, str) and manifest.strip():
                exact_source_files.add(_normalized(manifest.strip()))
    source_inputs = [
        path
        for path in paths
        if path in exact_source_files or path.startswith(tuple(source_prefixes))
    ]
    return {
        "name": "source",
        "status": "selected" if source_inputs else "not_applicable",
        "inputs": source_inputs,
        "reason": "source inputs changed" if source_inputs else "no source changes",
    }


def _non_test_check_scopes(
    changes,
    config,
    root,
    *,
    hygiene_config=None,
    sources_config=None,
    sources_identity=None,
    config_path=None,
    full=False,
    effective_profile_changed=False,
):
    """Per-check non-test impact aligned with authoritative Hygiene and Sources configs."""
    if hygiene_config is None:
        hygiene_config, _ = _resolve_hygiene_input(root, config)
    if sources_config is None and sources_identity is None:
        sources_config, sources_identity = _resolve_sources_input(root)
    hygiene_scopes = hygiene.check_applicability(
        changes,
        hygiene_config,
        root=root,
        full=False,
        code_prefixes=config.check_scope.code_prefixes,
    )
    is_delete = getattr(changes, "ref_action", None) == "delete"
    paths = [_normalized(path) for path in changes.paths]
    scopes = [hygiene_scopes[name] for name in hygiene.effective_checks(hygiene_config)]
    scopes.append(
        source_check_applicability(
            changes,
            config,
            sources_config=sources_config,
            sources_identity=sources_identity,
        )
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
    scopes.extend(
        workflow_script_check_scopes(
            changes,
            config,
            root,
            config_path=config_path,
            full=full,
            effective_profile_changed=effective_profile_changed,
        )
    )
    return scopes


_WORKFLOW_SCRIPT_CHECK_NAMES = frozenset(
    {"actionlint", "shellcheck", "workflow_shellcheck", "zizmor"}
)


def _workflow_rule_slice(config, name):
    """Per-check rule slice whose change invalidates all matching workflow/shell inputs."""
    wf = config.workflow_checks
    enabled = name in config.checks
    eff = config.effective_profile_policy
    if name == "actionlint":
        return (
            enabled,
            tuple(config.check_scope.workflow_prefixes),
            wf.actionlint_config,
            eff,
        )
    if name == "workflow_shellcheck":
        return (
            enabled,
            tuple(config.check_scope.workflow_prefixes),
            wf.actionlint_config,
            bool(wf.shellcheck_inline),
            eff,
        )
    if name == "zizmor":
        return (
            enabled,
            tuple(config.check_scope.workflow_prefixes),
            wf.zizmor_config,
            wf.zizmor_persona,
            bool(wf.zizmor_offline),
            eff,
        )
    return (
        enabled,
        tuple(config.check_scope.shell_prefixes),
        wf.shellcheck_config,
        eff,
    )


def _workflow_config_triggers(
    changes,
    config,
    repo_root,
    paths,
    *,
    config_path=None,
    effective_profile_changed=False,
):
    if effective_profile_changed:
        return {name: True for name in _WORKFLOW_SCRIPT_CHECK_NAMES}
    sel_rel = "ci-selection.toml"
    if config_path is not None:
        cfg_p = Path(config_path)
        if cfg_p.is_absolute():
            try:
                sel_rel = _normalized(
                    cfg_p.resolve().relative_to(repo_root.resolve()).as_posix()
                )
            except ValueError:
                sel_rel = _normalized(str(config_path))
        else:
            sel_rel = _normalized(str(config_path))
    if sel_rel not in paths:
        return {name: False for name in _WORKFLOW_SCRIPT_CHECK_NAMES}
    base_cfg = None
    if getattr(changes, "base_commit", None):
        res = _git_bytes(repo_root, "show", f"{changes.base_commit}:{sel_rel}")
        if res.returncode == 0:
            try:
                base_cfg = _parse_config_bytes(res.stdout)
            except SelectionError:
                base_cfg = None
    if base_cfg is None:
        return {name: True for name in _WORKFLOW_SCRIPT_CHECK_NAMES}
    return {
        name: _workflow_rule_slice(config, name) != _workflow_rule_slice(base_cfg, name)
        for name in _WORKFLOW_SCRIPT_CHECK_NAMES
    }


def workflow_script_check_scopes(
    changes,
    config,
    root,
    *,
    config_path=None,
    full=False,
    effective_profile_changed=False,
):
    """Per-check applicability for actionlint, shellcheck, workflow_shellcheck and zizmor."""
    scopes = []
    is_delete = getattr(changes, "ref_action", None) == "delete"
    paths = [_normalized(path) for path in changes.paths]
    wf_prefixes = config.check_scope.workflow_prefixes
    sh_prefixes = config.check_scope.shell_prefixes
    wf_cfg = config.workflow_checks
    repo_root = Path(root)
    repo_workflows = None
    repo_shells = None
    triggers = None
    changed_wf = None
    changed_sh = None
    al_cfg_changed = None
    sc_cfg_changed = None
    zz_cfg_changed = None
    cfg_problems = None
    for name in config.checks:
        if name not in _WORKFLOW_SCRIPT_CHECK_NAMES:
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
        if repo_workflows is None:
            cfg_problems = workflow_analysis.workflow_config_problems(
                config, root=repo_root
            )
            workflow_problems, shell_problems = {}, {}
            repo_workflows = workflow_analysis.discover_workflow_files(
                repo_root, wf_prefixes, problems=workflow_problems
            ) + list(workflow_problems)
            repo_shells = workflow_analysis.discover_shell_files(
                repo_root, sh_prefixes, problems=shell_problems
            ) + list(shell_problems)
            triggers = _workflow_config_triggers(
                changes,
                config,
                repo_root,
                paths,
                config_path=config_path,
                effective_profile_changed=effective_profile_changed,
            )
            changed_wf = [
                p
                for p in paths
                if (
                    workflow_analysis.is_workflow_file(p, wf_prefixes)
                    or p in workflow_problems
                )
                and p not in changes.deleted_paths
            ]
            changed_sh = [
                p
                for p in paths
                if (
                    workflow_analysis.is_shell_file(p, sh_prefixes)
                    or p in shell_problems
                )
                and p not in changes.deleted_paths
            ]
            al_cfg_changed = any(
                workflow_analysis.is_workflow_config(p, wf_cfg.actionlint_config)
                for p in paths
            )
            sc_cfg_changed = any(
                workflow_analysis.is_shellcheck_config(p, wf_cfg.shellcheck_config)
                for p in paths
            )
            zz_cfg_changed = any(
                workflow_analysis.is_zizmor_config(p, wf_cfg.zizmor_config)
                for p in paths
            )
        if name in cfg_problems:
            scopes.append(
                {
                    "name": name,
                    "status": "unknown",
                    "inputs": [],
                    "reason": cfg_problems[name],
                }
            )
            continue
        if name == "actionlint":
            scan_all = bool(full or al_cfg_changed or triggers["actionlint"])
            wf_inputs = sorted(
                set(changed_wf) | (set(repo_workflows) if scan_all else set())
            )
            if wf_inputs:
                scopes.append(
                    {
                        "name": name,
                        "status": "selected",
                        "inputs": wf_inputs,
                        "reason": (
                            "workflow files changed"
                            if changed_wf
                            else "workflow config or rules changed"
                        ),
                    }
                )
            else:
                scopes.append(
                    {
                        "name": name,
                        "status": "not_applicable",
                        "inputs": [],
                        "reason": (
                            "no_workflow_changes"
                            if repo_workflows
                            else "no_workflow_files"
                        ),
                    }
                )
        elif name == "workflow_shellcheck":
            if not wf_cfg.shellcheck_inline:
                scopes.append(
                    {
                        "name": name,
                        "status": "not_applicable",
                        "inputs": [],
                        "reason": "shellcheck_inline_disabled",
                    }
                )
            else:
                scan_all = bool(
                    full or al_cfg_changed or triggers["workflow_shellcheck"]
                )
                inline_inputs = sorted(
                    set(changed_wf) | (set(repo_workflows) if scan_all else set())
                )
                if inline_inputs:
                    scopes.append(
                        {
                            "name": name,
                            "status": "selected",
                            "inputs": inline_inputs,
                            "reason": (
                                "workflow files changed"
                                if changed_wf
                                else "workflow inline shellcheck rules changed"
                            ),
                        }
                    )
                else:
                    scopes.append(
                        {
                            "name": name,
                            "status": "not_applicable",
                            "inputs": [],
                            "reason": (
                                "no_workflow_changes"
                                if repo_workflows
                                else "no_workflow_files"
                            ),
                        }
                    )
        elif name == "zizmor":
            scan_all = bool(full or zz_cfg_changed or triggers["zizmor"])
            zz_inputs = sorted(
                set(changed_wf) | (set(repo_workflows) if scan_all else set())
            )
            if zz_inputs:
                scopes.append(
                    {
                        "name": name,
                        "status": "selected",
                        "inputs": zz_inputs,
                        "reason": (
                            "workflow security inputs changed"
                            if changed_wf
                            else "zizmor config or rules changed"
                        ),
                    }
                )
            else:
                scopes.append(
                    {
                        "name": name,
                        "status": "not_applicable",
                        "inputs": [],
                        "reason": (
                            "no_workflow_changes"
                            if repo_workflows
                            else "no_workflow_files"
                        ),
                    }
                )
        else:
            scan_all = bool(full or sc_cfg_changed or triggers["shellcheck"])
            sh_inputs = sorted(
                set(changed_sh) | (set(repo_shells) if scan_all else set())
            )
            if sh_inputs:
                scopes.append(
                    {
                        "name": name,
                        "status": "selected",
                        "inputs": sh_inputs,
                        "reason": (
                            "shell files changed"
                            if changed_sh
                            else "shellcheck config or rules changed"
                        ),
                    }
                )
            else:
                scopes.append(
                    {
                        "name": name,
                        "status": "not_applicable",
                        "inputs": [],
                        "reason": "no_shell_changes"
                        if repo_shells
                        else "no_shell_files",
                    }
                )
    return scopes


def _check_scope(
    selection,
    config,
    root,
    *,
    hygiene_config=None,
    sources_config=None,
    sources_identity=None,
    config_path=None,
    full=False,
    effective_profile_changed=False,
):
    """Per-check impact aligned with authoritative Hygiene and Sources configs."""
    changes = selection.changeset
    is_delete = getattr(changes, "ref_action", None) == "delete"
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
    return [
        test_entry,
        *_non_test_check_scopes(
            changes,
            config,
            root,
            hygiene_config=hygiene_config,
            sources_config=sources_config,
            sources_identity=sources_identity,
            config_path=config_path,
            full=full,
            effective_profile_changed=effective_profile_changed,
        ),
    ]


def _ref_config_binding(
    root,
    tree,
    config_path,
    hygiene_path=None,
    sources_path=None,
    *,
    ref_action=None,
):
    """Compare loaded policy files with the target Ref tree respecting policy scope."""
    if ref_action == "delete":
        return {
            "status": "not_applicable",
            "policy_scope": "not_applicable",
            "target_tree": tree,
            "mismatched": [],
            "policies": {},
        }
    if not tree:
        return {
            "status": "unverifiable",
            "policy_scope": "unknown",
            "target_tree": tree,
            "mismatched": ["target_tree_missing"],
            "policies": {},
        }
    root = Path(root).resolve()
    candidates = [
        ("ci-selection.toml", Path(config_path), True),
        (
            "ci-hygiene.toml",
            Path(hygiene_path) if hygiene_path else root / "ci-hygiene.toml",
            False,
        ),
        (
            "ci-sources.toml",
            Path(sources_path) if sources_path else root / "ci-sources.toml",
            False,
        ),
    ]
    mismatched = []
    policies = {}
    external_policies = []
    repo_policies = []
    for label, candidate, required in candidates:
        candidate = candidate if candidate.is_absolute() else root / candidate
        scope = _policy_file_scope(candidate, root=root)
        if scope == "event_external":
            external_policies.append(label)
            policies[label] = {
                "slot": label,
                "scope": "event_external",
                "sha256": _digest(candidate) if candidate.is_file() else None,
            }
            continue
        rel = candidate.resolve().relative_to(root).as_posix()
        if candidate.is_file():
            repo_policies.append(rel)
            policies[rel] = {
                "slot": label,
                "scope": "repo_target_tree",
                "sha256": _digest(candidate),
            }
            shown = _git(root, "show", f"{tree}:{rel}")
            if shown.returncode != 0 or shown.stdout != candidate.read_text(
                encoding="utf-8"
            ):
                mismatched.append(rel)
        else:
            shown = _git(root, "show", f"{tree}:{rel}")
            if required or shown.returncode == 0:
                repo_policies.append(rel)
                policies[rel] = {
                    "slot": label,
                    "scope": "repo_target_tree",
                    "sha256": None,
                }
                mismatched.append(rel)
    if external_policies and repo_policies:
        overall_scope = "mixed"
    elif external_policies:
        overall_scope = "event_external"
    else:
        overall_scope = "repo_target_tree"
    if mismatched:
        status = "mismatch"
    elif overall_scope == "event_external":
        status = "event_shared"
    else:
        status = "verified"
    return {
        "status": status,
        "policy_scope": overall_scope,
        "target_tree": tree,
        "mismatched": mismatched,
        "policies": policies,
    }


def _selection_entry_digest(entry, *, ref=None, sources_identity=None):
    ref_obj = ref if isinstance(ref, dict) else {}
    sources_ref = (
        sources_identity.get("effective_check_config_ref")
        if isinstance(sources_identity, dict)
        else None
    )
    material = {
        "index": entry.get("index"),
        "source_ref": entry.get("source_ref"),
        "remote_ref": entry.get("remote_ref"),
        "local_ref": entry.get("local_ref"),
        "baseline_ref": entry.get("baseline_ref"),
        "before": entry.get("before"),
        "after": entry.get("after"),
        "tree": entry.get("tree"),
        "ref_action": entry.get("ref_action"),
        "paths": sorted(ref_obj.get("paths") or ()),
        "deleted_paths": sorted(ref_obj.get("deleted_paths") or ()),
        "complete": bool(ref_obj.get("complete", False)),
        "status": entry.get("status"),
        "tests": list(entry.get("tests") or ()),
        "checks": list(entry.get("checks") or ()),
        "prerequisites": sorted(entry.get("prerequisites") or ()),
        "escalated": bool(entry.get("escalated", False)),
        "escalation_reason": entry.get("escalation_reason"),
        "full_tests": bool(entry.get("full_tests", False)),
        "reasons": {
            k: list(v) for k, v in sorted((entry.get("reasons") or {}).items())
        },
        "check_scope": list(entry.get("check_scope") or ()),
        "config_source_binding": entry.get("config_source_binding"),
        "sources_effective_ref": sources_ref,
    }
    return f"sha256:{_json_sha256(material)}"


def _ref_source_snapshot(
    root,
    ref,
    *,
    config,
    entry,
    sources_identity=None,
):
    """Build a tamper-evident per-Ref source snapshot bound to the target tree."""
    tree = ref.get("tree")
    ref_action = ref.get("ref_action")
    tach_sha256 = None
    tach_graph_digest = None
    tests_modules = []
    tests_digest = None
    source_tree_id = None
    if ref_action == "delete":
        source_origin = "ref_deleted"
    elif tree and _git(root, "cat-file", "-e", f"{tree}^{{tree}}").returncode == 0:
        source_tree_id = tree_content_id(root, tree)
        source_origin = "git_target_tree" if source_tree_id else "unverifiable"
        cat = _git(root, "cat-file", "-p", f"{tree}:{_normalized(config.tach)}")
        if cat.returncode == 0:
            tach_sha256 = hashlib.sha256(cat.stdout.encode("utf-8")).hexdigest()
            try:
                tg = _tach_graph_text(cat.stdout)
                tach_graph_digest = f"sha256:{_json_sha256({'modules': list(tg[0]), 'reverse': {k: list(v) for k, v in sorted(tg[1].items())}})}"
            except SelectionError:
                tach_graph_digest = None
        test_prefix = ".".join(Path(config.tests_dir).parts)
        tree_tests = _tree_test_modules(root, tree, config.tests_dir, test_prefix)
        tests_modules = list(tree_tests) if tree_tests is not None else []
        tests_digest = f"sha256:{_json_sha256(tests_modules)}"
    else:
        source_origin = "unverifiable"
    selection_digest = _selection_entry_digest(
        entry, ref=ref, sources_identity=sources_identity
    )
    body = {
        "kind": "engineering-ci-per-ref-source-snapshot",
        "schema_version": 1,
        "source_origin": source_origin,
        "target_tree": tree,
        "source_tree_id": source_tree_id,
        "ref_action": ref_action,
        "tach_path": config.tach,
        "tach_sha256": tach_sha256,
        "tach_graph_digest": tach_graph_digest,
        "tests_dir": config.tests_dir,
        "tests_modules": tests_modules,
        "tests_digest": tests_digest,
        "config_binding": entry.get("config_source_binding"),
        "selection_digest": selection_digest,
    }
    return {**body, "snapshot_digest": f"sha256:{_json_sha256(body)}"}


def verify_per_ref_source_snapshot(
    sel_entry,
    *,
    plan_ref=None,
    proj_ref=None,
    inputs=None,
):
    """Fail-closed verification of a bound per-Ref source snapshot when ODB is absent."""
    if not isinstance(sel_entry, dict):
        raise SelectionError("expected_per_ref_source_unverifiable")
    snap = sel_entry.get("source_snapshot_ref")
    if not isinstance(snap, dict):
        raise SelectionError("expected_per_ref_source_unverifiable")
    if snap.get("kind") != "engineering-ci-per-ref-source-snapshot" or (
        snap.get("schema_version") != 1
    ):
        raise SelectionError("expected_per_ref_source_unverifiable")
    origin = snap.get("source_origin")
    if origin not in {"git_target_tree", "ref_deleted"}:
        raise SelectionError("expected_per_ref_source_unverifiable")
    expected_snap_digest = f"sha256:{_json_sha256({k: v for k, v in snap.items() if k != 'snapshot_digest'})}"
    if snap.get("snapshot_digest") != expected_snap_digest:
        raise SelectionError("expected_per_ref_source_unverifiable")
    config_binding = sel_entry.get("config_source_binding")
    if (
        not isinstance(config_binding, dict)
        or snap.get("config_binding") != config_binding
    ):
        raise SelectionError("expected_per_ref_source_unverifiable")
    if origin == "ref_deleted":
        if (
            sel_entry.get("ref_action") != "delete"
            or snap.get("ref_action") != "delete"
            or config_binding.get("status") != "not_applicable"
        ):
            raise SelectionError("expected_per_ref_source_unverifiable")
    else:
        if not snap.get("target_tree") or snap.get("target_tree") != sel_entry.get(
            "tree"
        ):
            raise SelectionError("expected_per_ref_source_unverifiable")
        tree_id = snap.get("source_tree_id")
        if not isinstance(tree_id, str) or len(tree_id) != 64:
            raise SelectionError("expected_per_ref_source_unverifiable")
        tests_modules = snap.get("tests_modules")
        if (
            not isinstance(tests_modules, list)
            or snap.get("tests_digest") != f"sha256:{_json_sha256(tests_modules)}"
        ):
            raise SelectionError("expected_per_ref_source_unverifiable")
        if sel_entry.get("status") not in {"blocked", "not_applicable"}:
            if config_binding.get("status") not in {"verified", "event_shared"}:
                raise SelectionError("expected_per_ref_source_unverifiable")
            if not set(sel_entry.get("tests") or ()).issubset(set(tests_modules)):
                raise SelectionError("expected_per_ref_source_unverifiable")
    if plan_ref is not None:
        for key in (
            "remote_ref",
            "local_ref",
            "baseline_ref",
            "before",
            "after",
            "tree",
            "ref_action",
        ):
            if sel_entry.get(key) != plan_ref.get(key):
                raise SelectionError("expected_per_ref_source_unverifiable")
        if snap.get("target_tree") != plan_ref.get("tree") or snap.get(
            "ref_action"
        ) != plan_ref.get("ref_action"):
            raise SelectionError("expected_per_ref_source_unverifiable")
    sources_identity = inputs.get("sources") if isinstance(inputs, dict) else None
    expected_sel_digest = _selection_entry_digest(
        sel_entry, ref=plan_ref, sources_identity=sources_identity
    )
    if snap.get("selection_digest") != expected_sel_digest:
        raise SelectionError("expected_per_ref_source_unverifiable")
    if isinstance(inputs, dict) and origin == "git_target_tree":
        policies = config_binding.get("policies") or {}
        by_slot = {
            pol.get("slot"): pol
            for pol in policies.values()
            if isinstance(pol, dict) and pol.get("slot")
        }
        cfg_in = inputs.get("config")
        if isinstance(cfg_in, dict):
            cfg_scope = cfg_in.get("policy_scope")
            if cfg_scope is not None:
                if cfg_scope not in {"event_external", "repo_target_tree"}:
                    raise SelectionError("expected_per_ref_source_unverifiable")
                b_scope = config_binding.get("policy_scope")
                if cfg_scope == "event_external" and b_scope not in {
                    "event_external",
                    "mixed",
                }:
                    raise SelectionError("expected_per_ref_source_unverifiable")
                if cfg_scope == "repo_target_tree" and b_scope not in {
                    "repo_target_tree",
                    "mixed",
                }:
                    raise SelectionError("expected_per_ref_source_unverifiable")
            pol_entry = by_slot.get("ci-selection.toml") or policies.get(
                "ci-selection.toml"
            )
            if (
                isinstance(pol_entry, dict)
                and pol_entry.get("sha256") is not None
                and pol_entry.get("sha256") != cfg_in.get("sha256")
            ):
                raise SelectionError("expected_per_ref_source_unverifiable")
        for in_key, slot_name in (
            ("hygiene", "ci-hygiene.toml"),
            ("sources", "ci-sources.toml"),
        ):
            sub_in = inputs.get(in_key)
            if not isinstance(sub_in, dict):
                continue
            pol_entry = by_slot.get(slot_name) or policies.get(slot_name)
            if (
                isinstance(pol_entry, dict)
                and pol_entry.get("sha256") is not None
                and pol_entry.get("sha256") != sub_in.get("sha256")
            ):
                raise SelectionError("expected_per_ref_source_unverifiable")
    if proj_ref is not None and (
        proj_ref.get("source_snapshot_ref") != snap
        or proj_ref.get("config_source_binding") != config_binding
    ):
        raise SelectionError("expected_per_ref_source_unverifiable")


def verify_materialized_source_snapshot(
    sel_entry,
    materialized_root,
    *,
    sel_cfg,
    full=False,
    hygiene_config=None,
    sources_config=None,
    sources_identity=None,
    plan_ref=None,
    proj_ref=None,
    inputs=None,
):
    """Verify a non-ODB materialized Source directory against bound per-Ref snapshot."""
    verify_per_ref_source_snapshot(
        sel_entry,
        plan_ref=plan_ref,
        proj_ref=proj_ref,
        inputs=inputs,
    )
    snap = sel_entry["source_snapshot_ref"]
    if snap.get("source_origin") == "ref_deleted":
        return {"origin": "ref_deleted", "source_tree_id": None}
    if not materialized_root:
        raise SelectionError("expected_per_ref_source_unverifiable")
    mat_path = Path(materialized_root)
    if not mat_path.is_dir():
        raise SelectionError("expected_per_ref_source_unverifiable")
    if directory_content_id(mat_path) != snap.get("source_tree_id"):
        raise SelectionError("expected_per_ref_source_unverifiable")
    config_binding = sel_entry.get("config_source_binding") or {}
    if config_binding.get("status") == "verified":
        for rel_key, pol_info in (config_binding.get("policies") or {}).items():
            if (
                not isinstance(pol_info, dict)
                or pol_info.get("scope") != "repo_target_tree"
            ):
                continue
            pol_file = mat_path / rel_key
            expected_sha = pol_info.get("sha256")
            if not pol_file.is_file() or not expected_sha:
                raise SelectionError("expected_per_ref_source_unverifiable")
            lf_sha = hashlib.sha256(
                pol_file.read_text(encoding="utf-8").encode("utf-8")
            ).hexdigest()
            if _digest(pol_file) != expected_sha and lf_sha != expected_sha:
                raise SelectionError("expected_per_ref_source_unverifiable")
    mat_tach = mat_path / sel_cfg.tach
    tg = None
    if mat_tach.is_file():
        actual_tach_sha = hashlib.sha256(
            mat_tach.read_text(encoding="utf-8").encode("utf-8")
        ).hexdigest()
        if snap.get("tach_sha256") != actual_tach_sha:
            raise SelectionError("expected_per_ref_source_unverifiable")
        try:
            tg = _tach_graph(mat_tach)
            actual_tg_digest = f"sha256:{_json_sha256({'modules': list(tg[0]), 'reverse': {k: list(v) for k, v in sorted(tg[1].items())}})}"
        except SelectionError:
            tg = None
            actual_tg_digest = None
        if snap.get("tach_graph_digest") != actual_tg_digest:
            raise SelectionError("expected_per_ref_source_unverifiable")
    elif (
        snap.get("tach_sha256") is not None or snap.get("tach_graph_digest") is not None
    ):
        raise SelectionError("expected_per_ref_source_unverifiable")
    test_prefix = ".".join(Path(sel_cfg.tests_dir).parts)
    mat_tests = list(_test_modules(mat_path / sel_cfg.tests_dir, test_prefix))
    if mat_tests != list(snap.get("tests_modules") or ()):
        raise SelectionError("expected_per_ref_source_unverifiable")
    if plan_ref is not None:
        ref_cs = ref_changeset(mat_path, plan_ref)
        ref_sel = select(
            ref_cs,
            sel_cfg,
            root=mat_path,
            full=full,
            tach_graph=tg,
            all_tests=tuple(mat_tests),
        )
        binding_ok = config_binding.get("status") in {
            "verified",
            "event_shared",
            "not_applicable",
        }
        block_reason = (
            "per_ref_source_unverifiable"
            if config_binding.get("status") == "unverifiable"
            else "config_source_mismatch"
        )
        if not binding_ok:
            ref_sel = replace(
                ref_sel,
                status="blocked",
                tests=(),
                checks=(),
                escalated=True,
                reasons={
                    **ref_sel.reasons,
                    block_reason: config_binding.get("mismatched") or [],
                },
            )
        ref_scope = _check_scope(
            ref_sel,
            sel_cfg,
            mat_path,
            hygiene_config=hygiene_config,
            sources_config=sources_config,
            sources_identity=sources_identity,
            config_path=(inputs or {}).get("config", {}).get("path"),
            full=ref_sel.full_tests,
        )
        if not binding_ok:
            ref_scope = [
                {
                    **item,
                    "status": "blocked",
                    "reason": block_reason,
                }
                for item in ref_scope
            ]
        if (
            sel_entry.get("status") != ref_sel.status
            or sel_entry.get("tests") != list(ref_sel.tests)
            or sel_entry.get("checks") != list(ref_sel.checks)
            or sel_entry.get("check_scope") != ref_scope
        ):
            raise SelectionError("expected_per_ref_source_unverifiable")
    return {
        "origin": "materialized_source_tree",
        "source_tree_id": snap.get("source_tree_id"),
    }


def per_ref_selections_and_scopes(
    references,
    config,
    root,
    *,
    full=False,
    hygiene_config=None,
    sources_config=None,
    sources_identity=None,
    tach_graph=None,
    all_tests=None,
    config_path=None,
    hygiene_config_path=None,
    sources_config_path=None,
):
    """Run independent per-ref Selection and CheckScope for each ref in a multi-ref push."""
    root = Path(root) if root is not None else Path(".")
    if hygiene_config is None:
        hygiene_config, _ = _resolve_hygiene_input(root, config)
    if sources_config is None and not isinstance(sources_identity, dict):
        sources_config, sources_identity = _resolve_sources_input(root)
    per_ref_entries = []
    per_ref_scopes = []
    for index, ref in enumerate(references):
        if not isinstance(ref, dict):
            continue
        ref_cs = ref_changeset(root, ref)
        ref_sel = select(
            ref_cs,
            config,
            root=root,
            full=full,
            tach_graph=tach_graph,
            all_tests=all_tests,
        )
        config_binding = _ref_config_binding(
            root,
            ref.get("tree"),
            config_path or root / "ci-selection.toml",
            hygiene_config_path,
            sources_config_path,
            ref_action=ref.get("ref_action"),
        )
        if ref.get("ref_action") != "delete":
            tree = ref.get("tree")
            tree_ok = (
                bool(tree)
                and _git(root, "cat-file", "-e", f"{tree}^{{tree}}").returncode == 0
            )
            if not tree_ok and config_binding["status"] != "unverifiable":
                config_binding = {
                    **config_binding,
                    "status": "unverifiable",
                    "mismatched": list(config_binding["mismatched"])
                    + ["target_tree_missing"],
                }
        binding_ok = config_binding["status"] in {
            "verified",
            "event_shared",
            "not_applicable",
        }
        block_reason = (
            "per_ref_source_unverifiable"
            if config_binding["status"] == "unverifiable"
            else "config_source_mismatch"
        )
        if not binding_ok:
            ref_sel = replace(
                ref_sel,
                status="blocked",
                tests=(),
                checks=(),
                escalated=True,
                reasons={
                    **ref_sel.reasons,
                    block_reason: config_binding["mismatched"],
                },
            )
        ref_scope = _check_scope(
            ref_sel,
            config,
            root,
            hygiene_config=hygiene_config,
            sources_config=sources_config,
            sources_identity=sources_identity,
            config_path=config_path,
            full=ref_sel.full_tests,
        )
        if not binding_ok:
            ref_scope = [
                {
                    **item,
                    "status": "blocked",
                    "reason": block_reason,
                }
                for item in ref_scope
            ]
        source_label = (
            ref.get("source_ref")
            or ref.get("remote_ref")
            or ref.get("local_ref")
            or ref.get("after")
        )
        ref_base_prereqs = (
            ref_sel.prerequisites
            if ref_sel.base_prerequisites is None
            else ref_sel.base_prerequisites
        )
        ref_active_checks = {
            item["name"]
            for item in ref_scope[1:]
            if item.get("status") in {"selected", "unknown"}
        }
        _, ref_effective_prereqs = _selected_dependency_edges(
            ref_sel.tests, ref_active_checks, ref_base_prereqs, config
        )
        entry = {
            "index": index,
            "source_ref": source_label,
            "remote_ref": ref.get("remote_ref"),
            "local_ref": ref.get("local_ref"),
            "baseline_ref": ref.get("baseline_ref"),
            "before": ref.get("before"),
            "after": ref.get("after"),
            "tree": ref.get("tree"),
            "ref_action": ref.get("ref_action"),
            "status": ref_sel.status,
            "tests": list(ref_sel.tests),
            "checks": list(ref_sel.checks),
            "prerequisites": sorted(ref_effective_prereqs),
            "escalated": ref_sel.escalated,
            "escalation_reason": ref_sel.escalation_reason,
            "full_tests": ref_sel.full_tests,
            "reasons": {k: list(v) for k, v in sorted(ref_sel.reasons.items())},
            "check_scope": ref_scope,
            "config_source_binding": config_binding,
        }
        entry["source_snapshot_ref"] = _ref_source_snapshot(
            root,
            ref,
            config=config,
            entry=entry,
            sources_identity=sources_identity,
        )
        per_ref_entries.append(entry)
        per_ref_scopes.append(ref_scope)
    return per_ref_entries, per_ref_scopes


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


def _build_dependency_contract(
    active_edges, effective_prereqs, config, *, declaration, selected_by, reason
):
    if not active_edges:
        return plan_contract.prerequisite_contract(
            effective_prereqs,
            config.prerequisite_actions,
            declaration=declaration,
            selected_by=selected_by,
            reason=reason,
        )
    edges = []
    prereqs_with_edges = set()
    for edge in active_edges:
        p_ref = dict(edge["producer_ref"])
        p_kind, p_name = p_ref["kind"], p_ref["name"]
        if p_kind == "prerequisite_action":
            prereqs_with_edges.add(p_name)
            resolved = plan_contract._supported_action(
                config.prerequisite_actions.get(p_name)
            )
            section = f"prerequisite_actions.{p_name}"
        else:
            resolved = True
            section = "dependency_edges"
        edges.append(
            {
                "producer_ref": p_ref,
                "consumer_ref": dict(edge["consumer_ref"]),
                "dependency_kind": edge["dependency_kind"],
                "requires_pass": edge["requires_pass"],
                "output_ref": (
                    dict(edge["output_ref"])
                    if edge.get("output_ref") is not None
                    else None
                ),
                "source_binding_ref": "git",
                "declaration_ref": {**declaration, "section": section},
                "selection": {
                    "selected_by": selected_by,
                    "escalation_reason": reason,
                },
                "execution_phase": "pre_execution",
                "resolution": "resolved" if resolved else "unresolved",
                "unresolved_reason": (
                    None if resolved else "action_parameters_missing"
                ),
            }
        )
    remaining_prereqs = sorted(effective_prereqs - prereqs_with_edges)
    if remaining_prereqs:
        edges.extend(
            plan_contract.prerequisite_contract(
                remaining_prereqs,
                config.prerequisite_actions,
                declaration=declaration,
                selected_by=selected_by,
                reason=reason,
            )["edges"]
        )
    return plan_contract.graph_contract(edges)


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
    resolved_profile_path = None
    if effective_profile_path is not None:
        raw_prof_path = Path(effective_profile_path)
        resolved_profile_path = (
            raw_prof_path if raw_prof_path.is_absolute() else root / raw_prof_path
        ).resolve()
    loaded_profile = effective_profile
    if loaded_profile is None and resolved_profile_path is not None:
        loaded_profile = expected_checks.load_effective_profile(resolved_profile_path)
    profile_identity = (
        expected_checks.effective_profile_identity(
            path=resolved_profile_path,
            profile=loaded_profile,
            root=root,
        )
        if loaded_profile is not None
        else None
    )
    try:
        tach_graph = _tach_graph(tach_path)
    except SelectionError:
        tach_graph = None
    test_prefix = ".".join(Path(config.tests_dir).parts)
    all_tests = _test_modules(root / config.tests_dir, test_prefix)
    graph = _graph_validation(selection, config, root, tach_validation)
    profile_changed = False
    if resolved_profile_path is not None:
        try:
            prof_rel = _normalized(
                resolved_profile_path.relative_to(root.resolve()).as_posix()
            )
        except ValueError:
            prof_rel = None
        if prof_rel is not None and prof_rel in {
            _normalized(p) for p in selection.changeset.paths
        }:
            profile_changed = True
    scope = _check_scope(
        selection,
        config,
        root,
        hygiene_config=loaded_hygiene,
        sources_config=loaded_sources,
        sources_identity=sources_identity,
        config_path=config_path,
        full=selection.full_tests,
        effective_profile_changed=profile_changed,
    )
    active_checks = {
        item["name"]
        for item in scope[1:]
        if item.get("status") in {"selected", "unknown"}
    }
    base_prereqs = (
        selection.prerequisites
        if selection.base_prerequisites is None
        else selection.base_prerequisites
    )
    active_edges, effective_prereqs = _selected_dependency_edges(
        selection.tests, active_checks, base_prereqs, config
    )
    declaration = {"path": str(config_path), "sha256": _digest(config_path)}
    selected_by = (
        "full"
        if selection.full_tests
        else "escalated"
        if selection.escalated
        else "targeted"
    )
    dependency_contract = _build_dependency_contract(
        active_edges,
        effective_prereqs,
        config,
        declaration=declaration,
        selected_by=selected_by,
        reason=selection.escalation_reason,
    )
    per_ref_entries = ()
    per_ref_scopes = None
    if selection.changeset.snapshot == "multi" and selection.changeset.references:
        per_ref_entries, per_ref_scopes = per_ref_selections_and_scopes(
            selection.changeset.references,
            config,
            root,
            full=selection.full_tests,
            hygiene_config=loaded_hygiene,
            sources_config=loaded_sources,
            sources_identity=sources_identity,
            tach_graph=tach_graph,
            all_tests=all_tests,
            config_path=config_path,
            hygiene_config_path=hygiene_config_path,
            sources_config_path=sources_config_path,
        )
    content_binding = (
        worktree_content_binding(root, selection.changeset.paths)
        if selection.changeset.snapshot == "worktree" and selection.changeset.paths
        else None
    )
    weaver_prefixes_map = {
        "weaver_semantic_conventions": list(config.check_scope.weaver_prefixes)
    }
    wf_digests = workflow_analysis.workflow_config_digests(root, config.workflow_checks)
    expected = expected_checks.derive_expected_checks(
        selection.changeset,
        scope,
        config_path=config_path,
        content_binding=content_binding,
        hygiene_config=loaded_hygiene,
        hygiene_identity=hygiene_identity,
        sources_config=loaded_sources,
        sources_identity=sources_identity,
        effective_profile=loaded_profile,
        code_prefixes=config.check_scope.code_prefixes,
        source_prefixes=config.check_scope.source_prefixes,
        weaver_prefixes=weaver_prefixes_map,
        selection_config=config,
        per_ref_scopes=per_ref_scopes,
        per_ref_selections=per_ref_entries,
        root=root,
        workflow_config_digests=wf_digests,
    )
    tach_input = {"path": config.tach, "sha256": _digest(tach_path)}
    if tach_graph is not None:
        tach_input["modules"] = list(tach_graph[0])
        tach_input["reverse"] = {k: list(v) for k, v in sorted(tach_graph[1].items())}
    tach_input["all_tests"] = list(all_tests)
    config_id = config_identity(config_path, config, root=root)
    if wf_digests or any(c in _WORKFLOW_SCRIPT_CHECK_NAMES for c in config.checks):
        config_id["workflow_config_digests"] = wf_digests
    inputs_payload = {
        "config": config_id,
        "tach": tach_input,
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
    selection_payload = {
        "status": selection.status,
        "tests": list(selection.tests),
        "checks": list(selection.checks),
        "prerequisites": sorted(effective_prereqs),
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
    }
    if per_ref_entries:
        selection_payload["per_ref"] = list(per_ref_entries)
    return {
        "schema_version": plan_contract.PLAN_VERSION,
        "kind": PLAN_KIND,
        "dependency_contract": dependency_contract,
        "generator": generator_payload,
        "git": {
            "repo_root": str(root.resolve()),
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
        "selection": selection_payload,
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
        capture["argv"] = command
        capture["executable"] = executable
        capture["execution_source"] = str(root.resolve())
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
